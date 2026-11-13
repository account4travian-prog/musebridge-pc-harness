import hashlib
import os
from pathlib import Path

import pytest

from musebridge.checkpoints import CheckpointStore, MAX_DIFF_BYTES, MAX_FILE_BYTES


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def checkpoint_env(tmp_path):
    state = tmp_path / "checkpoint-state"
    root = tmp_path / "project"
    root.mkdir()
    return CheckpointStore(state), state, root


def test_modify_diff_restore_and_list_metadata(checkpoint_env):
    store, _state, root = checkpoint_env
    target = root / "notes.txt"
    before = "start 🌱\nkeep\n".encode("utf-8")
    after = "start 🌱\nchanged\n".encode("utf-8")
    target.write_bytes(before)
    edit_id = store.capture_before("chat-a", "run-1", root, "notes.txt")
    pending = store.list("chat-a", "run-1")
    assert pending["count"] == 1
    assert pending["edits"][0]["state"] == "pending"
    assert pending["edits"][0]["before_sha256"] == sha(before)

    target.write_bytes(after)
    metadata = store.finish(edit_id)
    assert metadata["change_kind"] == "modified"
    assert metadata["after_sha256"] == sha(after)
    diff = store.diff("chat-a", edit_id)
    assert "-keep" in diff["diff"] and "+changed" in diff["diff"]
    assert diff["binary"] is False

    result = store.restore("chat-a", edit_id)
    assert result["action"] == "restored_file"
    assert target.read_bytes() == before
    assert store.list("chat-a", "run-1")["edits"][0]["restored"] is True


def test_tracks_created_file_and_removes_only_unchanged_result(checkpoint_env):
    store, _state, root = checkpoint_env
    edit_id = store.capture_before("chat-a", "run-create", root, "new.txt")
    target = root / "new.txt"
    target.write_bytes(b"created")
    metadata = store.finish(edit_id)
    assert metadata["change_kind"] == "created"
    result = store.restore("chat-a", edit_id)
    assert result["action"] == "removed_created_file"
    assert not target.exists()


def test_tracks_deletion_and_restores_previous_bytes(checkpoint_env):
    store, _state, root = checkpoint_env
    target = root / "deleted.bin"
    before = bytes(range(256)) * 3
    target.write_bytes(before)
    edit_id = store.capture_before("chat-a", "run-delete", root, "deleted.bin")
    target.unlink()
    metadata = store.finish(edit_id)
    assert metadata["change_kind"] == "deleted"
    assert "Binary file change" in store.diff("chat-a", edit_id)["diff"]
    result = store.restore("chat-a", edit_id)
    assert result["action"] == "restored_file"
    assert target.read_bytes() == before


def test_restore_refuses_owner_edits_after_captured_after_state(checkpoint_env):
    store, _state, root = checkpoint_env
    target = root / "conflict.txt"
    target.write_bytes(b"before")
    edit_id = store.capture_before("chat-a", "run", root, "conflict.txt")
    target.write_bytes(b"tool after")
    store.finish(edit_id)
    target.write_bytes(b"later owner edit")
    with pytest.raises(ValueError, match="restore conflict"):
        store.restore("chat-a", edit_id)
    assert target.read_bytes() == b"later owner edit"


def test_cross_conversation_access_cannot_list_diff_or_restore_other_edits(checkpoint_env):
    store, _state, root = checkpoint_env
    target = root / "private.txt"
    target.write_bytes(b"before")
    edit_id = store.capture_before("chat-a", "run", root, "private.txt")
    target.write_bytes(b"after")
    store.finish(edit_id)
    assert store.list("chat-b")["count"] == 0
    with pytest.raises(KeyError, match="not found"):
        store.diff("chat-b", edit_id)
    with pytest.raises(KeyError, match="not found"):
        store.restore("chat-b", edit_id)
    assert target.read_bytes() == b"after"


def test_checkpoint_metadata_and_blobs_survive_store_reopen(checkpoint_env):
    store, state, root = checkpoint_env
    target = root / "restart.txt"
    target.write_bytes(b"before restart")
    edit_id = store.capture_before("chat-a", "run-reopen", root, "restart.txt")
    target.write_bytes(b"after restart")
    store.finish(edit_id)

    reopened = CheckpointStore(state)
    rows = reopened.list("chat-a", "run-reopen")["edits"]
    assert rows[0]["edit_id"] == edit_id
    assert rows[0]["after_sha256"] == sha(b"after restart")
    assert "after restart" in reopened.diff("chat-a", edit_id)["diff"]
    reopened.restore("chat-a", edit_id)
    assert target.read_bytes() == b"before restart"


def test_failed_edit_can_be_finished_as_unchanged_without_false_restore(checkpoint_env):
    store, _state, root = checkpoint_env
    target = root / "unchanged.txt"
    target.write_bytes(b"original")
    edit_id = store.capture_before("chat-a", "run-failed", root, "unchanged.txt")
    # Simulate a failed side effect which left the file untouched.
    metadata = store.finish(edit_id)
    assert metadata["change_kind"] == "unchanged"
    assert store.diff("chat-a", edit_id)["diff"] == ""
    result = store.restore("chat-a", edit_id)
    assert result["action"] == "already_original"
    assert target.read_bytes() == b"original"


def test_interrupted_pending_checkpoint_remains_honestly_pending_after_reopen(checkpoint_env):
    store, state, root = checkpoint_env
    target = root / "interrupted.txt"
    target.write_bytes(b"before")
    edit_id = store.capture_before("chat-a", "run-interrupted", root, "interrupted.txt")
    # Simulate process loss before finish() records an after snapshot.
    reopened = CheckpointStore(state)
    row = reopened.list("chat-a", "run-interrupted")["edits"][0]
    assert row["state"] == "pending"
    assert row["after_exists"] is None
    with pytest.raises(ValueError, match="still pending"):
        reopened.diff("chat-a", edit_id)
    with pytest.raises(ValueError, match="pending"):
        reopened.restore("chat-a", edit_id)


def test_restore_retry_recovers_after_filesystem_restore_before_metadata_commit(checkpoint_env, monkeypatch):
    store, state, root = checkpoint_env
    target = root / "restore-retry.txt"
    target.write_bytes(b"before")
    edit_id = store.capture_before("chat-a", "run-restore-retry", root, "restore-retry.txt")
    target.write_bytes(b"after")
    store.finish(edit_id)

    def crash_before_metadata(*_args, **_kwargs):
        raise OSError("simulated interruption before restore receipt")

    monkeypatch.setattr(store, "_mark_restored", crash_before_metadata)
    with pytest.raises(OSError, match="simulated interruption"):
        store.restore("chat-a", edit_id)
    assert target.read_bytes() == b"before"

    reopened = CheckpointStore(state)
    receipt = reopened.restore("chat-a", edit_id)
    assert receipt["already_restored"] is True
    assert receipt["action"] == "already_original"
    assert target.read_bytes() == b"before"


def test_path_validation_rejects_escape_absolute_git_and_store_paths(checkpoint_env):
    store, state, root = checkpoint_env
    for path in ("../outside.txt", root / "absolute.txt", ".git/config"):
        with pytest.raises((ValueError, FileNotFoundError, OSError)):
            store.capture_before("chat-a", "run", root, path)
    # The checkpoint DB/blobs cannot accidentally checkpoint their own store.
    inside_root = root / "state"
    store_inside = CheckpointStore(inside_root)
    with pytest.raises(ValueError, match="cannot be tracked"):
        store_inside.capture_before("chat-a", "run", root, "state/checkpoints.sqlite")


def test_restore_refuses_changed_symlink_target(checkpoint_env, tmp_path):
    store, _state, root = checkpoint_env
    target = root / "linked.txt"
    target.write_bytes(b"before")
    edit_id = store.capture_before("chat-a", "run", root, "linked.txt")
    target.write_bytes(b"after")
    store.finish(edit_id)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"owner file")
    target.unlink()
    try:
        target.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")
    with pytest.raises((ValueError, OSError)):
        store.restore("chat-a", edit_id)
    assert outside.read_bytes() == b"owner file"


def test_file_and_diff_size_bounds(checkpoint_env):
    store, _state, root = checkpoint_env
    target = root / "oversized.bin"
    target.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    with pytest.raises(ValueError, match="byte limit"):
        store.capture_before("chat-a", "run", root, "oversized.bin")

    text_file = root / "big-diff.txt"
    text_file.write_text("before\n", encoding="utf-8")
    edit_id = store.capture_before("chat-a", "run-diff", root, "big-diff.txt")
    text_file.write_text("x\n" * 50000, encoding="utf-8")
    store.finish(edit_id)
    result = store.diff("chat-a", edit_id)
    assert result["truncated"] is True
    assert len(result["diff"].encode("utf-8")) <= MAX_DIFF_BYTES + 32

