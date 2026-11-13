# MB-31b finish-line verification - November 13, 2026

Authority: [upgrade plan](../HARNESS-UPGRADE-PLAN.md), MB-31b. Implementation and local acceptance complete; next item MB-31c. Application version stays 0.7.0 and tool contract stays 2.

## Delivered behavior

- File-tool publications and restores advance a durable request revision. Identical writes do not create a new change. Counts track distinct project-relative files. Changes displays "not checked since last edit" and default Markdown `bridge.context` exposes `unverified_changes`.
- Each `shell.run` command captures the revision at admission. Only a terminal `succeeded` job with exit code 0 clears that revision. Background jobs count when their terminal result is observed by the tracker, even if they outlive the inline wait. A command begun before a later edit cannot clear it. Reusing an old job ID cannot upgrade its revision.
- `task.verify` captures the launch revision and additionally requires the adapter to accept the verification receipt. Failed hooks, stale receipts, unfinished checks and nonzero exits do not clear feedback. This feedback does not complete tasks or certify semantic correctness.
- Conversation actions -> Finish-line verification saves a separate project check command (up to 8000 UTF-8 bytes) and Auto-check after changes, off by default. Saving neither executes a command nor resumes a queue. Remote tools cannot configure it.
- After attributed dedicated-chat completion and turn settlement, an unchecked request may enqueue one prompt through the existing queue: run the configured command and fix failures or explain why it does not apply. A durable unique request key prevents a second prompt after retries, projection loss or continuation edits. Screenshot and goal continuations preserve that request lineage. Main remains excluded.
- Prompts obey Stop, queue holds, restart/disconnect/chat-switch holds and uncertain-send handling. The command/configuration, latest request, project, policy and queue admission are rechecked before delivery, including after asynchronous header preparation. A stale prompt is cancelled or held conservatively. An uncertain send is never retried automatically.
- During Continue Goal, the queued prompt replaces an existing goal round rather than adding a round. The shared request also carries the five-round cap, preventing an unchecked final round from producing a sixth send. Visual delivery retains the current goal lineage and limit.
- Queue -> Resume pending is usable even when a held dedicated queue is empty, allowing future prompts after restart. Existing manual queue ordering remains unchanged; goal-owned automatic prompts are consumed by the goal's current send slot.

## Persistence and verification

Catalog schema advances from 3 to 4. The additive migration creates feedback tables and adds nullable automation lineage columns plus a unique index to the queue inside the existing atomic migration. Existing pending queue rows retain their text/state and remain held on restart. Older writers refuse schema 4 through the existing schema-version gate; binary rollback requires a compatible catalog backup. No real profile or pairing data was modified for this work.

Local fixtures cover exactly one queued/sent follow-up, no prompt after a valid check, no prompt while paused/held/read-only, unknown terminal state, Main, new requests, changed workspace/settings, cross-chat checks, a check before edits, running checks, idempotent check reuse, failed verification hooks, screenshot lineage, five-round goals, lost sends and queue restart. Send-path fixtures verify request lineage and a queue hold introduced during header preparation prevents any network call/outbox admission. Offscreen Qt fixtures verify settings save does not run work and the Changes warning clears after a successful command. Migration fixtures retain a version-3 pending queue entry.

Final full suite: `.venv\Scripts\python.exe -m pytest -q tests` -> **566 passed, 5 skipped** in 108.14 seconds. Final focused verification/migration/UI/send regression run -> **45 passed** in 14.95 seconds, including explicit `bridge.context` feedback and the final column-width refinement. Skips retain the existing fixture/platform limitations; no skipped case is counted as acceptance.

Isolated offline startup: disposable `MUSEBRIDGE_DATA_DIR`, `QT_QPA_PLATFORM=offscreen`, `python -m musebridge.app --smoke` -> exit 0 with no stderr. `python -m tools.preview_shell` -> exit 0; visually inspected `.local/visual-review/changes.png` and widened the Change column so its warning is readable. The preview helper initially failed because it still required the file-read hash removed by MB-27, then left open fixture databases during error cleanup. Updated it to use the current read receipt and a finally-closed fixture window; rerun passed. These are offline Qt/native-module fixtures, not owner desktop interaction or real Muse acceptance.

## Limits

This item tracks direct file-tool edits and restores. Shell-only file discovery/recovery remains MB-32. Syntax diagnostics and after-edit hooks do not independently satisfy this item's `shell.run` / `task.verify` check rule. A successful arbitrary command is deliberately only a feedback signal; structured task acceptance retains its stronger receipts.

No live Muse evaluation or real owner project check was run for MB-31b. The last frozen live evaluation remains v2-context 12/12. Settings remain opt-in; real voice, desktop, day-to-day acceptance and parallel-chat routing remain open under their existing items. No re-pair, reflash, deployment or remote push was performed.
