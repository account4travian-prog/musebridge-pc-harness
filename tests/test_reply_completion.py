from musebridge.catalog import Catalog
from musebridge.execution import Execution,TurnRuns


def test_reply_completion_without_task_status_requires_bound_finished_idle_reply(tmp_path):
    c=Catalog(tmp_path);cid=c.create_conversation('Reply-only turn')['id'];e=Execution(c);tracker=TurnRuns(e)
    try:
        run=tracker.begin(cid);tracker.acknowledge(cid,run,{'message_id':'user','session_id':cid,'is_thread':True})
        tracker.poll(cid,10**15);assert e.run(cid,run)['state']=='acknowledged'  # silence alone
        def event(name,**fields):return {'type':'event','event':name,'payload':{'session_id':cid,**fields}}
        tracker.feed(cid,event('delta.message_done',message_id='foreign',reply_to_message_id='someone-else',content='unrelated'))
        tracker.poll(cid,10**15);assert e.run(cid,run)['state']=='acknowledged'
        tracker.feed(cid,event('delta.message_start',message_id='answer',reply_to_message_id='user'))
        tracker.poll(cid,10**15);assert e.run(cid,run)['state']=='acknowledged'
        tracker.feed(cid,event('delta.message_done',message_id='answer',reply_to_message_id='user',content='Read finished'))
        tracker.feed(cid,event('agent.status',activity_code='working'))
        tracker.poll(cid,10**15);assert e.run(cid,run)['state']=='acknowledged'
        tracker.feed(cid,event('agent.status',activity_code='idle'))
        tracker.poll(cid);assert e.run(cid,run)['state']=='acknowledged'  # quiet grace has not passed
        assert tracker.can_continue(cid,10**15)
        assert e.run(cid,run)['state']=='remote_reply_finished'
        assert c.rows("SELECT kind FROM events WHERE kind='run.remote_finished'")==[]
    finally:c.close()


def test_running_task_blocks_reply_only_completion_and_retired_events_do_not_end_next_turn(tmp_path):
    c=Catalog(tmp_path);cid=c.create_conversation('Tasks')['id'];e=Execution(c);tracker=TurnRuns(e)
    def event(name,**fields):return {'type':'event','event':name,'payload':{'session_id':cid,**fields}}
    try:
        first=tracker.begin(cid);tracker.acknowledge(cid,first,{'message_id':'u1','session_id':cid,'is_thread':True})
        tracker.feed(cid,event('task.status',task_id='t1',status='running'))
        tracker.feed(cid,event('delta.message_done',message_id='a1',reply_to_message_id='u1',content='Progress'))
        tracker.feed(cid,event('agent.status',activity_code='idle'))
        assert not tracker.can_continue(cid,10**15)
        tracker.feed(cid,event('task.status',task_id='t1',status='completed'))
        assert tracker.can_continue(cid,10**15) and e.run(cid,first)['state']=='remote_finished'
        second=tracker.begin(cid);tracker.acknowledge(cid,second,{'message_id':'u2','session_id':cid,'is_thread':True})
        tracker.feed(cid,event('delta.message_done',message_id='a1',reply_to_message_id='u1',content='Late replay'))
        assert not tracker.can_continue(cid,10**15) and e.run(cid,second)['state']=='acknowledged'
    finally:c.close()
