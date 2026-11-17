# MuseBridge interface

Updated 2026-11-16 for the MuseBridge 0.8.0 candidate, contract 2 and skill 2.

The interface retains the pale workspace sidebar, white conversation pane, blue accent, compact header/tabs, readable transcript and rounded composer. Muse remains the remote agent; this is a native Qt client with no local model picker.

## Design rules

Shared styles and line icons live in `musebridge/presentation.py`; shell construction and behavior live in `shell.py` and `shell_actions.py`. Keep runtime/tool execution in feature modules. Use visible text for primary actions and accessible names for icons. Show only functional destinations, with explicit disabled, focus, selected and error states.

## Current interface

MB-37 compact revision (November 16): Main's default surface is a small composer
under the transparent Muse agent cutout. Idle hides controls/status text; hover shows
chat, voice, activity and options. The plus menu exposes actual attachments and
Main tools rather than a simulated plugin catalog. Escape preserves the shared
Main draft; the outward arrow opens full history/workspace/policy. Empty margins
are excluded from the widget's input region. The original opaque portrait videos
are optional; reduced motion is still. Voice controls name a captured dedicated
conversation and provide Start, mute, interruption and Hang up; view selection
cannot retarget/end it. Owner checks prepares a disposable native Notepad and
spoken-test session with independent file checks and owner observations.
[Current evidence](../evidence/MB-37b-compact-and-owner-checks.md).

The workspace sidebar groups project chats and offers search, local archive/restore, and new chats. Chat, Activity and Plan remain the primary tabs. The composer includes project access, goal continuation, Stop Work, voice and send. The project tools expose Changes, Queue, Terminal, local Hooks and MCP configuration; availability depends on selecting a project conversation. Queue entries are visibly ordered and status-tagged, and terminal sessions expose text input/output controls.

Continuity notes and handoff are project/chat tools: notes are source-linked, corrections supersede, and handoff is clearly labeled as a summary-seeded new conversation. It does not imply native remote fork or transcript transfer. The live continuity probe verified note recording, handoff tool call, seed delivery to the new chat, and consumption only after acknowledgment; this remains explicit summary-seeding rather than hidden-state branching. Git worktrees open in separate project conversations; inspect changes before proceeding.

Application -> Teach Muse this PC previews a normal skill-saving request and records owner confirmation separately. The composer workspace control can attach a folder to the existing chat. Conversation actions -> Checks after file edits provides owner-only command/glob/enabled controls; it is off by default and separate from the general hook editor. No settings save executes a check.

Conversation actions -> Finish-line verification provides a separate project check command and Auto-check after changes checkbox, off by default. Changes marks tracked edits as "not checked since last edit" until a successful command covers their revision. A single optional follow-up appears in Queue after an attributed request settles; pending prompts obey existing holds. Resume pending remains available on an empty held dedicated-chat queue so future prompts can be allowed after restart. Settings save neither executes a check nor resumes the queue.

## Limits and review

Selecting another chat changes the view while background requests/queues/visual feedback continue. Active chat rows show Working. Up to three dedicated project chats plus Main share the connection. Archive holds future queued/visual sends; restore does not resume them. Stop Work remains global; disconnect/restart and uncertain delivery hold automation. Shared Main completion cannot drive automatic continuation. Terminal rendering uses a native pyte VT screen with journaled keyboard input and debounced fit-to-pane resizing. Hooks require explicit local configuration and imports start disabled. MCP is local stdio with explicit grants and no OAuth. Native fork, steer, worker delegation and compaction APIs are unavailable in the pinned SDK. Speech, real desktop and owner usability acceptance remain separate.

Changes combines direct checkpoints and eligible shell-change snapshots, suppressing equivalent duplicate rows. Restore retains later-owner-change refusal and shows unavailable/partial evidence honestly. Settings includes Windows OCR fallback (on by default), separate from desktop action permission. Teach now previews skill version 2; confirmation is recorded only after Muse reports saving it and the owner records that confirmation.

The live workbench probe receipt is [MB-19–21 workbench evidence](../evidence/MB-19-git.md); individual feature receipts document observed limits. The current expansion authority is [MASTERPLAN](../MASTERPLAN.md).

## Artifacts and cross-chat overview

Artifacts is a conversation-scoped library of immutable snapshots. Import, preview, attach, or save a copy. UTF-8, PDF and DOCX text can be read; parsing is isolated/bounded, successful extraction is cached, and original bytes remain exportable. Unsupported binary formats have no executable preview. Text-document references are included with the next manual send. Existing image attachments use the image route. Screenshot outputs can be attached explicitly. The Automatic visual feedback settings dialog additionally grants bounded, labelled image continuations for the owned browser or one explicitly chosen desktop window; grants pause on restart. Pending document attachments block queueing.

Overview lists actual latest runs, local jobs, queued/uncertain states, queue mode and goal state across conversations. The in-app attention badge and silent Windows notifications are separate opt-in settings. Desktop notices contain generic text, respect quiet-time behavior, never steal focus, and open the relevant conversation/Overview only when clicked. Do not equate remote turn completion with verified task completion.
