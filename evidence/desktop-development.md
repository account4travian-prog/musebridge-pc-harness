# Guarded Windows desktop adapter

Implemented `DesktopTools` in `musebridge/desktop.py`. The public schema/call contract includes `desktop.windows`, `desktop.observe`, `desktop.act`, and `desktop.screenshot`; `close()` disables further calls. It uses pywinauto 0.6.9 with the UIA backend and initializes/uninitializes COM for every call, so UIA wrappers remain local to that call/thread.

## Guardrails

- Every tool requires `conversation_id`. Window handles come from visible top-level metadata. Window enumeration is capped at 100 results; UI trees at 150 controls and depth 8; serialized tree/window output is kept under 150 KB.
- Read operations fail closed outside an active, unlocked interactive session. The adapter checks the current process session against WTS session state and the input desktop name. UIA only; no raw input simulation or arbitrary script/evaluation interface is exposed.
- Mutating actions are only `focus`, `invoke`, and `set_text`. The target must remain a valid window in this session, have integrity no higher than MuseBridge, be enabled and visible, and the user must have been idle for at least two seconds. `invoke` and `set_text` also require the target window to be foreground; a background target must first receive an explicit `focus` action. Foreground changes during preparation cause refusal.
- `set_text` is limited to UIA Edit controls and 10,000 characters; returned results never echo entered text. UIA control selection accepts an observed selector path or exact `automation_id` / `control_type` / `name` criteria. Ambiguous matches are rejected. Screenshots are JPEG artifacts with an 8 MiB cap and a conversation-hash subdirectory.

## Validation

Command: `.venv\Scripts\python.exe -m pytest tests\test_desktop.py -q`

Result: **14 passed**; `compileall` passed. Tests use dummy UIA wrappers and monkeypatched Windows state. They cover session/idle/foreground/integrity refusals, target invalidation, selectors and ambiguity, allowed actions, bounded Unicode tree output, screenshot artifact handling and required conversation IDs. No real application windows were acted on during tests. Read-only Windows session, input-desktop, current integrity and idle-query APIs were manually checked; current state reported an active unlocked session, and no desktop action was invoked.

The adapter follows pywinauto's documented UIA backend and uses its UIA wrapper methods for focus, invoke, edit text and capture: [Getting started](https://pywinauto.readthedocs.io/en/latest/getting_started.html), [UIA controls](https://pywinauto.readthedocs.io/en/latest/code/pywinauto.controls.uia_controls.html), [UIA wrapper](https://pywinauto.readthedocs.io/en/latest/code/pywinauto.controls.uiawrapper.html). The project plan calls for foreground, lock-state and user-activity checks and excludes elevated/secure-desktop control ([MASTERPLAN.md, D3](../MASTERPLAN.md#d3--one-remote-agent-local-tools-run-in-jordans-interactive-session)).

## Acceptance and limits

This is not integrated into `ToolRouter`; the root agent must dispatch the four desktop names. Live UIA tree quality and safe actuation against an owned disposable window remain unverified. Desktop APIs can fail on inaccessible or custom controls; those failures return bounded errors. Lock/session/integrity guards are mistake-prevention checks, not an OS security boundary, and there remains a narrow race between the final foreground/idle check and a UIA call.

## Root integration follow-up — 2026-11-11

Integrated into the functional harness; earlier component-only integration notes above are historical. See [combined implementation and live evidence](HARNESS-2026-11-11.md) and [current running guide](../RUNNING.md). Final full suite: 73 passed, 1 skipped. Live acceptance boundaries remain as recorded in the combined evidence.
