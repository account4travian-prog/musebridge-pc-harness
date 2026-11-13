"""Scoped Git inspection and explicit isolated worktree creation.

This is a mistake-prevention boundary around the selected project's Git repo,
not an OS sandbox. Git commands run as the current Windows user.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from pathlib import Path

MAX_DIFF_BYTES = 512 * 1024
MAX_STATUS_ENTRIES = 1000
MAX_BRANCHES = 500
MAX_WORKTREES = 100
MAX_COMMAND_OUTPUT = 2 * 1024 * 1024
COMMAND_TIMEOUT = 20
_PRIVATE_PATH_PARTS = {".git", ".local", "local-data", "credentials", "credential"}


def _hidden_options() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


class GitTools:
    """Git status/diff plus deliberate creation of a fresh linked worktree."""

    def __init__(self, catalog, state_dir):
        self.catalog = catalog
        self.state_dir = Path(state_dir).expanduser().resolve(strict=False)
        self.worktrees_dir = self.state_dir / "worktrees"
        self.hooks_dir = self.state_dir / "disabled-git-hooks"
        self._lock = threading.RLock()
        self._git = shutil.which("git")
        with catalog.lock, catalog.db:
            catalog.db.execute("""CREATE TABLE IF NOT EXISTS git_worktrees_v1 (
                worktree_id TEXT PRIMARY KEY,
                source_conversation_id TEXT NOT NULL,
                source_project_id TEXT NOT NULL,
                source_repo_root TEXT NOT NULL,
                source_project_root TEXT NOT NULL,
                worktree_root TEXT NOT NULL,
                worktree_project_root TEXT NOT NULL,
                branch TEXT NOT NULL,
                base_ref TEXT NOT NULL,
                base_commit TEXT NOT NULL,
                new_project_id TEXT NOT NULL,
                new_conversation_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('preparing','unknown','git_created','ready')),
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                error TEXT
            )""")
            catalog.db.execute("CREATE INDEX IF NOT EXISTS git_worktree_owner ON git_worktrees_v1(source_conversation_id,created_at)")

    def _require_git(self):
        if not self._git:
            raise OSError("Git executable was not found")
        return self._git

    @staticmethod
    def _is_inside(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False

    @staticmethod
    def _reject_reparse_ancestors(path: Path, *, include_leaf=True):
        """Reject symlinks/junctions along a selected root before canonicalizing."""
        current = path if include_leaf else path.parent
        chain = list(current.parents)[::-1] + [current]
        for item in chain:
            try:
                if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
                    raise ValueError("Git path traverses a symlink or junction")
            except OSError as exc:
                raise ValueError("Git path could not be inspected safely") from exc

    def _context(self, cid: str) -> tuple[dict, dict, Path, Path]:
        conversation = self.catalog.conversation(cid)
        if not conversation.get("project_id"):
            raise ValueError("Git tools require a project selected for this conversation")
        project = self.catalog.project(conversation["project_id"])
        raw_root = Path(project["root"]).expanduser()
        self._reject_reparse_ancestors(raw_root)
        root = raw_root.resolve(strict=True)
        if root != raw_root or not root.is_dir():
            raise ValueError("selected project root identity changed")
        completed = self._run(["rev-parse", "--show-toplevel"], cwd=root)
        repo_root = Path(completed.stdout.decode("utf-8", "replace").strip()).resolve(strict=True)
        if not self._is_inside(root, repo_root):
            raise ValueError("selected project is not inside the reported Git worktree")
        return conversation, project, root, repo_root

    def _environment(self) -> dict:
        env = os.environ.copy()
        env.update({
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_LFS_SKIP_SMUDGE": "1",
        })
        return env

    def _run(self, args, *, cwd: Path, max_output=MAX_COMMAND_OUTPUT):
        git = self._require_git()
        argv = [git, "-c", f"core.hooksPath={self.hooks_dir}", "-c", "core.fsmonitor=false", *args]
        # A pair of draining reader threads bounds retained output even for a
        # repository with a very large status/diff, without pipe deadlocks.
        try:
            proc = subprocess.Popen(argv, cwd=str(cwd), env=self._environment(),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, shell=False, **_hidden_options())
        except OSError as exc:
            raise OSError(f"could not start Git: {exc}") from exc
        outputs = [bytearray(), bytearray()]
        truncated = [False, False]

        def drain(stream, index):
            while True:
                data = stream.read(65536)
                if not data:
                    return
                remaining = max_output - len(outputs[index])
                if remaining > 0:
                    outputs[index].extend(data[:remaining])
                if len(data) > remaining:
                    truncated[index] = True

        threads = [threading.Thread(target=drain, args=(proc.stdout, 0), daemon=True),
                   threading.Thread(target=drain, args=(proc.stderr, 1), daemon=True)]
        for thread in threads:
            thread.start()
        try:
            code = proc.wait(timeout=COMMAND_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            proc.wait()
            for thread in threads:
                thread.join(timeout=2)
            raise OSError(f"Git command exceeded {COMMAND_TIMEOUT} seconds") from exc
        for thread in threads:
            thread.join(timeout=3)
        result = subprocess.CompletedProcess(argv, code, bytes(outputs[0]), bytes(outputs[1]))
        result.stdout_truncated = truncated[0]
        result.stderr_truncated = truncated[1]
        if code:
            message = result.stderr.decode("utf-8", "replace").strip()[:2000]
            if "not a git repository" in message.casefold():
                raise ValueError("selected project is not in a Git repository")
            raise OSError(message or f"Git command failed with exit code {code}")
        return result

    def status(self, cid: str) -> dict:
        conversation, project, root, _repo = self._context(cid)
        result = self._run(["status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=root)
        fields = result.stdout.decode("utf-8", "replace").split("\0")
        entries = []
        i = 0
        while i < len(fields) and fields[i]:
            record = fields[i]
            i += 1
            if len(record) < 4:
                continue
            status, path = record[:2], record[3:]
            if self._private_repo_path(path):
                if "R" in status or "C" in status:
                    i += 1
                continue
            entry = {"status": status, "path": path}
            if "R" in status or "C" in status:
                if i < len(fields) and fields[i]:
                    if not self._private_repo_path(fields[i]):
                        entry["original_path"] = fields[i]
                    i += 1
            entries.append(entry)
        branch = self._run(["symbolic-ref", "--short", "-q", "HEAD"], cwd=root, max_output=4096)
        return {"conversation_id": conversation["id"], "project_id": project["id"],
                "branch": branch.stdout.decode("utf-8", "replace").strip() or None,
                "entries": entries[:MAX_STATUS_ENTRIES], "count": len(entries),
                "truncated": len(entries) > MAX_STATUS_ENTRIES or result.stdout_truncated}

    def _pathspec(self, root: Path, repo_root: Path, supplied: str | None) -> str | None:
        if supplied is None:
            return None
        if not isinstance(supplied, str) or not supplied or "\x00" in supplied:
            raise ValueError("path must be a non-empty relative path")
        given = Path(supplied)
        if given.is_absolute() or given.drive:
            raise ValueError("Git diff path must be relative to the selected project")
        if any(p.casefold() in _PRIVATE_PATH_PARTS for p in given.parts):
            raise ValueError("private Git/application paths are not available")
        candidate = (root / given).resolve(strict=False)
        if not self._is_inside(candidate, root):
            raise ValueError("Git diff path escapes the selected project")
        if self._reject_if_linked_path(root, candidate):
            raise ValueError("Git diff path traverses a symlink or junction")
        rel_repo = candidate.relative_to(repo_root).as_posix()
        if rel_repo in ("", "."):
            return "."
        return ":(literal)" + rel_repo

    @staticmethod
    def _private_repo_path(value: str) -> bool:
        return any(part.casefold() in _PRIVATE_PATH_PARTS for part in Path(value).parts)

    @staticmethod
    def _reject_if_linked_path(root: Path, candidate: Path) -> bool:
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            return True
        current = root
        for component in relative.parts:
            current = current / component
            if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
                return True
        return False

    def diff(self, cid: str, path=None, staged=False) -> dict:
        if not isinstance(staged, bool):
            raise ValueError("staged must be a boolean")
        conversation, project, root, repo_root = self._context(cid)
        pathspec = self._pathspec(root, repo_root, path)
        if pathspec is None and root != repo_root:
            pathspec = ":(literal)" + root.relative_to(repo_root).as_posix()
        args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames", "--unified=3"]
        if staged:
            args.append("--cached")
        args.append("--")
        if pathspec is not None:
            args.append(pathspec)
        # These are excluded even for a default whole-project diff, so a
        # tracked local credential artifact cannot leak via diff output.
        for private in (".local", "local-data", "credentials", "credential"):
            args.append(":(exclude,glob)**/" + private + "/**")
        result = self._run(args, cwd=repo_root, max_output=MAX_DIFF_BYTES + 1)
        raw = result.stdout[:MAX_DIFF_BYTES]
        truncated = len(result.stdout) > MAX_DIFF_BYTES or result.stdout_truncated
        return {"conversation_id": conversation["id"], "project_id": project["id"],
                "path": path or ".", "staged": staged,
                "diff": raw.decode("utf-8", "replace"), "bytes": len(raw), "truncated": truncated}

    def branches(self, cid: str) -> dict:
        conversation, project, root, _repo = self._context(cid)
        result = self._run(["for-each-ref", "--format=%(refname:short)%09%(objectname)", "refs/heads"], cwd=root)
        rows = []
        for line in result.stdout.decode("utf-8", "replace").splitlines():
            name, _, commit = line.partition("\t")
            if name:
                rows.append({"name": name[:512], "commit": commit[:64]})
        return {"conversation_id": conversation["id"], "project_id": project["id"],
                "branches": rows[:MAX_BRANCHES], "count": len(rows), "truncated": len(rows) > MAX_BRANCHES}

    def worktrees(self, cid: str) -> dict:
        conversation, project, root, repo_root = self._context(cid)
        result = self._run(["worktree", "list", "--porcelain"], cwd=root)
        rows = []
        current = {}
        for line in result.stdout.decode("utf-8", "replace").splitlines() + [""]:
            if not line:
                if current:
                    rows.append(current)
                    current = {}
                continue
            key, _, value = line.partition(" ")
            if key == "worktree":
                current["path"] = value[:2048]
            elif key == "HEAD":
                current["head"] = value[:64]
            elif key == "branch":
                current["branch"] = value.removeprefix("refs/heads/")[:512]
            elif key in {"detached", "bare", "locked", "prunable"}:
                current[key] = True
        with self.catalog.lock:
            registered = [dict(row) for row in self.catalog.db.execute(
                "SELECT worktree_id,worktree_root,worktree_project_root,branch,base_ref,base_commit,state,new_project_id,new_conversation_id,created_at,error "
                "FROM git_worktrees_v1 WHERE source_repo_root=? ORDER BY created_at DESC LIMIT ?",
                (str(repo_root), MAX_WORKTREES))]
        for row in rows:
            row["managed"] = any(os.path.normcase(row.get("path", "")) == os.path.normcase(item["worktree_root"])
                                 for item in registered)
        return {"conversation_id": conversation["id"], "project_id": project["id"],
                "worktrees": rows[:MAX_WORKTREES], "count": len(rows), "truncated": len(rows) > MAX_WORKTREES,
                "managed": registered}

    @staticmethod
    def _validate_branch(branch: str):
        if not isinstance(branch, str) or not branch or len(branch) > 200 or "\x00" in branch:
            raise ValueError("branch must be a valid short branch name")

    def _resolve_base(self, root: Path, base: str | None) -> tuple[str, str]:
        if base is None or base == "":
            supplied_ref = "HEAD"
            label = "HEAD"
        elif not isinstance(base, str) or len(base) > 512 or "\x00" in base or base.startswith("-"):
            raise ValueError("base must name a branch, tag, or commit")
        elif base.startswith("refs/"):
            self._check_ref_format(root, base)
            if not base.startswith(("refs/heads/", "refs/remotes/", "refs/tags/")):
                raise ValueError("base ref must be a branch, remote branch, or tag")
            supplied_ref, label = base, base
        else:
            self._check_ref_format(root, base, branch=True)
            supplied_ref, label = base, base
        resolved = self._run(["rev-parse", "--verify", "--end-of-options", f"{supplied_ref}^{{commit}}"],
                             cwd=root, max_output=4096).stdout.decode("ascii", "replace").strip()
        if not resolved or len(resolved) > 64 or any(c not in "0123456789abcdefABCDEF" for c in resolved):
            raise ValueError("base does not resolve to a commit")
        return label, resolved

    def _check_ref_format(self, root: Path, ref: str, *, branch=False) -> str:
        args = ["check-ref-format"] + (["--branch"] if branch else []) + [ref]
        result = subprocess.run([self._require_git(), *args], cwd=str(root), env=self._environment(),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                shell=False, timeout=10, **_hidden_options())
        if result.returncode:
            raise ValueError("invalid Git ref or branch name")
        return result.stdout.decode("utf-8", "replace").strip()

    def _reject_checkout_filters(self, root: Path):
        result = subprocess.run([self._require_git(), "config", "--local", "--get-regexp", r"^filter\..*\.(smudge|process)$"],
                                cwd=str(root), env=self._environment(), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False,
                                timeout=10, **_hidden_options())
        if result.returncode == 0 and result.stdout.strip():
            raise ValueError("worktree creation refused: local Git checkout filters can execute external commands")

    def create_worktree(self, cid: str, branch: str, base=None, *, guard=None) -> dict:
        self._validate_branch(branch)
        with self._lock:
            conversation, project, root, repo_root = self._context(cid)
            # Worktree storage must be a separate tree from the source repo.
            self._reject_reparse_ancestors(self.state_dir, include_leaf=False)
            state_resolved = self.state_dir.resolve(strict=False)
            if self._is_inside(state_resolved, repo_root) or self._is_inside(repo_root, state_resolved):
                raise ValueError("worktree storage must be outside the selected Git repository")
            self.worktrees_dir.mkdir(parents=True, exist_ok=True)
            self.hooks_dir.mkdir(parents=True, exist_ok=True)
            self._reject_reparse_ancestors(self.worktrees_dir)
            self._reject_checkout_filters(root)
            if (repo_root / ".gitmodules").is_file():
                raise ValueError("worktree creation does not initialize Git submodules; prepare submodule handling explicitly")
            normalized = self._check_ref_format(root, branch, branch=True)
            if normalized != branch:
                raise ValueError("branch must be an explicit, unmodified branch name")
            branch_ref = "refs/heads/" + branch
            exists = subprocess.run([self._require_git(), "show-ref", "--verify", "--quiet", branch_ref],
                                    cwd=str(root), env=self._environment(), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False,
                                    timeout=10, **_hidden_options())
            if exists.returncode == 0:
                raise ValueError("branch already exists; worktree creation requires a fresh branch")
            if exists.returncode not in (1,):
                raise OSError("could not determine whether the branch already exists")
            base_ref, base_commit = self._resolve_base(root, base)
            # Conservative lock/unknown detection includes every prior record.
            with self.catalog.lock:
                prior = self.catalog.db.execute("SELECT state FROM git_worktrees_v1 WHERE source_repo_root=? AND branch=? LIMIT 1",
                                                (str(repo_root), branch)).fetchone()
            if prior:
                raise ValueError("branch is already reserved by a worktree record; inspect worktrees before retrying")
            worktree_id = uuid.uuid4().hex
            worktree_root = (self.worktrees_dir / worktree_id).resolve(strict=False)
            if worktree_root.exists():
                raise FileExistsError("generated worktree path is unexpectedly occupied")
            project_relative = root.relative_to(repo_root)
            worktree_project_root = worktree_root / project_relative
            new_project_id = str(uuid.uuid4())
            new_conversation_id = str(uuid.uuid4())
            now = time.time()
            with self.catalog.lock, self.catalog.db:
                self.catalog.db.execute("""INSERT INTO git_worktrees_v1 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (worktree_id, cid, project["id"], str(repo_root), str(root), str(worktree_root),
                     str(worktree_project_root), branch, base_ref, base_commit, new_project_id,
                     new_conversation_id, "preparing", now, now, None))
            # A crash, timeout, or partial error after this point is recorded as
            # uncertain and never retried or auto-cleaned by this service.
            try:
                if guard:guard()
                added = self._run(["worktree", "add", "-b", branch, str(worktree_root), base_commit], cwd=root)
            except Exception as exc:
                self._set_journal(worktree_id, "unknown", str(exc)[:1000])
                raise OSError(f"worktree add outcome is uncertain; inspect the Git worktrees list ({type(exc).__name__})") from exc
            self._set_journal(worktree_id, "git_created", None)
            try:
                project_name = (project["name"] + " (" + branch + ")")[:200]
                title = ("Worktree: " + branch)[:200]
                with self.catalog.lock, self.catalog.db:
                    self.catalog.db.execute("INSERT INTO projects(id,name,root,instructions) VALUES(?,?,?,?)",
                                            (new_project_id, project_name, str(worktree_project_root), project.get("instructions", "")))
                    self.catalog.db.execute("INSERT INTO conversations(id,project_id,title,remote_id,created,plan,goal,goal_state,archived) "
                                            "VALUES(?,?,?,?,?,'','','idle',0)",
                                            (new_conversation_id, new_project_id, title, new_conversation_id, time.time()))
                    self.catalog.db.execute("UPDATE git_worktrees_v1 SET state='ready',updated_at=? WHERE worktree_id=?",
                                            (time.time(), worktree_id))
            except Exception as exc:
                self._set_journal(worktree_id, "git_created", "registered catalog metadata pending repair")
                raise OSError("Git worktree was created but catalog registration failed; inspect the managed worktree record") from exc
            return {"worktree_id": worktree_id, "state": "ready", "branch": branch,
                    "base_ref": base_ref, "base_commit": base_commit, "worktree_root": str(worktree_root),
                    "project": self.catalog.project(new_project_id),
                    "conversation": self.catalog.conversation(new_conversation_id),
                    "git_output": added.stdout.decode("utf-8", "replace")[:2000]}

    def _set_journal(self, identifier, state, error):
        with self.catalog.lock, self.catalog.db:
            self.catalog.db.execute("UPDATE git_worktrees_v1 SET state=?,error=?,updated_at=? WHERE worktree_id=?",
                                    (state, error, time.time(), identifier))
