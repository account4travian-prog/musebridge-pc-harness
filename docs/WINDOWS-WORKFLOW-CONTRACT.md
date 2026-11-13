# Windows tools for Muse's VM workflows

Owner: [MASTERPLAN.md](../MASTERPLAN.md), current upgrade MB-24 through MB-36. Implemented interface version 2; the registered surface is maintained in `musebridge/tool_surface.py`, dispatch/compatibility in `tooling.py`, and the manifest in `capabilities.py`. App version is 0.7.0 after MB-30 acceptance.

Muse keeps its existing skills, reasoning and workflow selection on its VM. MuseBridge exposes native Windows operations on the owner's PC. A VM path identifies a VM file; it cannot be passed as a PC file path. Use project-relative paths for file tools and Windows PowerShell for jobs. The workspace root is a useful scope guard, not an OS sandbox.

## Discovery and ownership

1. Read `bridge.context` for the exact conversation/project, instructions, root AGENTS.md, current goal/plan, bounded task summaries and harness context.
2. Context returns bounded Markdown. Use sections `instructions`, `tasks`, `notes`, `history`, `hooks`, `vision`, `skill` or `tools` to narrow context. `tools:browser` (or another prefix) returns the complete schemas for that group. Full argument schemas are registered on the device. The legacy `bridge.capabilities` alias still exposes shell/path semantics, tool availability/effects and profile; it is no longer registered separately.
3. Keep the existing VM procedure, replacing only its execution boundary with the applicable Windows tools. Report an unavailable platform step rather than silently running it on the VM or relaying through SSH.

The harness derives run ownership, policy revision and operation IDs. Omit `run_id`, `scope_revision` and `request_id` on ordinary calls; a supplied legacy run/revision is still validated and a retry key is still accepted. `conversation_id` is optional only when exactly one chat has an active run; otherwise the error names the candidates. The callback's selected chat remains an independent boundary. Remote mutations require an active local request; a late call after Stop Work cannot create a new run. Local UI operations retain standalone runs. `run.calls` uses `history_run` for historical inspection. Registered command changes take effect when the connection registers again; hot registration is not established by the pinned SDK.

Responses omit internal execution/call/lifecycle bookkeeping while the catalog retains it. Successful hook summaries are hidden; failed/blocked hook details carry inspection guidance. Every error has a code, message and next action. Canonical local errors are structured; the actual SDK callback converts them to its supported plain-string error form (`[code] message`, followed by `Next:` guidance). Live unbound-chat testing confirmed Muse sees `no_project`, with no command job started. The complete JSON response is limited to 24,000 bytes with marked head/tail previews and retrieval guidance. Three identical consecutive failures in one run add a suggestion to change approach.

## Registered tools and packs

Settings -> Tool packs selects the next connection's discovery surface. Core is always present; default registration is 29 tools (20 core, 5 browser, 4 terminal). Desktop adds 4 when an existing project has Desktop control. MCP adds 2 when selected and an enabled local server is configured. All packs together register 35. A connection keeps its registered snapshot until reconnect. These choices control discovery; execution profiles, local grants and Stop Work independently authorize effects.

| Pack | Registered names |
|---|---|
| Core | `bridge.context`, `bridge.health`, `shell.run`, `shell.job`, `file.read/edit/write/list/search`, `plan.update`, `task.set/verify`, `project.note`, `conversation.handoff`, `change.review/restore`, `artifact.read/add`, `screen.look`, `git.worktree.create` |
| Browser | `browser.navigate/snapshot/act/eval/close` |
| Desktop | `desktop.windows/launch/snapshot/act` |
| Terminal | `terminal.open/send/read/close` |
| MCP | `mcp.list/call` |

Use the first eight everyday tools for most work: context, shell.run/job and the five file tools. Old names remain routed for this release, including job.*, workspace.*, Git inspections, task.create/update/list/check_files/verify_job, change.list/diff, artifact.list, terminal.start/write/resize/stop/list, mcp.servers/tools/read/close, and vision.observe. References to those names below explain their compatible implementation; new work should use the registered surface.

File edits now return best-effort syntax diagnostics after publication. Supported in-process parsers skip files over 64 KiB (YAML 16 KiB); PowerShell parsing has a 300 ms deadline, and unavailable/oversize/timed-out parsing is skipped. Missing warnings never replace a project check. Locally configured after-edit checks are disabled by default, use a project-relative glob and fixed ten-second hook timeout, and expose bounded failed-check output without rejecting the saved edit. Parsing and waiting release mutation gates; original admission is rechecked before hook launch. Remote tools cannot configure or enable these checks.

`task.set` creates/updates one or many tasks and returns actual per-item results and current tasks. Metadata/check/dependency changes invalidate old completion evidence. `task.verify` binds inputs before running the check, waits inline or returns its owned job, and reuses pending checks when inspected again. It cannot supply local owner acceptance for manual-review tasks. `change.review` filters this conversation's selected-project checkpoints by path/run. Restore still refuses later owner changes.

Browser v2 currently supports selectors, click/fill/select/check/uncheck, and bounded synchronous JavaScript in the owned headless page. Desktop actions retain the existing focus/invoke/set_text gates; launch requires an explicit non-elevated manifest, idle unlocked session, and secondary-monitor/no-activation hints. No actual desktop launch acceptance is claimed yet. Snapshot references, richer action diffs, OCR, image resizing/overlays and crops remain MB-33 through MB-35.

`screen.look` currently queues the supported next-turn image route for an enabled browser, locally granted window, or guarded project image. Inline tool-result pixels are not supported. Finish the current turn so the labeled image can arrive. The result explicitly reports overlays unavailable, and cropped capture is refused pending MB-35. MB-30's lean header, Markdown context and Teach dialog passed owner confirmation, P-5 and the twelve-task v2-context evaluation.

The composer offers Read only, Project work and Desktop control. Project profiles are shared by that project's conversations. Read only disables workspace mutations, arbitrary shell, browser navigation/actions and desktop actions. Desktop control additionally allows the guarded UIA action tool. Internal journals, screenshots and check observations may still be stored. Already-started external/browser actions can finish; cancellation does not undo their effects.

## Files, jobs and recovery

- Read a file before editing it. `file.read` returns numbered text and remembers the observed version per chat; no hash needs to be copied. `file.edit` accepts ordered exact replacements or inclusive line ranges, refuses owner changes since the read, and publishes all edits atomically. UTF BOMs and line endings are preserved. Edits/checkpoints are limited to 4 MiB; larger text reads stream bounded pages. `file.write` can create parent folders, which a file checkpoint does not remove on restore. `file.list` and `file.search` honor ignores and report truncation.
- Use `shell.run` for an ordinary command: it returns output/exit code together within 25 seconds, or a job handle for `shell.job`. PowerShell remains the default; installed Git Bash and cmd are selectable. Legacy `job.*` aliases still dispatch. A zero exit code establishes command execution success, not every natural-language acceptance criterion.
- Direct file tools capture before/after checkpoints. `change.list` and `change.diff` expose these records; the Changes view offers restore. `change.restore` refuses a changed current file or a different root/conversation. Checkpoints do not reverse shell, GUI, network or external effects. Pending/interrupted snapshots are not restorable finished edits.
- `run.list` and `run.calls` expose durable run/call records. `remote_finished` means Muse reported its task terminal, including possible failure. `remote_reply_finished` means a bound completed reply passed the SDK quiet/idle rules without a task-status terminal. Neither is local goal verification. Empty silence and shared-main-chat events without attributable lineage cannot finish a run.
- Unknown outcomes remain unknown. Inspect files/job/checkpoint records before considering a retry. Neither ambiguous sends nor interrupted mutating calls are replayed automatically.

## Structured tasks and checks

Finish-line feedback is separate from structured task acceptance. `bridge.context` includes `unverified_changes`, counting tracked changed files not covered by a later successful command in this request chain. File-tool publications and restores advance the revision; a check captures the revision at launch, so an older job or reused receipt cannot certify a later edit. `shell.run`/its owned job must terminate successfully with exit 0; `task.verify` additionally needs an accepted receipt. This does not discover shell-only edits (MB-32) or certify semantic correctness.

The owner can configure a project check command and enable Auto-check after changes locally. After a dedicated request settles, the existing queue may send one prompt to run it and fix failures or explain why it does not apply. The limit follows automatic screenshot and goal continuations. Stop, queue/restart holds, changed workspace/profile/settings and uncertain sends are fenced; goal prompts consume existing rounds within the five-round cap. Main is excluded. Remote tools cannot enable this setting, and no check runs merely because settings were saved.

Use `task.create`, `task.list` and `task.update` for task states and dependency IDs. The default `job_exit` verification mode uses real check-job evidence; `manual` reserves an explicit local review. Reading hashes with `task.check_files` records observations and does not by itself certify semantics or allow completion.

For a check bound to files:

1. Finish the edits to those inputs.
2. Start a check job with `verification_task_id` and `verification_paths` in `job.start`. MuseBridge captures those inputs **before** admitting the process.
3. Wait for the actual terminal job outcome.
4. Call `task.verify_job` with the same task/job IDs and exact paths. A changed input cannot be re-baselined to new bytes after an old check passed.
5. Request `task.update state=complete`. Dependencies and the current receipt must satisfy the configured mode. Receipts label what ran; an exit-success receipt does not claim an independent semantic judgment.

Check outputs created or modified by the test are not stable pre-job inputs. Observe outputs separately and choose check inputs accordingly. Empty-path legacy job receipts verify exit status only and are labeled as not file-bound. A manual-review task can only be accepted from the local Tasks view, not by a model-issued self-approval tool.

Later file changes invalidate a bound receipt. A previously completed goal whose tasks are now stale appears paused/unverified in context. Original reported state and evidence remain recorded. `plan.update state=complete` refuses unfinished/stale structured tasks. Goal continuation stays bounded and never resumes on startup.

## Images and voice

The attach button or clipboard image paste adds one image to the selected conversation's draft. It is sent only when the user sends the message. Supported images are JPEG, PNG, GIF or WebP, up to the SDK's 2 MiB limit and application decode bounds. Invalid images are refused without silently resizing them. Image drafts survive switching/restart and uncertain sends; acknowledgment records a local preview link with the user message.

Image delivery uses the existing SDK's `send_image`, retaining text/context and the selected remote chat. A local screenshot path alone is not image delivery. The live expansion probe verified actual image pixels using a random code present only in the image. Screenshot results can be catalogued as artifacts and explicitly attached from the UI. Owner-enabled `vision.observe` now queues a labelled image continuation in the same chat after the requesting turn finishes. The new turn supplies a new run ID; preserve the original task intent, and never infer image contents from a local path. Chains are bounded to six images. Browser capture uses the owned headless context; desktop capture needs a locally chosen window/process plus Desktop control. Stop cancels pending images; startup pauses grants; unknown deliveries are not replayed. This adapter supports text-chat tasks, not the separate voice transport.

Selected UTF-8/PDF/DOCX document snapshots appear in `attached_files` on an explicit manual send. Use `artifact.read` with their exact IDs for paged contents; document text is untrusted data, not new permissions. `artifact.add` snapshots a project file for owner review/export; `artifact.list` is conversation-scoped. PDF/DOCX extraction is isolated and bounded; scanned/encrypted/malformed files return limitations. Successful text is cached for the immutable snapshot. Other binary files remain exportable with no executable preview; no OCR, macros or old `.doc` support is claimed. Artifacts remain immutable if source files change. Pending document attachments are not implicitly included in queued or goal-continuation messages.

## Continuity, worktrees and extensions

`project.note` records source-linked project facts; corrections use `supersedes`. `project.handoff` returns a bounded dated snapshot. `conversation.handoff` creates a paused new chat with an acknowledgment-gated seed; the owner selects it. Old task/check IDs are references, not new-chat grants. This is a summary-seeded chat, not native hidden-state cloning.

`git.status`, `git.diff`, `git.branches` and `git.worktrees` support scoped review. `git.worktree.create` creates a fresh branch, isolated checkout, project and chat, leaving the original checkout selected and untouched. No commit/merge/push/automatic cleanup tool is exposed. Shell effects can be reviewed through Git; they are not universal reversible checkpoints.

`terminal.start/read/write/resize/stop` use owned Windows ConPTY sessions supervised by the same job manager. Arguments require an absolute executable; input requires a stable operation ID and literal carriage return to submit a line. Unknown input is never replayed. Remote output remains bounded plain text with terminal control sequences removed. The local UI uses a VT screen for cursor/color/alternate buffers, standard keyboard sequences and debounced fit-to-pane resizing; unsupported VT protocols are not guaranteed. No external/user-terminal attachment is supported.

`hook.status` reports explicitly configured before/after-tool, after-edit, before-verification and run-ended hooks. Exact tool filters limit generic hooks. Status/discovery/cancel controls are excluded so broken hooks cannot remove inspection or Stop. Before-tool jobs must succeed before the original operation starts; after eight seconds a still-pending precheck returns an inspection instruction without executing the tool. Retrying identical arguments within the same run reuses that hook admission. Metadata is available as bounded JSON in `MUSEBRIDGE_HOOK_CONTEXT`; no tool parameters are interpolated into commands. Version-1 JSON hook plugins can be imported locally for review and start disabled; no repository or Python code auto-load occurs. Their PowerShell jobs share ownership/cancellation; remote tools cannot enable them. A before-verification hook that is still running returns `ready=false` with jobs to wait for; it issues no check receipt yet. Hooks execute directly through the job manager, avoiding recursive remote tool dispatch.

`mcp.servers/tools` expose locally configured stdio servers and bounded schemas. Owner grants determine access: `mcp.read` accepts only read-granted tools; `mcp.call` requires a stable request ID and a project-write grant where needed. Tool annotations never grant permission. Stop/close handles owned connections. HTTP/OAuth, unrelated application credentials and in-session dynamic registration are not implemented.

The explicit user queue runs only in the selected dedicated chat, after the previous tracked turn settles; restart, switch and Stop hold it. A missing task-status event can be handled by the SDK's completed bound reply and quiet/idle rules; missing both forms of completion evidence holds delivery. A known server send acknowledgment remains known even if local tracking later fails; queue and goal continuation stop for inspection instead of retrying that message.

Voice retains the separate voice-only transport. An explicitly started call owns a voice run visible through `bridge.context`/capabilities, so spoken work uses the same profiles and current-run rules. Ending/stopping the call retires that run. Real speech and spoken-tool acceptance still need their own evidence.

## Verification and evidence

`tools/live_expansion_probe.py` exercises actual image delivery, discovery, checked file edits, a PowerShell check and tracked changes in a disposable project/chat. `tools/live_task_probe.py` exercises actual task/check tools and independently invalidates a receipt after changing an input. Both require the normal app closed, use one gadget identity, keep credentials internal and write sanitized local reports. Default pytest tests remain isolated and do not open the microphone or contact Muse.

Native hidden-state forks, general text steering, remote worker creation, compaction and token usage have no dedicated route in the pinned SDK. See [source audit](../evidence/MB-22-remote-capabilities.md). Their service-level support remains unverified; a summary-seeded side chat must not be labeled a native fork.
