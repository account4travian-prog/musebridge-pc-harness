"""Launcher registration tests use only temporary directories and COM shortcut files."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "Install-MuseBridge-Shortcuts.ps1"
STARTER = ROOT / "Start-MuseBridge.ps1"


def _powershell():
    return shutil.which("pwsh") or shutil.which("powershell")


def _run_ps(script, *arguments):
    shell = _powershell()
    if not shell:
        pytest.skip("PowerShell is not installed")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script), *map(str, arguments)],
        capture_output=True, text=True, timeout=20, creationflags=flags, check=False,
    )


def _fixture(tmp_path):
    root = tmp_path / "MuseBridge Project"
    pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
    pythonw.parent.mkdir(parents=True)
    pythonw.write_bytes(b"temporary test stub")
    icon = root / "musebridge" / "assets" / "musebridge.ico"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"temporary icon stub")
    desktop = tmp_path / "Owned Desktop"
    programs = tmp_path / "Owned Programs"
    return root, pythonw, icon, desktop, programs


def _install(tmp_path, root, desktop, programs):
    return _run_ps(INSTALLER, "-ProjectRoot", root,
                   "-DesktopDirectory", desktop, "-ProgramsDirectory", programs)


def test_installer_creates_and_reads_back_owned_shortcuts(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows Shell shortcut COM is Windows-only")
    root, pythonw, icon, desktop, programs = _fixture(tmp_path)
    result = _install(tmp_path, root, desktop, programs)
    assert result.returncode == 0, result.stderr

    expected_arguments = "-m musebridge.app --secondary-screen --connect --activate-existing"
    expected_links = [
        desktop / "MuseBridge Harness.lnk",
        programs / "MuseBridge" / "MuseBridge Harness.lnk",
    ]
    age_shortcut = tmp_path / "age_owned.ps1"
    age_shortcut.write_text(
        "param([string]$Path)\n"
        "$s = New-Object -ComObject WScript.Shell\n$x = $s.CreateShortcut($Path)\n"
        "$x.Arguments = '-m musebridge.app'\n$x.Save()\n",
        encoding="utf-8",
    )
    aged = _run_ps(age_shortcut, "-Path", expected_links[0])
    assert aged.returncode == 0, aged.stderr
    updated = _install(tmp_path, root, desktop, programs)
    assert updated.returncode == 0, updated.stderr

    readback = tmp_path / "readback.ps1"
    readback.write_text(
        "param([string]$Path)\n"
        "$s = New-Object -ComObject WScript.Shell\n"
        "$x = $s.CreateShortcut($Path)\n"
        "[pscustomobject]@{ Target=$x.TargetPath; Arguments=$x.Arguments; "
        "WorkingDirectory=$x.WorkingDirectory; Icon=$x.IconLocation } | ConvertTo-Json -Compress\n",
        encoding="utf-8",
    )
    for link in expected_links:
        assert link.is_file()
        checked = _run_ps(readback, "-Path", link)
        assert checked.returncode == 0, checked.stderr
        values = json.loads(checked.stdout)
        assert Path(values["Target"]) == pythonw
        assert values["Arguments"] == expected_arguments
        assert Path(values["WorkingDirectory"]) == root
        assert values["Icon"] == f"{icon},0"


def test_installer_refuses_foreign_shortcut_without_changing_it(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows Shell shortcut COM is Windows-only")
    root, pythonw, _icon, desktop, programs = _fixture(tmp_path)
    desktop.mkdir()
    link = desktop / "MuseBridge Harness.lnk"
    create_foreign = tmp_path / "create_foreign.ps1"
    create_foreign.write_text(
        "param([string]$LinkPath,[string]$TargetPath)\n"
        "$s = New-Object -ComObject WScript.Shell\n"
        "$x = $s.CreateShortcut($LinkPath)\n"
        "$x.TargetPath = $TargetPath\n$x.Arguments = '--keep-foreign'\n$x.Save()\n",
        encoding="utf-8",
    )
    created = _run_ps(create_foreign, "-LinkPath", link, "-TargetPath", pythonw)
    assert created.returncode == 0, created.stderr

    result = _install(tmp_path, root, desktop, programs)
    assert result.returncode != 0
    assert "Refusing to overwrite" in result.stderr

    readback = tmp_path / "read_foreign.ps1"
    readback.write_text(
        "param([string]$Path)\n"
        "$s = New-Object -ComObject WScript.Shell\n$x = $s.CreateShortcut($Path)\n"
        "[pscustomobject]@{ Target=$x.TargetPath; Arguments=$x.Arguments } | ConvertTo-Json -Compress\n",
        encoding="utf-8",
    )
    checked = _run_ps(readback, "-Path", link)
    assert checked.returncode == 0, checked.stderr
    values = json.loads(checked.stdout)
    assert Path(values["Target"]) == pythonw
    assert values["Arguments"] == "--keep-foreign"


def test_manual_launcher_keeps_connect_opt_in_and_secondary_default():
    source = STARTER.read_text(encoding="utf-8")
    assert "[switch]$Connect" in source
    assert "if ($Connect)" in source
    assert "'--secondary-screen'" in source
    assert "-WindowStyle Hidden" in source
    assert "--connect" not in source.split("if ($Connect)", 1)[0]
