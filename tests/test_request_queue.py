import asyncio
import pytest
from musebridge.catalog import Catalog
from musebridge.request_queue import RequestQueue,send_next


def test_queue_orders_edits_and_cancels_without_cross_chat_access(tmp_path):
    catalog=Catalog(tmp_path);cid=catalog.create_conversation('Queued')['id'];other=catalog.create_conversation('Other')['id']
    queue=RequestQueue(catalog)
    first=queue.add(cid,'first');second=queue.add(cid,'second')
    queue.edit(cid,second,'second revised')
    with pytest.raises(ValueError):queue.cancel(other,first)
    assert queue.claim(cid)['id']==first
    queue.settle(cid,first,'acknowledged','run')
    queue.cancel(cid,second)
    assert queue.claim(cid) is None
    assert [r['state'] for r in queue.rows(cid)]==['acknowledged','cancelled']
    with pytest.raises(ValueError):queue.add('main','cannot infer main completion')
    catalog.close()


def test_restart_holds_pending_and_unknown_requests_are_never_auto_replayed(tmp_path):
    catalog=Catalog(tmp_path);cid=catalog.create_conversation('Queue')['id'];queue=RequestQueue(catalog)
    first=queue.add(cid,'sent but no response');queue.claim(cid);second=queue.add(cid,'still pending');catalog.close()
    catalog=Catalog(tmp_path);queue=RequestQueue(catalog)
    assert queue.claim(cid) is None
    assert [r['state'] for r in queue.rows(cid)]==['unknown','pending']
    queue.resume(cid);assert queue.claim(cid)['id']==second
    catalog.close()


def test_cancelled_send_is_unknown_and_holds_following_requests(tmp_path):
    async def scenario():
        catalog=Catalog(tmp_path);cid=catalog.create_conversation('Queue')['id'];queue=RequestQueue(catalog)
        queue.add(cid,'first');queue.add(cid,'second');started=asyncio.Event();calls=[]
        async def send(*args):calls.append(args);started.set();await asyncio.Event().wait()
        task=asyncio.create_task(send_next(queue,cid,send));await started.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert len(calls)==1 and queue.claim(cid) is None
        assert [r['state'] for r in queue.rows(cid)]==['unknown','pending']
        catalog.close()
    asyncio.run(scenario())
