"""Fail-closed, nonactivating Windows application launch for desktop tools."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

from . import desktop


MAX_ARGS = 64
MAX_ARG_CHARS = 2048
MAX_ARGS_CHARS = 8192
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_NOASYNC = 0x00000100
SEE_MASK_FLAG_NO_UI = 0x00000400
SEE_MASK_HMONITOR = 0x00200000
SW_SHOWNOACTIVATE = 4


class _SHELLEXECUTEINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("fMask", ctypes.c_uint32),
        ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p),
        ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int32),
        ("hInstApp", ctypes.c_void_p),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p),
        ("hkeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_uint32),
        # hIcon and hMonitor share a union in SHELLEXECUTEINFO.
        ("hIcon", ctypes.c_void_p),
        ("hProcess", ctypes.c_void_p),
    ]


def _resolve_executable(app_or_path: str) -> Path:
    if not isinstance(app_or_path, str) or not app_or_path or "\0" in app_or_path:
        raise ValueError("Choose an executable by name or absolute path.")
    candidate = Path(app_or_path).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve(strict=True)
    else:
        found = shutil.which(app_or_path)
        if not found:
            raise ValueError("Executable was not found on PATH; provide an absolute .exe path.")
        resolved = Path(found).resolve(strict=True)
    if resolved.suffix.casefold() != ".exe" or not resolved.is_file():
        raise ValueError("Desktop launch accepts an existing .exe file only.")
    return resolved


def _validated_args(args) -> list[str]:
    if not isinstance(args, (list, tuple)) or len(args) > MAX_ARGS:
        raise ValueError(f"args must be a list of at most {MAX_ARGS} strings.")
    clean = []
    for item in args:
        if not isinstance(item, str) or "\0" in item or len(item) > MAX_ARG_CHARS:
            raise ValueError("Each launch argument must be bounded text without NUL characters.")
        clean.append(item)
    if sum(len(item) + 1 for item in clean) > MAX_ARGS_CHARS:
        raise ValueError("Combined launch arguments exceed the safe size limit.")
    return clean


def _parse_manifest_level(data: bytes) -> str:
    if not isinstance(data, bytes) or not data or len(data) > 1_000_000:
        raise ValueError("Executable manifest is missing or exceeds the safe inspection limit; launch refused.")
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("Executable manifest uses unsupported declarations; launch refused.")
    try:
        root = ET.fromstring(data)
    except (ET.ParseError, UnicodeError) as exc:
        raise ValueError("Executable manifest could not be safely inspected; launch refused.") from exc
    nodes = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "requestedExecutionLevel"]
    if len(nodes) != 1:
        raise ValueError("Executable has no unambiguous requestedExecutionLevel manifest; launch refused.")
    level = nodes[0].attrib.get("level")
    ui_access = nodes[0].attrib.get("uiAccess", "false").strip().casefold()
    if level != "asInvoker" or ui_access not in ("false", "0"):
        raise ValueError("Executable may request elevation or has an unknown execution level; choose an asInvoker app.")
    return level


def _manifest_bytes(executable: Path) -> bytes:
    """Load RT_MANIFEST resource 1 as data, never execute or map the target image."""
    if os.name != "nt":
        raise OSError("Desktop launch is available only on Windows.")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    load = kernel32.LoadLibraryExW
    load.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint]
    load.restype = ctypes.c_void_p
    module = load(str(executable), None, 0x00000002 | 0x00000020)  # AS_DATAFILE | AS_IMAGE_RESOURCE
    if not module:
        raise ValueError("Could not inspect the executable manifest; launch refused.")
    try:
        find = kernel32.FindResourceW
        find.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        find.restype = ctypes.c_void_p
        resource = find(module, ctypes.c_void_p(1), ctypes.c_void_p(24))  # resource 1, RT_MANIFEST
        if not resource:
            raise ValueError("Executable has no inspectable manifest; launch refused.")
        sizeof_resource = kernel32.SizeofResource
        sizeof_resource.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        sizeof_resource.restype = ctypes.c_uint32
        size = int(sizeof_resource(module, resource))
        if size <= 0 or size > 1_000_000:
            raise ValueError("Executable manifest size is invalid; launch refused.")
        load_resource = kernel32.LoadResource
        load_resource.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        load_resource.restype = ctypes.c_void_p
        loaded = load_resource(module, resource)
        lock_resource = kernel32.LockResource
        lock_resource.argtypes = [ctypes.c_void_p]
        lock_resource.restype = ctypes.c_void_p
        pointer = lock_resource(loaded)
        if not pointer:
            raise ValueError("Could not read executable manifest; launch refused.")
        return ctypes.string_at(pointer, size)
    finally:
        free_library = kernel32.FreeLibrary
        free_library.argtypes = [ctypes.c_void_p]
        free_library.restype = ctypes.c_int
        free_library(module)


def _manifest_execution_level(executable: Path) -> str:
    return _parse_manifest_level(_manifest_bytes(executable))


def _has_compat_elevation(executable: Path) -> bool:
    if os.name != "nt":
        raise OSError("Desktop launch is available only on Windows.")
    import winreg

    key_path = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ) as key:
                value, _kind = winreg.QueryValueEx(key, str(executable))
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ValueError("Could not check AppCompat elevation settings; launch refused.") from exc
        if isinstance(value, str) and "RUNASADMIN" in value.upper():
            return True
    return False


def _secondary_monitor():
    if os.name != "nt":
        raise OSError("Desktop launch is available only on Windows.")
    import win32api
    import win32con

    for handle, _dc, _rect in win32api.EnumDisplayMonitors(None, None):
        info = win32api.GetMonitorInfo(handle)
        if not info.get("Flags", 0) & win32con.MONITORINFOF_PRIMARY:
            return int(handle)
    raise ValueError("No secondary monitor is available; desktop launch was not started.")


def _build_shell_execute_info(executable: Path, args: list[str], monitor: int):
    info = _SHELLEXECUTEINFO()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC | SEE_MASK_FLAG_NO_UI | SEE_MASK_HMONITOR
    info.hwnd = None
    info.lpVerb = "open"
    info.lpFile = str(executable)
    info.lpParameters = subprocess.list2cmdline(args) if args else None
    info.lpDirectory = str(executable.parent)
    info.nShow = SW_SHOWNOACTIVATE
    info.hIcon = ctypes.c_void_p(monitor).value
    return info


def _shell_execute(executable: Path, args: list[str], monitor: int) -> int:
    if os.name != "nt":
        raise OSError("Desktop launch is available only on Windows.")
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    info = _build_shell_execute_info(executable, args, monitor)
    execute = shell32.ShellExecuteExW
    execute.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFO)]
    execute.restype = ctypes.c_int
    if not execute(ctypes.byref(info)):
        error = ctypes.get_last_error()
        raise OSError(error, "Windows refused to launch the application.")
    if not info.hProcess:
        raise OSError("Windows returned no process handle for the launched application.")
    try:
        get_process_id = kernel32.GetProcessId
        get_process_id.argtypes = [ctypes.c_void_p]
        get_process_id.restype = ctypes.c_uint32
        pid = int(get_process_id(info.hProcess))
        if not pid:
            raise OSError("Could not identify the launched process.")
        return pid
    finally:
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        close_handle(ctypes.c_void_p(info.hProcess))


def _windows_for_pid(pid: int) -> list[int]:
    if os.name != "nt":
        return []
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    results: list[int] = []
    visited = 0
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

    def visit(hwnd, _lparam):
        nonlocal visited
        visited += 1
        process = ctypes.c_uint32()
        user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        user32.GetWindowThreadProcessId.restype = ctypes.c_uint32
        user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        user32.IsWindowVisible.restype = ctypes.c_int
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process))
        if process.value == pid and user32.IsWindowVisible(hwnd):
            results.append(int(hwnd))
        return len(results) < 20 and visited < 5000

    callback = callback_type(visit)
    user32.EnumWindows.argtypes = [callback_type, ctypes.c_void_p]
    user32.EnumWindows.restype = ctypes.c_int
    user32.EnumWindows(callback, None)
    return results


def launch(app_or_path: str, args=(), *, guard) -> dict:
    """Launch one non-elevated EXE without activating it, targeting a secondary monitor."""
    if not callable(guard):
        raise ValueError("An execution guard is required for desktop launch.")
    guard()
    session, unlocked = desktop._session_state()
    if session == 0 or not unlocked:
        raise ValueError("Desktop launch requires the unlocked interactive Windows session.")
    if desktop._idle_ms() < desktop.MIN_IDLE_MS:
        raise ValueError("Recent user activity was detected; wait for the desktop to be idle before launching.")
    foreground = desktop._foreground_hwnd()
    executable = _resolve_executable(app_or_path)
    clean_args = _validated_args(args)
    level = _manifest_execution_level(executable)
    if level != "asInvoker" or _has_compat_elevation(executable):
        raise ValueError("Elevated applications are not launched by desktop tools; choose a non-elevated app.")
    monitor = _secondary_monitor()

    # Repeat all revocable gates directly before the operating-system launch.
    guard()
    current_session, current_unlocked = desktop._session_state()
    if current_session != session or not current_unlocked:
        raise ValueError("Interactive session changed or locked before launch; no app was started.")
    if desktop._idle_ms() < desktop.MIN_IDLE_MS:
        raise ValueError("User activity resumed before launch; no app was started.")
    if desktop._foreground_hwnd() != foreground:
        raise ValueError("Foreground window changed before launch; no app was started.")

    pid = _shell_execute(executable, clean_args, monitor)
    deadline = time.monotonic() + 1.0
    handles = []
    while not handles and time.monotonic() < deadline:
        handles = _windows_for_pid(pid)
        if not handles:
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    result = {"launched": True, "pid": pid, "target_monitor": "secondary",
              "activation_requested": False,
              "hint": "Secondary-monitor and no-activation launch hints were requested; the application may override them."}
    if handles:
        result["window_handle"] = handles[0]
    return result
