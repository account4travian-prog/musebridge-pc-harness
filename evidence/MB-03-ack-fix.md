# MB-03 live-chat evidence and send acknowledgment fix — 2026-11-11

the owner provided a screenshot showing the connected main Muse chat, two outgoing messages and Muse replies, together with a false `Send not confirmed (ValueError)` status and the already-delivered draft still in the input box. This is visible evidence of live text chat. It does not establish a bridge.health invocation or broader PC control.

## Cause and change

The official LinkSession.send_chat returns an SDK result envelope: `{ok, status, response}`. MuseBridge passed that outer object to `_acknowledged_message_ids`, which expects the inner acknowledgment (`message_id`, optionally nested under `result`). The helper therefore raised ValueError after a successful send. Meanwhile the chat subscription independently displayed the user message and Muse reply.

Core.send now unwraps and validates the SDK result using its official `acknowledgement()` helper before reading message IDs. Existing UI behavior clears a matching draft when the confirmed user row arrives. Rejected or genuinely ambiguous sends continue to preserve the draft, and are never automatically resent.

## Verification and running version

All **15 host tests passed**. New regression cases cover direct/nested inner message acknowledgments and missing/rejected acknowledgments, including single invocation/no replay. No additional live message was sent as part of this fix.

Source is fixed. The already-open app still runs its imported older code until the owner closes and reopens it. It was not forcibly restarted because its transcript and current draft are held in memory. Pairing and firmware do not need to change.

## Current harness capability

Implemented: native Windows chat to the existing Muse, streaming replies/updates, saved protected pairing, connection/reconnect logic, and advertised bridge.health. Visible live text chat is now established by the owner's screenshot. Reconnect/token-rotation and health invocation still need explicit live checks. The application advertises no file/shell/desktop tools and has no voice UI or execution path yet. Next substantive implementation is MB-04 Windows files/processes/job management; voice and desktop control follow.
