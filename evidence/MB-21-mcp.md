# MB-21 — MCP integrations

Implementation: `musebridge/mcp_client.py` and the project MCP interface. Configure a local stdio executable, arguments, enablement and explicit tool grants (`read` or `project_write`). Discovery displays server tool schemas. The client does not provide OAuth; grants are local and remote tools cannot expand them.

The live workbench probe verified an MCP read (`mcp_result_verified=true`). This is one observed integration path, not acceptance of arbitrary servers or all tool schemas. Review executable, arguments and grants before enabling a server.
