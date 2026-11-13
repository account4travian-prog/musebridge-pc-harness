"""Integration coverage for tool-bound lifecycle hooks and Stop Work."""
import json
import threading
import time

from musebridge.catalog import Catalog
from musebridge.tooling import ToolRouter


def setup_router(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    catalog = Catalog(tmp_path / "state")
    project = catalog.create_project("Project", root)
    cid = catalog.create_conversation("Chat", project["id"])["id"]
    router = ToolRouter(catalog, tmp_path / "state")
    return root, catalog, project, cid, router


def ps_quote(path):
    return str(path).replace("'", "''")


def call(router, cid, tool, **params):
    return router.call(tool, {"conversation_id": cid, **params})


def test_before_tool_passes_before_write_and_failure_blocks_it(tmp_path):
    root, catalog, project, cid, router = setup_router(tmp_path)
    target = root / "target.txt"
    marker = root / "precheck.txt"
    marker_path = ps_quote(marker)
    target_path = ps_quote(target)
    script = f"if (Test-Path -LiteralPath '{target_path}') {{ exit 9 }}; [IO.File]::WriteAllText('{marker_path}','precheck-passed')"
    try:
        router.hooks.set_hook("project:" + project["id"], "gate", "before_tool", script,
                              enabled=True, tool_filter=["file.write"])
        result = call(router, cid, "file.write", path="target.txt", text="written", create=True)
        assert result["ok"], result
        assert marker.read_text() == "precheck-passed"
        assert target.read_text() == "written"

        target.unlink()
        router.hooks.set_hook("project:" + project["id"], "gate", "before_tool", "exit 9",
                              enabled=True, tool_filter=["file.write"])
        blocked = call(router, cid, "file.write", path="target.txt", text="blocked", create=True)
        assert blocked["ok"] is False
        assert "hook did not pass" in blocked["error"]["message"]
        assert not target.exists()
    finally:
        router.close(); catalog.close()


def test_after_tool_receives_success_and_failure_and_filters_by_tool(tmp_path):
    root, catalog, project, cid, router = setup_router(tmp_path)
    try:
        router.hooks.set_hook("project:" + project["id"], "after-read", "after_tool",
                              "Write-Output 'after'", enabled=True, tool_filter=["file.read"])
        good_path = root / "good.txt"; good_path.write_text("ok", encoding="utf-8")
        succeeded = call(router, cid, "file.read", path="good.txt")
        failed = call(router, cid, "file.read", path="missing.txt")
        assert succeeded["ok"] is True
        assert failed["ok"] is False
        # A different tool is outside the exact allowlist.
        call(router, cid, "workspace.list")
        rows = catalog.rows("SELECT data FROM events WHERE kind='hook.admitted' AND conversation_id=?", (cid,))
        contexts = [json.loads(row["data"])["context"] for row in rows]
        assert {item["state"] for item in contexts} == {"succeeded", "failed"}
        assert all(item["tool"] == "file.read" for item in contexts)
        # Disabled configuration remains inert.
        disabled_hook = router.hooks.set_hook("project:" + project["id"], "disabled", "after_tool",
                                              "throw 'must not run'", enabled=False)
        before = len(rows)
        call(router, cid, "file.read", path="good.txt")
        rows_after = catalog.rows("SELECT data FROM events WHERE kind='hook.admitted' AND conversation_id=?", (cid,))
        assert len(rows_after) == before + 1  # the enabled hook still runs
        assert all(json.loads(row["data"])["hook_id"] != disabled_hook["hook_id"] for row in rows_after)
    finally:
        router.close(); catalog.close()


def test_precheck_deduplicates_identical_tool_calls_in_one_remote_run(tmp_path):
    root, catalog, project, cid, router = setup_router(tmp_path)
    marker = root / "count.txt"
    script = f"[IO.File]::AppendAllText('{ps_quote(marker)}','x')"
    source = root / "read.txt"; source.write_text("text", encoding="utf-8")
    try:
        router.hooks.set_hook("project:" + project["id"], "once", "before_tool", script,
                              enabled=True, tool_filter=["file.read"])
        run_id = router.execution.begin_run(cid, "remote")
        first = router.call("file.read", {"conversation_id": cid, "path": "read.txt", "run_id": run_id}, remote=True)
        second = router.call("file.read", {"conversation_id": cid, "path": "read.txt", "run_id": run_id}, remote=True)
        assert first["ok"] and second["ok"]
        assert marker.read_text() == "x"
        admitted = catalog.rows("SELECT data FROM events WHERE kind='hook.admitted' AND conversation_id=?", (cid,))
        assert len(admitted) == 1
        assert json.loads(admitted[0]["data"])["event"] == "before_tool"
    finally:
        router.close(); catalog.close()


def test_stop_during_precheck_is_not_blocked_and_prevents_target_write(tmp_path):
    root, catalog, project, cid, router = setup_router(tmp_path)
    target = root / "must-not-exist.txt"
    result_box = []
    try:
        router.hooks.set_hook("project:" + project["id"], "slow", "before_tool",
                              "Start-Sleep -Seconds 30", enabled=True, tool_filter=["file.write"])
        thread = threading.Thread(target=lambda: result_box.append(
            call(router, cid, "file.write", path=target.name, text="no", create=True)))
        thread.start()
        deadline = time.monotonic() + 5
        while not catalog.rows("SELECT invocation_id FROM hook_invocations_v1 WHERE event='before_tool'") and time.monotonic() < deadline:
            time.sleep(.02)
        assert catalog.rows("SELECT invocation_id FROM hook_invocations_v1 WHERE event='before_tool'")
        start = time.monotonic()
        router.stop_all()
        assert time.monotonic() - start < 3
        thread.join(5)
        assert not thread.is_alive()
        assert result_box and result_box[0]["ok"] is False
        assert not target.exists()
    finally:
        router.close(); catalog.close()


def test_read_only_profile_does_not_spawn_project_write_hooks(tmp_path):
    root, catalog, project, cid, router = setup_router(tmp_path)
    try:
        source = root / "read.txt"; source.write_text("ok", encoding="utf-8")
        router.hooks.set_hook("project:" + project["id"], "read-hook", "before_tool",
                              "throw 'read-only must skip this'", enabled=True,
                              tool_filter=["file.read"])
        router.set_profile(cid, "read_only")
        result = call(router, cid, "file.read", path="read.txt")
        assert result["ok"] is True
        assert catalog.rows("SELECT * FROM hook_invocations_v1 WHERE event='before_tool'") == []
        assert router.jobs._db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        router.close(); catalog.close()
