# Isolated browser adapter

Implemented `BrowserTools` in `musebridge/browser.py` for the Muse tool surface. Its `specs()` returns the SDK-style `description` / `required` / `optional` contract; `call(name, params)` dispatches six tools, and `close()` shuts down the adapter.

## Isolation and bounds

- Playwright sync API is owned by one dedicated worker thread and a serialized, bounded command queue. Callers never access Playwright objects directly.
- Chromium launches headless from Playwright. Each required `conversation_id` gets its own in-memory, non-persistent browser context; there is no `launch_persistent_context`, user-profile path, existing-browser attachment, cookie import or profile read. `browser.close` closes that conversation's context. Adapter `close()` closes all contexts and the browser process.
- `browser.open` accepts absolute HTTP/HTTPS URLs only, rejects embedded URL credentials, and installs a context route that aborts other request schemes, including redirects. `browser.observe` returns at most 12,000 visible-text characters. Selector, fill text, locator/action/navigation deadlines and queue size are bounded. Screenshots are JPEG artifacts (not returned inline), saved below a SHA-256-derived conversation folder and rejected/deleted above 2 MiB.
- Errors sent back through the tool call are truncated to 500 characters. If a caller times out waiting, the operation is not replayed; its outcome is reported as possibly unknown.

## Validation

Command: `.venv\Scripts\python.exe -m pytest tests\test_browser.py -q`

Result: **4 passed** with Playwright 1.65.0 and Chromium available. The local HTTP fixture exercises open, observe, fill, click, screenshot artifact creation, per-conversation localStorage isolation, and closing a conversation context. Unit coverage checks schemas, required IDs, bad schemes, embedded credentials, input bounds and serialized dedicated-thread dispatch.

Implementation follows the official Playwright API guidance to create non-persistent `browser.new_context()` instances for isolation and explicitly close contexts, and to use Playwright's sync API in its normal managed lifecycle: [Browser contexts](https://playwright.dev/python/docs/api/class-browser), [BrowserContext](https://playwright.dev/python/docs/api/class-browsercontext), [Playwright Python API](https://playwright.dev/python/docs/api/class-playwright).

## Limits

This adapter does not integrate into `ToolRouter`; the router must advertise these specs and delegate the six `browser.*` calls. It does not enforce destination host allowlists: HTTP(S) pages can still reach network services available to the PC, including local services. No real account, external site, or user profile was used in validation. Browser actions remain governed by the lifetime of the conversation context and current page state.

## Root integration follow-up — 2026-11-11

Integrated into the functional harness; earlier component-only integration notes above are historical. See [combined implementation and live evidence](HARNESS-2026-11-11.md) and [current running guide](../RUNNING.md). Final full suite: 73 passed, 1 skipped. Live acceptance boundaries remain as recorded in the combined evidence.
