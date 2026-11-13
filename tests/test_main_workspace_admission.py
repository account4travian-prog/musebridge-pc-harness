"""Regressions for definitive no-project errors and workspace-switch fencing."""
from __future__ import annotations

from musebridge.catalog import Catalog
from musebridge.core import Core
from musebridge.tooling import ToolRouter


def _router(tmp_path):
    catalog = Catalog(tmp_path / "state")
    router = ToolRouter(catalog, tmp_path / "state")
    return catalog, router


def _core(router, catalog, cid):
    return Core(None, lambda _text: None, lambda *_args: None, lambda _text: None,
                catalog=catalog, router=router, conversation_id=cid)


def _job_count(router):
    with router.jobs._lock:
        return router.jobs._db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


def test_shell_without_selected_project_returns_visible_no_project_not_unknown(tmp_path):
    catalog, router = _router(tmp_path)
    try:
        cid = catalog.create_conversation("Unbound chat")["id"]
        run_id = router.execution.begin_run(cid)
        core = _core(router, catalog, cid)

        result = core.tool("shell.run", {
            "conversation_id": cid,
            "run_id": run_id,
            "command": "Set-Content -LiteralPath sentinel.txt -Value should-not-run",
        })

        assert result["ok"] is False
        assert isinstance(result["error"], str)
        assert "[no_project]" in result["error"]
        assert "Choose a workspace" in result["error"]
        assert _job_count(router) == 0
        calls = router.execution.calls(cid, run_id)
        assert len(calls) == 1
        assert calls[0]["state"] == "failed"
        assert calls[0]["effect"] == "rejected"
        assert not any(row["state"] == "unknown" for row in calls)
        assert not catalog.rows("SELECT seq FROM events WHERE conversation_id=? AND kind='tool.started'", (cid,))
        assert not list(tmp_path.rglob("sentinel.txt"))
    finally:
        router.close()
        catalog.close()


def test_project_switch_revokes_current_guard_and_old_run(tmp_path):
    catalog, router = _router(tmp_path)
    root_a = tmp_path / "workspace-a"
    root_b = tmp_path / "workspace-b"
    root_a.mkdir()
    root_b.mkdir()
    try:
        project_a = catalog.create_project("Workspace A", root_a)
        project_b = catalog.create_project("Workspace B", root_b)
        cid = catalog.create_conversation("Switching chat", project_a["id"])["id"]
        run_id = router.execution.begin_run(cid)
        core = _core(router, catalog, cid)
        original_hook = router.hooks.before_tool

        def switch_project(*_args):
            with catalog.lock, catalog.db:
                catalog.db.execute("UPDATE conversations SET project_id=? WHERE id=?",
                                   (project_b["id"], cid))

        router.hooks.before_tool = switch_project
        first = core.tool("shell.run", {
            "conversation_id": cid,
            "run_id": run_id,
            "command": "Set-Content -LiteralPath sentinel.txt -Value should-not-run",
        })
        router.hooks.before_tool = original_hook

        assert first["ok"] is False
        assert isinstance(first["error"], str)
        assert "Workspace changed" in first["error"]
        assert _job_count(router) == 0
        assert not (root_a / "sentinel.txt").exists()
        assert not (root_b / "sentinel.txt").exists()
        first_calls = router.execution.calls(cid, run_id)
        assert len(first_calls) == 1 and first_calls[0]["state"] == "failed"
        assert not any(row["state"] == "unknown" for row in first_calls)

        # A delayed callback carrying the old run is still refused after the
        # conversation has selected the new project.
        second = core.tool("shell.run", {
            "conversation_id": cid,
            "run_id": run_id,
            "command": "Set-Content -LiteralPath sentinel.txt -Value should-not-run",
        })
        assert second["ok"] is False
        assert isinstance(second["error"], str)
        assert "previous workspace" in second["error"]
        assert _job_count(router) == 0
        assert not (root_a / "sentinel.txt").exists()
        assert not (root_b / "sentinel.txt").exists()
        assert not any(row["state"] == "unknown" for row in router.execution.calls(cid, run_id))
    finally:
        router.close()
        catalog.close()
