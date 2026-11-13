from pathlib import Path
import sys
import pytest
from musebridge.catalog import Catalog
from musebridge.execution import Execution
from musebridge.mcp_client import MCPBridge


def test_real_stdio_discovery_call_grants_schema_and_cleanup(tmp_path):
    root=tmp_path/'project';root.mkdir();catalog=Catalog(tmp_path/'state');project=catalog.create_project('MCP',root)
    cid=catalog.create_conversation('Client',project['id'])['id'];execution=Execution(catalog);bridge=MCPBridge(catalog,execution,tmp_path/'state')
    fixture=Path(__file__).resolve().parents[1]/'tools'/'mcp_fixture_server.py'
    try:
        server=bridge.configure_server(cid,'Fixture',sys.executable,[str(fixture)],grants={'echo':'read','fail':'read','uncertain':'project_write'},enabled=True)
        tools=bridge.tools(cid,server)
        assert {'echo','fail','uncertain'}=={t['name'] for t in tools}
        assert bridge.call(cid,server,'echo',{'text':'hello'},read_only=True)['content'][0]['text']=='fixture:hello'
        assert not bridge.call(cid,server,'fail',{},read_only=True)['ok']
        with pytest.raises(Exception):bridge.call(cid,server,'echo',{'text':123},read_only=True)
        execution.set_profile(cid,'read_only')
        with pytest.raises(ValueError):bridge.call(cid,server,'uncertain',{'marker':str(root/'no')})
        assert not (root/'no').exists()
        assert bridge.call(cid,server,'echo',{'text':'still works'},read_only=True)['ok']
        other=catalog.create_conversation('Unrelated')['id']
        with pytest.raises(ValueError):bridge.tools(other,server)
        bridge.stop_all();assert not bridge.actors
    finally:bridge.close();catalog.close()


def test_disabled_server_and_external_schema_refs_are_refused(tmp_path):
    from musebridge.mcp_client import _bounded_schema
    with pytest.raises(ValueError):_bounded_schema({'$ref':'http://127.0.0.1/unexpected'})
    catalog=Catalog(tmp_path);project=catalog.create_project('MCP',tmp_path);cid=catalog.create_conversation('Client',project['id'])['id']
    bridge=MCPBridge(catalog,Execution(catalog),tmp_path)
    try:
        server=bridge.configure_server(cid,'Disabled',sys.executable,[],enabled=False)
        with pytest.raises(ValueError):bridge.tools(cid,server)
    finally:bridge.close();catalog.close()


def test_cancelled_mutating_call_is_not_replayed(tmp_path):
    import threading,time
    catalog=Catalog(tmp_path/'state');root=tmp_path/'project';root.mkdir();project=catalog.create_project('MCP',root)
    cid=catalog.create_conversation('Client',project['id'])['id'];bridge=MCPBridge(catalog,Execution(catalog),tmp_path/'state')
    fixture=Path(__file__).resolve().parents[1]/'tools'/'mcp_fixture_server.py'
    marker=root/'effect.txt';errors=[]
    try:
        server=bridge.configure_server(cid,'Fixture',sys.executable,[str(fixture)],grants={'uncertain':'project_write'},enabled=True)
        def call():
            try:bridge.call(cid,server,'uncertain',{'marker':str(marker)},request_id='one-effect')
            except OSError as exc:errors.append(type(exc).__name__)
        thread=threading.Thread(target=call);thread.start()
        deadline=time.monotonic()+15
        while not marker.exists() and time.monotonic()<deadline:time.sleep(.02)
        assert marker.exists()
        bridge.stop_all();thread.join(12);assert not thread.is_alive() and errors
        with pytest.raises(OSError):bridge.call(cid,server,'uncertain',{'marker':str(marker)},request_id='one-effect')
        assert marker.read_text()=='x'
    finally:bridge.close();catalog.close()


def test_reconfiguration_fences_calls_while_actor_is_stopping(tmp_path,monkeypatch):
    import threading,time
    catalog=Catalog(tmp_path/'state');root=tmp_path/'project';root.mkdir();project=catalog.create_project('MCP',root)
    cid=catalog.create_conversation('Client',project['id'])['id'];bridge=MCPBridge(catalog,Execution(catalog),tmp_path/'state')
    entered=threading.Event();release=threading.Event();errors=[]
    try:
        server=bridge.configure_server(cid,'Fixture',sys.executable,[],grants={'echo':'read'},enabled=True)
        original_stop=bridge.stop_server
        def block_stop(server_id):
            entered.set()
            if not release.wait(5):raise TimeoutError('test release barrier timed out')
            return original_stop(server_id)
        monkeypatch.setattr(bridge,'stop_server',block_stop)
        def configure():
            try:
                bridge.configure_server(cid,'Fixture disabled',sys.executable,[],grants={},enabled=False,server_id=server)
            except Exception as exc:errors.append(exc)
        worker=threading.Thread(target=configure);worker.start()
        assert entered.wait(5)
        with pytest.raises(ValueError,match='being reconfigured'):
            bridge.call(cid,server,'echo',{'text':'must not start'},request_id='during-config')
        assert catalog.rows('SELECT request_key FROM mcp_requests_v1 WHERE request_key=?',
                            (cid+':'+server+':during-config',))==[]
        release.set();worker.join(8)
        assert not worker.is_alive() and not errors
        with pytest.raises(ValueError,match='not enabled'):
            bridge.call(cid,server,'echo',{'text':'disabled'},request_id='after-config')
    finally:
        release.set();bridge.close();catalog.close()


def test_stale_queued_call_is_rechecked_before_sdk_invocation(tmp_path):
    import asyncio
    catalog=Catalog(tmp_path/'state');root=tmp_path/'project';root.mkdir();project=catalog.create_project('MCP',root)
    cid=catalog.create_conversation('Client',project['id'])['id'];bridge=MCPBridge(catalog,Execution(catalog),tmp_path/'state')
    try:
        server=bridge.configure_server(cid,'Fixture',sys.executable,[],grants={'echo':'read'},enabled=True)
        _config,_project,old_epoch,effect=bridge._configuration_snapshot(cid,server,'echo')
        bridge.configure_server(cid,'Fixture',sys.executable,[],grants={'echo':'project_write'},enabled=True,server_id=server)
        class Session:
            calls=0
            async def call_tool(self,*_args,**_kwargs):
                self.calls+=1
                return type('Result',(),{'content':[],'is_error':False,'structured_content':None})()
        class Client:
            session=Session()
        client=Client()
        schema={'echo':{'input_schema':{'type':'object','properties':{'text':{'type':'string'}},'required':['text']}}}
        coroutine=bridge._invoke_queued(cid,server,old_epoch,'echo',{'text':'queued'},None,effect,True,schema,client)
        future=asyncio.run_coroutine_threadsafe(coroutine,bridge.loop)
        with pytest.raises(ValueError,match='configuration changed'):
            future.result(5)
        assert client.session.calls==0
    finally:bridge.close();catalog.close()
