"""Regression probes for consequential MB-11 through MB-23 integration risks."""
from musebridge.catalog import Catalog
from musebridge.execution import Execution, TurnRuns
from musebridge.tooling import ToolRouter
from musebridge.workspace import Workspace


def test_mutation_then_exception_is_not_journaled_as_failed(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()
    catalog = Catalog(tmp_path / "state")
    project = catalog.create_project("review", str(project_root))
    cid = catalog.create_conversation("review", project["id"])["id"]
    router = ToolRouter(catalog, tmp_path / "state")
    original = Workspace._atomic_replace

    def write_then_fail(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        raise RuntimeError("simulated process interruption after file publication")

    monkeypatch.setattr(Workspace, "_atomic_replace", write_then_fail)
    try:
        result = router.call("file.write", {"conversation_id": cid, "path": "written.txt",
            "text": "effect happened", "create": True})
        assert not result["ok"]
        assert (project_root / "written.txt").read_text() == "effect happened"
        run = router.execution.runs(cid)[0]
        call = router.execution.calls(cid, run["id"])[0]
        assert call["state"] == "unknown"
    finally:
        router.close()
        catalog.close()


def test_unrelated_parentless_main_chat_terminal_does_not_finish_local_run(tmp_path):
    catalog = Catalog(tmp_path / "state")
    cid = "main"
    tracker = TurnRuns(Execution(catalog))
    try:
        run_id = tracker.begin(cid)
        tracker.acknowledge(cid, run_id, {"result": {"message_id": "local-user"}})
        # The main chat is shared, and this terminal has neither a parent link
        # nor a task ID previously observed for this request.
        tracker.feed(cid, {"type": "event", "event": "task.status", "payload": {
            "session_id": "main", "task_id": "phone-task", "status": "completed"}})
        assert tracker.execution.run(cid, run_id)["state"] == "acknowledged"
    finally:
        tracker.stop_all()
        catalog.close()
