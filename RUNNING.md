# Running MuseBridge

Open **MuseBridge Harness** from the desktop shortcut or Windows Start search. These entries open the current build on the secondary screen and connect using the saved pairing. If the harness is already open, the shortcut restores that window instead of creating another gadget connection. No startup-at-login service is installed.

`Install-MuseBridge-Shortcuts.ps1` creates or repairs those two current-user shortcuts. The alternative is `Start-MuseBridge.ps1 -Connect` in this project folder. The PowerShell launcher keeps its offline default when `-Connect` is omitted; then use the in-app connection indicator. Both launch methods keep the console hidden and use the secondary screen by default. Development can use `pythonw -m musebridge.app --secondary-screen --connect`. No public listener is installed; existing-window activation uses a current-user local pipe.

For development on the secondary monitor, use `pythonw -m musebridge.app --secondary-screen` (add `--connect` when needed). Placement happens before the window opens, avoids activation, and is saved on normal exit. This option exits without showing a window if no secondary monitor is available. Development and evaluation windows must stay on the secondary display, per the owner's 2026-11-12 instruction; browser fixtures run headlessly.

## Projects and conversations

The current development build adds `shell.run` for commands and `shell.job` for
their running handles. Quick commands return output and exit code together;
commands exceeding 25 seconds continue as owned jobs. PowerShell, cmd and installed
Git Bash are supported. Existing `job.*` tools remain available during the upgrade.
Shell results are short by default; use the returned log handle for retained
output. Discarded middle output cannot be recovered. See the
[shell upgrade receipt](evidence/MB-26-shell.md) for validation and limitations.

Create a project from Application → New project or the folder icon. Each project has separate conversations; the main Muse chat has no project file/shell scope. Instructions, drafts, plans, local projections and layout persist. Local transcripts do not replace Muse's remote history. See the UI for chat archive, search, export, Activity and Plan workflows.

## Harness features in 0.6.0

- **Continuity:** project-scoped, source-linked decision/constraint/progress notes are append-only; corrections supersede prior notes. A handoff creates a new chat seeded with a bounded summary of plan, goal, tasks, verified checks, tracked changes and effective notes. It does not copy the transcript or hidden Muse state. The seed is consumed only after an acknowledged send. If local transcript projection or run tracking fails after server acknowledgment, MuseBridge retains the known acknowledged delivery, flags local tracking for inspection, and holds the queue; it does not report a false send failure or resend the message.
- **Request queue:** Ctrl+Shift+Enter queues a follow-up in a dedicated project conversation. Requests deliver in order after the active turn settles. Restart and Stop Work hold delivery. Main chat cannot run the queue; uncertain outcomes are held and never automatically replayed. Review/edit/cancel pending items before resuming.
- **Git:** project tools inspect status, diffs and branches and create isolated worktrees for separate conversations. They do not commit or push. Existing dirty work is protected; inspect the resulting worktree and current status.
- **Interactive terminal:** owned PowerShell runs in a ConPTY session with a native VT screen, colors, cursor positioning, alternate screen and journaled keyboard input. Click the screen for arrows/function keys/Ctrl chords. Fit-to-pane is on by default; turn it off for manual rows/columns. Uncertain input is not resent. This implements pyte’s VT subset, not every terminal protocol; mouse applications, images and host-control commands are not promised.
- **Hooks:** explicitly configured PowerShell hooks can run before/after work tools, after file edit, before verification, or when a run ends. Use exact tool filters; status/discovery/cancel controls stay usable. Before-tool checks gate execution (up to 8 seconds per call; a pending check prevents the original operation). Hook jobs bypass remote routing, preventing recursive hook dispatch. Commands receive bounded JSON metadata in `$env:MUSEBRIDGE_HOOK_CONTEXT` and run as your Windows account. Import hook-plugin JSON through Local lifecycle hooks, review its commands, then enable individual imported hooks; imports start disabled.
- **MCP:** configure local stdio servers and explicit per-tool read/project-write grants. Muse cannot enable servers or grant access. No OAuth flow is provided. Close integrations when finished.

These tools operate on the selected project, but that selection is not an OS security sandbox. Inspect commands, grants, hook configuration, Git changes and terminal input before use.

## Existing workflows

**Plan** edits persistent Markdown and goal state. Continue goal permits up to five explicit continuation rounds and stops on uncertainty, pause, blocking, disconnection or limit. Jobs shows journaled process output. Stop Work pauses new tools, cancels owned process trees, pauses goals and ends voice; disconnect does not necessarily stop an admitted job.

Browser tools use temporary headless Chromium contexts, separate from signed-in browsers. Desktop tools expose guarded UIA/window actions; invocation is not proof of desired result. Voice uses a separate SDK transport. Real speech, audio behavior and desktop-task acceptance remain pending owner review.

## Pairing and state

The already-paired board remains the pairing route; normal use needs no reflash or re-pair. Profile discovery can reuse an existing cached MSIX profile. Credential/identity material is protected with Windows DPAPI; harness databases in the user profile are not DPAPI-encrypted. Keep that profile private. No telemetry or public listener is implemented.

State normally lives in `%LOCALAPPDATA%\MuseBridge`. A launch from packaged Codex can resolve to its existing `Packages\OpenAI.Codex_*\LocalCache\Local\MuseBridge` location instead. Discovery reuses that directory directly, without copying credentials or creating a second identity. If multiple distinct profiles exist, startup refuses to merge them; set `MUSEBRIDGE_DATA_DIR` to the intended absolute directory before launching. Do not erase pairing/state to fix a path mismatch.

## Checks after file edits

Conversation actions -> **Checks after file edits...** lets you save a PowerShell check command and a project-relative glob (for example `**/*.py`). It is off by default; saving does not run anything. An enabled match uses the existing owned hook job with a ten-second timeout. Failed checks return their last twenty lines to Muse and leave the edit saved. Syntax warnings are best effort for small supported files; no warning is not proof the file is valid. See [edit-feedback limits and tests](evidence/MB-31a-edit-feedback.md).

Conversation actions -> **Finish-line verification...** saves a separate check command and the optional **Auto-check after changes** setting, off by default. After an attributed dedicated-chat request settles with unchecked file-tool edits, it queues one prompt asking Muse to run that command and fix failures or explain why it does not apply. Changes shows "not checked since last edit"; `bridge.context` reports `unverified_changes`. A successful `shell.run` must finish with exit 0 after the edits; a task check needs an accepted verification receipt. Older, failed and still-running checks do not clear newer edits. A successful command alone does not certify the task's meaning.

The prompt shares the existing Queue and its holds. After restart, Stop Work, disconnect or a chat switch, use Queue -> **Resume pending** when ready; this button also enables future prompts when the queue is empty. Saving settings does not resume work. There is at most one finish-line prompt per user request, including its screenshot and goal continuations; it occupies an existing goal round within the five-round cap. Main remains excluded. Shell-only file changes are not detected by this tracker until MB-32. See [finish-line evidence](evidence/MB-31b-finish-line.md).

## Development

Main now uses the dedicated `C:\Users\the owner\Documents\MuseBridge\PC-Workspace` folder. Click the workspace button beside the composer to attach an existing workspace or choose a new folder for the same chat. Rebinding keeps the conversation and draft, refuses active local writes/jobs, and retires old requests. Commands run with your Windows user permissions; the folder is not an OS sandbox.

Application -> **Teach Muse this PC...** previews a short request to fetch the Windows skill and save it on Muse's VM. Click **Send skill request** once. When Muse confirms it actually saved version 1, reopen the dialog and click **Muse confirmed it saved this skill - record confirmation**. Merely sending the request does not mark the skill installed. Your composer and pending attachments are preserved.

The first outgoing message per connection or scope change includes a short PC header; ordinary follow-ups send your text unchanged. Muse reads the full Markdown context through `bridge.context`, including saved project instructions and guarded `AGENTS.md`.

Live Main-chat command execution is verified. Main's current upstream stream omits request linkage on replies and task status, so the harness cannot safely certify that a particular Main request finished. Use **New conversation** in the PC workspace for work that needs automatic goal/queue continuation. The existing Main chat remains usable for manual requests; a visible reply and successful command are separate from a verified finished goal.

```powershell
uv sync
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe -m pytest -q tests
```

Current test counts and live results are in the [0.6 receipt](evidence/FIVE-CAPABILITIES-0.6.md); the [MB-23 integrated receipt](evidence/MB-23-integration.md) records the earlier 0.5 baseline. Feature evidence and limitations are in [evidence](evidence/) and the current [MASTERPLAN](MASTERPLAN.md). No native Muse fork/steer/worker/compaction API is available through the pinned SDK; the handoff is an explicit summary-seeded new chat.
## Artifacts and attachments

The Artifacts tab stores immutable conversation-owned snapshots (8 MiB each, 64 MiB raw files per conversation). UTF-8 text, PDF and Word `.docx` documents can be previewed/attached and read by Muse in pages. Document parsing runs in bounded background processes: at most 100 PDF pages, 256 KiB extracted UTF-8, 8 seconds, two parsers and a Windows memory cap. Successful text is cached against the immutable snapshot; export preserves original bytes and SHA-256. Encrypted/scanned/unsupported documents return a clear limitation; no OCR, old `.doc`, macros or executable preview is provided. Pending document references go only with the next manual send, and block queueing until sent/removed.

Images still support explicit selection/paste. For a text-chat visual task, open Conversation actions → Automatic visual feedback, enable the project’s owned browser and/or choose one specific desktop window, then Apply. Desktop capture additionally requires Desktop control. Muse uses `vision.observe`, finishes its current turn, and automatically receives the image as a labelled new turn in that same chat. It continues with the newly supplied run ID. A chain permits at most six images; no inline-tool-image or native steering API is implied. Stop cancels pending images, switching/disconnecting holds them, restart pauses grants, and uncertain sends are never replayed. Review/resume from that settings dialog. The voice channel does not use this text-image continuation adapter.

## Overview

Overview lists runs/jobs/queues across conversations. Its desktop-notification checkbox is off by default. If enabled, generic completion/inspection notices use Windows notification-area balloons with an explicit no-sound flag and quiet-time behavior; Windows may suppress them. They never include transcript/command contents or raise the app automatically. Clicking a notice opens its conversation/Overview. Delivery is coalesced/rate-limited; startup history is not replayed. Closing the app removes its icon. In-app attention indicators remain a separate option. A completed reply is separate from verified task success.
