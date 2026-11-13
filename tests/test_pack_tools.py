"""Real owned resource checks for the consolidated pack adapters."""
import sys
import time
from pathlib import Path

import pytest

from musebridge.catalog import Catalog
from musebridge.tooling import ToolRouter
from test_browser import local_site


@pytest.fixture
def harness(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    catalog = Catalog(tmp_path / 'state')
    project = catalog.create_project('Packs',root)
    cid = catalog.create_conversation('Owned fixtures',project['id'])['id']
    router = ToolRouter(catalog,tmp_path / 'state')
    try:
        yield root,catalog,router,cid
    finally:
        router.close()
        catalog.close()


def test_browser_aliases_select_check_eval_and_terminated_script_recovery(harness, local_site):
    _, _, router, cid = harness
    def call(name,**params):return router.call(name,{'conversation_id':cid,**params})
    assert call('browser.navigate',url=local_site)['ok']
    assert call('browser.snapshot',filter='initial')['ok']
    js = '''(() => {document.body.insertAdjacentHTML('beforeend', '<select id="choice"><option value="old">Old</option><option value="new">New</option></select><input type="checkbox" id="check">');return 1})()'''
    assert call('browser.eval',js=js)['payload']['value'] == 1
    assert call('browser.act',action='select',selector='#choice',text='new')['ok']
    assert call('browser.act',action='check',selector='#check')['ok']
    result = call('browser.eval',js='({choice:document.querySelector("#choice").value, checked:document.querySelector("#check").checked})')
    assert result['payload']['value'] == {'choice':'new','checked':True}
    start = time.monotonic()
    failed = call('browser.eval',js='(() => {while (true) {}})()')
    assert not failed['ok'] and time.monotonic() - start < 8
    assert call('browser.snapshot')['ok']
    capture = call('browser.screenshot')
    assert capture['ok'] and 'path' not in capture['payload'] and capture['payload'].get('artifact_id')
    router.set_profile(cid,'read_only')
    assert not call('browser.eval',js='document.body.remove()')['ok']
    assert call('browser.snapshot')['ok']


def test_mcp_call_read_grant_works_under_read_only_and_write_does_not(harness):
    root, catalog, router, cid = harness
    script = Path(__file__).resolve().parents[1] / 'tools' / 'mcp_fixture_server.py'
    server = router.mcp.configure_server(cid,'Owned',sys.executable,[str(script)],
        grants={'echo':'read','uncertain':'project_write'},enabled=True)
    catalog.preference('tool_packs',['mcp'])
    router.set_profile(cid,'read_only')
    result = router.call('mcp.call',{'conversation_id':cid,'server_id':server,
        'tool_name':'echo','arguments':{'text':'read permitted'}},remote=True)
    assert result['ok'] and result['payload']['content'][0]['text'] == 'fixture:read permitted'
    denied = router.call('mcp.call',{'conversation_id':cid,'server_id':server,
        'tool_name':'uncertain','arguments':{'marker':str(root / 'no.txt')}},remote=True)
    assert not denied['ok'] and not (root / 'no.txt').exists()
    assert len(router.call('mcp.list',{'conversation_id':cid})['payload']['servers']) == 1


def test_registration_snapshot_matches_connection_until_reconnect(harness):
    _, catalog, router, cid = harness
    assert len(router.begin_registration()) == 29
    catalog.preference('tool_packs',[])
    assert len(router.registration_specs()) == 20
    from musebridge.capabilities import manifest
    assert len(manifest(router,cid)['tools']) == 28  # health is separate discovery metadata.
    router.connection_specs = None
    assert len(manifest(router,cid)['tools']) == 19


def test_other_projects_desktop_pack_does_not_grant_this_chat_inspection(harness, monkeypatch):
    _, catalog, router, cid = harness
    project = catalog.create_project('Other project',Path(catalog.project(catalog.conversation(cid)['project_id'])['root']))
    other = catalog.create_conversation('Desktop owner',project['id'])['id']
    router.set_profile(other,'desktop')
    assert 'desktop.snapshot' in router.registration_specs()
    effects = []
    monkeypatch.setattr(router.desktop,'call',lambda *a,**kw: effects.append(a) or {})
    denied = router.call('desktop.snapshot',{'conversation_id':cid,'window':123})
    assert not denied['ok'] and not effects
    assert denied['error']['code'] == 'needs_desktop_control'


def test_task_batch_failure_and_partial_effect_have_truthful_wire_and_hook_state(harness, monkeypatch):
    _, catalog, router, cid = harness
    hook_states = []
    def hook(chat,event,*args,**kw):
        if event == 'after_tool':hook_states.append(kw['state'])
        return {'jobs':[],'skipped':[]}
    monkeypatch.setattr(router.hooks,'run_event',hook)
    rejected = router.call('task.set',{'conversation_id':cid,'tasks':[{'title':'Cannot be complete yet','state':'complete'}]})
    assert not rejected['ok']
    assert rejected['error']['tasks'] == [] and rejected['error']['items'][0]['error']['hint']
    assert router.tasks.list(cid) == []
    latest = router.execution.runs(cid)[0]
    assert router.execution.calls(cid,latest['id'])[0]['state'] == 'failed'
    assert hook_states == ['failed']
    partial = router.call('task.set',{'conversation_id':cid,'tasks':[{'title':'Valid'},{'title':'Blocked','depends_on':['unknown']} ]})
    assert partial['ok'] and partial['payload']['partial']
    assert hook_states == ['failed','partial']


def test_terminal_open_send_read_close_keep_input_and_resource_ownership(harness):
    _, catalog, router, cid = harness
    other = catalog.create_conversation('Other')['id']
    result = router.call('terminal.open',{'conversation_id':cid,'argv':[sys.executable,'-u','-i']})
    assert result['ok'], result
    identifier = result['payload']['terminal_id']
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            ready = router.call('terminal.read',{'conversation_id':cid,'terminal_id':identifier})
            if '>>>' in ready['payload']['output']:break
            time.sleep(.05)
        sent = router.call('terminal.send',{'conversation_id':cid,'terminal_id':identifier,
            'text':'print("PACK-ROUNDTRIP")\r','wait_s':2})
        assert sent['ok'] and sent['payload']['input_receipt']['state'] == 'done'
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            output = router.call('terminal.read',{'conversation_id':cid,'terminal_id':identifier})['payload']['output']
            if any(line.strip() == 'PACK-ROUNDTRIP' for line in output.splitlines()):break
            time.sleep(.05)
        assert any(line.strip() == 'PACK-ROUNDTRIP' for line in output.splitlines())
        denied = router.call('terminal.send',{'conversation_id':other,'terminal_id':identifier,'text':'print("no")\r'})
        assert not denied['ok']
    finally:
        assert router.call('terminal.close',{'conversation_id':cid,'terminal_id':identifier})['ok']
