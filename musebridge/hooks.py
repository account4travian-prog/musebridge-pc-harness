"""Locally configured, opt-in project lifecycle hooks.

Hooks reuse Execution policy and JobManager process supervision. They are
ordinary project-write commands; this module does not load repository code or
provide a remote configuration surface.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
import math
from pathlib import Path
import re
import time
import uuid
import regex as bounded_regex


EVENTS = frozenset(("before_tool", "after_tool", "after_file_edit", "before_verification", "run_ended"))
MAX_HOOKS_PER_SCOPE = 20
MAX_HOOKS_PER_EVENT = 8
MAX_NAME_CHARS = 80
MAX_COMMAND_CHARS = 12000
MAX_TIMEOUT_S = 3600
MAX_PATH_FILTER_CHARS = 512
PATH_FILTER_MATCH_TIMEOUT_S = 0.02
_ACTIVE_INVOCATION_STATES = ("accepted", "running")
_TERMINAL_INVOCATION_STATES = frozenset(("succeeded", "failed", "cancelled", "interrupted", "outcome_unknown", "rejected"))


class PathFilterTimeout(ValueError):
    """A configured glob exceeded its small matching-time budget."""


def validate_path_filter(value, event):
    """Validate an optional project-relative glob used only for file-edit events."""
    if value is None:
        value = ""
    if not isinstance(value, str) or len(value) > MAX_PATH_FILTER_CHARS:
        raise ValueError(f"path_filter must be a string up to {MAX_PATH_FILTER_CHARS} characters")
    value = value.replace("\\", "/").strip()
    if value and event != "after_file_edit":
        raise ValueError("path_filter is supported only for after_file_edit hooks")
    if ("\x00" in value or any(ord(char) < 32 for char in value)
            or value.startswith("/") or re.match(r"^[A-Za-z]:", value)
            or any(part == ".." for part in value.split("/"))):
        raise ValueError("path_filter must be a project-relative glob without parent traversal")
    if value:
        _path_glob_regex(value)
    return value


def path_matches(path, path_filter):
    """Match a normalized project-relative path against a stored glob."""
    if not path_filter:
        return True
    if not isinstance(path, str) or not path or path.startswith(("/", "\\")):
        return False
    normalized = path.replace("\\", "/")
    if any(part == ".." for part in normalized.split("/")):
        return False
    try:
        return bool(_path_glob_regex(path_filter).fullmatch(
            normalized, timeout=PATH_FILTER_MATCH_TIMEOUT_S))
    except TimeoutError as exc:
        raise PathFilterTimeout("path glob matching timed out; simplify the glob") from exc


@lru_cache(maxsize=128)
def _path_glob_regex(pattern):
    """Compile a small slash-aware glob; ``**/`` also matches zero folders."""
    parts = ["^"]
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*" and index + 1 < len(pattern) and pattern[index + 1] == "*":
            if index + 2 < len(pattern) and pattern[index + 2] == "/":
                parts.append("(?:.*/)?")
                index += 3
                continue
            parts.append(".*")
            index += 2
            continue
        if char == "*":
            parts.append("[^/]*")
        elif char == "?":
            parts.append("[^/]")
        elif char == "[":
            end = pattern.find("]", index + 1)
            if end < 0:
                parts.append(r"\[")
            else:
                group = pattern[index + 1:end]
                if not group:
                    raise ValueError("path_filter contains an empty character class")
                if group[0] == "!":
                    group = "^" + group[1:]
                parts.append("[" + group.replace("\\", r"\\") + "]")
                index = end
        else:
            parts.append(re.escape(char))
        index += 1
    parts.append("$")
    try:
        return bounded_regex.compile("".join(parts), bounded_regex.IGNORECASE)
    except bounded_regex.error as exc:
        raise ValueError("path_filter contains invalid glob syntax") from exc


class HookService:
    """Manage explicit local hook configuration and one-shot event dispatch.

    `execution` must be the application's Execution instance and `jobs` its
    JobManager. A hook's scope is always a project scope, shared by that
    project's conversations. A Stop Work generation change revokes any
    invocation not yet admitted to JobManager.
    """

    def __init__(self, catalog, execution, jobs):
        self.catalog = catalog
        self.execution = execution
        self.jobs = jobs
        with catalog.lock, catalog.db:
            catalog.db.execute("""CREATE TABLE IF NOT EXISTS lifecycle_hooks_v1 (
                hook_id TEXT PRIMARY KEY,
                scope_key TEXT NOT NULL,
                name TEXT NOT NULL,
                event TEXT NOT NULL,
                command TEXT NOT NULL,
                effect TEXT NOT NULL CHECK(effect='project_write'),
                enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                timeout_s REAL NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(scope_key,name,event)
            )""")
            catalog.db.execute("CREATE INDEX IF NOT EXISTS lifecycle_hook_scope_event ON lifecycle_hooks_v1(scope_key,event,enabled)")
            if 'tool_filter' not in {r[1] for r in catalog.db.execute('PRAGMA table_info(lifecycle_hooks_v1)')}:
                catalog.db.execute("ALTER TABLE lifecycle_hooks_v1 ADD COLUMN tool_filter TEXT NOT NULL DEFAULT '[]'")
            if 'path_filter' not in {r[1] for r in catalog.db.execute('PRAGMA table_info(lifecycle_hooks_v1)')}:
                catalog.db.execute("ALTER TABLE lifecycle_hooks_v1 ADD COLUMN path_filter TEXT NOT NULL DEFAULT ''")
            catalog.db.execute("""CREATE TABLE IF NOT EXISTS hook_invocations_v1 (
                invocation_id TEXT PRIMARY KEY,
                scope_key TEXT NOT NULL,
                hook_id TEXT NOT NULL,
                hook_name TEXT NOT NULL,
                event TEXT NOT NULL,
                source_id TEXT NOT NULL,
                request_id TEXT NOT NULL UNIQUE,
                conversation_id TEXT NOT NULL,
                job_id TEXT,
                state TEXT NOT NULL,
                reason TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(scope_key,hook_id,event,source_id)
            )""")
            catalog.db.execute("CREATE INDEX IF NOT EXISTS hook_invocation_owner ON hook_invocations_v1(conversation_id,created_at DESC)")
            # A process cannot safely resume a hook after application restart.
            # Keep the admission record and make the ambiguous result explicit.
            now = time.time()
            catalog.db.execute("""UPDATE hook_invocations_v1 SET state='outcome_unknown',
                reason='application restarted while hook invocation was incomplete',updated_at=?
                WHERE state IN ('accepted','running')""", (now,))
            # ToolRouter owns this relationship table in normal app startup. It
            # is created here too so this service remains independently usable.
            catalog.db.execute("CREATE TABLE IF NOT EXISTS job_links(job_id TEXT PRIMARY KEY,conversation_id TEXT)")

    def set_hook(self, scope_key, name, event, command, enabled=False, timeout_s=300,tool_filter=None,path_filter=None):
        project_id = self._project_scope(scope_key)
        if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME_CHARS or "\x00" in name:
            raise ValueError(f"name must be 1..{MAX_NAME_CHARS} characters")
        if not isinstance(event, str) or event not in EVENTS:
            raise ValueError("unknown lifecycle event")
        tool_filter=[] if tool_filter is None else tool_filter
        if not isinstance(tool_filter,list) or len(tool_filter)>50 or any(not isinstance(t,str) or not t or len(t)>100 for t in tool_filter):raise ValueError('tool_filter must contain at most 50 exact tool names')
        path_filter = validate_path_filter(path_filter, event)
        if not isinstance(command, str) or not command.strip() or len(command) > MAX_COMMAND_CHARS or "\x00" in command:
            raise ValueError(f"command must be 1..{MAX_COMMAND_CHARS} characters")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be bool")
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
            raise ValueError("timeout_s must be a number")
        timeout_s = float(timeout_s)
        if not math.isfinite(timeout_s) or not 0 < timeout_s <= MAX_TIMEOUT_S:
            raise ValueError(f"timeout_s must be in (0, {MAX_TIMEOUT_S}]")
        self.catalog.project(project_id)
        now = time.time()
        with self.catalog.lock, self.catalog.db:
            existing = self.catalog.db.execute(
                "SELECT hook_id,created_at FROM lifecycle_hooks_v1 WHERE scope_key=? AND name=? AND event=?",
                (scope_key, name.strip(), event)).fetchone()
            if existing is None:
                count = self.catalog.db.execute(
                    "SELECT COUNT(*) FROM lifecycle_hooks_v1 WHERE scope_key=?", (scope_key,)).fetchone()[0]
                per_event = self.catalog.db.execute(
                    "SELECT COUNT(*) FROM lifecycle_hooks_v1 WHERE scope_key=? AND event=?",
                    (scope_key, event)).fetchone()[0]
                if count >= MAX_HOOKS_PER_SCOPE:
                    raise ValueError(f"project hook limit reached ({MAX_HOOKS_PER_SCOPE})")
                if per_event >= MAX_HOOKS_PER_EVENT:
                    raise ValueError(f"project event hook limit reached ({MAX_HOOKS_PER_EVENT})")
                hook_id = uuid.uuid4().hex
                self.catalog.db.execute("""INSERT INTO lifecycle_hooks_v1
                    (hook_id,scope_key,name,event,command,effect,enabled,timeout_s,created_at,updated_at,path_filter)
                    VALUES(?,?,?,?,?,'project_write',?,?,?,?,?)""",
                    (hook_id, scope_key, name.strip(), event, command, int(enabled), timeout_s, now, now, path_filter))
            else:
                hook_id = existing["hook_id"]
                self.catalog.db.execute("""UPDATE lifecycle_hooks_v1 SET command=?,enabled=?,timeout_s=?,updated_at=?,
                    path_filter=? WHERE hook_id=?""", (command, int(enabled), timeout_s, now, path_filter, hook_id))
            self.catalog.db.execute('UPDATE lifecycle_hooks_v1 SET tool_filter=? WHERE hook_id=?',(json.dumps(sorted(set(tool_filter))),hook_id))
            self.catalog.db.execute("INSERT INTO events(conversation_id,kind,data,created) VALUES(?,?,?,?)",
                ("main", "hook.configuration_changed", json.dumps({"scope_key": scope_key,
                 "hook_id": hook_id, "name": name.strip(), "event": event, "enabled": enabled,
                 "effect": "project_write"}), now))
        return self._hook_by_id(hook_id)

    def list_hooks(self, scope_key):
        self._project_scope(scope_key)
        rows = self.catalog.rows("SELECT * FROM lifecycle_hooks_v1 WHERE scope_key=? ORDER BY event,name",
                                 (scope_key,))
        return [self._present_hook(row) for row in rows]

    def run_event(self, cid, event, source_id, **context):
        """Admit matching hooks once; process work remains asynchronous.

        The event payload is recorded only as bounded JSON metadata. Commands
        are not interpolated with event text, and hook output cannot recursively
        trigger this service.
        """
        conversation = self.catalog.conversation(cid)
        if event not in EVENTS:
            raise ValueError("unknown lifecycle event")
        if not isinstance(source_id, str) or not source_id.strip() or len(source_id) > 256 or "\x00" in source_id:
            raise ValueError("source_id must be a nonempty string up to 256 characters")
        project_id = conversation.get("project_id")
        if not project_id:
            return {"conversation_id": cid, "event": event, "jobs": [],
                    "skipped": [{"reason": "conversation has no project scope"}]}
        scope_key = "project:" + project_id
        # Only small, non-sensitive event metadata is retained for diagnostics.
        metadata = self._bounded_context(context)
        with self.execution.lock:
            policy = self.execution.policy(cid)
            generation = self.execution.generation
            if self.execution.paused or policy["profile"] == "read_only":
                return {"conversation_id": cid, "event": event, "jobs": [],
                        "skipped": [{"reason": "work is paused" if self.execution.paused else "read_only profile denies project_write hooks"}]}
            hooks = self.catalog.rows("""SELECT * FROM lifecycle_hooks_v1
                WHERE scope_key=? AND event=? AND enabled=1 ORDER BY name LIMIT ?""",
                (scope_key, event, MAX_HOOKS_PER_EVENT))
            results, skipped = [], []
            for hook in hooks:
                selected_tools=json.loads(hook['tool_filter'])
                if event in ('before_tool','after_tool') and selected_tools and context.get('tool') not in selected_tools:continue
                if event == 'after_file_edit' and hook['path_filter']:
                    try:
                        if not path_matches(context.get('path'), hook['path_filter']):
                            continue
                    except PathFilterTimeout as exc:
                        skipped.append({"hook": hook["name"], "hook_id": hook["hook_id"],
                                        "state": "path_filter_timeout", "reason": str(exc)})
                        continue
                # Snapshot and validate admission under the same lock used by
                # Execution.pause; Stop cannot slip between this check and start.
                current = self.execution.policy(cid)
                if self.execution.paused or generation != self.execution.generation or current["profile"] == "read_only":
                    skipped.append({"hook": hook["name"], "reason": "admission revoked by Stop Work"})
                    continue
                request_id = self._request_id(scope_key, hook["name"], event, source_id)
                existing = self.catalog.rows("""SELECT * FROM hook_invocations_v1
                    WHERE scope_key=? AND hook_id=? AND event=? AND source_id=?""",
                    (scope_key, hook["hook_id"], event, source_id))
                if existing:
                    row = existing[0]
                    results.append(self._existing_result(row))
                    continue
                invocation_id = uuid.uuid4().hex
                now = time.time()
                try:
                    with self.catalog.lock, self.catalog.db:
                        self.catalog.db.execute("""INSERT INTO hook_invocations_v1
                            (invocation_id,scope_key,hook_id,hook_name,event,source_id,request_id,
                             conversation_id,job_id,state,reason,created_at,updated_at)
                            VALUES(?,?,?,?,?,?,?,?,NULL,'accepted',NULL,?,?)""",
                            (invocation_id, scope_key, hook["hook_id"], hook["name"], event,
                             source_id, request_id, cid, now, now))
                    project = self.catalog.project(project_id)
                    # A project root is a persisted authorization boundary.
                    # Refuse a replaced symlink/junction alias before the
                    # command runner canonicalizes its cwd.
                    project_root = Path(project["root"])
                    if project_root.resolve(strict=True) != project_root:
                        raise ValueError("selected project root identity changed")
                    # Keep the Stop Work gate across JobManager admission. A
                    # subsequent Stop cancels every accepted/running job.
                    self._assert_admitted(cid, generation)
                    event_context={'format_version':1,'event':event,'conversation_id':cid,'source_id':source_id,**metadata}
                    encoded_context=json.dumps(event_context,ensure_ascii=True).replace("'","''")
                    command="$env:MUSEBRIDGE_HOOK_CONTEXT = '"+encoded_context+"'; "+hook['command']
                    job = self.jobs.start(request_id=request_id, command=command,
                                          cwd=str(project_root), timeout_s=hook["timeout_s"])
                    job_id = job["job_id"]
                    with self.catalog.lock, self.catalog.db:
                        self.catalog.db.execute("""UPDATE hook_invocations_v1 SET job_id=?,state='running',updated_at=?
                            WHERE invocation_id=? AND state='accepted'""", (job_id, time.time(), invocation_id))
                        self.catalog.db.execute("INSERT OR IGNORE INTO job_links(job_id,conversation_id) VALUES(?,?)",
                                                (job_id, cid))
                        self.catalog.db.execute("INSERT INTO events(conversation_id,kind,data,created) VALUES(?,?,?,?)",
                            (cid, "hook.admitted", json.dumps({"format_version":1,"invocation_id": invocation_id,
                             "hook_id": hook["hook_id"], "event": event, "source_id": source_id,
                             "job_id": job_id, "effect": "project_write", "context": metadata}), time.time()))
                    results.append({"hook": hook["name"], "hook_id": hook["hook_id"], "invocation_id": invocation_id,
                                    "job_id": job_id, "state": job["state"], "duplicate": False})
                except Exception as exc:
                    # Start may fail after the external job was admitted. Keep
                    # this key consumed; never turn an uncertain launch into a
                    # replay opportunity.
                    reason = str(exc)[:500]
                    with self.catalog.lock, self.catalog.db:
                        self.catalog.db.execute("""UPDATE hook_invocations_v1 SET state='outcome_unknown',reason=?,updated_at=?
                            WHERE invocation_id=? AND state IN ('accepted','running')""",
                            (reason, time.time(), invocation_id))
                        self.catalog.db.execute("INSERT INTO events(conversation_id,kind,data,created) VALUES(?,?,?,?)",
                            (cid, "hook.admission_unknown", json.dumps({"invocation_id": invocation_id,
                             "hook_id": hook["hook_id"], "reason": reason}), time.time()))
                    skipped.append({"hook": hook["name"], "invocation_id": invocation_id,
                                    "reason": reason, "state": "outcome_unknown"})
        return {"conversation_id": cid, "event": event, "jobs": results, "skipped": skipped}

    def refresh(self, cid, invocation_id=None):
        """Read owned invocations and refresh visible terminal job states."""
        self.catalog.conversation(cid)
        if invocation_id is None:
            rows = self.catalog.rows("SELECT * FROM hook_invocations_v1 WHERE conversation_id=? ORDER BY created_at DESC LIMIT 100", (cid,))
        else:
            rows = self.catalog.rows("SELECT * FROM hook_invocations_v1 WHERE conversation_id=? AND invocation_id=?", (cid, invocation_id))
            if not rows:
                raise ValueError("hook invocation belongs to another conversation or does not exist")
        result = []
        for row in rows:
            if row["job_id"] and row["state"] in _ACTIVE_INVOCATION_STATES:
                try:
                    job = self.jobs.status(row["job_id"])
                    if job["state"] in _TERMINAL_INVOCATION_STATES:
                        with self.catalog.lock, self.catalog.db:
                            self.catalog.db.execute("UPDATE hook_invocations_v1 SET state=?,updated_at=? WHERE invocation_id=? AND state IN ('accepted','running')",
                                                    (job["state"], time.time(), row["invocation_id"]))
                        row["state"] = job["state"]
                except (KeyError, RuntimeError):
                    pass
            result.append(self._present_invocation(row))
        return result

    def _assert_admitted(self, cid, generation):
        if generation != self.execution.generation or self.execution.paused:
            raise RuntimeError("Hook admission revoked by Stop Work")
        policy = self.execution.policy(cid)
        if policy["profile"] == "read_only":
            raise RuntimeError("read_only profile denies project_write hooks")

    @staticmethod
    def _request_id(scope_key, name, event, source_id):
        payload = "\0".join((scope_key, name, event, source_id)).encode("utf-8")
        return "hook:" + hashlib.sha256(payload).hexdigest()

    def _project_scope(self, scope_key):
        if not isinstance(scope_key, str) or not scope_key.startswith("project:"):
            raise ValueError("hooks require a project:<project_id> scope")
        project_id = scope_key[len("project:"):]
        if not project_id or ":" in project_id:
            raise ValueError("invalid project hook scope")
        self.catalog.project(project_id)
        return project_id

    def _hook_by_id(self, hook_id):
        rows = self.catalog.rows("SELECT * FROM lifecycle_hooks_v1 WHERE hook_id=?", (hook_id,))
        return self._present_hook(rows[0])

    @staticmethod
    def _present_hook(row):
        return {"hook_id": row["hook_id"], "scope_key": row["scope_key"], "name": row["name"],
                "event": row["event"], "command": row["command"], "effect": row["effect"],
                "enabled": bool(row["enabled"]), "timeout_s": row["timeout_s"],
                "tool_filter": json.loads(row['tool_filter']),
                "path_filter": row['path_filter'],
                "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def before_tool(self,cid,name,run_id,params,guard):
        """Bounded blocking gate. Hook processes bypass remote routing, so no recursion.

        Stable remote run/argument keys reuse pending check jobs on inspection/retry;
        the original operation never starts while its pre-check is pending/failed.
        """
        digest=hashlib.sha256(json.dumps(params,sort_keys=True,ensure_ascii=True).encode()).hexdigest()
        source=run_id+':'+name+':'+digest
        invocation=self.run_event(cid,'before_tool',source,tool=name,run_id=run_id)
        if any(item.get('state')=='outcome_unknown' for item in invocation['skipped']):raise ValueError('Before-tool hook admission is uncertain; inspect hook.status')
        deadline=time.monotonic()+8
        for item in invocation['jobs']:
            job=self.jobs.status(item['job_id'])
            while job['state'] in ('accepted','running') and time.monotonic()<deadline:
                guard();job=self.jobs.wait(item['job_id'],timeout_s=min(.25,max(.01,deadline-time.monotonic())))
            if job['state'] in ('accepted','running'):
                raise ValueError('Before-tool hook is still running; original tool was not executed. Inspect job '+item['job_id']+' then retry the identical operation in this run.')
            if job['state']!='succeeded' or job['exit_code']!=0:raise ValueError('Before-tool hook did not pass; original tool was not executed. Inspect hook.status')
        guard()
        return invocation

    @staticmethod
    def _present_invocation(row):
        return {"invocation_id": row["invocation_id"], "scope_key": row["scope_key"],
                "hook_id": row["hook_id"], "hook": row["hook_name"], "event": row["event"],
                "source_id": row["source_id"], "conversation_id": row["conversation_id"],
                "job_id": row["job_id"], "state": row["state"], "reason": row["reason"],
                "created_at": row["created_at"], "updated_at": row["updated_at"]}

    @staticmethod
    def _existing_result(row):
        return {"hook": row["hook_name"], "hook_id": row["hook_id"], "invocation_id": row["invocation_id"],
                "job_id": row["job_id"], "state": row["state"], "duplicate": True}

    @staticmethod
    def _bounded_context(context):
        if not context:
            return {}
        safe = {}
        for key, value in list(context.items())[:20]:
            if not isinstance(key, str) or len(key) > 80:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                safe[key] = value if not isinstance(value, str) else value[:500]
            elif isinstance(value, (dict, list)):
                safe[key] = json.dumps(value, ensure_ascii=False, default=str)[:1000]
        encoded = json.dumps(safe, ensure_ascii=False, default=str)
        while len(encoded.encode("utf-8")) > 4000 and safe:
            safe.pop(next(reversed(safe)))
            encoded = json.dumps(safe, ensure_ascii=False, default=str)
        return safe
