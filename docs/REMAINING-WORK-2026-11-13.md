# Remaining work assessment - updated November 16, 2026

Authority: [HARNESS-UPGRADE-PLAN.md](../HARNESS-UPGRADE-PLAN.md). This maintained path describes that backlog; it does not create a second implementation track.

The 0.8.0 candidate implements the MB-24..36 engineering features: stall feedback, broader file recovery, browser and desktop refs/actions, OCR, processed machine images and concurrent chats. It uses contract 2, catalog schema 5 and VM skill 2. [The closeout ledger](../evidence/HARNESS-1.0.md) records exact tests/evaluations and open release gates. Keep 1.0 acceptance open while these gates remain.

| Item | Verified state | Remaining gate |
|---|---|---|
| MB-30 | Owner confirmed skill v1; P-5 and v2-context passed 12/12 | Updated skill v2 needs a new Teach confirmation. |
| MB-31a/b/c | Syntax feedback, opt-in after-edit/finish-line checks, revision evidence and non-blocking stall hints implemented/tested | Optional settings remain off until locally enabled; no automatic semantic acceptance. |
| MB-32 | Private byte-preserving Git snapshots include eligible shell changes; later-owner refusal, partial I/O recovery, junction exclusion and 20000-file warm performance pass | Excluded/outside/database/network effects are not restorable; no multi-file transaction. |
| MB-33 | Browser refs, stale-ref refusal, richer actions/diffs and large-page tests pass; real v2-eyes browser task passed | Final candidate evaluation recorded in the ledger. |
| MB-34 | Desktop runtime refs/actions, OS OCR and gates implemented; native read-only geometry checked at 125% and 150% Qt scale | Real Notepad save and actual mixed-monitor input acceptance. v2-eyes desktop capability did not pass: activity/fullscreen gates blocked actions. |
| MB-35 | Resize/labels/crops bounded to transport; synthetic 4K fixture and actual image-pixel task passed | Whole v2-eyes gate remains 11/12 due to its desktop task. |
| MB-36a | Concurrent/isolation/restore fixtures and real overlapping two-chat probe pass; one identity/session, Main view did not interrupt work | Merge/release gate requires full-suite/final-eval evidence. SDK callback origin is not authenticated. |
| MB-36b | Docs, skill v2, walkthrough and dependency checks complete; final eval 11/12, 123 calls, 2 failures (1.63%) | Teach/desktop/voice/day-use verdict, desktop evaluation acceptance and 1.0 release acceptance. |

Frozen v2-eyes scored 11/12, 143 calls, 6 failures (4.20%) and 8593 preamble bytes. Browser/pixel tasks passed. Desktop actions were refused during recent user activity and a fullscreen foreground app. Its file checker passed, but no desktop action succeeded, so capability acceptance failed. The 40% call-reduction target was also missed. These observations are preserved.

## Floating companion - November 16

MB-37 is a new owner-requested feature: a floating Muse agent avatar provides Main
interaction while the harness contains dedicated side conversations. Interaction
research, source constraints and the artwork handoff are in
[the companion design](FLOATING-COMPANION-RESEARCH-2026-11-15.md). Research is
complete; the current compact revision provides transparent quiet idle, hover
controls, shared Main quick composer, attachments, and captured dedicated voice
targets. Full suite 619 passed/5 skipped; final scoped control checks 10 passed;
native transparency/hover/playback/placement pass. [Current receipt](../evidence/MB-37b-compact-and-owner-checks.md).
Owner interaction, new quick-panel live messages and real voice/desktop results
remain. Main itself still refuses voice; its hover control selects a dedicated chat.
This addition does not retroactively change the earlier frozen evaluations.

## Shared Main completion limit

Main's shared stream supplies replies/status without reliable request-parent linkage. Main commands work in its attached workspace, but the harness cannot safely certify which request finished. Dedicated chats remain the route for automatic continuation. Parallel routing retains this distinction.

## Owner checks prepared; owner is now available

The owner said on November 16 that real voice, Notepad and other checks can now be
set up. Application -> Owner checks has a prepared disposable project, staged
desktop prompt, named Notepad on the secondary display and a narrowly bound
screenshot grant. [Current walkthrough](OWNER-ACCEPTANCE-2026-11-16.md) covers
Teach v2, actual typing/save, spoken PC work, mute/interruption/hang-up and typed
follow-up. No prompt or microphone call was started automatically. Earlier
November 14 deferral is historical. Day-use verdict and unobserved hardware
conditions remain separate. Do not fill acceptance boxes from fixtures or time.

The candidate remains 0.8.0 until release acceptance. Queues/visual follow-ups continue across view changes; archive/disconnect/restart/Stop hold them. Held queues require explicit resume. Recovery and routing have direct evidence with limits documented in the ledger.

Final frozen runtime 0de011c passed 599 local tests, 5 skipped. Final candidate evaluation retained all baseline successes and met numeric failure/call/header thresholds, but its desktop task remained blocked; no 1.0 release or parallel-branch merge occurred. See the ledger for the measured comparison and incomplete-task caveat.
