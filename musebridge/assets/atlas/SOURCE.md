# The Muse agent avatar assets for the MuseBridge floating widget

Current avatar design (the wizard cat with geometric polyhedron emblem, glasses, and plumb line), packaged 2026-11-15.
All files are renamed copies of the originals in `~/workspace/avatars/` (source set `avatar-1794190217144080157-3*`); originals untouched.

## stills/ — WebP, 1600x1600, single frame
- `atlas-still.webp` — main portrait, light background. The default widget face.
- `atlas-still-darkmode.webp` — main portrait, dark background.
- `atlas-state-*-darkmode.webp` — dark-mode state portraits: idle, working, making-something, milestone, waking. Use as static fallbacks for the matching animation states.

## animations/ — MP4, H.264, 720x720, 24 fps, 5 s each, no audio
- `atlas-idle.mp4` — idle loop
- `atlas-working.mp4` — working state
- `atlas-making-something.mp4` — building/creating state
- `atlas-milestone.mp4` — milestone / level-up celebration

## animations-darkmode/ — MP4, H.264, 720x720, 5 s each, no audio
Same states on the dark background, plus:
- `atlas-waking-darkmode.mp4` — waking / connecting state (dark mode only)

## call-portrait/ — portrait crop for call-style layouts
- `atlas-call-idle-light.mp4` / `atlas-call-idle-dark.mp4` — MP4, 448x768, 25 fps, 8 s / 10 s idle
- `atlas-call-frame-light.png` / `atlas-call-frame-dark.png` — matching still frames, PNG

## Suggested widget state mapping
- Connected, waiting: idle
- The Muse agent working on a task: working
- The Muse agent building something (artifact/code): making-something
- Task complete / milestone: milestone (play once, return to idle)
- Bridge connecting / session start: waking (dark set), else idle

## Not included
- Dance videos (older September avatar design, 9:16, with-audio variants) — different, retired design; available on request.
- The September original avatar set (crescent emblem design) — superseded by this design on 2026-11-08.
