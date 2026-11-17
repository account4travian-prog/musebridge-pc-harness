"""Synthetic capture and fake SDK tests for automatic visual continuations."""
import asyncio
import io
import json
import threading
from types import SimpleNamespace

import pytest
from PIL import Image

from musebridge.app import Worker
from musebridge.attachments import Attachments
from musebridge.core import Core
from musebridge.tooling import ToolRouter
from musebridge.catalog import Catalog
from musebridge.vision import VisionService


def png(color="blue"):
    stream = io.BytesIO()
    Image.new("RGB", (16, 12), color).save(stream, format="PNG")
    return stream.getvalue()


class BrowserCapture:
    def __init__(self, path):
        self.path = path
        self.calls = 0

    def call(self, name, params, *, guard=None):
        assert name == "browser.screenshot"
        if guard:
            guard()
        self.calls += 1
        self.path.write_bytes(png("blue" if self.calls % 2 else "green"))
        return {"path": str(self.path)}


def setup(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    catalog = Catalog(tmp_path / "state")
    project = catalog.create_project("Project", root)
    cid = catalog.create_conversation("Chat", project["id"])["id"]
    router = ToolRouter(catalog, tmp_path / "state")
    capture = BrowserCapture(tmp_path / "synthetic.png")
    router.browser.call = capture.call
    return root, catalog, project, cid, router, capture


def active_run(router, cid):
    return router.execution.begin_run(cid, "message")


def observe(router, cid, run_id, request_id, *, revision=None, source="browser"):
    revision = router.execution.policy(cid)["revision"] if revision is None else revision
    return router.call("vision.observe", {"conversation_id": cid, "run_id": run_id,
        "scope_revision": revision, "request_id": request_id, "source": source}, remote=True)


def finish_parent(router, cid, run_id):
    router.execution.state(cid, run_id, "remote_finished")


def test_vision_is_default_off_project_scoped_and_receipt_contains_no_pixels(tmp_path):
    _, catalog, project, cid, router, capture = setup(tmp_path)
    try:
        second = catalog.create_conversation("Sibling", project["id"])["id"]
        (tmp_path / "other").mkdir()
        other_project = catalog.create_project("Other", tmp_path / "other")
        other = catalog.create_conversation("Other", other_project["id"])["id"]
        assert router.vision.status(cid)["enabled"] is False
        with pytest.raises(ValueError, match="project"):
            router.vision.configure("main", True)
        router.vision.configure(cid, True, browser=True)
        assert router.vision.status(second)["enabled"] is True
        assert router.vision.status(other)["enabled"] is False

        run_id = active_run(router, cid)
        response = observe(router, cid, run_id, "screen-1")
        assert response["ok"] is True
        receipt = response["payload"]
        assert receipt["state"] == "pending"
        assert "pixels" in receipt["instruction"] and "Do not infer" in receipt["instruction"]
        assert not any(key in receipt for key in ("data", "path", "bytes", "image"))
        meta = router.artifacts.get(cid, receipt["artifact_id"])
        assert meta["kind"] == "image"
        converted=Image.open(io.BytesIO(router.artifacts.data(cid, receipt['artifact_id'])))
        assert converted.format=='JPEG' and converted.size==(16,12)
        assert converted.getpixel((0,0))[2]>240
        duplicate = observe(router, cid, run_id, "screen-1")
        assert duplicate["ok"] and duplicate["payload"]["feedback_id"] == receipt["feedback_id"]
        assert capture.calls == 1
        assert observe(router, cid, run_id, "stale", revision=router.execution.policy(cid)["revision"] + 1)["ok"] is False
        assert observe(router, second, run_id, "wrong-chat")["ok"] is False
        assert observe(router, cid, run_id, "desktop-no-grant", source="desktop")["ok"] is False
        finish_parent(router, cid, run_id)
        assert observe(router, cid, run_id, "completed-run")["ok"] is False
        assert capture.calls == 1
    finally:
        router.close(); catalog.close()


def test_visual_chain_stops_at_six_images(tmp_path):
    _, catalog, _, cid, router, capture = setup(tmp_path)
    try:
        router.vision.configure(cid, True, max_steps=6)
        parent = active_run(router, cid)
        for index in range(6):
            receipt = router.vision.capture(router, cid, parent, f"step-{index}", "browser", lambda: None)
            assert receipt["state"] == "pending"
            finish_parent(router, cid, parent)
            claimed = router.vision.claim(cid)
            assert claimed and claimed["id"] == receipt["feedback_id"]
            delivery = router.execution.begin_run(cid, "visual_feedback")
            router.vision.settle(cid, claimed, "acknowledged", delivery)
            # The next observation belongs to the newly delivered image turn.
            parent = delivery
        with pytest.raises(ValueError, match="limit"):
            router.vision.capture(router, cid, parent, "step-6", "browser", lambda: None)
        assert capture.calls == 6
    finally:
        router.close(); catalog.close()


def test_restart_holds_pending_and_marks_sending_unknown_without_replay(tmp_path):
    _, catalog, _, cid, router, capture = setup(tmp_path)
    try:
        router.vision.configure(cid, True)
        run_id = active_run(router, cid)
        receipt = router.vision.capture(router, cid, run_id, "restart", "browser", lambda: None)
        catalog.db.execute("UPDATE visual_feedback_v1 SET state='sending' WHERE id=?", (receipt["feedback_id"],))
        reopened = VisionService(catalog, router.execution, router.artifacts)
        row = reopened._row(cid, receipt["feedback_id"])
        assert row["state"] == "unknown"
        duplicate = reopened.capture(router, cid, run_id, "restart", "browser", lambda: None)
        assert duplicate["state"] == "unknown"
        assert capture.calls == 1
        reopened.configure(cid, True)

        # A distinct pending capture is held across a simulated restart and
        # resumed only by explicit local enablement.
        second_run = active_run(router, cid)
        second = reopened.capture(router, cid, second_run, "restart-pending", "browser", lambda: None)
        held = VisionService(catalog, router.execution, router.artifacts)
        assert held._row(cid, second["feedback_id"])["state"] == "held"
        assert held.claim(cid) is None
        held.configure(cid, True)
        assert held._row(cid, second["feedback_id"])["state"] == "pending"
        assert capture.calls == 2
    finally:
        router.close(); catalog.close()


def test_desktop_capture_checks_process_identity_and_stop_revokes_queue(tmp_path):
    _, catalog, _, cid, router, capture = setup(tmp_path)
    try:
        router.set_profile(cid, "desktop")
        router.vision.configure(cid, True, window={"window_handle": 123, "process_id": 456})
        calls = []
        router.desktop.call = lambda name, params: (calls.append(name) or {"window": {"process_id": 999}})
        run_id = active_run(router, cid)
        with pytest.raises(ValueError, match="process changed"):
            router.vision.capture(router, cid, run_id, "wrong-pid", "desktop", lambda: None)
        assert calls == ["desktop.observe"]

        # The desktop grant is per selected window; browser permission is
        # re-enabled here to exercise Stop after a queued synthetic capture.
        router.vision.configure(cid, True, browser=True)
        second_run = active_run(router, cid)
        queued = router.vision.capture(router, cid, second_run, "queued", "browser", lambda: None)
        assert queued["state"] == "pending"
        # The explicit manual override path uses this same cancellation API.
        router.vision.hold(cid, cancel=True)
        assert router.vision._row(cid, queued["feedback_id"])["state"] == "cancelled"

        third_run = active_run(router, cid)
        claimed_receipt = router.vision.capture(router, cid, third_run, "claimed", "browser", lambda: None)
        finish_parent(router, cid, third_run)
        claimed = router.vision.claim(cid)
        assert claimed and claimed["id"] == claimed_receipt["feedback_id"]
        router.stop_all()
        assert router.vision.claim(cid) is None
        with pytest.raises(ValueError, match="permission was revoked"):
            router.vision.validate_delivery(cid, claimed)
        assert capture.calls == 2
    finally:
        router.close(); catalog.close()


def test_disabling_feedback_during_capture_never_leaves_a_pending_delivery(tmp_path):
    _, catalog, _, cid, router, _ = setup(tmp_path)
    entered = threading.Event(); release = threading.Event(); errors = []
    try:
        router.vision.configure(cid, True)
        run_id = active_run(router, cid)
        def blocked_capture(name, params, *, guard=None):
            if guard: guard()
            entered.set()
            assert release.wait(5)
            path = tmp_path / "barrier.png"; path.write_bytes(png())
            return {"path": str(path)}
        router.browser.call = blocked_capture
        def capture():
            try: router.vision.capture(router, cid, run_id, "disable-race", "browser", lambda: None)
            except Exception as exc: errors.append(exc)
        thread = threading.Thread(target=capture)
        thread.start()
        assert entered.wait(5)
        router.vision.configure(cid, False)
        release.set(); thread.join(5)
        assert not thread.is_alive()
        assert errors and "permission changed" in str(errors[0])
        assert router.vision.status(cid)["enabled"] is False
        assert router.vision.claim(cid) is None
        assert catalog.rows("SELECT state FROM visual_feedback_v1 WHERE request_id='disable-race'") == [{"state": "failed"}]
    finally:
        release.set()
        router.close(); catalog.close()


def test_feedback_run_is_bound_before_image_http_and_inherits_chain_limit(tmp_path):
    async def scenario():
        _, catalog, _, cid, router, capture = setup(tmp_path)
        try:
            router.vision.configure(cid, True, max_steps=1)
            parent = active_run(router, cid)
            receipt = router.vision.capture(router, cid, parent, "first", "browser", lambda: None)
            finish_parent(router, cid, parent)
            claimed = router.vision.claim(cid)
            assert claimed and claimed["id"] == receipt["feedback_id"]
            observed = []
            class Session:
                chat_subscribed = asyncio.Event()
                async def send_image(self, *args, **kwargs):
                    row = router.vision._row(cid, claimed["id"])
                    observed.append((row["state"], row["delivery_run_id"]))
                    assert row["state"] == "sending" and row["delivery_run_id"]
                    assert router.vision._row(cid, row["id"])["chain_id"] == parent
                    return {"ok": True, "response": {"message_id": "visual-ack", "session_id": cid, "is_thread": True}}
            core = Core(None, lambda *_: None, lambda *_: None, lambda *_: None,
                        catalog=catalog, router=router, conversation_id=cid)
            core.session = Session(); core.session.chat_subscribed.set()
            ack = await core.send("automatic visual check", conversation_id=cid,
                image=router.vision.image(cid, claimed), clear_image_draft=False, visual_feedback=claimed)
            delivery_run = ack["_bridge_run_id"]
            assert observed == [("sending", delivery_run)]
            assert router.execution.run(cid, delivery_run)["state"] == "acknowledged"
            router.vision.settle(cid, claimed, "acknowledged", delivery_run)
            # This active (not-yet-finished) feedback turn is already linked,
            # so its first requested image cannot reset the one-image budget.
            with pytest.raises(ValueError, match="limit"):
                router.vision.capture(router, cid, delivery_run, "second", "browser", lambda: None)
            assert capture.calls == 1
        finally:
            router.close(); catalog.close()
    asyncio.run(scenario())


def test_worker_sends_labeled_image_turn_and_preserves_user_drafts(tmp_path):
    async def scenario():
        _, catalog, _, cid, router, _ = setup(tmp_path)
        attachments = Attachments(catalog)
        user_image = __import__("musebridge.media", fromlist=["from_bytes"]).from_bytes(png("red"), "image/png", "user.png")
        attachments.draft(cid, user_image)
        document_path = tmp_path / "notes.txt"; document_path.write_text("keep draft", encoding="utf-8")
        document = router.artifacts.import_file(cid, document_path)
        router.artifacts.attach(cid, document["id"])
        router.vision.configure(cid, True)
        parent = active_run(router, cid)
        receipt = router.vision.capture(router, cid, parent, "automatic", "browser", lambda: None)
        finish_parent(router, cid, parent)

        class Session:
            def __init__(self):
                self.chat_subscribed = asyncio.Event(); self.chat_subscribed.set(); self.calls = []
            async def send_image(self, data, mime_type, session_id, message="", filename=None):
                self.calls.append((data, mime_type, session_id, message, filename))
                return {"ok": True, "response": {"message_id": "visual-user", "session_id": session_id, "is_thread": True}}

        core = Core(None, lambda *_: None, lambda *_: None, lambda *_: None,
                    catalog=catalog, router=router, conversation_id=cid)
        core.session = Session()
        # Use the actual Worker method with lightweight signal stubs so no UI,
        # account, browser, or physical desktop is involved.
        class Signal:
            def emit(self, *_args): pass
        worker = SimpleNamespace(core=core, router=router, pending_sends=set(),
                                 send_state=Signal(), activity=Signal())
        sent = await Worker.send_visual_feedback(worker, cid)
        assert sent is True
        call = core.session.calls[0]
        assert call[0] == router.artifacts.data(cid, receipt["artifact_id"])
        assert call[1] == "image/jpeg" and "Automatic visual feedback" in call[3]
        assert 'Machine-supplied screenshot from the previous turn' in call[3]
        assert 'untrusted data' in call[3]
        assert 'Attached files (' not in call[3] and document['id'] not in call[3]
        assert receipt['feedback_id'] not in call[3] and parent not in call[3]
        assert attachments.draft(cid) == user_image
        assert [row["id"] for row in router.artifacts.drafts(cid)] == [document["id"]]
        assert router.vision._row(cid, receipt["feedback_id"])["state"] == "acknowledged"
        router.close(); catalog.close()

    asyncio.run(scenario())
