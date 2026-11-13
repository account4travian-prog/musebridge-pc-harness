from __future__ import annotations

import os
from pathlib import Path

import pytest

from musebridge.workspace import Workspace
from musebridge import workspace_index


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    return Workspace(root)


def put(workspace, path, text):
    target = workspace.root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def fallback(monkeypatch):
    monkeypatch.setattr(workspace_index.shutil, "which", lambda _: None)


def json_size(payload):
    import json
    return len(json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii"))


def test_search_regex_literal_case_and_context(workspace, monkeypatch):
    fallback(monkeypatch)
    put(workspace, "src/a.txt", "before\nNeedle 42\nafter\nneedle lower\n")
    result = workspace_index.search_files(workspace, r"Needle\s+\d+", path="src", context=1)
    assert result["results"] == [{"path": "src/a.txt", "matches": [
        {"line": 2, "text": "Needle 42", "before": ["before"], "after": ["after"]}]}]
    assert workspace_index.search_files(workspace, "needle", path="src", literal=True)["count"] == 1
    assert workspace_index.search_files(workspace, "needle", path="src", literal=True,
                                         ignore_case=True)["count"] == 2


def test_search_modes_gitignore_nested_and_negation(workspace, monkeypatch):
    fallback(monkeypatch)
    put(workspace, ".gitignore", "*.log\ncache/\n!cache/\n")
    put(workspace, "cache/.gitignore", "*\n!keep.txt\n")
    put(workspace, "cache/keep.txt", "needle\nneedle again\n")
    put(workspace, "cache/drop.txt", "needle\n")
    put(workspace, "src/.gitignore", "*.tmp\n!keep.tmp\n")
    put(workspace, "src/ignore.tmp", "needle\n")
    put(workspace, "src/keep.tmp", "needle\n")
    put(workspace, "ignored.log", "needle\n")
    files = workspace_index.search_files(workspace, "needle", mode="files")
    assert [row["path"] for row in files["results"]] == ["cache/keep.txt", "src/keep.tmp"]
    counts = workspace_index.search_files(workspace, "needle", mode="count")
    assert counts["count"] == 3


def test_selected_workspace_does_not_inherit_parent_gitignore(tmp_path, monkeypatch):
    fallback(monkeypatch)
    outer = tmp_path / "outer"
    root = outer / "selected"
    root.mkdir(parents=True)
    (outer / ".gitignore").write_text("*.txt\n", encoding="utf-8")
    (root / "visible.txt").write_text("needle\n", encoding="utf-8")
    ws = Workspace(root)
    result = workspace_index.search_files(ws, "needle")
    assert [row["path"] for row in result["results"]] == ["visible.txt"]


def test_python_fallback_searches_selected_file(workspace, monkeypatch):
    fallback(monkeypatch)
    target = put(workspace, "one_file.txt", "before\nNeedle 42\nafter\n")
    result = workspace_index.search_files(workspace, r"Needle\s+\d+", path="one_file.txt", context=1)
    assert result["results"] == [{"path": "one_file.txt", "matches": [
        {"line": 2, "text": "Needle 42", "before": ["before"], "after": ["after"]}]}]


def test_tree_depth_counts_directories_first_and_cap(workspace):
    put(workspace, "z.txt", "z")
    put(workspace, "b/inside.txt", "x")
    put(workspace, "a.txt", "a")
    tree = workspace_index.list_tree(workspace, depth=2, limit=2)
    assert tree["entries"] == 2
    assert tree["truncated"] is True
    assert tree["omitted_count"] == 2
    assert tree["tree"].splitlines()[0] == "b/"
    assert "2 entries omitted" in tree["tree"]
    shallow = workspace_index.list_tree(workspace, depth=1)
    assert "inside.txt" not in shallow["tree"]


def test_directory_visit_cap_marks_tree_count_unknown(workspace, monkeypatch):
    for index in range(8):
        put(workspace, f"{index:02}.txt", "value")
    monkeypatch.setattr(workspace_index, "MAX_FILES", 3)
    tree = workspace_index.list_tree(workspace, depth=1, limit=200)
    assert tree["truncated"] is True
    assert tree["count_exact"] is False
    assert tree["omitted_count"] is None
    assert "scan capped" in tree["tree"]


def test_private_dirs_and_symlink_are_not_traversed(workspace, tmp_path, monkeypatch):
    fallback(monkeypatch)
    put(workspace, ".git/config", "needle")
    put(workspace, ".local/state", "needle")
    put(workspace, "visible.txt", "needle")
    outside = tmp_path / "outside.txt"
    outside.write_text("needle", encoding="utf-8")
    try:
        os.symlink(outside, workspace.root / "linked.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    result = workspace_index.search_files(workspace, "needle")
    assert [row["path"] for row in result["results"]] == ["visible.txt"]
    tree = workspace_index.list_tree(workspace)
    assert ".git" not in tree["tree"] and ".local" not in tree["tree"]


def test_reserved_and_configured_private_roots_are_excluded(workspace, monkeypatch):
    for name in ("credentials", "credential", "local-data", ".git", ".local"):
        put(workspace, f"{name}/secret.txt", "needle\n")
    private = put(workspace, "vault/secret.txt", "needle\n").parent.resolve()
    workspace._private_roots = (*workspace._private_roots, private)
    called = []
    monkeypatch.setattr(workspace_index.shutil, "which", lambda _: "rg")
    monkeypatch.setattr(workspace_index.subprocess, "Popen", lambda *args, **kwargs: called.append(args) or None)
    result = workspace_index.search_files(workspace, "needle")
    assert called == []  # a private root inside this workspace forces guarded traversal
    assert result["results"] == []


def test_rg_gets_case_insensitive_reserved_exclusions(workspace, monkeypatch):
    captured = {}

    class FailedStart(OSError):
        pass

    def fail_start(args, **kwargs):
        captured["args"] = args
        raise FailedStart("intentional test interception")

    monkeypatch.setattr(workspace_index.shutil, "which", lambda _: "rg")
    monkeypatch.setattr(workspace_index.subprocess, "Popen", fail_start)
    with pytest.raises(OSError, match="could not be started"):
        workspace_index.search_files(workspace, "needle")
    args = captured["args"]
    iglobs = [args[index + 1] for index, arg in enumerate(args[:-1]) if arg == "--iglob"]
    assert any("credentials" in pattern and "credential" in pattern and "local-data" in pattern for pattern in iglobs)
    assert any(".git" in pattern and ".local" in pattern for pattern in iglobs)


def test_search_cap_and_glob_hint(workspace, monkeypatch):
    fallback(monkeypatch)
    for index in range(5):
        put(workspace, f"logs/{index}.log", "needle\n")
    result = workspace_index.search_files(workspace, "needle", glob="*.log", max_results=2)
    assert len(result["results"]) == 2
    assert result["truncated"] is True
    assert result["omitted_count"] == 3
    assert "glob" in result["hint"]


def test_fallback_file_scan_and_size_caps(workspace, monkeypatch):
    fallback(monkeypatch)
    put(workspace, "a.txt", "needle\n")
    put(workspace, "b.txt", "needle\n")
    put(workspace, "c.txt", "needle\n")
    put(workspace, "large.txt", "x" * (workspace_index.MAX_FILE_BYTES + 1))
    monkeypatch.setattr(workspace_index, "MAX_FILES", 2)
    result = workspace_index.search_files(workspace, "needle")
    assert result["files_scanned"] == 2
    assert result["truncated"] is True
    assert result["count_exact"] is False
    assert all(row["path"] != "large.txt" for row in result["results"])


def test_fallback_regex_timeout_reports_unknown_omissions(workspace, monkeypatch):
    fallback(monkeypatch)
    put(workspace, "adversarial.txt", "a" * 100_000 + "!\n")
    monkeypatch.setattr(workspace_index, "MAX_SECONDS", 0.005)
    result = workspace_index.search_files(workspace, r"^(a+)+$")
    assert result["truncated"] is True
    assert result["count_exact"] is False
    assert result["omitted_count"] is None
    assert "narrow" in result["hint"].lower()


def test_search_pattern_and_glob_lengths_are_bounded(workspace):
    with pytest.raises(ValueError, match="pattern must be at most"):
        workspace_index.search_files(workspace, "x" * (workspace_index.MAX_PATTERN_CHARS + 1))
    with pytest.raises(ValueError, match="glob must be at most"):
        workspace_index.search_files(workspace, "x", glob="x" * (workspace_index.MAX_PATTERN_CHARS + 1))
    with pytest.raises(ValueError, match="must be booleans"):
        workspace_index.search_files(workspace, "x", literal=1)


def test_search_payload_budget_accounts_for_unicode_pattern_and_controls(workspace, monkeypatch):
    fallback(monkeypatch)
    put(workspace, "unicode-😀.txt", "\x01needle\x01\n")
    pattern = "😀" * 999
    result = workspace_index.search_files(workspace, pattern)
    assert json_size(result) <= workspace_index.MAX_OUTPUT_BYTES
    assert result["pattern"] == pattern
    controls = workspace_index.search_files(workspace, "needle", literal=True)
    assert json_size(controls) <= workspace_index.MAX_OUTPUT_BYTES
    assert controls["results"][0]["path"] == "unicode-😀.txt"


def test_tree_payload_budget_with_unicode_controls_and_long_path_metadata(workspace, monkeypatch):
    fake_entry = workspace.root / "unicode-😀\x01.txt"
    monkeypatch.setattr(workspace_index, "_walk", lambda *args, **kwargs: iter([(fake_entry, False, 1)]))
    tree = workspace_index.list_tree(workspace)
    assert json_size(tree) <= workspace_index.MAX_OUTPUT_BYTES
    assert "unicode-😀\x01.txt" in tree["tree"]

    long_relative = Path(*(f"segment-{i:03}-" + "x" * 180 for i in range(20)))
    long_start = workspace.root / long_relative
    monkeypatch.setattr(workspace_index, "_checked_dir", lambda *_: long_start)
    monkeypatch.setattr(workspace_index, "_walk", lambda *args, **kwargs: iter(()))
    long_tree = workspace_index.list_tree(workspace, path="placeholder")
    assert long_tree["path_is_preview"] is True
    assert "path preview" in long_tree["path"]
    assert json_size(long_tree) <= workspace_index.MAX_OUTPUT_BYTES


def test_search_drops_long_result_path_instead_of_returning_partial_path(workspace, monkeypatch):
    fallback(monkeypatch)
    put(workspace, "hit.txt", "needle\n")
    long_relative = "\x01" * 4000
    monkeypatch.setattr(workspace_index, "_files", lambda *args, **kwargs: iter([(workspace.root / "hit.txt", long_relative)]))
    result = workspace_index.search_files(workspace, "needle")
    assert result["truncated"] is True
    assert result["omitted_count"] == 1
    assert result["results"] == []
    assert json_size(result) <= workspace_index.MAX_OUTPUT_BYTES


def test_invalid_paths_modes_patterns_and_rg_failure(workspace, monkeypatch):
    with pytest.raises(ValueError):
        workspace_index.list_tree(workspace, path=".local")
    with pytest.raises(ValueError):
        workspace_index.search_files(workspace, "x", path="credentials")
    with pytest.raises(ValueError):
        workspace_index.search_files(workspace, "[", literal=False)
    with pytest.raises(ValueError):
        workspace_index.search_files(workspace, "x", mode="bad")
    put(workspace, "sample.txt", "needle\n")
    monkeypatch.setattr(workspace_index.shutil, "which", lambda _: "rg")
    monkeypatch.setattr(workspace_index.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("missing")))
    with pytest.raises(OSError, match="could not be started"):
        workspace_index.search_files(workspace, "needle")
