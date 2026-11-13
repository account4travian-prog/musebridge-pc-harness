# MB-16 — Continuity and handoffs

Implementation: `musebridge/continuity.py`; project tools expose source-linked notes and explicit handoff creation.

Notes are append-only decision, constraint, or progress records scoped to a project (or standalone chat), with bounded text/source and versioning. A correction supersedes one current note of the same kind in the same scope. Handoff builds a bounded source snapshot: project, plan/goal, tasks, current verified checks, tracked changes and effective notes. It creates a new conversation and does not copy transcript or hidden remote state. The seed is marked used only after an acknowledged send in the target conversation.

Live evidence: `.local/workbench-probe-1794455635/continuity-report-1794456441.json` records `note_recorded`, `handoff_tool_called`, `new_chat_received_seed`, and `seed_consumed_after_ack` all true, `unknown_calls=0`, and `passed=true`. An earlier workbench run ended before this portion; the later continuity probe closes that evidence gap. A handoff is a new summary-seeded chat, not native fork or hidden-state transfer. The send-acknowledgment UUID handling has a regression fix: after server acknowledgment, local projection/tracking errors keep the send known-acknowledged and flag local tracking; the queue holds rather than presenting false failure or resending.
