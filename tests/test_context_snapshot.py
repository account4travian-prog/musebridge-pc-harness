import json
from pathlib import Path

import pytest

from musebridge import context_snapshot as snapshot_module
from musebridge.catalog import Catalog
from musebridge.context_snapshot import ContextSnapshot, DEFAULT_CONTEXT_JSON_BYTES
from musebridge.workspace import Workspace


class _Catalog:
    def __init__(self, root):
        self.root = str(root)
        self.confirmed = 0

    def conversation(self, cid):
        return {"id": cid, "project_id": "p"}

    def project(self, project_id):
        return {"id": "p", "name": "Fixture", "root": self.root,
                "instructions": "Use the fixture instructions before editing."}

    def context(self, cid):
        return {
            "project": self.project("p"), "project_agents_md": "Fixture instructions",
            "goal": "Check the fixture", "goal_state": "active", "plan": "Read and report",
        }

    def preference(self, key, default=None):
        assert key == "musebridge_skill_confirmed_version"
        return self.confirmed if self.confirmed else default


class _Execution:
    def __init__(self, profile="Project work"):
        self.profile = profile

    def policy(self, cid):
        return {"profile": self.profile}

    def runs(self, cid):
        return [{"id": "r1", "state": "complete", "origin": "user", "created": 1.0}]


class _Tasks:
    def list(self, cid):
        return [{"task_id": "t1", "title": "Check output", "state": "pending",
                 "criteria": "Contains the expected value", "dependencies": [],
                 "verification": {"kind": "manual", "passed": None, "stale": False}}]


class _Continuity:
    def list(self, cid, limit):
        return {"notes": [{"kind": "fact", "version": 1, "created_utc": "today",
                           "text": "Keep the fixture local", "source": "owner", "effective": True}],
                "truncated": False}


class _Git:
    def __init__(self, root, repo_root=None):
        self.root = str(root)
        self.repo_root = str(repo_root if repo_root is not None else root)
        self.status_called = 0

    def _context(self, cid):
        return None, None, self.root, self.repo_root

    def status(self, cid):
        self.status_called += 1
        return {"branch": "main", "count": 2, "truncated": False}


def _router(root, *, schemas=None, repo_root=None):
    router = type("Router", (), {})()
    router.catalog = _Catalog(root)
    router.execution = _Execution()
    router.tasks = _Tasks()
    router.continuity = _Continuity()
    router.git = _Git(root, repo_root)
    router.connection_specs = schemas
    router.registration_specs = lambda: schemas or {}
    router.hooks = type("Hooks", (), {"refresh": lambda self, cid: []})()
    router.vision = type("Vision", (), {"status": lambda self, cid: {"enabled": False, "browser": False, "deliveries": []}})()
    return router


def test_default_context_is_bounded_and_has_project_summary(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "AGENTS.md").write_text("Fixture instructions", encoding="utf-8")
    router = _router(tmp_path)
    snapshot = ContextSnapshot(router)
    calls = []

    def probe(name):
        calls.append(name)
        return {"name": name, "available": True, "version": "1.2.3"}

    monkeypatch.setattr(snapshot, "_probe", probe)
    rendered = snapshot.render("chat-1")
    assert len(json.dumps(rendered, ensure_ascii=True).encode("ascii")) <= DEFAULT_CONTEXT_JSON_BYTES
    assert "Project: Fixture" in rendered
    assert "Root: " + str(tmp_path) in rendered
    assert "Project tree (depth 2)" in rendered
    assert "Git: main; 2 changed file(s)" in rendered
    assert "Fixture instructions" in rendered
    assert "Use the fixture instructions before editing." in rendered
    assert "Check the fixture" in rendered
    assert "t1 [pending] Check output" in rendered
    assert set(calls) == set(snapshot_module._TOOLS + snapshot_module._SHELLS)
    snapshot.environment()
    assert len(calls) == len(snapshot_module._TOOLS + snapshot_module._SHELLS)
    snapshot.reset_environment()
    snapshot.environment()
    assert len(calls) == 2 * len(snapshot_module._TOOLS + snapshot_module._SHELLS)


def test_project_summary_does_not_claim_parent_git_repository(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    router = _router(nested, repo_root=tmp_path)
    result = ContextSnapshot(router).project_summary("chat-1")
    assert result["branch"] is None
    assert result["status_summary"] == "selected project is inside a parent Git repository"
    assert router.git.status_called == 0


def test_tools_sections_render_full_schema_records_by_prefix(tmp_path):
    schemas = {
        "file.read": {"description": "Read a file safely.", "required": {"path": {"type": "string", "description": "Workspace path."}},
                      "optional": {"offset": {"type": "integer", "description": "Starting byte."}}, "timeout_ms": 8000},
        "file.search": {"description": "Search workspace text.", "required": {}, "optional": {}},
        "shell.run": {"description": "Run a bounded command.", "required": {}, "optional": {}},
    }
    context = ContextSnapshot(_router(tmp_path, schemas=schemas))
    index = context.render("chat-1", "tools")
    files = context.render("chat-1", "tools:file")
    assert "file.read" in index and "shell.run" in index
    assert "file.read" in files and "file.search" in files
    assert "shell.run" not in files
    assert "Workspace path." in files and "timeout_ms: 8000" in files


def test_project_instructions_have_full_section_and_long_text_pointer(tmp_path, monkeypatch):
    router = _router(tmp_path)
    router.catalog.context = lambda cid: {
        "project": {"id": "p", "name": "Fixture", "root": str(tmp_path),
                    "instructions": "owner rule " * 2000},
        "goal": "", "goal_state": "idle", "plan": "",
    }
    context = ContextSnapshot(router)
    monkeypatch.setattr(context, "_probe", lambda name: {"name": name, "available": False, "version": "unknown"})
    default = context._render_default("chat-1")
    full = context.render("chat-1", "instructions")
    assert "Project instructions" in default
    assert 'bridge.context(section="instructions")' in default
    assert len(json.dumps(default, ensure_ascii=True).encode("ascii")) <= DEFAULT_CONTEXT_JSON_BYTES
    assert "owner rule" in full
    assert "shortened by the context size limit" in full


def test_skill_content_and_owner_confirmation_status(tmp_path, monkeypatch):
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("---\nname: gadget-musebridge-windows-pc\n---\nExact fixture skill.\n", encoding="utf-8")
    monkeypatch.setattr(snapshot_module, "SKILL_PATH", skill_file)
    router = _router(tmp_path)
    context = ContextSnapshot(router)
    assert context.render("chat-1", "skill") == skill_file.read_text(encoding="utf-8")
    assert "available; installation not confirmed" in context.render("chat-1")
    router.catalog.confirmed = snapshot_module.SKILL_VERSION
    assert "owner confirmed" in context.render("chat-1")


def test_catalog_context_reads_agents_through_workspace_guard(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "AGENTS.md").write_text("Safe project instructions", encoding="utf-8")
    catalog = Catalog(tmp_path / "catalog")
    try:
        project = catalog.create_project("Fixture", project_root)
        conversation = catalog.create_conversation("Fixture chat", project["id"])
        assert catalog.context(conversation["id"])["project_agents_md"] == "Safe project instructions"

        original = Workspace._validate_operation_path

        def deny_private(self, path, *, write=False):
            if str(path) == "AGENTS.md":
                raise ValueError("private target was C:\\secret\\AGENTS.md")
            return original(self, path, write=write)

        monkeypatch.setattr(Workspace, "_validate_operation_path", deny_private)
        result = catalog.context(conversation["id"])
        assert "unavailable" in result["project_agents_md"]
        assert "C:\\secret" not in result["project_agents_md"]
    finally:
        catalog.close()
