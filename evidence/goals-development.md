# Bounded goal continuation — 2026-11-11

## Implemented

`musebridge/goals.py` adds `GoalRunner(catalog, send, status)`. Nothing starts during construction or app startup: the owner must call `await run(cid, max_rounds=5, turn_timeout_s=300)`. A run requires a nonempty goal in `active` state and a conversation with a dedicated Muse side-chat `remote_id`; the main chat is rejected. Only one run is admitted at a time. Round count and per-turn deadline have hard upper bounds.

Each prompt uses the current catalog goal and plan. A `MuseTurn` is created before calling the injected `send(cid, prompt)` callback, so incoming events can be held by the SDK until the send acknowledgment arrives. A round advances only when the SDK emits `TaskFinished` and `MuseTurn.complete(time.monotonic())` is true. Silence alone cannot finish a turn. One shared `ReplayScope` per conversation retires each turn before the next, preventing old task, request, and reply events from finishing a later round. `feed` accepts only the active conversation; wrong chat sessions are filtered by `MuseTurn`. `disconnect(cid, reason)` is provided for a closed subscription.

Unknown/rejected acknowledgment, send exception, disconnect, timeout, tool-updated goal state, and cancellation do not trigger a retry. Failures pause an active goal, record a bounded reason event, and report status. Owner/tool changes to `complete`, `blocked`, or `paused` are preserved. Reaching the round limit pauses the goal and asks the owner to explicitly continue it; exhausting rounds never marks a goal complete.

## SDK behavior reused

The pinned vendored SDK implementation is in `vendor/muse-gadget-sdk/linux/src/musegadget/muse_turn.py`: `ReplayScope` and idle observation (lines 132–184), `MuseTurn.acknowledge` (268), event `feed` (307), `complete` (354), and turn `retire` (374). The SDK's own fixtures are in `vendor/muse-gadget-sdk/linux/tests/test_muse_turn.py`; the continuation tests use the same raw event shape, pre-ack event flow, side-chat acknowledgment shape, and `task.status` terminal events. This avoids introducing a separate model or reimplementing Muse event lineage.

## Verification performed

Command: `.venv\Scripts\python.exe -m pytest -q tests/test_goals.py`

Result: **8 passed**. Tests use a real local `Catalog`, the pinned SDK's `MuseTurn`/`ReplayScope`, and an injected fake send callback. They cover pre-ack events, multiple bounded rounds, wrong conversation/session filtering, retired old-turn replay, unknown acknowledgment/no retry, timeout/no retry, disconnect and bounded reason reporting, cancellation/no next send, preservation of tool-updated completion, and main-chat rejection. No live Muse request was sent.

## Integration boundaries

The app must wire Continue Goal explicitly, pass the normal send callback that returns the SDK's inner acknowledgment, and call `feed(cid, event)` from its chat observer. The app should call `disconnect(cid, reason)` when the selected chat subscription ends. This module does not register UI controls or Muse tools, add a reconnect/retry policy, verify server-side tool admission, or independently prove live Muse behavior. Goal tool calls are trusted to update `Catalog`; the runner polls the goal state while waiting and will not overwrite a tool/user decision.

## Root integration follow-up — 2026-11-11

Integrated into the functional harness; earlier component-only integration notes above are historical. See [combined implementation and live evidence](HARNESS-2026-11-11.md) and [current running guide](../RUNNING.md). Final full suite: 73 passed, 1 skipped. Live acceptance boundaries remain as recorded in the combined evidence.
