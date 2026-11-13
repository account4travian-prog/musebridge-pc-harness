from musebridge.catalog import Catalog
from musebridge.execution import Execution
from musebridge.hooks import HookService
from musebridge.jobs import JobManager
from musebridge.tooling import ToolRouter
from musebridge.edit_checks import EditChecks, HOOK_TIMEOUT_S, HOOK_NAME


def _setup(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    catalog = Catalog(tmp_path / "state")
    project = catalog.create_project("Settings", root)
    cid = catalog.create_conversation("Settings chat", project["id"])["id"]
    router = ToolRouter(catalog, tmp_path / "state")
    return root, catalog, router, project, cid, EditChecks(router)


def test_settings_are_project_scoped_owner_preferences_and_off_by_default(tmp_path):
    _, catalog, router, project, cid, checks = _setup(tmp_path)
    try:
        second = catalog.create_conversation("Sibling", project["id"])["id"]
        other_project = catalog.create_project("Other", project["root"])
        other = catalog.create_conversation("Other chat", other_project["id"])["id"]
        assert checks.configuration(cid) == {
            "enabled": False, "configured": False, "hook_id": None,
            "command": "", "path_filter": "",
        }
        saved = checks.configure(cid, "Write-Output 'ok'", "src/**/*.py", enabled=True)
        assert saved["enabled"] is True
        assert saved["path_filter"] == "src/**/*.py"
        assert saved["hook_id"] == catalog.preference("edit_check_hook:" + project["id"])
        assert checks.configuration(second) == saved
        assert not checks.configuration(other)["configured"]
        hooks = router.hooks.list_hooks("project:" + project["id"])
        hook = next(row for row in hooks if row["hook_id"] == saved["hook_id"])
        assert hook["timeout_s"] == HOOK_TIMEOUT_S
        assert hook["name"] == HOOK_NAME
        assert hook["enabled"] is True
        disabled = checks.configure(cid, saved["command"], saved["path_filter"], enabled=False)
        assert disabled["enabled"] is False
    finally:
        router.close()
        catalog.close()


def test_settings_validation_does_not_enable_invalid_or_unscoped_checks(tmp_path):
    _, catalog, router, project, cid, checks = _setup(tmp_path)
    try:
        with pytest.raises(ValueError, match="command"):
            checks.configure(cid, "", "**/*.py", enabled=True)
        with pytest.raises(ValueError, match="project-relative glob"):
            checks.configure(cid, "Write-Output x", "../outside.py", enabled=True)
        main = catalog.create_conversation("No project")["id"]
        with pytest.raises(ValueError, match="Choose a project"):
            checks.configure(main, "Write-Output x", enabled=True)
        assert catalog.preference("edit_check_hook:" + project["id"]) is None
    finally:
        router.close()
        catalog.close()
import pytest

