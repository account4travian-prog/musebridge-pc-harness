# MB-37 floating the Muse agent companion - November 15, 2026

Authority: [upgrade plan](../HARNESS-UPGRADE-PLAN.md#mb-37-floating-the-claude-agent-companion).
Candidate remains 0.8.0; this is new local work after the frozen 0de011c runtime.
Earlier 1.0 evaluation and owner-acceptance gaps remain open.

## Delivered behavior

- The Muse agent is an independent, draggable, always-on-top native Qt widget in the
  existing process. Clicking the avatar or Chat opens Main in its own resizable
  panel. The harness contains dedicated side conversations only. Main links in
  Overview and desktop notices open the companion without replacing a side chat.
- Main retains its history, draft, image/document attachments, attached workspace
  and policy. Explicit Worker.send targeting separates Main from the selected
  side chat. Acknowledgments, send errors and transcript rows retain their chat
  identity. Unknown sends preserve drafts and are never replayed automatically.
- The Work menu lists side-chat activity and opens the selected chat in the
  harness. Options expose light/dark art, three sizes, reduced motion, static
  image import, bundled-art reset, position reset, hide, Stop Work and Resume.
  View -> Show Atlas recovers a hidden widget. Position and options persist.
- Minimizing the harness leaves the Muse agent available. Closing Main hides its panel;
  quitting the harness saves both drafts and stops the widget/player/worker.
  Oversized Main drafts refuse close before shutdown starts.
- The Muse agent's supplied portraits and silent MP4s are bundled unchanged. Idle,
  sending/waiting and brief reply-received animations are used; dark mode has a
  connecting clip. The supplied milestone clip means reply received here, never
  verified task success. One-shot playback ends at the actual media end, with a
  bounded fallback timer. Hidden/reduced-motion/custom-static modes stop playback.
  No audio output or microphone is created for the avatar player.

## Artwork provenance and packaging

The owner supplied `Atlas avatar widget package.zip`. Seven WebP stills, nine
square MP4 clips and its README (renamed SOURCE.md) were copied byte-for-byte to
`musebridge/assets/atlas`. The source ZIP remains in the ignored attachment
directory; call portraits remain available there and are not bundled into this
text companion. The artwork is opaque, so it is shown inside a rounded tile.
No background removal or replacement artwork was generated.

[Hash and package receipt](MB-37-assets.json): all 17 files match the uploaded
ZIP and the built wheel. Total bundled bytes: 8,022,008. Offline wheel build
completed successfully with the three new runtime modules and the full art set.
Native playback reported 5-second light clips and 8-second dark clips in the
observed set; the package README's five-second dark description is not used as
the playback clock.

## Checks and repairs

Full regression suite: **608 passed, 5 skipped in 292.62 seconds**. A subsequent
connection-status/replay refinement prevents a stale Connected label after
`Connection unavailable` and a new-reply animation for unchanged replayed
history. The affected companion, window, shell, workspace, overview, notification
and Teach checks passed **36 tests in 25.58 seconds** on that final code.
Eleven companion tests cover concurrent
Main/side sends, reversed replies/acknowledgments, newer/unknown-send drafts,
attachments, independent Main workspace/policy, saved Main startup, last-side
archive, Overview routing, hide/minimize/quit, oversized drafts, corrupt avatar
imports, lost-monitor clamping and connection loss.

[Native receipt](MB-37-native.json), from
`python tools/companion_probe.py --secondary-screen`: disposable offline profile,
no injected input or real account. Observed 39 light and 49 dark video frames;
widget and panel remained within the secondary screen, foreground was unchanged,
side selection and both drafts survived, reduced motion stopped playback, the Muse agent
remained visible while the harness was minimized, and closing stopped the worker
and hid every fixture window. QWidget-only preview captures are under
`.local/companion-review`; they contain synthetic data.

The existing idle application was closed normally and the final source build
reopened using the normal hidden-console launcher and saved pairing. Read-only
native inspection found the harness and the Muse agent visible, wholly on the secondary
display, in one UI process. The harness showed Connected. No pairing operation
or live test prompt was performed. [Sanitized app receipt](MB-37-live-app.json).

The initial native check found the panel's Windows title-bar/frame could extend
past the work area despite its client rectangle fitting. Placement now clamps
the actual frame bounds after the panel is shown; the repeated native check
passed. This is observed Windows placement, not proof of every mixed-monitor
scale/hotplug arrangement. Qt's [QVideoSink](https://doc.qt.io/qt-6/qvideosink.html)
and [QMediaPlayer](https://doc.qt.io/qtforpython-6/PySide6/QtMultimedia/QMediaPlayer.html)
references informed the silent playback adapter.

## Remaining limits

Main voice remains explicitly unavailable: the existing Main voice refusal and
dedicated-chat voice route are preserved. Main's shared stream still cannot
certify request completion or drive automatic continuation. No second model,
transport identity, startup service or public listener was added. Arbitrary GPT
sprite-sheet import, a new global shortcut and automatic OS reduced-motion
discovery are not implemented; the supplied MP4 set and manual reduced-motion
switch are supported. Main reply timing is not a verification receipt.

Owner review of size, animation and everyday interaction remains open. Live
Main/side message delivery for this new panel has not been exercised against
Muse; the concurrency evidence above is local, alongside earlier transport
acceptance. No test prompt, microphone call, skill-saving request or third-party
message was sent during this change.
