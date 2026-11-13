# Workspace file primitives

Implemented `musebridge.workspace.Workspace` as the bounded file-operation foundation for the Windows harness. The selected root is a mistake-prevention boundary; operations still run with the current user's permissions, as required by the [execution contract](../MASTERPLAN.md#execution-contract-to-keep-stable-across-transports) and its explicit [work-scope limitation](../MASTERPLAN.md#execution-contract-to-keep-stable-across-transports).

## Behavior

- Relative and absolute paths resolve through `Path.resolve`, then must remain below the selected root. Resolved links/junctions outside it are rejected. The checkout's `.local` tree and known `%LOCALAPPDATA%` / `%APPDATA%\MuseBridge` credential locations are blocked; writes anywhere under `.git` are blocked.
- Directory listings and reads have hard caps. Reads return UTF-8 text with replacement for invalid sequences, base64 bytes, a SHA-256 for the returned chunk, byte offsets and EOF.
- Writes are UTF-8, limited to 1 MiB, staged in the destination directory, flushed, and atomically published. `create=True` publishes via an exclusive hard link to avoid replacing an existing destination. Expected hashes are checked before staging and again immediately before publication. Exact edits require one match and a caller-supplied expected hash.
- Search is explicitly literal (fixed-string), excludes private folders, caps returned match count and line text, and uses ripgrep when installed or a bounded Python text walk otherwise. Git status runs `git status --porcelain=v1 -z` with an argv list and returns parsed entries; it makes no repository changes.

## Validation

Command: `.venv\Scripts\python.exe -m pytest tests\test_workspace.py -q`

Result: **16 passed, 1 skipped** on Windows. The skipped case requires directory-symlink creation, which was unavailable in the test environment. Tests cover traversal and symlink containment, local-data exclusions, chunked Unicode reads, read/list/search bounds, stale hashes, exclusive create, unique-match edits, failed-publication preservation, Git status shape, and argv-based subprocess calls.

The SDK's Linux `fileops` informed the bounded reads, SHA-256 expectations and staged same-directory publication ([source](../vendor/muse-gadget-sdk/linux/src/musegadget/fileops.py#L39)). The Windows implementation intentionally has a whole-text write API instead of the SDK's multi-request chunk protocol.

## Limits

The root check is not OS isolation. `Path.resolve` plus a final recheck reduces ordinary link mistakes but does not close every race against another process changing reparse points. Expected-hash checks are optimistic: another process could still replace a file in the narrow interval between the final check and `os.replace`. Exclusive creation relies on same-volume hard-link support. Search limits returned data and individual scanned-file size; ripgrep can still inspect many files before the 15-second subprocess timeout. No dispatcher/UI integration, shell command runner or app-level authorization was added in this slice.

## Root integration follow-up — 2026-11-11

Integrated into the functional harness; earlier component-only integration notes above are historical. See [combined implementation and live evidence](HARNESS-2026-11-11.md) and [current running guide](../RUNNING.md). Final full suite: 73 passed, 1 skipped. Live acceptance boundaries remain as recorded in the combined evidence.
