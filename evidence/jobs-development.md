# MB-04 process-job component development — 2026-11-11

## Implemented

`musebridge/jobs.py` provides a thread-safe `JobManager` backed by a SQLite WAL journal with full synchronous commits. Admission is committed before the worker thread starts. A unique caller `request_id` is deduplicated; reuse with changed command, working directory, or timeout is rejected. On manager startup, persisted `accepted` or `running` work is marked `outcome_unknown` and is never replayed.

Commands are PowerShell script strings (or a one-element string argv), passed as one argument to hidden `powershell.exe -NoProfile -NonInteractive -Command ...`. At most four accepted/running jobs are admitted at a time; duplicate IDs resolve before this capacity check. Output is combined from stdout/stderr, retained up to 256 KiB per job, returned in pages of at most 16 KiB, and read with monotonic cursors plus a truncation indicator. `wait` is bounded and wakes on output or terminal state. Timeout, cancellation, admission pause, Stop Work, and close are implemented.

The installed pywin32 APIs were exercised locally. A Windows Job Object is configured with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. MuseBridge creates the primary PowerShell process with `CREATE_SUSPENDED`, assigns it to the Job Object, then resumes its main thread. Descendant processes inherit the Job Object, so cancellation and close terminate the owned process tree. If assignment fails, the still-suspended primary process is explicitly terminated.

## Verification performed

Command: `.venv\Scripts\python.exe -m pytest -q tests/test_jobs.py`

Result: **8 passed** on Windows with the project virtual environment. Tests execute harmless local PowerShell only and cover output, concurrent request-ID deduplication and changed-argument rejection, the four-job admission cap, nonzero exit, timeout, descendant cancellation, output bounds/cursors and paging, pause validation, and startup ambiguity without replay. No live Muse connection or credentials were used.

## Limits and next work

This is the process-job portion of MB-04 only. The bounded file read/write tools, tool registration in the Muse core, conversation-to-job ownership integration, disposable-project edit/run/read acceptance flow, interrupted-write tests, GUI Stop Work controls, and full-harness acceptance remain unimplemented or unverified. The manager is not a sandbox: a PowerShell command runs with the current user's permissions. The caller/orchestrator must enforce working-directory and project-scope rules. `outcome_unknown` means the prior process result cannot be reconstructed after an abrupt manager restart; it is deliberately not retried.

## API behavior notes

- `start(request_id, command, cwd, timeout_s=300)` accepts a single PowerShell script string; the one-element sequence form is equivalent. A multi-element sequence is rejected to prevent silently ignored arguments.
- `status` and `wait` return `job_id`, `state`, `exit_code`, one output page, the absolute end `cursor` for that page, `output_end` for the current retained buffer, `truncated`, and optional `error`.
- `wait` may return early when new output arrives; pass its returned cursor to continue waiting.
- `cancel` waits up to five seconds for a terminal status after terminating the Job Object. `close` pauses admission, cancels jobs, closes remaining Job Objects as a final kill-on-close safeguard, and joins workers before closing SQLite.

## Root integration follow-up — 2026-11-11

Integrated into the functional harness; earlier component-only integration notes above are historical. See [combined implementation and live evidence](HARNESS-2026-11-11.md) and [current running guide](../RUNNING.md). Final full suite: 73 passed, 1 skipped. Live acceptance boundaries remain as recorded in the combined evidence.
