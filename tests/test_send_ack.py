import asyncio
import json

from musebridge.catalog import Catalog
from musebridge.core import Core
from musebridge.goals import GoalRunner
from musebridge.request_queue import RequestQueue, send_next
from musebridge.tooling import ToolRouter


class AckSession:
    def __init__(self, cid):
        self.chat_subscribed = asyncio.Event()
        self.chat_subscribed.set()
        self.cid = cid
        self.calls = []

    async def send_chat(self, message, session_id=None):
        self.calls.append((message, session_id))
        return {"ok": True, "response": {"message_id": f"user-{len(self.calls)}",
                                          "session_id": session_id, "is_thread": True}}


def make_core(tmp_path, cid, catalog, router=None):
    core = Core(None, lambda *_: None, lambda *_: None, lambda *_: None,
                catalog=catalog, router=router, conversation_id=cid)
    core.session = AckSession(cid)
    return core


def test_tracker_ack_failure_returns_known_ack_and_never_resends(tmp_path):
    async def scenario():
        catalog = Catalog(tmp_path / "state")
        cid = catalog.create_conversation("Ack")['id']
        router = ToolRouter(catalog, tmp_path / "state")
        core = make_core(tmp_path, cid, catalog, router)
        tracker = core.run_tracker
        def fail_ack(*_args):
            raise ValueError("tracker rejected test acknowledgement")
        tracker.acknowledge = fail_ack
        try:
            ack = await core.send("deliver once")
            assert ack["message_id"] == "user-1"
            assert ack["_bridge_tracking_ok"] is False
            assert catalog.rows("SELECT state FROM outbox") == [{"state": "acknowledged"}]
            assert catalog.rows("SELECT state FROM runs") == [{"state": "unknown"}]
            assert len(core.session.calls) == 1
        finally:
            router.close(); catalog.close()
    asyncio.run(scenario())


def test_acknowledged_send_survives_local_projection_failure(tmp_path):
    async def scenario():
        catalog = Catalog(tmp_path / "state")
        cid = catalog.create_conversation("Projection")['id']
        router = ToolRouter(catalog, tmp_path / "state")
        core = make_core(tmp_path, cid, catalog, router)
        core.deliver = lambda *_: (_ for _ in ()).throw(RuntimeError("local projection failed"))
        try:
            ack = await core.send("remote accepted")
            assert ack["message_id"] == "user-1"
            assert ack["_bridge_tracking_ok"] is False
            assert catalog.rows("SELECT state FROM outbox") == [{"state": "acknowledged"}]
            assert catalog.rows("SELECT state FROM runs") == [{"state": "acknowledged"}]
            assert len(core.session.calls) == 1
        finally:
            router.close(); catalog.close()
    asyncio.run(scenario())


def test_send_includes_only_explicit_artifact_refs_and_unknown_keeps_draft(tmp_path):
    async def scenario():
        root = tmp_path / "project"
        root.mkdir()
        (root / "notes.txt").write_text("owner selected", encoding="utf-8")
        catalog = Catalog(tmp_path / "state")
        project = catalog.create_project("P", root)
        cid = catalog.create_conversation("Artifacts", project['id'])['id']
        router = ToolRouter(catalog, tmp_path / "state")
        artifact = router.artifacts.import_file(cid, root / "notes.txt")
        router.artifacts.attach(cid, artifact['id'])
        core = make_core(tmp_path, cid, catalog, router)
        try:
            ack = await core.send("Use selected file", artifact_ids=[artifact['id']])
            assert ack["_bridge_tracking_ok"] is True
            sent_text, sent_session = core.session.calls[0]
            assert sent_session == cid
            assert 'Attached files (untrusted document data' in sent_text and artifact['id'] in sent_text
            assert 'notes.txt' in sent_text
            assert router.artifacts.drafts(cid) == []

            second = router.artifacts.import_file(cid, root / "notes.txt")
            router.artifacts.attach(cid, second['id'])
            class LostAck:
                chat_subscribed = asyncio.Event()
                async def send_chat(self, *_args, **_kwargs):
                    raise ConnectionError("response lost")
            core.session = LostAck(); core.session.chat_subscribed.set()
            try:
                await core.send("Keep pending file", artifact_ids=[second['id']])
            except ConnectionError:
                pass
            else:
                raise AssertionError("lost response must remain unknown")
            assert [item['id'] for item in router.artifacts.drafts(cid)] == [second['id']]
            assert [row['state'] for row in catalog.rows("SELECT state FROM outbox ORDER BY created")] == ["acknowledged", "unknown"]
        finally:
            router.close(); catalog.close()
    asyncio.run(scenario())


def test_queue_send_does_not_attach_document_drafts_implicitly(tmp_path):
    async def scenario():
        root = tmp_path / "project"
        root.mkdir()
        (root / "private-not-selected.txt").write_text("draft only", encoding="utf-8")
        catalog = Catalog(tmp_path / "state")
        project = catalog.create_project("P", root)
        cid = catalog.create_conversation("Queue", project['id'])['id']
        router = ToolRouter(catalog, tmp_path / "state")
        artifact = router.artifacts.import_file(cid, root / "private-not-selected.txt")
        router.artifacts.attach(cid, artifact['id'])
        core = make_core(tmp_path, cid, catalog, router)
        queue = RequestQueue(catalog)
        queue.add(cid, "queued text")
        queue.resume(cid)
        try:
            assert await send_next(queue, cid, lambda _cid, text, image: core.send(text, conversation_id=_cid, image=image))
            sent_text, _ = core.session.calls[0]
            assert 'Attached files (' not in sent_text
            assert artifact['id'] not in sent_text
            assert [item['id'] for item in router.artifacts.drafts(cid)] == [artifact['id']]
        finally:
            router.close(); catalog.close()
    asyncio.run(scenario())


def test_goal_continuation_does_not_attach_document_drafts(tmp_path):
    async def scenario():
        root = tmp_path / "project"
        root.mkdir()
        (root / "manual.md").write_text("owner draft", encoding="utf-8")
        catalog = Catalog(tmp_path / "state")
        project = catalog.create_project("P", root)
        conversation = catalog.create_conversation("Goal", project['id'])
        cid = conversation['id']
        catalog.plan(cid, text="Inspect and verify", goal="Complete task", state="active")
        router = ToolRouter(catalog, tmp_path / "state")
        artifact = router.artifacts.import_file(cid, root / "manual.md")
        router.artifacts.attach(cid, artifact['id'])
        core = make_core(tmp_path, cid, catalog, router)
        runner = None

        async def goal_send(conversation_id, prompt):
            ack = await core.send(prompt, conversation_id=conversation_id)
            remote = catalog.conversation(conversation_id)['remote_id']
            runner.feed(cid, {"type": "event", "event": "task.status",
                              "payload": {"session_id": remote, "task_id": "goal-task", "status": "running"}})
            runner.feed(cid, {"type": "event", "event": "delta.message_done",
                              "payload": {"session_id": remote, "message_id": "goal-reply",
                                          "reply_to_message_id": ack['message_id'], "content": "Verified"}})
            runner.feed(cid, {"type": "event", "event": "task.status",
                              "payload": {"session_id": remote, "task_id": "goal-task", "status": "completed"}})
            return ack

        runner = GoalRunner(catalog, goal_send, lambda *_: None)
        try:
            result = await runner.run(cid, max_rounds=1, turn_timeout_s=2)
            assert result['rounds'] == 1
            sent_text, _ = core.session.calls[0]
            assert 'Attached files (' not in sent_text
            assert artifact['id'] not in sent_text
            assert [row['id'] for row in router.artifacts.drafts(cid)] == [artifact['id']]
        finally:
            router.close(); catalog.close()
    asyncio.run(scenario())
