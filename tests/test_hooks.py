import time

from musebridge.catalog import Catalog
from musebridge.execution import Execution
from musebridge.hooks import HookService
from musebridge.jobs import JobManager


def _setup(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    state = tmp_path / "state"
    catalog = Catalog(state)
    project = catalog.create_project("Project", root)
    first = catalog.create_conversation("First", project["id"])
    second = catalog.create_conversation("Second", project["id"])
    other_project = catalog.create_project("Other", root)
    other = catalog.create_conversation("Other chat", other_project["id"])
    execution = Execution(catalog)
    jobs = JobManager(state / "jobs")
    hooks = HookService(catalog, execution, jobs)
    return root, state, catalog, project, first["id"], second["id"], other["id"], execution, jobs, hooks


def _script(*parts):
    return "; ".join(parts)


def _wait(jobs, job_id, timeout=15):
    deadline = time.monotonic() + timeout
    result = jobs.wait(job_id, timeout_s=min(20, timeout))
    while result["state"] in ("accepted", "running") and time.monotonic() < deadline:
        result = jobs.wait(job_id, cursor=result["cursor"], timeout_s=2)
    return result


def test_hook_is_disabled_by_default_then_runs_once_across_project_chats(tmp_path):
    root, _, catalog, project, first, second, other, execution, jobs, hooks = _setup(tmp_path)
    marker = root / "effect.txt"
    path = str(marker).replace("'", "''")
    script = _script(f"[IO.File]::AppendAllText('{path}','x')", "Write-Output 'hook-ran'")

    disabled = hooks.set_hook("project:" + project["id"], "audit", "after_file_edit", script)
    assert disabled["enabled"] is False
    assert hooks.run_event(first, "after_file_edit", "edit-1")["jobs"] == []
    assert not marker.exists()

    hooks.set_hook("project:" + project["id"], "audit", "after_file_edit", script, enabled=True)
    first_result = hooks.run_event(first, "after_file_edit", "edit-1")
    assert len(first_result["jobs"]) == 1
    job_id = first_result["jobs"][0]["job_id"]
    final = _wait(jobs, job_id)
    assert final["state"] == "succeeded"
    assert marker.read_text() == "x"

    repeated_from_sibling_chat = hooks.run_event(second, "after_file_edit", "edit-1")
    assert repeated_from_sibling_chat["jobs"][0]["duplicate"] is True
    assert repeated_from_sibling_chat["jobs"][0]["job_id"] == job_id
    assert hooks.run_event(other, "after_file_edit", "edit-1")["jobs"] == []
    assert marker.read_text() == "x"
    assert catalog.rows("SELECT conversation_id FROM job_links WHERE job_id=?", (job_id,))[0]["conversation_id"] == first
    jobs.close()
    catalog.close()


def test_read_only_and_paused_policy_skip_without_admitting_jobs(tmp_path):
    _, _, catalog, project, first, _, _, execution, jobs, hooks = _setup(tmp_path)
    scope = "project:" + project["id"]
    hooks.set_hook(scope, "verify", "before_verification", "Write-Output 'x'", enabled=True)

    execution.set_profile(first, "read_only")
    read_only = hooks.run_event(first, "before_verification", "verify-1")
    assert not read_only["jobs"] and "read_only" in read_only["skipped"][0]["reason"]
    execution.set_profile(first, "project")
    execution.pause(True)
    paused = hooks.run_event(first, "before_verification", "verify-2")
    assert not paused["jobs"] and "paused" in paused["skipped"][0]["reason"]
    assert catalog.rows("SELECT * FROM hook_invocations_v1") == []
    jobs.close()
    catalog.close()


def test_hook_timeout_is_reported_and_not_replayed(tmp_path):
    _, _, catalog, project, first, _, _, _, jobs, hooks = _setup(tmp_path)
    scope = "project:" + project["id"]
    hooks.set_hook(scope, "slow", "run_ended", "Start-Sleep -Seconds 10", enabled=True, timeout_s=0.3)
    admitted = hooks.run_event(first, "run_ended", "run-1")["jobs"][0]
    result = _wait(jobs, admitted["job_id"])
    assert result["state"] == "failed"
    assert result["error"] == "timed out"
    duplicate = hooks.run_event(first, "run_ended", "run-1")["jobs"][0]
    assert duplicate["duplicate"] is True
    assert duplicate["job_id"] == admitted["job_id"]
    jobs.close()
    catalog.close()


def test_hook_cancel_is_terminal_and_never_replayed(tmp_path):
    _, _, catalog, project, first, _, _, execution, jobs, hooks = _setup(tmp_path)
    scope = "project:" + project["id"]
    hooks.set_hook(scope, "wait", "after_file_edit", "Start-Sleep -Seconds 30", enabled=True)
    admitted = hooks.run_event(first, "after_file_edit", "edit-cancel")["jobs"][0]
    execution.pause(True)
    jobs.stop_all()
    cancelled = jobs.status(admitted["job_id"])
    assert cancelled["state"] == "cancelled"
    row = hooks.refresh(first, admitted["invocation_id"])[0]
    assert row["state"] == "cancelled"
    execution.pause(False)
    jobs.set_paused(False)
    duplicate = hooks.run_event(first, "after_file_edit", "edit-cancel")["jobs"][0]
    assert duplicate["duplicate"] is True and duplicate["job_id"] == admitted["job_id"]
    jobs.close()
    catalog.close()


def test_incomplete_invocation_becomes_unknown_after_restart_without_replay(tmp_path):
    root, state, catalog, project, first, _, _, execution, jobs, hooks = _setup(tmp_path)
    marker = root / "once.txt"
    path = str(marker).replace("'", "''")
    script = _script(f"[IO.File]::AppendAllText('{path}','x')", "Start-Sleep -Seconds 10")
    hooks.set_hook("project:" + project["id"], "restart", "after_file_edit", script,
                   enabled=True, timeout_s=20)
    admitted = hooks.run_event(first, "after_file_edit", "edit-restart")["jobs"][0]
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert marker.read_text() == "x"

    # Simulate application exit while its lifecycle journal still says running.
    jobs.close()
    catalog.close()
    catalog = Catalog(state)
    execution = Execution(catalog)
    jobs = JobManager(state / "jobs")
    hooks = HookService(catalog, execution, jobs)
    row = hooks.refresh(first, admitted["invocation_id"])[0]
    assert row["state"] == "outcome_unknown"
    duplicate = hooks.run_event(first, "after_file_edit", "edit-restart")["jobs"][0]
    assert duplicate["duplicate"] is True
    assert duplicate["state"] == "outcome_unknown"
    assert marker.read_text() == "x"
    jobs.close()
    catalog.close()


def test_hooks_require_project_scope_and_validate_opt_in_configuration(tmp_path):
    _, _, catalog, project, first, _, _, _, jobs, hooks = _setup(tmp_path)
    try:
        hooks.set_hook("chat:" + first, "x", "after_file_edit", "Write-Output x")
        assert False, "chat scoped hooks must be rejected"
    except ValueError:
        pass
    try:
        hooks.set_hook("project:" + project["id"], "x", "after_file_edit", "Write-Output x", enabled=1)
        assert False, "truthy non-bool enabled values must be rejected"
    except ValueError:
        pass
    try:
        hooks.set_hook("project:" + project["id"], "x", "unknown", "Write-Output x")
        assert False, "unknown hook event must be rejected"
    except ValueError:
        pass
    jobs.close()
    catalog.close()


def test_changed_project_root_alias_is_rejected_before_job_admission(tmp_path, monkeypatch):
    from pathlib import Path

    _, _, catalog, project, first, _, _, _, jobs, hooks = _setup(tmp_path)
    hooks.set_hook("project:" + project["id"], "verify", "before_verification",
                   "Write-Output 'must-not-run'", enabled=True)
    root = Path(project["root"])
    resolve = Path.resolve

    def aliased(path, *args, **kwargs):
        if path == root:
            return root.parent / "replacement"
        return resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", aliased)
    result = hooks.run_event(first, "before_verification", "verify-root-replaced")
    assert not result["jobs"]
    assert result["skipped"][0]["state"] == "outcome_unknown"
    assert "identity changed" in result["skipped"][0]["reason"]
    assert jobs._db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    jobs.close()
    catalog.close()


def test_after_file_edit_path_filter_only_admits_matching_project_relative_path(tmp_path):
    _, _, catalog, project, first, _, _, _, jobs, hooks = _setup(tmp_path)
    scope = "project:" + project["id"]
    hook = hooks.set_hook(scope, "python-check", "after_file_edit", "Write-Output 'checked'",
                          enabled=True, timeout_s=10, path_filter="src/**/*.PY")
    try:
        assert hook["path_filter"] == "src/**/*.PY"
        miss = hooks.run_event(first, "after_file_edit", "edit-no-match", path="docs/readme.md")
        assert miss["jobs"] == [] and miss["skipped"] == []
        assert jobs._db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0

        hit = hooks.run_event(first, "after_file_edit", "edit-match", path="src/package/main.py")
        assert hit["jobs"][0]["hook_id"] == hook["hook_id"]
        duplicate = hooks.run_event(first, "after_file_edit", "edit-match", path="src/package/main.py")
        assert duplicate["jobs"][0]["duplicate"] is True
        assert duplicate["jobs"][0]["job_id"] == hit["jobs"][0]["job_id"]
        assert _wait(jobs, hit["jobs"][0]["job_id"])["exit_code"] == 0
    finally:
        jobs.close()
        catalog.close()


def test_path_filter_rejects_parent_and_non_edit_event_globs(tmp_path):
    _, _, catalog, project, _, _, _, _, jobs, hooks = _setup(tmp_path)
    scope = "project:" + project["id"]
    try:
        for pattern in ("../secret.py", "/absolute.py", "C:/absolute.py"):
            try:
                hooks.set_hook(scope, "bad", "after_file_edit", "Write-Output x", path_filter=pattern)
                assert False, "unsafe path glob should be rejected"
            except ValueError:
                pass
        try:
            hooks.set_hook(scope, "bad-event", "run_ended", "Write-Output x", path_filter="*.py")
            assert False, "non-edit hook cannot have a path filter"
        except ValueError:
            pass
    finally:
        jobs.close()
        catalog.close()


def test_path_filter_matcher_is_bounded_and_timeout_does_not_block_other_hooks(tmp_path):
    from musebridge.hooks import PathFilterTimeout, path_matches

    adversarial = "a*" * 36 + "b"
    started = time.perf_counter()
    try:
        matched = path_matches("a" * 200 + "c", adversarial)
    except PathFilterTimeout as exc:
        assert "simplify the glob" in str(exc)
    else:
        assert matched is False
    assert time.perf_counter() - started < 0.5

    hook_root = tmp_path / "hooks"
    hook_root.mkdir()
    _, _, catalog, project, first, _, _, _, jobs, hooks = _setup(hook_root)
    scope = "project:" + project["id"]
    hooks.set_hook(scope, "slow-pattern", "after_file_edit", "Write-Output 'wrong'",
                   enabled=True, path_filter=adversarial)
    hooks.set_hook(scope, "ordinary", "after_file_edit", "Write-Output 'healthy'", enabled=True)
    try:
        result = hooks.run_event(first, "after_file_edit", "glob-timeout", path="a" * 200 + "c")
        assert any(item.get("state") == "path_filter_timeout" for item in result["skipped"])
        assert len(result["jobs"]) == 1
        assert _wait(jobs, result["jobs"][0]["job_id"])["exit_code"] == 0
    finally:
        jobs.close()
        catalog.close()
