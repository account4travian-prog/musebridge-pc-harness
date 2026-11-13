# MB-18 — Hooks and MCP extensions

> This receipt records the 0.5 baseline. Its old limits are superseded where changed by the [0.6 five-capability receipt](FIVE-CAPABILITIES-0.6.md).

Hooks (`musebridge/hooks.py`) are project-local PowerShell commands explicitly configured by the user. Supported events are after file edit, before verification, and run ended; results are inspectable. They run with the user's account permissions.

MCP (`musebridge/mcp_client.py`) connects to configured local stdio servers and requires explicit per-tool read or project-write grants. Remote tools cannot enable servers or grant access. There is no OAuth flow. Review executable, arguments and grants; close the server when finished.

Probe evidence: a hook effect and MCP read were observed (`hook_effect_verified`, `mcp_result_verified` true). Evidence does not establish safety of arbitrary user-configured commands or every integration.
