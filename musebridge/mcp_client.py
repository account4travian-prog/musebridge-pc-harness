"""Opt-in project-scoped stdio MCP, using the maintained Python SDK."""
import asyncio
import concurrent.futures
import json
import os
from pathlib import Path
import threading
import uuid
import hashlib
import time
import jsonschema


MAX_RESULT_BYTES=100_000


def _bounded_schema(schema):
    if len(json.dumps(schema).encode())>32_000:raise ValueError('MCP schema exceeds 32 KB')
    def inspect(value,depth=0):
        if depth>20:raise ValueError('MCP schema is too deeply nested')
        if isinstance(value,dict):
            for key,item in value.items():
                if key in ('$ref','$dynamicRef','$recursiveRef') and (not isinstance(item,str) or not item.startswith('#')):
                    raise ValueError('External MCP schema references are not supported')
                inspect(item,depth+1)
        elif isinstance(value,list):
            for item in value:inspect(item,depth+1)
    inspect(schema);jsonschema.Draft202012Validator.check_schema(schema)


class MCPBridge:
    def __init__(self,catalog,execution,state_dir):
        self.catalog=catalog;self.execution=execution;self.closed=False
        self.loop=asyncio.new_event_loop();self.actors={}
        self._config_lock=threading.RLock();self._configuring=set();self._config_epochs={};self._configure_locks={}
        self.thread=threading.Thread(target=self.loop.run_forever,name='MuseBridge-MCP',daemon=True);self.thread.start()
        with catalog.lock,catalog.db:
            catalog.db.execute('''CREATE TABLE IF NOT EXISTS mcp_servers_v1(
                id TEXT PRIMARY KEY,project_id TEXT NOT NULL,name TEXT NOT NULL,command TEXT NOT NULL,
                args_json TEXT NOT NULL,grants_json TEXT NOT NULL,enabled INTEGER NOT NULL)''')
            catalog.db.execute('''CREATE TABLE IF NOT EXISTS mcp_requests_v1(
                request_key TEXT PRIMARY KEY,args_hash TEXT NOT NULL,state TEXT NOT NULL,result_json TEXT,created REAL NOT NULL)''')
            catalog.db.execute("UPDATE mcp_requests_v1 SET state='unknown' WHERE state='sending'")

    def _project(self,cid):
        c=self.catalog.conversation(cid)
        if not c['project_id']:raise ValueError('MCP requires a selected project')
        return self.catalog.project(c['project_id'])

    def configure_server(self,cid,name,command,args,grants=None,enabled=False,server_id=None):
        project=self._project(cid);path=Path(command)
        if not path.is_absolute() or not path.is_file() or path.suffix.casefold()!='.exe':raise ValueError('Choose an absolute executable path for a local stdio server')
        if not isinstance(name,str) or not name.strip() or len(name)>100:raise ValueError('MCP server name must be 1..100 characters')
        if not isinstance(args,list) or len(args)>50 or any(not isinstance(a,str) or '\0' in a for a in args) or len(json.dumps(args))>16000:raise ValueError('Invalid MCP server arguments')
        grants=grants or {}
        if not isinstance(grants,dict) or len(grants)>100 or any(not isinstance(k,str) or len(k)>100 or v not in ('read','project_write') for k,v in grants.items()):raise ValueError('Tool grants must explicitly map names to read or project_write')
        if type(enabled) is not bool:raise ValueError('enabled must be a boolean')
        if not server_id:server_id=uuid.uuid4().hex
        with self._config_lock:
            configure_lock=self._configure_locks.setdefault(server_id,threading.Lock())
        with configure_lock:
            updating=bool(self.catalog.rows('SELECT id FROM mcp_servers_v1 WHERE id=?',(server_id,)))
            if updating:self._config(cid,server_id)
            with self._config_lock:
                self._config_epochs[server_id]=self._config_epochs.get(server_id,0)+1
                self._configuring.add(server_id)
            try:
                # Revoke queued work before actor shutdown, without holding the
                # config lock while waiting for the loop to stop that actor.
                if updating:self.stop_server(server_id)
                with self.catalog.lock,self.catalog.db:
                    self.catalog.db.execute('INSERT OR REPLACE INTO mcp_servers_v1 VALUES(?,?,?,?,?,?,?)',
                        (server_id,project['id'],name.strip(),str(path),json.dumps(args),json.dumps(grants),int(enabled)))
            finally:
                with self._config_lock:self._configuring.discard(server_id)
        return server_id

    def servers(self,cid):
        project=self._project(cid)
        rows=self.catalog.rows('SELECT id,name,enabled,grants_json FROM mcp_servers_v1 WHERE project_id=?',(project['id'],))
        return [{**{k:r[k] for k in ('id','name','enabled')},'grants':json.loads(r['grants_json'])} for r in rows]

    def _config(self,cid,server_id):
        project=self._project(cid)
        rows=self.catalog.rows('SELECT * FROM mcp_servers_v1 WHERE id=? AND project_id=?',(server_id,project['id']))
        if not rows:raise ValueError('MCP server belongs to another or unknown project')
        return rows[0],project

    def grant(self,cid,server_id,name):
        _config,_project,_epoch,effect=self._configuration_snapshot(cid,server_id,name)
        return effect

    def _configuration_snapshot(self,cid,server_id,tool_name=None):
        with self._config_lock:
            if server_id in self._configuring:
                raise ValueError('MCP server is being reconfigured; retry after local settings finish.')
            config,project=self._config(cid,server_id)
            epoch=self._config_epochs.get(server_id,0)
            if not config['enabled']:raise ValueError('MCP server is not enabled locally')
            effect=json.loads(config['grants_json']).get(tool_name) if tool_name is not None else None
            if tool_name is not None and effect not in ('read','project_write'):
                raise ValueError('This MCP tool has no explicit local grant')
            return config,project,epoch,effect

    def _assert_current(self,cid,server_id,tool_name,epoch,effect=None,read_only=False,allow_missing_grant=False):
        with self._config_lock:
            if server_id in self._configuring or self._config_epochs.get(server_id,0)!=epoch:
                raise ValueError('MCP server configuration changed before the tool call started; no new call was made.')
            config,_project=self._config(cid,server_id)
            if not config['enabled']:
                raise ValueError('MCP server was disabled before the tool call started; no new call was made.')
            current=json.loads(config['grants_json']).get(tool_name) if tool_name else None
            if tool_name and (current not in ('read','project_write') or (effect is not None and current!=effect)):
                raise ValueError('MCP tool grant changed before the call started; no new call was made.')
            if tool_name and not allow_missing_grant and current is None:
                raise ValueError('MCP tool grant changed before the call started; no new call was made.')
            if read_only and current!='read':
                raise ValueError('mcp.read only admits tools explicitly granted read access')
            if self.execution.paused:
                raise ValueError('Work is paused locally')
            if self.execution.policy(cid)['profile']=='read_only' and current not in (None,'read'):
                raise ValueError('Read only profile refuses mutating MCP tools')
            return current

    async def _serve(self,cid,server_id,epoch,config,project,queue,ready):
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters,stdio_client
        params=StdioServerParameters(command=config['command'],args=json.loads(config['args_json']),cwd=project['root'])
        try:
            with open(os.devnull,'w') as errlog:
                # The actor owns the context from enter to exit. Low-level calls
                # avoid high-level header-mismatch retries of mutating tools.
                async with Client(stdio_client(params,errlog=errlog),mode='legacy',cache=None,read_timeout_seconds=20) as client:
                    listed=await asyncio.wait_for(client.list_tools(),10)
                    if len(listed.tools)>100 or listed.next_cursor:raise ValueError('MCP server exceeds the supported 100-tool listing')
                    schemas={}
                    for tool in listed.tools:
                        if tool.name in schemas:raise ValueError('MCP server returned duplicate tool names')
                        _bounded_schema(tool.input_schema)
                        schemas[tool.name]={'name':tool.name,'description':(tool.description or '')[:2000],'input_schema':tool.input_schema}
                    if not ready.done():ready.set_result(schemas)
                    while True:
                        item=await queue.get()
                        if item is None:break
                        name,args,future,guard,effect,read_only=item
                        if future.cancelled():continue
                        try:
                            payload=await self._invoke_queued(cid,server_id,epoch,name,args,guard,effect,read_only,schemas,client)
                            if not future.done():future.set_result(payload)
                        except asyncio.CancelledError:
                            if not future.done():future.set_exception(OSError('MCP call cancelled; outcome may be unknown'))
                            raise
                        except Exception as exc:
                            if not future.done():future.set_exception(OSError('MCP call failed ('+type(exc).__name__+'); inspect outcome before retrying'))
        except asyncio.CancelledError:raise
        except Exception as exc:
            if not ready.done():ready.set_exception(OSError('MCP startup failed ('+type(exc).__name__+')'))
        finally:
            while not queue.empty():
                item=queue.get_nowait()
                if item is not None and not item[2].done():item[2].set_exception(OSError('MCP server stopped before request settled'))

    async def _invoke_queued(self,cid,server_id,epoch,name,args,guard,effect,read_only,schemas,client):
        if name not in schemas:raise ValueError('Unknown MCP tool')
        jsonschema.Draft202012Validator(schemas[name]['input_schema']).validate(args)
        if guard:guard()
        # This lock-protected check is the invocation start boundary. If
        # reconfiguration wins first, stale queued work cannot reach the SDK.
        self._assert_current(cid,server_id,name,epoch,effect,read_only)
        result=await asyncio.wait_for(client.session.call_tool(name,args,read_timeout_seconds=20),22)
        content=[];unsupported=[]
        for block in getattr(result,'content',[]):
            if block.type=='text':content.append({'type':'text','text':block.text})
            else:unsupported.append(block.type)
        payload={'ok':not getattr(result,'is_error',False),'content':content,
            'structured_content':getattr(result,'structured_content',None),'unsupported_content_types':unsupported}
        if len(json.dumps(payload).encode())>MAX_RESULT_BYTES:raise OSError('MCP result exceeds the supported size; inspect external outcome before retrying')
        return payload

    async def _actor(self,cid,server_id,expected_epoch=None):
        with self._config_lock:
            if server_id in self._configuring:
                raise ValueError('MCP server is being reconfigured; retry after local settings finish.')
            config,project=self._config(cid,server_id)
            epoch=self._config_epochs.get(server_id,0)
            if expected_epoch is not None and epoch!=expected_epoch:
                raise ValueError('MCP server configuration changed; start a new request using current settings.')
            if not config['enabled']:raise ValueError('MCP server is not enabled locally')
            actor=self.actors.get(server_id)
            if actor is None:
                queue=asyncio.Queue(maxsize=16);ready=self.loop.create_future()
                actor={'queue':queue,'ready':ready,'epoch':epoch,
                    'task':self.loop.create_task(self._serve(cid,server_id,epoch,config,project,queue,ready))}
                self.actors[server_id]=actor
            elif actor['epoch']!=epoch:
                raise OSError('MCP actor belongs to an older server configuration; reconnect it locally')
            elif actor['task'].done():raise OSError('MCP server stopped; reconnect it explicitly in local settings')
        await asyncio.wait_for(asyncio.shield(actor['ready']),15)
        if expected_epoch is not None:self._assert_current(cid,server_id,None,expected_epoch)
        return actor

    async def _tools(self,cid,server_id):
        _config,_project,epoch,_effect=self._configuration_snapshot(cid,server_id)
        actor=await self._actor(cid,server_id,epoch)
        self._assert_current(cid,server_id,None,epoch)
        grants=json.loads(self._config(cid,server_id)[0]['grants_json'])
        return [{**tool,'granted_effect':grants.get(name)} for name,tool in actor['ready'].result().items()]

    def _submit(self,coroutine,timeout=35):
        if self.closed:coroutine.close();raise RuntimeError('MCP bridge is closed')
        future=asyncio.run_coroutine_threadsafe(coroutine,self.loop)
        try:return future.result(timeout)
        except concurrent.futures.TimeoutError:
            future.cancel();raise OSError('MCP operation timed out; outcome may be unknown') from None

    def tools(self,cid,server_id):
        if self.execution.paused:raise ValueError('Work is paused locally; MCP discovery will not start a server')
        return self._submit(self._tools(cid,server_id))

    async def _call(self,cid,server_id,name,args,guard,epoch,effect,read_only):
        actor=await self._actor(cid,server_id,epoch)
        schemas=actor['ready'].result()
        if name not in schemas:raise ValueError('Unknown MCP tool')
        jsonschema.Draft202012Validator(schemas[name]['input_schema']).validate(args)
        future=self.loop.create_future()
        self._assert_current(cid,server_id,name,epoch,effect,read_only)
        await actor['queue'].put((name,args,future,guard,effect,read_only))
        return await asyncio.wait_for(future,25)

    def call(self,cid,server_id,tool_name,arguments,*,guard=None,read_only=False,request_id=None):
        _config,_project,epoch,effect=self._configuration_snapshot(cid,server_id,tool_name)
        if read_only and effect!='read':raise ValueError('mcp.read only admits tools explicitly granted read access')
        if self.execution.policy(cid)['profile']=='read_only' and effect!='read':raise ValueError('Read only profile refuses mutating MCP tools')
        if self.execution.paused:raise ValueError('Work is paused locally')
        if not isinstance(arguments,dict) or len(json.dumps(arguments).encode())>32_000:raise ValueError('MCP arguments must be a bounded object')
        request_id=request_id or str(uuid.uuid4())
        if not isinstance(request_id,str) or not request_id or len(request_id)>200:raise ValueError('Invalid MCP request ID')
        key=cid+':'+server_id+':'+request_id
        digest=hashlib.sha256(json.dumps([tool_name,arguments],sort_keys=True).encode()).hexdigest()
        with self.catalog.lock,self.catalog.db:
            rows=self.catalog.rows('SELECT * FROM mcp_requests_v1 WHERE request_key=?',(key,))
            if rows:
                if rows[0]['args_hash']!=digest:raise ValueError('MCP request ID was reused with different arguments')
                if rows[0]['state']=='settled':return json.loads(rows[0]['result_json'])
                raise OSError('MCP request outcome is uncertain or still pending; it was not replayed')
            self.catalog.db.execute('INSERT INTO mcp_requests_v1 VALUES(?,?,?,NULL,?)',(key,digest,'sending',time.time()))
        try:result=self._submit(self._call(cid,server_id,tool_name,arguments,guard,epoch,effect,read_only))
        except BaseException:
            with self.catalog.lock,self.catalog.db:self.catalog.db.execute("UPDATE mcp_requests_v1 SET state='unknown' WHERE request_key=?",(key,))
            raise
        with self.catalog.lock,self.catalog.db:
            self.catalog.db.execute("UPDATE mcp_requests_v1 SET state='settled',result_json=? WHERE request_key=?",(json.dumps(result),key))
        return result

    async def _stop(self,server_id=None):
        ids=[server_id] if server_id else list(self.actors)
        tasks=[]
        for key in ids:
            actor=self.actors.pop(key,None)
            if actor:
                if not actor['ready'].done():
                    actor['ready'].set_exception(OSError('MCP server configuration changed before startup completed'))
                actor['task'].cancel();tasks.append(actor['task'])
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)

    def stop_server(self,server_id):return self._submit(self._stop(server_id),10)
    def stop_all(self):return self._submit(self._stop(),10)

    def close(self):
        if self.closed:return
        try:self.stop_all()
        finally:
            self.closed=True;self.loop.call_soon_threadsafe(self.loop.stop);self.thread.join(3)
