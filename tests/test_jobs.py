"""Real Windows process tests; these intentionally execute only local probes."""
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

pytest.importorskip("win32job", reason="Windows Job Objects are required")

from musebridge.jobs import JobManager


def terminal(manager, job_id, timeout=15):
    deadline = time.monotonic() + timeout
    cursor = 0
    while time.monotonic() < deadline:
        value = manager.wait(job_id, cursor=cursor, timeout_s=min(1, deadline-time.monotonic()))
        cursor = value["cursor"]
        if value["state"] in {"succeeded", "failed", "cancelled", "interrupted", "outcome_unknown"}:
            return manager.status(job_id, cursor=0)
    raise AssertionError("job did not finish: " + repr(manager.status(job_id)))


def test_finished_summary_handles_a_real_completed_job(tmp_path):
    manager = JobManager(tmp_path / "data")
    try:
        job = manager.start("summary-regression", "Write-Output 'finished'", tmp_path, 15)
        completed = terminal(manager, job["job_id"])
        summary = manager.finished_summary()
        assert len(summary) == 1
        assert set(summary[0]) == {"job_id", "state", "finished"}
        assert summary[0]["job_id"] == job["job_id"]
        assert summary[0]["state"] == completed["state"] == "succeeded"
        assert isinstance(summary[0]["finished"], float)
    finally:
        manager.close()


def test_runs_hidden_powershell_and_deduplicates_request_id(tmp_path):
    marker = tmp_path / "ran.txt"
    manager = JobManager(tmp_path / "data")
    try:
        command = "Set-Content -LiteralPath '" + str(marker) + "' -Value 'once'; Write-Output 'completed'"
        first = manager.start("request-1", command, tmp_path, 15)
        second = manager.start("request-1", command, tmp_path, 15)
        assert first["job_id"] == second["job_id"]
        result = terminal(manager, first["job_id"])
        assert result["state"] == "succeeded"
        assert "completed" in result["output"]
        assert marker.read_text().strip() == "once"
        with pytest.raises(ValueError, match="different arguments"):
            manager.start("request-1", "Set-Content other.txt x", tmp_path, 15)
    finally:
        manager.close()


def test_concurrent_duplicate_admission_creates_one_job(tmp_path):
    marker = tmp_path / "concurrent.txt"
    manager = JobManager(tmp_path / "data")
    try:
        command = "Set-Content -LiteralPath '" + str(marker) + "' -Value 'once'; Start-Sleep -Milliseconds 250"
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(lambda _: manager.start("same-id", command, tmp_path, 15), range(8)))
        assert len({row["job_id"] for row in rows}) == 1
        assert terminal(manager, rows[0]["job_id"])["state"] == "succeeded"
        assert marker.read_text().strip() == "once"
    finally:
        manager.close()


def test_reports_nonzero_exit_and_timeout(tmp_path):
    manager = JobManager(tmp_path / "data")
    try:
        failed = manager.start("fail", "Write-Output 'expected'; exit 7", tmp_path, 15)
        result = terminal(manager, failed["job_id"])
        assert result["state"] == "failed"
        assert result["exit_code"] == 7
        timeout = manager.start("timeout", "Start-Sleep -Seconds 20", tmp_path, 0.4)
        result = terminal(manager, timeout["job_id"])
        assert result["state"] == "failed"
        assert result["error"] == "timed out"
    finally:
        manager.close()


def test_cancel_kills_process_tree(tmp_path):
    import win32api
    import win32con
    import win32event

    manager = JobManager(tmp_path / "data")
    try:
        script = "$p=Start-Process powershell.exe -ArgumentList '-NoProfile','-NonInteractive','-Command','Start-Sleep -Seconds 30' -PassThru; Write-Output $p.Id; Start-Sleep -Seconds 30"
        started = manager.start("tree", script, tmp_path, 60)
        deadline = time.monotonic() + 10
        child_pid = None
        while time.monotonic() < deadline and child_pid is None:
            value = manager.wait(started["job_id"], timeout_s=1)
            for line in value["output"].splitlines():
                if line.strip().isdigit():
                    child_pid = int(line.strip())
                    break
        assert child_pid is not None, manager.status(started["job_id"])
        result = manager.cancel(started["job_id"])
        assert result["state"] == "cancelled"
        try:
            child = win32api.OpenProcess(win32con.SYNCHRONIZE, False, child_pid)
        except Exception:
            child = None  # It exited before the handle check.
        if child:
            try:
                assert win32event.WaitForSingleObject(child, 5000) == win32event.WAIT_OBJECT_0
            finally:
                win32api.CloseHandle(child)
    finally:
        manager.close()


def test_output_is_bounded_and_cursor_reports_truncation(tmp_path):
    manager = JobManager(tmp_path / "data")
    try:
        started = manager.start("large", "Write-Output ('x' * 400000)", tmp_path, 20)
        result = terminal(manager, started["job_id"])
        assert result["state"] == "succeeded"
        assert len(result["output"]) == 16 * 1024
        assert result["output_end"] >= 400000
        pages = [result["output"]]
        cursor = result["cursor"]
        while cursor < result["output_end"]:
            page = manager.status(started["job_id"], cursor=cursor)
            assert len(page["output"]) <= 16 * 1024
            assert page["cursor"] > cursor
            pages.append(page["output"])
            cursor = page["cursor"]
        assert sum(map(len, pages)) == 256 * 1024
        old = manager.status(started["job_id"], cursor=0)
        assert old["truncated"] is True
        last = manager.status(started["job_id"], cursor=result["output_end"] - 10)
        assert last["output"] == "x" * 8 + "\r\n"
    finally:
        manager.close()


def test_incomplete_admission_after_restart_is_unknown_and_not_replayed(tmp_path):
    directory = tmp_path / "data"
    manager = JobManager(directory)
    import hashlib
    import json
    import uuid

    command = "Set-Content should-not-run.txt x"
    cwd = str(tmp_path.resolve())
    timeout_s = 10.0
    encoded = json.dumps({"command": [command], "cwd": cwd, "timeout_s": timeout_s},
                         sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    job_id = str(uuid.uuid4())
    with manager._lock:
        manager._db.execute("INSERT INTO jobs(job_id,request_id,args_hash,command_json,cwd,timeout_s,state,created) VALUES(?,?,?,?,?,?,?,?)",
            (job_id, "ambiguous", hashlib.sha256(encoded.encode()).hexdigest(), json.dumps([command]),
             cwd, timeout_s, "running", time.time()))
    # Simulate an abrupt process death without invoking the normal close path.
    with manager._lock:
        manager._closed = True
        manager._db.close()
    restarted = JobManager(directory)
    try:
        replay = restarted.start("ambiguous", command, tmp_path, 10)
        assert replay["job_id"] == job_id
        assert replay["state"] == "outcome_unknown"
        assert not (tmp_path / "should-not-run.txt").exists()
    finally:
        restarted.close()


def test_pause_and_strict_parameters(tmp_path):
    manager = JobManager(tmp_path / "data")
    try:
        manager.set_paused(True)
        with pytest.raises(RuntimeError, match="paused"):
            manager.start("blocked", "Write-Output x", tmp_path)
        manager.set_paused(False)
        with pytest.raises(ValueError):
            manager.start("bad-timeout", "Write-Output x", tmp_path, True)
        with pytest.raises(ValueError):
            manager.set_paused(1)
    finally:
        manager.close()


def test_limits_active_jobs_and_still_deduplicates_at_capacity(tmp_path):
    manager = JobManager(tmp_path / "data")
    try:
        jobs = [manager.start("active-" + str(i), "Start-Sleep -Seconds 30", tmp_path, 60)
                for i in range(4)]
        assert len({row["job_id"] for row in jobs}) == 4
        assert manager.start("active-0", "Start-Sleep -Seconds 30", tmp_path, 60)["job_id"] == jobs[0]["job_id"]
        with pytest.raises(RuntimeError, match="limit"):
            manager.start("fifth", "Write-Output x", tmp_path, 10)
    finally:
        manager.close()
