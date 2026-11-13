"""Windows-only process-tree checks for isolated evaluation checkers."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import time

import pytest

pytest.importorskip('win32job', reason='Windows Job Objects are required')

from tools.eval.checker_process import run_checker_process


def _wait_pid_exit(pid: int, timeout: float = 5.0) -> bool:
    import win32api
    import win32con
    import win32event

    try:
        handle = win32api.OpenProcess(win32con.SYNCHRONIZE, False, pid)
    except Exception:
        return True
    try:
        return win32event.WaitForSingleObject(handle, int(timeout * 1000)) == win32event.WAIT_OBJECT_0
    finally:
        win32api.CloseHandle(handle)


def _read_pid(path: Path, timeout: float = 5.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return int(path.read_text(encoding='ascii'))
        time.sleep(0.02)
    raise AssertionError('child process did not record its pid')


def test_timeout_terminates_checker_and_grandchild(tmp_path):
    pid_file = tmp_path / 'grandchild pid.txt'
    parent_pid_file = tmp_path / 'checker pid.txt'
    grandchild = "import time; time.sleep(60)"
    checker = (
        'import pathlib, subprocess, sys, time; '
        f'child=subprocess.Popen([sys.executable, "-c", {grandchild!r}]); '
        f'pathlib.Path({str(pid_file)!r}).write_text(str(child.pid), encoding="ascii"); '
        f'pathlib.Path({str(parent_pid_file)!r}).write_text(str(__import__("os").getpid()), encoding="ascii"); '
        'time.sleep(60)'
    )

    result = run_checker_process([sys.executable, '-c', checker], tmp_path, dict(os.environ), 0.5)

    assert result is None
    assert _wait_pid_exit(_read_pid(parent_pid_file))
    assert _wait_pid_exit(_read_pid(pid_file))


def test_normal_checker_exit_also_terminates_grandchild(tmp_path):
    pid_file = tmp_path / 'normal-exit-child.txt'
    child = "import time; time.sleep(60)"
    checker = (
        'import pathlib, subprocess, sys; '
        f'child=subprocess.Popen([sys.executable, "-c", {child!r}]); '
        f'pathlib.Path({str(pid_file)!r}).write_text(str(child.pid), encoding="ascii"); '
        'raise SystemExit(7)'
    )

    result = run_checker_process([sys.executable, '-c', checker], tmp_path, dict(os.environ), 10)

    assert result == 7
    assert _wait_pid_exit(_read_pid(pid_file))


def test_direct_argv_env_and_paths_with_spaces(tmp_path):
    working = tmp_path / 'folder with spaces'
    working.mkdir()
    output = working / 'value with spaces.txt'
    environment = dict(os.environ)
    environment['MUSEBRIDGE_CHECKER_TEST_SECRET'] = 'private-checker-value'
    code = (
        'import os, pathlib; '
        f'pathlib.Path({str(output)!r}).write_text(os.environ["MUSEBRIDGE_CHECKER_TEST_SECRET"], encoding="utf-8")'
    )
    parent_environment = dict(os.environ)

    result = run_checker_process([sys.executable, '-c', code], working, environment, 10)

    assert result == 0
    assert output.read_text(encoding='utf-8') == 'private-checker-value'
    assert dict(os.environ) == parent_environment


def test_invalid_arguments_fail_without_launching(tmp_path):
    with pytest.raises(ValueError):
        run_checker_process([], tmp_path, {}, 1)
    with pytest.raises(ValueError):
        run_checker_process([sys.executable, 'bad\0arg'], tmp_path, {}, 1)
    with pytest.raises(ValueError):
        run_checker_process([sys.executable, '-c', 'pass'], tmp_path, {}, 0)
