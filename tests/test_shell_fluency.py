import json
from pathlib import Path

import pytest

from tools.shell_fluency_probe import REQUESTS, score_shell, seed_project
from tools.shell_fluency_probe import _command_valid

INTERPRETER = r"C:\Tools\Python\python.exe"


def prepared_project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    hashes = seed_project(root)
    (root / "found.txt").write_bytes("a.find\r\nnested/b.find\r\n".encode("utf-16"))
    (root / "count-result.txt").write_bytes(b"\xef\xbb\xbf3\r\n")
    (root / "python-result.txt").write_bytes("python-ok\n".encode("utf-16-le"))
    (root / "env-result.txt").write_text("fixture-value\n", encoding="utf-8")
    return root, hashes


def fake_records(root, cid, shell):
    commands = {
        "find": "find . -type f -name '*.find' | sort > found.txt" if shell == "bash"
                else "Get-ChildItem -Recurse -File -Filter '*.find' | Sort-Object | Set-Content found.txt",
        "count": "wc -l < count.txt | tr -d ' ' > count-result.txt" if shell == "bash"
                 else "(Get-Content count.txt).Count | Set-Content count-result.txt",
        "python": f'"$(cygpath -u \'{INTERPRETER}\')" run.py > python-result.txt' if shell == "bash"
                  else f"& '{INTERPRETER}' run.py | Set-Content python-result.txt",
        "env": f"MUSEBRIDGE_FLUENCY=fixture-value \"$(cygpath -u '{INTERPRETER}')\" env.py > env-result.txt" if shell == "bash"
               else f"$env:MUSEBRIDGE_FLUENCY='fixture-value'; & '{INTERPRETER}' env.py | Set-Content env-result.txt",
        "chain": f'"$(cygpath -u \'{INTERPRETER}\')" fail.py && "$(cygpath -u \'{INTERPRETER}\')" mark.py' if shell == "bash"
                 else f"& '{INTERPRETER}' fail.py; if ($LASTEXITCODE -ne 0) {{ exit $LASTEXITCODE }}; & '{INTERPRETER}' mark.py",
    }
    calls = [{"tool": "shell.run", "state": "succeeded"} for _ in REQUESTS]
    links = [f"job-{name}" for name in REQUESTS]
    jobs = []
    for name in REQUESTS:
        jobs.append({
            "job_id": f"job-{name}",
            "request_id": f"{cid}:{name}",
            "command_json": json.dumps({"shell": shell, "argv": [commands[name]]}),
            "cwd": str(root),
            "state": "failed" if name == "chain" else "succeeded",
            "exit_code": 3 if name == "chain" else 0,
        })
    return calls, links, jobs


@pytest.mark.parametrize("shell", ["powershell", "bash"])
def test_prepared_fixture_and_expected_job_receipts_pass_offline(tmp_path, shell):
    root, hashes = prepared_project(tmp_path)
    calls, links, jobs = fake_records(root, "chat-a", shell)

    result = score_shell(root, shell, "chat-a", hashes, calls, links, jobs, INTERPRETER)

    assert result["passed"] is True
    assert result["shell_run_calls"] == 5
    assert result["job_records"] == 5
    assert result["unexpected_nonzero_jobs"] == 0
    assert result["failed_commands"] == 0
    assert result["checks"]["exit_chain"] is True
    assert "command_json" not in result
    assert "output" not in result


def test_seeded_project_without_results_fails_independent_checks(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    hashes = seed_project(root)
    calls, links, jobs = fake_records(root, "chat-b", "powershell")

    result = score_shell(root, "powershell", "chat-b", hashes, calls, links, jobs, INTERPRETER)

    assert result["passed"] is False
    assert result["checks"]["file_seed_unchanged"] is True
    assert result["checks"]["file_find_output"] is False
    assert result["checks"]["file_chain_marker_absent"] is True
    assert result["failed_commands"] == 4


def test_planning_updates_do_not_count_as_command_failures(tmp_path):
    root, hashes = prepared_project(tmp_path)
    calls, links, jobs = fake_records(root, 'planning', 'powershell')
    calls.extend([{'tool': 'plan.update', 'state': 'succeeded'},
                  {'tool': 'task.create', 'state': 'succeeded'}])
    result = score_shell(root, 'powershell', 'planning', hashes, calls, links, jobs, INTERPRETER)
    assert result['passed']
    assert result['failed_commands'] == 0


def test_missing_or_wrong_shell_jobs_and_failed_tool_calls_are_counted(tmp_path):
    root, hashes = prepared_project(tmp_path)
    calls, links, jobs = fake_records(root, "chat-c", "bash")
    calls[0]["state"] = "failed"
    jobs.pop()

    result = score_shell(root, "powershell", "chat-c", hashes, calls, links[:-1], jobs, INTERPRETER)

    assert result["passed"] is False
    assert result["failed_shell_run_calls"] == 1
    assert result["missing_jobs"] == 1
    assert result["checks"]["exactly_five_calls_and_jobs"] is False
    assert result["checks"]["jobs_in_project"] is True
    assert result["checks"]["command_find"] is False


def test_unexpected_nonzero_exit_and_out_of_project_working_directory_fail(tmp_path):
    root, hashes = prepared_project(tmp_path)
    calls, links, jobs = fake_records(root, "chat-d", "powershell")
    jobs[0]["exit_code"] = 7
    jobs[0]["state"] = "failed"
    jobs[1]["cwd"] = str(tmp_path)

    result = score_shell(root, "powershell", "chat-d", hashes, calls, links, jobs, INTERPRETER)

    assert result["passed"] is False
    assert result["unexpected_nonzero_jobs"] == 1
    assert result["checks"]["jobs_in_project"] is False


@pytest.mark.parametrize("shell,command", [
    ("powershell", "python fail.py && python mark.py"),
    ("powershell", "python fail.py; if ($?) { python mark.py } else { exit 3 }"),
    ("bash", "set -e; python fail.py; python mark.py"),
    ("bash", "python fail.py || exit $?; python mark.py"),
    ("bash", "if python fail.py; then python mark.py; else exit 3; fi"),
])
def test_chain_command_checker_accepts_shell_specific_conditional_styles(shell, command):
    payload = json.dumps({"shell": shell, "argv": [command]})
    assert _command_valid("chain", shell, payload)
