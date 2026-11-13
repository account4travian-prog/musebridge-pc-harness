"""Durable, direct-file checkpoints with conflict-aware restoration.

This records only one file's before/after state. It cannot reverse arbitrary
shell commands, GUI actions, network operations, or other external effects.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_DIFF_BYTES = 64 * 1024
MAX_LIST_ITEMS = 200


class CheckpointStore:
    """Store before/after file snapshots and restore only unchanged results."""

    def __init__(self, directory: str | os.PathLike[str]):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.blob_dir = self.directory / "blobs"
        self.blob_dir.mkdir(exist_ok=True)
        self.database = self.directory / "checkpoints.sqlite"
        self._lock = threading.RLock()
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""CREATE TABLE IF NOT EXISTS edits (
                edit_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                root TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                before_exists INTEGER NOT NULL,
                before_sha256 TEXT,
                before_size INTEGER NOT NULL,
                before_blob TEXT,
                after_exists INTEGER,
                after_sha256 TEXT,
                after_size INTEGER,
                after_blob TEXT,
                state TEXT NOT NULL CHECK(state IN ('pending','finished')),
                captured_at REAL NOT NULL,
                finished_at REAL,
                restored_at REAL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS edits_owner_run ON edits(conversation_id, run_id, captured_at DESC)")

    def _connect(self) -> sqlite3.Connection:
        self._reject_blob_link(self.database)
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @contextmanager
    def _connection(self):
        db = self._connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _valid_owner(value: object, field: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise ValueError(f"{field} must be a non-empty string up to 256 characters")
        if any(ord(ch) < 32 for ch in value):
            raise ValueError(f"{field} cannot contain control characters")
        return value

    @staticmethod
    def _sha(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _put_blob(self, data: bytes) -> str:
        self._reject_blob_link(self.blob_dir)
        digest = self._sha(data)
        target = self.blob_dir / f"{digest}.blob"
        if target.exists():
            self._reject_blob_link(target)
            if target.stat().st_size > MAX_FILE_BYTES or self._sha(target.read_bytes()) != digest:
                raise OSError("checkpoint content blob failed its integrity check")
            return digest
        fd, temp_name = tempfile.mkstemp(prefix="snapshot-", suffix=".tmp", dir=self.blob_dir)
        temporary = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                # Identical content may be committed by another store instance.
                if self._sha(target.read_bytes()) != digest:
                    raise OSError("checkpoint content blob failed its integrity check")
            temporary.unlink(missing_ok=True)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return digest

    def _get_blob(self, blob_id: str | None, expected_hash: str | None, expected_size: int) -> bytes:
        if blob_id is None or expected_hash is None:
            raise OSError("checkpoint blob is missing")
        if not re.fullmatch(r"[0-9a-f]{64}", blob_id) or blob_id != expected_hash:
            raise OSError("checkpoint blob identifier is invalid")
        if expected_size < 0 or expected_size > MAX_FILE_BYTES:
            raise OSError("checkpoint blob has an invalid size")
        self._reject_blob_link(self.blob_dir)
        path = self.blob_dir / f"{blob_id}.blob"
        self._reject_blob_link(path)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise OSError("checkpoint blob exceeds the file size limit")
        data = path.read_bytes()
        if len(data) != expected_size or self._sha(data) != expected_hash:
            raise OSError("checkpoint blob failed its integrity check")
        return data

    @staticmethod
    def _reject_blob_link(path: Path) -> None:
        if path.is_symlink():
            raise OSError("checkpoint blob cannot be a symbolic link")
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            raise OSError("checkpoint blob cannot be a junction")

    def _target(self, root: str | os.PathLike[str], relative_path: str | os.PathLike[str]) -> tuple[Path, str]:
        from .workspace import Workspace

        original_root=Path(root)
        if original_root.resolve(strict=True)!=original_root:
            raise ValueError('checkpoint workspace root identity changed')
        self._reject_reparse(original_root)
        workspace = Workspace(root)
        supplied = Path(relative_path)
        if supplied.is_absolute():
            raise ValueError("checkpoint path must be relative to its workspace root")
        self._reject_reparse(original_root/supplied)
        target = workspace._validate_operation_path(supplied, write=True)
        if target == workspace.root or not target.relative_to(workspace.root).parts:
            raise ValueError("checkpoint path must name a file below the workspace root")
        try:
            target.relative_to(self.directory)
        except ValueError:
            pass
        else:
            raise ValueError("checkpoint data directory cannot be tracked by itself")
        relative = target.relative_to(workspace.root).as_posix()
        return target, relative

    @staticmethod
    def _reject_reparse(target: Path) -> None:
        """Do not restore through a link/junction that changed after capture."""
        parts = [target, *target.parents]
        for path in parts:
            if path.is_symlink():
                raise ValueError("checkpoint target became a symbolic link; restore refused")
            is_junction = getattr(path, "is_junction", None)
            if callable(is_junction) and is_junction():
                raise ValueError("checkpoint target became a junction; restore refused")

    @staticmethod
    def _read_target(target: Path) -> bytes | None:
        try:
            info = target.stat()
        except FileNotFoundError:
            return None
        if not target.is_file():
            raise ValueError("checkpoint target is not a regular file")
        if info.st_size > MAX_FILE_BYTES:
            raise ValueError(f"checkpoint file exceeds {MAX_FILE_BYTES} byte limit")
        with target.open('rb') as stream:data=stream.read(MAX_FILE_BYTES+1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"checkpoint file exceeds {MAX_FILE_BYTES} byte limit")
        return data

    def capture_before(self, conversation_id: str, run_id: str, root: str | os.PathLike[str],
                       relative_path: str | os.PathLike[str]) -> str:
        conversation_id = self._valid_owner(conversation_id, "conversation_id")
        run_id = self._valid_owner(run_id, "run_id")
        root_path = Path(root).expanduser().resolve(strict=True)
        target, stored_relative = self._target(root_path, relative_path)
        if not target.parent.is_dir():
            raise FileNotFoundError(str(target.parent))
        self._reject_reparse(target)
        before = self._read_target(target)
        digest = self._sha(before) if before is not None else None
        blob_id = self._put_blob(before) if before is not None else None
        edit_id = uuid.uuid4().hex
        with self._lock, self._connection() as db:
            db.execute("""INSERT INTO edits(edit_id,conversation_id,run_id,root,relative_path,
                before_exists,before_sha256,before_size,before_blob,state,captured_at)
                VALUES(?,?,?,?,?,?,?,?,?,'pending',?)""",
                (edit_id, conversation_id, run_id, str(root_path), stored_relative,
                 int(before is not None), digest, len(before or b""), blob_id, time.time()))
        return edit_id

    def finish(self, edit_id: str) -> dict:
        if not isinstance(edit_id, str) or not edit_id:
            raise ValueError("edit_id must be a non-empty string")
        with self._lock:
            with self._connection() as db:
                row = db.execute("SELECT * FROM edits WHERE edit_id=?", (edit_id,)).fetchone()
            if row is None:
                raise KeyError("unknown checkpoint edit_id")
            if row["state"] != "pending":
                raise ValueError("checkpoint has already been finished")
            target, stored_relative = self._target(row["root"], row["relative_path"])
            if stored_relative != row["relative_path"]:
                raise ValueError("checkpoint path identity changed")
            self._reject_reparse(target)
            after = self._read_target(target)
            after_hash = self._sha(after) if after is not None else None
            after_blob = self._put_blob(after) if after is not None else None
            finished_at = time.time()
            with self._connection() as db:
                updated = db.execute("""UPDATE edits SET after_exists=?,after_sha256=?,after_size=?,
                    after_blob=?,state='finished',finished_at=? WHERE edit_id=? AND state='pending'""",
                    (int(after is not None), after_hash, len(after or b""), after_blob, finished_at, edit_id))
                if updated.rowcount != 1:
                    raise ValueError("checkpoint state changed before finish")
        return self._metadata(row, after_exists=after is not None, after_sha256=after_hash,
                              after_size=len(after or b""), state="finished", finished_at=finished_at)

    @staticmethod
    def _metadata(row, *, after_exists=None, after_sha256=None, after_size=None,
                  state=None, finished_at=None) -> dict:
        before_exists = bool(row["before_exists"])
        if after_exists is None:
            after_exists = bool(row["after_exists"]) if row["after_exists"] is not None else None
        if after_sha256 is None and row["after_sha256"] is not None:
            after_sha256 = row["after_sha256"]
        if after_size is None and row["after_size"] is not None:
            after_size = row["after_size"]
        if state is None:
            state = row["state"]
        if state != "finished":
            kind = "pending"
        elif not before_exists and after_exists:
            kind = "created"
        elif before_exists and not after_exists:
            kind = "deleted"
        elif row["before_sha256"] == after_sha256:
            kind = "unchanged"
        else:
            kind = "modified"
        return {"edit_id": row["edit_id"], "conversation_id": row["conversation_id"],
                "run_id": row["run_id"], "path": row["relative_path"], "state": state,
                "change_kind": kind, "before_exists": before_exists,
                "before_sha256": row["before_sha256"], "before_size": int(row["before_size"]),
                "after_exists": after_exists, "after_sha256": after_sha256,
                "after_size": after_size, "captured_at": row["captured_at"],
                "finished_at": finished_at if finished_at is not None else row["finished_at"],
                "restored": row["restored_at"] is not None}

    def list(self, conversation_id: str, run_id: str | None = None) -> dict:
        conversation_id = self._valid_owner(conversation_id, "conversation_id")
        if run_id is not None:
            run_id = self._valid_owner(run_id, "run_id")
        with self._lock, self._connection() as db:
            if run_id is None:
                rows = db.execute("SELECT * FROM edits WHERE conversation_id=? ORDER BY captured_at DESC, edit_id DESC LIMIT ?",
                                  (conversation_id, MAX_LIST_ITEMS + 1)).fetchall()
            else:
                rows = db.execute("SELECT * FROM edits WHERE conversation_id=? AND run_id=? ORDER BY captured_at DESC, edit_id DESC LIMIT ?",
                                  (conversation_id, run_id, MAX_LIST_ITEMS + 1)).fetchall()
        truncated = len(rows) > MAX_LIST_ITEMS
        rows = rows[:MAX_LIST_ITEMS]
        return {"conversation_id": conversation_id, "run_id": run_id,
                "edits": [self._metadata(row) for row in rows],
                "count": len(rows), "limit": MAX_LIST_ITEMS, "truncated": truncated}

    def _owned_row(self, conversation_id: str, edit_id: str):
        conversation_id = self._valid_owner(conversation_id, "conversation_id")
        if not isinstance(edit_id, str) or not edit_id:
            raise ValueError("edit_id must be a non-empty string")
        with self._lock, self._connection() as db:
            row = db.execute("SELECT * FROM edits WHERE edit_id=? AND conversation_id=?",
                             (edit_id, conversation_id)).fetchone()
        if row is None:
            raise KeyError("checkpoint edit not found for this conversation")
        return row

    def diff(self, conversation_id: str, edit_id: str) -> dict:
        row = self._owned_row(conversation_id, edit_id)
        if row["state"] != "finished":
            raise ValueError("checkpoint is still pending; no after snapshot is available")
        before = self._get_blob(row["before_blob"], row["before_sha256"], row["before_size"]) if row["before_exists"] else b""
        after = self._get_blob(row["after_blob"], row["after_sha256"], row["after_size"]) if row["after_exists"] else b""
        if (row["before_exists"] and b"\0" in before) or (row["after_exists"] and b"\0" in after):
            text = "Binary file change; content is retained for restore but omitted from the text diff."
            return {"edit_id": edit_id, "path": row["relative_path"], "binary": True,
                    "diff": text, "truncated": False}
        try:
            old_lines = before.decode("utf-8").splitlines(keepends=True)
            new_lines = after.decode("utf-8").splitlines(keepends=True)
        except UnicodeDecodeError:
            text = "Non-UTF-8 file change; content is retained for restore but omitted from the text diff."
            return {"edit_id": edit_id, "path": row["relative_path"], "binary": True,
                    "diff": text, "truncated": False}
        generated = "".join(difflib.unified_diff(old_lines, new_lines,
            fromfile=f"a/{row['relative_path']}", tofile=f"b/{row['relative_path']}", lineterm="\n"))
        encoded = generated.encode("utf-8")
        truncated = len(encoded) > MAX_DIFF_BYTES
        if truncated:
            generated = encoded[:MAX_DIFF_BYTES].decode("utf-8", errors="ignore") + "\n[diff truncated]\n"
        return {"edit_id": edit_id, "path": row["relative_path"], "binary": False,
                "diff": generated, "truncated": truncated}

    @staticmethod
    def _state_matches(data: bytes | None, exists: bool, expected_hash: str | None) -> bool:
        if exists != (data is not None):
            return False
        return data is None or CheckpointStore._sha(data) == expected_hash

    @staticmethod
    def _atomic_restore(target: Path, data: bytes, *, exclusive: bool = False) -> None:
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.restore-", suffix=".tmp", dir=target.parent)
        temporary = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if exclusive:
                os.link(temporary, target)
                temporary.unlink()
            else:
                os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def restore(self, conversation_id: str, edit_id: str, *, expected_root=None) -> dict:
        row = self._owned_row(conversation_id, edit_id)
        if expected_root is not None and Path(row['root'])!=Path(expected_root).resolve(strict=True):
            raise ValueError('checkpoint belongs to a different workspace root')
        if row["state"] != "finished":
            raise ValueError("checkpoint is pending; restore refused")
        target, stored_relative = self._target(row["root"], row["relative_path"])
        if stored_relative != row["relative_path"]:
            raise ValueError("checkpoint path identity changed")
        self._reject_reparse(target)
        with self._lock:
            current = self._read_target(target)
            if self._state_matches(current, bool(row["before_exists"]), row["before_sha256"]):
                # Handles a retry after a crash between filesystem publication
                # and the database's restored_at update.
                return self._mark_restored(row, already_restored=True)
            if not self._state_matches(current, bool(row["after_exists"]), row["after_sha256"]):
                raise ValueError("current file no longer matches the captured after state; restore conflict")
            before = self._get_blob(row["before_blob"], row["before_sha256"], row["before_size"]) if row["before_exists"] else None
            # Recheck immediately before publication/removal. This narrows but
            # cannot eliminate races with unrelated processes on the file.
            self._reject_reparse(target)
            current = self._read_target(target)
            if not self._state_matches(current, bool(row["after_exists"]), row["after_sha256"]):
                raise ValueError("current file changed during restore preparation; restore conflict")
            if before is None:
                if target.exists():
                    target.unlink()
                action = "removed_created_file"
            else:
                if not target.parent.is_dir():
                    raise FileNotFoundError(str(target.parent))
                self._atomic_restore(target, before, exclusive=current is None)
                action = "restored_file"
        return self._mark_restored(row, action=action, already_restored=False)

    def _mark_restored(self, row, *, action: str = "already_original", already_restored: bool) -> dict:
        restored_at = row["restored_at"] or time.time()
        with self._lock, self._connection() as db:
            db.execute("UPDATE edits SET restored_at=? WHERE edit_id=? AND conversation_id=?",
                       (restored_at, row["edit_id"], row["conversation_id"]))
        return {"edit_id": row["edit_id"], "path": row["relative_path"],
                "restored": True, "already_restored": already_restored,
                "action": action, "before_sha256": row["before_sha256"]}
