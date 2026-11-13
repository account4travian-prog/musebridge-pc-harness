# MuseBridge harness upgrade plan (MB-24 to MB-36)

Written 2026-11-12 by a planning model for **the implementation agent**, at the owner's request. the owner asked for
decisions and a plan, not implementation: nothing under `musebridge/` was changed while writing it.

This file is the detailed backlog for everything after 0.6.0. [MASTERPLAN.md](MASTERPLAN.md) stays
the index and points here. Evidence for every decision is in
[docs/HARNESS-RESEARCH-2026-11-12.md](docs/HARNESS-RESEARCH-2026-11-12.md); finding numbers like
"F3" refer to that file.

Keep this file ASCII only (Codex on Windows can turn other characters into `?`).

---

## 0. How to use this document

1. Read sections 1 to 3 once. They are short.
2. Do the phases in order. Inside a phase, do the items in order unless an item says otherwise.
3. Each item has the same five parts: **Goal**, **Do**, **Files**, **Done when**, **If it goes
   wrong**. "Done when" is the acceptance test; do not mark an item done on anything less.
4. After each item: run the tests, commit, tick the box in section 10, write one line in the log.
5. If something is not covered, use section 8 (the trouble table) before asking the owner.

**You may** change design details when evidence says so. Record what you changed and why in the
section 10 log. **You may not** change the items in section 3.1 (owner rules) without the owner.

**Stop and ask the owner only for:** anything in section 3.1; a probe result that removes a whole
phase's purpose; data loss in the real profile; needing his hands (microphone, pasting the skill
into Muse, the ESP32 board).

**Do not ask the owner for:** permission to proceed to the next item, naming choices, test design,
refactors inside this plan, or reruns.

---

## 1. What you are inheriting (verified 2026-11-12)

| Fact | Detail |
|---|---|
| Version | 0.6.0, Python, PySide6 UI, vendored Muse SDK at `9d8cca15...`. |
| Tests | `.venv\Scripts\python.exe -m pytest -q tests` -> 225 passed, 4 skipped. |
| **App does not start** | `musebridge/jobs.py:125` `finished_summary()` calls `dict(row)` on a plain tuple. Crashes whenever the jobs database has a finished job. Traceback: `.local/runtime-0.6-error.txt`. This is where GPT stopped. |
| No version control | No `.git` at the root. The last session edited 27 files with no rollback. |
| Unfinished closeout | `evidence/FIVE-CAPABILITIES-0.6.md` ends "Final deployed-window observation is appended after the checked build is reopened." It never was. |
| Tool surface | 59 tools, about 26 KB of schema. |
| Pairing | Works. Do not re-pair, reflash, or touch the saved profile. |

Muse is the only reasoning agent. MuseBridge is the tool surface it works through. This plan
improves that surface: fewer and better tools, fewer round trips, better feedback, better
eyes and hands, and a way to measure all of it.

---

## 2. What "better" means: the scoreboard

Every phase is judged against the same numbers, produced by the eval suite built in MB-25.

| Metric | Meaning | Direction |
|---|---|---|
| Task success | Fraction of eval tasks whose independent check passes | up |
| Tool calls per task | Round trips Muse needed | down |
| Failed-call rate | Calls returning `ok:false` / all calls | down |
| Wall time per task | Send to verified result | down |
| Result bytes per task | Total tool-result payload sent to Muse | down |
| Preamble bytes per message | Harness text added to each user message | down |

Targets for 1.0 compared with the 0.6.0 baseline you will record in MB-25: success at least equal
and no task regressed; tool calls per task down 40% or more; failed-call rate under 5%; preamble
under 800 bytes on ordinary messages. If a change does not move a number, say so in the log and
keep or revert it on its merits. Do not claim an improvement you did not measure.

---

## 3. Decisions already made

### 3.1 Owner rules (do not change without the owner)

- Muse stays the only reasoning agent. No local LLM, no second agent loop, no model-provider swap.
- No re-pairing, no reflashing, no duplicate gadget identity. One process owns the identity; live
  probes run with the normal app closed.
- No startup-at-login, no public listener, no telemetry, no publishing, no pushing to a remote.
- Preserve OpenClawGateway and everything outside this project.
- Desktop control never steals input while the owner is using the PC (keep the idle / foreground /
  lock checks). Do not disturb a running game.
- Development helpers: at most two helper-model workers, given disjoint files. The owner's standing model rules (kept in his private notes) bound which models may be used.
- Never print or log credentials, tokens or raw profile paths. Evidence stays sanitized.
- Never run destructive tests against the real profile under `%LOCALAPPDATA%\MuseBridge`. Use
  `MUSEBRIDGE_DATA_DIR` pointing at a temp directory.

### 3.2 Design decisions (mine; change only with evidence, and log it)

| # | Decision | Why (finding) |
|---|---|---|
| D1 | Cut the registered tool set from 59 to about 30, with a core of 8 everyday tools. | F1; Vercel and Anthropic results. |
| D2 | Remove `run_id`, `scope_revision`, `request_id` and `expected_sha256` as things Muse must supply. The harness derives them. They stay accepted if sent. | F2. |
| D3 | `conversation_id` becomes optional: inferred when exactly one conversation has an active run. | F2, F7. |
| D4 | One blocking `shell.run` replaces `job.start` + `job.wait`. It returns output and exit code in the same call and hands back a job handle only if the command outlives the wait cap. | F3. |
| D5 | Files are read and edited by **line**, with line numbers in the output. The harness remembers what Muse last read and refuses edits to files that changed since, instead of asking for a hash. | F4. |
| D6 | Edit matching normalizes line endings and reports the nearest candidate on a miss. Line-anchored editing is built as a second mode and the default is chosen by eval (P-3). | F4, 2.1. |
| D7 | Stable guidance moves into a skill Muse keeps on its VM. The per-message preamble shrinks to a short header, sent on the first message of a chat and when something changed. | F5. |
| D8 | Every result is short by default, truncates head-and-tail with a "how to get the rest" line, and every error carries a `hint` naming the next action. | 2.2, 2.3, F11. |
| D9 | Edits return automatic diagnostics (syntax check). Failed checks are warnings in the result, never a silent pass. | 2.3. |
| D10 | Browser and desktop tools switch to snapshot-with-refs, and every action returns what changed. | F8, F9, 2.4. |
| D11 | Vision is delivered inline in the tool result if probe P-2 passes; otherwise the existing image-turn route stays, with automatic downscaling. | F6. |
| D12 | Checkpoints move to a shadow Git repository per project so shell-made changes are also restorable. | F10. |
| D13 | Several project chats can be live at once (limit 3). The UI's selected chat no longer decides where tool calls go. | F7. |
| D14 | `shell.run` offers `shell: powershell | bash | cmd`. PowerShell 7 is preferred over 5.1 when installed. The default is chosen by eval (P-4), starting as PowerShell. | F13. |
| D15 | Windows built-in OCR may be used as a **perception fallback** for windows with no usable accessibility tree. It is an OS text-recognition API, not a reasoning agent. the owner can veto (section 9). | F8. |
| D16 | Version numbers: 0.6.1 after Phase 0, 0.7.0 after Phase 3, 0.8.0 after Phase 5, 1.0.0 at closeout. Contract version goes 1 -> 2 in Phase 2. | - |
| D17 | Old tool names keep working in the dispatcher for one release (not registered, still routed) so anything Muse remembers does not break. | safety |

---

## 4. The phases

```
Phase 0  Repair and safety net        MB-24          must be first
Phase 1  Measure                      MB-25          baseline + 5 probes
Phase 2  Core tools v2                MB-26..29      biggest capability gain
Phase 3  Context                      MB-30          skill + lean header
Phase 4  Feedback loops               MB-31, MB-32   self-correction
Phase 5  Eyes and hands               MB-33..35      browser, desktop, vision
Phase 6  Parallel chats               MB-36a         background work
Phase 7  Closeout                     MB-36b         1.0 acceptance
```

Phases 5 and 6 are independent of each other and of Phase 4; if you use helper-model workers, that is
where to split. Phases 0 to 3 are strictly sequential.

---

### Phase 0 - Repair and safety net

#### MB-24a Put the project under Git

- **Goal:** every later step can be undone.
- **Do:**
  1. `vendor/muse-gadget-sdk/` is its own Git checkout (it has a `.git`). Do **not** delete that
     and do not commit the SDK into this repository. Add `vendor/muse-gadget-sdk/` to
     `.gitignore` and write `vendor/PINNED.md` recording the upstream URL
     (https://github.com/facebookincubator/muse-gadget-sdk) and commit
     `9d8cca156cdec9ea06efcf854b03dccc5ca76d42`. Confirm with `git -C vendor/muse-gadget-sdk status`
     that the SDK is unmodified; if it is modified, save `git diff` as `vendor/local.patch` and
     commit that.
  2. Confirm `.gitignore` also covers `.venv/`, `.local/`, `__pycache__/`, `.pytest_cache/`,
     `*.egg-info/`, `firmware/serial-ble/.pio/`.
  3. `git init`, commit everything else as `baseline: 0.6.0 as inherited`.
  4. Work on a branch `harness-v2`. Commit after every item. No remote, no push.
- **Files:** `.gitignore`, `vendor/PINNED.md`.
- **Done when:** `git status` is clean and `git log` shows the baseline commit.
- **If it goes wrong:** if Git reports the vendor directory as an embedded repository, you forgot
  the ignore line. Never run `git clean` or `git reset --hard` before the baseline commit exists.

#### MB-24b Fix the launch crash

- **Goal:** the app opens with real data.
- **Do:** in `musebridge/jobs.py` `finished_summary`, build dicts explicitly:
  `[{'job_id': r[0], 'state': r[1], 'finished': r[2]} for r in ...]`. Do not set a global
  `row_factory` on the jobs connection (other code indexes rows by position).
  Then grep the package for other `dict(row)` on connections without `row_factory`
  (`jobs.py` is the only connection without one today; re-check).
- **Add a test** in `tests/test_attention_plugins.py` or `tests/test_jobs.py`: start a trivial job,
  wait for it to finish, call `finished_summary()`, assert it returns a list of dicts with the three
  keys. The existing test only covers the empty list, which is why this shipped.
- **Done when:** the new test fails before the fix and passes after; full suite green.
- **If it goes wrong:** if the app still fails to open, run
  `.venv\Scripts\python.exe -m musebridge.app` (not `pythonw`) from a console to see the traceback.

#### MB-24c Finish the 0.6 closeout

- **Do:** close any running MuseBridge. Launch with `Start-MuseBridge.ps1`. Confirm one window,
  status "Connected", all tabs present (use the same accessibility check the 0.5 receipt
  describes; do not take a foreground screenshot of the owner's desktop). Append the observation to
  `evidence/FIVE-CAPABILITIES-0.6.md`, replacing its last line. Bump version to 0.6.1 in
  `pyproject.toml` and everywhere `grep -rn "0\.6\.0" musebridge` finds a registration version
  (at least `musebridge/core.py:279`).
- **Done when:** the receipt has a dated final observation and the app is left running and
  connected.
- **If it goes wrong:** "Connected" not reached within 60 s -> check the status text. A token
  refresh failure is not a harness bug; record it and continue with offline work. Do not re-pair.

---

### Phase 1 - Measure

#### MB-25a Tool statistics

- **Goal:** know what Muse actually does.
- **Do:** the `tool_calls` table already records tool, state, timestamps and error code
  (`execution.py:44-46`). Add two columns by additive migration: `result_bytes INTEGER`,
  `error_message TEXT` (first 200 characters; never parameters or output). Add
  `tools/harness_stats.py` that prints, for a data directory and optional time range: calls per
  tool, failure rate per tool, top 10 error messages, median and p90 duration per tool, calls per
  run, and preamble bytes per send (log this number from `Core.send`).
- **Files:** `musebridge/execution.py`, `musebridge/tooling.py`, `musebridge/core.py`,
  `tools/harness_stats.py`, a test for the migration.
- **Done when:** running the script against a temp profile after a fixture run prints all six
  tables, and the migration test upgrades a copy of a 0.6 database without loss.
- **Bonus worth five minutes:** run it read-only against the **real** profile's catalog (copy the
  SQLite file to temp first, never open the live file for writing). The existing history will
  show which tools Muse fails on most today. Put the sanitized table in
  `evidence/MB-25-baseline.md`.

#### MB-25b Eval suite

- **Goal:** one command that scores the harness with the real Muse.
- **Do:** create `tools/eval/` following the pattern of `tools/live_workbench_probe.py`
  (app closed, identity lock taken, disposable project and chat, sanitized report).
  - `tools/eval/tasks/NN_name/` each holds `seed/` (files copied into a fresh temp project),
    `prompt.txt` (what is sent to Muse), `check.py` (independent pass/fail that inspects files or
    runs a command; exit 0 = pass), `limits.json` (max minutes, max turns).
  - `tools/eval/run_eval.py [--tasks ...] [--label v1-baseline]` runs each task in a new project
    chat, waits for the run to finish (use the existing `TurnRuns` completion rules), runs
    `check.py`, collects the section 2 metrics from the catalog, and writes
    `evidence/eval/<label>-<date>.json` plus a Markdown table.
  - `tools/eval/compare.py a.json b.json` prints the difference per task and overall.
- **The tasks** (section 7 lists all twelve with their checks). Keep them small: each should take
  a competent agent under five minutes.
- **Done when:** `run_eval.py --label v1-baseline` completes on 0.6.1 and the report is committed.
  This is the number everything else is compared to. **Do not skip it**: without a baseline the
  rest of the plan cannot be shown to work.
- **If it goes wrong:**
  - A task hangs: `limits.json` timeout marks it failed-timeout; the runner must continue.
  - Muse usage runs low: run the six tasks marked "core" in section 7 only.
  - Results vary between runs: that is expected. Compare totals, and rerun any single task whose
    result flipped before drawing a conclusion from it.

#### MB-25c Five probes

Run each once, in a disposable project chat, with a temporary build that registers the probe tool.
Write results to `evidence/MB-25-probes.md` as: question, exact method, observation, decision.

| Probe | Method | If yes | If no |
|---|---|---|---|
| **P-1 long blocking call** | Register `probe.sleep` with spec `timeout_ms: 200000`; it sleeps N seconds and returns a code. Ask Muse to call it with 45, then 150. | `shell.run` inline wait cap = 120 s (max 600 s on request). | Cap = 25 s; `shell.run` returns a job handle sooner. The design is otherwise identical. |
| **P-2 inline image** | Register `probe.picture` returning `{"format":"jpeg-base64","data_base64":<jpeg>}` of a generated image showing a random 6-digit code that appears nowhere in text. Keep the JPEG under 120 KB. Ask Muse what code it sees. Try once more with the tool named `camera.capture` if the first attempt fails (the service may key on the name). | Inline vision: MB-35 uses route A. | MB-35 uses route B (image turn). Record exactly what Muse received. |
| **P-3 edit format** | Seed a CRLF Python file with nested indentation and a tab-indented Makefile. Ask for three specific edits using the current `file.edit`. Count failed edit calls. (Repeat after MB-27 with both new modes.) | - | - This probe produces counts, not yes/no. |
| **P-4 shell fluency** | Give Muse five routine jobs (find files by pattern, count lines, run a Python script, set an environment variable for one command, chain two commands with stop-on-failure). Once telling it the shell is PowerShell, once Git Bash (after MB-26). Count failed commands. | - | - Counts decide the default in D14. |
| **P-5 header memory** | After MB-30: in one chat, send the context header once, then 12 unrelated small requests, then ask something that needs a fact only in the header. | Header on first message only. | Repeat the header every 8 turns. |

P-1 and P-2 must run now. P-3 baseline runs now. P-4 and P-5 run when their items land.

- **If it goes wrong:** if a probe tool does not appear to Muse, the registration probably exceeded
  a limit or the name was rejected: names must match `[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+`.

---

### Phase 2 - Core tools v2

Section 5 is the reference for every tool's parameters and result shape. Build to that table.

Shared rules for all v2 tools (implement once, in the router):

1. **Binding (D2, D3).** Resolve the conversation: explicit `conversation_id` if given; else the
   only conversation with an active run; else error `ambiguous_conversation` listing the
   candidates by title. Resolve the run as that conversation's active run; if none, open a local
   one as today. Use the current policy revision. If `run_id` / `scope_revision` are supplied,
   validate them as today.
2. **Result envelope (D8).** `{"ok": true, "payload": {...}}` with no `execution` or `lifecycle`
   blocks. Keep writing those to the catalog; stop sending them to Muse. Hook output appears in a
   result only when a hook **failed or blocked**.
3. **Size.** Default budget 24,000 bytes per result. Over budget -> keep the head and the tail,
   insert `... [N lines omitted; <how to get them>] ...`.
4. **Errors (D8).** `{"ok": false, "error": {"code": ..., "message": ..., "hint": ...}}`. Section 6
   lists the codes and hint texts. Every `raise ValueError('...')` reachable from a tool gets a
   hint. Messages never include credentials or absolute profile paths.
5. **Repeat detection.** If the same tool with the same arguments fails three times in a row in a
   run, append to the hint: "This exact call has failed 3 times. Change the approach: <tool
   specific suggestion>."
6. **Empty results say so:** "No matches.", "Command produced no output (exit 0)."

#### MB-26 `shell.run` and `shell.job`

- **Goal:** one call per command (F3, F13).
- **Do:**
  - `shell.run(command, cwd?, timeout_s?=120, shell?="powershell", background?=false)`.
    Starts through the existing `JobManager` (keep Job Objects, journal, cancellation), then waits
    in the same call up to `min(timeout_s, cap)` where cap comes from P-1.
    - Finished in time -> `{exit_code, output, duration_s}` (+ `truncated`, `log` handle when cut).
    - Not finished -> `{running: true, job_id, output_so_far, hint: "shell.job wait ..."}`; the
      job keeps running up to `timeout_s`.
    - `background: true` -> return the handle immediately (dev servers, watchers).
  - `shell.job(job_id, action="wait"|"status"|"cancel"|"list", wait_s?)` replaces `job.status`,
    `job.wait`, `job.cancel`. `list` shows this conversation's running jobs.
  - Shells: `powershell` = `pwsh.exe` if on PATH else `powershell.exe`; `bash` = Git Bash
    (`bash.exe` next to `git.exe`, not WSL's `C:\Windows\System32\bash.exe`); `cmd`. Report
    which shell and version in `bridge.context`. For PowerShell, prefix the script with
    `$ProgressPreference='SilentlyContinue'; [Console]::OutputEncoding=[Text.Encoding]::UTF8;` and
    make the process exit code follow the last native command's `$LASTEXITCODE` when non-zero.
    Check what `jobs.py:240-260` does today before changing it; keep stderr merged into output in
    order.
  - Output shaping: strip ANSI escapes; collapse carriage-return progress lines to their final
    state; cap at the result budget keeping head 30% / tail 70% (errors are usually at the end).
  - Register the spec with `timeout_ms` = (cap + 15) * 1000.
  - Pass `invoke_slots=asyncio.Semaphore(12)` when constructing `LinkSession` in `core.py` so
    blocking calls cannot starve `shell.job cancel`.
  - Idempotency: accept an optional `request_id`; when absent generate one. Do not require it.
- **Files:** `musebridge/jobs.py`, `musebridge/tooling.py`, `musebridge/execution.py`
  (effect table), `musebridge/core.py`, new `tests/test_shell_run.py`.
- **Done when:** tests cover: quick command returns output + exit code in one call; non-zero exit
  is `ok: true` with `exit_code` set (a failed command is information, not a tool error);
  command outliving the cap returns a handle and `shell.job wait` completes it; cancel kills a
  grandchild process; 2 MB of output is truncated head/tail under budget; UTF-8 output survives;
  Read only profile refuses `shell.run`; Stop Work cancels a blocked call and returns promptly.
- **If it goes wrong:**
  - Blocking call makes the UI stall: the wait must happen on the SDK's executor thread, never
    the Qt thread or the asyncio loop thread.
  - Exit code always 0 under PowerShell: the wrapper is swallowing `$LASTEXITCODE`; test with
    `cmd /c exit 3`.
  - Git Bash not found: omit `bash` from the description and return error `shell_unavailable`.

#### MB-27 File tools: `file.read`, `file.edit`, `file.write`, `file.list`, `file.search`

- **Goal:** edits that land first time (F4).
- **Do:**
  - `file.read(path, start_line?=1, max_lines?=400)` returns numbered text
    (`   42| code`), `total_lines`, and `more: "file.read start_line=443"` when cut. No base64 and
    no hashes in the result. Binary file -> `{binary: true, size, kind}` with a hint (image ->
    `screen.look`; PDF/DOCX -> `artifact.add` then `artifact.read`). Detect UTF-8, UTF-8 BOM and
    UTF-16 (common for PowerShell-written files) and say which. Lines longer than 2,000
    characters are cut with a marker. Accept project-relative paths with either slash.
  - **Read tracking:** keep, per conversation, `{path: sha256 as last shown to Muse}` in memory
    and in a small table. `file.read`, `file.write` and a successful `file.edit` update it.
  - `file.edit(path, old, new, replace_all?=false)` or `file.edit(path, edits=[{old,new,...}])`,
    applied in order, all-or-nothing.
    - Requires the file to have been read in this conversation and unchanged since
      (`not_read` / `changed_since_read` errors with hints). This replaces `expected_sha256`.
    - Matching: normalize CRLF to LF on both sides, match, write back with the file's original
      line endings and BOM. On zero matches try again ignoring trailing whitespace per line; if
      that matches exactly once, apply it and note `matched_ignoring_trailing_whitespace: true`.
      Still zero -> error `no_match` whose hint shows the closest lines (use `difflib`), with
      line numbers and whitespace made visible. More than one match without `replace_all` ->
      `ambiguous_match` listing the line numbers.
    - Result: a numbered snippet of each changed region with 3 lines of context, plus diagnostics
      from MB-31a. Keep calling the existing checkpoint capture around the write.
  - **Line-anchored mode (built now, default decided by P-3):** `file.edit(path,
    start_line, end_line, new)` replaces that inclusive range; `end_line = start_line - 1`
    inserts. Guarded by the same read tracking, which is what makes line numbers safe.
  - `file.write(path, text, overwrite?=false)` creates parent directories. Overwriting needs
    `overwrite: true` **or** a prior read of the current content. New files: LF, except `.ps1`,
    `.bat`, `.cmd` -> CRLF.
  - `file.list(path?=".", depth?=2)` returns an indented tree, directories first, honoring
    `.gitignore`, capped at 200 entries with counts for what was cut ("node_modules/ (4,112
    files)").
  - `file.search(pattern, path?, glob?, mode?="content"|"files"|"count", context?=0,
    literal?=false, ignore_case?=false, max_results?=50)` using ripgrep regex. Output grouped by
    file with line numbers. Over the cap -> say how many more and suggest a narrower `glob`.
  - Raise the edit size limit from 1 MiB to 4 MiB; keep the path-escape and private-directory
    guards in `workspace.py` exactly as they are.
- **Files:** `musebridge/workspace.py`, `musebridge/tooling.py`, `tests/test_workspace.py`, new
  `tests/test_file_tools_v2.py`.
- **Done when:** tests cover: multi-line `old` typed with LF matches a CRLF file and the file
  stays CRLF; BOM preserved; tab vs space mismatch gives `no_match` with the nearest lines shown;
  two edits in one call both apply or neither; edit without prior read is refused with the hint;
  file changed on disk after read is refused; range edit and insert work; `file.read` on a
  5,000-line file returns 400 lines and the `more` pointer; search respects `.gitignore` and the
  cap. Then rerun probe P-3 with both edit modes and record the counts.
- **Decide the default:** whichever mode had fewer failed edit calls in P-3 is described first in
  the tool description and in the skill. Keep both working.
- **If it goes wrong:**
  - Line numbers drift after an edit: each successful edit must update the tracked hash and the
    result must show new line numbers; tell Muse in the description that line numbers change
    after every edit.
  - ripgrep missing on another machine: keep the Python fallback in `workspace.py`.

#### MB-28 Bookkeeping removal, envelope, errors

- **Goal:** apply the shared rules above to every tool (F2, F11).
- **Do:** implement binding, envelope, size budget, error hints and repeat detection in
  `ToolRouter.call`. Change `registration_specs()` so nothing is promoted to required
  (`tooling.py:150-157`); remove `run_id` and `scope_revision` from registered schemas.
- **Safety check you must keep:** Stop Work still blocks all mutations until Resume; a profile
  change still takes effect on the next call; a call for conversation A can never write to
  conversation B's project. Write a test for each. The old run-revision checks existed to prevent
  those three things; the new binding must prevent them by construction.
- **Done when:** `tests/test_execution.py` and `tests/test_tooling.py` are updated and green; a new
  test proves each of the three safety properties with no `run_id` supplied; no result sent to Muse
  contains `call_id`.
- **If it goes wrong:** if a legitimate existing test encodes "stale run is refused", keep that
  behavior for calls that **do** send a `run_id`, and add the new path beside it.

#### MB-29 Consolidate the surface, add packs

- **Goal:** about 30 registered tools, 8 in everyday use (F1).
- **Do:** register the set in section 5. Mapping from old to new:

| Old tools | New |
|---|---|
| `job.start`, `job.wait`, `job.status`, `job.cancel` | `shell.run`, `shell.job` |
| `workspace.list`, `workspace.search` | `file.list`, `file.search` |
| `git.status`, `git.diff`, `git.branches`, `git.worktrees` | removed; Muse runs `git` through `shell.run`. Keep `git.worktree.create`. |
| `bridge.capabilities`, `run.list`, `run.calls`, `hook.status`, `vision.status`, `project.notes`, `project.handoff` | folded into `bridge.context` (sections on request) or owner-UI only |
| `task.create`, `task.update`, `task.list` | `task.set` (create or update, one or many); list comes back in every `task.*` result and in `bridge.context` |
| `task.check_files`, `task.verify_job` | `task.verify` (runs the check command itself, binds the receipt; one call) |
| `change.list`, `change.diff` | `change.review` |
| `artifact.list`, `artifact.read` | `artifact.read` (no id = list) |
| `mcp.servers`, `mcp.tools`, `mcp.read`, `mcp.call`, `mcp.close` | `mcp.list`, `mcp.call` (the owner's grant decides read vs write) |
| `terminal.start`, `.write`, `.read`, `.resize`, `.stop`, `.list` | `terminal.open`, `terminal.send` (returns the output that follows), `terminal.read`, `terminal.close` |

  - **Packs**, chosen in Settings and applied at connect: `core` (always), `browser`, `desktop`,
    `terminal`, `mcp` (only if a server is configured). Default: core + browser + terminal.
    The desktop pack registers when any project uses the Desktop control profile.
  - **Descriptions:** rewrite every one to the template: what it does in one sentence; when to use
    it; when to use something else instead; one example call; what comes back. Put safety caveats
    in the skill, not in every description. Target 40 to 120 words each.
  - Keep old names routed in `_dispatch` for this release (D17), mapped onto the new code.
  - Contract version -> 2. Update `docs/WINDOWS-WORKFLOW-CONTRACT.md` to the v2 surface.
- **Done when:** a test asserts the registered count per pack combination and that every
  description has an example; old names still dispatch; full suite green; **eval rerun
  (`--label v2-core`) shows success not lower than baseline and tool calls per task lower.**
  Bump to 0.7.0 only after Phase 3.
- **If it goes wrong:**
  - Success drops on a task: read that run's tool calls with `harness_stats.py`. The usual cause
    is a description that no longer says when to use the tool. Fix the description first; restore
    a removed tool only if two description fixes do not work.
  - `task.verify` semantics: it must keep the existing receipt rules (inputs captured before the
    check runs, later edits invalidate). Reuse `tasks.prepare_job` / `verify_job`; only the
    number of calls changes.

---

### Phase 3 - Context

#### MB-30 Skill, lean header, environment snapshot

- **Goal:** Muse knows this PC before it starts, without a JSON blob on every message (F5).
- **Do:**
  1. **Skill.** Write `docs/muse-skill/gadget-musebridge-windows-pc/SKILL.md` in the format of
     `vendor/muse-gadget-sdk/skills/*/SKILL.md` (YAML `name`, `description`, then Markdown).
     Under 1,200 words. Contents: what this device is; the 8 core tools and the normal loop
     (context -> search/read -> edit -> run the check -> report); Windows facts that trip models
     (paths, PowerShell vs bash differences, CRLF, no `sudo`, `py`/`python`, quoting); how to
     verify before saying done; what needs the owner (profiles, Stop Work, desktop grant); how to
     look at the screen; what never to do (credentials, outside the project). Write it from what
     the eval runs showed Muse getting wrong, not from imagination.
  2. **Install path.** Add Application -> "Teach Muse this PC": it sends a normal chat message
     asking Muse to call `bridge.context(section="skill")`, save the returned text as a skill
     named `gadget-musebridge-windows-pc` on its VM, and confirm. the owner clicks it once per skill
     version. `bridge.context` reports `skill_version`; the header mentions a newer version only
     when one exists.
  3. **Header.** Replace the preamble in `Core.send` (`core.py:189-214`) with at most 8 lines of
     plain text, for example:
     ```
     [MuseBridge] Windows 11 PC, project "cardx" at C:\...\cardx (git: main, 3 modified).
     Profile: Project work. Shell: PowerShell 7.5 (bash available).
     Conversation: 7c1e... Tools: file.*, shell.*, plan/task, browser.*. Skill: gadget-musebridge-windows-pc v2.
     Call bridge.context for project instructions, plan and tasks.
     ```
     Send it on the first message of a chat per connection, when project/profile/pause state
     changed, and every N turns per probe P-5. Otherwise send the user's text unchanged.
     Handoff seeds, attachment lists and visual-feedback notes keep their existing behavior but as
     short text lines, not JSON.
  4. **`bridge.context(section?)`** returns Markdown, not JSON. Default section: project name and
     root, profile, shell versions, tools on PATH (`python`, `py`, `uv`, `node`, `npm`, `git`,
     `rg`, `dotnet`, `cargo`: name and version, probed once per connection and cached), a depth-2
     tree, git branch and status summary, `AGENTS.md` (first 6,000 characters, with a pointer to
     read the rest), goal, plan, open tasks, effective notes. Sections on request: `tasks`,
     `notes`, `history` (recent runs), `tools` (full schemas), `skill`.
- **Done when:** preamble bytes on an ordinary message are under 800 (measured by MB-25a); a
  first-message send in a test contains the header and the second does not; `bridge.context`
  output for the fixture project is under 12,000 bytes; the owner has clicked "Teach Muse this PC"
  once and Muse confirmed (owner step, section 9); probe P-5 done; **eval rerun
  (`--label v2-context`) recorded.** Bump to 0.7.0.
- **If it goes wrong:**
  - Muse stops calling `bridge.context` and misses project instructions: put the first 1,500
    characters of `AGENTS.md` in the first-message header.
  - Muse cannot save skills itself: fall back to the SDK's documented route - the owner pastes the
    `SKILL.md` text into the chat. Keep the button; change what it sends.
  - Tasks in the eval that depended on the old JSON context fail: the missing fact belongs in
    `bridge.context` or the skill; add it there, not back into every message.

---

### Phase 4 - Feedback loops

#### MB-31a Diagnostics on edit (D9)

- **Do:** after `file.edit` / `file.write`, run a fast in-process syntax check by extension and add
  `diagnostics` to the result when there is a problem: `.py` (`ast.parse`), `.json`, `.toml`
  (`tomllib`), `.ps1` (PowerShell parser via a short `pwsh -Command` only if under 300 ms; skip
  otherwise), `.yaml` if PyYAML is present, `.xml`/`.html` well-formedness for `.xml` only.
  Budget 500 ms total; on timeout say nothing. Format: `line 42: unexpected indent`.
  Do **not** reject the edit: report it. (SWE-agent rejects; we cannot be sure a multi-step edit
  is not half way through, so we warn loudly instead: "The file does not parse after this edit.")
- **Also:** if the project has `check_on_edit` set in its settings (new optional field: a command
  and a glob), run it with a 10 s cap and include the last 20 lines when it fails. This is the
  existing after-edit hook made visible in the result.
- **Done when:** tests show a broken Python edit returns the diagnostic and a good one returns
  none; timing stays under budget on a 4 MiB file.

#### MB-31b Finish-line check

- **Do:** track per run: files changed, and whether any `shell.run` / `task.verify` succeeded
  after the last change. Expose it in `bridge.context` as `unverified_changes`. When a run ends
  with unverified changes **and** the project has a `check_command`, and the owner has enabled
  "Auto-check after changes" for the project (off by default), queue one continuation message
  through the existing request queue: "You changed N files and no check has run since. Run
  `<check_command>` and fix what fails, or say why it does not apply." Maximum one automatic
  continuation per user request; respects Stop Work, queue holds and the five-round goal limit.
- **Done when:** fixture test shows exactly one continuation is queued, none when a check ran,
  none when paused; the Changes view shows "not checked since last edit".

#### MB-31c Repeat and stall hints

- **Do:** the three-identical-failures rule from the shared rules, plus: after 15 tool calls in a
  run with no file change, no successful command and no plan update, add a one-time hint to the
  next result: "15 calls without progress. Consider updating the plan or telling the user what is
  blocking." Hints are text in results; they never block a call.

#### MB-32 Shadow-Git checkpoints (D12)

- **Goal:** anything Muse changes in a project, by any tool, can be reviewed and restored (F10).
- **Do:** per project, keep a bare repository under the data directory
  (`<data>/shadow/<project-id>.git`) and operate it with `GIT_DIR` + `GIT_WORK_TREE=<root>`, so
  the project's own `.git` is never touched. Honor the project's `.gitignore` plus a built-in
  exclude list (`node_modules`, `.venv`, build outputs, files over 8 MiB). Snapshot at run start
  and at run end (and before `change.restore`). `change.review(run?)` lists changed files and
  diffs between snapshots; `change.restore(path?|run?)` restores files from the run-start
  snapshot, refusing when the current file differs from the run-end snapshot (the same
  "later owner edit" rule as today). Keep the existing per-edit checkpoint store working for
  finer-grained restore of file-tool edits.
- **Limits to state in the tool description:** restores files only; does not undo installs,
  network calls, deleted databases or anything outside the project folder.
- **Done when:** tests on a temp project: a file changed by `shell.run` shows in `change.review`
  and restores; an owner edit after the run blocks restore; the project's real `.git` index and
  HEAD are byte-identical before and after; a 20,000-file tree snapshots in under 5 s on the
  second run; a project without Git installed degrades to the old behavior with a clear note.
- **If it goes wrong:** snapshot too slow on a huge tree -> snapshot only at run start/end, never
  per call, and skip when the tree has over 100,000 tracked candidates (say so in
  `bridge.context`).

---

### Phase 5 - Eyes and hands

#### MB-33 Browser v2 (D10)

- **Do:** tools `browser.navigate(url)`, `browser.snapshot(filter?)`, `browser.act(ref, action,
  text?/key?/value?)` with actions `click`, `fill`, `type`, `press`, `select`, `hover`, `scroll`,
  `wait_for` (text or seconds), `back`; `browser.close`. Use Playwright's accessibility/ARIA
  snapshot to produce lines like `[e12] button "Sign in"`, refs valid until the next snapshot.
  `navigate` and every `act` return: title, URL, and the snapshot **diff** (added / removed /
  changed lines), or the full snapshot when the page changed or the diff is over half the page.
  Cap snapshots at the result budget; `filter` narrows by text or role. Stale ref ->
  `stale_ref` error with the hint to snapshot again. Add `browser.eval(js)` returning JSON under
  8 KB, in the browser pack, allowed only in Project work or higher.
  Keep the isolated headless context. A signed-in browser profile is **not** in scope.
- **Reference:** https://playwright.dev/mcp/snapshots for the format and behavior to copy.
- **Done when:** tests against the existing local HTTP fixtures: log in to a fixture form using
  refs only; stale ref is refused; diff after a click shows only the changed region; a 5,000-node
  page stays under budget with a `filter` hint. Eval task 9 passes.

#### MB-34 Desktop v2 (D10, D15)

- **Do:** tools `desktop.windows()` (titles, process, handle, focused, minimized),
  `desktop.launch(app_or_path, args?)` (returns the new window; uses `ShellExecute`; refuses
  elevation), `desktop.snapshot(window, filter?)`, `desktop.act(window, ref?|x,y?, action, ...)`.
  - Snapshot: flatten the UIA control view to interactive and text elements:
    `[e7] Button "Save" enabled`, `[e9] Edit "File name" value="report.txt"`, max 150 elements,
    refs valid until the next snapshot of that window. Include the window's focused element.
    When the tree has fewer than 5 useful elements and D15 is allowed, add OCR lines:
    `[t3] text "Export" at 412,218` (Windows.Media.Ocr through the `winrt` packages).
  - Actions: `click`, `double_click`, `right_click`, `type` (text), `keys` (chords like
    `ctrl+s`, `alt+f4` is refused), `set_text`, `select`, `toggle`, `expand`, `scroll`, `focus`,
    `wait_for` (element text appears, max 10 s). Prefer UIA patterns (Invoke, Value, Toggle,
    SelectionItem, ExpandCollapse, Scroll). Fall back to synthesized input at the element's
    center only when no pattern applies, and only after the existing idle / foreground / lock /
    elevation checks pass.
  - Every action returns the snapshot diff of that window (and notes a new top-level window or
    dialog if one appeared).
  - Keep everything behind the Desktop control profile. Keep refusing while the owner is active;
    the error hint says "The owner is using the PC; try again when idle or ask them."
- **Reference to read first:** https://github.com/CursorTouch/Windows-MCP (state capture and
  input approach) and the UFO2 paper for the hybrid idea. Do not copy code without checking its
  license terms against this project; reimplement on pywinauto/comtypes already in use.
- **Done when:** tests against owned fixture windows (a small PySide6 form launched by the test,
  as `tools/window_capture_smoke.py` does): click a button by ref and see the label change in the
  diff; type into an edit; send `ctrl+a`; `launch` Notepad equivalent fixture; stale ref refused;
  input refused when the fixture simulates recent user activity. Then one live owner-observed
  task with real Notepad (section 9). Eval task 11 passes.
- **If it goes wrong:**
  - `winrt` OCR packages will not install on this Python: ship without OCR; the tool says "no
    readable text; use screen.look".
  - Synthesized clicks land on the wrong monitor/scale: use physical pixel coordinates from UIA
    `BoundingRectangle` and make the process per-monitor DPI aware; test at 125% and 150%.

#### MB-35 `screen.look` (D11)

- **Do:** one tool replaces `vision.observe`, `desktop.screenshot`, `browser.screenshot`:
  `screen.look(source="browser"|"window"|"file", window?, path?, marks?=true, region?)`.
  - Always downscale and re-encode to fit: longest side 1,568 px to start, JPEG quality stepped
    down until it fits the route's limit. (Today oversize screenshots are refused; machine
    captures should be resized, user-chosen attachments still are not.)
  - `marks: true` draws the snapshot's ref labels on the image (set-of-marks) so Muse can connect
    what it sees to `e12` refs.
  - **Route A (P-2 passed):** return the image in the result in the shape that worked in P-2,
    under 120 KB. Muse sees it in the same turn.
  - **Route B (P-2 failed):** keep the existing image-turn machinery (`vision.py`): the result
    says "The image will arrive as your next message; end this turn now." Keep the six-image
    chain limit and all its Stop/hold rules.
  - Keep the owner grants exactly as they are: browser capture of the owned context is allowed
    in Project work; window capture needs Desktop control and a locally allowed window.
    `source="file"` accepts image files inside the project.
- **Done when:** a live pixel-only random-code check passes through whichever route is active;
  a 4K window capture fits the limit; marks are legible at the downscaled size; grants are
  enforced in tests. Bump to 0.8.0 and rerun the eval (`--label v2-eyes`).

---

### Phase 6 - Parallel chats

#### MB-36a Decouple routing from the selected chat (D13)

- **Goal:** Muse keeps working in project A while the owner reads or types in chat B (F7).
- **Do:**
  - `Core` keeps a set of **live conversations** (max 3 project chats + main), each with its own
    subscription task and `Transcript`. Selecting a chat in the UI only changes what is shown.
  - Remove the `expected_conversation_id=self.conversation_id` check (`core.py:76`); the router's
    binding rule from Phase 2 decides. `select_conversation` no longer stops the previous run.
  - A conversation becomes live when a message is sent in it and stops being live when its run
    settles and it is not selected (keep the subscription for 60 s afterward to catch late
    events).
  - `send()` takes the conversation explicitly and no longer fails with "Conversation changed
    before send".
  - With two active runs, calls without `conversation_id` get `ambiguous_conversation`; the
    header for each chat therefore includes its ID and says to pass it. Two chats in the **same
    project** may both read; the second one to write gets `project_busy` unless it is in its own
    worktree (reuse `git.worktree.create`).
  - Overview already lists runs across chats; add a per-chat "working" indicator in the sidebar.
- **Check the SDK first:** confirm `LinkSession.subscribe_chat` can be iterated for several chat
  IDs on one session at once (read `link_client.py:544-600`). If only one stream is possible,
  subscribe to the main stream and filter by `session_id` if events carry it; if neither works,
  stop this item, record why in the log, and leave single-chat behavior. Do not open a second
  session with the same identity.
- **Done when:** fake-SDK tests: two chats run tools concurrently into their own projects with no
  cross-writes; switching the UI selection mid-run does not interrupt it; a call with the wrong
  `conversation_id` is refused; Stop Work stops both. One live check with two real chats.
- **If it goes wrong:** this is the riskiest refactor in the plan. Do it on its own branch off
  `harness-v2`, merge only when the whole suite and the eval are green. If it is not stable in
  two attempts, park it and proceed to closeout: it is valuable, not essential.

---

### Phase 7 - Closeout

#### MB-36b Acceptance for 1.0

1. Full suite green; `uv sync --locked` clean; no test uses the real profile.
2. Final eval run (`--label v1.0`) and `compare.py` against the baseline. Put the table in
   `evidence/HARNESS-1.0.md` with: per-phase eval results, probe outcomes, every design decision
   you changed and why, known gaps.
3. Update `README.md`, `RUNNING.md`, `docs/WINDOWS-WORKFLOW-CONTRACT.md`, `docs/UI-DESIGN.md`,
   the skill (bump its version), and the MB-24..36 rows in `MASTERPLAN.md`.
4. Remove the old-name dispatch aliases (D17) only if `harness_stats.py` shows zero calls to
   them since 0.7.0; otherwise leave them and say so.
5. Leave the app running and connected. Tell the owner what he needs to do from section 9.

---

## 5. Tool contract v2 (reference)

`conversation_id` is accepted by every tool and omitted below. `?` = optional.

**Core pack (always registered)**

| Tool | Parameters | Returns |
|---|---|---|
| `bridge.context` | `section?` | Markdown: project, environment, instructions, plan, tasks, notes. |
| `shell.run` | `command, cwd?, timeout_s?, shell?, background?` | `exit_code, output, duration_s` or `running, job_id, output_so_far`. |
| `shell.job` | `job_id?, action, wait_s?` | Same shape as `shell.run`; `list` returns running jobs. |
| `file.read` | `path, start_line?, max_lines?` | Numbered lines, `total_lines`, `more`. |
| `file.edit` | `path` + (`old, new, replace_all?`) or `edits[]` or (`start_line, end_line, new`) | Numbered snippet of changed regions, `diagnostics?`. |
| `file.write` | `path, text, overwrite?` | `lines`, `diagnostics?`. |
| `file.list` | `path?, depth?` | Indented tree with counts. |
| `file.search` | `pattern, path?, glob?, mode?, context?, literal?, ignore_case?, max_results?` | Matches grouped by file. |
| `plan.update` | `plan?, goal?, state?` | Current plan and task summary. |
| `task.set` | `tasks[]` (`id?`, `title`, `criteria?`, `depends_on?`, `state?`, `check?`) | Full task list. |
| `task.verify` | `task_id, command?, paths?` | Check output tail, receipt, task list. |
| `project.note` | `kind, text, source?, supersedes?` | Note ID. |
| `conversation.handoff` | `title?` | New chat title; owner must select it. |
| `change.review` | `run?, path?` | Changed files and diffs. |
| `change.restore` | `path?` or `run?` | What was restored or why not. |
| `artifact.read` | `artifact_id?, offset?, length?` | List, or a page of text. |
| `artifact.add` | `path` | Artifact ID. |
| `screen.look` | `source, window?, path?, marks?, region?` | Image (route A) or "arrives next turn" (route B). |
| `git.worktree.create` | `branch, base?` | New project and chat names. |
| `bridge.health` | - | SDK-compatible health. |

**Browser pack:** `browser.navigate`, `browser.snapshot`, `browser.act`, `browser.eval`, `browser.close`.
**Desktop pack:** `desktop.windows`, `desktop.launch`, `desktop.snapshot`, `desktop.act`.
**Terminal pack:** `terminal.open`, `terminal.send`, `terminal.read`, `terminal.close`.
**MCP pack:** `mcp.list`, `mcp.call`.

Total 20 core + 5 + 4 + 4 + 2 = 35 at most; 29 with the default packs. If the eval shows Muse
never uses a core tool across all tasks, move it to a pack or remove it.

**Description template** (every tool):

```
<What it does, one sentence.> Use it when <situation>. For <neighbouring need> use <other tool>.
Example: <tool>(<realistic arguments>). Returns <shape in a few words>.
```

---

## 6. Error catalogue

Every error is `{code, message, hint}`. Start with these; add as real failures appear in
`harness_stats.py`.

| Code | When | Hint text (adapt wording, keep the action) |
|---|---|---|
| `ambiguous_conversation` | Two active runs and no ID | "Pass conversation_id. Active: <title A> (<id>), <title B> (<id>)." |
| `no_project` | Tool needs a project | "This chat has no project. Ask the user to choose one in MuseBridge." |
| `read_only` | Profile forbids it | "The owner set Read only. Explain what you would change and ask them to switch to Project work." |
| `paused` | Stop Work active | "The owner pressed Stop Work. Stop and report where you got to." |
| `needs_desktop_control` | Desktop action without the profile | "Ask the owner to enable Desktop control for this project." |
| `owner_active` | Desktop action while the PC is in use | "The owner is using the PC. Wait and retry once, or ask them." |
| `not_read` | Edit before read | "Read the file first: file.read path=<p>." |
| `changed_since_read` | File changed on disk | "The file changed since you read it. Read it again: file.read path=<p> start_line=<n>." |
| `no_match` | `old` not found | "Closest text is at lines A-B: <snippet with visible whitespace>. Copy it exactly, or use start_line/end_line." |
| `ambiguous_match` | `old` found N times | "Found at lines A, B, C. Add surrounding lines to old, or set replace_all." |
| `path_outside_project` | Escape | "Paths are relative to <root>. Use shell.run for anything outside, if the profile allows." |
| `too_large` | Result or file over a limit | "Narrow it: <specific parameter to use>." |
| `stale_ref` | Old snapshot ref | "Take a new snapshot; refs change after the page or window changes." |
| `shell_unavailable` | Requested shell missing | "Available shells: <list>." |
| `timeout` | Tool-level timeout | "Still running as job <id>. Use shell.job wait, or cancel." |
| `outcome_unknown` | Interrupted mutation | "It may or may not have happened. Check the result (file.read / shell.run) before retrying." |
| `bad_arguments` | Missing or wrong type | "Expected: <tool>(<example>)." |

---

## 7. Eval tasks

Each is a directory under `tools/eval/tasks/`. "Core" tasks form the short suite.

| # | Core | Task given to Muse | Independent check |
|---|---|---|---|
| 1 | yes | Fix the failing unit test in a 6-file Python package (bug is an off-by-one). | `pytest` exits 0; test file unchanged (hash). |
| 2 | yes | Rename a function used in 5 files of a CRLF codebase. | Old name absent, new name present, all files still CRLF, `python -m compileall` passes. |
| 3 | yes | Add a CLI flag to a script and document it in the README. | Running the script with the flag prints the expected line; README mentions the flag. |
| 4 | yes | Find which of 300 log files contains a given error code and report the timestamp. | Answer written to `answer.txt` equals the planted value. |
| 5 | yes | Edit a tab-indented Makefile target and a nested YAML value. | Tabs preserved; YAML parses and has the new value. |
| 6 | yes | A PowerShell script fails on paths with spaces; fix it and prove it. | Script run against the seeded "My Files" directory exits 0 and produces the output file. |
| 7 | | Create a small project from nothing: package, one module, tests, passing. | `pytest` exits 0 with at least 3 tests. |
| 8 | | Long command: run a seeded 70-second test script and report the summary line. | `answer.txt` equals the script's final line. |
| 9 | | Browser: on the local fixture site, log in, change a setting, read back the confirmation code. | Fixture server recorded the setting; `answer.txt` equals the code. |
| 10 | | Make a mistake on purpose recoverable: prompt asks for a change, then says "undo that". | Files byte-identical to the seed. |
| 11 | | Desktop: in the fixture form app, fill three fields and press Save. | The fixture wrote the expected JSON file. |
| 12 | | Vision: a fixture window shows a code only as pixels; report it. | `answer.txt` equals the code. |

Tasks 9, 11 and 12 will fail or be awkward on the baseline. That is the point: record it.

---

## 8. When things go wrong

| Situation | What to do |
|---|---|
| Tests fail after your change | Fix or revert before moving on. Never mark an item done with a red suite. |
| A test fails that you did not touch | Run it alone and on the baseline commit. If it fails there too, note it as pre-existing in the log and continue. |
| The app will not connect | Read the status line. `Connection unavailable (X)` is network/service: wait and continue offline work. "Pairing revoked" or "invalid": **stop and tell the owner**. Never delete profile files. |
| Two profiles found at startup | Set `MUSEBRIDGE_DATA_DIR` as `RUNNING.md` describes. Do not merge or delete. |
| A live probe or eval sends something and the result is unclear | Treat it as unknown. Inspect the disposable project and the journal. Do not resend automatically. |
| Muse usage or your own usage is running out | Commit. Write the exact next step in the section 10 log. Stop cleanly at an item boundary. |
| Registration seems rejected (no tools visible to Muse) | Check total size under 256 KiB and names match the pattern in MB-25c. Roll back to the previous registered set to confirm. |
| A decision in 3.2 looks wrong in practice | Follow the evidence, log the change with the numbers. |
| You need something from section 3.1 changed | Ask the owner; meanwhile do the items that do not depend on it. |
| Schema migration needed | Additive only (`ALTER TABLE ADD COLUMN`, new tables), inside `BEGIN IMMEDIATE`, bump `user_version`, test on a copy of a 0.6 database. Follow `execution.py:31-49`. |
| Code style | The existing code packs statements with `;`. New and touched code: one statement per line, short functions, a docstring on public functions. Do not reformat files you are not changing. |
| Delegating to helper models | Disjoint files only; you own `tooling.py`, `core.py`, `execution.py`, migrations and the contract. Review and run tests on everything returned. |

---

## 9. What needs the owner

Nothing here blocks Phases 0 to 2. Collect these and ask once.

| When | What | Why |
|---|---|---|
| After MB-30 | Click Application -> "Teach Muse this PC" and confirm Muse saved the skill. | Skills live on Muse's VM; only a chat message can put one there. |
| Before MB-34 OCR | Say no if he does not want Windows OCR used for unreadable windows (D15). Default is to use it. | It is the one place a recognition component is added. |
| After MB-34 | Watch one real desktop task (e.g. Notepad: type, save to the project folder). | Carries MB-06 acceptance. |
| Any time | One spoken request with tool use; mute, interrupt, hang up. | Carries MB-05 acceptance; needs his voice. |
| After 1.0 | Use it for a normal day and report friction. | Carries MB-07 acceptance. |
| Optional | Set a `check_command` per project and enable "Auto-check after changes". | Turns on MB-31b. |

---

## 10. Progress

Tick the box, add the commit hash, and write one line per item in the log.

- [x] MB-24a Git baseline (`14f527d`)
- [x] MB-24b Launch crash fixed + test (`42d208d`)
- [x] MB-24c 0.6 closeout, 0.6.1 (`98bb283`)
- [x] MB-25a Tool statistics (`20839ed`)
- [x] MB-25b Eval suite + `v1-baseline` run (`45cd986`)
- [x] MB-25c Probes P-1, P-2, P-3 baseline (`4e26b31`)
- [x] MB-26 `shell.run` / `shell.job` (+ P-4) (`a2544f6`)
- [x] MB-27 File tools v2 (+ P-3 rerun, default chosen)
- [x] MB-28 Binding, envelope, errors
- [x] MB-29 Consolidated surface + packs, eval `v2-core`
- [x] MB-30 Skill, header, `bridge.context` (+ P-5), eval `v2-context`, 0.7.0
- [x] MB-31a Edit diagnostics
- [x] MB-31b Finish-line check
- [x] MB-31c Repeat and stall hints
- [ ] MB-32 Shadow-Git checkpoints
- [ ] MB-33 Browser v2
- [ ] MB-34 Desktop v2
- [ ] MB-35 `screen.look`, eval `v2-eyes`, 0.8.0
- [ ] MB-36a Parallel chats
- [ ] MB-36b Closeout, eval `v1.0`, 1.0.0

Current checkpoint (2026-11-13): MB-30 is complete. Owner confirmed the VM
skill; P-5 passed with a 527-byte first header and zero added bytes on thirteen
follow-ups. Frozen v2-context passed 12/12: 139 calls, 4 failed calls, 81,159 result
bytes, 6,543 preamble bytes over 14 sends, 771.893 seconds. Local suite: 500 passed,
5 skipped. Version 0.7.0 is installed; contract remains 2. See
`evidence/MB-30-context.md` and `docs/REMAINING-WORK-2026-11-13.md`.
MB-31a and MB-31b are complete: syntax feedback, optional after-edit checks,
revision-bound unchecked-change feedback and one opt-in queued finish-line prompt.
Latest suite and limits: `evidence/MB-31b-finish-line.md`. Begin MB-31c. Keep phases/items in order. Main command execution works, while
shared-Main request completion remains unattributed; automatic continuation
uses dedicated project chats.

### Log

| Date | Item | Commit | Result / numbers / deviations |
|---|---|---|---|
| 2026-11-13 | MB-30 implementation and Main repair | cb2ba6c | Four-line acknowledged header; ordinary follow-ups unchanged; bounded Markdown context and environment cache; VM skill and Teach/confirmation dialog; same-chat workspace binding. Owner-selected PC-Workspace created and attached to Main without changing chat IDs. Main hostname command independently verified exit 0; fresh unbound chat receives readable no_project with no job. Shared Main events omit request linkage, so automatic completion remains unverified. Full suite 499 passed, 5 skipped; locked sync clean. Owner Teach, P-5 and v2-context remain required. |
| 2026-11-12 | Plan written | - | Baseline measured: 225 passed / 4 skipped; app crashes on launch (jobs.py:125); 59 tools, 25,867 schema bytes. |

| 2026-11-12 | MB-24a | 14f527d | Git baseline preserves inherited 0.6.0, including the original launch defect. SDK pinned/clean and ignored; private state excluded. Per the owner's latest request, repair was made first in the working tree, then the inherited source/test blobs were preserved in the baseline index without reverting the repaired files. Local branch harness-v2; no remote. |
| 2026-11-12 | MB-24b | 42d208d | Completed-job regression reproduced the tuple/dict ValueError before the fix and passed after. All other dict(row) uses have Row factories. Full suite: 226 passed, 4 skipped. |
| 2026-11-12 | MB-24c | 98bb283 | 0.6.1 installed with locked offline sync. One native window observed Connected with all nine tabs; final 0.6 receipt completed. Launcher opens offline by design; documented --connect CLI used for the connection check after accessibility clicking lacked geometry. App left connected at this boundary. |
| 2026-11-12 | MB-25a | 20839ed | Additive schema v2 records result size and sanitized errors; send.context records numeric preamble bytes. Six read-only statistics tables plus byte coverage verified, including a copied legacy database migration. Full suite 234 passed, 4 skipped before one additional passing telemetry test. Sanitized historical survey is in evidence/MB-25-baseline.md; it is not the controlled eval baseline. |
| 2026-11-12 | MB-25b checkpoint | uncommitted | Two helper-model workers have eval tasks and runner in progress under tools/eval with disjoint test files. Added locked PyYAML dev dependency for the independent YAML checker. Computer Use reported owner physical Escape while selecting the app; no app close or live eval attempted. Resume by reviewing worker files/tests, preserving log seeds despite *.log ignore, then close the normal app before taking its identity lock. Baseline and P-1/P-2/P-3 remain required before Phase 2. |
| 2026-11-12 | MB-25b worker handoff | uncommitted | Workers stopped. Tasks 01-12 are saved but unvalidated; test_eval_tasks.py still needed. Only fixture_app.py infrastructure is saved; run_eval.py, compare.py and test_eval_runner.py still needed. Fix missing local Qt import in _PixelCode.paintEvent before running fixtures. No MB-25b tests or live sends ran. Preserve task 02 CRLF bytes and all 300 task 04 log seeds in Git. |
| 2026-11-12 | Owner display instruction | this commit | the owner resumed work with all visible development on the secondary monitor. Added --secondary-screen placement before show, with explicit refusal when unavailable. Native restart showed one offline window with nine tabs; normal exit saved geometry verified wholly inside the non-primary screen. App is now closed for identity-locked evaluations. Test windows must use the secondary display without activation; browsers stay headless. |
| 2026-11-12 | MB-25b fixture preparation | 6a08bec | Frozen all 12 task definitions, all 300 log seeds, and exact seed bytes via .gitattributes. Task 10 has a separate undo followup and requires an intermediate edit. Task 09 validates private fixture state, not model-created state. Task/checker and probe tests: 8 passed. Full suite before final runner tests and two added probe tests: 241 passed, 4 skipped. Baseline still pending; MB-25b is not complete. |
| 2026-11-12 | MB-25c probe preparation | ff1fefb | Temporary tools are gated on a scored 0.6.1 baseline, use the existing identity lock, abort remaining probes on uncertainty, and restore resources even if close fails. Five probe-helper tests pass. No live probe has run yet; Phase 2 remains gated. |
| 2026-11-12 | MB-25b | 45cd986 | Complete controlled baseline: 9/12, 206 tool calls, 10 failed/uncertain (4.8544%), 945.327 s, 222548 result bytes, 35429 preamble bytes / 14 sends. Compare against evidence/eval/v1-baseline-complete-2026-11-12.json. Original report and supplemental source hashes retained: only three original chats proved to have zero admission were subsequently executed. Uncertain browser request never replayed. Long-command checker timeout and guarded desktop false pass remain failures. Fixed evaluator reconnection classification and checker descendant containment; 17 focused tests pass. Both native fixture modes verified secondary placement. |
| 2026-11-12 | MB-25c | 4e26b31 | Early probes complete. P-1: 45 s returned correct code; 150 s completed locally but Muse reported timeout and did not recover code. Both incoming timeouts were 125000 ms despite descriptor 200000 ms: choose inline cap 25 s per plan. P-2: neither stock JPEG JSON envelope nor camera.capture naming produced visible pixels: route B. P-3: 3 edits, 0 failures, exact CRLF/tab bytes preserved. Questions/methods/observations/decisions and sanitized JSON are in evidence/MB-25-probes.md. Phase 2 can start with MB-26; P-4 and P-5 remain later gates. |
| 2026-11-12 | MB-26 implementation checkpoint | 4b035fe / 0bb7f60 | shell.run/shell.job implemented with cap 25 s, hidden PowerShell/cmd/Git Bash, owned Job Object cancellation, merged UTF-8 output and bounded head/tail/log pages. Full suite 285 passed, 4 skipped; subsequent future-cursor guard passed targeted regression. Runtime contract remains version 1 with legacy IDs until MB-28/29; app version stays 0.6.1 until Phase 3. P-4 preparation is in progress; do not tick MB-26 or start MB-27 until the live shell comparison/default decision is recorded. See evidence/MB-26-shell.md. |
| 2026-11-12 | MB-26 | a2544f6 | P-4 completed: PowerShell 5/5 and Git Bash 5/5, zero failed calls and commands for both; expected chain exit 3 passed. Retain PowerShell on the tie, keep Bash selectable. Source and local/full tests are in evidence/MB-26-shell.md; live checks in evidence/MB-26-shell-fluency.json. Added non-activating secondary-screen startup; 26 targeted UI/shell/probe tests passed. The apparent helper-model quota error came from an older worker state: the owner requested a fresh check, which succeeded (16 passed, 1 skipped). Continue with MB-27 using a helper model; normal app remains closed for upcoming identity-locked file probes. |
| 2026-11-12 | MB-27 | 9787bce | Guarded numbered reads, per-chat durable version receipts, atomic ordered/range edits, 4 MiB checkpoints, streaming large reads, ignore-aware bounded search/tree. Full item suite 365 passed, 5 skipped. Live P-3: exact and range each 3 edits, 0 failed calls, exact CRLF/tab bytes; retain exact first on tie. See evidence/MB-27-files.md and evidence/MB-27-edit-probes.json. Continue MB-28; normal app remains closed for subsequent identity-locked evaluation. |
| 2026-11-12 | MB-28 | d62f506 | Local ID-free binding and current policy gates; explicit stale legacy IDs still refused. Wire envelopes remove internal bookkeeping, normalize actionable/private-safe errors, cap complete JSON at 24000 bytes, and detect three identical failures. Full suite 396 passed, 5 skipped. Refinement: only local UI can create standalone mutation runs; remote mutation without an active request is refused to fence late Stop/Resume callbacks. SDK lacks trusted remote-turn attribution; do not claim ID-free detection of replaced same-chat turns. Capability response lists effect/availability instead of duplicating registered schemas. See evidence/MB-28-binding.md. Continue MB-29; app remains closed for live evaluation. |
| 2026-11-12 | MB-29 implementation checkpoint + requested launcher | e48ce96 / 79d3238 | Contract v2 tool surface/packs and combined task/change adapters implemented; default 29 tools, maximum 35. Actual browser/MCP/REPL tests, stale receipt refresh, per-project desktop inspection and MCP config fencing pass. Full suite after launcher: 462 passed, 5 skipped; locked offline sync clean. Installed/read back desktop and Start-menu MuseBridge Harness entries; current-user local activation prevents a duplicate gadget connection. Secondary-screen startup smoke passed. MB-29 remains unticked until live v2-core success/call-count gate passes. Normal app is closed for the identity-locked evaluation. |
| 2026-11-12 local | MB-29 | d42c041 | Live v2-core (UTC report dated 2026-11-13) passed 12/12 versus baseline 9/12, no task-success regression. Calls 145 vs 206 (-29.61%), failures/unknown 5/145 (3.45%) vs 10/206 (4.85%), result bytes 97622 vs 222548, wall 787.474 vs 945.327 s. Preamble increased 35429 to 49842 bytes across 14 sends; MB-30 must fix it. Full suite 462 passed, 5 skipped; launcher installed and tested. See evidence/MB-29-surface.md and MB-29-comparison.md. Begin MB-30; leave version 0.6.1 until skill/header/P-5/owner Teach/eval gates are met. |

| 2026-11-13 | MB-30 accepted, 0.7.0 | 27181dc | Owner explicitly confirmed VM skill v1; recorded that confirmation locally. P-5 first-header-only recall passed (527 bytes then thirteen zeros, zero tools). Frozen v2-context 12/12, 139 calls, 4 failed (2.88%), 81159 result bytes, 6543 preamble bytes / 14 sends, 771.893 s. Full suite 500 passed, 5 skipped; locked sync clean. Context bytes down 81.53% from baseline and 86.87% from v2-core. Begin MB-31a; shared-Main completion limitation remains explicit. |

| 2026-11-13 | MB-31a | 91996d2 | Bounded syntax feedback, optional owner-only glob-filtered after-edit hook/check, visible failed-check tail; publication remains successful on feedback failure. Mutation locks released during feedback, original admission rechecked before hook start, Stop/Resume late-hook and absolute-path receipt regressions covered. YAML cap 16 KiB after measured budget failure; other in-process parsers 64 KiB, PowerShell 300 ms best effort. Adversarial glob bounded to 20 ms. Full suite 538 passed, 5 skipped; isolated native startup clean. Next MB-31b. |
| 2026-11-13 | MB-31b | See local item commit | Durable request lineage and edit revisions; successful zero-exit commands capture their starting revision, task checks require an accepted receipt. Optional owner-configured finish-line prompt uses the queue once per request, including visual/goal continuations; respects holds, Stop, stale settings and the five-round limit. Changes shows unchecked edits; context exposes unverified_changes. Empty held queues can be resumed. Local fixture/UI/send evidence and final counts: evidence/MB-31b-finish-line.md. Next MB-31c; shell-only change discovery remains MB-32. |
