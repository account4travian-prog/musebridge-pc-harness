# MB-20 — Interactive terminals

> This receipt records the 0.5 baseline. Its old limits are superseded where changed by the [0.6 five-capability receipt](FIVE-CAPABILITIES-0.6.md).

Implementation: `musebridge/terminals.py`, `terminal_host.py`, and `terminal_ui.py`. A project conversation can own a PowerShell ConPTY session with line input, Ctrl+C, resize and paged text output. Input delivery is journaled; uncertain input is not resent. Processes run as the current Windows user, so the project folder is not an OS sandbox. Output is plain text, not full VT emulation.

The live probe confirmed interactive input (`interactive_terminal_verified=true`). This does not validate every interactive program or terminal rendering behavior.
