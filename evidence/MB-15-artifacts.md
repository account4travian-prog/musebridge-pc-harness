# MB-15 images — 2026-11-11

> This receipt records the 0.5 baseline. Its old limits are superseded where changed by the [0.6 five-capability receipt](FIVE-CAPABILITIES-0.6.md).

Implemented one-image selection, clipboard paste, preview, removal, persistent per-conversation image drafts and acknowledged transcript preview links. The control transport sends the validated bytes through the pinned SDK's image route with text/project context and the selected remote chat. Image-only messages are supported. Uncertain sends retain the draft and do not replay; acknowledging an older image cannot clear a replacement draft.

The helper model supplied immutable image admission and format/decode bounds; root integrated storage, Core/Worker and Qt UI. Formats: JPEG/PNG/GIF/WebP, SDK limit 2 MiB, application limit 20,000 pixels per dimension and 40 million cumulative decoded pixels. These are decode/work bounds, not claims of universal image safety. No silent resizing/re-encoding of selected files occurs. Clipboard images are explicitly encoded as PNG when pasted.

Testing exposed different Windows lstat/fstat creation/change timestamps for an unchanged file; identity comparison now uses inode/device, size and write time. The regression also still detects a real file change during the bounded read. Format mismatch, corrupt/truncated data, decode bounds, regular-file checks, draft persistence, conversation/text routing and uncertain-send handling are tested.

Real vision passed in `.local/expansion-probe-1794394606/report.json`: a random six-digit code existed only in generated image pixels, actual Muse received the image and wrote the exact code into vision.txt. The report contains only the pass/fail observation, not private screenshots/transcripts. It also verifies PC edits, command exit status and tracked changes.

**Scope boundary:** the artifact library now stores immutable conversation snapshots; it does not provide PDF/Word extraction or automatic selected-window screenshot-to-model delivery. Screenshots are catalogued only when within image bounds and require explicit owner preview/attachment. Earlier explicit-image vision proof does not establish automatic screenshot delivery. Image and checkpoint content is local user data under the app state directory, not DPAPI-encrypted credential data.

## Artifacts, image pixels, and screenshots

The conversation artifact store admits immutable snapshots up to 8 MiB each and 64 MiB total per conversation. Supported UTF-8 text extensions are limited to 256 KiB and can be read in pages up to 10,000 characters. Other formats remain binary: preview reports unavailable extraction and Save copy exports the original bytes. No executable preview is offered. Text document drafts are included as references in the next manual send; queue submission is refused while such drafts remain. Images require explicit attachment through the established image-send route.

Actual document delivery passed in `.local/artifact-probe-1794457200/report.json`: the source was edited after import, Muse used `artifact.read` and wrote the original snapshot contents exactly, and the draft was consumed only after acknowledgment. No token or file contents appear in this receipt. Default tests also verify unknown-send draft retention, wrong-chat refusal and no implicit queued/goal document attachment.

Screenshot output is catalogued only when within image bounds and can then be previewed/attached by the owner. There is no automatic screenshot vision loop; the pinned SDK image method creates a user turn. Earlier MB-15 image proof applies to explicit image delivery. The artifact library now supplies document snapshot storage, but does not implement PDF/Word extraction.

## Overview and attention

Overview summarizes latest recorded run state, active local jobs, pending/uncertain queue state, queue mode and goal state across conversations. Its opt-in attention indicator is a silent in-app badge for new completed/failed work, not an OS notification. Remote turn completion remains separate from task verification.

