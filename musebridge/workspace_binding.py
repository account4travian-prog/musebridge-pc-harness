"""Attach an existing local workspace to an existing conversation."""

from __future__ import annotations

import time
from pathlib import Path
import sqlite3
import json

from .workspace import Workspace


def bind_workspace(router, cid: str, project_id: str) -> dict:
    """Bind a conversation to a catalog project without changing its identity.

    Pending writes and jobs make rebinding ambiguous and are refused. Old
    message/tool runs are retired before the project scope changes.
    """
    catalog = router.catalog
    execution = router.execution
    if not isinstance(project_id, str) or not project_id:
        raise ValueError("Choose an existing local workspace")

    with execution.lock, catalog.lock, catalog.db:
        conversation = catalog.conversation(cid)
        project = catalog.project(project_id)
        project_root = Path(project["root"]).resolve(strict=True)
        if not project_root.is_dir() or str(project_root) != project["root"]:
            raise ValueError("Workspace folder is no longer available at its saved location")
        # Validate the same path boundary used by workspace tools before making
        # it the execution scope.
        Workspace(project_root)
        old_project_id = conversation.get("project_id")
        if old_project_id == project_id:
            return {"conversation_id": cid, "project_id": project_id, "changed": False,
                    "profile": execution.policy(cid)["profile"]}

        active_calls = catalog.db.execute(
            "SELECT tool,effect FROM tool_calls WHERE conversation_id=? AND state IN ('accepted','running')",
            (cid,),
        ).fetchall()
        mutating = [row["tool"] for row in active_calls if row["effect"] != "read"]
        if mutating:
            raise ValueError("Workspace cannot change while a local write or action is running")

        job_ids = []
        try:
            job_ids = [row[0] for row in catalog.db.execute(
                "SELECT job_id FROM job_links WHERE conversation_id=?", (cid,)
            )]
        except sqlite3.OperationalError:
            # Older or minimal catalogs may not yet have the job-link table.
            pass
        jobs = getattr(router, "jobs", None)
        if jobs and job_ids:
            states = jobs.states(job_ids)
            if any(state in ("accepted", "running") for state in states.values()):
                raise ValueError("Workspace cannot change while an owned command job is running")

        now = time.time()
        retiring = catalog.db.execute(
            "SELECT id FROM runs WHERE conversation_id=? AND state IN ('sending','acknowledged','local')",
            (cid,),
        ).fetchall()
        catalog.db.execute(
            "UPDATE runs SET state='interrupted',finished=? WHERE conversation_id=? AND state IN ('sending','acknowledged','local')",
            (now, cid),
        )
        for run in retiring:
            catalog.db.execute(
                "INSERT INTO events(conversation_id,kind,data,created) VALUES(?,?,?,?)",
                (cid,"run.interrupted",json.dumps({"run_id":run["id"],"reason":"workspace scope changed"}),now),
            )
        catalog.db.execute("UPDATE conversations SET project_id=? WHERE id=?", (project_id, cid))
        catalog.db.execute(
            "INSERT INTO events(conversation_id,kind,data,created) VALUES(?,?,?,?)",
            (cid,"workspace.bound",json.dumps({"project_id":project_id,"previous_project_id":old_project_id}),now),
        )
        return {"conversation_id": cid, "project_id": project_id, "changed": True,
                "profile": execution.policy(cid)["profile"], "retired_runs": len(retiring)}
