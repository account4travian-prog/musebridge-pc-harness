"""Journaled process jobs for MuseBridge.

Commands are scripts passed as one argv element to a hidden, noninteractive
shell process. On Windows the supervisor uses job objects so a kill ends the
whole process tree; on Linux it uses a POSIX process group for the same
guarantee. This module is a process supervisor, not a security sandbox;
callers must validate working directories and commands.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import math
from pathlib import Path

try:
    import win32api
    import win32con
    import win32file
    import win32job
    import win32pipe
    import win32process
    import win32event
    _WIN32 = True
except ImportError:  # Linux: the Windows-only process APIs are unavailable.
    win32api = win32con = win32file = win32job = win32pipe = win32process = win32event = None
    _WIN32 = False
from .shells import command_argv, command_line, normalize_shell, resolve_shell


_TERMINAL = {"succeeded", "failed", "cancelled", "interrupted", "outcome_unknown"}
_OUTPUT_LIMIT = 256 * 1024
_HEAD_LIMIT = 8192
_PAGE_LIMIT = 16 * 1024
_ACTIVE_LIMIT = 4
_POLL = 0.04


class JobManager:
    def __init__(self, directory, activity=lambda message: None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.activity = activity
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._db = sqlite3.connect(self.directory / "jobs.sqlite3", check_same_thread=False,
                                   isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
            args_hash TEXT NOT NULL, command_json TEXT NOT NULL, cwd TEXT NOT NULL,
            timeout_s REAL NOT NULL, state TEXT NOT NULL, created REAL NOT NULL,
            started REAL, finished REAL, exit_code INTEGER, output TEXT NOT NULL DEFAULT '',
            output_base INTEGER NOT NULL DEFAULT 0, error TEXT
        )""")
        self._migrate_output()
        # The process cannot be recovered safely after manager restart. Keep its
        # admission record and make the ambiguous result visible without replay.
        self._db.execute("UPDATE jobs SET state='outcome_unknown', finished=?, error='manager restarted while job was incomplete' WHERE state IN ('accepted','running')",
                         (time.time(),))
        self._jobs = {}
        self._threads = set()
        self._blocked_output = set()
        self._paused = False
        self._closed = False
        self._db_closed = False

    def _migrate_output(self):
        """Add bounded-head accounting without losing the legacy output ring."""
        self._db.execute('BEGIN IMMEDIATE')
        try:
            columns = {row[1] for row in self._db.execute('PRAGMA table_info(jobs)')}
            if 'output_head' not in columns:
                self._db.execute("ALTER TABLE jobs ADD COLUMN output_head TEXT NOT NULL DEFAULT ''")
                self._db.execute(
                    'UPDATE jobs SET output_head=CASE WHEN output_base=0 THEN substr(output,1,?) ELSE ? END',
                    (_HEAD_LIMIT, ''),
                )
            if 'output_total' not in columns:
                self._db.execute('ALTER TABLE jobs ADD COLUMN output_total INTEGER NOT NULL DEFAULT 0')
                self._db.execute('UPDATE jobs SET output_total=output_base+length(output)')
            self._db.execute('COMMIT')
        except Exception:
            self._db.execute('ROLLBACK')
            raise

    def start(self, request_id, command, cwd, timeout_s=300, shell=None):
        """Start or inspect an identical journaled request without replaying it."""
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("request_id must be a nonempty string")
        shell = normalize_shell(shell)
        if isinstance(command, str):
            argv = [command]
        elif isinstance(command, (list, tuple)) and len(command) == 1 and all(isinstance(x, str) for x in command):
            argv = list(command)
        else:
            raise ValueError("command must be a script string or one-element compatibility list")
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
            raise ValueError("timeout_s must be a number")
        timeout_s = float(timeout_s)
        if not math.isfinite(timeout_s) or not 0 < timeout_s <= 24 * 60 * 60:
            raise ValueError("timeout_s must be in (0, 86400]")
        cwd = str(Path(cwd).resolve(strict=True))
        if not Path(cwd).is_dir():
            raise ValueError("cwd must be a directory")
        legacy_encoded = json.dumps({"command": argv, "cwd": cwd, "timeout_s": timeout_s},
                                    sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        legacy_digest = hashlib.sha256(legacy_encoded.encode("utf-8")).hexdigest()
        encoded = json.dumps({"command": argv, "cwd": cwd, "timeout_s": timeout_s,
                              "shell": shell},
                             sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self._condition:
            if self._closed:
                raise RuntimeError("job manager is closed")
            row = self._db.execute("SELECT job_id,args_hash FROM jobs WHERE request_id=?", (request_id,)).fetchone()
            if row:
                legacy_powershell_retry = shell == 'powershell' and row[1] == legacy_digest
                if row[1] != digest and not legacy_powershell_retry:
                    raise ValueError("request_id was already used with different arguments")
                return self._snapshot(row[0], 0)
            if self._paused:
                raise RuntimeError("job admission is paused")
            active = self._db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('accepted','running')").fetchone()[0]
            if active >= _ACTIVE_LIMIT:
                raise RuntimeError("active job limit reached")
            job_id = str(uuid.uuid4())
            command_record = json.dumps({'shell': shell, 'argv': argv}, ensure_ascii=False)
            self._db.execute("INSERT INTO jobs(job_id,request_id,args_hash,command_json,cwd,timeout_s,state,created) VALUES(?,?,?,?,?,?,?,?)",
                             (job_id, request_id, digest, command_record, cwd, timeout_s, "accepted", time.time()))
            thread = threading.Thread(target=self._run, args=(job_id, argv, cwd, timeout_s, shell),
                                      name="MuseBridge-job-" + job_id[:8], daemon=True)
            self._threads.add(thread)
            thread.start()
            self._emit("Accepted local job " + job_id)
            return self._snapshot(job_id, 0)

    def status(self, job_id, cursor=0):
        with self._lock:
            return self._snapshot(job_id, cursor)

    def preview(self, job_id):
        """Return bounded first and last output segments without overlap."""
        with self._lock:
            row = self._db.execute(
                'SELECT state,exit_code,output,output_base,output_head,output_total,started,finished '
                'FROM jobs WHERE job_id=?', (job_id,)
            ).fetchone()
            if not row:
                raise KeyError(job_id)
            state, exit_code, tail, output_base, head, total, started, finished = row
            if output_base == 0:
                head = ''
                omitted = 0
            else:
                head_length = min(len(head), max(0, total - len(tail)))
                head = head[:head_length]
                omitted = max(0, total - len(head) - len(tail))
            return {'head': head, 'tail': tail, 'omitted_chars': omitted,
                    'total_chars': total, 'started': started, 'finished': finished,
                    'state': state, 'exit_code': exit_code}

    def states(self,job_ids):
        """Lightweight local dashboard query, without reading process output."""
        identifiers=list(dict.fromkeys(job_ids))
        if len(identifiers)>1000:raise ValueError('Too many jobs for one status summary')
        if not identifiers:return {}
        with self._lock:
            query='SELECT job_id,state FROM jobs WHERE job_id IN ('+','.join('?' for _ in identifiers)+')'
            return {row[0]:row[1] for row in self._db.execute(query,identifiers)}

    def finished_summary(self):
        """Notification metadata only, never process output or arguments."""
        with self._lock:
            rows = self._db.execute(
                "SELECT job_id,state,finished FROM jobs "
                "WHERE state NOT IN ('accepted','running') "
                "ORDER BY finished DESC LIMIT 200"
            )
            return [
                {"job_id": row[0], "state": row[1], "finished": row[2]}
                for row in rows
            ]

    def wait(self, job_id, cursor=0, timeout_s=20):
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s):
            raise ValueError("timeout_s must be a finite number")
        timeout_s = max(0.0, min(float(timeout_s), 30.0))
        deadline = time.monotonic() + timeout_s
        with self._condition:
            while True:
                value = self._snapshot(job_id, cursor)
                if value["state"] in _TERMINAL or value["cursor"] > cursor or time.monotonic() >= deadline:
                    return value
                self._condition.wait(max(0.0, deadline - time.monotonic()))

    def _terminate_handle(self, handle, grace_s=1):
        """End one registered job: a Windows job object, or a POSIX process group.

        On POSIX the handle is a Popen started in its own session, so killing
        its process group ends the whole tree, like TerminateJobObject.
        ``grace_s`` bounds the wait for a SIGTERM exit before SIGKILL.
        """
        if sys.platform == "win32" and _WIN32:
            try:
                win32job.TerminateJobObject(handle, 1)
            except Exception:
                pass
            return
        try:
            pgid = os.getpgid(handle.pid)
        except (ProcessLookupError, PermissionError, AttributeError):
            try:
                handle.terminate()
            except Exception:
                pass
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        if grace_s <= 0:
            return
        try:
            handle.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def cancel(self, job_id):
        with self._condition:
            row = self._db.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            if row[0] in _TERMINAL:
                return self._snapshot(job_id, 0)
            self._db.execute("UPDATE jobs SET error='cancellation requested' WHERE job_id=?", (job_id,))
            handle = self._jobs.get(job_id)
            if handle:
                self._terminate_handle(handle)
            else:
                # It may still be between accepted and assignment; the worker
                # checks this marker before resuming its suspended process.
                self._db.execute("UPDATE jobs SET state='cancelled',finished=?,exit_code=NULL WHERE job_id=? AND state='accepted'",
                                 (time.time(), job_id))
            self._condition.notify_all()
        # Give the worker a bounded opportunity to publish its actual terminal
        # state after TerminateJobObject and pipe draining.
        deadline = time.monotonic() + 5
        with self._condition:
            while True:
                result = self._snapshot(job_id, 0)
                if result["state"] in _TERMINAL or time.monotonic() >= deadline:
                    return result
                self._condition.wait(max(0.0, deadline - time.monotonic()))

    def stop_all(self):
        self.set_paused(True)
        with self._lock:
            ids = [r[0] for r in self._db.execute("SELECT job_id FROM jobs WHERE state IN ('accepted','running')")]
        for job_id in ids:
            self.cancel(job_id)

    def set_paused(self, paused):
        if not isinstance(paused, bool):
            raise ValueError("paused must be bool")
        with self._condition:
            if self._closed and not paused:
                raise RuntimeError("job manager is closed")
            self._paused = bool(paused)
            self._condition.notify_all()

    def close(self):
        with self._condition:
            if self._closed:
                return
            self._paused = True
            self._closed = True
            rows = self._db.execute("SELECT job_id,state FROM jobs WHERE state IN ('accepted','running')").fetchall()
            for job_id, state in rows:
                self._db.execute("UPDATE jobs SET error='cancellation requested' WHERE job_id=?", (job_id,))
                handle = self._jobs.get(job_id)
                if handle:
                    self._terminate_handle(handle)
                elif state == "accepted":
                    self._db.execute("UPDATE jobs SET state='cancelled',finished=? WHERE job_id=?",
                                     (time.time(), job_id))
            self._condition.notify_all()
        deadline = time.monotonic() + 5
        for thread in list(self._threads):
            thread.join(max(0.0, deadline - time.monotonic()))
        with self._lock:
            # Closing any remaining Job Object is the final process-tree kill.
            for handle in list(self._jobs.values()):
                if sys.platform == "win32" and _WIN32:
                    try:
                        win32api.CloseHandle(handle)
                    except Exception:
                        pass
                else:
                    try:
                        handle.wait(timeout=1)
                    except Exception:
                        pass
            self._jobs.clear()
        for thread in list(self._threads):
            thread.join(2)
        with self._lock:
            if not self._threads:
                self._close_db()

    def _run(self, job_id, command, cwd, timeout_s, shell):
        if sys.platform == "win32" and _WIN32:
            return self._run_windows(job_id, command, cwd, timeout_s, shell)
        return self._run_posix(job_id, command, cwd, timeout_s, shell)

    def _run_windows(self, job_id, command, cwd, timeout_s, shell):
        job = process = thread_handle = None
        job_registered = False
        read_out = write_out = null_in = None
        readers = []
        try:
            with self._lock:
                row = self._db.execute("SELECT state,error FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if not row or row[0] != "accepted":
                    return
                self._db.execute("UPDATE jobs SET state='running',started=? WHERE job_id=?", (time.time(), job_id))
                self._condition.notify_all()
            job = win32job.CreateJobObject(None, "MuseBridge-" + job_id)
            info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
            info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
            sa = win32security_attributes()
            read_out, write_out = win32pipe.CreatePipe(sa, 0)
            win32api.SetHandleInformation(read_out, win32con.HANDLE_FLAG_INHERIT, 0)
            si = win32process.STARTUPINFO()
            si.dwFlags |= win32con.STARTF_USESTDHANDLES | win32con.STARTF_USESHOWWINDOW
            si.wShowWindow = win32con.SW_HIDE
            null_in=win32file.CreateFile('NUL',win32con.GENERIC_READ,
                win32con.FILE_SHARE_READ|win32con.FILE_SHARE_WRITE,sa,win32con.OPEN_EXISTING,
                win32con.FILE_ATTRIBUTE_NORMAL,None)
            si.hStdInput = null_in
            si.hStdOutput = write_out
            si.hStdError = write_out
            normalized_shell, executable = resolve_shell(shell)
            command_text = command_line(normalized_shell, executable, command[0])
            environment=os.environ.copy()
            environment['PYTHONIOENCODING']='utf-8'
            environment['PYTHONUTF8']='1'
            process, thread_handle, pid, _ = win32process.CreateProcess(
                executable, command_text, None, None, True,
                win32con.CREATE_SUSPENDED | win32con.CREATE_NO_WINDOW | win32con.CREATE_UNICODE_ENVIRONMENT,
                environment, cwd, si)
            win32api.CloseHandle(write_out); write_out = None
            win32job.AssignProcessToJobObject(job, process)
            with self._condition:
                self._jobs[job_id] = job
                job_registered = True
                cancel = self._db.execute("SELECT error,state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if cancel[1] != "running" or cancel[0] == "cancellation requested":
                    win32job.TerminateJobObject(job, 1)
                else:
                    win32process.ResumeThread(thread_handle)
            reader = threading.Thread(target=self._read_pipe, args=(job_id, read_out), daemon=True)
            reader.start()
            readers.append(reader)
            started = time.monotonic()
            timed_out = False
            while win32event_wait(process, _POLL):
                with self._lock:
                    row = self._db.execute("SELECT error,state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if row and (row[0] == "cancellation requested" or row[1] == "cancelled"):
                    try: win32job.TerminateJobObject(job, 1)
                    except Exception: pass
                    break
                if time.monotonic() - started >= timeout_s:
                    timed_out = True
                    try: win32job.TerminateJobObject(job, 124)
                    except Exception: pass
                    break
            # TerminateJobObject can complete asynchronously. Observe the
            # primary process signal before reading its exit code; if an OS
            # anomaly leaves it active, terminate the suspended/owned process.
            if win32event.WaitForSingleObject(process, 5000) == win32event.WAIT_TIMEOUT:
                try: win32process.TerminateProcess(process, 1)
                except Exception: pass
                signaled = win32event.WaitForSingleObject(process, 5000) == win32event.WAIT_OBJECT_0
            else:
                signaled = True
            # Closing the kill-on-close job also ends children that outlive
            # PowerShell and may otherwise keep inherited pipe writers open.
            self._release_job(job_id, job, job_registered)
            job = None
            job_registered = False
            drain_deadline = time.monotonic() + 3
            for t in readers: t.join(max(0.0, drain_deadline-time.monotonic()))
            if any(t.is_alive() for t in readers):
                with self._lock: self._blocked_output.add(job_id)
                for h in (read_out,):
                    try: win32api.CloseHandle(h)
                    except Exception: pass
                read_out = None
                for t in readers: t.join(0.5)
            exit_code = win32process.GetExitCodeProcess(process) if signaled else None
            with self._condition:
                row = self._db.execute("SELECT error,state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if row[1] == "cancelled" or row[0] == "cancellation requested":
                    state = "cancelled"
                elif timed_out:
                    state = "failed"
                elif not signaled:
                    state = "outcome_unknown"
                else:
                    state = "succeeded" if exit_code == 0 else "failed"
                self._db.execute("UPDATE jobs SET state=?,finished=?,exit_code=?,error=? WHERE job_id=?",
                    (state, time.time(), int(exit_code) if exit_code is not None else None, "timed out" if timed_out else None, job_id))
                self._condition.notify_all()
            self._emit("Local job " + state + " (" + job_id + ")")
        except Exception as exc:
            # If assignment failed, the suspended process is not yet protected
            # by the Job Object. Terminate the primary process explicitly.
            if process:
                try: win32process.TerminateProcess(process, 1)
                except Exception: pass
            with self._condition:
                row = self._db.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if row and row[0] not in _TERMINAL:
                    self._db.execute("UPDATE jobs SET state='failed',finished=?,error=? WHERE job_id=?",
                                     (time.time(), type(exc).__name__ + ": " + str(exc)[:500], job_id))
                self._condition.notify_all()
            self._emit("Local job failed to start or finish (" + job_id + ")")
        finally:
            for t in readers: t.join(timeout=0.5)
            for h in (read_out, write_out, null_in, process, thread_handle):
                if h:
                    try: win32api.CloseHandle(h)
                    except Exception: pass
            if job:
                self._release_job(job_id, job, job_registered)
            with self._lock:
                self._threads.discard(threading.current_thread())
                if self._closed and not self._threads:
                    self._close_db()

    def _run_posix(self, job_id, command, cwd, timeout_s, shell):
        """Run one job on POSIX: bash via Popen in its own session.

        The process group is the kill boundary, mirroring the Windows job
        object: terminating the group ends the shell and its children.
        """
        process = None
        readers = []
        try:
            with self._lock:
                row = self._db.execute("SELECT state,error FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if not row or row[0] != "accepted":
                    return
                self._db.execute("UPDATE jobs SET state='running',started=? WHERE job_id=?", (time.time(), job_id))
                self._condition.notify_all()
            normalized_shell, executable = resolve_shell(shell)
            argv = command_argv(normalized_shell, executable, command[0])
            environment = os.environ.copy()
            environment['PYTHONIOENCODING'] = 'utf-8'
            environment['PYTHONUTF8'] = '1'
            process = subprocess.Popen(
                argv, cwd=cwd, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=True, env=environment)
            with self._condition:
                self._jobs[job_id] = process
                cancel = self._db.execute("SELECT error,state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if cancel[1] != "running" or cancel[0] == "cancellation requested":
                    # No wait here: this runs under the condition lock, and the
                    # poll loop below reaps the process.
                    self._terminate_handle(process, grace_s=0)
            reader = threading.Thread(target=self._read_stream, args=(job_id, process.stdout), daemon=True)
            reader.start()
            readers.append(reader)
            started = time.monotonic()
            timed_out = False
            while process.poll() is None:
                with self._lock:
                    row = self._db.execute("SELECT error,state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if row and (row[0] == "cancellation requested" or row[1] == "cancelled"):
                    self._terminate_handle(process)
                    break
                if time.monotonic() - started >= timeout_s:
                    timed_out = True
                    self._terminate_handle(process)
                    break
                time.sleep(_POLL)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                process.wait(timeout=3)
            for t in readers:
                t.join(timeout=3)
            if any(t.is_alive() for t in readers):
                with self._lock:
                    self._blocked_output.add(job_id)
            exit_code = process.returncode
            signaled = exit_code is not None
            with self._condition:
                row = self._db.execute("SELECT error,state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if row[1] == "cancelled" or row[0] == "cancellation requested":
                    state = "cancelled"
                elif timed_out:
                    state = "failed"
                elif not signaled:
                    state = "outcome_unknown"
                else:
                    state = "succeeded" if exit_code == 0 else "failed"
                self._db.execute("UPDATE jobs SET state=?,finished=?,exit_code=?,error=? WHERE job_id=?",
                    (state, time.time(), int(exit_code) if exit_code is not None else None, "timed out" if timed_out else None, job_id))
                self._condition.notify_all()
            self._emit("Local job " + state + " (" + job_id + ")")
        except Exception as exc:
            if process is not None:
                try:
                    self._terminate_handle(process)
                except Exception:
                    pass
            with self._condition:
                row = self._db.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if row and row[0] not in _TERMINAL:
                    self._db.execute("UPDATE jobs SET state='failed',finished=?,error=? WHERE job_id=?",
                                     (time.time(), type(exc).__name__ + ": " + str(exc)[:500], job_id))
                self._condition.notify_all()
            self._emit("Local job failed to start or finish (" + job_id + ")")
        finally:
            for t in readers:
                t.join(timeout=0.5)
            with self._lock:
                self._jobs.pop(job_id, None)
                self._threads.discard(threading.current_thread())
                if self._closed and not self._threads:
                    self._close_db()

    def _read_stream(self, job_id, stream):
        import codecs
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        try:
            while True:
                data = stream.read1(8192) if hasattr(stream, 'read1') else stream.read(8192)
                if not data:
                    break
                self._append(job_id, decoder.decode(data))
        except (ValueError, OSError):
            pass
        finally:
            self._append(job_id, decoder.decode(b'', final=True))

    def _read_pipe(self, job_id, handle):
        import codecs
        decoder=codecs.getincrementaldecoder('utf-8')(errors='replace')
        while True:
            try:
                _, data = win32file.ReadFile(handle, 8192)
            except Exception:
                break
            if not data: break
            self._append(job_id, decoder.decode(data))
        self._append(job_id,decoder.decode(b'',final=True))


    def _append(self, job_id, text):
        with self._condition:
            if job_id in self._blocked_output or self._db_closed:
                return
            row = self._db.execute(
                "SELECT output,output_base,output_head,output_total FROM jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if not row: return
            out = row[0] + text
            excess = max(0, len(out) - _OUTPUT_LIMIT)
            if excess:
                out = out[excess:]
            head = row[2]
            remaining_head = max(0, _HEAD_LIMIT - len(head))
            if remaining_head:
                head += text[:remaining_head]
            self._db.execute(
                "UPDATE jobs SET output=?,output_base=?,output_head=?,output_total=? WHERE job_id=?",
                (out, row[1] + excess, head, row[3] + len(text), job_id),
            )
            self._condition.notify_all()

    def _snapshot(self, job_id, cursor):
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
            raise ValueError("cursor must be a nonnegative integer")
        row = self._db.execute("SELECT state,exit_code,output,output_base,error FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError(job_id)
        state, exit_code, output, base, error = row
        if cursor > base + len(output):
            raise ValueError('cursor is beyond current output_end; refresh the job status without a cursor')
        truncated = cursor < base
        start = max(cursor, base) - base
        end = min(len(output), start + _PAGE_LIMIT)
        return {"job_id": job_id, "state": state, "exit_code": exit_code,
                "output": output[start:end], "cursor": base + end,
                "output_end": base + len(output),
                "truncated": truncated, "error": error}

    def _release_job(self, job_id, handle, registered):
        with self._lock:
            owned = self._jobs.pop(job_id, None)
            if sys.platform == "win32" and _WIN32:
                # close() may already have closed and removed a registered handle.
                if owned is handle or not registered:
                    try: win32api.CloseHandle(handle)
                    except Exception: pass
            # POSIX: the handle is a Popen reaped by its worker; nothing to close.

    def _close_db(self):
        if not self._db_closed:
            self._db_closed = True
            self._db.close()

    def _emit(self, message):
        try: self.activity(message)
        except Exception: pass


def win32security_attributes():
    import win32security
    sa = win32security.SECURITY_ATTRIBUTES()
    sa.bInheritHandle = True
    return sa


def win32event_wait(handle, seconds):
    return win32event.WaitForSingleObject(handle, max(0, int(seconds * 1000))) == win32con.WAIT_TIMEOUT
