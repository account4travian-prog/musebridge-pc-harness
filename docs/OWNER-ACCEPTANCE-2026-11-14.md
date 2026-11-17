# Owner acceptance walkthrough - November 14, 2026

Current setup/steps are now in the [November 16 guide](OWNER-ACCEPTANCE-2026-11-16.md).
The owner is available and the disposable test session is prepared. The deferral
and unchecked boxes below retain their historical scope; they are not a new hold.

The owner chose **later** for real Notepad/voice testing on November 14. This is the prepared walkthrough, not a completed acceptance receipt. Automated fixtures, synthetic speech and screenshots do not check these boxes.

Use candidate **0.8.0**, runtime commit **0de011c** on `codex/parallel-chats`; [the ledger](../evidence/HARNESS-1.0.md) records its checks and remaining release gates. Keep the saved pairing; no re-pair or reflash is needed. Use a dedicated workspace conversation, because Main still cannot attribute completion reliably. Closing a fullscreen game and bringing the permitted target forward are owner actions; the harness must not take focus away from an active fullscreen app.

## Confirm the updated VM skill

Open Application -> Teach Muse this PC, review the version 2 preview, and click Send skill request once. Wait for Muse to confirm that it saved version 2 on its VM. Reopen the dialog and record that confirmation. Earlier version 1 confirmation does not confirm the new skill. This is one normal request; do not resend it if its outcome is uncertain.

- [ ] Muse explicitly confirmed saving skill version 2; owner recorded confirmation.

## Real Notepad task

1. Create an empty disposable folder under `%TEMP%`, named `MuseBridge-owner-acceptance`, and attach it as a project in MuseBridge. Choose Desktop control. Open Notepad yourself and bring it forward; leave games and other important windows untouched.
2. Send: `Use the desktop tools to enter "MuseBridge desktop acceptance." in this Notepad window and save it as notepad-acceptance.txt in this project's folder. Use snapshots to inspect the controls and verify the saved result. Do not use shell/file tools to perform the write.`
3. Keep the PC idle while Muse operates. Watch the target window and save dialog. If you start typing/moving the mouse, actions should refuse recent activity rather than competing for input. Stop Work should prevent further actions.
4. Open the saved file independently. Confirm the exact sentence, the right folder and that other documents/windows were unaffected. Report the result and any friction. A tool's Invoke receipt alone is insufficient.

- [ ] Observed actual Notepad typing and save on the intended window.
- [ ] Independently inspected the file, exact text and folder.
- [ ] Recent user activity/Stop behaved correctly.
- [ ] Recorded the tested build, date and display scale.

## Actual microphone/speaker request

1. In the same disposable project, start the voice call. Speak: `Create voice-acceptance.txt in this project containing "MuseBridge spoken acceptance." Then check the saved text and tell me the result.`
2. Confirm Muse heard your actual voice and that real PC tool activity produced the file. Independently open it and check the sentence. A silent WAV or typed transcript does not satisfy this step.
3. Test microphone mute/unmute, interrupt a spoken response, and hang up. Confirm interruption/hang-up stop audio as expected and the normal PC connection remains usable. Do not include private information in the test.

- [ ] Real spoken request, audible response and verified PC tool effect.
- [ ] Microphone mute/unmute checked.
- [ ] Response interruption checked.
- [ ] Hang-up checked; subsequent typed PC command still works.

## Everyday use and acceptance

Use the checked candidate for normal work for a day. Note wrong-project actions, missing replies, blocked/resumed work, confusing warnings, and recovery friction. Include the relevant chat/task and approximate time, without credentials or private message contents. Report whether the ordinary workflow feels usable. This is separate from the fixture evaluation score.

- [ ] Owner day-to-day trial and explicit verdict recorded.
- [ ] Any reported defects repaired and relevant checks rerun before final acceptance.

Real mixed-monitor/DPI behavior and any sleep/wake or device-unplug condition not separately observed remain explicit acceptance gaps. These boxes are filled from actual owner observations, never from a passing automated suite or elapsed time.
