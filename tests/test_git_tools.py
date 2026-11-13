import subprocess
from pathlib import Path

import pytest

from musebridge.catalog import Catalog
from musebridge.git_tools import GitTools, MAX_DIFF_BYTES


def git(repo, *args, check=True):
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, shell=False)
    if check and result.returncode:
        raise AssertionError(result.stderr)
    return result


@pytest.fixture
def setup_repo(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "MuseBridge Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "seed")
    catalog = Catalog(tmp_path / "catalog")
    project = catalog.create_project("Disposable", str(repo))
    conversation = catalog.create_conversation("Git fixture", project["id"])
    tools = GitTools(catalog, tmp_path / "app-state")
    yield tmp_path, repo, catalog, project, conversation, tools
    catalog.close()


def test_status_diff_branches_and_worktrees_are_scoped(setup_repo):
    _tmp, repo, _catalog, _project, conversation, tools = setup_repo
    (repo / "tracked.txt").write_text("base\nunstaged\n", encoding="utf-8")
    (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
    git(repo, "add", "staged.txt")
    (repo / "untracked.txt").write_text("new\n", encoding="utf-8")

    status = tools.status(conversation["id"])
    by_path = {entry["path"]: entry["status"] for entry in status["entries"]}
    assert by_path["tracked.txt"] == " M"
    assert by_path["staged.txt"].startswith("A")
    assert by_path["untracked.txt"] == "??"
    assert "unstaged" in tools.diff(conversation["id"], "tracked.txt")["diff"]
    assert "staged" in tools.diff(conversation["id"], "staged.txt", staged=True)["diff"]
    assert tools.branches(conversation["id"])["branches"][0]["name"] == "main"
    assert len(tools.worktrees(conversation["id"])["worktrees"]) == 1


def test_fresh_worktree_is_registered_and_preserves_original_dirty_state(setup_repo):
    tmp_path, repo, catalog, _project, conversation, tools = setup_repo
    (repo / "tracked.txt").write_text("owner staged\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    (repo / "tracked.txt").write_text("owner staged plus unstaged\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("owner untracked\n", encoding="utf-8")
    original_head = git(repo, "rev-parse", "HEAD").stdout.strip()

    result = tools.create_worktree(conversation["id"], "muse/task-17")
    worktree = Path(result["worktree_root"])
    assert result["state"] == "ready"
    assert result["project"]["root"] == str(worktree)
    assert result["conversation"]["project_id"] == result["project"]["id"]
    assert (worktree / "tracked.txt").read_text(encoding="utf-8") == "base\n"
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == original_head
    assert git(repo, "diff", "--cached", "--", "tracked.txt").stdout
    assert "owner staged plus unstaged" in git(repo, "diff", "--", "tracked.txt").stdout
    assert (repo / "untracked.txt").read_text(encoding="utf-8") == "owner untracked\n"
    assert git(repo, "status", "--porcelain=v1").stdout.count("\n") >= 2
    assert tools.worktrees(conversation["id"])["managed"][0]["state"] == "ready"
    assert catalog.conversation(result["conversation"]["id"])["project_id"] == result["project"]["id"]


def test_worktree_creation_refuses_occupied_branch_and_never_retries(setup_repo):
    _tmp, repo, _catalog, _project, conversation, tools = setup_repo
    git(repo, "branch", "already-there")
    with pytest.raises(ValueError, match="fresh branch"):
        tools.create_worktree(conversation["id"], "already-there")
    with pytest.raises(ValueError, match="branch"):
        tools.create_worktree(conversation["id"], "bad..branch")
    with pytest.raises(OSError):
        tools.create_worktree(conversation["id"], "fresh", base="missing-ref")


def test_uncertain_add_is_journaled_and_branch_stays_reserved(setup_repo, monkeypatch):
    _tmp, _repo, catalog, _project, conversation, tools = setup_repo
    real_run = tools._run

    def fail_at_worktree_add(args, **kwargs):
        if args and args[0] == "worktree":
            raise OSError("injected timeout after command dispatch")
        return real_run(args, **kwargs)

    monkeypatch.setattr(tools, "_run", fail_at_worktree_add)
    with pytest.raises(OSError, match="outcome is uncertain"):
        tools.create_worktree(conversation["id"], "muse/uncertain")
    row = catalog.rows("SELECT state FROM git_worktrees_v1 WHERE branch=?", ("muse/uncertain",))[0]
    assert row["state"] == "unknown"
    monkeypatch.setattr(tools, "_run", real_run)
    with pytest.raises(ValueError, match="reserved"):
        tools.create_worktree(conversation["id"], "muse/uncertain")


def test_path_escape_private_git_and_link_escape_are_refused(setup_repo, tmp_path):
    _tmp, repo, _catalog, _project, conversation, tools = setup_repo
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    with pytest.raises(ValueError, match="relative"):
        tools.diff(conversation["id"], str(outside))
    with pytest.raises(ValueError, match="escapes"):
        tools.diff(conversation["id"], "../outside.txt")
    with pytest.raises(ValueError, match="private"):
        tools.diff(conversation["id"], ".git/config")
    link = repo / "escape-link"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ValueError, match="symlink or junction"):
        tools.diff(conversation["id"], "escape-link")


def test_diff_output_is_bounded(setup_repo):
    _tmp, repo, _catalog, _project, conversation, tools = setup_repo
    (repo / "tracked.txt").write_text("X" * (MAX_DIFF_BYTES * 2), encoding="utf-8")
    result = tools.diff(conversation["id"], "tracked.txt")
    assert result["truncated"] is True
    assert result["bytes"] <= MAX_DIFF_BYTES


def test_non_git_project_has_clear_error(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    catalog = Catalog(tmp_path / "catalog")
    try:
        project = catalog.create_project("Plain", str(root))
        conversation = catalog.create_conversation("No Git", project["id"])
        tools = GitTools(catalog, tmp_path / "state")
        with pytest.raises(ValueError, match="not in a Git repository"):
            tools.status(conversation["id"])
    finally:
        catalog.close()


def test_subproject_scope_and_diff_paths(setup_repo):
    tmp_path, repo, catalog, _project, _conversation, tools = setup_repo
    nested = repo / "nested"
    nested.mkdir()
    (nested / "inside.txt").write_text("nested base\n", encoding="utf-8")
    git(repo, "add", "nested/inside.txt")
    git(repo, "commit", "-m", "nested fixture")
    subproject = catalog.create_project("Nested", str(nested))
    subchat = catalog.create_conversation("Nested chat", subproject["id"])
    (repo / "outside.txt").write_text("outside change\n", encoding="utf-8")
    (nested / "inside.txt").write_text("nested changed\n", encoding="utf-8")
    diff = tools.diff(subchat["id"])
    assert "nested/inside.txt" in diff["diff"]
    assert "outside.txt" not in diff["diff"]
    with pytest.raises(ValueError, match="escapes"):
        tools.diff(subchat["id"], "../outside.txt")
