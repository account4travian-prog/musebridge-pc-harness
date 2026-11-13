import base64
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from musebridge.workspace import MAX_READ_BYTES, MAX_WRITE_BYTES, Workspace


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_resolve_accepts_relative_and_absolute_paths(tmp_path):
    (tmp_path / "src").mkdir()
    ws = Workspace(tmp_path)
    assert ws.resolve("src") == (tmp_path / "src").resolve()
    assert ws.resolve(tmp_path / "src") == (tmp_path / "src").resolve()


def test_resolve_rejects_parent_and_absolute_escape(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    ws = Workspace(tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        ws.resolve("../outside.txt")
    with pytest.raises(ValueError, match="escapes"):
        ws.resolve(outside)


def test_resolve_rejects_symlink_escape_when_supported(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")
    ws = Workspace(root)
    with pytest.raises(ValueError, match="escapes"):
        ws.resolve("linked/secret.txt")
    with pytest.raises(ValueError, match="escapes"):
        ws.list("linked")


def test_resolve_rejects_private_checkout_and_appdata_paths(tmp_path, monkeypatch):
    (tmp_path / ".local").mkdir()
    appdata = tmp_path / "appdata"
    (appdata / "MuseBridge").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    ws = Workspace(tmp_path)
    for path in (".local", ".local/session.json", appdata / "MuseBridge" / "identity.dpapi"):
        with pytest.raises(ValueError, match="private application data|credential storage"):
            ws.resolve(path)


def test_read_returns_byte_bounded_utf8_base64_hash_and_eof(tmp_path):
    target = tmp_path / "unicode.txt"
    raw = "雪🙂é".encode("utf-8") + b"tail"
    target.write_bytes(raw)
    result = Workspace(tmp_path).read("unicode.txt", offset=1, length=5)
    part = raw[1:6]
    assert result["text"] == part.decode("utf-8", errors="replace")
    assert base64.b64decode(result["data_base64"]) == part
    assert result["sha256"] == digest(raw)
    assert result["chunk_sha256"] == digest(part)
    assert result["length"] == 5
    assert result["next_offset"] == 6
    assert result["eof"] is False
    tail = Workspace(tmp_path).read("unicode.txt", offset=6, length=MAX_READ_BYTES)
    assert tail["eof"] is True
    assert len(base64.b64decode(tail["data_base64"])) == len(raw) - 6


def test_read_caps_length_even_when_caller_asks_for_more(tmp_path):
    (tmp_path / "large").write_bytes(b"x" * (MAX_READ_BYTES + 10))
    result = Workspace(tmp_path).read("large", length=MAX_READ_BYTES + 100)
    assert result["length"] == MAX_READ_BYTES
    assert result["eof"] is False


def test_write_updates_atomically_and_checks_stale_hash(tmp_path, monkeypatch):
    target = tmp_path / "file.txt"
    target.write_text("before")
    ws = Workspace(tmp_path)
    published = []
    replace = os.replace

    def record(source, destination):
        published.append((Path(source), Path(destination)))
        return replace(source, destination)

    monkeypatch.setattr("musebridge.workspace.os.replace", record)
    result = ws.write("file.txt", "after", expected_sha256=digest(b"before"))
    assert target.read_text() == "after"
    assert result["sha256"] == digest(b"after")
    assert len(published) == 1 and published[0][0].parent == target.parent
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        ws.write("file.txt", "stale", expected_sha256=digest(b"before"))
    assert target.read_text() == "after"
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_create_is_exclusive_and_new_files_are_atomic(tmp_path):
    ws = Workspace(tmp_path)
    result = ws.write("new.txt", "hello 🌎", create=True)
    assert result["sha256"] == digest("hello 🌎".encode())
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "hello 🌎"
    with pytest.raises(FileExistsError):
        ws.write("new.txt", "clobber", create=True)
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "hello 🌎"


def test_write_requires_existing_target_unless_create_requested(tmp_path):
    ws = Workspace(tmp_path)
    with pytest.raises(FileNotFoundError):
        ws.write("missing.txt", "no")
    assert not (tmp_path / "missing.txt").exists()


def test_write_enforces_size_and_preserves_original_on_failure(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("unchanged")
    ws = Workspace(tmp_path)
    with pytest.raises(ValueError, match="byte limit"):
        ws.write("file.txt", "x" * (MAX_WRITE_BYTES + 1))
    assert target.read_text() == "unchanged"


def test_write_protects_git_private_dirs_and_missing_parents(tmp_path):
    git = tmp_path / ".git"
    git.mkdir()
    (git / "config").write_text("keep")
    ws = Workspace(tmp_path)
    with pytest.raises(ValueError, match=".git"):
        ws.write(".git/config", "break")
    with pytest.raises(FileNotFoundError):
        ws.write("absent/file.txt", "no", create=True)
    assert (git / "config").read_text() == "keep"


def test_edit_requires_one_exact_match_and_expected_hash(tmp_path):
    target = tmp_path / "edit.txt"
    target.write_text("a old b")
    ws = Workspace(tmp_path)
    result = ws.edit("edit.txt", "old", "new", digest(b"a old b"))
    assert result["sha256"] == digest(b"a new b")
    assert target.read_text() == "a new b"
    for text in ("none here", "old and old"):
        target.write_text(text)
        before = target.read_bytes()
        with pytest.raises(ValueError, match="exactly one match"):
            ws.edit("edit.txt", "old", "replacement", digest(before))
        assert target.read_bytes() == before
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        ws.edit("edit.txt", "none", "changed", digest(b"stale"))


def test_edit_failure_during_publication_leaves_target_and_cleans_temp(tmp_path, monkeypatch):
    target = tmp_path / "edit.txt"
    target.write_text("old")

    def fail_replace(*args, **kwargs):
        raise OSError("publication failed")

    monkeypatch.setattr("musebridge.workspace.os.replace", fail_replace)
    with pytest.raises(OSError, match="publication failed"):
        Workspace(tmp_path).edit("edit.txt", "old", "new", digest(b"old"))
    assert target.read_text() == "old"
    assert not list(tmp_path.glob(".*.tmp"))


def test_list_and_search_output_are_bounded_and_search_is_literal(tmp_path):
    for i in range(5):
        (tmp_path / f"file{i}.txt").write_text("a.b matches\naXb does not\n")
    ws = Workspace(tmp_path)
    listing = ws.list(limit=2)
    assert len(listing["entries"]) == 2
    assert listing["truncated"] is True
    result = ws.search("a.b", limit=3)
    assert result["mode"] == "literal"
    assert len(result["matches"]) == 3
    assert result["truncated"] is True
    assert all("a.b" in item["text"] for item in result["matches"])


def test_search_fallback_is_safe_and_bounded(tmp_path, monkeypatch):
    (tmp_path / "match.txt").write_text("needle\nneedle\n")
    (tmp_path / ".local").mkdir()
    (tmp_path / ".local" / "secret.txt").write_text("needle\n")
    monkeypatch.setattr("musebridge.workspace.shutil.which", lambda _name: None)
    result = Workspace(tmp_path).search("needle", limit=1)
    assert result["engine"] == "python"
    assert result["truncated"] is True
    assert len(result["matches"]) == 1
    assert all(".local" not in item["path"] for item in result["matches"])


def test_git_status_is_structured_and_read_only(tmp_path):
    result = Workspace(tmp_path).git_status()
    assert result["available"] is (shutil.which("git") is not None)
    assert isinstance(result["entries"], list)
    assert "is_repository" in result


def test_rg_and_git_invocations_use_argument_vectors(monkeypatch, tmp_path):
    commands = []

    class Completed:
        returncode = 1
        stdout = b""
        stderr = b""

    monkeypatch.setattr("musebridge.workspace.shutil.which", lambda name: f"C:/tools/{name}.exe")

    def run(args, **kwargs):
        commands.append((args, kwargs))
        return Completed()

    monkeypatch.setattr("musebridge.workspace.subprocess.run", run)
    ws = Workspace(tmp_path)
    ws.search("a; echo unsafe")
    ws.git_status()
    assert all(isinstance(args, list) for args, _ in commands)
    assert all("shell" not in kwargs or kwargs["shell"] is False for _, kwargs in commands)

