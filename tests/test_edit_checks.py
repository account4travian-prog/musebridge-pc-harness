from musebridge.catalog import Catalog
from musebridge.edit_checks import EditChecks
from musebridge.tooling import ToolRouter


def _setup(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    catalog = Catalog(tmp_path / "state")
    project = catalog.create_project("Checks", root)
    cid = catalog.create_conversation("Check chat", project["id"])["id"]
    router = ToolRouter(catalog, tmp_path / "state")
    return root, catalog, router, cid, EditChecks(router)


def _admit(router, cid, edit_id, path):
    return router.hooks.run_event(cid, "after_file_edit", edit_id, path=path)


def test_matching_after_edit_check_waits_for_existing_hook_job_once(tmp_path):
    root, catalog, router, cid, checks = _setup(tmp_path)
    marker = root / "check-ran.txt"
    command = f"[IO.File]::WriteAllText('{str(marker)}','yes')"
    try:
        config = checks.configure(cid, command, "src/**/*.py", enabled=True)
        invocation = _admit(router, cid, "edit-1", "src/main.py")
        result = checks.after_edit(cid, "src/main.py", "edit-1", invocation, lambda: None)
        assert result["passed"] is True
        assert result["state"] == "succeeded"
        assert marker.read_text() == "yes"

        repeated = _admit(router, cid, "edit-1", "src/main.py")
        assert repeated["jobs"][0]["duplicate"] is True
        same = checks.after_edit(cid, "src/main.py", "edit-1", repeated, lambda: None)
        assert same["job_id"] == result["job_id"]
        assert router.jobs._db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    finally:
        router.close()
        catalog.close()


def test_nonmatching_file_and_disabled_check_do_not_wait_or_launch(tmp_path):
    _, catalog, router, cid, checks = _setup(tmp_path)
    try:
        checks.configure(cid, "Write-Output 'should-not-run'", "src/**/*.py", enabled=True)
        miss = _admit(router, cid, "edit-doc", "docs/readme.md")
        assert miss["jobs"] == []
        assert checks.after_edit(cid, "docs/readme.md", "edit-doc", miss, lambda: None) is None
        assert router.jobs._db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0

        checks.configure(cid, "Write-Output 'disabled'", "**/*.py", enabled=False)
        disabled = _admit(router, cid, "edit-disabled", "src/main.py")
        assert disabled["jobs"] == []
        assert checks.after_edit(cid, "src/main.py", "edit-disabled", disabled, lambda: None) is None
    finally:
        router.close()
        catalog.close()


def test_failed_check_returns_warning_tail_and_does_not_reject_saved_edit(tmp_path):
    _, catalog, router, cid, checks = _setup(tmp_path)
    try:
        lines = "; ".join(f"Write-Output 'line-{index}'" for index in range(25))
        checks.configure(cid, lines + "; exit 3", "*.py", enabled=True)
        invocation = _admit(router, cid, "edit-failed-check", "main.py")
        result = checks.after_edit(cid, "main.py", "edit-failed-check", invocation, lambda: None)
        assert result["passed"] is False
        assert result["state"] == "failed"
        assert "saved" in result["warning"]
        assert "line-24" in result["output_tail"]
        assert "line-0" not in result["output_tail"]
        assert len(result["output_tail"].splitlines()) <= 20
    finally:
        router.close()
        catalog.close()


def test_pause_read_only_and_revoked_guard_report_warning_without_raising(tmp_path):
    _, catalog, router, cid, checks = _setup(tmp_path)
    try:
        checks.configure(cid, "Write-Output 'should-not-run'", "*.py", enabled=True)
        router.execution.pause(True)
        paused = _admit(router, cid, "edit-paused", "main.py")
        result = checks.after_edit(cid, "main.py", "edit-paused", paused, lambda: None)
        assert result["state"] == "skipped"
        assert result["passed"] is False
        assert "saved" in result["warning"]
        router.execution.pause(False)

        router.execution.set_profile(cid, "read_only")
        readonly = _admit(router, cid, "edit-readonly", "main.py")
        result = checks.after_edit(cid, "main.py", "edit-readonly", readonly, lambda: None)
        assert result["state"] == "skipped"
        assert "saved" in result["warning"]
        router.execution.set_profile(cid, "project")

        admitted = _admit(router, cid, "edit-revoked", "main.py")

        def revoke():
            raise RuntimeError("Stop Work")

        result = checks.after_edit(cid, "main.py", "edit-revoked", admitted, revoke)
        assert result["state"] == "interrupted"
        assert "saved" in result["warning"]
    finally:
        router.close()
        catalog.close()


def test_after_edit_rejects_unowned_invocation_receipt(tmp_path):
    _, catalog, router, cid, checks = _setup(tmp_path)
    try:
        checks.configure(cid, "Write-Output 'x'", "*.py", enabled=True)
        forged = {"jobs": [{"hook_id": checks.configuration(cid)["hook_id"],
                            "invocation_id": "missing", "job_id": "missing"}]}
        result = checks.after_edit(cid, "main.py", "edit-forged", forged, lambda: None)
        assert result["state"] == "unavailable"
        assert result["passed"] is False
    finally:
        router.close()
        catalog.close()


def test_configuration_read_error_after_publication_becomes_warning(tmp_path, monkeypatch):
    _, catalog, router, cid, checks = _setup(tmp_path)
    try:
        monkeypatch.setattr(checks, "configuration", lambda _cid: (_ for _ in ()).throw(OSError("store unavailable")))
        result = checks.after_edit(cid, "main.py", "edit-config-error", {}, lambda: None)
        assert result["state"] == "unavailable"
        assert result["passed"] is False
        assert "saved" in result["warning"]
    finally:
        router.close()
        catalog.close()


def test_glob_timeout_is_visible_after_saved_edit(tmp_path):
    _, catalog, router, cid, checks = _setup(tmp_path)
    try:
        hostile_glob = "a*" * 36 + "b"
        checks.configure(cid, "Write-Output 'must-not-run'", hostile_glob, enabled=True)
        path = "a" * 200 + "c"
        invocation = _admit(router, cid, "edit-glob-timeout", path)
        assert any(item.get("state") == "path_filter_timeout" for item in invocation["skipped"])
        result = checks.after_edit(cid, path, "edit-glob-timeout", invocation, lambda: None)
        assert result["state"] == "skipped"
        assert result["passed"] is False
        assert "simplify the glob" in result["warning"]
        assert "saved" in result["warning"]
        assert router.jobs._db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        router.close()
        catalog.close()
