# MuseBridge 0.6.0 — five-capability delivery

2026-11-11. Current follow-up to [MASTERPLAN](../MASTERPLAN.md), MB-15/17/18/20/23. The [0.5 acceptance ledger](MB-23-integration.md) remains historical evidence. Development used root orchestration and at most two helper-model workers, as requested. Muse remains the only runtime reasoning agent; VM skills are not copied or replaced.

## Delivered behavior

| Capability | Implementation and evidence |
|---|---|
| Automatic visual feedback | `vision.status/observe`, local project grant UI, durable capture/delivery ledger and a Worker scheduler. Muse requests a screenshot, finishes its turn, automatically receives the image as a labelled new turn in the same chat, then continues using its new run ID. Browser capture uses the owned headless context; desktop capture requires a locally selected HWND/process and Desktop control. Six-image maximum per chain; pre-ACK run binding prevents budget resets. Actual two-image read/click/read loop passed. This is supported SDK `send_image`, not inline tool-result images or native steering. |
| PDF/Word reading | UTF-8/PDF/DOCX snapshots, bounded subprocess extraction, background preview/attach and cached successful text keyed by immutable artifact ID/extractor version. Original bytes/hash/export preserved. Actual Muse read distinct document markers from both formats. Main DOCX body/table text and PDF text layer are covered; no OCR, old `.doc`, macro execution or layout fidelity is claimed. Returned metadata states coverage. |
| Broader hooks/plugins | Before/after work-tool events with exact filters, alongside file-edit/verification/run-end events. Before checks gate the original tool; failure/pending/Stop cannot execute it. Inspect/discover/cancel controls remain available. Hooks run through supervised jobs without recursive router dispatch. Bounded versioned JSON context is passed in `MUSEBRIDGE_HOOK_CONTEXT`. Local plugin import previews commands and installs disabled hooks; remote tools/repository text cannot enable them. Actual Muse writes ran successful before/after hooks. |
| Terminal screen | Maintained pyte VT parser plus native Qt grid: cursor/color/truecolor, alternate screen restoration, Unicode, arrows/function keys/Ctrl chords and debounced fit-to-pane resizing. FIFO asynchronous input keeps the UI responsive and preserves key order; stale Stop/Resume admissions are refused. Real ConPTY VT/input fixture passed. Remote results stay sanitized text. This is a useful VT subset, not every mouse/graphics/host protocol or an attach to unrelated terminals. |
| Silent desktop notifications | Separate opt-in Overview setting. Native Windows notification-area API requests `NIIF_NOSOUND` and quiet-time behavior, with generic text, coalescing/rate limit and no startup-history replay. Only deliberate click raises the app/selects a conversation. Fresh icon IDs prevent delayed old clicks retargeting a new notice. Native Shell accepted a synthetic notification and its icon was removed. OS suppression/visibility and an owner click were not independently accepted. |

## Test evidence

Full local regression: **225 passed, 4 skipped in 36.84 seconds**. Subsequent affected document/UI/terminal checks also passed after coverage text and shutdown callback refinements (27 tests). The skipped cases still require Windows symlink creation permission; they are not passes.

```powershell
uv sync --offline
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe tools\preview_shell.py
```

New tests cover document bounds/cache/timeout/DTD rejection, hook pass/fail/filter/dedup/Stop, visual grants/scope/PID checks/budget/restart/cancellation, fake-SDK image continuation, pre-ACK chain binding, VT buffers/keys/colors/real PTY, native callback packing/icon cleanup and opt-in attention deduplication. Default tests use synthetic data and do not contact Muse or capture user windows.

Live and native receipts (ignored local fixture paths, retained for diagnosis):

- `.local/five-capabilities-1794459964/report.json`: `pdf_received`, `docx_received`, both pixel verifications, two automatic image acknowledgments, browser click, disabled plugin import and successful before/after hooks all true. No job-tool escape, zero unknown calls, `passed=true`. Codes existed in canvas pixels; document markers were in the selected snapshots. The result independently checks the produced files.
- `.local/notification-smoke-1794460045.json`: `shell_accepted=true`, `closed=true`, `clicked=false`. No transcript or command output was included; acceptance means API acceptance, not proof Windows visibly presented it during notification suppression.
- Owned-window PrintWindow smoke: target red pixels verified despite a second owned green fixture covering it; captured image 496×319. No unrelated app was captured and no foreground activation was requested. The local `window-capture-smoke-*.json` report records only dimensions/result.

## Safety and recovery exercised

- Automatic vision is off until locally enabled, pauses grants at restart, holds on switch/disconnect, cancels pending captures on Stop and never replays uncertain image sends. A new manual request cancels stale pending images. Pending manual image/document drafts are preserved by automatic continuations. Voice calls are outside this text-image adapter.
- A configuration change while capture is running is rechecked before queueing; it cannot leave an enabled deliverable. Policy revisions and Stop generation are rechecked before sending. An HWND/process mismatch is refused.
- Selected-window screenshots use target rendering through PrintWindow in an eight-second, 256 MiB worker, with bounded bitmap/output, pre/post identity checks and no ImageGrab/desktop-region fallback. Unsupported/all-black/timeout captures fail rather than sending an occluding app. Some GPU/minimized/protected windows may not support this method.
- PDF/DOCX workers have eight-second timeouts, fail-closed Windows memory limits, two-parser concurrency and bounded input/pages/text/archive/output. DOCX entity declarations are refused. Transient extraction failures are not cached; cached text cannot change the original snapshot.
- Hooks require explicit local enablement. Before checks wait at most eight seconds per tool call; if still running, the original tool is not executed and the returned instruction identifies the job. Repeating identical arguments in that remote run reuses the precheck. Commands are not an OS sandbox; they retain the current user's permissions.
- VT rendering never executes OSC clipboard/hyperlink or terminal-host control commands. Unknown terminal input is not resent. Queued local input captures admission generation/profile revision, so a quick Stop/Resume cannot revive it.
- Desktop notification service is inert by default and removed on shutdown/disable. It does not change Windows notification/privacy settings, install startup behavior or play an application sound.

## How to use

1. Select a project conversation. Conversation actions → **Automatic visual feedback…** → enable the owned browser and/or choose one allowed window → Apply. Desktop source also needs **Desktop control**. Ask Muse to use `vision.observe` when an image check is needed.
2. **Artifacts** → Import PDF/DOCX → Preview or Attach to message. Send normally; Muse reads it with `artifact.read`. Unsupported/scanned/encrypted content gets an explicit limitation.
3. Conversation actions → **Local lifecycle hooks…** → configure a filtered hook, or import/review plugin JSON and enable individual entries. Results remain inspectable through the dialog/Activity/jobs.
4. **Terminal** → New PowerShell → click the screen to type. Fit-to-pane defaults on; disable it to enter manual dimensions. Stop Work still revokes old queued input.
5. **Overview** → enable **Silent desktop notifications while MuseBridge is open**. In-app attention remains an independent option.

No ESP32 flash, re-pairing, local model, public listener, scheduled task or unrelated application change was needed. Native fork/remote-worker/compaction APIs remain outside the pinned SDK. Earlier real speech, arbitrary Windows-app task and long recovery/physical-board acceptance remain open; these five additions do not substitute for those owner journeys.

Final deployed-window observation (2026-11-12, MB-24c): the finished-job startup regression was reproduced and repaired in 0.6.1. The normal launcher opened one window; the existing documented --connect invocation then reached Connected. Native accessibility independently observed exactly one MuseBridge window, Connected, and all nine tabs. No foreground screenshot, microphone or re-pairing was used. Full regression after the repair: 226 passed, 4 skipped. The app was left open and connected at this item boundary; live evals may subsequently close it under the same identity lock. Current work continues in HARNESS-UPGRADE-PLAN.md.
