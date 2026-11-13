# MB-17 — Request queue

> This receipt records the 0.5 baseline. Its old limits are superseded where changed by the [0.6 five-capability receipt](FIVE-CAPABILITIES-0.6.md).

Implementation: `musebridge/request_queue.py` and `queue_ui.py`. Ctrl+Shift+Enter queues a follow-up; the queue page displays order, delivery state and run reference, with pending edit/cancel and hold/resume controls. Requests deliver in order after the current turn settles. Queueing is restricted to a selected dedicated conversation; main chat is disallowed. Restart and Stop Work hold the queue. Unknown/uncertain outcomes are not automatically replayed.

The live workbench probe recorded queue delivery, waiting for the first terminal turn, and verification of queued files (`queue_delivered`, `queue_waited_for_first_terminal`, `queued_files_verified` true). Inspect held/uncertain entries before resuming.

An integrated follow-up found that some actual Muse turns emit a finished answer without task-status events. Requiring a task terminal stranded the next request. The controller now uses the pinned SDK's completed bound reply plus quiet/idle rules and records `remote_reply_finished` distinctly; empty silence, foreign or unfinished replies and running tasks still cannot advance it. Three real queued requests subsequently passed with states `remote_finished`, `remote_reply_finished`, `remote_reply_finished`: all acknowledged, all ordered completion boundaries valid, third file independently verified, zero unknown calls. See [MB-23](MB-23-integration.md) for the exact sanitized report.

Overview lists cross-conversation work without reading command output into its periodic summary. Attention is opt-in, silent and in-app. No OS toast, native steering or concurrent remote goal dashboard is claimed.
