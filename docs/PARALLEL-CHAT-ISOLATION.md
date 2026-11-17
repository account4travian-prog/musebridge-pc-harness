# Parallel chat isolation decision - November 14, 2026

MB-36a implementation contract. Owner rules remain those in `HARNESS-UPGRADE-PLAN.md`; one gadget session/identity and Muse as the sole reasoning agent are unchanged.

## Verified SDK boundary

The pinned SDK's LinkSession.subscribe_chat opens an independent stream_http subscription per session ID. The underlying request/response table is keyed by unique stream IDs and each subscription has its own bounded consumer. Several streams can share one LinkSession. Its global chat_subscribed Event is only a compatibility indicator; one ending subscription clears it, so it cannot represent readiness for all chats. MuseBridge must keep per-chat opened/task state.

The same SDK's _invoke passes only command, params and timeout to run_command. It does not pass a trusted originating chat or request identity. Removing the selected-chat check without another consistency check would accept mismatched model bookkeeping when more than one request is active.

## Selected behavior

- Keep at most three live dedicated project chats plus Main, each with an independent Transcript, replay/completion state, subscription and ready event. Selection changes what the UI displays; it does not stop other runs. Retain settled background subscriptions for 60 seconds for late events, then retire them. Never create a second gadget connection.
- Include a short ephemeral conversation/run binding with each real connected request. Stable environment guidance still follows the lean-header cache. When multiple requests are active, every tool except read-only bridge.context and connection health must supply a matching conversation_id/run_id pair; omission is ambiguous and mismatch is refused before effects. Context can discover a fresh binding, including a voice run. Single active requests can still infer their binding. The registered fields remain optional for compatibility.
- This is consistency/mistake prevention, not server-attested caller authentication: the SDK cannot certify which remote chat originated a model-supplied pair. Do not claim stronger tenant/security isolation than the transport provides.
- Admit project writes under an exclusive ownership lease keyed by canonical workspace root, including alias/nested-root checks. Two chats may read the same project. Configured hook commands also acquire ownership, including a hook around a read tool. A different writer gets project_busy until the owning run and its active effects settle. Rebase the private run snapshot when first claiming a write lease so recovery cannot undo a prior chat's completed writes. Separate worktrees have separate roots. Stop cancels all owned work and revokes late admissions across chats.
- Route queues, visual continuations and goal messages using their captured conversation ID, never the selected UI chat. Main's parentless/shared completion uncertainty remains; it cannot drive automatic continuation.

The ephemeral binding is an evidence-driven refinement of D2/D7: the callback lacks trusted attribution, so completely ID-free simultaneous runs cannot meet the wrong-chat consistency criterion. The added text and tool field costs must be measured by the final evaluation. It is not a second JSON preamble or a new reasoning loop.

Archiving holds future queued and visual sends; restoring requires explicit resume. The send boundary rechecks archive status after asynchronous context preparation. Changing the viewed chat alone does not hold work. Reaping subscriptions shares the admission lock with subscription creation to avoid retiring a chat being refreshed.

Implementation lives on its own branch off foundation 92ea905. The [live two-chat probe](../evidence/MB-36a-live-parallel.json) passed: two real Muse requests had overlapping local shell intervals, both finished with the correct files while Main was selected, and neither wrote the other project. One session/identity was used. Merge follows full-suite and final-evaluation checks; see [acceptance evidence](../evidence/MB-36a-parallel.md). Owner Notepad/voice/day-to-day checks remain separate and were deferred by the owner.
