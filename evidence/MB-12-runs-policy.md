# MB-12 execution foundation — 2026-11-11

Implemented additive SQLite schema version 1 with execution scopes, runs and tool calls. A newer unsupported database version is refused before mutation. Old metadata/drafts/history/remote IDs are retained. Interrupted calls and open runs become unknown on process reconstruction, without replay.

The composer exposes shared project profiles: Read only, Project work and Desktop control. The router gates actual operations, including local restore and manual task review. Remote mutations additionally require current run_id and scope_revision; run ownership and admission generation/revision prevent cross-chat use and stale work after Stop/Resume or a profile change. Browser work queued on its dedicated thread rechecks its admission before dispatch. Already executing actions can finish; this is application policy, not OS sandboxing or reversal of effects.

Run attribution uses the pinned SDK turn tracker, handles pre-ack events and records remote_finished separately from local/goal verification. The main chat is shared: unlinked parentless task terminals do not finish the local run. Sends retain their original conversation while waiting for goal shutdown/send locks; a chat switch refuses the send and leaves its draft rather than sending it in the new chat.

Review reproduced and fixed two defects: an exception after a published file write was labeled failed instead of unknown, and a phone task terminal could finish a main-chat run. Both regressions pass. Mutation errors after dispatch now carry an explicit unknown outcome/inspect-before-retry instruction. Read failures remain ordinary failed observations. Routine event rows store tool names/IDs/states/error classes, not command arguments or transcript text.

Live discovery, scoped mutating tools and their durable receipts were exercised in `.local/expansion-probe-1794394606/report.json` with zero unknown calls. Hooks/MCP/PTY now use this boundary. Detailed results and remaining acceptance are consolidated in [MB-23](MB-23-integration.md). Voice has a scoped-call adapter but actual speech is not claimed verified.

The 0.5 live campaign also found reply-only turns with no task-status event. `remote_reply_finished` records a bound completed reply after the pinned SDK's quiet/idle rules, distinct from `remote_finished` task status and from goal verification. Silence alone, foreign replies, unfinished text and known running tasks cannot advance the controller. Local regressions and actual reply-only Muse proof passed.
