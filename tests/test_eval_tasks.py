import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "tools" / "eval" / "tasks"
EXPECTED = {
    "01_off_by_one": (True, None),
    "02_crlf_rename": (True, None),
    "03_cli_flag": (True, None),
    "04_find_log": (True, None),
    "05_make_yaml": (True, None),
    "06_powershell_spaces": (True, None),
    "07_small_project": (False, None),
    "08_long_command": (False, None),
    "09_browser_setting": (False, "browser"),
    "10_undo_change": (False, None),
    "11_desktop_form": (False, "desktop"),
    "12_vision_code": (False, "vision"),
}


def project_copy(name, destination):
    source = TASKS / name / "seed"
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".keep"))
    return destination


def check(name, cwd, *, env=None, timeout=45):
    return subprocess.run([sys.executable, str(TASKS / name / "check.py")], cwd=cwd,
                          env=env, capture_output=True, text=True, timeout=timeout)


def assert_pass(result):
    assert result.returncode == 0, result.stdout + result.stderr


def assert_fail(result):
    assert result.returncode != 0, result.stdout + result.stderr


def test_all_twelve_task_contracts_are_complete_and_well_formed():
    names = sorted(path.name for path in TASKS.iterdir() if path.is_dir())
    assert names == sorted(EXPECTED)
    for name, (core, fixture) in EXPECTED.items():
        directory = TASKS / name
        assert (directory / "seed").is_dir()
        assert any(path.is_file() for path in (directory / "seed").rglob("*"))
        prompt = (directory / "prompt.txt").read_text(encoding="utf-8")
        checker = (directory / "check.py").read_text(encoding="utf-8")
        ast.parse(checker, filename=str(directory / "check.py"))
        limits = json.loads((directory / "limits.json").read_text(encoding="utf-8"))
        assert set(limits) == {"max_minutes", "max_turns", "core", "fixture", "check_timeout_s"}
        assert limits["max_minutes"] in (3, 5)
        assert limits["max_turns"] == 4
        assert limits["core"] is core
        assert limits["fixture"] == fixture
        assert 1 <= limits["check_timeout_s"] <= 120
        assert (directory / "followup.txt").is_file() == (name == "10_undo_change")
        if fixture == "browser":
            assert "{fixture_url}" in prompt
        if fixture in ("desktop", "vision"):
            assert "{window_handle}" in prompt
            assert "focus" in prompt.lower() or "activate" in prompt.lower()
    long_limits = json.loads((TASKS / "08_long_command" / "limits.json").read_text(encoding="utf-8"))
    long_seed = (TASKS / "08_long_command" / "seed" / "long_check.py").read_text(encoding="utf-8")
    assert "time.sleep(70)" in long_seed
    assert long_limits["check_timeout_s"] >= 80
    assert "70 seconds" in (TASKS / "08_long_command" / "prompt.txt").read_text(encoding="utf-8")
    browser_prompt = (TASKS / "09_browser_setting" / "prompt.txt").read_text(encoding="utf-8")
    assert "username `fixture`" in browser_prompt and "password `test-only`" in browser_prompt
    assert "Mode to Advanced" in browser_prompt
    assert "confirmation_code" not in browser_prompt


def test_core_seeded_failures_then_independent_checks_pass(tmp_path):
    one = project_copy("01_off_by_one", tmp_path / "one")
    assert_fail(check("01_off_by_one", one))
    code = one / "counter" / "counter.py"
    code.write_text(code.read_text(encoding="utf-8").replace("range(limit + 1)", "range(limit)"), encoding="utf-8")
    original_test = one / "tests" / "test_counter.py"
    assert_pass(check("01_off_by_one", one))
    assert original_test.read_bytes() == (TASKS / "01_off_by_one" / "seed" / "tests" / "test_counter.py").read_bytes()

    two = project_copy("02_crlf_rename", tmp_path / "two")
    assert_fail(check("02_crlf_rename", two))
    for path in two.glob("*.py"):
        data = path.read_bytes().replace(b"legacy_label", b"display_label")
        path.write_bytes(data)
    assert_pass(check("02_crlf_rename", two))
    assert all(b"\n" not in path.read_bytes().replace(b"\r\n", b"") for path in two.glob("*.py"))

    three = project_copy("03_cli_flag", tmp_path / "three")
    assert_fail(check("03_cli_flag", three))
    cli = three / "cli.py"
    cli.write_text("import argparse\n\ndef main():\n    parser = argparse.ArgumentParser()\n    parser.add_argument('--shout', action='store_true')\n    args = parser.parse_args()\n    print('HELLO' if args.shout else 'hello')\n\nif __name__ == '__main__':\n    main()\n", encoding="utf-8")
    readme = three / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nUse `--shout` to print HELLO.\n", encoding="utf-8")
    assert_pass(check("03_cli_flag", three))

    four = project_copy("04_find_log", tmp_path / "four")
    assert len(list((four / "logs").glob("*.log"))) == 300
    assert_fail(check("04_find_log", four))
    (four / "answer.txt").write_text("2026-11-12T10:34:56Z\n", encoding="utf-8")
    assert_pass(check("04_find_log", four))

    five = project_copy("05_make_yaml", tmp_path / "five")
    assert_fail(check("05_make_yaml", five))
    makefile = five / "Makefile"
    makefile.write_bytes(makefile.read_bytes().replace(b"python -m pytest tests\n", b"python -m pytest tests -q\n"))
    settings = five / "settings.yaml"
    settings.write_text(settings.read_text(encoding="utf-8").replace("workers: 2", "workers: 3"), encoding="utf-8")
    assert_pass(check("05_make_yaml", five))

    six = project_copy("06_powershell_spaces", tmp_path / "six")
    assert_fail(check("06_powershell_spaces", six))
    script = six / "build.ps1"
    script.write_text("$source = Join-Path $PSScriptRoot 'My Files\\input.txt'\n$destination = Join-Path $PSScriptRoot 'output file.txt'\nGet-Content -Raw $source | Set-Content -NoNewline -Encoding utf8 $destination\n", encoding="utf-8")
    assert_pass(check("06_powershell_spaces", six))


def test_remaining_checkers_validate_dynamic_fixture_and_recovery_contracts(tmp_path, monkeypatch):
    seven = project_copy("07_small_project", tmp_path / "seven")
    assert_fail(check("07_small_project", seven))
    package = seven / "tinycalc"; package.mkdir()
    (package / "__init__.py").write_text("from .ops import add, subtract, multiply\n", encoding="utf-8")
    (package / "ops.py").write_text("def add(a, b): return a + b\ndef subtract(a, b): return a - b\ndef multiply(a, b): return a * b\n", encoding="utf-8")
    tests = seven / "tests"; tests.mkdir()
    (tests / "test_ops.py").write_text("from tinycalc import add, subtract, multiply\ndef test_positive(): assert add(2, 3) == 5\ndef test_negative(): assert subtract(-2, -3) == 1\ndef test_zero(): assert multiply(0, 8) == 0\n", encoding="utf-8")
    assert_pass(check("07_small_project", seven))

    eight = TASKS / "08_long_command"
    checker_source = (eight / "check.py").read_text(encoding="utf-8")
    assert "timeout=90" in checker_source
    assert "time.sleep(70)" in (eight / "seed" / "long_check.py").read_text(encoding="utf-8")

    nine = project_copy("09_browser_setting", tmp_path / "nine")
    expected_code = "817263"
    expected_state = {"saved": True, "mode": "advanced", "confirmation_code": expected_code}
    env = os.environ.copy()
    env["MUSEBRIDGE_EVAL_EXPECTED"] = json.dumps({"confirmation_code": expected_code,
        "fixture_state": expected_state})
    assert_fail(check("09_browser_setting", nine, env=env))
    (nine / "answer.txt").write_text(expected_code + "\n", encoding="utf-8")
    (nine / "fixture_state.json").write_text(json.dumps({"saved": True, "mode": "standard",
        "confirmation_code": "000000"}), encoding="utf-8")
    assert_pass(check("09_browser_setting", nine, env=env))
    env["MUSEBRIDGE_EVAL_EXPECTED"] = json.dumps({"confirmation_code": expected_code,
        "fixture_state": {**expected_state, "mode": "standard"}})
    assert_fail(check("09_browser_setting", nine, env=env))

    ten = project_copy("10_undo_change", tmp_path / "ten")
    first_prompt = (TASKS / "10_undo_change" / "prompt.txt").read_text(encoding="utf-8")
    followup = (TASKS / "10_undo_change" / "followup.txt").read_text(encoding="utf-8")
    assert "do not undo" in first_prompt.lower()
    assert "undo" in followup.lower()
    (ten / "notes.txt").write_text("status: reviewed\nowner-note: preserve these bytes\n", encoding="utf-8")
    assert_fail(check("10_undo_change", ten))
    (ten / "notes.txt").write_bytes((TASKS / "10_undo_change" / "seed" / "notes.txt").read_bytes())
    assert_pass(check("10_undo_change", ten))

    eleven = project_copy("11_desktop_form", tmp_path / "eleven")
    assert_fail(check("11_desktop_form", eleven))
    (eleven / "saved.json").write_text(json.dumps({"name": "Test fixture", "topic": "harness", "amount": "3"}), encoding="utf-8")
    assert_pass(check("11_desktop_form", eleven))

    twelve = project_copy("12_vision_code", tmp_path / "twelve")
    code = "504183"
    env["MUSEBRIDGE_EVAL_EXPECTED"] = json.dumps({"code": code})
    assert_fail(check("12_vision_code", twelve, env=env))
    (twelve / "answer.txt").write_text(code + "\n", encoding="utf-8")
    assert_pass(check("12_vision_code", twelve, env=env))
