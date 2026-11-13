import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from musebridge.catalog import Catalog
from musebridge.tasks import TaskStore


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def task_env(tmp_path):
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    catalog = Catalog(appdata)
    project = catalog.create_project("Disposable", root)
    first = catalog.create_conversation("First", project["id"])
    second = catalog.create_conversation("Second", project["id"])
    tasks = TaskStore(catalog)
    return catalog, tasks, root, first["id"], second["id"], appdata


class FakeJobs:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def status(self, job_id, cursor=0):
        self.calls.append((job_id, cursor))
        return {"job_id": job_id, **self.result}


def link_job(catalog, cid, job_id):
    with catalog.lock, catalog.db:
        catalog.db.execute("CREATE TABLE IF NOT EXISTS job_links(job_id TEXT PRIMARY KEY,conversation_id TEXT)")
        catalog.db.execute("INSERT OR REPLACE INTO job_links VALUES(?,?)", (job_id, cid))


def verify_success(tasks, catalog, cid, task, root, paths=(), job_id="job-1"):
    prepared = tasks.prepare_job(cid, task["task_id"], root, paths) if paths else None
    link_job(catalog, cid, job_id)
    if prepared is not None:
        tasks.link_job_verification(cid, task["task_id"], job_id, prepared)
    jobs = FakeJobs({"state": "succeeded", "exit_code": 0,
                     "output": "test output", "output_end": 12})
    result = tasks.verify_job(cid, task["task_id"], jobs, job_id, root, paths)
    return result, jobs


def test_create_task_and_dependency_gate_completion(task_env):
    catalog, store, root, cid, _other, _appdata = task_env
    parent = store.create(cid, "Write file", "Create result", [])
    child = store.create(cid, "Verify file", "Check result", [parent["task_id"]])
    assert child["state"] == "blocked"
    assert child["dependency_states"] == {parent["task_id"]: "pending"}
    assert store.update(cid, child["task_id"], "active")["state"] == "blocked"
    assert store.update(cid, child["task_id"], "complete")["state"] == "blocked"

    result_file = root / "result.txt"
    result_file.write_text("verified output", encoding="utf-8")
    _, _jobs = verify_success(store, catalog, cid, parent, root, ["result.txt"])
    assert store.update(cid, parent["task_id"], "complete")["state"] == "complete"
    assert store.update(cid, child["task_id"], "active")["state"] == "active"


def test_file_hash_observation_is_not_semantic_verification(task_env):
    _catalog, store, root, cid, _other, _appdata = task_env
    task = store.create(cid, "Write artifact", "The artifact must be correct")
    target = root / "artifact.txt"
    target.write_bytes(b"actual file")
    observation = store.check_files(cid, task["task_id"], root,
                                    [{"path": "artifact.txt", "sha256": digest(b"actual file")}])
    assert observation["matched"] is True
    assert observation["semantic_acceptance"] is False
    result = store.update(cid, task["task_id"], "complete")
    assert result["state"] == "awaiting_review"
    assert result["blocked_reason"] == "current locally issued verification is required"


def test_job_exit_receipt_can_complete_job_exit_mode_and_binds_current_files(task_env):
    catalog, store, root, cid, _other, _appdata = task_env
    task = store.create(cid, "Run tests", "Run test suite", verification_mode="job_exit")
    output = root / "artifact.txt"
    output.write_bytes(b"test-produced output")
    verified, jobs = verify_success(store, catalog, cid, task, root, ["artifact.txt"])
    assert jobs.calls == [("job-1", 0)]
    assert verified["passed"] is True
    assert verified["evidence"]["job_id"] == "job-1"
    assert "output_sha256_page" in verified["evidence"]
    assert "test output" not in str(verified["evidence"])
    completed = store.update(cid, task["task_id"], "complete")
    assert completed["state"] == "complete"
    assert completed["verification_mode"] == "job_exit"

    output.write_bytes(b"owner changed the output")
    listed = store.list(cid)
    current = next(row for row in listed if row["task_id"] == task["task_id"])
    assert current["state"] == "awaiting_review"
    assert current["verification"]["stale"] is True
    assert current["completed_at"] is None
    # task.list computes stale state without writing, preserving the UI's
    # read-only contract. A subsequent state update persists the invalidation.
    persisted = catalog.rows("SELECT state FROM structured_tasks_v1 WHERE task_id=?", (task["task_id"],))[0]
    assert persisted["state"] == "complete"
    assert store.update(cid, task["task_id"], "complete")["state"] == "awaiting_review"
    persisted = catalog.rows("SELECT state FROM structured_tasks_v1 WHERE task_id=?", (task["task_id"],))[0]
    assert persisted["state"] == "awaiting_review"


def test_job_exit_is_execution_evidence_not_claimed_semantic_proof(task_env):
    catalog, store, root, cid, _other, _appdata = task_env
    task = store.create(cid, "Check behavior", "Confirm the user outcome", verification_mode="job_exit")
    verified, _jobs = verify_success(store, catalog, cid, task, root, job_id="job-for-inspection")
    scope = verified["evidence"]["verification_scope"]
    assert "not an independent semantic judgment" in scope
    assert verified["evidence"]["job_id"] == "job-for-inspection"
    assert store.update(cid, task["task_id"], "complete")["state"] == "complete"


def test_prepared_input_change_before_verification_invalidates_successful_job(task_env):
    catalog, store, root, cid, _other, _appdata = task_env
    task = store.create(cid, "Run against source", "Tests pass for this source")
    source = root / "source.txt"
    source.write_bytes(b"A")
    prepared = store.prepare_job(cid, task["task_id"], root, ["source.txt"])
    link_job(catalog, cid, "job-race")
    store.link_job_verification(cid, task["task_id"], "job-race", prepared)

    # Simulate owner edits between successful execution and verification.
    source.write_bytes(b"B")
    jobs = FakeJobs({"state": "succeeded", "exit_code": 0, "output": "passed", "output_end": 6})
    result = store.verify_job(cid, task["task_id"], jobs, "job-race", root, ["source.txt"])
    assert result["evidence"]["job_state"] == "succeeded"
    assert result["evidence"]["inputs_unchanged"] is False
    assert result["passed"] is False
    assert result["evidence"]["verification_scope"].startswith("job exit success and pre-job")
    assert store.update(cid, task["task_id"], "complete")["state"] == "awaiting_review"
    assert source.read_bytes() == b"B"


def test_file_bound_verify_requires_prepared_job_link(task_env):
    catalog, store, root, cid, _other, _appdata = task_env
    task = store.create(cid, "Bound check")
    (root / "source.txt").write_bytes(b"A")
    link_job(catalog, cid, "job-unprepared")
    with pytest.raises(ValueError, match="prepared snapshot"):
        store.verify_job(cid, task["task_id"], FakeJobs({"state": "succeeded", "exit_code": 0}),
                         "job-unprepared", root, ["source.txt"])


def test_idempotent_job_retry_reuses_original_snapshot_after_inputs_change(task_env):
    catalog, store, root, cid, _other, _appdata = task_env
    task = store.create(cid, "Retry-safe test", "Check exact source revision")
    source = root / "source.txt"
    source.write_bytes(b"A")
    original = store.prepare_job(cid, task["task_id"], root, ["source.txt"])
    link_job(catalog, cid, "deduped-job")
    first_link = store.link_job_verification(cid, task["task_id"], "deduped-job", original)
    assert first_link["preparation_id"] == original["preparation_id"]

    # The request ID is retried after an owner edit; JobManager returns the old
    # job ID, but the new preparation must never replace the pre-run snapshot.
    source.write_bytes(b"B")
    retried = store.prepare_job(cid, task["task_id"], root, ["source.txt"])
    relink = store.link_job_verification(cid, task["task_id"], "deduped-job", retried)
    assert relink["reused_existing_snapshot"] is True
    assert relink["preparation_id"] == original["preparation_id"]
    result = store.verify_job(cid, task["task_id"],
                              FakeJobs({"state": "succeeded", "exit_code": 0, "output": "ok"}),
                              "deduped-job", root, ["source.txt"])
    assert result["passed"] is False
    assert result["files"][0]["sha256"] == digest(b"A")
    assert result["current_files"][0]["sha256"] == digest(b"B")
    assert store.update(cid, task["task_id"], "complete")["state"] == "awaiting_review"


def test_failed_job_result_does_not_allow_completion(task_env):
    catalog, store, root, cid, _other, _appdata = task_env
    task = store.create(cid, "Run failing checks", "Check failing behavior")
    link_job(catalog, cid, "job-fail")
    jobs = FakeJobs({"state": "failed", "exit_code": 1, "output": "secret failure output", "output_end": 21})
    result = store.verify_job(cid, task["task_id"], jobs, "job-fail", root)
    assert result["passed"] is False
    assert "secret failure output" not in str(store.list(cid))
    assert store.update(cid, task["task_id"], "complete")["state"] == "awaiting_review"


def test_manual_mode_requires_local_review_and_pins_job_outputs_when_available(task_env):
    catalog, store, root, cid, _other, _appdata = task_env
    task = store.create(cid, "Manual acceptance", "Assess usability", verification_mode="manual")
    artifact = root / "ui.txt"
    artifact.write_text("reviewed version", encoding="utf-8")
    verify_success(store, catalog, cid, task, root, ["ui.txt"], "job-manual")
    assert store.update(cid, task["task_id"], "complete")["state"] == "awaiting_review"
    completed = store.accept_review(cid, task["task_id"], "Owner reviewed the result")
    assert completed["state"] == "complete"
    assert completed["verification_mode"] == "manual"
    artifact.write_text("changed after review", encoding="utf-8")
    stale = next(row for row in store.list(cid) if row["task_id"] == task["task_id"])
    assert stale["state"] == "awaiting_review"
    assert stale["verification"]["stale"] is True


def test_cross_conversation_tasks_jobs_and_dependencies_are_refused(task_env):
    catalog, store, root, cid, other, _appdata = task_env
    task = store.create(cid, "Private task")
    other_task = store.create(other, "Other chat task")
    with pytest.raises(ValueError, match="same conversation"):
        store.create(cid, "Wrong dependency", dependencies=[other_task["task_id"]])
    with pytest.raises(KeyError, match="this conversation"):
        store.update(other, task["task_id"], "active")
    with pytest.raises(ValueError, match="Unknown conversation"):
        store.list("unknown-chat")

    link_job(catalog, other, "other-job")
    with pytest.raises(ValueError, match="does not belong"):
        store.verify_job(cid, task["task_id"], FakeJobs({"state": "succeeded", "exit_code": 0}),
                         "other-job", root)


def test_dependency_cycle_and_self_dependency_are_refused(task_env, monkeypatch):
    catalog, store, _root, cid, _other, _appdata = task_env
    first = store.create(cid, "First")
    with catalog.lock, catalog.db:
        catalog.db.execute("UPDATE structured_tasks_v1 SET dependencies_json=? WHERE task_id=?",
                           (json.dumps(["future-task"]), first["task_id"]))
    monkeypatch.setattr("musebridge.tasks.uuid.uuid4", lambda: SimpleNamespace(hex="future-task"))
    with pytest.raises(ValueError, match="cycle"):
        store.create(cid, "Future task", dependencies=[first["task_id"]])

    # Force the generated candidate ID to equal an existing dependency ID.
    existing = first["task_id"]
    monkeypatch.setattr("musebridge.tasks.uuid.uuid4", lambda: SimpleNamespace(hex=existing))
    with pytest.raises(ValueError, match="itself"):
        store.create(cid, "Self", dependencies=[existing])


def test_reopen_keeps_tasks_and_catalog_schema_version_unchanged(task_env):
    catalog, store, _root, cid, _other, appdata = task_env
    version_before = catalog.db.execute("PRAGMA user_version").fetchone()[0]
    task = store.create(cid, "Persistent", "Keep task")
    catalog.close()
    reopened = Catalog(appdata)
    version_after_catalog = reopened.db.execute("PRAGMA user_version").fetchone()[0]
    reopened_tasks = TaskStore(reopened)
    version_after_tasks = reopened.db.execute("PRAGMA user_version").fetchone()[0]
    row = next(row for row in reopened_tasks.list(cid) if row["task_id"] == task["task_id"])
    assert row["title"] == "Persistent"
    assert version_before == version_after_catalog == version_after_tasks
    reopened.close()


def test_verification_rejects_wrong_project_root_and_bad_file_hash(task_env, tmp_path):
    _catalog, store, root, cid, _other, _appdata = task_env
    task = store.create(cid, "Root check")
    wrong = tmp_path / "other-project"
    wrong.mkdir()
    with pytest.raises(ValueError, match="does not match"):
        store.check_files(cid, task["task_id"], wrong, [{"path": "x", "exists": False}])
    with pytest.raises(ValueError, match="SHA-256"):
        store.check_files(cid, task["task_id"], root, [{"path": "x", "sha256": "bad"}])
    alias = tmp_path / "project-alias"
    try:
        alias.symlink_to(root, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")
    with pytest.raises(ValueError, match="root identity changed"):
        store.check_files(cid, task["task_id"], alias, [{"path": "x", "exists": False}])


def test_task_and_verification_limits_are_enforced(task_env):
    _catalog, store, root, cid, _other, _appdata = task_env
    task = store.create(cid, "Bounds")
    with pytest.raises(ValueError, match="16"):
        store.check_files(cid, task["task_id"], root, [{"path": f"{i}.txt", "exists": False} for i in range(65)])
    with pytest.raises(ValueError, match="unknown task dependency"):
        store.create(cid, "Unknown dependency", dependencies=["does-not-exist"])

