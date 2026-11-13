"""Integration checks for Core.send header inclusion and acknowledgement state."""
from __future__ import annotations

import asyncio
import json

import pytest

from musebridge.catalog import Catalog
from musebridge.core import Core
from musebridge.tooling import ToolRouter


class AckSession:
    def __init__(self, cid):
        self.chat_subscribed = asyncio.Event()
        self.chat_subscribed.set()
        self.cid = cid
        self.calls = []

    async def send_chat(self, message, session_id=None):
        self.calls.append((message, session_id))
        return {"ok": True, "response": {
            "message_id": f"user-{len(self.calls)}",
            "session_id": session_id,
            "is_thread": True,
        }}


def _make_core(cid, catalog, router, session=None):
    core = Core(None, lambda *_args: None, lambda *_args: None, lambda *_args: None,
                catalog=catalog, router=router, conversation_id=cid)
    core.session = session or AckSession(cid)
    return core


def _quiet_header_probes(monkeypatch, router):
    monkeypatch.setattr(router.context_snapshot, "project_summary", lambda cid: {
        "conversation_id": cid, "project": {"name": "Header fixture", "root": "."},
        "profile": "project", "branch": "main", "status_summary": "clean",
    })
    monkeypatch.setattr("musebridge.shells.describe_shells", lambda: {
        "powershell": {"available": True, "version": "7.5"},
        "bash": {"available": True, "version": "5.2"},
    })


def _preamble_bytes(catalog, cid):
    rows = catalog.rows("SELECT data FROM events WHERE conversation_id=? AND kind='send.context' ORDER BY seq",
                        (cid,))
    return [json.loads(row["data"])["preamble_bytes"] for row in rows]


@pytest.mark.parametrize('revoke', [False, True])
def test_finish_check_send_preserves_lineage_and_rechecks_hold_after_header(monkeypatch, tmp_path, revoke):
    from musebridge.request_queue import RequestQueue, send_next
    from musebridge.app import Worker
    from types import SimpleNamespace
    async def scenario():
        catalog = Catalog(tmp_path / 'state')
        queue = RequestQueue(catalog)
        root = tmp_path / 'project'
        root.mkdir()
        project = catalog.create_project('Check send', root)
        cid = catalog.create_conversation('Check send', project['id'])['id']
        router = ToolRouter(catalog, tmp_path / 'state')
        core = _make_core(cid, catalog, router)
        _quiet_header_probes(monkeypatch, router)
        router.finish_line.configure(cid, 'exit 0', True)
        source = router.execution.begin_run(cid)
        router.finish_line.bind_run(cid, source)
        assert router.call('file.write', {'conversation_id': cid, 'path': 'file.txt', 'text': 'new'})['ok']
        router.execution.state(cid, source, 'remote_finished')
        router.finish_line.queue_after_run(cid, source, queue)
        worker = SimpleNamespace(router=router, queue=queue, voice_active=False)
        original = router.context_snapshot.project_summary
        def summary(cid):
            if revoke:queue.hold(cid)
            return original(cid)
        monkeypatch.setattr(router.context_snapshot, 'project_summary', summary)
        async def send(cid, text, image, continuation_of=None):
            stamp = router.execution.generation
            return await core.send(text, conversation_id=cid, continuation_of=continuation_of,
                finish_check=True, send_guard=lambda: Worker.queue_send_guard(worker, cid, stamp))
        try:
            if revoke:
                with pytest.raises(ValueError, match='held or revoked'):
                    await send_next(queue, cid, send, validate=router.finish_line.delivery_allowed)
                assert core.session.calls == []
                assert catalog.rows('SELECT * FROM outbox') == []
                assert router.execution.active_run(cid) is None
                assert queue.rows(cid)[0]['state'] == 'unknown'
            else:
                assert await send_next(queue, cid, send, validate=router.finish_line.delivery_allowed)
                assert len(core.session.calls) == 1
                delivery = queue.rows(cid)[0]['run_id']
                assert router.execution.run(cid, delivery)['origin'] == 'finish_check'
                assert router.finish_line.summary(cid, delivery)['request_id'] == source
        finally:
            router.close()
            catalog.close()
    asyncio.run(scenario())


def test_header_first_send_then_user_text_and_scope_changes(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / "project"
        root.mkdir()
        catalog = Catalog(tmp_path / "state")
        project = catalog.create_project("Header fixture", root, "Initial project instructions.")
        cid = catalog.create_conversation("Header chat", project["id"])["id"]
        router = ToolRouter(catalog, tmp_path / "state")
        _quiet_header_probes(monkeypatch, router)
        core = _make_core(cid, catalog, router)
        try:
            await core.send("first user request")
            first_wire = core.session.calls[0][0]
            first_header, marker, first_user = first_wire.partition("\n\nUser request:\n")
            assert marker
            assert first_header.startswith("[MuseBridge] Windows PC.")
            assert len(first_header.encode("utf-8")) < 800
            assert _preamble_bytes(catalog, cid)[0] < 800
            assert first_user == "first user request"

            await core.send("second user request verbatim")
            assert core.session.calls[1][0] == "second user request verbatim"
            assert _preamble_bytes(catalog, cid) == [
                _preamble_bytes(catalog, cid)[0], 0,
            ]

            catalog.update_project(project["id"], "Changed project instructions.")
            await core.send("after instructions changed")
            changed_instructions = core.session.calls[2][0]
            assert changed_instructions.startswith("[MuseBridge] Windows PC.")
            assert _preamble_bytes(catalog, cid)[2] > 0

            router.set_profile(cid, "read_only")
            await core.send("after profile changed")
            profile_header = core.session.calls[3][0]
            assert "Profile: Read only" in profile_header
            assert _preamble_bytes(catalog, cid)[3] > 0

            # A new Core represents a new connection and therefore has a fresh
            # HeaderManager, even for the same local conversation.
            next_core = _make_core(cid, catalog, router)
            assert next_core.header_manager is not core.header_manager
            await next_core.send("first message after reconnect")
            reconnect_header = next_core.session.calls[0][0]
            assert reconnect_header.startswith("[MuseBridge] Windows PC.")
            assert _preamble_bytes(catalog, cid)[-1] > 0
        finally:
            router.close()
            catalog.close()

    asyncio.run(scenario())


def test_unknown_send_does_not_commit_header_for_next_attempt(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / "project"
        root.mkdir()
        catalog = Catalog(tmp_path / "state")
        project = catalog.create_project("Unknown header", root)
        cid = catalog.create_conversation("Unknown send", project["id"])["id"]
        router = ToolRouter(catalog, tmp_path / "state")
        _quiet_header_probes(monkeypatch, router)

        class LoseFirstAck(AckSession):
            async def send_chat(self, message, session_id=None):
                self.calls.append((message, session_id))
                if len(self.calls) == 1:
                    raise ConnectionError("fake lost acknowledgement")
                return {"ok": True, "response": {
                    "message_id": f"user-{len(self.calls)}", "session_id": session_id,
                    "is_thread": True,
                }}

        session = LoseFirstAck(cid)
        core = _make_core(cid, catalog, router, session)
        try:
            with pytest.raises(ConnectionError):
                await core.send("uncertain first attempt")
            assert cid not in core.header_manager.states
            assert catalog.rows("SELECT state FROM outbox ORDER BY created") == [{"state": "unknown"}]

            await core.send("new acknowledged attempt")
            assert session.calls[1][0].startswith("[MuseBridge] Windows PC.")
            assert cid in core.header_manager.states
            assert catalog.rows("SELECT state FROM outbox ORDER BY created") == [
                {"state": "unknown"}, {"state": "acknowledged"},
            ]
        finally:
            router.close()
            catalog.close()

    asyncio.run(scenario())


def test_acknowledgement_commits_header_even_if_local_run_tracking_fails(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / "project"
        root.mkdir()
        catalog = Catalog(tmp_path / "state")
        project = catalog.create_project("Tracking failure", root)
        cid = catalog.create_conversation("Known ack", project["id"])["id"]
        router = ToolRouter(catalog, tmp_path / "state")
        _quiet_header_probes(monkeypatch, router)
        core = _make_core(cid, catalog, router)

        def fail_local_tracking(*_args):
            raise RuntimeError("synthetic local tracker failure")

        core.run_tracker.acknowledge = fail_local_tracking
        try:
            ack = await core.send("known remote acknowledgement")
            assert ack["message_id"] == "user-1"
            assert ack["_bridge_tracking_ok"] is False
            assert cid in core.header_manager.states
            assert catalog.rows("SELECT state FROM outbox ORDER BY created") == [{"state": "acknowledged"}]

            core.run_tracker.acknowledge = lambda *_args: None
            await core.send("message after known ack")
            assert core.session.calls[1][0] == "message after known ack"
            assert _preamble_bytes(catalog, cid) == [
                _preamble_bytes(catalog, cid)[0], 0,
            ]
        finally:
            router.close()
            catalog.close()

    asyncio.run(scenario())
