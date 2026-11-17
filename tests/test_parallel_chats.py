"""Fake multiplexed SDK with real file effects and explicit cross-chat oracles."""
import asyncio
from types import SimpleNamespace

import pytest

from musebridge.catalog import Catalog
from musebridge.core import Core
from musebridge.tooling import ToolRouter
from musebridge.live_chats import ConversationLimit


class MultiplexedSession:
    def __init__(self):
        self.chat_subscribed=asyncio.Event()
        self.registered=asyncio.Event()
        self.registered.set()
        self.queues={}
        self.open=set()
        self.calls=[]
    async def open_side_chat(self,remote):
        return SimpleNamespace(exists=True)
    async def subscribe_chat(self,remote=None,*,opened=None):
        queue=self.queues.setdefault(remote,asyncio.Queue())
        self.open.add(remote)
        self.chat_subscribed.set()
        if opened:opened.set()
        try:
            while True:
                event=await queue.get()
                if event is None:return
                yield event
        finally:
            self.open.discard(remote)
            self.chat_subscribed.clear()  # Pinned SDK compatibility-event behavior.
    async def send_chat(self,text,session_id=None):
        self.calls.append((text,session_id))
        return {'ok':True,'response':{'message_id':'user-'+str(len(self.calls)),
            'session_id':session_id,'is_thread':True}}


def setup(tmp_path,monkeypatch):
    catalog=Catalog(tmp_path/'state')
    router=ToolRouter(catalog,tmp_path/'state')
    conversations=[]
    for name in ('A','B','C','D'):
        root=tmp_path/name
        root.mkdir()
        project=catalog.create_project(name,root)
        conversations.append(catalog.create_conversation(name,project['id'])['id'])
    core=Core(None,lambda *_:None,lambda *_:None,lambda *_:None,
        catalog=catalog,router=router,conversation_id=conversations[0])
    core.session=MultiplexedSession()
    monkeypatch.setattr(router.context_snapshot,'project_summary',lambda *_:{'branch':'main','status_summary':'clean'})
    monkeypatch.setattr('musebridge.shells.describe_shells',lambda:{'powershell':{'available':True,'version':'7.5'}})
    return catalog,router,core,conversations


def test_two_chats_keep_runs_and_file_effects_when_view_switches(tmp_path,monkeypatch):
    async def scenario():
        catalog,router,core,(a,b,_,_)=setup(tmp_path,monkeypatch)
        try:
            await core.prepare_chat(core.session)
            first=await core.send('Task A',conversation_id=a)
            await core.select_conversation(b)
            assert router.execution.run(a,first['_bridge_run_id'])['state']=='acknowledged'
            second=await core.send('Task B',conversation_id=b)
            results=await asyncio.gather(*(asyncio.to_thread(core.tool,'file.write',
                {'conversation_id':cid,'run_id':ack['_bridge_run_id'],'path':'result.txt','text':text})
                for cid,ack,text in ((a,first,'A bytes'),(b,second,'B bytes'))))
            assert all(result['ok'] for result in results),results
            assert (tmp_path/'A/result.txt').read_text()=='A bytes'
            assert (tmp_path/'B/result.txt').read_text()=='B bytes'
            wrong=core.tool('file.write',{'conversation_id':b,'run_id':first['_bridge_run_id'],
                'path':'cross-write.txt','text':'must not exist'})
            assert not wrong['ok'] and not (tmp_path/'B/cross-write.txt').exists()
            assert 'conversation_changed' in wrong['error']
            omitted=core.tool('file.write',{'conversation_id':a,'path':'omitted.txt','text':'no'})
            assert not omitted['ok'] and 'ambiguous_conversation' in omitted['error']
            assert len(core.session.open)==3  # Main plus A and B, one session object.
            assert 'Work binding:' in core.session.calls[0][0]
            router.stop_all()
            for cid,ack in ((a,first),(b,second)):
                stopped=core.tool('file.write',{'conversation_id':cid,'run_id':ack['_bridge_run_id'],
                    'path':'after-stop.txt','text':'no'})
                assert not stopped['ok']
                assert not (tmp_path/catalog.project(catalog.conversation(cid)['project_id'])['name']/'after-stop.txt').exists()
        finally:
            await core.live_chats.close()
            router.close()
            catalog.close()
    asyncio.run(scenario())


def test_shared_sdk_event_does_not_disable_other_subscription_or_mix_transcripts(tmp_path,monkeypatch):
    async def scenario():
        catalog,router,core,(a,b,_,_)=setup(tmp_path,monkeypatch)
        try:
            await core.prepare_chat(core.session)
            await core.send('A',conversation_id=a)
            await core.select_conversation(b)
            await core.send('B',conversation_id=b)
            for cid,text in ((a,'A reply'),(b,'B reply')):
                await core.session.queues[cid].put({'type':'event','event':'message.assistant',
                    'payload':{'session_id':cid,'message_id':'same-message-id','content':text,'role':'assistant'}})
            await asyncio.sleep(.05)
            assert catalog.messages(a)[-1]['text']=='A reply'
            assert catalog.messages(b)[-1]['text']=='B reply'
            await core.session.queues[b].put(None)
            await asyncio.sleep(.05)
            assert not core.session.chat_subscribed.is_set()
            assert core.chat_ready(a) and not core.chat_ready(b)
            count=len(core.session.calls)
            await core.send('A still routes',conversation_id=a)
            assert len(core.session.calls)==count+1 and core.session.calls[-1][1]==a
        finally:
            await core.live_chats.close()
            router.close()
            catalog.close()
    asyncio.run(scenario())


def test_three_active_project_limit_does_not_interrupt_viewed_or_running_chats(tmp_path,monkeypatch):
    async def scenario():
        catalog,router,core,chats=setup(tmp_path,monkeypatch)
        try:
            await core.prepare_chat(core.session)
            for cid in chats[:3]:await core.send('Work',conversation_id=cid)
            await core.select_conversation(chats[3])
            assert core.conversation_id==chats[3]
            assert len(core.session.open)==4
            with pytest.raises(ConversationLimit):await core.send('No slot',conversation_id=chats[3])
            assert len(core.session.calls)==3
            assert all(router.execution.active_run(cid) for cid in chats[:3])
        finally:
            await core.live_chats.close()
            router.close()
            catalog.close()
    asyncio.run(scenario())


def test_same_canonical_project_writer_is_exclusive_until_run_finishes(tmp_path,monkeypatch):
    async def scenario():
        catalog,router,core,(a,_,_,_)=setup(tmp_path,monkeypatch)
        # A second project record at exactly the same physical root is still shared.
        project=catalog.create_project('Alias',tmp_path/'A')
        b=catalog.create_conversation('Same files',project['id'])['id']
        try:
            await core.prepare_chat(core.session)
            one=await core.send('A',conversation_id=a)
            two=await core.send('B',conversation_id=b)
            assert core.tool('file.write',{'conversation_id':a,'run_id':one['_bridge_run_id'],
                'path':'owned.txt','text':'first'})['ok']
            assert core.tool('file.read',{'conversation_id':b,'run_id':two['_bridge_run_id'],'path':'owned.txt'})['ok']
            blocked=core.tool('file.write',{'conversation_id':b,'run_id':two['_bridge_run_id'],'path':'owned.txt','text':'wrong'})
            assert not blocked['ok'] and 'project_busy' in blocked['error']
            assert (tmp_path/'A/owned.txt').read_text()=='first'
            router.execution.state(a,one['_bridge_run_id'],'remote_finished')
            allowed=core.tool('file.write',{'conversation_id':b,'run_id':two['_bridge_run_id'],'path':'owned.txt','text':'second'})
            assert allowed['ok'],allowed
            assert (tmp_path/'A/owned.txt').read_text()=='second'
            router.execution.state(b,two['_bridge_run_id'],'remote_finished')
            restored=router.call('change.restore',{'conversation_id':b,'run':two['_bridge_run_id']})
            assert restored['ok'],restored
            assert (tmp_path/'A/owned.txt').read_text()=='first'
        finally:
            await core.live_chats.close()
            router.close()
            catalog.close()
    asyncio.run(scenario())


def test_old_voice_binding_is_refused_and_read_context_supplies_current_pair(tmp_path,monkeypatch):
    async def scenario():
        catalog,router,core,(a,b,_,_)=setup(tmp_path,monkeypatch)
        monkeypatch.setattr(router.context_snapshot,'environment',lambda:{'shells':{},'tools':{}})
        try:
            await core.prepare_chat(core.session)
            old=await core.send('Typed request',conversation_id=a)
            await core.send('Other work',conversation_id=b)
            core.run_tracker.stop(a,'interrupted')
            voice=router.execution.begin_run(a,'voice')
            router.execution.state(a,voice,'acknowledged')
            context=core.tool('bridge.context',{'conversation_id':a})
            assert context['ok'] and 'Active run: '+voice in context['payload']
            stale=core.tool('file.write',{'conversation_id':a,'run_id':old['_bridge_run_id'],'path':'stale','text':'no'})
            assert not stale['ok'] and not (tmp_path/'A/stale').exists()
            fresh=core.tool('file.write',{'conversation_id':a,'run_id':voice,'path':'voice-contract.txt','text':'current'})
            assert fresh['ok'],fresh
        finally:
            await core.live_chats.close()
            router.close()
            catalog.close()
    asyncio.run(scenario())


def test_reaped_subscription_cannot_reenable_old_mutations(tmp_path,monkeypatch):
    import time
    async def scenario():
        catalog,router,core,(a,b,_,_)=setup(tmp_path,monkeypatch)
        try:
            await core.prepare_chat(core.session)
            ack=await core.send('A',conversation_id=a)
            await core.select_conversation(b)
            router.execution.state(a,ack['_bridge_run_id'],'remote_finished')
            core.run_tracker.stop(a,'interrupted')
            core.live_chats.states[a].idle_since=time.monotonic()-61
            await core.live_chats.reap()
            assert a not in core.live_conversations() and a not in core.session.open
            late=core.tool('file.write',{'conversation_id':a,'run_id':ack['_bridge_run_id'],'path':'late.txt','text':'no'})
            assert not late['ok'] and not (tmp_path/'A/late.txt').exists()
        finally:
            await core.live_chats.close()
            router.close()
            catalog.close()
    asyncio.run(scenario())


def test_background_hook_cannot_bypass_shared_root_writer_lease(tmp_path,monkeypatch):
    async def scenario():
        catalog,router,core,(a,_,_,_)=setup(tmp_path,monkeypatch)
        b=catalog.create_conversation('Reader with hook',catalog.conversation(a)['project_id'])['id']
        try:
            await core.prepare_chat(core.session)
            one=await core.send('A',conversation_id=a)
            two=await core.send('B',conversation_id=b)
            assert core.tool('file.write',{'conversation_id':a,'run_id':one['_bridge_run_id'],'path':'owned','text':'owner'})['ok']
            router.hooks.set_hook('project:'+catalog.conversation(a)['project_id'],'Read hook','before_tool',
                "Set-Content -Path should-not-exist -Value wrong",enabled=True,tool_filter=['file.read'])
            read=core.tool('file.read',{'conversation_id':b,'run_id':two['_bridge_run_id'],'path':'owned'})
            assert not read['ok'] and 'project_busy' in read['error']
            assert not (tmp_path/'A/should-not-exist').exists()
            assert catalog.rows('SELECT * FROM hook_invocations_v1')==[]
        finally:
            await core.live_chats.close()
            router.close()
            catalog.close()
    asyncio.run(scenario())


def test_lost_stream_before_ack_marks_tracking_uncertain_and_holds_only_that_queue(tmp_path,monkeypatch):
    from musebridge.app import Worker
    from musebridge.request_queue import RequestQueue
    async def scenario():
        catalog,router,core,(a,b,_,_)=setup(tmp_path,monkeypatch)
        queue=RequestQueue(catalog)
        queue.resume(a)
        queue.resume(b)
        worker=SimpleNamespace(queue=queue,router=router,goal=SimpleNamespace(feed=lambda *_:None))
        core.event_observer=lambda cid,event:Worker.observe_event(worker,cid,event)
        entered=asyncio.Event()
        release=asyncio.Event()
        original=core.session.send_chat
        async def delayed(text,session_id=None):
            entered.set()
            await release.wait()
            return await original(text,session_id)
        core.session.send_chat=delayed
        try:
            await core.prepare_chat(core.session)
            pending=asyncio.create_task(core.send('A uncertain subscription',conversation_id=a))
            await entered.wait()
            await core.session.queues[a].put(None)
            await asyncio.sleep(.05)
            release.set()
            ack=await pending
            assert not ack['_bridge_tracking_ok']
            assert a in queue.held and b in queue.enabled
            assert catalog.rows('SELECT state FROM outbox')==[{'state':'acknowledged'}]
            assert router.execution.run(a,ack['_bridge_run_id'])['state']=='unknown'
        finally:
            release.set()
            await core.live_chats.close()
            router.close()
            catalog.close()
    asyncio.run(scenario())


def test_archive_during_header_preparation_refuses_send_without_outbox(tmp_path,monkeypatch):
    async def scenario():
        catalog,router,core,(a,_,_,_)=setup(tmp_path,monkeypatch)
        def archive_during_preparation(cid):
            catalog.archive_conversation(cid)
            return {}
        monkeypatch.setattr(router.context_snapshot,'project_summary',archive_during_preparation)
        try:
            await core.prepare_chat(core.session)
            with pytest.raises(ValueError,match='archived'):
                await core.send('Must not reach the service',conversation_id=a)
            assert core.session.calls==[]
            assert catalog.rows('SELECT state FROM outbox')==[]
            assert router.execution.active_run(a) is None
        finally:
            await core.live_chats.close()
            router.close()
            catalog.close()
    asyncio.run(scenario())
