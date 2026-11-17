"""Finish-line checks use real file publication and bounded, durable queue lineage."""
import asyncio
from types import SimpleNamespace

import pytest

from musebridge.catalog import Catalog
from musebridge.request_queue import RequestQueue, send_next
from musebridge.tooling import ToolRouter


@pytest.fixture
def harness(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    catalog = Catalog(tmp_path / 'state')
    queue = RequestQueue(catalog)
    project = catalog.create_project('Finish line', root)
    cid = catalog.create_conversation('Work', project['id'])['id']
    router = ToolRouter(catalog, tmp_path / 'state')
    router.finish_line.configure(cid, 'python -m pytest -q', True)
    try:
        yield root, catalog, router, cid, queue
    finally:
        router.close()
        catalog.close()


def begin(router, cid, source=None, goal=False):
    run = router.execution.begin_run(cid, 'goal' if goal else 'finish_check' if source else 'message')
    router.finish_line.bind_run(cid, run, source, goal)
    return run


def write(router, cid, path='output.txt', text='changed'):
    result = router.call('file.write', {'conversation_id': cid, 'path': path, 'text': text})
    assert result['ok'], result
    return result


def finish(router, cid, run):
    router.execution.state(cid, run, 'remote_finished')


def test_exactly_one_followup_for_all_changes_and_continuation_edits(harness, monkeypatch):
    root, catalog, router, cid, queue = harness
    run = begin(router, cid)
    write(router, cid)
    write(router, cid, 'second.txt')
    assert (root / 'second.txt').read_text() == 'changed'
    assert router.finish_line.summary(cid)['unverified_changes'] == 2
    monkeypatch.setattr(router.context_snapshot, 'environment', lambda: {'shells': {}, 'tools': {}})
    context = router.call('bridge.context', {'conversation_id': cid})
    assert context['ok'] and 'unverified_changes: 2' in context['payload']
    finish(router, cid, run)
    first = router.finish_line.queue_after_run(cid, run, queue)
    for _ in range(3):
        assert router.finish_line.queue_after_run(cid, run, queue) is None
    assert len(queue.rows(cid)) == 1
    assert 'You changed 2 files' in queue.rows(cid)[0]['text']
    sent = []
    async def send(queued_cid, text, image, continuation_of=None):
        sent.append(text)
        followup = begin(router, cid, continuation_of)
        write(router, cid, 'third.txt')
        finish(router, cid, followup)
        return {'_bridge_run_id': followup}
    assert asyncio.run(send_next(queue, cid, send, validate=router.finish_line.delivery_allowed))
    followup = queue.rows(cid)[0]['run_id']
    assert router.finish_line.queue_after_run(cid, followup, queue) is None
    assert len(sent) == 1 and queue.rows(cid)[0]['id'] == first
    assert router.finish_line.summary(cid, followup)['files_changed'] == 3
    # The queue's unique request key survives a lost reservation projection.
    with catalog.lock, catalog.db:
        catalog.db.execute('UPDATE finish_requests_v1 SET queued=NULL')
    assert router.finish_line.queue_after_run(cid, followup, queue) == first
    assert len(queue.rows(cid)) == 1


@pytest.mark.parametrize('command,unchecked', [('exit 0', 0), ('exit 3', 1)])
def test_only_zero_exit_after_last_edit_suppresses_prompt(harness, command, unchecked):
    _, _, router, cid, queue = harness
    run = begin(router, cid)
    write(router, cid)
    result = router.call('shell.run', {'conversation_id': cid, 'command': command})
    assert result['ok'] and result['payload']['exit_code'] == (0 if not unchecked else 3)
    assert router.finish_line.summary(cid)['unverified_changes'] == unchecked
    finish(router, cid, run)
    assert bool(router.finish_line.queue_after_run(cid, run, queue)) == bool(unchecked)


def test_old_running_and_retried_check_cannot_verify_later_edits(harness, monkeypatch):
    _, _, router, cid, _ = harness
    run = begin(router, cid)
    write(router, cid)
    state = {'state': 'running', 'exit_code': None}
    monkeypatch.setattr(router.jobs, 'status', lambda *_: state)
    router.finish_line.watch_check(cid, run, 'job')
    assert router.finish_line.summary(cid)['unverified_changes'] == 1
    write(router, cid, 'later.txt')
    state.update(state='succeeded', exit_code=0)
    router.finish_line.watch_check(cid, run, 'job')
    summary = router.finish_line.summary(cid)
    assert summary['checked_revision'] == 1 and summary['revision'] == 2
    assert summary['unverified_changes'] == 1
    router.finish_line.watch_check(cid, run, 'new-job')
    assert router.finish_line.summary(cid)['unverified_changes'] == 0


def test_check_before_any_change_and_noop_write_do_not_verify_new_content(harness):
    _, _, router, cid, _ = harness
    begin(router, cid)
    assert router.call('shell.run', {'conversation_id': cid, 'command': 'exit 0'})['ok']
    write(router, cid)
    revision = router.finish_line.summary(cid)['revision']
    write(router, cid)
    summary = router.finish_line.summary(cid)
    assert summary['revision'] == revision and summary['unverified_changes'] == 1


@pytest.mark.parametrize('condition', ['paused', 'held', 'read_only', 'disabled', 'main', 'unknown', 'new_request', 'workspace'])
def test_ineligible_runs_never_queue(harness, condition):
    root, catalog, router, cid, queue = harness
    if condition == 'main':
        with catalog.lock, catalog.db:
            catalog.db.execute('UPDATE conversations SET project_id=? WHERE id=?',
                               (catalog.conversation(cid)['project_id'], 'main'))
        cid = 'main'
    run = begin(router, cid)
    write(router, cid)
    finish(router, cid, run)
    if condition == 'paused':
        router.paused = True
    elif condition == 'held':
        queue.hold(cid)
    elif condition == 'read_only':
        router.set_profile(cid, 'read_only')
    elif condition == 'disabled':
        router.finish_line.configure(cid, 'exit 0', False)
    elif condition == 'unknown':
        with catalog.lock, catalog.db:
            catalog.db.execute("UPDATE runs SET state='unknown' WHERE id=?", (run,))
    elif condition == 'new_request':
        begin(router, cid)
    elif condition == 'workspace':
        other = catalog.create_project('Other', root)
        with catalog.lock, catalog.db:
            catalog.db.execute('UPDATE conversations SET project_id=? WHERE id=?', (other['id'], cid))
    assert router.finish_line.queue_after_run(cid, run, queue) is None
    assert queue.rows(cid) == []


def test_queue_hold_stop_restart_and_new_request_fence_pending_prompt(harness):
    _, catalog, router, cid, queue = harness
    run = begin(router, cid)
    write(router, cid)
    finish(router, cid, run)
    identifier = router.finish_line.queue_after_run(cid, run, queue)
    queue.hold()
    assert queue.claim(cid) is None
    queue.resume(cid)
    restarted = RequestQueue(catalog)
    assert restarted.claim(cid) is None
    assert router.finish_line.queue_after_run(cid, run, restarted) is None
    restarted.resume(cid)
    begin(router, cid)
    sends = []
    async def send(*args, **kwargs):
        sends.append(args)
    assert not asyncio.run(send_next(restarted, cid, send, validate=router.finish_line.delivery_allowed))
    assert sends == [] and restarted.rows(cid)[0]['state'] == 'cancelled'
    assert restarted.rows(cid)[0]['id'] == identifier


def test_other_conversation_cannot_supply_check_evidence(harness):
    root, catalog, router, cid, _ = harness
    run = begin(router, cid)
    write(router, cid)
    other_root=root.parent/'other-project'
    other_root.mkdir()
    other_project=catalog.create_project('Other project',other_root)
    other = catalog.create_conversation('Other', other_project['id'])['id']
    begin(router, other)
    assert router.call('shell.run', {'conversation_id': other, 'command': 'exit 0'})['ok']
    assert router.finish_line.summary(cid, run)['unverified_changes'] == 1
    with pytest.raises(ValueError):
        router.finish_line.summary(other, run)


def test_task_verify_records_new_check_without_upgrading_old_receipt(harness):
    _, _, router, cid, _ = harness
    begin(router, cid)
    write(router, cid)
    task = router.tasks.create(cid, 'Check file', check_command='exit 0')
    result = router.call('task.verify', {'conversation_id': cid, 'task_id': task['task_id'], 'paths': ['output.txt']})
    assert result['ok'] and result['payload']['verification']['passed']
    assert router.finish_line.summary(cid)['unverified_changes'] == 0
    write(router, cid, 'later.txt')
    result = router.call('task.verify', {'conversation_id': cid, 'task_id': task['task_id'],
        'job_id': result['payload']['job_id'], 'paths': ['output.txt']})
    assert result['ok'] and result['payload']['already_verified']
    assert router.finish_line.summary(cid)['unverified_changes'] == 1


def test_worker_goal_substitution_uses_queue_and_never_adds_sixth_round(harness):
    from musebridge.app import Worker
    _, catalog, router, cid, queue = harness
    sent = []
    class Core:
        conversation_id = cid
        async def send(self, text, *, conversation_id, continuation_of=None, goal_round=False, **kwargs):
            run = begin(router, cid, continuation_of, goal_round)
            write(router, cid, str(len(sent)) + '.txt')
            finish(router, cid, run)
            sent.append(text)
            return {'_bridge_run_id': run}
    async def visual(*_args, **_kwargs):
        return False
    worker = SimpleNamespace(router=router, catalog=catalog, queue=queue, core=Core(),
                             goal=SimpleNamespace(round_number=1), send_visual_feedback=visual)
    worker.queue_finish_check = lambda cid: Worker.queue_finish_check(worker, cid)
    async def scenario():
        worker.loop = asyncio.get_running_loop()
        for number in range(1, 6):
            worker.goal.round_number = number
            await Worker.goal_send(worker, cid, 'ordinary goal round')
        worker.queue_finish_check(cid)
    asyncio.run(scenario())
    assert len(sent) == 5 and sum('no check has run since' in text for text in sent) == 1
    assert len(queue.rows(cid)) == 1 and not queue.has_pending(cid)
    state = router.finish_line.summary(cid)
    assert state['rounds'] == state['round_limit'] == 5


def test_fifth_goal_round_with_first_unchecked_edit_cannot_queue(harness):
    _, _, router, cid, queue = harness
    source = None
    for _ in range(5):
        source = begin(router, cid, source, True)
        finish(router, cid, source)
    # Simulate an edit published at the end of round five.
    router.finish_line.changed(cid, source, 'late.txt')
    assert router.finish_line.queue_after_run(cid, source, queue) is None
    assert queue.rows(cid) == []


def test_changed_check_settings_cancel_pending_automation(harness):
    _, _, router, cid, queue = harness
    run = begin(router, cid)
    write(router, cid)
    finish(router, cid, run)
    router.finish_line.queue_after_run(cid, run, queue)
    router.finish_line.configure(cid, 'exit 0', True)
    assert not router.finish_line.delivery_allowed(cid, run)


def test_failed_task_verification_hook_does_not_clear_feedback(harness):
    _, catalog, router, cid, _ = harness
    begin(router, cid)
    write(router, cid)
    router.hooks.set_hook('project:' + catalog.conversation(cid)['project_id'],
                         'Fail verification', 'before_verification', 'exit 2', enabled=True)
    task = router.tasks.create(cid, 'Check', check_command='exit 0')
    params = {'conversation_id': cid, 'task_id': task['task_id']}
    result = router.call('task.verify', params)
    for hook in router.hooks.refresh(cid):
        if hook.get('job_id'):
            router.jobs.wait(hook['job_id'], timeout_s=3)
    result = router.call('task.verify', params)
    assert not result['ok']
    assert router.finish_line.summary(cid)['unverified_changes'] == 1


def test_worker_waits_for_settled_turn_then_sends_once(harness):
    from musebridge.app import Worker
    _, catalog, router, cid, queue = harness
    run = begin(router, cid)
    write(router, cid)
    finish(router, cid, run)
    settled = [False]
    sent = []
    signal = SimpleNamespace(emit=lambda *_: None)
    async def scenario():
        ready = asyncio.Event()
        ready.set()
        async def send(text, **kwargs):
            sent.append(text)
            followup = begin(router, cid, kwargs['continuation_of'])
            finish(router, cid, followup)
            return {'_bridge_run_id': followup}
        core = SimpleNamespace(conversation_id=cid, _remote_exists=True, send=send,
            run_tracker=SimpleNamespace(poll=lambda *_: None, can_continue=lambda *_: settled[0]),
            session=SimpleNamespace(registered=ready, chat_subscribed=ready), _opened=ready)
        async def visual(*_args, **_kwargs):
            return False
        worker = SimpleNamespace(conversation_id=cid, core=core, catalog=catalog, router=router,
            goal_future=None, voice_active=False, pending_sends=set(), queue=queue,
            send_state=signal, activity=signal, send_visual_feedback=visual)
        worker.queue_finish_check = lambda cid: Worker.queue_finish_check(worker, cid)
        worker.queue_send_guard = lambda cid, stamp: Worker.queue_send_guard(worker, cid, stamp)
        task = asyncio.create_task(Worker.drive_queue(worker))
        try:
            await asyncio.sleep(.5)
            assert sent == [] and queue.rows(cid) == []
            settled[0] = True
            await asyncio.sleep(1)
            assert len(sent) == 1 and queue.rows(cid)[0]['state'] == 'acknowledged'
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_unknown_automatic_send_holds_and_is_never_replayed(harness):
    _, _, router, cid, queue = harness
    run = begin(router, cid)
    write(router, cid)
    finish(router, cid, run)
    router.finish_line.queue_after_run(cid, run, queue)
    async def send(*_args, **_kwargs):
        raise ConnectionError('lost acknowledgment')
    with pytest.raises(ConnectionError):
        asyncio.run(send_next(queue, cid, send, validate=router.finish_line.delivery_allowed))
    assert cid in queue.held and queue.rows(cid)[0]['state'] == 'unknown'
    queue.resume(cid)
    assert queue.claim(cid) is None
    assert router.finish_line.queue_after_run(cid, run, queue) is None


def test_visual_goal_round_keeps_current_request_and_limit(harness):
    from musebridge.app import Worker
    _, catalog, router, cid, queue = harness
    source = begin(router, cid)
    write(router, cid)
    finish(router, cid, source)
    async def visual(*_args, **kwargs):
        assert kwargs['goal_round']
        delivery = begin(router, cid, source, True)
        finish(router, cid, delivery)
        return {'_bridge_run_id': delivery}
    worker = SimpleNamespace(router=router, catalog=catalog, queue=queue,
        core=SimpleNamespace(conversation_id=cid), send_visual_feedback=visual,
        goal_last_run='stale-prior-goal')
    async def scenario():
        worker.loop = asyncio.get_running_loop()
        return await Worker.goal_send(worker, cid, 'goal round')
    ack = asyncio.run(scenario())
    assert worker.goal_last_run == ack['_bridge_run_id']
    state = router.finish_line.summary(cid, worker.goal_last_run)
    assert state['request_id'] == source and state['round_limit'] == 5


def test_v3_queue_migration_preserves_pending_requests(tmp_path):
    import sqlite3
    database = tmp_path / 'harness.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('''CREATE TABLE request_queue_v1(
            id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,text TEXT NOT NULL,state TEXT NOT NULL,
            created REAL NOT NULL,updated REAL NOT NULL,mime_type TEXT,filename TEXT,image BLOB,run_id TEXT)''')
        db.execute("INSERT INTO request_queue_v1 VALUES('legacy','main','Keep this request','pending',1,1,NULL,NULL,NULL,NULL)")
        db.execute('PRAGMA user_version=3')
    catalog = Catalog(tmp_path)
    try:
        queue = RequestQueue(catalog)
        from musebridge.execution import SCHEMA_VERSION
        assert catalog.db.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
        assert queue.rows('main')[0]['text'] == 'Keep this request'
        assert queue.rows('main')[0]['automatic_run'] is None
        assert queue.rows('main')[0]['state'] == 'pending'
        assert queue.claim('main') is None
    finally:
        catalog.close()
