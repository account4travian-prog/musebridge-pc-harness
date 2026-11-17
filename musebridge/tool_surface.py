"""Tool contract v2 schemas and settings-driven registration packs."""

from __future__ import annotations

import copy
import sqlite3


DEFAULT_PACKS = ("core", "browser", "terminal")


def parameter(description: str, kind: str = "string") -> dict:
    return {"type": kind, "description": description}


def spec(description: str, required: dict | None = None, optional: dict | None = None) -> dict:
    return {"description": description, "required": required or {}, "optional": optional or {}}


CID = parameter("Conversation identifier from bridge.context. Omit when the callback is already bound to this chat.")
_V = {}


def _add(name, description, required=None, optional=None):
    req = {}
    req.update(required or {})
    opt = {"conversation_id": copy.deepcopy(CID)}
    opt.update(optional or {})
    _V[name] = spec(description, req, opt)


# Descriptions carry a practical example and a neighbouring-tool choice so the
# compact v2 surface remains discoverable without duplicating implementation schemas.
_add("bridge.context", 'Read project instructions, environment, plan, tasks, and notes. Use it to orient or refresh state before project work. For service health use bridge.health; for a narrow source lookup use file.search. Example: bridge.context(section="tasks"). Returns a concise section or the complete context when section is omitted.', optional={"section": parameter("Optional context section to return.")})
_add("shell.run", 'Run a bounded command in the selected project and shell. Use it for tests, scripts, Git, and ordinary command line work. For an already running interactive process use terminal.send; for a returned background job use shell.job. Example: shell.run(command="python -m pytest -q", shell="powershell"). Returns exit status and output, or a job handle when the command continues.', {"command": parameter("Command text to run in the selected shell.")}, {"cwd": parameter("Project-relative working directory."), "timeout_s": parameter("Command timeout in seconds.", "number"), "shell": parameter("powershell, bash, or cmd."), "background": parameter("Return a running job handle immediately.", "boolean")})
_add("shell.job", 'Inspect, wait for, list, or cancel a command job owned by this conversation. Use it when shell.run returns a job identifier or to list this chat’s running jobs; action defaults to wait. Start a new command with shell.run; use terminal.read for persistent interactive sessions. Example: shell.job(job_id="job-id", action="wait", wait_s=10). Returns current state, output, and exit status when known.', optional={"job_id": parameter("Job identifier returned by shell.run; omit for list."), "action": parameter("wait (default), status, cancel, or list."), "wait_s": parameter("Maximum wait, in seconds.", "number"), "cursor": parameter("Retained output cursor.", "integer")})
_add("file.read", 'Read numbered lines from a project text file and record its version for safe later edits. Use it to inspect or prepare a file edit. For finding text across files use file.search; for binary or immutable snapshots use artifact.read. Example: file.read(path="src/app.py", start_line=1, max_lines=100). Returns numbered text, line totals, and a continuation pointer when more remains.', {"path": parameter("Project-relative file path.")}, {"start_line": parameter("First line, starting at 1.", "integer"), "max_lines": parameter("Maximum lines to return.", "integer")})
_add("file.edit", 'Apply a guarded text replacement or line-range edit to a file already read in this chat. Use it for focused changes and atomic batches. For a new file use file.write; for a discovery pass use file.search. Example: file.edit(path="src/app.py", old="return 1", new="return 2"). Returns changed numbered regions and best-effort syntax diagnostics; it refuses content changed since the read. Missing warnings are not validation: run the project check.', {"path": parameter("Project-relative file path.")}, {"old": parameter("Text to replace."), "new": parameter("Replacement text."), "replace_all": parameter("Replace every exact match.", "boolean"), "edits": parameter("Ordered text or line-range edits.", "array"), "start_line": parameter("First inclusive line; range mode.", "integer"), "end_line": parameter("Last inclusive line; start_line minus one inserts.", "integer")})
_add("file.write", 'Create a project text file, or replace one after reading it or explicitly allowing overwrite. Use it for whole-file content and new parents. For a smaller change use file.edit; for immutable capture use artifact.add. Example: file.write(path="notes/todo.txt", text="Review tests\\n"). Returns path, byte count, checkpoint details and best-effort syntax diagnostics. Missing warnings are not validation: run the project check.', {"path": parameter("Project-relative file path.")})
_V["file.write"]["required"]["text"] = parameter("UTF-8 text content.")
_V["file.write"]["optional"]["overwrite"] = parameter("Allow replacement without a current read.", "boolean")
_add("file.list", 'List a bounded project tree with directories first and nested ignore rules applied. Use it to discover project structure. For matching file contents use file.search; to inspect a file use file.read. Example: file.list(path="src", depth=2). Returns an indented tree, entry counts, and truncation information when capped.', optional={"path": parameter("Project-relative directory; default is the project root."), "depth": parameter("Maximum directory depth.", "integer")})
_add("file.search", 'Search project files using a regular expression or literal text, with optional glob, context, and result cap. Use it to locate code or references. For a directory overview use file.list; for full surrounding source use file.read. Example: file.search(pattern="TODO", glob="*.py", context=1). Returns grouped numbered matches, or file/count summaries, with a narrowing hint when capped.', {"pattern": parameter("Search expression or literal text.")}, {"path": parameter("Project-relative directory or file."), "glob": parameter("Optional file-name glob."), "mode": parameter("content, files, or count."), "context": parameter("Number of nearby lines.", "integer"), "literal": parameter("Treat pattern as literal text.", "boolean"), "ignore_case": parameter("Match without case sensitivity.", "boolean"), "max_results": parameter("Maximum returned matches.", "integer")})
_add("plan.update", 'Save or update the project plan and goal state. Use it to record an agreed objective and progress. For concrete work items use task.set; for project facts and decisions use project.note. Example: plan.update(goal="Repair import handling", state="active"). Returns the current plan and task summary.', optional={"plan": parameter("Markdown plan text."), "goal": parameter("Current goal description."), "state": parameter("idle, active, paused, complete, or blocked.")})
_add("task.set", 'Create or update one or more project tasks, including dependencies and acceptance checks. Use it to keep executable work visible. For overall direction use plan.update; to run a check and issue a receipt use task.verify. Example: task.set(tasks=[{"id":"tests","title":"Run tests"}]). Returns the full task list.', {"tasks": parameter("Task objects with optional id, criteria, depends_on, state, and check fields.", "array")})
_add("task.verify", 'Run or record the configured verification for a task and bind its result to the task inputs. Use it after task.set has defined the task and check. For an ordinary one-off command use shell.run; for task state without verification use task.set. Example: task.verify(task_id="tests", command="python -m pytest", paths=["src/app.py", "tests/test_app.py"]). Returns check output, receipt, and task state.', {"task_id": parameter("Task identifier.")}, {"command": parameter("Verification command."), "paths": parameter("Project-relative files bound as check inputs.", "array"), "job_id": parameter("Completed job identifier, if checking an existing job.")})
_add("project.note", 'Record a project decision, constraint, or progress fact with its source. Use it for durable context that should survive a new conversation. For goals and work sequence use plan.update; for task status use task.set. Example: project.note(kind="decision", text="Keep the parser local", source="Owner request"). Returns the note identifier.', {"kind": parameter("decision, constraint, or progress."), "text": parameter("Fact or decision text.")}, {"source": parameter("Source reference or owner statement."), "supersedes": parameter("Earlier note identifier being corrected.")})
_add("conversation.handoff", 'Create a separate conversation seeded with a project handoff summary. Use it when work should continue in another chat. For current project state use bridge.context; this does not create a hidden-state fork. Example: conversation.handoff(title="Continue parser work"). Returns the new conversation details for owner selection.', optional={"title": parameter("Optional new conversation title.")})
_add("change.review", 'Review file edits and completed run snapshots, including shell-made changes. Use it before restoring work; use bridge.context for project history. Example: change.review(path="src/app.py"). Returns bounded changed paths/diffs or a snapshot-unavailable note. Snapshots exclude ignored/private/build files, databases and files over 8 MiB.', optional={"run": parameter("Optional run identifier to narrow the review."), "path": parameter("Optional project-relative path.")})
_add("change.restore", 'Restore reviewed files from run snapshots or direct edit checkpoints. Later owner edits cause a conflict refusal. Use it after change.review; use file.edit for a new correction. Example: change.restore(run="run-id", path="src/app.py"). Restores files only: it does not undo installs, network calls, deleted databases or anything outside the project. Returns restored files or a refusal.', optional={"edit_id": parameter("Direct edit checkpoint identifier."), "path": parameter("Project-relative path to restore."), "run": parameter("Completed run identifier.")})
_add("artifact.read", 'List this conversation’s immutable artifacts or read a bounded page from one artifact. Use it to inspect text extracted from a captured file. For live project text use file.read; for adding a snapshot use artifact.add. Example: artifact.read(artifact_id="artifact-id", offset=0, length=5000). Returns artifact metadata or a text page with continuation details.', optional={"artifact_id": parameter("Owned artifact identifier; omit to list."), "offset": parameter("Character offset.", "integer"), "length": parameter("Maximum characters to return.", "integer")})
_add("artifact.add", 'Capture a project file as an immutable artifact owned by this conversation. Use it when a stable snapshot is needed for preview or export. For editable project content use file.read and file.edit; to inspect a prior capture use artifact.read. Example: artifact.add(path="reports/summary.pdf"). Returns artifact metadata including its identifier and source hash.', {"path": parameter("Project-relative regular file path.")})
_add("screen.look", 'Request a bounded visual observation from an allowed browser, selected desktop window, or project image file. Use it when visual layout matters. For page text use browser.snapshot; for UI structure use desktop.snapshot. Example: screen.look(source="browser", marks=true). Returns a queued image for the next turn; pixels are not supported inline, and later overlays may add marks.', {"source": parameter("browser, window, or file.")}, {"window": parameter("Selected window handle for window source.", "integer"), "path": parameter("Project-relative image path for file source."), "marks": parameter("Whether to add visual marks; default true.", "boolean"), "region": parameter("Optional bounded region coordinates or description.", "object")})
_add("git.worktree.create", 'Create an isolated Git worktree and corresponding project conversation. Use it to work on a branch without disturbing the current checkout. For ordinary Git commands use shell.run; for reviewing tracked edits use change.review. Example: git.worktree.create(branch="fix/parser", base="main"). Returns the worktree and conversation details.', {"branch": parameter("New branch name.")}, {"base": parameter("Base branch or commit; defaults to the current base.")})
_add("bridge.health", 'Report whether the local bridge and its supported features are available. Use it for a quick connection check. For project instructions and current work use bridge.context; for command execution use shell.run. Example: bridge.health(). Returns a compact health status and feature availability summary.')

# Browser controls address only the isolated browser owned by this conversation.
_add("browser.navigate", 'Navigate the isolated browser to a URL. Use it to open a page for this conversation. For an existing page overview use browser.snapshot; for project files use file.read. Example: browser.navigate(url="https://example.com"). Returns the final URL and navigation status, without attaching to a personal browser profile.', {"url": parameter("HTTP or HTTPS URL to open.")})
_add("browser.snapshot", 'Read a bounded snapshot of the isolated browser page, including its visible text and basic page details. Use it to inspect a page before interaction. For a desktop window use desktop.snapshot; for an action use browser.act. Example: browser.snapshot(filter="pricing"). Returns page metadata and matching visible text when a filter is supplied.', optional={"filter": parameter("Optional text filter for visible page content.")})
_add("browser.act", 'Act on an element ref from browser.snapshot. Use it for form interactions and inspect returned changes. Refs remain stable across actions; an explicit new snapshot invalidates old refs. For page inspection use browser.snapshot; for Windows UI use desktop.act. Example: browser.act(ref="e12", action="fill", text="hello"). Returns URL/title and a bounded diff or full snapshot after a large change.', {"action": parameter("click, fill, type, press, select, hover, scroll, wait_for, back, check, uncheck.")}, {"ref": parameter("Exact element ref from this chat snapshot."), "selector": parameter("Legacy CSS/Playwright locator."), "text": parameter("Text for fill/type/wait_for."), "key": parameter("Key such as Control+A for press."), "value": parameter("Option value for select."), "amount":parameter("Scroll distance in pixels, -10000..10000.",'number'), "seconds":parameter("wait_for delay, at most 10 seconds.",'number')})
_add("browser.eval", 'Evaluate a bounded JavaScript expression in the isolated browser page. Use it for page inspection that visible text snapshots cannot answer. For ordinary interaction use browser.act; for project code use shell.run. Example: browser.eval(js="document.title"). Returns a bounded serialized value or an execution error.')
_V["browser.eval"]["required"]["js"] = parameter("JavaScript expression to evaluate.")
_add("browser.close", 'Close and discard this conversation’s isolated browser session. Use it when browser work is finished or should be reset. For page navigation use browser.navigate; to inspect current state use browser.snapshot. Example: browser.close(). Returns whether the browser session closed and its state was discarded.')

# Desktop is registered only for projects with a desktop execution profile.
_add("desktop.windows", 'List bounded metadata for available top-level desktop windows. Use it to find a target for permitted UI work. For an already selected window tree use desktop.snapshot; browser pages use browser.snapshot. Example: desktop.windows(). Returns window handles and titles without taking a screenshot.')
_add("desktop.launch", 'Launch an application through the configured desktop controls. Use it when the selected project profile permits launching an app. For inspecting existing windows use desktop.windows; for command-line programs use shell.run. Example: desktop.launch(app_or_path="notepad.exe"). Returns launch status and available window information for the newly started application.', {"app_or_path": parameter("Application name or allowed executable path.")}, {"args": parameter("Optional argument list.", "array")})
_add("desktop.snapshot", 'Read a bounded UI Automation snapshot for one selected desktop window. Use it to find available controls before acting. For a browser page use browser.snapshot; to click or enter text use desktop.act. Example: desktop.snapshot(window=12345, filter="Save"). Returns window details and basic control selector aliases.', {"window": parameter("Window handle returned by desktop.windows.", "integer")}, {"filter": parameter("Optional text filter for controls.")})
_add("desktop.act", 'Act on a desktop.snapshot ref in the selected window, preferring UIA patterns. Use it for permitted Windows interactions. Input requires an idle unlocked PC, matching foreground and non-elevated target. For inspection use desktop.snapshot. Example: desktop.act(window=12345, ref="e12", action="set_text", text="hello"). Returns window changes and newly opened target-process windows; inspect them to verify the effect. Alt+F4 is refused.', {"window": parameter("Window handle returned by desktop.windows.", "integer"), "action": parameter("click, double_click, right_click, type, keys, set_text, select, toggle, expand, scroll, focus, wait_for; legacy invoke.")}, {"ref":parameter("Exact UIA reference from this chat/window."), "selector": parameter("Legacy numeric selector path."), "text": parameter("Text to type/set or wait for."), "keys":parameter("Bounded chord such as ctrl+s."), "x":parameter("Physical screen x inside the target window.",'integer'), "y":parameter("Physical screen y inside the target window.",'integer'), "direction":parameter("Scroll up, down, left or right."), "value":parameter("Expand true or collapse false.",'boolean')})

# Terminal sessions are persistent per conversation and separate from shell jobs.
_add("terminal.open", 'Open a persistent interactive terminal session in the project. Use it for commands that need input across multiple steps. For a bounded one-shot command use shell.run; to inspect an existing session use terminal.read. Example: terminal.open(shell="powershell", cwd="."). Returns the terminal identifier and initial output.', optional={"shell": parameter("powershell, bash, or cmd; default is the configured shell."), "cwd": parameter("Project-relative starting directory."), "args": parameter("Optional shell argument list.", "array"), "rows": parameter("Terminal height.", "integer"), "cols": parameter("Terminal width.", "integer")})
_add("terminal.send", 'Send input to an owned persistent terminal and return output that follows it. Use it after terminal.open when a process needs interactive input. For a one-shot command use shell.run; for reading without sending input use terminal.read. Example: terminal.send(terminal_id="terminal-id", text="python\\n"). Returns new output and a continuation cursor when needed.', {"terminal_id": parameter("Identifier returned by terminal.open."), "text": parameter("Input text to send.")}, {"cursor": parameter("Output cursor to continue from.", "integer"), "wait_s": parameter("Maximum wait for new output.", "number")})
_add("terminal.read", 'Read new output from an owned persistent terminal without sending input. Use it to check a running interactive process. To send input use terminal.send; to inspect a shell.run job use shell.job. Example: terminal.read(terminal_id="terminal-id", cursor=0). Returns terminal output and the next cursor.', {"terminal_id": parameter("Identifier returned by terminal.open.")}, {"cursor": parameter("Output cursor to continue from.", "integer")})
_add("terminal.close", 'Close an owned persistent terminal session and its process. Use it when interactive work is complete. For a background one-shot job use shell.job with action cancel; for opening a new session use terminal.open. Example: terminal.close(terminal_id="terminal-id"). Returns close status and final output when available.', {"terminal_id": parameter("Identifier returned by terminal.open.")})

# MCP schemas exist in V2_SPECS, but are exposed only when selected and configured.
_add("mcp.list", 'List enabled local MCP servers and their granted tools for the selected project. Use it to discover explicitly configured external tools. For local project commands use shell.run; to invoke a listed grant use mcp.call. Example: mcp.list(server_id="server-id"). Returns server metadata and granted tool names without calling a server.', optional={"server_id": parameter("Optional configured server identifier to inspect.")})
_add("mcp.call", 'Call one tool granted by an enabled project-local MCP server. Use it only after mcp.list shows the server and grant. For built-in project operations use the matching core tool; external server effects depend on the owner’s grant. Example: mcp.call(server_id="server-id", tool_name="lookup", arguments={}). Returns the bounded server result or a structured error.', {"server_id": parameter("Configured MCP server identifier."), "tool_name": parameter("Tool name granted by that server."), "arguments": parameter("JSON object of tool arguments.", "object")})


V2_SPECS = _V
CORE_TOOLS = tuple(name for name in V2_SPECS if name not in {
    "browser.navigate", "browser.snapshot", "browser.act", "browser.eval", "browser.close",
    "desktop.windows", "desktop.launch", "desktop.snapshot", "desktop.act",
    "terminal.open", "terminal.send", "terminal.read", "terminal.close", "mcp.list", "mcp.call",
})
PACK_TOOLS = {
    "core": CORE_TOOLS,
    "browser": ("browser.navigate", "browser.snapshot", "browser.act", "browser.eval", "browser.close"),
    "desktop": ("desktop.windows", "desktop.launch", "desktop.snapshot", "desktop.act"),
    "terminal": ("terminal.open", "terminal.send", "terminal.read", "terminal.close"),
    "mcp": ("mcp.list", "mcp.call"),
}


def _has_row(catalog, query: str) -> bool:
    try:
        return bool(catalog.rows(query))
    except (sqlite3.Error, AttributeError, TypeError):
        try:
            return catalog.db.execute(query).fetchone() is not None
        except (sqlite3.Error, AttributeError, TypeError):
            return False


def select_packs(catalog) -> tuple[str, ...]:
    """Return validated configured packs, with core always present."""
    requested = catalog.preference("tool_packs", default=["browser", "terminal"])
    if not isinstance(requested, (list, tuple)) or any(not isinstance(x, str) for x in requested):
        raise ValueError("tool_packs must be a list of pack names")
    if len(set(requested)) != len(requested):
        raise ValueError("tool_packs contains duplicate names")
    unknown = sorted(set(requested) - set(PACK_TOOLS))
    if unknown:
        raise ValueError("unknown tool pack: " + ", ".join(unknown))
    selected = ["core"] + [name for name in requested if name not in ("core", "desktop", "mcp")]
    if _has_row(catalog, "SELECT s.scope_key FROM execution_scopes s JOIN projects p ON s.scope_key='project:'||p.id WHERE s.profile='desktop' LIMIT 1"):
        selected.append("desktop")
    if "mcp" in requested and _has_row(catalog, "SELECT id FROM mcp_servers_v1 WHERE enabled=1 LIMIT 1"):
        selected.append("mcp")
    return tuple(selected)


def registration_specs(router) -> dict:
    """Build fresh v2 schemas for the packs enabled in this router's catalog."""
    packs = select_packs(router.catalog)
    names = [name for pack in packs for name in PACK_TOOLS[pack]]
    schemas = {name: copy.deepcopy(V2_SPECS[name]) for name in names}
    for name,schema in schemas.items():
        if name!='bridge.health':schema['optional']['run_id']=parameter('Run ID from this request Work binding. Pass it with conversation_id when chats run concurrently.')
    for name in ("shell.run", "shell.job", "task.verify"):
        schemas[name]["timeout_ms"] = 40000
    return schemas
