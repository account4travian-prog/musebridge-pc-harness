"""Offline contracts for the MB-29 task and change adapters."""
from __future__ import annotations

import threading
import time

import pytest

from musebridge.catalog import Catalog
from musebridge.core_tools_v2 import CoreToolError, CoreToolsV2
from musebridge.tooling import ToolRouter


@pytest.fixture
def harness(tmp_path):
    state = tmp_path / "state"
    root = tmp_path / "project"
    root.mkdir()
    catalog = Catalog(state)
    project = catalog.create_project("Disposable", root)
    cid = catalog.create_conversation("Task chat", project["id"])["id"]
    other = catalog.create_conversation("Other chat", project["id"])["id"]
    router = ToolRouter(catalog, state)
    tools = CoreToolsV2(router)
    try:
        yield root, catalog, router, tools, cid, other
    finally:
        router.close()
        catalog.close()


def guard_for(router, cid):
    generation = router.execution.generation

    def guard():
        if router.execution.paused or router.execution.generation != generation:
            raise CoreToolError("paused", "Work was paused; no further action is allowed.")
        if router.execution.policy(cid)["profile"] == "read_only":
            raise CoreToolError("read_only", "The current profile does not allow this action.")
    return guard


def call(harness, name, params, *, cid=None, run_id=None):
    _root, _catalog, router, tools, default_cid, _other = harness
    cid = cid or default_cid
    return tools.call(name, cid, params, run_id, guard_for(router, cid))


def test_task_set_reports_partial_batch_and_returns_full_task_list(harness):
    result = call(harness, "task.set", {"tasks": [
        {"title": "Run tests", "criteria": "exit code zero", "check": "Write-Output ok"},
        {"title": "Invalid dependent", "depends_on": ["missing-task"]},
    ]})
    assert result["partial"] is True
    assert result["all_succeeded"] is False
    assert result["succeeded"] == 1 and result["failed"] == 1
    assert len(result["tasks"]) == 1
    assert result["tasks"][0]["title"] == "Run tests"
    assert result["tasks"][0]["has_check"] is True
    assert result["items"][1]["ok"] is False


def test_new_task_cannot_be_created_as_complete_without_verification(harness):
    router = harness[2]
    before = len(router.tasks.list(harness[4]))
    result = call(harness, "task.set", {"tasks": [
        {"title": "Premature completion", "state": "complete"},
    ]})
    assert result["all_succeeded"] is False
    assert result["items"][0]["ok"] is False
    assert len(router.tasks.list(harness[4])) == before


def test_task_set_dependency_and_check_edits_invalidate_completion(harness):
    result = call(harness, "task.set", {"tasks": [
        {"title": "Build", "check": "Write-Output verified"},
    ]})
    task_id = result["items"][0]["task_id"]
    checked = call(harness, "task.verify", {"task_id": task_id})
    assert checked["verification"]["passed"] is True
    router = harness[2]
    router.tasks.update(harness[4], task_id, "complete")
    changed = call(harness, "task.set", {"tasks": [
        {"id": task_id, "criteria": "build and inspect", "check": "Write-Output changed"},
    ]})
    assert changed["items"][0]["ok"], changed["items"]
    task = next(row for row in changed["tasks"] if row["task_id"] == task_id)
    assert task["state"] == "awaiting_review", task
    assert task["verification"] is None

    dependency = router.tasks.create(harness[4], "Prerequisite")
    blocked = call(harness, "task.set", {"tasks": [
        {"id": task_id, "depends_on": [dependency["task_id"]], "state": "complete"},
    ]})
    updated = next(row for row in blocked["tasks"] if row["task_id"] == task_id)
    assert updated["state"] == "blocked"
    assert updated["verification"] is None


def test_task_verify_rejects_manual_and_cross_conversation_jobs(harness):
    _root, _catalog, router, tools, cid, other = harness
    manual = router.tasks.create(cid, "Owner judgment", verification_mode="manual")
    with pytest.raises(CoreToolError) as captured:
        tools.call("task.verify", cid, {"task_id": manual["task_id"], "command": "Write-Output ok"}, None,
                   guard_for(router, cid))
    assert captured.value.code == "manual_review_required"

    failing = router.tasks.create(cid, "Failing check", check_command="exit 3")
    failed_result = tools.call("task.verify", cid, {"task_id": failing["task_id"]}, None,
                               guard_for(router, cid))
    assert failed_result["verification"]["passed"] is False
    assert failed_result["verification"]["evidence"]["exit_code"] == 3

    task_a = router.tasks.create(cid, "A", check_command="Write-Output a")
    started = tools.call("task.verify", cid, {"task_id": task_a["task_id"]}, None,
                         guard_for(router, cid))
    job_id = started["job_id"]
    task_b = router.tasks.create(other, "B", check_command="Write-Output b")
    with pytest.raises(CoreToolError) as wrong_owner:
        tools.call("task.verify", other, {"task_id": task_b["task_id"], "job_id": job_id}, None,
                   guard_for(router, other))
    assert wrong_owner.value.code == "job_not_owned"


def test_task_verify_binds_inputs_before_check_and_reports_changed_input(harness):
    root, _catalog, _router, _tools, _cid, _other = harness
    target = root / "input.txt"
    target.write_text("before", encoding="utf-8")
    task = call(harness, "task.set", {"tasks": [
        {"title": "Check input", "check": "Set-Content -LiteralPath input.txt -Value changed; exit 0"},
    ]})["items"][0]["task_id"]
    result = call(harness, "task.verify", {"task_id": task, "paths": ["input.txt"]})
    assert result["ready"] is True
    assert result["verification"]["passed"] is False
    assert result["verification"]["evidence"]["inputs_unchanged"] is False
    assert target.read_text(encoding="utf-8").strip() == "changed"


def test_verified_job_retry_refreshes_current_input_truth_in_adapter(harness):
    root = harness[0]
    target = root / "input.txt"
    target.write_text("before", encoding="utf-8")
    task_id = call(harness, "task.set", {"tasks": [
        {"title": "Recheck current inputs", "check": "Write-Output ok"},
    ]})["items"][0]["task_id"]

    verified = call(harness, "task.verify", {"task_id": task_id, "paths": ["input.txt"]})
    assert verified["verification"]["passed"] is True
    target.write_text("changed after verification", encoding="utf-8")

    refreshed = call(harness, "task.verify", {"task_id": task_id, "job_id": verified["job_id"]})
    assert refreshed["already_verified"] is True
    assert refreshed["verification"]["passed"] is False
    assert refreshed["verification"]["evidence"]["inputs_unchanged"] is False
    repeated = call(harness, "task.verify", {"task_id": task_id, "job_id": verified["job_id"]})
    assert repeated["verification"]["verification_id"] == refreshed["verification"]["verification_id"]


def test_router_task_verify_retry_refreshes_stale_inputs(harness):
    root, _catalog, router, _tools, cid, _other = harness
    target = root / "input.txt"
    target.write_text("before", encoding="utf-8")
    task = router.tasks.create(cid, "Router current truth", check_command="Write-Output ok")
    first = router.call("task.verify", {
        "conversation_id": cid, "task_id": task["task_id"], "paths": ["input.txt"],
    })
    assert first["ok"] and first["payload"]["verification"]["passed"] is True
    target.write_text("changed after router receipt", encoding="utf-8")

    again = router.call("task.verify", {
        "conversation_id": cid, "task_id": task["task_id"],
        "job_id": first["payload"]["job_id"],
    })
    refreshed = again["payload"]["verification"]
    assert refreshed["passed"] is False
    assert refreshed["evidence"]["inputs_unchanged"] is False


def test_task_verify_reuses_pending_job_by_job_id_without_duplicate_process(harness):
    router = harness[2]
    tools = harness[3]
    cid = harness[4]
    tools.inline_wait_s = 0
    task = router.tasks.create(cid, "Slow check")
    command = "Start-Sleep -Seconds 2; Write-Output done"
    first = tools.call("task.verify", cid,
                       {"task_id": task["task_id"], "command": command}, None, guard_for(router, cid))
    assert first["running"] is True
    job_id = first["job_id"]
    again = tools.call("task.verify", cid,
                       {"task_id": task["task_id"], "job_id": job_id}, None,
                       guard_for(router, cid))
    assert again["job_id"] == job_id
    preparations = router.catalog.rows(
        "SELECT preparation_id,state,job_id FROM task_job_preparations_v1 WHERE task_id=?", (task["task_id"],))
    assert len(preparations) == 1
    assert preparations[0]["job_id"] == job_id
    assert router.catalog.rows("SELECT job_id FROM job_links WHERE conversation_id=? AND job_id=?", (cid, job_id))

    tools.inline_wait_s = 4
    finished = tools.call("task.verify", cid,
                          {"task_id": task["task_id"], "job_id": job_id}, None,
                          guard_for(router, cid))
    assert finished["ready"] is True
    assert finished["verification"]["passed"] is True
    repeated = tools.call("task.verify", cid,
                          {"task_id": task["task_id"], "job_id": job_id}, None,
                          guard_for(router, cid))
    assert repeated["already_verified"] is True
    assert repeated["verification"]["verification_id"] == finished["verification"]["verification_id"]


def test_task_verify_stop_work_while_waiting_cancels_and_issues_no_receipt(harness):
    _root, _catalog, router, tools, cid, _other = harness
    tools.inline_wait_s = 5
    task = router.tasks.create(cid, "Cancelable", check_command="Start-Sleep -Seconds 10")
    outcome = []

    def run():
        try:
            outcome.append(tools.call("task.verify", cid, {"task_id": task["task_id"]}, None,
                                      guard_for(router, cid)))
        except Exception as exc:
            outcome.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        rows = router.catalog.rows("SELECT job_id FROM job_links WHERE conversation_id=?", (cid,))
        if rows:
            break
        time.sleep(0.02)
    assert rows
    router.stop_all()
    thread.join(8)
    assert not thread.is_alive()
    assert outcome and isinstance(outcome[0], Exception)
    assert router.catalog.rows("SELECT verification_id FROM task_verifications_v1 WHERE task_id=?", (task["task_id"],)) == []


def test_change_review_and_run_restore_reverse_chronological_per_file(harness):
    root, _catalog, router, _tools, cid, _other = harness
    target = root / "notes.txt"
    target.write_text("before", encoding="utf-8")
    run_id = router.execution.begin_run(cid)
    first = router.checkpoints.capture_before(cid, run_id, root, "notes.txt")
    target.write_text("middle", encoding="utf-8")
    router.checkpoints.finish(first)
    second = router.checkpoints.capture_before(cid, run_id, root, "notes.txt")
    target.write_text("after", encoding="utf-8")
    router.checkpoints.finish(second)

    reviewed = call(harness, "change.review", {"run": run_id}, run_id=run_id)
    assert reviewed["count"] == 2
    assert all("diff" in item for item in reviewed["edits"])
    restored = call(harness, "change.restore", {"run": run_id}, run_id=run_id)
    assert restored["all_succeeded"] is True
    assert target.read_text(encoding="utf-8") == "before"


def test_change_review_and_path_restore_ignore_the_adapter_local_run(harness):
    root, _catalog, router, _tools, cid, _other = harness
    target = root / "notes.txt"
    target.write_text("before", encoding="utf-8")
    historical_run = router.execution.begin_run(cid)
    first = router.checkpoints.capture_before(cid, historical_run, root, "notes.txt")
    target.write_text("middle", encoding="utf-8")
    router.checkpoints.finish(first)
    second = router.checkpoints.capture_before(cid, historical_run, root, "notes.txt")
    target.write_text("after", encoding="utf-8")
    router.checkpoints.finish(second)
    router.checkpoints.restore(cid, second)
    assert target.read_text(encoding="utf-8") == "middle"

    # A normal tool call may itself have a fresh local run; path history must
    # still select the latest unrestored checkpoint from the selected project.
    local_run = router.execution.begin_run(cid, origin="tool")
    reviewed = call(harness, "change.review", {}, run_id=local_run)
    assert reviewed["count"] == 2
    restored = call(harness, "change.restore", {"path": "notes.txt"}, run_id=local_run)
    assert restored["all_succeeded"] is True
    assert target.read_text(encoding="utf-8") == "before"


def test_change_tools_filter_history_to_the_current_project_root(harness, tmp_path):
    root, catalog, router, _tools, cid, _other = harness
    target = root / "notes.txt"
    target.write_text("before", encoding="utf-8")
    run_id = router.execution.begin_run(cid)
    edit = router.checkpoints.capture_before(cid, run_id, root, "notes.txt")
    target.write_text("tool result", encoding="utf-8")
    router.checkpoints.finish(edit)

    new_root = tmp_path / "replacement-project"
    new_root.mkdir()
    replacement = catalog.create_project("Replacement", new_root)
    with catalog.lock, catalog.db:
        catalog.db.execute("UPDATE conversations SET project_id=? WHERE id=?", (replacement["id"], cid))

    reviewed = call(harness, "change.review", {"path": "notes.txt"})
    assert reviewed["count"] == 0
    restored = call(harness, "change.restore", {"path": "notes.txt"})
    assert restored["restored"] == []
    assert target.read_text(encoding="utf-8") == "tool result"


def test_change_restore_preserves_owner_edit_on_conflict(harness):
    root, _catalog, router, _tools, cid, _other = harness
    target = root / "notes.txt"
    target.write_text("before", encoding="utf-8")
    run_id = router.execution.begin_run(cid)
    edit = router.checkpoints.capture_before(cid, run_id, root, "notes.txt")
    target.write_text("tool result", encoding="utf-8")
    router.checkpoints.finish(edit)
    target.write_text("owner result", encoding="utf-8")
    result = call(harness, "change.restore", {"path": "notes.txt"}, run_id=run_id)
    assert result["all_succeeded"] is False
    assert result["failed"]
    assert target.read_text(encoding="utf-8") == "owner result"
