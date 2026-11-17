# MB-37b compact companion and ready owner checks - November 16, 2026

Authority: [MB-37](../HARNESS-UPGRADE-PLAN.md#mb-37-floating-the-claude-agent-companion).
The owner supplied five desktop comparisons and requested closer interaction
parity, then said the real voice/Notepad/other checks could be set up now.

## Delivered changes

| Reference behavior | MuseBridge behavior now |
|---|---|
| Quiet idle pet | Transparent Muse agent cutout, gentle bob when motion is enabled, small handle; no permanent white card or status text. |
| Hover controls | Compact chat, voice, side-chat activity and options strip; collapses after leaving. |
| Small chat composer | Click opens Main quick chat. It shares Main's draft, attachment state, send admission and acknowledgments; Escape preserves the draft. Latest replies stay bounded; full history opens through the outward arrow. |
| Plus menu | Image/file attachments, removal, Main workspace/permissions, actual available tools and voice controls. No fabricated plugin catalog. |
| Voice access | Explicit dedicated-chat selector with Start, mute, interruption and Hang up. Main voice refusal remains. The controller captures its conversation before awaits; view changes neither retarget nor hang up the call. |

Transparent margins outside the avatar/visible controls are excluded from the
window's input region. The original light/dark MP4s remain available through
**Portrait video animations**; their opaque backgrounds are not described as
transparent animation. Stop Work remains global. Voice start stops only a goal
in its captured conversation; an unrelated active goal is preserved.

## Owner check preparation

Application and the Atlas options open **Owner checks**. Preparation creates a
fresh disposable project and unique empty Notepad input, enables Desktop control
only for that project, stages the desktop prompt, and opens the named file.
Discovery verifies the Notepad process and unique document marker before moving
that window to the secondary display without z-order/focus changes and granting
its screenshot access. A changed/unplaced window is refused. No prompt is sent
and no microphone is opened by preparation.

The real setup was observed through the native accessibility tree: status
**Ready**, the prepared Notepad selected, and secondary-display placement
confirmed by the application. [Sanitized setup receipt](MB-37b-owner-setup.json)
records the unique marker, empty input, absent result files and staged draft.
The current [walkthrough](../docs/OWNER-ACCEPTANCE-2026-11-16.md) gives exact steps.
Owner observations remain unchecked. Saving them records mechanical file checks
separately from actual observed typing/speech/audio/Stop behavior.

## Verification

- Full regression: **619 passed, 5 skipped in 302.60 seconds**. After making the
  two temporary control windows normal targetable windows, the affected compact
  UI, owner setup and voice checks passed **10 tests in 6.96 seconds**.
- [Native secondary-display probe](MB-37b-native.json): transparent/quiet idle,
  visible hover controls, shared quick draft, light/dark video frames (47/50),
  reduced-motion stop, preserved side selection and both drafts, minimized
  harness visibility, unchanged foreground and full fixture shutdown all passed.
  It used a disposable offline profile and injected no desktop input. Synthetic
  preview captures are under `.local/companion-review-v2`.
- Unit/integration regressions cover quick Enter delivery to Main, bidirectional
  draft synchronization and late acknowledgments, attachment isolation, actual
  hover/Escape controls, voice target capture across selection changes, refusal
  of Main/uncreated voice threads, no automatic mic start, fresh owner-result
  files, idempotent setup and the single intended Notepad grant.
- Offline wheel build contains current source and the cutout byte-for-byte.
  [Bundle/code/asset hashes](MB-37b-bundle.json). Earlier 1.0 frozen measurements
  retain their original scope; no release/merge or new physical acceptance is claimed.

## Cutout provenance

The built-in imagegen tool performed a background-extraction edit of the supplied
`stills/atlas-still.webp`. Project output is
`musebridge/assets/atlas/atlas-cutout.png`, 1254 x 1254 RGBA with alpha range 0-255.
Original portrait/video files and the generated original are retained. This is
an edited cutout, not a claim of byte-identical foreground extraction. The final
prompt was:

> Use case: background-extraction. Edit target: the attached Muse agent wizard-cat portrait. Remove ONLY the pale white background to genuine transparent alpha for a desktop floating companion. Preserve the exact cat identity, watercolor illustration style, whole body and pose, navy wizard hat and cloak, glasses, gold geometric emblem, staff and plumb line. Do not redesign or add any details. Keep all ears, hat tip, staff ornament, feet and dangling plumb line intact. No outline, halo, glow, ground shadow, text, panel or checkerboard. One complete character centered with small transparent padding.

## Still pending

Owner Notepad actuation/save, actual microphone/speaker behavior, skill-v2
confirmation, everyday-use verdict and new quick-panel live-message acceptance
are pending. The owner is available; this replaces the earlier deferral, not
the need for observations. No message, skill-saving request or microphone call
was started by Codex in the live session. Tests are local/native evidence and
preparation is ready; neither substitutes for owner results or the unmet 1.0 gates.
