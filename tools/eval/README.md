# Harness evaluation

Run from the repository root with the normal MuseBridge window closed:

```powershell
uv run python -m tools.eval.run_eval --list
uv run python -m tools.eval.run_eval --label v1-baseline
```

The runner locks the existing gadget identity. It creates a disposable project
and a separate Muse chat for each task, sends each request once, waits for
attributed completion, then runs an independent checker. An uncertain send is
recorded and never automatically replayed. Task timeouts are scored failures.
Task 10 verifies an intermediate edit before sending its separate undo request.

All native fixture windows must open on the secondary display without
activation. A missing secondary monitor is a fixture failure. The browser site
binds only to loopback and the browser runs headlessly. Only synthetic fixture
credentials are used. Expected visual/browser codes go to the checker process;
they are absent from the model prompt and the parent process environment.

Seed copies and raw local working state stay under ignored `.local/`.
Sanitized JSON and Markdown score reports go to `evidence/eval/`. Reports retain
missing measurements explicitly; an unavailable byte count is not zero. Tool
payload bytes exclude transport framing. Finished remote replies are separate
from successful independent checks.

Use `--tasks 4` to select a task or `--tasks core` for tasks 1-6 when usage is
limited. Give reruns a distinct label to preserve earlier evidence. Compare two
reports with:

```powershell
uv run python -m tools.eval.compare evidence/eval/first.json evidence/eval/second.json
```

The `v1-baseline` run on 0.6.1 must precede the temporary SDK probes and v2 tool
implementation. See [the upgrade plan](../../HARNESS-UPGRADE-PLAN.md) for the
twelve task definitions, phase gates, and acceptance targets.

For subsequent comparisons use `evidence/eval/v1-baseline-complete-2026-11-12.json`.
It preserves the original run and supplements only the three requests whose
journals proved they were never admitted. Source hashes and replacement decisions
are included; the uncertain browser request was not replayed. The baseline notes
explain the long-command verification timeout and the strict desktop capability
failure. Checker process trees are now terminated on timeout or parent exit.
