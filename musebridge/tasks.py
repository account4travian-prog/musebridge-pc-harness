"""Structured task records and locally grounded verification receipts.

This module records task state; it does not execute checks. Shell/job
verification is accepted only by inspecting the trusted JobManager plus its
conversation ownership link. Manual acceptance is intended for local UI only.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path

MAX_TASKS_PER_CONVERSATION = 200
MAX_DEPENDENCIES = 32
MAX_CRITERIA_CHARS = 4000
MAX_DETAILS_CHARS = 4000
MAX_CHECK_COMMAND_CHARS = 128000
MAX_VERIFY_PATHS = 16
MAX_VERIFY_PATH_CHARS = 512
MAX_VERIFIED_FILE_BYTES = 1024 * 1024
MAX_RECEIPTS_PER_TASK = 1000
MAX_LIST_FIELD_CHARS = 128
TASK_STATES = ("pending", "active", "blocked", "awaiting_review", "complete")


class TaskStore:
    """Additive task store sharing a Catalog's lock and SQLite connection."""

    def __init__(self, catalog):
        self.catalog = catalog
        with catalog.lock, catalog.db:
            catalog.db.execute("""CREATE TABLE IF NOT EXISTS structured_tasks_v1 (
                task_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                title TEXT NOT NULL,
                criteria TEXT NOT NULL DEFAULT '',
                check_command TEXT,
                verification_mode TEXT NOT NULL DEFAULT 'job_exit' CHECK(verification_mode IN ('job_exit','manual')),
                dependencies_json TEXT NOT NULL DEFAULT '[]',
                state TEXT NOT NULL CHECK(state IN ('pending','active','blocked','awaiting_review','complete')),
                details TEXT NOT NULL DEFAULT '',
                blocked_reason TEXT,
                current_verification_id TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                completed_at REAL
            )""")
            catalog.db.execute("CREATE INDEX IF NOT EXISTS task_owner_state ON structured_tasks_v1(conversation_id,state,created_at)")
            catalog.db.execute("""CREATE TABLE IF NOT EXISTS task_verifications_v1 (
                verification_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('file_observation','job_result','manual_review')),
                passed INTEGER,
                evidence_json TEXT NOT NULL,
                revision_json TEXT NOT NULL,
                stale INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            )""")
            catalog.db.execute("CREATE INDEX IF NOT EXISTS task_verification_owner ON task_verifications_v1(conversation_id,task_id,created_at DESC)")
            catalog.db.execute("""CREATE TABLE IF NOT EXISTS task_job_preparations_v1 (
                preparation_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                root TEXT NOT NULL,
                paths_json TEXT NOT NULL,
                fingerprints_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('prepared','linked','verified','stale')),
                job_id TEXT UNIQUE,
                check_sha256 TEXT,
                prepared_at REAL NOT NULL,
                linked_at REAL,
                verified_at REAL
            )""")
            catalog.db.execute("CREATE INDEX IF NOT EXISTS task_job_preparation_owner ON task_job_preparations_v1(conversation_id,task_id,prepared_at DESC)")
            columns = {row[1] for row in catalog.db.execute("PRAGMA table_info(structured_tasks_v1)")}
            if "check_command" not in columns:
                catalog.db.execute("ALTER TABLE structured_tasks_v1 ADD COLUMN check_command TEXT")
            preparation_columns = {row[1] for row in catalog.db.execute("PRAGMA table_info(task_job_preparations_v1)")}
            if "check_sha256" not in preparation_columns:
                catalog.db.execute("ALTER TABLE task_job_preparations_v1 ADD COLUMN check_sha256 TEXT")

    @staticmethod
    def _text(value, field, maximum, *, allow_empty=False):
        if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value.strip()):
            qualifier = "string" if allow_empty else "non-empty string"
            raise ValueError(f"{field} must be a {qualifier} up to {maximum} characters")
        if "\x00" in value:
            raise ValueError(f"{field} cannot contain NUL")
        return value

    def _conversation(self, cid: str) -> dict:
        return self.catalog.conversation(cid)

    def _project_root(self, cid: str, supplied_root=None) -> Path:
        from .workspace import Workspace

        conversation = self._conversation(cid)
        if not conversation.get("project_id"):
            raise ValueError("task verification requires a project selected for this conversation")
        project = self.catalog.project(conversation["project_id"])
        project_root = Path(project["root"]).expanduser()
        if project_root.resolve(strict=True) != project_root:
            raise ValueError("selected project root identity changed")
        self._reject_reparse_path(project_root)
        # Keep the original path for identity checks before Workspace resolves it.
        Workspace(project_root)
        if supplied_root is not None:
            selected_root = Path(supplied_root).expanduser()
            if selected_root.resolve(strict=True) != selected_root:
                raise ValueError("verification root identity changed")
            self._reject_reparse_path(selected_root)
            if selected_root != project_root:
                raise ValueError("verification root does not match this conversation's selected project")
        return project_root

    @staticmethod
    def _reject_reparse_path(path: Path) -> None:
        for component in (path, *path.parents):
            if component.is_symlink():
                raise ValueError("project path contains a symbolic link")
            is_junction = getattr(component, "is_junction", None)
            if callable(is_junction) and is_junction():
                raise ValueError("project path contains a junction")

    def _graph(self, cid: str, candidate_id: str | None = None, candidate_deps=()) -> dict[str, list[str]]:
        rows = self.catalog.rows("SELECT task_id,dependencies_json FROM structured_tasks_v1 WHERE conversation_id=?", (cid,))
        graph = {row["task_id"]: json.loads(row["dependencies_json"]) for row in rows}
        if candidate_id is not None:
            if candidate_id in graph:
                raise ValueError("task ID already exists")
            graph[candidate_id] = list(candidate_deps)
        return graph

    def _validate_graph(self, cid: str, candidate_id: str | None = None, candidate_deps=()) -> dict:
        graph = self._graph(cid, candidate_id, candidate_deps)
        for task_id, deps in graph.items():
            if task_id in deps:
                raise ValueError("a task cannot depend on itself")
            for dep in deps:
                if dep not in graph:
                    exists = self.catalog.rows("SELECT conversation_id FROM structured_tasks_v1 WHERE task_id=?", (dep,))
                    if exists:
                        raise ValueError("dependencies must belong to the same conversation")
                    raise ValueError("unknown task dependency")
        visiting = set()
        visited = set()

        def visit(task_id):
            if task_id in visiting:
                raise ValueError("task dependencies contain a cycle")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in graph[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in graph:
            visit(task_id)
        return graph

    def create(self, cid: str, title: str, criteria: str = "", dependencies=(),
               verification_mode: str = "job_exit", check_command: str | None = None) -> dict:
        self._conversation(cid)
        title = self._text(title, "title", 200)
        criteria = self._text(criteria, "criteria", MAX_CRITERIA_CHARS, allow_empty=True)
        if check_command is not None:
            check_command = self._text(check_command, "check", MAX_CHECK_COMMAND_CHARS)
        if verification_mode not in ("job_exit", "manual"):
            raise ValueError("verification_mode must be job_exit or manual")
        if not isinstance(dependencies, (list, tuple)) or len(dependencies) > MAX_DEPENDENCIES:
            raise ValueError(f"dependencies must be a list of at most {MAX_DEPENDENCIES} task IDs")
        deps = []
        for dependency in dependencies:
            dependency = self._text(dependency, "dependency", 128)
            if dependency in deps:
                raise ValueError("duplicate task dependency")
            deps.append(dependency)
        task_id = uuid.uuid4().hex
        if task_id in deps:
            raise ValueError("a task cannot depend on itself")
        now = time.time()
        with self.catalog.lock, self.catalog.db:
            count = self.catalog.db.execute("SELECT COUNT(*) FROM structured_tasks_v1 WHERE conversation_id=?", (cid,)).fetchone()[0]
            if count >= MAX_TASKS_PER_CONVERSATION:
                raise ValueError("conversation task limit reached")
            self._validate_graph(cid, task_id, deps)
            missing = [dep for dep in deps if dep not in self._graph(cid)]
            if missing:
                # _validate_graph has distinguished cross-chat IDs from unknown ones.
                raise ValueError("unknown task dependency")
            dep_states = self._dependency_states(cid, deps)
            state = "blocked" if any(value != "complete" for value in dep_states.values()) else "pending"
            reason = "dependencies are not complete" if state == "blocked" else None
            self.catalog.db.execute("""INSERT INTO structured_tasks_v1(task_id,conversation_id,title,criteria,
                check_command,verification_mode,dependencies_json,state,blocked_reason,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (task_id, cid, title.strip(), criteria, check_command, verification_mode,
                 json.dumps(deps), state, reason, now, now))
        return self._task_row(cid, task_id)

    def set_fields(self, cid: str, task_id: str, fields: dict) -> dict:
        """Serialize metadata validation, dependency checks, and publication."""
        if not isinstance(fields, dict) or set(fields) - {"title", "criteria", "depends_on", "check", "state"}:
            raise ValueError("task fields must contain only supported task metadata")
        with self.catalog.lock, self.catalog.db:
            self._refresh_completed(cid)
            return self._set_fields_locked(cid, task_id, fields)

    def _set_fields_locked(self, cid: str, task_id: str, fields: dict) -> dict:
        """Update v2 task metadata/state and invalidate stale check evidence."""
        if not isinstance(fields, dict):
            raise ValueError("task fields must be an object")
        allowed = {"title", "criteria", "depends_on", "check", "state"}
        if set(fields) - allowed:
            raise ValueError("task contains an unsupported field")
        old = self._owned_task(cid, task_id)
        title = self._text(fields.get("title", old["title"]), "title", 200).strip()
        criteria = self._text(fields.get("criteria", old["criteria"]), "criteria", MAX_CRITERIA_CHARS, allow_empty=True)
        dependencies = fields.get("depends_on", json.loads(old["dependencies_json"]))
        if not isinstance(dependencies, list) or len(dependencies) > MAX_DEPENDENCIES:
            raise ValueError(f"depends_on must be a list of at most {MAX_DEPENDENCIES} task IDs")
        normalized_dependencies = []
        for dependency in dependencies:
            dependency = self._text(dependency, "dependency", 128)
            if dependency in normalized_dependencies:
                raise ValueError("duplicate task dependency")
            normalized_dependencies.append(dependency)
        check_command = fields.get("check", old["check_command"])
        if check_command is not None:
            check_command = self._text(check_command, "check", MAX_CHECK_COMMAND_CHARS)
        requested_state = fields.get("state", old["state"])
        if requested_state not in TASK_STATES:
            raise ValueError("state must be pending, active, blocked, awaiting_review, or complete")

        graph = self._graph(cid)
        graph[task_id] = normalized_dependencies
        self._validate_dependency_graph(cid, graph)
        dependencies_changed = normalized_dependencies != json.loads(old["dependencies_json"])
        criteria_changed = criteria != old["criteria"]
        check_changed = check_command != old["check_command"]
        invalidate = dependencies_changed or criteria_changed or check_changed
        verification_id = None if invalidate else old["current_verification_id"]
        dependency_states = self._dependency_states(cid, normalized_dependencies)
        reason = None
        completed_at = old["completed_at"]
        state = requested_state
        if state in ("active", "complete") and any(value != "complete" for value in dependency_states.values()):
            state = "blocked"
            reason = "dependencies are not complete"
        elif state == "complete":
            candidate = dict(old)
            candidate["current_verification_id"] = verification_id
            receipt, valid = self._current_receipt(candidate)
            if valid and not self._receipt_satisfies_mode(candidate, receipt):
                valid = False
            if not valid:
                state = "awaiting_review"
                reason = "current locally issued verification is required"
            else:
                completed_at = time.time()
        elif invalidate and old["state"] == "complete" and "state" not in fields:
            state = "awaiting_review"
            reason = "task criteria, dependencies, or check command changed"
        if state != "complete":
            completed_at = None
        if "state" in fields and state in ("pending", "active") and state != old["state"]:
            verification_id = None

        now = time.time()
        with self.catalog.lock, self.catalog.db:
            self.catalog.db.execute("""UPDATE structured_tasks_v1 SET title=?,criteria=?,check_command=?,
                dependencies_json=?,state=?,blocked_reason=?,current_verification_id=?,completed_at=?,updated_at=?
                WHERE task_id=? AND conversation_id=?""",
                (title, criteria, check_command, json.dumps(normalized_dependencies), state, reason,
                 verification_id, completed_at, now, task_id, cid))
        self._refresh_completed(cid)
        return self._task_row(cid, task_id)

    def _validate_dependency_graph(self, cid: str, graph: dict[str, list[str]]) -> None:
        visiting = set()
        visited = set()

        def visit(task_id):
            if task_id in visiting:
                raise ValueError("task dependencies contain a cycle")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in graph[task_id]:
                if dependency not in graph:
                    exists = self.catalog.rows("SELECT conversation_id FROM structured_tasks_v1 WHERE task_id=?", (dependency,))
                    if exists:
                        raise ValueError("dependencies must belong to the same conversation")
                    raise ValueError("unknown task dependency")
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in graph:
            visit(task_id)

    def _dependency_states(self, cid: str, dependencies) -> dict[str, str]:
        if not dependencies:
            return {}
        placeholders = ",".join("?" for _ in dependencies)
        rows = self.catalog.rows(f"SELECT task_id,state FROM structured_tasks_v1 WHERE conversation_id=? AND task_id IN ({placeholders})",
                                 (cid, *dependencies))
        return {row["task_id"]: row["state"] for row in rows}

    def _task_row(self, cid: str, task_id: str) -> dict:
        rows = self.catalog.rows("SELECT * FROM structured_tasks_v1 WHERE conversation_id=? AND task_id=?", (cid, task_id))
        if not rows:
            raise KeyError("task not found for this conversation")
        task = self._present(rows[0])
        task["dependency_states"] = self._dependency_states(cid, task["dependencies"])
        task["verification"] = self._verification_summary(cid, task_id)
        return task

    def _present(self, row: dict) -> dict:
        result = dict(row)
        result["dependencies"] = json.loads(result.pop("dependencies_json"))
        check_command = result.pop("check_command", None)
        result["has_check"] = bool(check_command)
        return result

    def list(self, cid: str) -> list[dict]:
        self._conversation(cid)
        rows = self.catalog.rows("SELECT * FROM structured_tasks_v1 WHERE conversation_id=? ORDER BY created_at,task_id LIMIT ?",
                                 (cid, MAX_TASKS_PER_CONVERSATION + 1))[:MAX_TASKS_PER_CONVERSATION]
        graph = self._validate_graph(cid)
        by_id = {row["task_id"]: row for row in rows}
        receipt_state = {}
        for task_id, row in by_id.items():
            task = dict(row)
            receipt, valid = self._current_receipt(task, mark_stale=False)
            if valid and not self._receipt_satisfies_mode(task, receipt):
                valid = False
            receipt_state[task_id] = (receipt, valid)
        effective = {}

        def effective_state(task_id, visiting=None):
            if task_id in effective:
                return effective[task_id]
            visiting = visiting or set()
            if task_id in visiting:
                raise ValueError("task dependencies contain a cycle")
            visiting.add(task_id)
            row = by_id[task_id]
            state = row["state"]
            if state == "complete":
                if any(effective_state(dep, visiting) != "complete" for dep in graph.get(task_id, [])):
                    state = "blocked"
                elif not receipt_state[task_id][1]:
                    state = "awaiting_review"
            visiting.remove(task_id)
            effective[task_id] = state
            return state

        result = []
        for row in rows:
            task_id = row["task_id"]
            item = self._present(row)
            for field in ("title", "criteria", "details"):
                original = item[field]
                item[field] = original[:MAX_LIST_FIELD_CHARS]
                item[field + "_truncated"] = len(original) > len(item[field])
            item["state"] = effective_state(task_id)
            item["dependency_states"] = {dep: effective_state(dep) for dep in graph.get(task_id, [])}
            item["verification"] = self._verification_summary(cid, task_id)
            receipt, _valid = receipt_state[task_id]
            if item["verification"] is not None and receipt is not None:
                item["verification"]["stale"] = bool(receipt["stale"] or receipt.get("stale_now", False))
            if item["state"] == "blocked" and row["state"] == "complete":
                item["blocked_reason"] = "a dependency is no longer complete"
            elif item["state"] == "awaiting_review" and row["state"] == "complete":
                item["blocked_reason"] = "verification is missing, stale, or needs local review"
            if item["state"] != "complete":
                item["completed_at"] = None
            result.append(item)
        return result

    def _owned_task(self, cid: str, task_id: str) -> dict:
        self._conversation(cid)
        rows = self.catalog.rows("SELECT * FROM structured_tasks_v1 WHERE conversation_id=? AND task_id=?", (cid, task_id))
        if not rows:
            raise KeyError("task not found for this conversation")
        return rows[0]

    def _verification_summary(self, cid: str, task_id: str) -> dict | None:
        rows = self.catalog.rows("""SELECT v.* FROM structured_tasks_v1 t JOIN task_verifications_v1 v
            ON v.verification_id=t.current_verification_id WHERE t.conversation_id=? AND t.task_id=?""", (cid, task_id))
        if not rows:
            return None
        row = rows[0]
        evidence = json.loads(row["evidence_json"])
        if isinstance(evidence.get("note"), str):
            original_note = evidence["note"]
            evidence["note"] = original_note[:MAX_LIST_FIELD_CHARS]
            evidence["note_truncated"] = len(original_note) > len(evidence["note"])
        revision = json.loads(row["revision_json"])
        files = revision.get("files", [])
        revision_root = revision.get("root")
        revision_summary = {"root": revision_root[:128] if isinstance(revision_root, str) else revision_root,
                            "root_truncated": isinstance(revision_root, str) and len(revision_root) > 128,
                            "file_count": len(files),
                            "fingerprints_sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest(),
                            "files": [{**files[0], "path": files[0]["path"][:64],
                                       "path_truncated": len(files[0]["path"]) > 64}] if files else [],
                            "truncated": len(files) > 1}
        return {"verification_id": row["verification_id"], "kind": row["kind"],
                "passed": None if row["passed"] is None else bool(row["passed"]),
                "stale": bool(row["stale"]), "created_at": row["created_at"],
                "evidence": evidence, "revision": revision_summary}

    def _fingerprints(self, cid: str, root, paths) -> list[dict]:
        from .workspace import Workspace

        if not isinstance(paths, (list, tuple)) or len(paths) > MAX_VERIFY_PATHS:
            raise ValueError(f"paths must be a list of at most {MAX_VERIFY_PATHS} project-relative paths")
        project_root = self._project_root(cid, root)
        workspace = Workspace(project_root)
        fingerprints = []
        seen = set()
        for path in paths:
            if not isinstance(path, str) or not path or len(path) > MAX_VERIFY_PATH_CHARS:
                raise ValueError(f"verification paths must be non-empty relative strings up to {MAX_VERIFY_PATH_CHARS} characters")
            candidate = Path(path)
            if candidate.is_absolute():
                raise ValueError("verification paths must be project-relative")
            target = workspace._validate_operation_path(candidate, write=True)
            relative = target.relative_to(workspace.root).as_posix()
            if relative in seen:
                raise ValueError("verification paths must be unique after resolution")
            seen.add(relative)
            try:
                info = target.stat()
            except FileNotFoundError:
                fingerprints.append({"path": relative, "exists": False, "sha256": None, "size": 0})
                continue
            if not target.is_file():
                raise ValueError("verification paths must name regular files")
            if info.st_size > MAX_VERIFIED_FILE_BYTES:
                raise ValueError(f"verification file exceeds {MAX_VERIFIED_FILE_BYTES} byte limit")
            if target.resolve(strict=False) != target:
                raise ValueError("verification file identity changed")
            with target.open("rb") as stream:
                data = stream.read(MAX_VERIFIED_FILE_BYTES + 1)
            if len(data) > MAX_VERIFIED_FILE_BYTES:
                raise ValueError(f"verification file exceeds {MAX_VERIFIED_FILE_BYTES} byte limit")
            if target.resolve(strict=False) != target:
                raise ValueError("verification file identity changed")
            fingerprints.append({"path": relative, "exists": True,
                                 "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
        return fingerprints

    def _expected_checks(self, cid: str, task_id: str, root, checks) -> tuple[list[dict], bool]:
        if not isinstance(checks, (list, tuple)) or not checks or len(checks) > MAX_VERIFY_PATHS:
            raise ValueError(f"checks must contain 1..{MAX_VERIFY_PATHS} file checks")
        from .workspace import Workspace
        project_root = self._project_root(cid, root)
        workspace = Workspace(project_root)
        paths, expected = [], {}
        for check in checks:
            if not isinstance(check, dict) or not isinstance(check.get("path"), str):
                raise ValueError("each check requires a relative path")
            path = check["path"]
            candidate = Path(path)
            if candidate.is_absolute():
                raise ValueError("verification paths must be project-relative")
            target = workspace._validate_operation_path(candidate, write=True)
            canonical_path = target.relative_to(workspace.root).as_posix()
            exists = check.get("exists", True)
            if not isinstance(exists, bool):
                raise ValueError("check exists must be a boolean")
            digest = check.get("sha256")
            if exists and (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)):
                raise ValueError("existing-file checks require a 64-character SHA-256")
            if not exists and digest is not None:
                raise ValueError("absent-file checks cannot include a SHA-256")
            paths.append(path)
            expected[canonical_path] = (exists, digest.lower() if isinstance(digest, str) else None)
        fingerprints = self._fingerprints(cid, project_root, paths)
        actual_by_path = {item["path"]: item for item in fingerprints}
        matched = True
        for canonical_path, (exists, digest) in expected.items():
            actual = actual_by_path.get(canonical_path)
            if actual is None or bool(actual["exists"]) != exists or (exists and actual["sha256"] != digest):
                matched = False
        return fingerprints, matched

    def _insert_receipt(self, cid, task_id, kind, passed, evidence, revision, *, stale=False) -> str:
        receipt_id = uuid.uuid4().hex
        with self.catalog.lock, self.catalog.db:
            count = self.catalog.db.execute("SELECT COUNT(*) FROM task_verifications_v1 WHERE conversation_id=? AND task_id=?",
                                            (cid, task_id)).fetchone()[0]
            if count >= MAX_RECEIPTS_PER_TASK:
                raise ValueError("task verification receipt limit reached")
            self.catalog.db.execute("""INSERT INTO task_verifications_v1(verification_id,task_id,conversation_id,
                kind,passed,evidence_json,revision_json,stale,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (receipt_id, task_id, cid, kind, None if passed is None else int(bool(passed)),
                 json.dumps(evidence, ensure_ascii=False), json.dumps(revision, ensure_ascii=False), int(stale), time.time()))
            if kind in ("job_result", "manual_review"):
                self.catalog.db.execute("UPDATE structured_tasks_v1 SET current_verification_id=?,updated_at=? WHERE task_id=? AND conversation_id=?",
                                        (receipt_id, time.time(), task_id, cid))
        return receipt_id

    def check_files(self, cid: str, task_id: str, root, checks) -> dict:
        """Record file/hash observations; these do not certify semantic criteria."""
        self._owned_task(cid, task_id)
        fingerprints, matched = self._expected_checks(cid, task_id, root, checks)
        evidence = {"check_count": len(checks), "matched_expected_state": matched,
                    "semantic_acceptance": False}
        receipt_id = self._insert_receipt(cid, task_id, "file_observation", None, evidence,
                                          {"root": str(self._project_root(cid, root)), "files": fingerprints})
        return {"verification_id": receipt_id, "kind": "file_observation", "matched": matched,
                "semantic_acceptance": False, "files": fingerprints}

    def prepare_job(self, cid: str, task_id: str, root, paths=()) -> dict:
        """Durably snapshot check inputs before a job is started."""
        self._owned_task(cid, task_id)
        project_root = self._project_root(cid, root)
        fingerprints = self._fingerprints(cid, project_root, paths)
        preparation_id = uuid.uuid4().hex
        prepared_at = time.time()
        normalized_paths = [item["path"] for item in fingerprints]
        with self.catalog.lock, self.catalog.db:
            self.catalog.db.execute("""INSERT INTO task_job_preparations_v1(preparation_id,task_id,
                conversation_id,root,paths_json,fingerprints_json,state,prepared_at)
                VALUES(?,?,?,?,?,?,'prepared',?)""",
                (preparation_id, task_id, cid, str(project_root), json.dumps(normalized_paths),
                 json.dumps(fingerprints), prepared_at))
        return {"preparation_id": preparation_id, "task_id": task_id,
                "conversation_id": cid, "root": str(project_root),
                "paths": normalized_paths, "files": fingerprints,
                "file_bound": bool(normalized_paths), "prepared_at": prepared_at}

    def link_job_verification(self, cid: str, task_id: str, job_id: str, prepared: dict) -> dict:
        """Bind the pre-job snapshot to the same-chat job admitted by the router."""
        self._owned_task(cid, task_id)
        if not isinstance(job_id, str) or not job_id or len(job_id) > 128 or not isinstance(prepared, dict):
            raise ValueError("job_id up to 128 characters and a prepared snapshot are required")
        preparation_id = prepared.get("preparation_id")
        if not isinstance(preparation_id, str) or not preparation_id:
            raise ValueError("prepared snapshot has no preparation_id")
        with self.catalog.lock, self.catalog.db:
            try:
                owner = self.catalog.db.execute("SELECT conversation_id FROM job_links WHERE job_id=?", (job_id,)).fetchone()
            except Exception as exc:
                raise ValueError("job ownership links are unavailable") from exc
            if owner is None or owner["conversation_id"] != cid:
                raise ValueError("job does not belong to this conversation")
            row = self.catalog.db.execute("SELECT * FROM task_job_preparations_v1 WHERE preparation_id=? AND task_id=? AND conversation_id=?",
                                          (preparation_id, task_id, cid)).fetchone()
            if row is None:
                raise ValueError("prepared snapshot does not belong to this task and conversation")
            if prepared.get("root") != row["root"] or prepared.get("files") != json.loads(row["fingerprints_json"]):
                raise ValueError("prepared snapshot was changed after it was captured")
            existing = self.catalog.db.execute("SELECT * FROM task_job_preparations_v1 WHERE job_id=?",
                                               (job_id,)).fetchone()
            if existing is not None:
                existing_paths = json.loads(existing["paths_json"])
                incoming_paths = json.loads(row["paths_json"])
                if (existing["task_id"] != task_id or existing["conversation_id"] != cid or
                        existing["root"] != row["root"] or existing_paths != incoming_paths):
                    raise ValueError("job is already linked to a different task or verification path scope")
                if existing["preparation_id"] != preparation_id:
                    self.catalog.db.execute("UPDATE task_job_preparations_v1 SET state='stale' WHERE preparation_id=? AND state='prepared'",
                                            (preparation_id,))
                return {"linked": True, "reused_existing_snapshot": True,
                        "job_id": job_id, "task_id": task_id,
                        "preparation_id": existing["preparation_id"],
                        "preparation_state": existing["state"],
                        "file_bound": bool(existing_paths), "file_count": len(existing_paths)}
            if row["job_id"] and row["job_id"] != job_id:
                raise ValueError("prepared snapshot is already linked to another job")
            if row["state"] == "stale":
                raise ValueError("prepared snapshot is no longer linkable")
            linked_at = row["linked_at"] or time.time()
            self.catalog.db.execute("UPDATE task_job_preparations_v1 SET state='linked',job_id=?,linked_at=? WHERE preparation_id=?",
                                    (job_id, linked_at, preparation_id))
        return {"linked": True, "job_id": job_id, "task_id": task_id,
                "preparation_id": preparation_id,
                "file_bound": bool(json.loads(row["paths_json"])),
                "file_count": len(json.loads(row["paths_json"]))}

    def bind_check_command(self, preparation_id: str, cid: str, task_id: str, digest: str) -> None:
        """Bind an idempotent prepared check to one exact command fingerprint."""
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("check command fingerprint is invalid")
        with self.catalog.lock, self.catalog.db:
            row = self.catalog.db.execute("SELECT check_sha256 FROM task_job_preparations_v1 "
                                          "WHERE preparation_id=? AND task_id=? AND conversation_id=?",
                                          (preparation_id, task_id, cid)).fetchone()
            if row is None:
                raise ValueError("prepared check does not belong to this task and conversation")
            if row["check_sha256"] and row["check_sha256"] != digest:
                raise ValueError("prepared check command cannot be changed")
            self.catalog.db.execute("UPDATE task_job_preparations_v1 SET check_sha256=? WHERE preparation_id=?",
                                    (digest, preparation_id))

    def verify_job(self, cid: str, task_id: str, job_manager, job_id: str,
                   root, paths=()) -> dict:
        """Issue bounded job evidence; file-bound jobs must use their pre-run snapshot."""
        task = self._owned_task(cid, task_id)
        if not isinstance(job_id, str) or not job_id or len(job_id) > 128:
            raise ValueError("job_id must be a non-empty string up to 128 characters")
        with self.catalog.lock:
            try:
                rows = self.catalog.db.execute("SELECT conversation_id FROM job_links WHERE job_id=?", (job_id,)).fetchall()
            except Exception as exc:
                raise ValueError("job ownership links are unavailable") from exc
        if not rows or rows[0]["conversation_id"] != cid:
            raise ValueError("job does not belong to this conversation")
        result = job_manager.status(job_id, 0)
        if not isinstance(result, dict) or result.get("job_id") != job_id:
            raise ValueError("JobManager returned an invalid result")
        state = result.get("state")
        exit_code = result.get("exit_code")
        passed = state == "succeeded" and isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code == 0
        if state not in ("succeeded", "failed", "cancelled", "interrupted", "outcome_unknown"):
            raise ValueError("job must reach a terminal state before task verification")
        project_root = self._project_root(cid, root)
        if not isinstance(paths, (list, tuple)):
            raise ValueError("paths must be a list of prepared project-relative paths")
        if paths:
            canonical_requested = [item["path"] for item in self._fingerprints(cid, project_root, paths)]
            prep_rows = self.catalog.rows("SELECT * FROM task_job_preparations_v1 WHERE job_id=? AND task_id=? AND conversation_id=? AND state IN ('linked','verified','stale')",
                                          (job_id, task_id, cid))
            if not prep_rows:
                raise ValueError("file-bound verification requires a prepared snapshot linked to this job")
            prep = prep_rows[0]
            if canonical_requested != json.loads(prep["paths_json"]):
                raise ValueError("verify_job paths do not match the linked pre-job snapshot")
        else:
            prep_rows = self.catalog.rows("SELECT * FROM task_job_preparations_v1 WHERE job_id=? AND task_id=? AND conversation_id=? AND state IN ('linked','verified','stale')",
                                          (job_id, task_id, cid))
            prep = prep_rows[0] if prep_rows else None
        if prep is not None:
            if Path(prep["root"]).resolve(strict=True) != project_root:
                raise ValueError("prepared root no longer matches the selected project")
            prepared_files = json.loads(prep["fingerprints_json"])
            current_files = self._fingerprints(cid, project_root, [item["path"] for item in prepared_files])
            files_unchanged = current_files == prepared_files and prep["state"] != "stale"
            fingerprints = prepared_files
            file_bound = bool(prepared_files)
            if file_bound and not files_unchanged:
                passed = False
        else:
            current_files = []
            fingerprints = []
            files_unchanged = True
            file_bound = False
        if paths and not file_bound:
            raise ValueError("file-bound verification requires at least one prepared input path")
        output = result.get("output", "")
        output_digest = hashlib.sha256(output.encode("utf-8", errors="replace")).hexdigest() if isinstance(output, str) else None
        evidence = {"job_id": job_id, "job_state": state, "exit_code": exit_code,
                    "output_sha256_page": output_digest,
                    "output_end": result.get("output_end"),
                    "output_cursor": result.get("cursor"),
                    "output_truncated": bool(result.get("truncated", False)), "passed": passed,
                    "file_bound": file_bound, "inputs_unchanged": files_unchanged,
                    "verification_scope": ("job exit success and pre-job input fingerprints stayed unchanged; not an independent semantic judgment"
                                           if file_bound else "job exit status only; no file inputs were bound; not an independent semantic judgment")}
        revision = {"root": str(project_root), "files": fingerprints}
        receipt_id = self._insert_receipt(cid, task_id, "job_result", passed, evidence, revision,
                                          stale=file_bound and not files_unchanged)
        if prep is not None:
            with self.catalog.lock, self.catalog.db:
                self.catalog.db.execute("UPDATE task_job_preparations_v1 SET state=?,verified_at=? WHERE preparation_id=?",
                                        ("verified" if files_unchanged else "stale", time.time(), prep["preparation_id"]))
        if task["state"] == "complete" and not passed:
            with self.catalog.lock, self.catalog.db:
                self.catalog.db.execute("UPDATE structured_tasks_v1 SET state='awaiting_review',blocked_reason=?,completed_at=NULL,updated_at=? WHERE task_id=? AND conversation_id=?",
                                        ("latest job verification failed", time.time(), task_id, cid))
        else:
            self._refresh_completed(cid)
        return {"verification_id": receipt_id, "task_id": task["task_id"],
                "kind": "job_result", "passed": passed,
                "verification_mode": task["verification_mode"], "evidence": evidence,
                "files": fingerprints, "current_files": current_files}

    def accept_review(self, cid: str, task_id: str, note: str = "", root=None, paths=()) -> dict:
        """Record an explicit local review. Do not expose this as a Muse tool."""
        task = self._owned_task(cid, task_id)
        note = self._text(note, "review note", 2000, allow_empty=True)
        dependencies = json.loads(task["dependencies_json"])
        states = self._dependency_states(cid, dependencies)
        if any(state != "complete" for state in states.values()):
            raise ValueError("manual review cannot accept a task with incomplete dependencies")
        if root is None and not paths and task["current_verification_id"]:
            prior = self.catalog.rows("SELECT revision_json FROM task_verifications_v1 WHERE verification_id=? AND conversation_id=?",
                                      (task["current_verification_id"], cid))
            if prior:
                prior_revision = json.loads(prior[0]["revision_json"])
                root = prior_revision.get("root")
                paths = [item["path"] for item in prior_revision.get("files", [])]
        revision_root = self._project_root(cid, root) if root is not None else None
        fingerprints = self._fingerprints(cid, revision_root, paths) if paths else []
        revision = {"root": str(revision_root) if revision_root is not None else None,
                    "files": fingerprints}
        receipt_id = self._insert_receipt(cid, task_id, "manual_review", True,
                                          {"note": note, "accepted_locally": True}, revision)
        result = self.update(cid, task_id, "complete", details=task["details"])
        result["verification_id"] = receipt_id
        return result

    def _current_receipt(self, task: dict, *, mark_stale: bool = True) -> tuple[dict | None, bool]:
        receipt_id = task["current_verification_id"]
        if not receipt_id:
            return None, False
        rows = self.catalog.rows("SELECT * FROM task_verifications_v1 WHERE verification_id=? AND task_id=? AND conversation_id=?",
                                 (receipt_id, task["task_id"], task["conversation_id"]))
        if not rows:
            return None, False
        receipt = rows[0]
        if not receipt["passed"] or receipt["stale"]:
            return receipt, False
        revision = json.loads(receipt["revision_json"])
        root = revision.get("root")
        if root is not None:
            try:
                project_root = self._project_root(task["conversation_id"])
                if Path(root).resolve(strict=True) != project_root:
                    raise ValueError("project root changed")
                current = self._fingerprints(task["conversation_id"], project_root,
                                             [item["path"] for item in revision.get("files", [])])
                if current != revision.get("files", []):
                    raise ValueError("verified file fingerprints changed")
            except (ValueError, OSError, FileNotFoundError):
                receipt["stale_now"] = True
                if mark_stale:
                    with self.catalog.lock, self.catalog.db:
                        self.catalog.db.execute("UPDATE task_verifications_v1 SET stale=1 WHERE verification_id=?", (receipt_id,))
                return receipt, False
        return receipt, True

    def _refresh_completed(self, cid: str) -> None:
        rows = self.catalog.rows("SELECT * FROM structured_tasks_v1 WHERE conversation_id=?", (cid,))
        graph = self._validate_graph(cid)
        by_id = {row["task_id"]: row for row in rows}
        # Mark stale evidence on every task so the list shows the invalidation,
        # even before a completion attempt is made.
        for task in by_id.values():
            if task["current_verification_id"]:
                self._current_receipt(task)
        for task_id, task in by_id.items():
            if task["state"] != "complete":
                continue
            dependencies = graph.get(task_id, [])
            if any(dep not in by_id or by_id[dep]["state"] != "complete" for dep in dependencies):
                with self.catalog.lock, self.catalog.db:
                    self.catalog.db.execute("UPDATE structured_tasks_v1 SET state='blocked',blocked_reason=?,completed_at=NULL,updated_at=? WHERE task_id=?",
                                            ("a dependency is no longer complete", time.time(), task_id))
                task["state"] = "blocked"
                continue
            receipt, valid = self._current_receipt(task)
            if valid and not self._receipt_satisfies_mode(task, receipt):
                valid = False
            if not valid:
                with self.catalog.lock, self.catalog.db:
                    self.catalog.db.execute("UPDATE structured_tasks_v1 SET state='awaiting_review',blocked_reason=?,completed_at=NULL,updated_at=? WHERE task_id=?",
                                            ("verification is missing or stale", time.time(), task_id))
                task["state"] = "awaiting_review"

    def update(self, cid: str, task_id: str, state: str, details: str | None = None) -> dict:
        task = self._owned_task(cid, task_id)
        if state not in TASK_STATES:
            raise ValueError("state must be pending, active, blocked, awaiting_review, or complete")
        if details is not None:
            details = self._text(details, "details", MAX_DETAILS_CHARS, allow_empty=True)
        with self.catalog.lock, self.catalog.db:
            self._refresh_completed(cid)
            task = self._owned_task(cid, task_id)
            deps = json.loads(task["dependencies_json"])
            dep_states = self._dependency_states(cid, deps)
            current_state = task["state"]
            blocked_reason = None
            completed_at = task["completed_at"]
            current_verification = task["current_verification_id"]
            final_state = state

            if state == "active" and any(dep_state != "complete" for dep_state in dep_states.values()):
                final_state = "blocked"
                blocked_reason = "dependencies are not complete"
            elif state == "complete":
                if any(dep_state != "complete" for dep_state in dep_states.values()):
                    final_state = "blocked"
                    blocked_reason = "dependencies are not complete"
                else:
                    receipt, valid = self._current_receipt(task)
                    if valid and not self._receipt_satisfies_mode(task, receipt):
                        valid = False
                    if not valid:
                        final_state = "awaiting_review"
                        blocked_reason = "current locally issued verification is required"
                    else:
                        final_state = "complete"
                        completed_at = time.time()
            if final_state != "complete":
                completed_at = None
            if state in ("pending", "active") and state != current_state:
                current_verification = None
            resulting_details = task["details"] if details is None else details
            self.catalog.db.execute("""UPDATE structured_tasks_v1 SET state=?,details=?,blocked_reason=?,
                current_verification_id=?,completed_at=?,updated_at=? WHERE task_id=? AND conversation_id=?""",
                (final_state, resulting_details, blocked_reason, current_verification,
                 completed_at, time.time(), task_id, cid))
        return self._task_row(cid, task_id)

    @staticmethod
    def _receipt_satisfies_mode(task: dict, receipt: dict | None) -> bool:
        if receipt is None:
            return False
        kind = receipt["kind"]
        if task["verification_mode"] == "manual":
            return kind == "manual_review" and bool(receipt["passed"])
        return kind in ("job_result", "manual_review") and bool(receipt["passed"])
