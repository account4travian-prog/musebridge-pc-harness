# Owner checks - ready to run November 16, 2026

This is the current hands-on guide. It replaces the manual folder/setup steps
in the [November 14 walkthrough](OWNER-ACCEPTANCE-2026-11-14.md). The owner is now
available for the real checks; availability does not count as acceptance.

The **MuseBridge - Owner checks** window is prepared. The disposable project is
**Owner checks 44b39f66**, with the dedicated **Notepad and voice acceptance**
conversation. Its prepared input document is open in Notepad on the secondary
display. Only this project has the new Desktop control setting and only that
Notepad window has the new screenshot grant. Main's workspace is unchanged.
The two expected result files do not exist yet. No prompt or microphone call
was started by the setup.

Reopen the controls from **Application -> Owner checks - Notepad and voice** or
The Muse agent's options menu. Do not press Prepare again unless the setup needs to be
reopened; the current session is already ready. **Open check folder** opens its
files, including START-HERE.md and any recorded observations.

## Confirm the current skill

Use **Teach skill v2...** in Owner checks. Send the previewed skill-saving
request once. Wait for Muse to confirm saving version 2, then reopen the dialog
and record its confirmation. The staged desktop draft is preserved. A previous
version-1 confirmation does not confirm version 2. Do not resend an uncertain
request.

## 1. Notepad typing and Save As

1. Click **1. Load desktop prompt**. This brings the harness forward with the
   prepared request; press **Send** there.
2. Click the prepared Notepad window and leave the keyboard/mouse idle for at
   least three seconds. Watch the Muse agent type and use Save As. The prompt permits
   one bounded retry if recent user activity blocks the first action; it does
   not permit bypassing the guards or writing the result through shell/file tools.
3. Independently open **notepad-acceptance.txt** in the check folder. It must
   contain exactly: `MuseBridge desktop acceptance 44b39f66.`
4. Return to Owner checks and click **Check saved files**. Tick the Notepad
   observation only if you saw the actual typing/save on the intended document.

If refused, retain the message and report it. If you use the PC during an
action, it should refuse recent activity. Stop Work remains global. Tick the
input/Stop Work observation only after actually observing the relevant behavior;
the mere presence of the button or an already-finished task does not prove it.

## 2. Spoken PC request

Use the same dedicated check conversation. The first confirmed text request
above establishes its remote thread. Main itself remains text-only.

1. Click **2. Voice controls**, confirm the displayed check conversation, and
   click **Start voice call**. Opening the controls alone does not open the mic.
2. Read the request shown in those controls aloud:

   > Create voice-acceptance.txt in this project containing exactly "MuseBridge spoken acceptance 44b39f66." Then read it back and tell me the result.

3. Confirm an audible response and independently inspect **voice-acceptance.txt**.
4. Use **Mute microphone**, unmute, **Interrupt spoken reply**, and **Hang up**.
   Confirm the controls change the actual sound/capture behavior. Voice targets
   its captured dedicated conversation even when the harness view changes.

No fixture, file-only result, typed transcript or synthetic audio substitutes
for your actual speech and listening observations.

## 3. Typed work after hang-up and recording the result

Click **3. Load typed follow-up**, then Send in the harness. Muse should read the
voice result and answer through the ordinary typed connection. Use **Check
saved files**, tick only what you personally observed, and **Save my observations**.
This writes owner-observations.json in the disposable check folder. File checks
and owner judgments are stored separately. Tell Codex the results or the exact
failure; no pairing credentials or private conversations are needed.

## 4. Companion and ordinary-use review

- Idle should show the cutout and small handle. Hover shows chat, voice,
  activity and options. Click opens a compact Main composer; Escape collapses
  it without losing the draft. The outward arrow opens full Main history.
- The plus menu has real image/file attachments, Main tools and workspace
  controls. It is not a copy of GPT's plugin catalog.
- Main and the side conversation should retain independent drafts and replies.
  Minimize the harness, drag the pet, change size and hide/recover it with
  View -> Show Atlas. Original portrait videos are optional in the Muse agent settings.
- Note monitor/DPI scale, any wrong-window behavior, recovery friction and
  usability problems. A day of normal work and its explicit verdict remain
  separate from these short checks. Sleep/wake/unplug and the frozen evaluation's
  desktop-task gate remain unaccepted until separately observed or rerun.

Candidate is still 0.8.0. This prepared session and passing local tests do not
mark the 1.0 release accepted.
