import ctypes
import os
from pathlib import Path
import sys

import pytest

from musebridge import desktop_launch as launch_module


AS_INVOKER = b'''<?xml version="1.0" encoding="UTF-8"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3"><security><requestedPrivileges>
    <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
  </requestedPrivileges></security></trustInfo>
</assembly>'''


def test_manifest_parser_accepts_only_explicit_as_invoker():
    assert launch_module._parse_manifest_level(AS_INVOKER) == "asInvoker"
    for level in (b"highestAvailable", b"requireAdministrator", b"unknown"):
        data = AS_INVOKER.replace(b"asInvoker", level)
        with pytest.raises(ValueError, match="elevation|unknown"):
            launch_module._parse_manifest_level(data)
    ui_access = AS_INVOKER.replace(b'uiAccess="false"', b'uiAccess="true"')
    with pytest.raises(ValueError, match="elevation"):
        launch_module._parse_manifest_level(ui_access)
    bad_ui_access = AS_INVOKER.replace(b'uiAccess="false"', b'uiAccess="maybe"')
    with pytest.raises(ValueError, match="elevation"):
        launch_module._parse_manifest_level(bad_ui_access)


@pytest.mark.skipif(os.name != "nt", reason="Windows resource API only")
def test_real_current_python_manifest_uses_pointer_safe_resource_loading():
    data = launch_module._manifest_bytes(Path(sys.executable))
    assert launch_module._parse_manifest_level(data) == "asInvoker"


@pytest.mark.parametrize("data", [b"", b"not xml", b"<!DOCTYPE a><a/>", b"<!ENTITY x 'x'><a/>"])
def test_manifest_parser_fails_closed_on_missing_malformed_or_declarations(data):
    with pytest.raises(ValueError):
        launch_module._parse_manifest_level(data)


def test_shell_execute_info_targets_secondary_monitor_without_activation(tmp_path):
    executable = tmp_path / "sample.exe"
    info = launch_module._build_shell_execute_info(executable, ["hello world", "--mode"], 0x12345)
    assert info.cbSize == ctypes.sizeof(info)
    expected_flags = (launch_module.SEE_MASK_NOCLOSEPROCESS | launch_module.SEE_MASK_NOASYNC
                      | launch_module.SEE_MASK_FLAG_NO_UI | launch_module.SEE_MASK_HMONITOR)
    assert info.fMask == expected_flags
    assert info.hwnd is None
    assert info.lpVerb == "open"
    assert info.lpFile == str(executable)
    assert info.lpDirectory == str(tmp_path)
    assert info.lpParameters == '"hello world" --mode'
    assert info.nShow == launch_module.SW_SHOWNOACTIVATE
    assert info.hIcon == 0x12345


@pytest.mark.parametrize("args", ["not-a-list", ["bad\0arg"], ["x" * 2049], ["x"] * 65])
def test_argument_validation_is_bounded(args):
    with pytest.raises(ValueError):
        launch_module._validated_args(args)


def _setup_launch(monkeypatch, tmp_path):
    executable = tmp_path / "fixture.exe"
    executable.write_bytes(b"fixture")
    monkeypatch.setattr(launch_module, "_resolve_executable", lambda _value: executable)
    monkeypatch.setattr(launch_module, "_manifest_execution_level", lambda _path: "asInvoker")
    monkeypatch.setattr(launch_module, "_has_compat_elevation", lambda _path: False)
    monkeypatch.setattr(launch_module, "_secondary_monitor", lambda: 0xBEEF)
    monkeypatch.setattr(launch_module.desktop, "_session_state", lambda: (7, True))
    monkeypatch.setattr(launch_module.desktop, "_idle_ms", lambda: 5000)
    foreground_values = iter([111, 111])
    monkeypatch.setattr(launch_module.desktop, "_foreground_hwnd", lambda: next(foreground_values))
    return executable


def test_launch_uses_guard_secondary_monitor_and_only_reports_new_pid(monkeypatch, tmp_path):
    _setup_launch(monkeypatch, tmp_path)
    calls = []
    guard = lambda: calls.append("guard")
    native = []

    def shell(exe, args, monitor):
        native.append((exe, args, monitor))
        return 3456

    monkeypatch.setattr(launch_module, "_shell_execute", shell)
    monkeypatch.setattr(launch_module, "_windows_for_pid", lambda pid: [789] if pid == 3456 else [])
    result = launch_module.launch("fixture.exe", ["--test"], guard=guard)
    assert calls == ["guard", "guard"]
    assert native == [(tmp_path / "fixture.exe", ["--test"], 0xBEEF)]
    assert result == {"launched": True, "pid": 3456, "target_monitor": "secondary",
                      "activation_requested": False,
                      "hint": "Secondary-monitor and no-activation launch hints were requested; the application may override them.",
                      "window_handle": 789}


@pytest.mark.parametrize(
    "patches, message",
    [
        (("session", lambda: (0, False)), "unlocked interactive"),
        (("idle", lambda: 0), "activity"),
        (("monitor", lambda: _raise(ValueError("No secondary monitor is available"))), "secondary monitor"),
        (("elevation", lambda _path: True), "Elevated applications"),
    ],
)
def test_preflight_refusals_never_call_native_launcher(monkeypatch, tmp_path, patches, message):
    _setup_launch(monkeypatch, tmp_path)
    field, replacement = patches
    if field == "session":
        monkeypatch.setattr(launch_module.desktop, "_session_state", replacement)
    elif field == "idle":
        monkeypatch.setattr(launch_module.desktop, "_idle_ms", replacement)
    elif field == "monitor":
        monkeypatch.setattr(launch_module, "_secondary_monitor", replacement)
    else:
        monkeypatch.setattr(launch_module, "_has_compat_elevation", replacement)
    monkeypatch.setattr(launch_module, "_shell_execute", lambda *_args: pytest.fail("must not launch"))
    with pytest.raises(ValueError, match=message):
        launch_module.launch("fixture.exe", guard=lambda: None)


def _raise(error):
    raise error


def test_guard_revoked_before_effect_stops_launch(monkeypatch, tmp_path):
    _setup_launch(monkeypatch, tmp_path)
    count = 0

    def guard():
        nonlocal count
        count += 1
        if count == 2:
            raise ValueError("Admission revoked by Stop Work")

    monkeypatch.setattr(launch_module, "_shell_execute", lambda *_args: pytest.fail("must not launch"))
    with pytest.raises(ValueError, match="revoked"):
        launch_module.launch("fixture.exe", guard=guard)
    assert count == 2


def test_foreground_change_immediately_before_effect_stops_launch(monkeypatch, tmp_path):
    _setup_launch(monkeypatch, tmp_path)
    values = iter([111, 222])
    monkeypatch.setattr(launch_module.desktop, "_foreground_hwnd", lambda: next(values))
    monkeypatch.setattr(launch_module, "_shell_execute", lambda *_args: pytest.fail("must not launch"))
    with pytest.raises(ValueError, match="Foreground window changed"):
        launch_module.launch("fixture.exe", guard=lambda: None)


def test_user_compat_layer_runasadmin_refuses_before_native_call(monkeypatch, tmp_path):
    _setup_launch(monkeypatch, tmp_path)
    monkeypatch.setattr(launch_module, "_has_compat_elevation", lambda _path: True)
    monkeypatch.setattr(launch_module, "_shell_execute", lambda *_args: pytest.fail("must not launch"))
    with pytest.raises(ValueError, match="Elevated applications"):
        launch_module.launch("fixture.exe", guard=lambda: None)
