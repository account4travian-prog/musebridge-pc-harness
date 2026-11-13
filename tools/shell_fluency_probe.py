"""Collect the MB-26 P-4 shell fluency probe with a real Muse session.

This preparation CLI records only sanitized counts and independent checks. It
does not change the shell default or replay an uncertain send.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

from tools.harness_probes import new_chat, ready, turn, require_baseline
from musebridge.execution import WRITE_TOOLS, BROWSER_TOOLS


REPO_ROOT = Path(__file__).resolve().parents[1]
REQUESTS = ("find", "count", "python", "env", "chain")
EXPECTED_PATHS = ["a.find", "nested/b.find"]
SEED_FILES = ("a.find", "nested/b.find", "count.txt", "run.py", "env.py", "fail.py", "mark.py")


def seed_project(root: Path) -> dict[str, str]:
    """Create the bounded task fixture and return private seed digests."""
    root = Path(root)
    (root / "nested").mkdir(parents=True, exist_ok=True)
    contents = {
        "a.find": "alpha fixture\n",
        "nested/b.find": "beta fixture\n",
        "count.txt": "one\ntwo\nthree\n",
        "run.py": "print('python-ok')\n",
        "env.py": "import os\nprint(os.environ.get('MUSEBRIDGE_FLUENCY', 'absent'))\n",
        "fail.py": "import os\nimport sys\nassert os.environ.get('MUSEBRIDGE_FLUENCY') is None\nsys.exit(3)\n",
        "mark.py": "from pathlib import Path\nPath('forbidden-chain.txt').write_text('ran\n', encoding='utf-8')\n",
    }
    import hashlib
    for relative, text in contents.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    return {relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
            for relative in SEED_FILES}


def _decode_text(path: Path) -> str | None:
    """Decode common PowerShell and shell redirection encodings safely."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16").lstrip("\ufeff")
        except UnicodeDecodeError:
            return None
    if len(data) >= 4 and len(data) % 2 == 0:
        even_nuls = data[0::2].count(0)
        odd_nuls = data[1::2].count(0)
        half = len(data) // 2
        try:
            if odd_nuls > half // 3:
                return data.decode("utf-16-le")
            if even_nuls > half // 3:
                return data.decode("utf-16-be")
        except UnicodeDecodeError:
            return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _file_checks(project: Path, seed_hashes: dict[str, str]) -> dict[str, bool]:
    import hashlib
    project = Path(project)
    found_text = _decode_text(project / "found.txt")
    count_text = _decode_text(project / "count-result.txt")
    python_text = _decode_text(project / "python-result.txt")
    env_text = _decode_text(project / "env-result.txt")
    found = [] if found_text is None else [line.strip().replace("\\", "/") for line in found_text.splitlines() if line.strip()]
    intact = True
    for relative, digest in seed_hashes.items():
        try:
            intact = intact and hashlib.sha256((project / relative).read_bytes()).hexdigest() == digest
        except OSError:
            intact = False
    return {
        "find_output": found == EXPECTED_PATHS,
        "line_count_output": count_text is not None and count_text.strip() == "3",
        "python_output": python_text is not None and python_text.strip() == "python-ok",
        "one_command_environment": env_text is not None and env_text.strip() == "fixture-value",
        "chain_marker_absent": not (project / "forbidden-chain.txt").exists(),
        "seed_unchanged": intact,
    }


def _command_valid(name: str, shell: str, command_json: str, interpreter: str | None = None) -> bool:
    try:
        payload = json.loads(command_json)
    except (TypeError, json.JSONDecodeError):
        return False
    if payload.get("shell") != shell:
        return False
    argv = payload.get("argv")
    if not isinstance(argv, list) or len(argv) != 1 or not isinstance(argv[0], str):
        return False
    command = argv[0].casefold().replace("\\", "/")
    if interpreter:
        executable = str(interpreter).casefold().replace("\\", "/")
        if name in ("python", "env", "chain") and executable not in command:
            return False
        scrubbed = command.replace(executable, "")
        if re.search(r"(?<![a-z0-9])[a-z]:/|(?:^|[\s'\"])/[a-z0-9_.-]|(?:^|[\s'\"])(?:\.\./|\.\.\\)", scrubbed):
            return False
        if re.search(r"(?:^|[;&|])\s*(?:cd|pushd|set-location)\s", scrubbed):
            return False
    if name == "find":
        return ".find" in command and "found.txt" in command and ("find " in command or "get-childitem" in command)
    if name == "count":
        return "count.txt" in command and "count-result.txt" in command and any(token in command for token in ("wc", "measure-object", ".count", "get-content"))
    if name == "python":
        return "run.py" in command and "python-result.txt" in command
    if name == "env":
        return all(token in command for token in ("musebridge_fluency", "fixture-value", "env.py", "env-result.txt"))
    if name == "chain":
        before, marker = command.find("fail.py"), command.find("mark.py")
        if before < 0 or marker <= before:
            return False
        if shell == "bash":
            return ("&&" in command or bool(re.search(r"(?:^|[;&|])\s*set\s+-e(?:\s|;|$)", command))
                    or bool(re.search(r"\|\|\s*(?:exit|return)", command))
                    or bool(re.search(r"\bif\b.+\bthen\b", command)))
        return ("&&" in command or bool(re.search(r"\bif\b", command)
                and ("lastexitcode" in command or "$?" in command))
                or ("try" in command and "catch" in command))
    return False


def score_shell(project: Path, shell: str, conversation_id: str, seed_hashes: dict[str, str],
                tool_calls: list[dict], linked_job_ids: list[str], job_rows: list[dict],
                interpreter: str | None = None) -> dict:
    """Score one prepared shell using sanitized call/job metadata and project files."""
    calls = [row for row in tool_calls if row.get("tool") == "shell.run"]
    failed_calls = sum(row.get("state") != "succeeded" for row in calls)
    administrative = {'plan.update', 'task.create', 'task.update', 'project.note'}
    disallowed = (set(WRITE_TOOLS) - {"shell.run"} - administrative) | set(BROWSER_TOOLS) | {"desktop.act"}
    other_writes = sum(row.get("tool") in disallowed for row in tool_calls)
    linked = set(linked_job_ids)
    jobs = [row for row in job_rows if row.get("job_id") in linked]
    expected_by_request = {f"{conversation_id}:{name}": name for name in REQUESTS}
    by_request = {row.get("request_id"): row for row in jobs}
    missing_jobs = sum(request not in by_request for request in expected_by_request)
    exact_jobs = len(calls) == 5 and len(linked) == 5 and len(jobs) == 5 and set(by_request) == set(expected_by_request)
    command_checks = {
        name: bool(row and _command_valid(name, shell, row.get("command_json"), interpreter))
        for request, name in expected_by_request.items()
        for row in [by_request.get(request)]
    }
    cwd_ok = True
    root = Path(project).resolve()
    for row in jobs:
        try:
            Path(row["cwd"]).resolve().relative_to(root)
        except (OSError, KeyError, ValueError, TypeError):
            cwd_ok = False
    nonzero_unexpected = 0
    exit_checks = {}
    for request, name in expected_by_request.items():
        row = by_request.get(request)
        if not row:
            exit_checks[name] = False
            continue
        expected_exit = 3 if name == "chain" else 0
        exit_checks[name] = row.get("state") == ("failed" if expected_exit else "succeeded") and row.get("exit_code") == expected_exit
        if row.get("exit_code") not in (None, 0) and not (name == "chain" and row.get("exit_code") == 3):
            nonzero_unexpected += 1
    file_checks = _file_checks(project, seed_hashes)
    file_check_for = {
        "find": "find_output", "count": "line_count_output", "python": "python_output",
        "env": "one_command_environment", "chain": "chain_marker_absent",
    }
    request_checks = {}
    for index, (request, name) in enumerate(expected_by_request.items()):
        row = by_request.get(request)
        call_ok = index < len(calls) and calls[index].get("state") == "succeeded"
        this_cwd_ok = True
        if row:
            try:
                Path(row["cwd"]).resolve().relative_to(root)
            except (OSError, KeyError, ValueError, TypeError):
                this_cwd_ok = False
        request_checks[name] = bool(row and call_ok and command_checks[name] and exit_checks[name]
                                    and file_checks[file_check_for[name]] and file_checks["seed_unchanged"]
                                    and this_cwd_ok)
    failed_commands = sum(not value for value in request_checks.values())
    # Extra shell calls/jobs or prohibited mutations invalidate at least one
    # command even if the five expected outputs happen to be present.
    if not exact_jobs or len(calls) != 5 or other_writes:
        failed_commands = min(5, max(1, failed_commands))
    checks = {**{f"command_{key}": value for key, value in command_checks.items()},
              **{f"request_{key}": value for key, value in request_checks.items()},
              **{f"exit_{key}": value for key, value in exit_checks.items()},
              **{f"file_{key}": value for key, value in file_checks.items()},
              "exactly_five_calls_and_jobs": exact_jobs,
              "no_failed_shell_run_calls": failed_calls == 0,
              "no_other_write_tools": other_writes == 0,
              "jobs_in_project": cwd_ok,
              "no_unexpected_nonzero_jobs": nonzero_unexpected == 0}
    return {
        "shell": shell,
        "status": "completed" if exact_jobs else "incomplete",
        "shell_run_calls": len(calls),
        "failed_shell_run_calls": failed_calls,
        "failed_commands": failed_commands,
        "other_write_tool_calls": other_writes,
        "job_records": len(jobs),
        "missing_jobs": missing_jobs,
        "unexpected_nonzero_jobs": nonzero_unexpected,
        "checks": checks,
        "passed": all(checks.values()),
    }


def _prompt(shell: str, interpreter: str) -> str:
    shell_title = "PowerShell" if shell == "powershell" else "Git Bash"
    return f"""Perform exactly five `shell.run` calls using shell `{shell}` ({shell_title}) in this disposable project. Keep every working directory and every file operation inside the project root; use relative project paths. The only absolute executable path permitted is the Python interpreter given below. Do not use another shell, terminal, file.write, or job.start. Use request IDs exactly: `find`, `count`, `python`, `env`, `chain`. Do not retry a call with an uncertain outcome. If a shell.run returns a running job handle, wait with shell.job for that same handle rather than starting the command again.

Python interpreter executable: {interpreter}

1. Find all `*.find` files recursively. Write sorted relative paths using `/`, one per line, to `found.txt`.
2. Count the lines in `count.txt` and write only the decimal count to `count-result.txt`.
3. Run `run.py` with the exact interpreter above and write its stdout to `python-result.txt`.
4. For this command only, set `MUSEBRIDGE_FLUENCY=fixture-value`, run `env.py` with the exact interpreter above, and write stdout to `env-result.txt`. Do not persist the variable.
5. In one command, run `fail.py` and run `mark.py` only if `fail.py` succeeds, using the exact interpreter above for both scripts. The command must return exit code 3. `forbidden-chain.txt` must remain absent.

For Git Bash, convert the provided Windows interpreter path with `cygpath -u` inside the command as needed. Do not change directories outside the project.

Use one shell.run call per numbered item, in order. Do not make any other shell.run calls. Afterward report the five exit codes and whether the requested files were produced; do not claim a shell fluent based on an output you did not verify."""


def _collect_shell(worker, project: Path, cid: str, shell: str, seed_hashes: dict[str, str]) -> dict:
    calls = worker.catalog.rows("SELECT tool,state FROM tool_calls WHERE conversation_id=? ORDER BY created,rowid", (cid,))
    linked = worker.catalog.rows("SELECT job_id FROM job_links WHERE conversation_id=?", (cid,))
    identifiers = [row["job_id"] for row in linked]
    manager = worker.router.jobs
    # Let short fixture jobs settle locally if Muse returned an early handle.
    for identifier in identifiers:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = manager.status(identifier)
            if state["state"] not in ("accepted", "running"):
                break
            manager.wait(identifier, state["output_end"], min(1.0, deadline - time.monotonic()))
    with manager._lock:
        if identifiers:
            placeholders = ",".join("?" for _ in identifiers)
            rows = manager._db.execute(
                f"SELECT job_id,request_id,command_json,cwd,state,exit_code FROM jobs WHERE job_id IN ({placeholders})",
                identifiers,
            ).fetchall()
        else:
            rows = []
    jobs = [dict(zip(("job_id", "request_id", "command_json", "cwd", "state", "exit_code"), row))
            for row in rows]
    result = score_shell(project, shell, cid, seed_hashes, calls, identifiers, jobs, sys.executable)
    return result


async def probe_shell(worker, root: Path, shell: str) -> dict:
    await ready(worker)
    cid, project = await new_chat(worker, root, "p4-" + shell)
    seed_hashes = seed_project(project)
    try:
        await turn(worker, cid, _prompt(shell, sys.executable), 180)
    except Exception as exc:
        partial = _collect_shell(worker, project, cid, shell, seed_hashes)
        partial.update({"status": "uncertain", "error_code": type(exc).__name__, "passed": False})
        return partial
    return _collect_shell(worker, project, cid, shell, seed_hashes)


def _write_report(report: dict) -> tuple[Path, Path]:
    directory = REPO_ROOT / "evidence"
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "MB-26-shell-fluency.json"
    md_path = directory / "MB-26-shell-fluency.md"
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    rows = ["# MB-26 shell fluency probe", "", "No default shell change is made by this probe.", "",
            "| Shell | Status | `shell.run` calls | Failed calls | Failed commands | Job records | Result |",
            "|---|---:|---:|---:|---:|---:|---:|"]
    for result in report.get("shells", []):
        rows.append("| {shell} | {status} | {shell_run_calls} | {failed_shell_run_calls} | {failed_commands} | {job_records} | {passed} |".format(
            shell=result.get("shell", "unknown"), status=result.get("status", "unknown"),
            shell_run_calls=result.get("shell_run_calls", 0),
            failed_shell_run_calls=result.get("failed_shell_run_calls", 0),
            failed_commands=result.get("failed_commands", 0),
            job_records=result.get("job_records", 0), passed=result.get("passed", False)))
    rows.extend(["", "Check results:"])
    for result in report.get("shells", []):
        for name, value in sorted(result.get("checks", {}).items()):
            rows.append(f"- {result.get('shell', 'unknown')} / {name}: {bool(value)}")
    rows.append("")
    json_created = False
    try:
        with json_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
        json_created = True
        with md_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(rows))
    except Exception:
        if json_created:
            json_path.unlink(missing_ok=True)
        raise
    return json_path, md_path


async def run_live() -> dict:
    from PySide6.QtCore import QLockFile
    from musebridge.app import Worker
    from musebridge.storage import Store
    require_baseline()
    probe_report = json.loads((REPO_ROOT / 'evidence' / 'MB-25-probes.json').read_text(encoding='utf-8'))
    if probe_report.get('P-1-decision', {}).get('inline_cap_s') != 25:
        raise RuntimeError('Review the measured inline wait cap before running this probe')
    if any((REPO_ROOT / 'evidence' / ('MB-26-shell-fluency' + extension)).exists() for extension in ('.json', '.md')):
        raise RuntimeError('A shell fluency report already exists; inspect it before any explicit rerun')
    if "MUSEBRIDGE_EVAL_EXPECTED" in os.environ:
        raise RuntimeError("Remove MUSEBRIDGE_EVAL_EXPECTED before running the shell fluency probe")
    store = Store()
    lock = QLockFile(str(store.directory / "application.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        raise RuntimeError("Close MuseBridge before the shell fluency probe")
    worker = None
    report = {"probe": "MB-26-P4", "created_at": datetime.now(timezone.utc).isoformat(), "shells": []}
    aborted = False
    try:
        case_root = REPO_ROOT / ".local" / ("shell-fluency-" + uuid.uuid4().hex[:12])
        case_root.mkdir(parents=True, exist_ok=False)
        worker = Worker(store)
        worker.connect()
        await ready(worker)
        from musebridge.shells import resolve_shell
        for shell in ("powershell", "bash"):
            try:
                resolve_shell(shell)
            except RuntimeError:
                report["shells"].append({"shell": shell, "status": "skipped_unavailable",
                                         "shell_run_calls": 0, "failed_shell_run_calls": 0,
                                         "failed_commands": 0, "other_write_tool_calls": 0,
                                         "job_records": 0, "missing_jobs": 5,
                                         "unexpected_nonzero_jobs": 0, "checks": {}, "passed": False})
                continue
            result = await probe_shell(worker, case_root, shell)
            report["shells"].append(result)
            if result["status"] == "uncertain":
                aborted = True
                break
    except Exception as exc:
        report["error_code"] = type(exc).__name__
        aborted = True
    finally:
        if aborted:
            report["aborted_on_uncertainty"] = True
        if worker is not None:
            try:
                worker.close()
            except Exception as exc:
                report["close_error_code"] = type(exc).__name__
        lock.unlock()
        _write_report(report)
    return report


def main(argv=None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    try:
        report = asyncio.run(run_live())
    except Exception as exc:
        print(type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps({"probe": report["probe"], "aborted_on_uncertainty": report.get("aborted_on_uncertainty", False),
                      "shells": [{"shell": row["shell"], "status": row["status"], "passed": row["passed"]}
                                 for row in report["shells"]]}, sort_keys=True))
    return 1 if report.get("aborted_on_uncertainty") else 0


if __name__ == "__main__":
    raise SystemExit(main())
