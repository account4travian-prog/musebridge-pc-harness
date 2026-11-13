import asyncio
import json
import sqlite3
import threading
from pathlib import Path

import pytest
from musebridge.catalog import Catalog
from musebridge.tooling import ToolRouter
from musebridge.execution import Execution,TurnRuns,SCHEMA_VERSION
from musebridge.core import Core


@pytest.fixture
def harness(tmp_path):
    root=tmp_path/'project';root.mkdir()
    catalog=Catalog(tmp_path/'state');project=catalog.create_project('Windows',root)
    cid=catalog.create_conversation('A',project['id'])['id']
    other=catalog.create_conversation('B',project['id'])['id']
    router=ToolRouter(catalog,tmp_path/'state')
    yield root,catalog,router,cid,other
    router.close();catalog.close()


def test_manifest_is_truthful_and_profiles_gate_actual_effects(harness):
    root,catalog,router,cid,other=harness
    def call(name,**kwargs):return router.call(name,{'conversation_id':cid,**kwargs})
    manifest=call('bridge.capabilities')['payload']
    assert manifest['workflow']['owner']=='Muse VM'
    assert not manifest['workflow']['local_skill_runtime'] and not manifest['sandbox']
    assert manifest['execution']['shell']=='powershell'
    assert manifest['execution']['inline_wait_cap_seconds']==25
    assert manifest['tools']['file.write']['enabled']
    assert 'desktop.act' not in manifest['tools']  # Optional pack is not registered yet.
    revision=manifest['scope']['revision']
    router.set_profile(cid,'read_only')
    assert router.execution.policy(other)['profile']=='read_only'  # Shared project policy.
    for name,args in [('file.write',{'path':'forbidden','text':'no','create':True}),
                      ('job.start',{'request_id':'no','command':'Set-Content forbidden no'}),
                      ('browser.open',{'url':'http://127.0.0.1/'}),
                      ('desktop.act',{'window_handle':1,'action':'focus'}),
                      ('plan.update',{'goal':'not changed'})]:
        assert not call(name,**args)['ok']
    assert not list(root.iterdir())
    assert catalog.context(cid)['goal']==''
    assert call('workspace.list')['ok']
    router.set_profile(cid,'project')
    assert not call('file.write',path='stale',text='no',create=True,scope_revision=revision)['ok']
    assert not (root/'stale').exists()
    assert call('file.write',path='allowed',text='yes',create=True)['ok']
    assert (root/'allowed').read_text()=='yes'


def test_cross_chat_run_binding_and_failed_calls_are_durable(harness):
    root,catalog,router,cid,other=harness
    run=router.execution.begin_run(cid)
    denied=router.call('file.write',{'conversation_id':other,'run_id':run,'path':'no','text':'no','create':True})
    assert not denied['ok'] and not (root/'no').exists()
    assert not router.call('file.read',{'conversation_id':other,'path':'x'},expected_conversation_id=cid)['ok']
    result=router.call('file.read',{'conversation_id':cid,'path':'absent','run_id':run})
    assert not result['ok']
    rows=router.execution.calls(cid,run)
    assert len(rows)==2 and all(row['state']=='failed' for row in rows)
    assert [row['effect'] for row in rows] == ['rejected', 'read']
    assert rows[0]['error_code'] and 'absent' not in json.dumps(rows)
    events=catalog.rows('SELECT kind,data FROM events WHERE conversation_id=? ORDER BY seq',(cid,))
    kinds=[e['kind'] for e in events]
    assert kinds==['run.started','tool.accepted','tool.result','tool.accepted','tool.started','tool.result']
    router.execution.state(cid,run,'remote_finished')
    assert not router.call('file.write',{'conversation_id':cid,'path':'late','text':'no','create':True,'run_id':run})['ok']


def test_queued_browser_action_rechecks_revocation_before_dispatch(harness):
    root,catalog,router,cid,other=harness
    entered=threading.Event();release=threading.Event();effects=[]
    def slow_dispatch(name,params):
        if params.get('url','').endswith('/first'):
            entered.set();assert release.wait(5)
        effects.append(params['url'])
        return {'title':'test'}
    router.browser._dispatch=slow_dispatch
    results=[]
    first=threading.Thread(target=lambda:results.append(router.call('browser.open',{'conversation_id':cid,'url':'http://test/first'})))
    second=threading.Thread(target=lambda:results.append(router.call('browser.open',{'conversation_id':cid,'url':'http://test/second'})))
    try:
        first.start();assert entered.wait(5);second.start()
        # Wait for the second action to have actually reached the worker queue.
        import time
        deadline=time.monotonic()+5
        while router.browser._queue.qsize()<1 and time.monotonic()<deadline:time.sleep(.01)
        assert router.browser._queue.qsize()==1
        router.paused=True;router.paused=False  # A quick Stop/Resume still revokes old admissions.
        release.set();first.join(5);second.join(5)
        assert not first.is_alive() and not second.is_alive()
        assert effects==['http://test/first']
        assert len(results)==2 and sum(r['ok'] for r in results)==1
    finally:release.set();first.join(6);second.join(6)


def test_interrupted_runs_and_profiles_survive_catalog_upgrade(tmp_path):
    catalog=Catalog(tmp_path);cid=catalog.create_conversation('Existing')['id']
    catalog.draft(cid,'keep');catalog.message(cid,'m','Muse','history')
    execution=Execution(catalog);execution.set_profile(cid,'read_only')
    run=execution.begin_run(cid);execution.start_call(cid,'file.read','read',run)
    catalog.close();catalog=Catalog(tmp_path);execution=Execution(catalog)
    try:
        assert execution.policy(cid)['profile']=='read_only'
        assert execution.run(cid,run)['state']=='unknown'
        assert execution.calls(cid,run)[0]['state']=='unknown'
        assert catalog.draft(cid)=='keep' and catalog.messages(cid)[0]['text']=='history'
        assert catalog.db.execute('PRAGMA user_version').fetchone()[0]==SCHEMA_VERSION
    finally:catalog.close()
    db=sqlite3.connect(tmp_path/'harness.sqlite');db.execute('PRAGMA user_version=999');db.close()
    with pytest.raises(RuntimeError,match='newer'):Catalog(tmp_path)


def test_run_terminal_is_correlated_even_before_ack(harness):
    root,catalog,router,cid,other=harness
    tracker=TurnRuns(router.execution)
    remote=catalog.conversation(cid)['remote_id']
    def event(name,**data):return {'type':'event','event':name,'payload':{'session_id':remote,**data}}
    run=tracker.begin(cid)
    tracker.feed(other,event('task.status',task_id='wrong',status='completed'))
    tracker.feed(cid,event('task.status',task_id='task1',status='running'))
    tracker.feed(cid,event('delta.message_done',message_id='answer',reply_to_message_id='user',content='Done'))
    tracker.feed(cid,event('task.status',task_id='task1',status='completed'))
    tracker.acknowledge(cid,run,{'result':{'message_id':'user','session_id':remote,'is_thread':True}})
    assert router.execution.run(cid,run)['state']=='remote_finished'
    tracker.feed(cid,event('task.status',task_id='task1',status='completed'))
    assert len(catalog.rows("SELECT * FROM events WHERE kind='run.remote_finished'"))==1
    # Remote terminal status is never promoted to locally verified success.
    assert not catalog.rows("SELECT * FROM events WHERE kind='run.succeeded'")


def test_send_keeps_its_original_conversation_while_waiting_for_lock(tmp_path):
    async def scenario():
        catalog=Catalog(tmp_path);cid=catalog.create_conversation('Original')['id']
        core=Core(None,lambda _:None,lambda *_:None,lambda _:None,catalog=catalog,conversation_id=cid)
        class Session:
            chat_subscribed=asyncio.Event();calls=[]
            async def send_chat(self,*a,**kw):self.calls.append((a,kw))
        core.session=Session();core.session.chat_subscribed.set()
        await core.send_lock.acquire()
        pending=asyncio.create_task(core.send('original draft',conversation_id=cid))
        await asyncio.sleep(0)
        core.conversation_id='main';core.send_lock.release()
        with pytest.raises(ConnectionError,match='Conversation changed'):await pending
        assert core.session.calls==[] and catalog.rows('SELECT * FROM outbox')==[]
        catalog.close()
    asyncio.run(scenario())


def test_remote_mutation_requires_current_run_and_cannot_resume_revoked_run(harness):
    root,catalog,router,cid,other=harness
    params={'conversation_id':cid,'path':'sentinel','text':'no','create':True}
    assert not router.call('file.write',params,expected_conversation_id=cid,remote=True)['ok']
    run=router.execution.begin_run(cid);revision=router.execution.policy(cid)['revision']
    params.update(run_id=run,scope_revision=revision)
    router.paused=True;router.paused=False
    assert not router.call('file.write',params,expected_conversation_id=cid,remote=True)['ok']
    assert not (root/'sentinel').exists()
    params['run_id']=router.execution.begin_run(cid)
    assert router.call('file.write',params,expected_conversation_id=cid,remote=True)['ok']
