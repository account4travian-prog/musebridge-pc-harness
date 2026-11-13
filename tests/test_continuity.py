import json
import time
import uuid

import pytest
from musegadget.muse_turn import MuseTurn

from musebridge.catalog import Catalog
from musebridge.continuity import ContinuityStore


def test_project_notes_are_shared_with_provenance_and_corrections_are_auditable(tmp_path):
    catalog = Catalog(tmp_path)
    project = catalog.create_project("A", tmp_path)
    first = catalog.create_conversation("First", project["id"])
    second = catalog.create_conversation("Second", project["id"])
    other_project = catalog.create_project("B", tmp_path)
    other = catalog.create_conversation("Other", other_project["id"])
    store = ContinuityStore(catalog)

    original = store.note(first["id"], "decision", "Use SQLite", source="owner:2026-11-11")
    correction = store.note(second["id"], "decision", "Use SQLite WAL", supersedes=original["note_id"])

    notes = store.list(first["id"])
    assert {row["note_id"] for row in notes["notes"]} == {original["note_id"], correction["note_id"]}
    old = next(row for row in notes["notes"] if row["note_id"] == original["note_id"])
    assert old["effective"] is False
    assert old["superseded_by"] == correction["note_id"]
    assert correction["source"] == ""
    assert correction["created_utc"].endswith("+00:00")
    assert store.list(other["id"])["notes"] == []
    with pytest.raises(ValueError):
        store.note(other["id"], "decision", "Cross project correction", supersedes=correction["note_id"])

    handoff = store.handoff(second["id"])
    assert [item["note_id"] for item in handoff["summary"]["continuity_notes"]] == [correction["note_id"]]
    assert handoff["summary"]["continuity_notes"][0]["source"] == ""
    assert len(handoff["text"].encode("utf-8")) <= handoff["max_bytes"]
    catalog.close()


def test_unprojected_main_notes_stay_chat_scoped_and_not_in_new_project(tmp_path):
    catalog = Catalog(tmp_path)
    project = catalog.create_project("P", tmp_path)
    chat = catalog.create_conversation("P chat", project["id"])
    store = ContinuityStore(catalog)
    store.note("main", "progress", "Main-only progress")
    store.note(chat["id"], "progress", "Project progress")

    assert [n["text"] for n in store.list("main")["notes"]] == ["Main-only progress"]
    assert [n["text"] for n in store.list(chat["id"])["notes"]] == ["Project progress"]
    handoff = store.handoff("main")
    assert handoff["project_id"] is None
    assert [n["text"] for n in handoff["summary"]["continuity_notes"]] == ["Main-only progress"]
    catalog.close()


def test_handoff_seed_is_explicit_and_consumed_only_after_acknowledged_send(tmp_path):
    catalog = Catalog(tmp_path)
    project = catalog.create_project("P", tmp_path)
    source = catalog.create_conversation("Source", project["id"])
    catalog.plan(source["id"], text="Inspect then verify", goal="Repair sample", state="active")
    store = ContinuityStore(catalog)
    store.note(source["id"], "constraint", "Keep changes local", source="owner decision")

    created = store.create_handoff(source["id"])
    target_id = created["conversation"]["id"]
    assert str(uuid.UUID(target_id)) == target_id
    turn = MuseTurn(target_id, owns_chat=True)
    assert turn.acknowledge({"result": {"message_id": "first-user-message",
                                          "session_id": target_id, "is_thread": True}}) == []
    assert turn.acknowledged
    seed = store.retrieve_seed(target_id)
    assert seed["state"] == "pending"
    assert "not a native fork" in seed["label"]
    assert seed["summary"]["goal"] == "Repair sample"
    assert "Keep changes local" in seed["text"]
    assert store.mark_used(target_id, "not-an-outbox-id")["state"] == "pending"

    unknown = catalog.admit_send(target_id, seed["text"])
    catalog.settle_send(unknown, "unknown")
    assert store.mark_used(target_id, unknown)["state"] == "pending"
    assert store.retrieve_seed(target_id) is not None

    acknowledged = catalog.admit_send(target_id, seed["text"])
    catalog.settle_send(acknowledged, "acknowledged")
    assert store.mark_used(target_id, acknowledged)["state"] == "used"
    assert store.retrieve_seed(target_id) is None
    with pytest.raises(ValueError):
        store.mark_used(target_id, unknown)
    catalog.close()


def test_notes_and_pending_seed_survive_catalog_restart(tmp_path):
    catalog = Catalog(tmp_path)
    project = catalog.create_project("P", tmp_path)
    source = catalog.create_conversation("Source", project["id"])
    store = ContinuityStore(catalog)
    note = store.note(source["id"], "progress", "Persisted fact", source="check:job-17")
    target = store.create_handoff(source["id"])["conversation"]["id"]
    catalog.close()

    catalog = Catalog(tmp_path)
    store = ContinuityStore(catalog)
    assert store.list(source["id"])["notes"][0]["note_id"] == note["note_id"]
    seed = store.retrieve_seed(target)
    assert seed["state"] == "pending"
    assert "Persisted fact" in seed["text"]
    catalog.close()


def test_handoff_only_includes_tasks_and_verified_checks_from_source_chat(tmp_path):
    from musebridge.tasks import TaskStore

    catalog = Catalog(tmp_path)
    project = catalog.create_project("P", tmp_path)
    first = catalog.create_conversation("First", project["id"])
    second = catalog.create_conversation("Second", project["id"])
    tasks = TaskStore(catalog)
    catalog.tasks = tasks
    task = tasks.create(first["id"], "Check file", "File exists", verification_mode="manual")
    receipt_id = uuid.uuid4().hex
    now = time.time()
    with catalog.lock, catalog.db:
        catalog.db.execute("UPDATE structured_tasks_v1 SET current_verification_id=? WHERE task_id=?",
                           (receipt_id, task["task_id"]))
        catalog.db.execute("""INSERT INTO task_verifications_v1
            (verification_id,task_id,conversation_id,kind,passed,evidence_json,revision_json,stale,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (receipt_id, task["task_id"], first["id"], "manual_review", 1,
             json.dumps({"evidence": "Inspected"}), "{}", 0, now))
    tasks.create(second["id"], "Other task", "Not copied")

    store = ContinuityStore(catalog)
    handoff = store.handoff(first["id"])["summary"]
    assert [item["task_id"] for item in handoff["tasks"]["items"]] == [task["task_id"]]
    assert [item["verification_id"] for item in handoff["verified_checks"]["items"]] == [receipt_id]
    assert json.loads(catalog.rows("SELECT evidence_json FROM task_verifications_v1 WHERE verification_id=?",
                                   (receipt_id,))[0]["evidence_json"])
    catalog.close()


def test_handoff_is_bounded_and_discloses_truncation(tmp_path):
    catalog = Catalog(tmp_path)
    source = catalog.create_conversation("Source")
    store = ContinuityStore(catalog)
    for i in range(50):
        store.note(source["id"], "progress", f"Fact {i}: " + ("x" * 3500))

    handoff = store.handoff(source["id"])
    assert len(handoff["text"].encode("utf-8")) <= handoff["max_bytes"]
    assert handoff["truncated"] is True
    assert handoff["summary"]["truncated_sections"]
    catalog.close()


def test_note_validation_and_supersession_requires_current_same_kind(tmp_path):
    catalog = Catalog(tmp_path)
    source = catalog.create_conversation("Source")
    store = ContinuityStore(catalog)
    note = store.note(source["id"], "decision", "First")
    correction = store.note(source["id"], "decision", "Second", supersedes=note["note_id"])
    with pytest.raises(ValueError):
        store.note(source["id"], "progress", "Wrong kind", supersedes=correction["note_id"])
    with pytest.raises(ValueError):
        store.note(source["id"], "decision", "Third", supersedes=note["note_id"])
    with pytest.raises(ValueError):
        store.note(source["id"], "decision", " ")
    with pytest.raises(ValueError):
        store.list(source["id"], limit=True)
    catalog.close()
