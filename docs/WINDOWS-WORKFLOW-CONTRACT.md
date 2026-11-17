# Windows tools for Muse's VM workflows

Owner: [MASTERPLAN.md](../MASTERPLAN.md), current upgrade MB-24 through MB-36. Implemented interface version 2; the registered surface is maintained in `musebridge/tool_surface.py`, dispatch/compatibility in `tooling.py`, and the manifest in `capabilities.py`. Candidate app version is 0.8.0, catalog schema 5, VM skill version 2. Release acceptance remains separate; see [closeout evidence](../evidence/HARNESS-1.0.md).

Muse keeps its existing skills, reasoning and workflow selection on its VM. MuseBridge exposes native Windows operations on the owner's PC. A VM path identifies a VM file; it cannot be passed as a PC file path. Use project-relative paths for file tools and Windows PowerShell for jobs. The workspace root is a useful scope guard, not an OS sandbox.

## Discovery and ownership

Local UI routing (MB-37, November 15): Main opens through the floating Muse agent
companion; the harness selects dedicated side conversations. Both share one
worker/identity. The Main panel sends with an explicit conversation target and
does not select or rebind a side chat. Main workspace/profile controls are in
that panel. This changes no remote tool schemas, the dedicated-chat limit or
Main's completion-attribution restriction. Main voice is still refused. Companion voice controls now capture an explicitly chosen dedicated chat before async readiness work. Viewing a different chat neither changes that voice target nor hangs up its call. The owner-check kit prepares a fresh project and one verified Notepad window; it does not send a request or start capture.

1. Read `bridge.context` for the exact conversation/project, instructions, root AGENTS.md, current goal/plan, bounded task summaries and harness context.
2. Context returns bounded Markdown. Use sections `instructions`, `tasks`, `notes`, `history`, `hooks`, `vision`, `skill` or `tools` to narrow context. `tools:browser` (or another prefix) returns the complete schemas for that group. Full argument schemas are registered on the device. The legacy `bridge.capabilities` alias still exposes shell/path semantics, tool availability/effects and profile; it is no longer registered separately.
3. Keep the existing VM procedure, replacing only its execution boundary with the applicable Windows tools. Report an unavailable platform step rather than silently running it on the VM or relaying through SSH.

The harness derives policy revision and operation IDs. A short ephemeral conversation_id/run_id binding accompanies each real connected request. With multiple active conversations, every tool except read-only `bridge.context` and connection health requires that matching pair; omissions are ambiguous and mismatches are refused. Single-active calls can infer it. Voice should use `bridge.context(conversation_id=...)` without an old run ID to discover its current binding. Scope revision remains internal; supplied legacy revisions/retry keys are validated. UI selection does not route callbacks. This is consistency protection, not authenticated caller isolation: the SDK supplies no trusted originating chat/request to its command callback. Remote mutations require an active local request; late calls after Stop cannot create one. Local UI operations retain standalone runs. `run.calls` uses `history_run` for historical inspection. Registered command changes take effect on reconnect.

Up to three dedicated chats plus Main share one gadget session with independent subscriptions, transcripts and completion tracking. Settled background subscriptions retire after 60 seconds. A lost stream holds that chat's automation and preserves uncertainty without replay. Per-project writer leases cover canonical aliases and overlapping roots; other writers receive `project_busy` until effects settle. Hooks also acquire this lease. The first write rebases its private snapshot after any prior writer, preventing restore from undoing that writer's changes. Separate worktrees permit independent writers. See [the routing decision and trust limits](PARALLEL-CHAT-ISOLATION.md).

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

Browser snapshots return bounded ARIA text and up to 150 scoped element references. Actions support click/fill/type/press/select/hover/scroll/wait/back/check and return concise snapshot differences, or a full snapshot after navigation/large changes. Explicit snapshots invalidate old handles; removed or wrong-chat references are refused. Filtering precedes the control cap. `browser.eval` returns narrowing guidance beyond 8 KB.

Desktop snapshots bind references to UIA runtime identities and window/process, report values/focus and support richer UIA/mouse/key actions. Idle, lock, foreground and elevation gates remain; mouse fallback rechecks immediately before input. Input coordinates use per-thread physical DPI context. Fullscreen foreground takeover and destructive close/lock chords are refused. OS-only OCR runs in a timeout/memory-bounded process when enabled; its text references are perception only. Real owner Notepad and mixed-monitor input acceptance remain open.

`screen.look` queues the supported next-turn image route for an enabled browser, locally granted window, or guarded project image. Finish the current turn so labelled pixels arrive. Machine images support reference overlays, validated original-image-coordinate crops `{x,y,width,height}`, longest edge 1568 and final encoding under 2 MiB. Original owner attachments remain unchanged. Inline tool-result pixels are not supported. Skill v1 was owner-confirmed; the updated version 2 requires a new Teach confirmation.

The composer offers Read only, Project work and Desktop control. Project profiles are shared by that project's conversations. Read only disables workspace mutations, arbitrary shell, browser navigation/actions and desktop actions. Desktop control additionally allows the guarded UIA action tool. Internal journals, screenshots and check observations may still be stored. Already-started external/browser actions can finish; cancellation does not undo their effects.

## Files, jobs and recovery

- Read a file before editing it. `file.read` returns numbered text and remembers the observed version per chat; no hash needs to be copied. `file.edit` accepts ordered exact replacements or inclusive line ranges, refuses owner changes since the read, and publishes all edits atomically. UTF BOMs and line endings are preserved. Edits/checkpoints are limited to 4 MiB; larger text reads stream bounded pages. `file.write` can create parent folders, which a file checkpoint does not remove on restore. `file.list` and `file.search` honor ignores and report truncation.
- Use `shell.run` for an ordinary command: it returns output/exit code together within 25 seconds, or a job handle for `shell.job`. PowerShell remains the default; installed Git Bash and cmd are selectable. Legacy `job.*` aliases still dispatch. A zero exit code establishes command execution success, not every natural-language acceptance criterion.
- Direct file tools capture before/after checkpoints. Private per-project bare Git run snapshots add eligible shell-made file changes without using the project's index/HEAD, filters or hooks. Exact bytes, nested ignores, final opened-handle paths and root/owner identity are checked. Files over 8 MiB, private/build/dependency/database files and links/junctions are excluded; candidates cap at 100000 and bytes at 256 MiB. Metadata caching reuses unchanged blobs and can miss same-metadata rewrites; restore compares actual current bytes against the fixed end tree. These are observed file differences, not authenticated attribution of external edits.
- `change.review/restore` and Changes expose run snapshots alongside direct checkpoints. Restore preflights all selected files and refuses changed current bytes or different roots/chats. Publication is atomic per file, not across files: I/O interruption may leave partial restore requiring inspection and deliberate retry. Ordinary filesystem compare/publication races remain. Missing Git falls back to direct checkpoints. Unknown/interrupted runs or runs ending with active shell jobs have no certified end snapshot. Recovery cannot reverse excluded files, installs, network/database effects or out-of-project changes. See [restore rehearsals](../evidence/MB-32-recovery.md).
- `run.list` and `run.calls` expose durable run/call records. `remote_finished` means Muse reported its task terminal, including possible failure. `remote_reply_finished` means a bound completed reply passed the SDK quiet/idle rules without a task-status terminal. Neither is local goal verification. Empty silence and shared-main-chat events without attributable lineage cannot finish a run.
- Unknown outcomes remain unknown. Inspect files/job/checkpoint records before considering a retry. Neither ambiguous sends nor interrupted mutating calls are replayed automatically.

## Structured tasks and checks

Finish-line feedback is separate from structured task acceptance. `bridge.context` includes `unverified_changes`, counting tracked changed files not covered by a later successful command in this request chain. File-tool publications/restores and eligible shell-only run-end differences advance the revision; a check captures the revision at launch, so an older job or reused receipt cannot certify a later edit. Duplicate direct/snapshot evidence is reconciled. `shell.run`/its owned job must terminate successfully with exit 0; `task.verify` additionally needs an accepted receipt. Snapshot discovery at run end can leave shell-only changes conservatively unchecked; no tracker certifies semantic correctness.

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

The explicit user queue runs in its captured dedicated chat after the previous tracked turn settles, including while another view is selected. Restart, disconnect, archive and Stop hold delivery. Missing task-status events may be handled by completed bound replies and quiet/idle rules; missing both forms of completion evidence holds delivery. A known server acknowledgment remains known even if local tracking later fails; automation stops for inspection instead of replaying that message.

Voice retains the separate voice-only transport. An explicitly started call owns a voice run visible through `bridge.context`/capabilities, so spoken work uses the same profiles and current-run rules. Ending/stopping the call retires that run. Real speech and spoken-tool acceptance still need their own evidence.

## Verification and evidence

`tools/live_expansion_probe.py` exercises actual image delivery, discovery, checked file edits, a PowerShell check and tracked changes in a disposable project/chat. `tools/live_task_probe.py` exercises actual task/check tools and independently invalidates a receipt after changing an input. Both require the normal app closed, use one gadget identity, keep credentials internal and write sanitized local reports. Default pytest tests remain isolated and do not open the microphone or contact Muse.

Native hidden-state forks, general text steering, remote worker creation, compaction and token usage have no dedicated route in the pinned SDK. See [source audit](../evidence/MB-22-remote-capabilities.md). Their service-level support remains unverified; a summary-seeded side chat must not be labeled a native fork.
