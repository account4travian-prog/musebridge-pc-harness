"""Guarded Windows UI Automation tools backed by pywinauto's UIA backend."""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

MAX_WINDOWS = 100
MAX_TREE_NODES = 150
MAX_TREE_DEPTH = 8
MAX_CONTROL_TEXT = 256
MAX_ACTION_TEXT = 10_000
MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024
MAX_SCREENSHOT_DIMENSION = 20_000
MAX_SCREENSHOT_PIXELS = 16_000_000
PW_RENDERFULLCONTENT = 0x00000002
WINDOW_CAPTURE_TIMEOUT = 8
MIN_IDLE_MS = 2_000


class DesktopActionRefused(ValueError):
    no_effect=True
    code='desktop_unavailable'
    hint=('Leave the desktop idle and ask the owner to bring the permitted non-elevated window forward. '
          'Stop desktop work while the gate is blocked; do not bypass it with shell, scripts or other tools.')


def _param(description: str, kind: str = "string") -> dict:
    return {"type": kind, "description": description}


CID = _param("Exact conversation_id from the MuseBridge context. Required for every desktop tool.")
HANDLE = _param("Top-level Windows window handle returned by desktop.windows.", "integer")
_SPECS = {
    "desktop.windows": {
        "description": "List bounded metadata for visible top-level windows in the unlocked interactive session.",
        "required": {"conversation_id": CID}, "optional": {},
    },
    "desktop.observe": {
        "description": "Read a bounded UI Automation tree for one window. Select returned controls by their selector or automation ID.",
        "required": {"conversation_id": CID, "window_handle": HANDLE}, "optional": {},
    },
    "desktop.act": {
        "description": "Perform a guarded UIA focus, invoke, or text-setting action on one uniquely identified control.",
        "required": {"conversation_id": CID, "window_handle": HANDLE, "action": _param("One of focus, invoke, set_text.")},
        "optional": {"selector": _param("Exact selector path returned by desktop.observe."),
                     "automation_id": _param("Exact UIA automation ID."),
                     "control_type": _param("Exact UIA control type."),
                     "name": _param("Exact UIA control name."),
                     "text": _param("Text to set, up to 10000 characters.")},
    },
    "desktop.screenshot": {
        "description": "Save a bounded screenshot of one top-level window and return its artifact path.",
        "required": {"conversation_id": CID, "window_handle": HANDLE}, "optional": {},
    },
}


_SPECS['desktop.snapshot']={'description':'Capture UIA controls with stable runtime references and OCR fallback.',
    'required':{'conversation_id':CID,'window_handle':HANDLE},'optional':{'filter':_param('Text or role filter.')}}


@contextlib.contextmanager
def _com_initialized():
    """Initialize COM on the calling thread for the duration of each UIA call."""
    try:
        import pythoncom
    except ImportError as exc:
        raise OSError("pywin32 COM support is required for UI Automation") from exc
    pythoncom.CoInitialize()
    try:
        yield
    finally:
        pythoncom.CoUninitialize()


def _desktop():
    try:
        from pywinauto import Desktop
    except ImportError as exc:
        raise OSError("pywinauto is not installed in this Python environment") from exc
    return Desktop(backend="uia")


@contextlib.contextmanager
def _physical_coordinates():
    """Keep UIA rectangles and synthesized input in physical pixels on mixed DPI screens."""
    setter=None
    previous=None
    if os.name=='nt':
        try:
            setter=ctypes.WinDLL('user32').SetThreadDpiAwarenessContext
            setter.argtypes=[ctypes.c_void_p]
            setter.restype=ctypes.c_void_p
            previous=setter(ctypes.c_void_p(-4))
        except (AttributeError,OSError):setter=None
    try:yield
    finally:
        if setter and previous:setter(previous)


def _fullscreen_foreground(hwnd):
    """Refuse taking focus away from a fullscreen foreground app, including games."""
    if os.name!='nt' or not hwnd:return False
    from ctypes import wintypes
    class Rect(ctypes.Structure):
        _fields_=[(name,wintypes.LONG) for name in ('left','top','right','bottom')]
    class Monitor(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('monitor',Rect),('work',Rect),('flags',wintypes.DWORD)]
    user=ctypes.WinDLL('user32')
    user.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(Rect)]
    user.MonitorFromWindow.argtypes=[wintypes.HWND,wintypes.DWORD]
    user.MonitorFromWindow.restype=wintypes.HMONITOR
    user.GetMonitorInfoW.argtypes=[wintypes.HMONITOR,ctypes.POINTER(Monitor)]
    rect=Rect()
    if not user.GetWindowRect(hwnd,ctypes.byref(rect)):return False
    info=Monitor()
    info.size=ctypes.sizeof(info)
    if not user.GetMonitorInfoW(user.MonitorFromWindow(hwnd,2),ctypes.byref(info)):return False
    return all(abs(getattr(rect,name)-getattr(info.monitor,name))<=2 for name in ('left','top','right','bottom'))


def _input_desktop_name() -> str:
    """Return the current input desktop name (Default while unlocked)."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.OpenInputDesktop.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
    user32.OpenInputDesktop.restype = ctypes.c_void_p
    user32.GetUserObjectInformationW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                                ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]
    user32.GetUserObjectInformationW.restype = ctypes.c_bool
    user32.CloseDesktop.argtypes = [ctypes.c_void_p]
    user32.CloseDesktop.restype = ctypes.c_bool
    handle = user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
    if not handle:
        raise OSError(ctypes.get_last_error(), "cannot inspect the current input desktop")
    try:
        needed = ctypes.c_uint(0)
        user32.GetUserObjectInformationW(handle, 2, None, 0, ctypes.byref(needed))  # UOI_NAME
        buffer = ctypes.create_unicode_buffer(max(needed.value // ctypes.sizeof(ctypes.c_wchar), 64))
        if not user32.GetUserObjectInformationW(handle, 2, buffer, ctypes.sizeof(buffer), ctypes.byref(needed)):
            raise OSError(ctypes.get_last_error(), "cannot read current input desktop name")
        return buffer.value
    finally:
        user32.CloseDesktop(handle)


def _current_session_id() -> int:
    import win32ts
    return int(win32ts.ProcessIdToSessionId(os.getpid()))


def _session_state() -> tuple[int, bool]:
    """Return this process' session ID and whether its interactive desktop is unlocked."""
    import win32ts
    session_id = _current_session_id()
    if session_id == 0:
        return session_id, False
    try:
        active = any(record["SessionId"] == session_id and record["State"] == win32ts.WTSActive
                     for record in win32ts.WTSEnumerateSessions(None))
    except Exception as exc:
        raise OSError("cannot verify the interactive Windows session state") from exc
    return session_id, active and _input_desktop_name().casefold() == "default"


def _process_session_id(pid: int) -> int:
    import win32ts
    return int(win32ts.ProcessIdToSessionId(int(pid)))


def _integrity_level(pid: int) -> int:
    """Return the final RID from the process token's mandatory integrity SID."""
    import win32api
    import win32con
    import win32security
    process = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    try:
        token = win32security.OpenProcessToken(process, win32security.TOKEN_QUERY)
        try:
            sid, _attributes = win32security.GetTokenInformation(token, win32security.TokenIntegrityLevel)
            return int(sid.GetSubAuthority(sid.GetSubAuthorityCount() - 1))
        finally:
            token.Close()
    finally:
        process.Close()


def _idle_ms() -> int:
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        raise OSError(ctypes.get_last_error(), "cannot read last-input time")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    now_low = int(kernel32.GetTickCount64()) & 0xFFFFFFFF
    return (now_low - int(info.dwTime)) & 0xFFFFFFFF


def _foreground_hwnd() -> int:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    return int(user32.GetForegroundWindow() or 0)


def _window_pid(hwnd: int) -> int:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
    if not pid.value:
        raise ValueError("target window is no longer valid")
    return int(pid.value)


def _is_window(hwnd: int) -> bool:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    return bool(user32.IsWindow(ctypes.c_void_p(hwnd)))


def _capture_dimensions(width: int, height: int) -> int:
    """Validate target-window dimensions and return the bounded DIB byte size."""
    if (type(width) is not int or type(height) is not int or width <= 0 or height <= 0
            or width > MAX_SCREENSHOT_DIMENSION or height > MAX_SCREENSHOT_DIMENSION
            or width * height > MAX_SCREENSHOT_PIXELS):
        raise ValueError("selected window exceeds safe screenshot dimensions")
    return width * height * 4


def _render_window_printwindow(hwnd: int, expected_pid: int):
    """Native worker operation: render only this HWND into a bounded top-down DIB."""
    import ctypes
    from ctypes import wintypes
    from PIL import Image

    if os.name != "nt":
        raise OSError("selected-window capture requires Windows PrintWindow")

    class Rect(ctypes.Structure):
        _fields_ = [(name, wintypes.LONG) for name in ("left", "top", "right", "bottom")]

    class BitmapInfoHeader(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    class RGBQuad(ctypes.Structure):
        _fields_ = [("rgbBlue", ctypes.c_ubyte), ("rgbGreen", ctypes.c_ubyte),
                    ("rgbRed", ctypes.c_ubyte), ("rgbReserved", ctypes.c_ubyte)]

    class BitmapInfo(ctypes.Structure):
        _fields_ = [("bmiHeader", BitmapInfoHeader), ("bmiColors", RGBQuad * 1)]

    def validate_target():
        if not _is_window(hwnd):
            raise ValueError("target window closed while capturing screenshot")
        if _window_pid(hwnd) != expected_pid:
            raise ValueError("target window process changed while capturing screenshot")

    validate_target()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(Rect))
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetDC.argtypes = (wintypes.HWND,)
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    user32.ReleaseDC.restype = ctypes.c_int
    user32.PrintWindow.argtypes = (wintypes.HWND, wintypes.HDC, wintypes.UINT)
    user32.PrintWindow.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.DeleteDC.argtypes = (wintypes.HDC,)
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.CreateDIBSection.argtypes = (wintypes.HDC, ctypes.POINTER(BitmapInfo), wintypes.UINT,
                                       ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.GdiFlush.restype = wintypes.BOOL

    rect = Rect()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        raise OSError("could not read target window bounds")
    width, height = int(rect.right - rect.left), int(rect.bottom - rect.top)
    byte_count = _capture_dimensions(width, height)
    header = BitmapInfoHeader(ctypes.sizeof(BitmapInfoHeader), width, -height, 1, 32, 0,
                              byte_count, 0, 0, 0, 0)
    info = BitmapInfo(header, (RGBQuad * 1)())
    screen_dc = user32.GetDC(None)
    if not screen_dc:
        raise OSError("could not create a PrintWindow device context")
    memory_dc = bitmap = previous = None
    bits = ctypes.c_void_p()
    try:
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        if not memory_dc:
            raise OSError("could not create a PrintWindow memory context")
        bitmap = gdi32.CreateDIBSection(screen_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not bitmap or not bits.value:
            raise OSError("could not allocate the bounded PrintWindow bitmap")
        previous = gdi32.SelectObject(memory_dc, bitmap)
        if not previous or previous == ctypes.c_void_p(-1).value:
            previous = None
            raise OSError("could not select the PrintWindow bitmap")
        if not user32.PrintWindow(wintypes.HWND(hwnd), memory_dc, PW_RENDERFULLCONTENT):
            raise OSError("selected window could not render a private screenshot")
        if not gdi32.GdiFlush():
            raise OSError("selected-window screenshot rendering did not complete")
        raw = ctypes.string_at(bits, byte_count)
        image = Image.frombytes("RGB", (width, height), raw, "raw", "BGRX", 0, 1)
    finally:
        if memory_dc and previous:
            gdi32.SelectObject(memory_dc, previous)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, screen_dc)
    validate_target()
    if image.getbbox() is None:
        raise OSError("selected window rendered an all-black image; capture refused because content may be unavailable")
    return image


def _capture_window_printwindow(hwnd: int, expected_pid: int) -> bytes:
    """Run synchronous PrintWindow out of process with bounded time and output."""
    if not _is_window(hwnd) or _window_pid(hwnd) != expected_pid:
        raise ValueError("target window identity changed before screenshot")
    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                [sys.executable, "-m", "musebridge.window_capture_worker", str(hwnd), str(expected_pid)],
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                timeout=WINDOW_CAPTURE_TIMEOUT, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            size = output.tell()
            if size > MAX_SCREENSHOT_BYTES:
                raise OSError("selected-window screenshot exceeds the safe output limit")
            if result.returncode != 0 or size < 4:
                raise OSError("selected window could not render a private screenshot")
            output.seek(0)
            data = output.read(MAX_SCREENSHOT_BYTES + 1)
    except subprocess.TimeoutExpired:
        raise OSError("selected-window screenshot timed out; no screenshot was saved") from None
    if len(data) > MAX_SCREENSHOT_BYTES or not data.startswith(b"\xff\xd8\xff"):
        raise OSError("selected-window screenshot worker returned invalid image data")
    if not _is_window(hwnd) or _window_pid(hwnd) != expected_pid:
        raise ValueError("target window process changed while capturing screenshot")
    return data


class DesktopTools:
    """Narrow UIA adapter that refuses automation during user activity or elevation."""

    def __init__(self, artifact_dir: str | Path):
        self.artifact_dir = Path(artifact_dir).expanduser().resolve()
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self._closed = False
        from .desktop_refs import DesktopRefs
        self.refs=DesktopRefs(self)
        self.ocr_enabled=True

    @staticmethod
    def specs() -> dict:
        return {name: {"description": spec["description"],
                       "required": {key: dict(value) for key, value in spec["required"].items()},
                       "optional": {key: dict(value) for key, value in spec["optional"].items()}}
                for name, spec in _SPECS.items()}

    @staticmethod
    def _validate_cid(value: object) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise ValueError("conversation_id must be a non-empty string up to 256 characters")
        if any(ord(char) < 32 for char in value):
            raise ValueError("conversation_id cannot contain control characters")
        return value

    @staticmethod
    def _validate_handle(value: object) -> int:
        if isinstance(value, bool):
            raise ValueError("window_handle must be a positive integer")
        if isinstance(value, int):
            hwnd = value
        elif isinstance(value, str) and value.isdecimal():
            hwnd = int(value)
        else:
            raise ValueError("window_handle must be a positive integer")
        if hwnd <= 0:
            raise ValueError("window_handle must be a positive integer")
        return hwnd

    def _validate(self, name: str, params: dict) -> dict:
        if self._closed:
            raise OSError("desktop tools are closed")
        if name not in _SPECS:
            raise ValueError("unknown desktop tool")
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        missing = [key for key in _SPECS[name]["required"] if key not in params]
        if missing:
            raise ValueError("missing parameter: " + ", ".join(missing))
        clean = {"conversation_id": self._validate_cid(params["conversation_id"])}
        if "window_handle" in _SPECS[name]["required"]:
            clean["window_handle"] = self._validate_handle(params["window_handle"])
        if name == 'desktop.snapshot':
            clean['filter']=params.get('filter')
        if name == "desktop.act" and (params.get('ref') is not None or params.get('action') not in ('focus','invoke','set_text') or 'x' in params):
            action=params.get('action')
            if action not in ('click','double_click','right_click','type','keys','set_text','select','toggle','expand','scroll','focus','wait_for','invoke'):
                raise ValueError('action must be a supported desktop action')
            clean.update({key:params[key] for key in ('action','ref','selector','automation_id','control_type','name','text','keys','x','y','direction','value') if key in params})
            if 'text' in clean and (not isinstance(clean['text'],str) or len(clean['text'])>MAX_ACTION_TEXT):raise ValueError('text exceeds 10000 characters')
            if action in ('set_text','type') and 'text' not in clean:raise ValueError('text is required for this action')
            if 'x' in clean and (type(clean['x']) is not int or type(clean.get('y')) is not int):raise ValueError('x and y must be physical integer coordinates')
            clean['_v2']=True
        elif name == "desktop.act":
            action = params["action"]
            if action not in ("focus", "invoke", "set_text"):
                raise ValueError("action must be focus, invoke, or set_text")
            clean["action"] = action
            selector = params.get("selector")
            criteria = {key: params[key] for key in ("automation_id", "control_type", "name") if key in params}
            if selector is not None:
                if criteria:
                    raise ValueError("provide selector or control criteria, not both")
                if not isinstance(selector, str) or len(selector) > 100 or not selector:
                    raise ValueError("selector must be a non-empty selector path from desktop.observe")
                if not all(part.isdigit() for part in selector.split(".")):
                    raise ValueError("selector must be a selector path from desktop.observe")
                clean["selector"] = selector
            else:
                if not criteria:
                    raise ValueError("provide selector or at least one control criterion")
                for key, value in criteria.items():
                    if not isinstance(value, str) or not value or len(value) > MAX_CONTROL_TEXT:
                        raise ValueError(f"{key} must be a non-empty string up to {MAX_CONTROL_TEXT} characters")
                    clean[key] = value
            if action == "set_text":
                text = params.get("text")
                if not isinstance(text, str) or len(text) > MAX_ACTION_TEXT:
                    raise ValueError(f"set_text requires text up to {MAX_ACTION_TEXT} characters")
                clean["text"] = text
            elif "text" in params:
                raise ValueError("text is only valid with set_text")
        return clean

    def call(self, name: str, params: dict, *, guard=None) -> dict:
        clean = self._validate(name, params)
        if guard:clean['_guard']=guard
        try:
            with _com_initialized(),_physical_coordinates():
                return self._dispatch(name, clean)
        except (ValueError, OSError):
            raise
        except Exception as exc:
            detail = str(exc).replace("\x00", "")[:500] or type(exc).__name__
            raise OSError(f"desktop UIA operation failed: {detail}") from exc

    def _require_session(self, require_unlocked: bool = True) -> int:
        session_id, unlocked = _session_state()
        if session_id == 0:
            raise ValueError("desktop tools require an interactive user session")
        if require_unlocked and not unlocked:
            raise ValueError("interactive desktop is locked or on a secure desktop")
        return session_id

    def _resolve_window(self, hwnd: int):
        if not _is_window(hwnd):
            raise ValueError("target window is no longer valid")
        window = _desktop().window(handle=hwnd).wrapper_object()
        try:
            actual_handle = int(window.handle)
        except Exception:
            actual_handle = hwnd
        if actual_handle != hwnd:
            raise ValueError("UIA returned a different target window")
        return window

    @staticmethod
    def _short(value: object, limit: int = MAX_CONTROL_TEXT) -> str:
        try:
            text = str(value or "")
        except Exception:
            return ""
        return text[:limit]

    @staticmethod
    def _rect(wrapper) -> dict | None:
        try:
            rect = wrapper.rectangle()
            return {"left": int(rect.left), "top": int(rect.top),
                    "right": int(rect.right), "bottom": int(rect.bottom)}
        except Exception:
            return None

    def _window_rect(self, hwnd, window=None):
        """Physical outer bounds matching PrintWindow, rather than a UIA client rect."""
        if os.name=='nt':
            from ctypes import wintypes
            rect=wintypes.RECT()
            user=ctypes.WinDLL('user32',use_last_error=True)
            user.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
            user.GetWindowRect.restype=wintypes.BOOL
            if user.GetWindowRect(hwnd,ctypes.byref(rect)):
                return {key:int(getattr(rect,key)) for key in ('left','top','right','bottom')}
        return self._rect(window) if window is not None else None

    def _window_metadata(self, wrapper) -> dict | None:
        try:
            if not wrapper.is_visible():
                return None
            handle = int(wrapper.handle)
            pid = int(wrapper.process_id())
            return {"window_handle": handle, "process_id": pid,
                    "title": self._short(wrapper.window_text(), 300),
                    "class_name": self._short(wrapper.class_name(), 200),
                    "visible": True, "enabled": bool(wrapper.is_enabled()),
                    "focused":handle==_foreground_hwnd(),
                    "minimized":bool(getattr(wrapper,'is_minimized',lambda:False)()),
                    "rectangle": self._rect(wrapper)}
        except Exception:
            return None

    def _dispatch(self, name: str, params: dict) -> dict:
        self._require_session()
        if name == "desktop.windows":
            windows = []
            truncated = False
            candidates = _desktop().windows(top_level_only=True, visible_only=True)
            for scanned, wrapper in enumerate(candidates):
                if scanned >= 500:
                    truncated = True
                    break
                record = self._window_metadata(wrapper)
                if record is None:
                    continue
                if len(windows) >= MAX_WINDOWS:
                    truncated = True
                    break
                windows.append(record)
            result = {"windows": windows, "count": len(windows), "limit": MAX_WINDOWS, "truncated": truncated}
            return self._fit_result_list(result, "windows")

        hwnd = params["window_handle"]
        window = self._resolve_window(hwnd)
        pid = int(window.process_id())
        session_id = self._require_session()
        if _process_session_id(pid) != session_id:
            raise ValueError("target window is outside the current interactive session")

        if name == "desktop.observe":
            return self._observe(hwnd, window)
        if name == 'desktop.snapshot':
            return self.refs.snapshot(params['conversation_id'],hwnd,window,params.get('filter'))
        if name == "desktop.screenshot":
            return self._screenshot(params["conversation_id"], hwnd, pid)
        if params.get('_v2'):
            return self.refs.act(params['conversation_id'],hwnd,pid,window,params)
        if params.get('_guard'):params['_guard']()
        return self._act(hwnd, pid, window, params)

    @staticmethod
    def _foreground():
        return _foreground_hwnd()

    def _process_windows(self,pid):
        try:
            result=[]
            for wrapper in _desktop().windows(top_level_only=True,visible_only=True)[:100]:
                if int(wrapper.process_id())==pid:
                    record=self._window_metadata(wrapper)
                    if record:result.append(record)
            return result
        except Exception:return []

    def _tree(self, window) -> tuple[list[dict], dict[str, object], bool]:
        nodes = []
        wrappers = {}
        pending = [("0", window, 0)]
        truncated = False
        while pending:
            selector, wrapper, depth = pending.pop(0)
            try:
                info = wrapper.element_info
                node = {"selector": selector,
                        "name": self._short(wrapper.window_text()),
                        "automation_id": self._short(getattr(info, "automation_id", "")),
                        "control_type": self._short(getattr(info, "control_type", ""), 100),
                        "class_name": self._short(getattr(info, "class_name", ""), 200),
                        "enabled": bool(wrapper.is_enabled()),
                        "visible": bool(wrapper.is_visible()),
                        "rectangle": self._rect(wrapper)}
                nodes.append(node)
                wrappers[selector] = wrapper
                if depth >= MAX_TREE_DEPTH:
                    try:
                        if wrapper.children():
                            truncated = True
                    except Exception:
                        pass
                    continue
                children = wrapper.children()
                for index, child in enumerate(children):
                    if len(nodes) + len(pending) >= MAX_TREE_NODES:
                        truncated = True
                        break
                    pending.append((f"{selector}.{index}", child, depth + 1))
            except Exception:
                continue
        return nodes, wrappers, truncated or bool(pending)

    def _observe(self, hwnd: int, window) -> dict:
        nodes, _wrappers, truncated = self._tree(window)
        result = {"window": {"window_handle": hwnd,
                             "title": self._short(window.window_text(), 300),
                             "process_id": int(window.process_id()),
                             "rectangle": self._window_rect(hwnd,window)},
                  "controls": nodes, "count": len(nodes), "truncated": truncated,
                  "max_depth": MAX_TREE_DEPTH, "max_controls": MAX_TREE_NODES}
        return self._fit_result_list(result, "controls")

    @staticmethod
    def _fit_result_list(result: dict, key: str, max_bytes: int = 150_000) -> dict:
        # The remote bridge JSON-escapes Unicode, so measure that representation
        # rather than only UTF-8 storage size.
        while len(json.dumps(result, ensure_ascii=True).encode("ascii")) > max_bytes and result[key]:
            result[key].pop()
            result["count"] = len(result[key])
            result["truncated"] = True
        return result

    @staticmethod
    def _matches(node: dict, params: dict) -> bool:
        return all(node.get(key) == params[key] for key in ("automation_id", "control_type", "name") if key in params)

    def _select_control(self, window, params: dict):
        nodes, wrappers, _truncated = self._tree(window)
        selector = params.get("selector")
        if selector is not None:
            if selector not in wrappers:
                raise ValueError("selector did not match a control in the current UIA tree")
            return selector, wrappers[selector], next(node for node in nodes if node["selector"] == selector)
        matches = [node for node in nodes if self._matches(node, params)]
        if not matches:
            raise ValueError("control criteria did not match a UIA element")
        if len(matches) > 1:
            raise ValueError(f"control criteria are ambiguous ({len(matches)} matches); use selector from desktop.observe")
        node = matches[0]
        return node["selector"], wrappers[node["selector"]], node

    def _guard_mutation(self, hwnd: int, pid: int, action: str, foreground_before: int) -> None:
        session_id, unlocked = _session_state()
        if session_id == 0 or not unlocked:
            raise DesktopActionRefused("interactive desktop is locked or unavailable; action refused")
        if _process_session_id(pid) != session_id:
            raise DesktopActionRefused("target window is outside the current interactive session")
        if _integrity_level(pid) > _integrity_level(os.getpid()):
            raise DesktopActionRefused("target runs at a higher integrity level; elevated targets are not supported")
        if _idle_ms() < MIN_IDLE_MS:
            raise DesktopActionRefused("user activity was detected within the last 2 seconds; action refused")
        if not _is_window(hwnd):
            raise DesktopActionRefused("target window is no longer valid")
        foreground_now = _foreground_hwnd()
        if foreground_now != foreground_before:
            raise DesktopActionRefused("foreground window changed during action preparation; action refused")
        if action=='focus' and foreground_now!=hwnd and _fullscreen_foreground(foreground_now):
            raise DesktopActionRefused('A fullscreen app is foreground; ask the owner to bring the target forward when ready')
        if action != "focus" and foreground_now != hwnd:
            raise DesktopActionRefused("target is not foreground; request an explicit focus action first")

    def _act(self, hwnd: int, pid: int, window, params: dict) -> dict:
        selector, control, node = self._select_control(window, params)
        action = params["action"]
        if not node["visible"] or not node["enabled"]:
            raise ValueError("selected control is hidden or disabled")
        if action == "set_text" and node["control_type"] != "Edit":
            raise ValueError("set_text is supported only for UIA Edit controls")
        foreground_before = _foreground_hwnd()
        self._guard_mutation(hwnd, pid, action, foreground_before)
        if action == "focus":
            control.set_focus()
            if _foreground_hwnd() != hwnd:
                raise OSError("focus action did not activate the expected target window")
            verified = True
        elif action == "invoke":
            control.invoke()
            # UIA accepting Invoke does not prove the application's intended
            # result (and a valid Close button can destroy its own window).
            verified = False
        else:
            setter = getattr(control, "set_edit_text", None)
            if not callable(setter):
                raise ValueError("selected UIA control does not support set_edit_text")
            setter(params["text"])
            try:
                getter = getattr(control, "get_value", None)
                observed = getter() if callable(getter) else control.window_text()
                verified = observed == params["text"]
            except Exception:
                verified = False
            if not verified:
                raise OSError("set_text could not verify the resulting control value")
        return {"ok": True, "action": action, "window_handle": hwnd,
                "selector": selector, "verified": bool(verified),
                "invoked": action == 'invoke',
                "verification_required": 'Observe the application to verify the effect.' if action == 'invoke' else None}

    def _screenshot(self, cid: str, hwnd: int, expected_pid: int) -> dict:
        if not _is_window(hwnd):
            raise ValueError("target window is no longer valid")
        if _window_pid(hwnd) != expected_pid:
            raise ValueError("target window process changed before screenshot")
        rectangle=self._window_rect(hwnd)
        data = _capture_window_printwindow(hwnd, expected_pid)
        self._require_session()
        if not _is_window(hwnd):
            raise ValueError("target window closed while capturing screenshot")
        if _window_pid(hwnd) != expected_pid:
            raise ValueError("target window process changed while capturing screenshot")
        if rectangle!=self._window_rect(hwnd):
            raise ValueError('Target window moved or resized during capture; request a fresh observation')
        safe_id = hashlib.sha256(cid.encode("utf-8")).hexdigest()[:20]
        directory = self.artifact_dir / safe_id
        target = directory / (f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}.jpg")
        if len(data) > MAX_SCREENSHOT_BYTES:
            raise ValueError(f"screenshot exceeds {MAX_SCREENSHOT_BYTES} byte limit")
        directory.mkdir(parents=True, exist_ok=True)
        try:
            target.write_bytes(data)
            if not _is_window(hwnd) or _window_pid(hwnd) != expected_pid:
                raise ValueError("target window identity changed while saving screenshot")
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return {"path": str(target), "size": len(data), "format": "jpeg", "rectangle":rectangle}

    def close(self) -> None:
        self._closed = True

