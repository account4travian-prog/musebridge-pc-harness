# MuseBridge 0.5.0 integration and acceptance — 2026-11-11

Historical 0.5 functional development build. See [the 0.6 follow-up](FIVE-CAPABILITIES-0.6.md) for superseding features and validation. This ledger records the earlier acceptance handoff under [MASTERPLAN](../MASTERPLAN.md), MB-11–23. It does not assert full owner/device acceptance. Root source has no Git metadata; the SDK remains clean at `9d8cca156cdec9ea06efcf854b03dccc5ca76d42`. Project package and control/voice registration versions are 0.5.0. No firmware change, re-pairing, local model or duplicate gadget identity was introduced.

## Delivered and exercised

| Journey | Evidence and practical limit |
|---|---|
| VM workflow compatibility | Versioned Windows manifest, project-relative paths, PowerShell, execution profiles, run/call ownership. Actual Muse discovery/file/command workflows pass. Existing VM skill execution internals were not independently inspected; this is execution compatibility, not a VM skills installation. |
| Checked work | Direct-file checkpoints/diff/conflict-aware restore and structured dependencies/check receipts. Actual Muse task/check run passed; an independent later file edit invalidated its receipt and effective completed goal. Exit-success evidence is not independent semantic judgment. |
| Images and documents | Earlier actual Muse pixel-only random-code vision test passed. Current artifact probe imports a synthetic UTF-8 document, changes the source afterward, and actual Muse reads the immutable artifact and writes its original contents. Binary PDF/Word extraction and an automatic screenshot vision loop are absent. |
| Continuity | Actual note, handoff inspection and new remote chat receiving the exact summary marker passed. Seed consumed only after acknowledgment; source task IDs remain references. Summary branching is not hidden-state cloning. |
| Queue | Two explicit requests delivered in order after the first turn boundary. Unknown sends/restart/switch/Stop hold delivery; no blind replay. A subsequent three-request probe tests advancement across a reply-only turn. |
| Git/worktrees | Actual Muse status/diff/worktree creation passed. New checkout was independently readable with seed bytes; original dirty file, untracked owner file and selected branch survived unchanged. No commit/merge/push/cleanup. |
| Terminal | Actual Muse ConPTY prompt/read/input/exit produced the expected fixture file. Local real ConPTY tests additionally cover Unicode, resize, input deduplication, natural exit and child-process termination. Plain-text view, not full VT emulation. |
| Hooks/MCP | Actual Muse invoked a locally configured MCP echo tool and after-edit hook; output/effect independently checked. Real local protocol tests cover grants, schemas, uncertain cancellation/replay and connection cleanup. Hooks reuse supervised jobs. Arbitrary third-party servers/commands remain outside these fixture claims. |
| UI | Isolated Qt journeys cover drafts/chat switching, queue/document refusal, Changes restore/conflict, profiles and background-job visibility. Fictitious offscreen previews at 1280×840 and 960×720 include Chat/Plan/Changes/Tasks/Queue/Artifacts/Overview. Final 0.5.0 normal app launch was independently observed through native accessibility: exactly one MuseBridge window, Connected, and all nine tabs present. The app remains open on the existing main chat; no microphone activation, foreground screenshot or user draft entry was used for this check. |

## Test commands and result

```powershell
uv sync --offline
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe tools\preview_shell.py
```

Final local suite: **180 passed, 4 skipped in 26.39 seconds**. The four skipped cases need Windows symlink creation permission (file checkpoints, task inputs, workspace paths and Git paths). Equivalent validation and deterministic path-alias guards have exercised tests, but the skipped physical symlink cases are not passes. Pytest uses isolated state, local processes and loopback browser/MCP fixtures; it does not use the paired Muse account or microphone.

Contained failure/recovery checks:

- Before baseline and fault, unique temporary profiles contain synthetic drafts, plans, IDs and a journal. A child process performs one filesystem effect and exits through `os._exit(73)` after durable admission, under a 15-second parent-process supervisor. Exit code proves injection. On reopening, the queue/run/call are unknown and held, draft and once-only marker survive, SQLite integrity is `ok`, no claim/replay occurs, and a fresh unrelated file operation succeeds. The baseline exits normally. This tests abrupt process exit, not power-loss durability.
- A pre-expansion schema fixture upgrades while preserving draft, history, instructions, goal, plan and remote ID. SQLite backup is restored into a separate profile and reopened semantically. An injected table-creation denial rolls the execution migration back; removing it permits upgrade without losing owner fixture data. This tests transactional failure and restore, not a storage-controller crash.
- Lost acknowledgments, cancellation and post-publication errors remain uncertain. Known server acknowledgments survive local projection/tracker failure without another send. Stop/Resume revokes old runs/admissions; pending terminal input is revoked. Actual owned process cancellation is separate from undoing an already executed external effect.

These checks apply chaos/recovery principles to bounded fixtures; they do not disrupt the live paired profile, host disk, game or unrelated applications.

## Live probe receipts

Reports under ignored `.local/` contain sanitized booleans/IDs, not credentials or private screenshots. Do not rerun ambiguous operations blindly; inspect their fixture and journal first. Each probe takes the application's existing identity lock and must run while the normal app is closed.

- `.local/expansion-probe-1794394606/report.json`: actual image pixels, Windows edits, successful check job, capabilities and checkpoints passed; zero unknown tool calls.
- `.local/task-probe-1794395291/report.json`: task completion receipt and later owner-edit invalidation passed; zero unknown tool calls.
- `.local/workbench-probe-1794456522/report.json`: queue files/order, MCP read, terminal, hook, usable worktree, preserved original work and continuity tools all passed; zero unknown calls. That older report's pass oracle covered effects, not the second run's terminal event. The run exposed the reply-only completion defect below; do not cite it as proof that every task-status event arrived.
- `.local/workbench-probe-1794455635/continuity-report-1794456441.json`: note recorded, handoff called, exact new-chat seed received, seed consumed after acknowledgment; all true, zero unknown calls.
- `.local/artifact-probe-1794457200/report.json`: immutable document received, artifact tool called and selected draft consumed after acknowledgment; all true. Second reply had **no task-status events**, but `second_turn_answer_complete=true` after the fix; zero unknown calls.
- `.local/queue-recovery-1794457292/report.json`: three acknowledged requests, ordered completion boundaries and independently verified third file all true. Run states were `remote_finished`, `remote_reply_finished`, `remote_reply_finished`; zero unknown tool calls, `passed=true`. This closes the actual reply-only queue advancement regression.

## Defects found and repaired

1. Handoff IDs used compact UUIDs while Muse acknowledged canonical UUIDs. SDK exact-session validation then rejected an accepted message. New handoffs use canonical IDs, with real SDK acknowledgment regression coverage and live new-chat proof.
2. Post-ACK tracker/projection errors could present false send failure. Delivery acknowledgment is now retained; local tracking is reported separately. Queue/goal continuation holds for inspection instead of resending.
3. The controller required a task-status terminal for every answer. Actual subsequent turns sometimes emitted only assistant replies. It now uses the pinned SDK's `complete()` quiet/idle rules **with a bound, finished reply**, records `remote_reply_finished` separately, and still refuses empty silence, foreign/unfinished replies and running tasks. This never means verified goal success. Regression tests and live reply-only proof passed.
4. Hook admission did not reject a changed project-root alias before process launch. It now checks the persisted root immediately before admission, with a no-process-effect regression.
5. Windows MSIX redirection hid the existing paired profile from a changed process environment. Storage now discovers the existing cache profile without decrypting/copying/merging it, prefers a stable physical path for aliases, and refuses multiple distinct profiles. `MUSEBRIDGE_DATA_DIR` allows an explicit absolute choice. Credential paths remain excluded from project file/artifact tools.

## Remaining acceptance and boundaries

- Owner-initiated real speech, mute/barge-in/audio behavior and spoken tool request; prior silent voice admission/hangup is insufficient.
- A real benign Windows application task while respecting owner input; offscreen Qt and guarded UIA fixture tests are insufficient.
- Extended sleep/wake, actual network loss/recovery/token rotation and physical board-unplug acceptance. Pairing still uses the ESP32; everyday PC networking does not use USB as a command relay.
- Binary document extraction, automatic screenshot vision, generic before/after-every-tool plugin events, full VT emulation and OS notifications are not implemented. Implemented alternatives and limits remain visible in feature receipts; no owner acceptance of these exclusions is inferred.
- Native hidden-state fork, text steering, remote worker creation, compaction and usage lack dedicated routes in the pinned SDK. Service support remains unknown. No fabricated UI or local replacement model is supplied.

Next dispatch is MB-23 live owner journeys and extended recovery, with any additional adapter work explicitly retained in the MasterPlan. Do not restart the completed foundation or treat this ledger as permission to publish, enable startup, activate the microphone or re-pair.
