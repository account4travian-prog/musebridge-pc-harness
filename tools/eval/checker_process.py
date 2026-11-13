"""Run an independent checker inside an owned Windows process tree."""
from __future__ import annotations

import math
import os
from pathlib import Path
import subprocess


_TERMINATE_WAIT_MS = 5000


def run_checker_process(argv, cwd, env, timeout_s) -> int | None:
    """Run argv directly and return its exit code, or None after a timeout.

    The suspended process is assigned to a kill-on-close Job Object before it
    resumes. The whole tree is terminated after parent exit and on timeout.
    Standard streams go to NUL, so checker output never enters harness logs.
    """
    command = _validate_argv(argv)
    workdir = Path(cwd).resolve(strict=True)
    if not workdir.is_dir():
        raise ValueError('cwd must be a directory')
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
        raise ValueError('timeout_s must be a finite positive number')
    timeout_s = float(timeout_s)
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError('timeout_s must be a finite positive number')
    environment = _validate_environment(env)
    return _run_windows(command, workdir, environment, timeout_s)


def _validate_argv(argv) -> list[str]:
    if not isinstance(argv, (list, tuple)) or not argv:
        raise ValueError('argv must be a nonempty string sequence')
    if any(not isinstance(item, str) or not item or '\0' in item for item in argv):
        raise ValueError('argv must contain nonempty strings without NUL characters')
    return list(argv)


def _validate_environment(env) -> dict[str, str]:
    if not isinstance(env, dict):
        raise ValueError('env must be a string mapping')
    if any(not isinstance(key, str) or not isinstance(value, str) or '\0' in key or '\0' in value
           for key, value in env.items()):
        raise ValueError('env must contain strings without NUL characters')
    return dict(env)


def _run_windows(argv: list[str], cwd: Path, env: dict[str, str], timeout_s: float) -> int | None:
    if os.name != 'nt':
        raise OSError('checker process containment requires Windows Job Objects')

    import win32api
    import win32con
    import win32event
    import win32file
    import win32job
    import win32process
    import win32security

    job = process = thread_handle = null_stdio = None
    assigned = False
    timed_out = False
    exit_code = None
    try:
        job = win32job.CreateJobObject(None, '')
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        info['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)

        security = win32security.SECURITY_ATTRIBUTES()
        security.bInheritHandle = True
        null_stdio = win32file.CreateFile(
            'NUL', win32con.GENERIC_READ | win32con.GENERIC_WRITE,
            win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
            security, win32con.OPEN_EXISTING, win32con.FILE_ATTRIBUTE_NORMAL, None)

        startup = win32process.STARTUPINFO()
        startup.dwFlags |= win32con.STARTF_USESTDHANDLES | win32con.STARTF_USESHOWWINDOW
        startup.wShowWindow = win32con.SW_HIDE
        startup.hStdInput = null_stdio
        startup.hStdOutput = null_stdio
        startup.hStdError = null_stdio
        creation_flags = (win32con.CREATE_SUSPENDED | win32con.CREATE_NO_WINDOW |
                          win32con.CREATE_UNICODE_ENVIRONMENT)
        process, thread_handle, _pid, _tid = win32process.CreateProcess(
            argv[0], subprocess.list2cmdline(argv), None, None, True,
            creation_flags, env, str(cwd), startup)
        win32job.AssignProcessToJobObject(job, process)
        assigned = True
        win32process.ResumeThread(thread_handle)

        wait_ms = min(0xFFFFFFFE, max(1, math.ceil(timeout_s * 1000)))
        wait_result = win32event.WaitForSingleObject(process, wait_ms)
        timed_out = wait_result == win32con.WAIT_TIMEOUT
        if wait_result == win32con.WAIT_OBJECT_0:
            exit_code = int(win32process.GetExitCodeProcess(process))
        else:
            if wait_result != win32con.WAIT_TIMEOUT:
                raise OSError('checker process wait failed')
    finally:
        if job is not None:
            _terminate_tree(job, process, assigned, win32job, win32process, win32event,
                            win32con, win32api)
            job = None
        elif process is not None and win32event.WaitForSingleObject(process, 0) == win32con.WAIT_TIMEOUT:
            try:
                win32process.TerminateProcess(process, 1)
            except Exception:
                pass
            win32event.WaitForSingleObject(process, _TERMINATE_WAIT_MS)
        for handle in (null_stdio, thread_handle, process):
            if handle is not None:
                try:
                    win32api.CloseHandle(handle)
                except Exception:
                    pass
    return None if timed_out else exit_code


def _terminate_tree(job, process, assigned, win32job, win32process,
                    win32event, win32con, win32api) -> None:
    """Terminate all owned processes and wait briefly for the job to empty."""
    if assigned:
        try:
            win32job.TerminateJobObject(job, 1)
        except Exception:
            pass
        win32event.WaitForSingleObject(job, _TERMINATE_WAIT_MS)
    elif process is not None:
        try:
            win32process.TerminateProcess(process, 1)
        except Exception:
            pass
        win32event.WaitForSingleObject(process, _TERMINATE_WAIT_MS)
    try:
        win32api.CloseHandle(job)
    except Exception:
        pass
