import asyncio

import pytest

from musebridge.catalog import Catalog
from musebridge.goals import GoalRunner


def event(name, session, *, message_id=None, parent=None, **fields):
    payload = {"session_id": session, **fields}
    if message_id is not None:
        payload["message_id"] = message_id
    if parent is not None:
        payload["reply_to_message_id"] = parent
    return {"type": "event", "event": name, "payload": payload}


def setup_catalog(tmp_path):
    catalog = Catalog(tmp_path / "catalog")
    project = catalog.create_project("Example", str(tmp_path))
    conversation = catalog.create_conversation("Goal", project["id"])
    cid, remote = conversation["id"], conversation["remote_id"]
    catalog.plan(cid, text="Inspect, implement, verify", goal="Finish the example", state="active")
    return catalog, cid, remote


def test_known_ack_with_lost_local_tracking_pauses_without_continuation(tmp_path):
    catalog,cid,remote=setup_catalog(tmp_path);calls=[];statuses=[]
    async def send(conversation_id,prompt):
        calls.append(prompt)
        return {'message_id':'known-user','session_id':remote,'is_thread':True,'_bridge_tracking_ok':False}
    runner=GoalRunner(catalog,send,lambda cid,text:statuses.append(text))
    try:
        result=asyncio.run(runner.run(cid,max_rounds=3,turn_timeout_s=1))
        assert len(calls)==1 and result['goal_state']=='paused' and result['rounds']==0
        assert 'acknowledged' in statuses[-1] and 'not confirmed' not in statuses[-1]
    finally:catalog.close()


def terminal_events(runner, cid, remote, number):
    user_id, task_id, reply_id = f"u{number}", f"t{number}", f"r{number}"
    runner.feed(cid, event("task.status", remote, task_id=task_id, status="running"))
    runner.feed(cid, event("delta.message_done", remote, message_id=reply_id,
                           parent=user_id, content=f"Round {number} verified"))
    runner.feed(cid, event("task.status", remote, task_id=task_id, status="completed"))


def test_pre_ack_events_are_held_and_multiple_rounds_stop_at_limit(tmp_path):
    catalog, cid, remote = setup_catalog(tmp_path)
    calls, statuses = [], []
    runner = None

    async def send(conversation_id, prompt):
        number = len(calls) + 1
        calls.append(prompt)
        terminal_events(runner, conversation_id, remote, number)  # Events precede send acknowledgement.
        return {"result": {"message_id": f"u{number}", "session_id": remote, "is_thread": True}}

    runner = GoalRunner(catalog, send, lambda c, text: statuses.append((c, text)))
    try:
        result = asyncio.run(runner.run(cid, max_rounds=2, turn_timeout_s=2))
        assert result == {"conversation_id": cid, "rounds": 2, "goal_state": "paused"}
        assert len(calls) == 2
        assert "round 1 of 2" in calls[0]
        assert "round 2 of 2" in calls[1]
        assert "2-round continuation limit" in statuses[-1][1]
        assert catalog.context(cid)["goal_state"] == "paused"
    finally:
        catalog.close()


def test_wrong_chat_and_retired_turn_events_cannot_finish_next_round(tmp_path):
    catalog, cid, remote = setup_catalog(tmp_path)
    other = catalog.create_conversation("Other")["id"]
    calls = []
    runner = None

    async def send(conversation_id, prompt):
        number = len(calls) + 1
        calls.append(prompt)
        if number == 1:
            terminal_events(runner, cid, remote, 1)
        else:
            # Wrong conversation and wrong remote chat must not advance the active turn.
            runner.feed(other, event("task.status", remote, task_id="foreign", status="completed"))
            runner.feed(cid, event("task.status", "another-remote", task_id="wrong-chat", status="completed"))
            # A full replay of the prior round must also be retired.
            terminal_events(runner, cid, remote, 1)
            terminal_events(runner, cid, remote, 2)
        return {"result": {"message_id": f"u{number}", "session_id": remote, "is_thread": True}}

    runner = GoalRunner(catalog, send, lambda *_: None)
    try:
        result = asyncio.run(runner.run(cid, max_rounds=2, turn_timeout_s=2))
        assert result["rounds"] == 2
        assert len(calls) == 2
        assert catalog.context(cid)["goal_state"] == "paused"
    finally:
        catalog.close()


def test_unknown_ack_pauses_without_resending(tmp_path):
    catalog, cid, _ = setup_catalog(tmp_path)
    calls, statuses = [], []

    async def send(*_):
        calls.append(1)
        return {"status": "unknown"}

    runner = GoalRunner(catalog, send, lambda c, text: statuses.append(text))
    try:
        result = asyncio.run(runner.run(cid, max_rounds=5, turn_timeout_s=1))
        assert len(calls) == 1
        assert result["goal_state"] == "paused"
        assert "No retry was sent" in statuses[-1]
    finally:
        catalog.close()


def test_timeout_pauses_and_does_not_resend(tmp_path):
    catalog, cid, remote = setup_catalog(tmp_path)
    calls = []

    async def send(conversation_id, prompt):
        calls.append(prompt)
        return {"result": {"message_id": "u1", "session_id": remote, "is_thread": True}}

    runner = GoalRunner(catalog, send, lambda *_: None)
    try:
        result = asyncio.run(runner.run(cid, max_rounds=3, turn_timeout_s=0.05))
        assert len(calls) == 1
        assert result["goal_state"] == "paused"
    finally:
        catalog.close()


def test_disconnect_pauses_and_reports_bounded_reason(tmp_path):
    catalog, cid, remote = setup_catalog(tmp_path)
    calls, statuses = [], []
    runner = None

    async def scenario():
        sent = asyncio.Event()

        async def send(conversation_id, prompt):
            calls.append(prompt)
            sent.set()
            return {"result": {"message_id": "u1", "session_id": remote, "is_thread": True}}

        nonlocal runner
        runner = GoalRunner(catalog, send, lambda c, text: statuses.append((c, text)))
        task = asyncio.create_task(runner.run(cid, max_rounds=3, turn_timeout_s=10))
        await sent.wait()
        runner.disconnect(cid, "d" * 1000)
        result = await task
        return result

    try:
        result = asyncio.run(scenario())
        assert len(calls) == 1
        assert result["goal_state"] == "paused"
        assert len(statuses[-1][1]) <= 240
    finally:
        catalog.close()


def test_cancellation_pauses_without_starting_another_round(tmp_path):
    catalog, cid, remote = setup_catalog(tmp_path)
    sent = None
    calls = []

    async def scenario():
        nonlocal sent
        sent = asyncio.Event()

        async def send(conversation_id, prompt):
            calls.append(prompt)
            sent.set()
            return {"result": {"message_id": "u1", "session_id": remote, "is_thread": True}}

        runner = GoalRunner(catalog, send, lambda *_: None)
        task = asyncio.create_task(runner.run(cid, max_rounds=4, turn_timeout_s=20))
        await sent.wait()
        await runner.stop()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(scenario())
        assert len(calls) == 1
        assert catalog.context(cid)["goal_state"] == "paused"
    finally:
        catalog.close()


def test_goal_state_changed_by_tools_stops_continuation_without_overwrite(tmp_path):
    catalog, cid, remote = setup_catalog(tmp_path)
    calls = []
    runner = None

    async def send(conversation_id, prompt):
        calls.append(prompt)
        catalog.plan(cid, state="complete")
        terminal_events(runner, cid, remote, 1)
        return {"result": {"message_id": "u1", "session_id": remote, "is_thread": True}}

    runner = GoalRunner(catalog, send, lambda *_: None)
    try:
        result = asyncio.run(runner.run(cid, max_rounds=5, turn_timeout_s=2))
        assert len(calls) == 1
        assert result["rounds"] == 1
        assert result["goal_state"] == "complete"
    finally:
        catalog.close()


def test_main_chat_and_concurrent_run_are_rejected(tmp_path):
    catalog, cid, _ = setup_catalog(tmp_path)
    calls = []

    async def send(*_):
        calls.append(1)
        return {}

    runner = GoalRunner(catalog, send, lambda *_: None)
    try:
        catalog.plan("main", goal="Main goal", state="active")
        main = asyncio.run(runner.run("main", turn_timeout_s=0.1))
        assert main["goal_state"] == "paused"
        assert not calls

        async def concurrent():
            runner._active = True
            try:
                with pytest.raises(RuntimeError, match="already running"):
                    await runner.run(cid)
            finally:
                runner._active = False
        asyncio.run(concurrent())
    finally:
        catalog.close()
