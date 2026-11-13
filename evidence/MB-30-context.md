# MB-30 implementation and Main-chat repair

Implementation commit: `cb2ba6c`. MB-30 is complete. Version 0.7.0 is installed, contract 2. The frozen evaluation ran the accepted 0.6.1 implementation before the release metadata bump.

Implemented a four-line acknowledged header under 800 UTF-8 bytes, sent on first use per connection and project/profile/Stop state changes. Ordinary unchanged turns send exactly the user's text. Header state commits only after known server acknowledgement, including when local tracking later fails. Uncertain sends retain the header for a later distinct request and are never automatically replayed. P-5 will select whether periodic repetition is needed.

`bridge.context` now returns Markdown with environment versions cached per connection, project tree/Git summary, saved instructions, guarded AGENTS.md, goal/plan, task IDs and effective notes. Default encoded text is bounded below 12,000 bytes; narrower sections include instructions, tasks, notes, history, complete tool schemas, skill, hooks and vision. Main's selected subfolder is identified as inside a parent repository rather than falsely claiming that repository as its own.

The VM skill and Application -> Teach Muse this PC dialog are implemented and locally tested. Sending the teaching request preserves composer/attachment drafts and does not record installation. The owner confirmation control records the installed version; P-5 refuses live execution before that confirmation. No second local skill runtime was added.

The owner explicitly selected `C:\Users\the owner\Documents\MuseBridge\PC-Workspace`. Created that directory and attached it to the existing Main chat under the application identity lock with the normal app closed. Local and remote chat identities were preserved. The folder is ignored by this source repository. Same-chat workspace selection is now available through the composer folder control. Binding refuses active mutation calls or owned jobs, retires old requests and fences pending effects against scope changes.

## Live evidence

- Original probe: [MB-30-main-workspace.json](MB-30-main-workspace.json). Its request timed out waiting for attributable completion; it was not resent.
- Durable independent execution readback: [MB-30-main-execution.json](MB-30-main-execution.json). Main context and shell calls succeeded. Exactly one hostname job in the chosen workspace exited zero, with output matching the local PC. No hostname, user name, identifiers or raw output are in the receipt.
- A separate no-tool Main diagnostic and fresh unbound-chat negative test: [MB-30-main-events.json](MB-30-main-events.json). The shared Main stream's task and assistant events lack request-parent linkage; its completed answer remains unbound in the pinned SDK. The bridge correctly keeps that request unverified instead of accepting somebody else's Main reply. No attribution rule was weakened.
- The fresh unbound chat made one shell call, received a readable `[no_project]` error, reported that code accurately and created no command job. It did not report `device_error: unknown`. This verifies the SDK error-string adapter end to end, separately from Main command execution.

Use a separate workspace conversation for automatic continuation. Manual Main requests can execute PC tools, but its present upstream event shape cannot safely establish request completion. Fixture evaluation in side chats does not prove this shared-Main completion property.

## Remaining acceptance

The owner explicitly confirmed VM skill version 1 on November 13. The local confirmation preference was still zero when read back, so that explicit owner reply was recorded under the application lock; no installation was inferred from sending a message. P-5 then passed with first-message-only headers: fourteen sends, 527 bytes on turn 1, zero added bytes on turns 2-14, zero tool calls, and correct final recall of the private project fact. Retain `repeat_every=0`. See [P-5 JSON](MB-30-header-memory.json). The frozen v2-context evaluation passed all twelve tasks.

Initial local validation on November 13: **499 passed, 5 skipped** in 105.15 seconds. After the Teach acknowledgement/draft-preservation fix, the complete suite passed **500 tests, 5 skipped** in 106.66 seconds. Locked offline dependency sync is clean. Focused coverage includes same-chat workspace binding and scope fences, SDK serialization, header acknowledgement/unknown delivery behavior, context size/instruction retention, Teach request/confirmation and draft preservation. These isolated tests do not contact Muse or use the real profile. The live checks above were separate, explicit identity-locked probes.

Owner Teach confirmation, P-5 and the frozen v2-context evaluation are complete. MB-30 is ticked and version 0.7.0 is installed. The next work is MB-31a.

## Frozen context evaluation

[v2-context JSON](eval/v2-context-2026-11-13.json) and [baseline comparison](MB-30-comparison.md): 12/12, 139 tool calls, 4 failed calls (2.88%), 81,159 result bytes, 6,543 preamble bytes across fourteen sends, 771.893 seconds. Baseline was 9/12, 206 calls and 35,429 preamble bytes; v2-core was 12/12, 145 calls and 49,842 preamble bytes. Task success did not regress. Preamble decreased 81.53% versus baseline and 86.87% versus v2-core. The 1.0 target of 40% fewer calls is still unmet (current reduction 32.52%). Browser/desktop capability checks and independent fixture checks passed; this is not owner voice/Notepad/day-use acceptance.
