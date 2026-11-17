# Harness closeout candidate - November 14, 2026

Authority: [HARNESS-UPGRADE-PLAN.md](../HARNESS-UPGRADE-PLAN.md). Current candidate **0.8.0**, contract **2**, catalog schema **5**, VM skill **2**. This filename is the planned 1.0 ledger, not a claim that 1.0 acceptance or release has occurred. The owner explicitly deferred real desktop, voice and everyday-use checks; [the exact walkthrough](../docs/OWNER-ACCEPTANCE-2026-11-14.md) is prepared.

## Implementation and local checks

MB-31c stall feedback, MB-32 private Git recovery, MB-33 browser refs/actions/diffs, MB-34 desktop refs/actions/OCR, MB-35 machine-image processing and MB-36a concurrent routing are implemented. Foundation **92ea905** passed **585 tests, 5 skipped**. Final frozen code **0de011c** on `codex/parallel-chats` passed **599 tests, 5 skipped in 419.95 seconds**. `uv sync --locked` resolved 81 packages and checked 78 cleanly; `git diff --check`, local documentation links and the plan's ASCII check passed. Skill v2 is 954 words. Targeted archive/routing/UI/finish-line checks passed 38 tests; header/routing checks passed 14; final desktop/vision/evaluator/error checks passed 67.

Risk review repaired snapshot rebasing after writer handoff, hook writer ownership, per-stream EOF isolation, pre-ack tracking loss, mouse fallback activity rechecks and archived follow-up admission. This was a source/self review using the code-review skill, not an independent reviewer approval. Restore rehearsals include exact binary/CRLF bytes, index/HEAD invariance, later-owner refusal, I/O interruption/manual idempotent recovery, junction exclusion and 20000-file warm performance. [Recovery evidence](MB-32-recovery.md), [browser/desktop/vision evidence](MB-33-35-local.md), [parallel evidence](MB-36a-parallel.md).

## Frozen measurements

| Phase | Success | Tool calls | Failed calls | Preamble bytes | Scope |
|---|---:|---:|---:|---:|---|
| Baseline | 9/12 | 206 | 10 (4.85%) | 35429 | [Original baseline](eval/v1-baseline-complete-2026-11-12.json) |
| v2-core | 12/12 | 145 | 5 (3.45%) | 49842 | [Core evaluation](eval/v2-core-2026-11-13.json) |
| v2-context | 12/12 | 139 | 4 (2.88%) | 6543 | [Context evaluation](eval/v2-context-2026-11-13.json) |
| v2-eyes | 11/12 | 143 | 6 (4.20%) | 8593 | [Frozen eyes report](eval/v2-eyes-2026-11-14.json) |
| v1.0 candidate | 11/12 | 123 | 2 (1.63%) | 8557 | [Final candidate report](eval/v1.0-2026-11-14.json) |

Probe decisions remain: P-1 selects 25-second inline command wait (150-second request timed out remotely); P-2 selects labelled next-turn image delivery (inline envelopes did not deliver pixels); P-3 proved exact CRLF/tab edits; P-4 retained PowerShell after a five-case tie with Bash; P-5 selected first stable header only. [Probe receipt](MB-25-probes.md), [shell comparison](MB-26-shell-fluency.json), [header/context receipt](MB-30-context.md).

Final targets are all twelve tasks successful with no baseline success regression, fewer than 5% failed tool calls, at least 40% fewer calls than 206 (at most 123), and first ordinary preamble below 800 bytes. Targets are assessed from frozen reports, never filled from intent or selected successful reruns.

v2-eyes browser and pixel tasks passed. Its desktop file checker passed, but five desktop action attempts were refused by activity/foreground/fullscreen gates and no desktop action succeeded. The evaluator correctly returned `failed-capability-not-exercised`. It used 30.58% fewer calls and 75.75% fewer preamble bytes than baseline, missing the 40% call target. Its twelve initial per-task preambles ranged from 674 to 683 bytes (read-only numeric catalog check), below 800. [Full comparison](eval/v2-eyes-versus-baseline-2026-11-14.md). Retain the 11/12 result, including the agent's shell workaround; file-only success is insufficient and the workspace is not a shell sandbox.

After that observation, desktop pre-action refusals gained an explicit no-effect/handoff error and a do-not-bypass hint. Guards were retained. The final task 11 prompt now explicitly requires desktop.snapshot/desktop.act and stops on a refusal, clarifying its existing capability requirement rather than accepting substitute shell writes. The original v2-eyes report is unchanged. The runner also accepts the plan's exact safe `v1.0` label (previously rejected by its generic no-dot label rule).

Final candidate evaluation on unchanged runtime commit **0de011c**: **11/12**, **123 calls**, **2 failed calls (1.626%)**, **830.601 seconds**, **89142 result bytes**, **8557 preamble bytes across 14 sends**. All nine baseline-success tasks retained success; browser and image pixels passed. Task 11 returned `failed-check`, with no successful desktop action after its safety refusal. It stopped instead of substituting shell writes. [Final comparison](eval/v1.0-versus-baseline-2026-11-14.md).

| Final gate | Observed result | Decision |
|---|---|---|
| All twelve tasks | 11/12; desktop blocked | Open; release/merge not accepted. |
| Baseline successes preserved | All nine retained | Passed. |
| Failed-call rate below 5% | 2/123 = 1.63% | Threshold met. |
| Calls reduced at least 40% | 206 to 123 = 40.29% | Numeric threshold met, but desktop work remained incomplete; no all-task efficiency claim. |
| First ordinary preamble below 800 bytes | Twelve first sends ranged 671..680 bytes | Passed; [numeric header receipt](MB-36a-header-metrics.json). |
| Context/result overhead | Preamble down 75.85%; results down 59.95% from baseline | Measured; per-request routing bindings cost more than the earlier context-only milestone. |
| Full local suite and dependency lock | 599 passed/5 skipped; locked sync clean | Passed. |
| Real owner desktop/voice/day use and skill v2 | Deferred by the owner | Open; exact walkthrough prepared. |

No further rerun was used to fish for a twelve-task or lower-call result. MB-33 is accepted; MB-34/35, MB-36a merge and MB-36b release boxes remain open according to their unmet acceptance gates. The engineering implementation is available as the 0.8.0 candidate. After the eval worker released the identity lock, 24 owned evaluation chats were archived locally and four owned fixture desktop grants were removed. No owner chats or grants were changed.

## Design refinements and compatibility

- D2/D7: short ephemeral conversation/run binding per connected request refines wholly ID-free calls and unchanged follow-ups. The SDK lacks trusted callback origin, so simultaneous requests need a matching model-supplied pair. Stable environment headers remain cached. This is consistency protection, not tenant authentication.
- D12: snapshot first-write rebasing preserves a prior chat's completed writes. Private Git imports exact bytes without project filters/hooks/index; snapshots skip links and excluded/oversized files and use bounded metadata caching. Actual bytes are compared during restore. Multi-file publication is not transactional and interrupted/unknown runs have no certified end tree.
- D15: OS OCR is available and on by default, with an owner switch; it is a perception fallback. Its text refs cannot substitute for actionable UIA refs. Real OS recognition of a synthetic code passed.
- Desktop capture: real scaled-window checks found Qt UIA root bounds omit non-client framing. Snapshots/labels now use physical outer bounds matching PrintWindow, and movement during capture/reference preparation is refused. Read-only native probes passed at Qt scale 1.25 and 1.5 on the secondary display. This does not claim actual Windows scale-setting changes or mixed-monitor input acceptance. [Numeric receipt](MB-34-scaled-geometry.json).
- D16: installed candidate is 0.8.0. The 1.0 version bump waits for actual release acceptance, including the owner's deferred checks.
- D17: read-only `harness_stats.summarize` since the 0.7.0 acceptance commit (2026-11-13T03:59:05-06:00) found zero old-name calls in the retained real catalog at final closeout inspection ([numeric receipt](MB-36b-alias-usage.json)). Compatibility dispatch is retained for this 0.8 candidate: skill v2 has not been owner-confirmed and local UI still uses legacy inspection routes such as change.diff. Removing remote aliases is deferred to accepted 1.0 closeout; none is registered as duplicate discovery.

## Migration, operating limits and remaining acceptance

The parallel branch remains separate from `harness-v2` while full release acceptance is unmet. No remote push, public release, startup-at-login change, re-pair or reflash was performed. Candidate runtime code is committed; documentation/evidence may have a later local commit with identical runtime code.

The candidate was reopened with the saved pairing after evaluation cleanup. Native read-only inspection observed **one visible app window, Connected status, secondary-display placement and unchanged foreground since launch**. The app remains running and connected. [Sanitized app-state receipt](MB-36b-app-state.json).

Before the live migration, SQLite online backups of the existing real catalog/jobs were integrity-checked in the ignored local acceptance-backup directory. No credential export, re-pair or reflash occurred. The real catalog migrated from schema 3 to 5. Do not run an older schema writer on that profile; rollback needs the corresponding database backup and stopped app, not only an old binary checkout. Fixture tests do not use the real profile; live probes intentionally use the saved identity and disposable project/chat data.

The workspace is not an OS sandbox. Shell/network/install/excluded database effects are outside file restore. External concurrent edits retain ordinary compare/publication races, and same-metadata rewrites can evade cached snapshot discovery. Main's shared stream cannot attribute individual completion; dedicated chats are required for automatic continuation. No native fork/steer/worker/compaction SDK API is claimed. Actual owner Notepad input, microphone/speaker use, mute, interruption, hang-up, mixed-monitor input behavior and a day of normal use are unaccepted. VM skill version 2 also needs the owner's Teach confirmation. These remain open regardless of test counts or elapsed time.
