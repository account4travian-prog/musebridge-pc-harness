"""Bounded file operations scoped to a user-selected Windows workspace.

The selected root is a mistake-prevention boundary, not an OS sandbox. Calls
still run with the current user's Windows permissions.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

MAX_READ_BYTES = 64 * 1024
MAX_WRITE_BYTES = 4 * 1024 * 1024
MAX_SEARCH_FILE_BYTES = 2 * 1024 * 1024
MAX_SEARCH_FILES = 5000
MAX_LIST_LIMIT = 1000
MAX_SEARCH_LIMIT = 1000


def _hidden_subprocess_options() -> dict:
    """Avoid flashing a console window when called from the Windows UI."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


class Workspace:
    """A small, bounded file API rooted at a directory selected by the user."""

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root).expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise NotADirectoryError(str(self.root))
        self._write_lock = threading.RLock()
        self._private_roots = self._get_private_roots()

    def _get_private_roots(self) -> tuple[Path, ...]:
        roots = []
        for variable in ("LOCALAPPDATA", "APPDATA"):
            value = os.environ.get(variable)
            if value:
                roots.append((Path(value) / "MuseBridge").resolve(strict=False))
        from .storage import data_directories
        roots.extend(path.resolve(strict=False) for path in data_directories())
        return tuple(roots)

    @staticmethod
    def _inside(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False

    def resolve(self, path: str | os.PathLike[str]) -> Path:
        """Resolve a workspace-relative path, including existing links."""
        if not isinstance(path, (str, os.PathLike)):
            raise ValueError("path must be a string or path-like value")
        supplied = Path(path).expanduser()
        candidate = supplied if supplied.is_absolute() else self.root / supplied
        resolved = candidate.resolve(strict=False)
        if not self._inside(resolved, self.root):
            raise ValueError("path escapes the selected workspace")
        relative_parts = resolved.relative_to(self.root).parts
        lowered = tuple(part.casefold() for part in relative_parts)
        # .local is the app's ignored, private per-checkout state directory.
        if any(part in {".local", "local-data", "credentials", "credential"} for part in lowered):
            raise ValueError("private application data is outside the workspace file API")
        if any(self._inside(resolved, private) for private in self._private_roots):
            raise ValueError("MuseBridge credential storage is outside the workspace file API")
        return resolved

    def _validate_operation_path(self, path: str | os.PathLike[str], *, write: bool = False) -> Path:
        resolved = self.resolve(path)
        if write:
            relative_parts = resolved.relative_to(self.root).parts
            if any(part.casefold() == ".git" for part in relative_parts):
                raise ValueError("writes inside .git are not allowed")
        return resolved

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def list(self, path: str = ".", limit: int = 200) -> dict:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")
        limit = min(limit, MAX_LIST_LIMIT)
        directory = self._validate_operation_path(path)
        if not directory.is_dir():
            raise NotADirectoryError(str(directory))
        items = []
        truncated = False
        # Sorting stabilizes paging/output for the UI. Symlinks are resolved
        # and skipped if they escape or point into private application data.
        with os.scandir(directory) as entries:
            for entry in entries:
                if len(items) >= limit:
                    truncated = True
                    break
                try:
                    full = self._validate_operation_path(entry.path)
                    stat_result = full.stat()
                except (OSError, ValueError):
                    continue
                items.append({
                    "name": entry.name,
                    "path": full.relative_to(self.root).as_posix(),
                    "type": "directory" if full.is_dir() else "file",
                    "size": stat_result.st_size if full.is_file() else None,
                })
        items.sort(key=lambda item: (item["name"].casefold(), item["name"]))
        return {"path": directory.relative_to(self.root).as_posix() or ".", "entries": items,
                "limit": limit, "truncated": truncated}

    def read(self, path: str, offset: int = 0, length: int = MAX_READ_BYTES) -> dict:
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        if not isinstance(length, int) or isinstance(length, bool) or length < 1:
            raise ValueError("length must be a positive integer")
        length = min(length, MAX_READ_BYTES)
        target = self._validate_operation_path(path)
        with target.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            full = stream.read(MAX_WRITE_BYTES + 1) if size <= MAX_WRITE_BYTES else None
            if full is not None:
                data = full[offset:offset+length+1]
            else:
                stream.seek(offset)
                data = stream.read(length + 1)
        eof = len(data) <= length
        data = data[:length]
        return {
            "path": target.relative_to(self.root).as_posix(),
            "size": size,
            "offset": offset,
            "next_offset": offset + len(data),
            "length": len(data),
            "text": data.decode("utf-8", errors="replace"),
            "data_base64": base64.b64encode(data).decode("ascii"),
            "sha256": self._sha256(full) if full is not None else None,
            "chunk_sha256": self._sha256(data),
            "eof": eof,
        }

    def _check_expected(self, target: Path, expected_sha256: str | None, *, create: bool = False) -> None:
        exists = target.exists()
        if create and exists:
            raise FileExistsError(str(target))
        if not create and not exists:
            raise FileNotFoundError(str(target))
        if exists and target.stat().st_size > MAX_WRITE_BYTES:
            raise ValueError('file exceeds the 4 MiB edit limit')
        if exists and not create and expected_sha256 is None:
            raise ValueError('expected_sha256 is required to replace an existing file')
        if expected_sha256 is not None:
            if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
                raise ValueError("expected_sha256 must be a 64-character hexadecimal SHA-256")
            if not exists or self._sha256(target.read_bytes()) != expected_sha256.lower():
                raise ValueError("file changed since it was read (SHA-256 mismatch)")

    def _atomic_replace(self, target: Path, data: bytes, expected_sha256: str | None, *, create: bool = False) -> dict:
        if len(data) > MAX_WRITE_BYTES:
            raise ValueError(f"write exceeds {MAX_WRITE_BYTES} byte limit")
        with self._write_lock:
            # Re-resolve immediately before staging to catch a changed link in
            # the usual case. This remains a user-level guard, not a sandbox.
            checked = self._validate_operation_path(target, write=True)
            if checked != target:
                target = checked
            self._check_expected(target, expected_sha256, create=create)
            fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Optimistic expectation is checked again directly before the
                # atomic publication, catching stale edits during staging.
                self._check_expected(target, expected_sha256, create=create)
                if self._validate_operation_path(target, write=True) != target:
                    raise ValueError("target path changed while writing")
                if create:
                    # Publish the staged inode atomically while refusing an
                    # already occupied destination.
                    os.link(temporary, target)
                    temporary.unlink()
                else:
                    os.replace(temporary, target)
            except Exception:
                try:
                    temporary.unlink(missing_ok=True)
                finally:
                    raise
        return {"path": target.relative_to(self.root).as_posix(), "size": len(data), "sha256": self._sha256(data)}

    def write(self, path: str, text: str, expected_sha256: str | None = None, create: bool = False) -> dict:
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        if not isinstance(create, bool):
            raise ValueError("create must be a boolean")
        target = self._validate_operation_path(path, write=True)
        if not target.parent.is_dir():
            raise FileNotFoundError(str(target.parent))
        return self._atomic_replace(target, text.encode("utf-8"), expected_sha256, create=create)

    def text_document(self, path: str):
        """Load a guarded, bounded byte snapshot for the line-based tools."""
        from .workspace_text import decode_document, FileTextError
        target = self._validate_operation_path(path)
        with target.open('rb') as stream:
            data = stream.read(MAX_WRITE_BYTES + 1)
        if len(data) > MAX_WRITE_BYTES:
            raise FileTextError('too_large',
                'This file exceeds the 4 MiB bounded text limit. Narrow it with shell.run, or use artifact.add/read for supported documents.')
        return decode_document(data, path), self._sha256(data)

    def text_page(self, path: str, start_line=1, max_lines=400, budget=18000):
        """Read a numbered page; large files use bounded-memory streaming."""
        from .workspace_text import decode_document, FileTextError, _binary_kind
        from .file_stream import read_text_stream
        target = self._validate_operation_path(path)
        with target.open('rb') as stream:
            size = os.fstat(stream.fileno()).st_size
            if size <= MAX_WRITE_BYTES:
                data = stream.read(MAX_WRITE_BYTES + 1)
                if len(data) <= MAX_WRITE_BYTES:
                    return decode_document(data, path).read_lines(start_line, max_lines, budget), self._sha256(data)
                stream.seek(0)
            try:
                return read_text_stream(stream, path, start_line, max_lines, budget)
            except FileTextError as exc:
                if exc.code != 'binary':
                    raise
                stream.seek(0)
                kind = _binary_kind(stream.read(512), path)
                return {'binary': True, 'kind': 'binary' if kind == 'text' else kind,
                        'size': size, 'hint': exc.hint}, None

    def edit(self, path: str, old: str, new: str, expected_sha256: str) -> dict:
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            raise ValueError("old and new must be strings and old must not be empty")
        if not isinstance(expected_sha256, str):
            raise ValueError("expected_sha256 is required")
        target = self._validate_operation_path(path, write=True)
        with self._write_lock:
            if target.stat().st_size > MAX_WRITE_BYTES:
                raise ValueError('file exceeds the 4 MiB edit limit')
            original = target.read_bytes()
            self._check_expected(target, expected_sha256)
            try:
                source = original.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("edit only supports UTF-8 text files") from exc
            count = source.count(old)
            if count != 1:
                raise ValueError(f"edit requires exactly one match; found {count}")
            updated = source.replace(old, new, 1).encode("utf-8")
            return self._atomic_replace(target, updated, expected_sha256)

    def search(self, query: str, path: str = ".", limit: int = 100) -> dict:
        if not isinstance(query, str) or not query:
            raise ValueError("query must be a non-empty string")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")
        limit = min(limit, MAX_SEARCH_LIMIT)
        base = self._validate_operation_path(path)
        if not base.exists():
            raise FileNotFoundError(str(base))
        rg = shutil.which("rg")
        if rg:
            return self._search_with_rg(rg, query, base, limit)
        return self._search_fallback(query, base, limit)

    def _search_with_rg(self, rg: str, query: str, base: Path, limit: int) -> dict:
        args = [rg, "--json", "--fixed-strings", "--hidden", "--color", "never",
                "--glob", "!.git/**", "--glob", "!.local/**", "--glob", "!**/.git/**",
                "--glob", "!**/.local/**", "--max-filesize", "2M",
                "--max-depth", "16", "--max-count", str(limit), "--", query, str(base)]
        try:
            completed = subprocess.run(args, cwd=self.root, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", timeout=15,
                                       **_hidden_subprocess_options())
        except subprocess.TimeoutExpired as exc:
            raise OSError("ripgrep search exceeded the 15 second time limit") from exc
        matches = []
        truncated = False
        for line in completed.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "match":
                continue
            data = event["data"]
            file_path = Path(data["path"]["text"])
            try:
                resolved = self._validate_operation_path(file_path)
            except (OSError, ValueError):
                continue
            if len(matches) == limit:
                truncated = True
                break
            matches.append({"path": resolved.relative_to(self.root).as_posix(),
                            "line": data["line_number"],
                            "text": data["lines"]["text"].rstrip("\r\n")[:4096]})
        if completed.returncode not in (0, 1):
            raise OSError(completed.stderr.strip() or f"rg failed with exit code {completed.returncode}")
        return {"query": query, "mode": "literal", "matches": matches, "limit": limit,
                "truncated": truncated, "engine": "rg"}

    def _search_fallback(self, query: str, base: Path, limit: int) -> dict:
        matches = []
        files_seen = 0
        truncated = False
        pending = [base]
        visited = set()
        while pending:
            current = pending.pop()
            try:
                current = self._validate_operation_path(current)
                if current.is_dir():
                    identity = os.path.normcase(str(current))
                    if identity in visited:
                        continue
                    visited.add(identity)
                    with os.scandir(current) as entries:
                        for entry in entries:
                            if entry.name.casefold() in {".git", ".local"}:
                                continue
                            try:
                                child = self._validate_operation_path(entry.path)
                            except (OSError, ValueError):
                                continue
                            if child.is_dir():
                                pending.append(child)
                            elif child.is_file():
                                pending.append(child)
                    continue
                if not current.is_file():
                    continue
                files_seen += 1
                if files_seen > MAX_SEARCH_FILES:
                    truncated = True
                    break
                if current.stat().st_size > MAX_SEARCH_FILE_BYTES:
                    continue
                raw = current.read_bytes()
                if b"\0" in raw:
                    continue
                for number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
                    if query in line:
                        if len(matches) >= limit:
                            truncated = True
                            break
                        matches.append({"path": current.relative_to(self.root).as_posix(),
                                        "line": number, "text": line[:4096]})
                if truncated:
                    break
            except OSError:
                continue
        return {"query": query, "mode": "literal", "matches": matches, "limit": limit,
                "truncated": truncated, "engine": "python", "files_scanned": min(files_seen, MAX_SEARCH_FILES)}

    def git_status(self) -> dict:
        git = shutil.which("git")
        if not git:
            return {"available": False, "is_repository": False, "entries": [], "error": "git was not found"}
        try:
            completed = subprocess.run([git, "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                                       cwd=self.root, capture_output=True, timeout=15,
                                       **_hidden_subprocess_options())
        except subprocess.TimeoutExpired as exc:
            raise OSError("git status exceeded the 15 second time limit") from exc
        if completed.returncode:
            error = completed.stderr.decode("utf-8", errors="replace").strip()
            return {"available": True, "is_repository": False, "entries": [], "error": error or "not a git repository"}
        fields = completed.stdout.decode("utf-8", errors="replace").split("\0")
        entries = []
        i = 0
        while i < len(fields) and fields[i]:
            record = fields[i]
            if len(record) < 4:
                i += 1
                continue
            status = record[:2]
            path = record[3:]
            entry = {"status": status, "path": path}
            i += 1
            if "R" in status or "C" in status:
                if i < len(fields) and fields[i]:
                    entry["original_path"] = fields[i]
                    i += 1
            entries.append(entry)
        max_entries = 1000
        return {"available": True, "is_repository": True, "entries": entries[:max_entries],
                "count": len(entries), "truncated": len(entries) > max_entries}
