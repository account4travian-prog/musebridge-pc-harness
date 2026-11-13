"""Compact adapters for v2 task and change tools."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import time
import uuid


INLINE_VERIFY_WAIT_S = 25.0
VERIFY_JOB_TIMEOUT_S = 3600.0
MAX_TASK_BATCH = 20
MAX_REVIEW_EDITS = 100
MAX_REVIEW_BYTES = 18000
MAX_REVIEW_DIFF_CHARS = 5000
MAX_CHECKPOINT_HISTORY = 5000


class CoreToolError(ValueError):
    """A safe, typed refusal from a v2 adapter."""

    def __init__(self, code: str, message: str, hint: str = ""):
        self.code = code
        self.hint = hint
        self.no_effect = True
        super().__init__(message)


class CoreToolsV2:
    """Implement task and per-edit change operations over existing stores."""

    def __init__(self, router):
        self.router = router
        self.inline_wait_s = INLINE_VERIFY_WAIT_S

    def call(self, name: str, cid: str, params: dict, run_id: str | None, guard) -> dict:
        """Dispatch a v2 task/change action and return its bounded payload."""
        if not isinstance(params, dict):
            raise CoreToolError("bad_arguments", "Parameters must be an object.")
        params = copy.deepcopy(params)
        if name == "task.set":
            return self._task_set(cid, params, guard)
        if name == "task.verify":
            result = self._task_verify(cid, params, guard, run_id)
            if result.get('verification', {}).get('passed'):
                self.router.finish_line.approve_check(result['job_id'])
            return result
        if name == "change.review":
            return self._change_review(cid, params, run_id)
        if name == "change.restore":
            return self._change_restore(cid, params, run_id, guard)
        raise CoreToolError("unknown_tool", "Unsupported v2 task/change operation.")

    def _task_set(self, cid: str, params: dict, guard) -> dict:
        rows = params.get("tasks")
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_TASK_BATCH:
            raise CoreToolError("bad_arguments", f"tasks must contain 1..{MAX_TASK_BATCH} task objects.")
        results = []
        succeeded = 0
        failed = 0
        for index, item in enumerate(rows):
            task_id = item.get("id") if isinstance(item, dict) else None
            try:
                with self.router.execution.lock:
                    guard()
                    results.append(self._set_one_task(cid, index, item, task_id))
                succeeded += 1
            except Exception as exc:
                results.append({"index": index, "ok": False, "task_id": task_id,
                                "error": {"code": getattr(exc, "code", type(exc).__name__),
                                          "message": str(exc)[:300]}})
                failed += 1
        full_tasks = self.router.tasks.list(cid)
        return {"items": results, "tasks": full_tasks, "partial": bool(succeeded and failed),
                "all_succeeded": failed == 0, "succeeded": succeeded, "failed": failed}

    def _set_one_task(self, cid, index, item, task_id):
        if not isinstance(item, dict):
            raise ValueError("each task must be an object")
        allowed = {"id", "title", "criteria", "depends_on", "state", "check"}
        if set(item) - allowed:
            raise ValueError("task contains an unsupported field")
        if "id" in item:
            if not isinstance(task_id, str) or not task_id:
                raise ValueError("id must be a non-empty task ID")
            task = self.router.tasks.set_fields(cid, task_id, {k: v for k, v in item.items() if k != "id"})
            created = False
        else:
            if "title" not in item:
                raise ValueError("new tasks require a title")
            if "state" in item and item["state"] not in (
                    "pending", "active", "blocked", "awaiting_review", "complete"):
                raise ValueError("state must be pending, active, blocked, awaiting_review, or complete")
            if item.get("state") == "complete":
                raise ValueError("new tasks cannot be completed before they have current verification")
            task = self.router.tasks.create(
                cid,
                item["title"],
                item.get("criteria", ""),
                item.get("depends_on", []),
                check_command=item.get("check"),
            )
            created = True
            task_id = task["task_id"]
            if "state" in item:
                task = self.router.tasks.set_fields(cid, task_id, {"state": item["state"]})
        return {"index": index, "ok": True, "created": created,
                "task_id": task_id, "task": task}

    def _task_verify(self, cid: str, params: dict, guard, run_id=None) -> dict:
        task_id = params.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise CoreToolError("bad_arguments", "task_id is required.")
        task = self.router.tasks._owned_task(cid, task_id)
        if task["verification_mode"] == "manual":
            raise CoreToolError("manual_review_required",
                                "This task requires local owner review; Muse cannot certify it.")
        command = params.get("command") or task.get("check_command")
        if command is not None:
            if not isinstance(command, str) or not command.strip():
                raise CoreToolError("bad_arguments", "command must be a non-empty check script.")
            if len(command.encode("utf-8")) > 128000:
                raise CoreToolError("bad_arguments", "Check command exceeds 128000 bytes.")
        paths = params.get("paths", [])
        if not isinstance(paths, list):
            raise CoreToolError("bad_arguments", "paths must be a list of project-relative files.")
        context = self.router.catalog.context(cid)
        if not context["project"]:
            raise CoreToolError("project_required", "Select a project before verifying a task.")
        root = context["project"]["root"]
        command_hash = hashlib.sha256(command.encode("utf-8")).hexdigest() if command is not None else None
        requested_job = params.get("job_id")
        if requested_job is not None and (not isinstance(requested_job, str) or not requested_job):
            raise CoreToolError("bad_arguments", "job_id must be a non-empty owned check job ID.")
        preparation, job_id, prep_state = self._find_or_prepare(
            cid, task_id, root, paths, command_hash, command, guard, requested_job, run_id,
        )
        if prep_state == "verified":
            receipt = self._receipt_for_job(cid, task_id, job_id, root, preparation["paths"])
            return self._task_verify_payload(cid, {
                "ready": True, "running": False, "job_id": job_id,
                "output_tail": "", "verification": receipt,
                "already_verified": True,
            })
        output = self._wait_check(job_id, guard)
        if output["running"]:
            return self._task_verify_payload(cid, {
                "ready": False, "running": True, "job_id": job_id,
                "output_tail": output["output_tail"],
                "hint": "Wait for this check to finish, then call task.verify again; it will reuse the same job.",
                "preparation_id": preparation["preparation_id"],
            })

        guard()
        hook_source = task_id + ":" + job_id
        hook_jobs = self.router.hooks.run_event(cid, "before_verification", hook_source)
        if any(item.get("state") == "outcome_unknown" for item in hook_jobs.get("skipped", [])):
            raise CoreToolError("hook_outcome_unknown", "A verification hook has an unknown outcome; inspect it before retrying.")
        if any(item.get("reason") in ("work is paused", "admission revoked by Stop Work")
               for item in hook_jobs.get("skipped", [])):
            raise CoreToolError("paused", "Verification is paused locally; no receipt was issued.")
        if any("read_only profile" in item.get("reason", "") for item in hook_jobs.get("skipped", [])):
            raise CoreToolError("read_only", "The current profile does not allow verification hooks; no receipt was issued.")
        hook_statuses = [self.router.jobs.status(item["job_id"]) for item in hook_jobs.get("jobs", []) if item.get("job_id")]
        if any(item["state"] in ("accepted", "running") for item in hook_statuses):
            return self._task_verify_payload(cid, {
                "ready": False, "running": False, "job_id": job_id,
                "output_tail": output["output_tail"], "hook_jobs": hook_jobs["jobs"],
                "hint": "Wait for verification hooks to finish, then call task.verify again; the check will not be relaunched.",
            })
        if any(item["state"] != "succeeded" or item["exit_code"] != 0 for item in hook_statuses):
            raise CoreToolError("verification_hook_failed", "A before-verification hook failed; no task receipt was issued.")

        with self.router.execution.lock:
            guard()
            verification = self.router.tasks.verify_job(cid, task_id, self.router.jobs, job_id, root, paths)
        return self._task_verify_payload(cid, {
            "ready": True, "running": False, "job_id": job_id,
            "output_tail": output["output_tail"], "exit_code": output["exit_code"],
            "verification": verification,
        })

    def _find_or_prepare(self, cid, task_id, root, paths, command_hash, command, guard, requested_job=None, run_id=None):
        if requested_job:
            preparations = self.router.catalog.rows(
                "SELECT * FROM task_job_preparations_v1 WHERE conversation_id=? AND task_id=? "
                "AND job_id=? AND state IN ('linked','verified','stale') ORDER BY prepared_at DESC LIMIT 1",
                (cid, task_id, requested_job),
            )
            if not preparations:
                raise CoreToolError("job_not_owned", "job_id is not a prepared verification job for this task and conversation.")
        else:
            preparations = self.router.catalog.rows(
                "SELECT * FROM task_job_preparations_v1 WHERE conversation_id=? AND task_id=? "
                "AND state IN ('prepared','linked') ORDER BY prepared_at DESC LIMIT 1", (cid, task_id),
            )
        if preparations:
            row = preparations[0]
            stored_paths = json.loads(row["paths_json"])
            if paths:
                incoming = [item["path"] for item in self.router.tasks._fingerprints(cid, root, paths)]
                if incoming != stored_paths:
                    job = self.router.jobs.status(row["job_id"]) if row["job_id"] else None
                    if job and job["state"] in ("accepted", "running"):
                        raise CoreToolError("check_in_progress", "A different check is already running for this task.")
                    raise CoreToolError("check_retry_mismatch", "Resume the existing check with its original paths before starting another.")
            stored_hash = row["check_sha256"]
            if stored_hash and command_hash and stored_hash != command_hash:
                raise CoreToolError("check_retry_mismatch", "A different check is already prepared; finish or inspect it before changing the command.")
            if command_hash:
                self.router.tasks.bind_check_command(row["preparation_id"], cid, task_id, command_hash)
            prepared = {"preparation_id": row["preparation_id"], "root": row["root"],
                        "files": json.loads(row["fingerprints_json"]), "paths": stored_paths}
            job_id = row["job_id"]
            if not job_id:
                if not command:
                    raise CoreToolError("check_required", "Repeat the check command to resume its prepared job.")
                job_id = self._start_check(cid, task_id, root, command, prepared, guard, run_id)
                row = {**dict(row), "state": "linked"}
            return prepared, job_id, "verified" if row["state"] in ("verified", "stale") else row["state"]

        with self.router.execution.lock:
            guard()
        if not command or not command_hash:
            raise CoreToolError("check_required", "Provide a check command or save one with task.set.")
        prepared = self.router.tasks.prepare_job(cid, task_id, root, paths)
        self.router.tasks.bind_check_command(prepared["preparation_id"], cid, task_id, command_hash)
        job_id = self._start_check(cid, task_id, root, command, prepared, guard, run_id)
        return prepared, job_id, "linked"

    def _start_check(self, cid, task_id, root, command, prepared, guard, run_id=None):
        request_id = f"{cid}:task-verify:{prepared['preparation_id']}"
        with self.router.execution.lock:
            guard()
            job = self.router.jobs.start(request_id, command, root, timeout_s=VERIFY_JOB_TIMEOUT_S)
            job_id = job["job_id"]
            if run_id:
                self.router.finish_line.watch_check(cid, run_id, job_id, require_receipt=True)
            with self.router.catalog.lock, self.router.catalog.db:
                self.router.catalog.db.execute("INSERT OR IGNORE INTO job_links(job_id,conversation_id) VALUES(?,?)",
                                               (job_id, cid))
            self.router.tasks.link_job_verification(cid, task_id, job_id, prepared)
        return job_id

    def _receipt_for_job(self, cid, task_id, job_id, root, paths):
        """Refresh the stored job evidence against the job and current input files."""
        rows = self.router.catalog.rows(
            "SELECT * FROM task_verifications_v1 WHERE conversation_id=? AND task_id=? "
            "AND kind='job_result' ORDER BY created_at DESC LIMIT 100", (cid, task_id),
        )
        prior = None
        for row in rows:
            evidence = json.loads(row["evidence_json"])
            if evidence.get("job_id") == job_id:
                prior = row
                break

        prep_rows = self.router.catalog.rows(
            "SELECT * FROM task_job_preparations_v1 WHERE conversation_id=? AND task_id=? "
            "AND job_id=? AND state IN ('linked','verified','stale') ORDER BY prepared_at DESC LIMIT 1",
            (cid, task_id, job_id),
        )
        if prior is not None and prep_rows:
            prep = prep_rows[0]
            project_root = self.router.tasks._project_root(cid, root)
            if Path(prep["root"]).resolve(strict=True) != project_root:
                raise CoreToolError("project_changed", "The prepared check belongs to a different project root.")
            prepared_files = json.loads(prep["fingerprints_json"])
            current_files = self.router.tasks._fingerprints(
                cid, project_root, [item["path"] for item in prepared_files],
            )
            inputs_unchanged = current_files == prepared_files and prep["state"] != "stale"
            file_bound = bool(prepared_files)
            status = self.router.jobs.status(job_id, 0)
            passed = (status["state"] == "succeeded" and status.get("exit_code") == 0
                      and (not file_bound or inputs_unchanged))
            evidence = json.loads(prior["evidence_json"])
            if (bool(prior["passed"]) == passed
                    and evidence.get("job_state") == status["state"]
                    and evidence.get("exit_code") == status.get("exit_code")
                    and evidence.get("file_bound") == file_bound
                    and evidence.get("inputs_unchanged") == inputs_unchanged):
                return {"verification_id": prior["verification_id"], "task_id": task_id,
                        "kind": prior["kind"], "passed": passed, "evidence": evidence,
                        "verification_mode": self.router.tasks._owned_task(cid, task_id)["verification_mode"],
                        "files": prepared_files, "current_files": current_files}

        return self.router.tasks.verify_job(cid, task_id, self.router.jobs, job_id, root, paths)

    def _wait_check(self, job_id, guard) -> dict:
        deadline = time.monotonic() + max(0.0, min(float(self.inline_wait_s), 25.0))
        cursor = 0
        while True:
            try:
                guard()
            except Exception:
                try:
                    self.router.jobs.cancel(job_id)
                except Exception:
                    pass
                raise
            snapshot = self.router.jobs.status(job_id, cursor)
            if snapshot["state"] not in ("accepted", "running"):
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self.router.jobs.wait(job_id, snapshot["output_end"], min(1.0, remaining))
            cursor = snapshot["output_end"]
        preview = self.router.jobs.preview(job_id)
        if preview["state"] in ("accepted", "running"):
            text = preview["tail"]
            if preview["omitted_chars"]:
                text = "[output omitted]\n" + text
            return {"running": True, "output_tail": text[-6000:], "exit_code": None}
        if preview["state"] == "succeeded" and preview["exit_code"] == 0:
            pass
        tail = preview["tail"][-6000:]
        return {"running": False, "output_tail": tail, "exit_code": preview["exit_code"],
                "state": preview["state"]}

    def _task_verify_payload(self, cid: str, data: dict) -> dict:
        return {**data, "tasks": self.router.tasks.list(cid)}

    def _change_review(self, cid: str, params: dict, current_run: str | None) -> dict:
        run_id = params.get("run")
        path = params.get("path")
        root = self._selected_root(cid)
        if run_id is not None:
            if not isinstance(run_id, str) or not run_id:
                raise CoreToolError("bad_arguments", "run must be a run ID.")
            self.router.execution.run(cid, run_id)
        if path is not None:
            path = _relative_path(path)
        selected_run = run_id or (current_run if self.router.execution.active_run(cid) else self.router.shadow.latest(cid))
        if selected_run:
            shadow=self.router.shadow.review(cid,selected_run,path)
            if shadow and shadow.get('edits'):
                return shadow
        edits, history_truncated = self._edits_for_root(cid, run_id, root, path)
        output = []
        used = 0
        truncated = False
        for item in edits:
            record = {key: item[key] for key in (
                "edit_id", "run_id", "path", "state", "change_kind", "restored",
                "before_sha256", "after_sha256",
            )}
            if item["state"] == "finished":
                record["diff"] = self.router.checkpoints.diff(cid, item["edit_id"])["diff"][:MAX_REVIEW_DIFF_CHARS]
            encoded_size = len(json.dumps(record, ensure_ascii=True).encode("utf-8"))
            if used + encoded_size > MAX_REVIEW_BYTES or len(output) >= MAX_REVIEW_EDITS:
                truncated = True
                break
            output.append(record)
            used += encoded_size
        return {"run_id": run_id, "path": path, "edits": output,
                "snapshot_note": shadow.get('note') if selected_run and shadow else None,
                "count": len(output), "truncated": truncated or history_truncated,
                "hint": "Use run or path filters to narrow the checkpoint review."
                if truncated or history_truncated else None}

    def _change_restore(self, cid: str, params: dict, current_run: str | None, guard) -> dict:
        run_id = params.get("run")
        path = _relative_path(params["path"]) if "path" in params else None
        root = self._selected_root(cid)
        if run_id is not None:
            if not isinstance(run_id, str) or not run_id:
                raise CoreToolError("bad_arguments", "run must be a run ID.")
            self.router.execution.run(cid, run_id)
        if run_id is None and path is None:
            raise CoreToolError("restore_scope_required", "Specify a project-relative path or run to restore.")
        shadow_run=run_id or self.router.shadow.latest(cid,path)
        if shadow_run:
            shadow=self.router.shadow.restore(cid,shadow_run,path,guard)
            if shadow is not None:
                return shadow
        edits, history_truncated = self._edits_for_root(cid, run_id, root, path)
        edits = [item for item in edits if item["state"] == "finished"
                 and item["change_kind"] != "unchanged" and not item["restored"]]
        if path is not None and edits:
            edits = edits[:1]
        by_path: dict[str, list[dict]] = {}
        for item in edits:
            by_path.setdefault(item["path"], []).append(item)
        restored, failed = [], []
        for path_key, path_edits in by_path.items():
            path_failed = False
            for item in path_edits:
                if path_failed:
                    failed.append({"path": path_key, "edit_id": item["edit_id"],
                                   "error": "skipped_after_newer_restore_conflict"})
                    continue
                try:
                    with self.router.execution.lock:
                        guard()
                        result = self.router.checkpoints.restore(cid, item["edit_id"], expected_root=root)
                        if not result.get('already_restored'):
                            self.router.finish_line.changed(cid, current_run, path_key)
                    restored.append(result)
                except Exception as exc:
                    path_failed = True
                    failed.append({"path": path_key, "edit_id": item["edit_id"],
                                   "error": {"code": getattr(exc, "code", type(exc).__name__),
                                             "message": str(exc)[:250]}})
        return {"run_id": run_id, "path": path, "restored": restored,
                "failed": failed, "partial": bool(restored and failed),
                "all_succeeded": not failed and not history_truncated,
                "truncated": history_truncated,
                "hint": "Checkpoint history exceeded the bounded restore scan; narrow by path or run."
                if history_truncated else None}

    def _selected_root(self, cid: str) -> str:
        context = self.router.catalog.context(cid)
        if not context["project"]:
            raise CoreToolError("project_required", "Select a project before reviewing or restoring changes.")
        return context["project"]["root"]

    def _edits_for_root(self, cid: str, run_id: str | None, root: str,
                        path: str | None = None) -> tuple[list[dict], bool]:
        clauses = ["conversation_id=?", "root=?"]
        values: list[object] = [cid, root]
        if run_id is not None:
            clauses.append("run_id=?")
            values.append(run_id)
        if path is not None:
            clauses.append("relative_path=?")
            values.append(path)
        where = " AND ".join(clauses)
        with self.router.checkpoints._lock, self.router.checkpoints._connection() as db:
            rows = db.execute(f"SELECT * FROM edits WHERE {where} "
                               "ORDER BY captured_at DESC,edit_id DESC LIMIT ?",
                               (*values, MAX_CHECKPOINT_HISTORY + 1)).fetchall()
        truncated = len(rows) > MAX_CHECKPOINT_HISTORY
        rows = rows[:MAX_CHECKPOINT_HISTORY]
        return [self.router.checkpoints._metadata(row) for row in rows], truncated


def _relative_path(value: object) -> str:
    """Validate a project-relative checkpoint path for safe filtering."""
    from pathlib import PurePosixPath, PureWindowsPath

    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise CoreToolError("bad_arguments", "path must be a non-empty project-relative path.")
    normalized = value.replace("\\", "/")
    if PurePosixPath(normalized).is_absolute() or PureWindowsPath(value).is_absolute():
        raise CoreToolError("bad_arguments", "path must be project-relative.")
    if ".." in PurePosixPath(normalized).parts:
        raise CoreToolError("bad_arguments", "path cannot traverse outside the project.")
    return normalized
