"""Owner-configured, bounded checks after tracked file edits."""
from __future__ import annotations

import time

from .hooks import PathFilterTimeout, path_matches


HOOK_NAME = "Automatic edit check"
HOOK_EVENT = "after_file_edit"
HOOK_TIMEOUT_S = 10.0
WAIT_TIMEOUT_S = 10.0
_PREFERENCE_PREFIX = "edit_check_hook:"


class EditChecks:
    """Configure one reserved after-file-edit hook per project and await it safely."""

    def __init__(self, router):
        self.router = router

    def configuration(self, cid):
        """Return the local owner configuration for this conversation's project."""
        project = self.router.catalog.context(cid).get("project")
        if not project:
            return {"enabled": False, "configured": False, "hook_id": None,
                    "command": "", "path_filter": ""}
        hook_id = self.router.catalog.preference(self._preference_key(project["id"]))
        if not isinstance(hook_id, str):
            return {"enabled": False, "configured": False, "hook_id": None,
                    "command": "", "path_filter": ""}
        for hook in self.router.hooks.list_hooks("project:" + project["id"]):
            if hook["hook_id"] == hook_id and hook["name"] == HOOK_NAME and hook["event"] == HOOK_EVENT:
                return {"enabled": hook["enabled"], "configured": True, "hook_id": hook_id,
                        "command": hook["command"], "path_filter": hook.get("path_filter", "")}
        return {"enabled": False, "configured": False, "hook_id": None,
                "command": "", "path_filter": ""}

    def configure(self, cid, command, path_filter="", enabled=False):
        """Save the local hook disabled-first; enabling it is an explicit owner action."""
        if type(enabled) is not bool:
            raise ValueError("enabled must be bool")
        project = self.router.catalog.context(cid).get("project")
        if not project:
            raise ValueError("Choose a project before configuring an edit check")
        if not isinstance(command, str) or not command.strip():
            if enabled:
                raise ValueError("Provide a check command before enabling automatic checks")
            current = self.configuration(cid)
            if not current["configured"]:
                return current
            command = current["command"]
        scope = "project:" + project["id"]
        existing = self.configuration(cid)
        hook_id = existing.get("hook_id")
        # Store/update the reserved hook disabled, then commit its local owner ID,
        # and only then enable it. A preference write failure cannot leave a
        # runnable orphan hook behind.
        hook = self.router.hooks.set_hook(
            scope, HOOK_NAME, HOOK_EVENT, command, enabled=False,
            timeout_s=HOOK_TIMEOUT_S, path_filter=path_filter,
        )
        if hook_id and hook["hook_id"] != hook_id:
            raise ValueError("The stored edit-check hook changed; inspect local hook settings")
        self.router.catalog.preference(self._preference_key(project["id"]), hook["hook_id"])
        if enabled:
            hook = self.router.hooks.set_hook(
                scope, HOOK_NAME, HOOK_EVENT, command, enabled=True,
                timeout_s=HOOK_TIMEOUT_S, path_filter=path_filter,
            )
        return {"enabled": hook["enabled"], "configured": True, "hook_id": hook["hook_id"],
                "command": hook["command"], "path_filter": hook.get("path_filter", "")}

    def after_edit(self, cid, path, edit_id, invocation, guard):
        """Wait briefly for this edit's already-admitted check; never reject its edit."""
        try:
            return self._after_edit(cid, path, edit_id, invocation, guard)
        except PathFilterTimeout as exc:
            return {"state": "skipped", "passed": False,
                    "warning": "The file edit was saved; " + str(exc) + "."}
        except Exception as exc:
            return {"state": "unavailable", "passed": False,
                    "warning": "The file edit was saved; the check result could not be read (" +
                              type(exc).__name__ + "). Inspect local jobs."}

    def _after_edit(self, cid, path, edit_id, invocation, guard):
        configuration = self.configuration(cid)
        if not configuration["enabled"] or not path_matches(path, configuration["path_filter"]):
            return None
        hook_id = configuration["hook_id"]
        candidates = (invocation or {}).get("jobs", []) if isinstance(invocation, dict) else []
        admitted = next((item for item in candidates
                         if item.get("hook_id") == hook_id and item.get("job_id")), None)
        if admitted is None:
            skipped = (invocation or {}).get("skipped", []) if isinstance(invocation, dict) else []
            reason = next((item.get("reason") for item in skipped if item.get("hook_id") == hook_id), None)
            return {"state": "skipped", "passed": False,
                    "warning": "The file edit was saved; its check was not admitted." +
                               (" " + str(reason)[:200] if reason else "")}

        # Prove the caller's receipt still refers to the exact edit and reserved
        # hook before observing its job. This is read-only and cannot relaunch it.
        rows = self.router.catalog.rows(
            "SELECT invocation_id,job_id,source_id,hook_id,event,conversation_id,state "
            "FROM hook_invocations_v1 WHERE invocation_id=?", (admitted.get("invocation_id"),),
        )
        if not rows or rows[0]["conversation_id"] != cid or rows[0]["hook_id"] != hook_id \
                or rows[0]["event"] != HOOK_EVENT or rows[0]["source_id"] != edit_id \
                or rows[0]["job_id"] != admitted["job_id"]:
            return {"state": "unavailable", "passed": False,
                    "warning": "The file edit was saved; check ownership could not be confirmed."}

        job_id = admitted["job_id"]
        deadline = time.monotonic() + WAIT_TIMEOUT_S
        cursor = 0
        status = None
        try:
            while True:
                guard()
                remaining = deadline - time.monotonic()
                status = self.router.jobs.wait(job_id, cursor=cursor,
                                               timeout_s=min(0.25, max(0.0, remaining)))
                cursor = status.get("cursor", cursor)
                if status.get("state") not in ("accepted", "running"):
                    break
                if remaining <= 0:
                    return {"state": "running", "passed": False, "job_id": job_id,
                            "warning": "The file edit was saved; its check is still running. Inspect local jobs."}
        except Exception as exc:
            return {"state": "interrupted", "passed": False, "job_id": job_id,
                    "warning": "The file edit was saved; the check could not be confirmed (" +
                              type(exc).__name__ + "). Inspect local jobs before retrying."}

        passed = status.get("state") == "succeeded" and status.get("exit_code") == 0
        result = {"state": status.get("state", "unknown"), "passed": passed,
                  "exit_code": status.get("exit_code"), "job_id": job_id}
        if not passed:
            result["warning"] = "The file edit was saved; its check did not pass."
            result["output_tail"] = self._last_lines(job_id)
        return result

    def _last_lines(self, job_id):
        try:
            output = self.router.jobs.preview(job_id).get("tail", "")
        except Exception:
            return ""
        lines = output.splitlines()[-20:]
        text = "\n".join(lines)
        if len(text) > 8000:
            text = "…" + text[-7999:]
        return text

    @staticmethod
    def _preference_key(project_id):
        return _PREFERENCE_PREFIX + project_id
