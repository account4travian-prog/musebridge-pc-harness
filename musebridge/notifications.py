"""Opt-in, silent Windows notification-area balloons for the desktop app.

Nothing is registered or displayed until ``DesktopNotifications.show`` is
called. Windows may suppress balloons during quiet time; delivery is best
effort. The service never changes Windows notification settings.
"""
from __future__ import annotations

import ctypes
import os
import uuid
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication


# Shell_NotifyIconW constants.
NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIM_SETVERSION = 0x00000004
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010
NIIF_NOSOUND = 0x00000010
NIIF_RESPECT_QUIET_TIME = 0x00000080
NOTIFYICON_VERSION_4 = 4
NIN_BALLOONUSERCLICK = 0x0405  # WM_USER + 5
WM_APP = 0x8000


class _BalloonTimeoutOrVersion(ctypes.Union):
    _fields_ = [('uTimeout', ctypes.c_uint32), ('uVersion', ctypes.c_uint32)]


class NOTIFYICONDATAW(ctypes.Structure):
    """SDK NOTIFYICONDATAW layout, including the V4 fields."""
    _anonymous_ = ('timeout_version',)
    _fields_ = [
        ('cbSize', ctypes.c_uint32),
        ('hWnd', ctypes.c_void_p),
        ('uID', ctypes.c_uint32),
        ('uFlags', ctypes.c_uint32),
        ('uCallbackMessage', ctypes.c_uint32),
        ('hIcon', ctypes.c_void_p),
        ('szTip', ctypes.c_wchar * 128),
        ('dwState', ctypes.c_uint32),
        ('dwStateMask', ctypes.c_uint32),
        ('szInfo', ctypes.c_wchar * 256),
        ('timeout_version', _BalloonTimeoutOrVersion),
        ('szInfoTitle', ctypes.c_wchar * 64),
        ('dwInfoFlags', ctypes.c_uint32),
        ('guidItem', ctypes.c_ubyte * 16),
        ('hBalloonIcon', ctypes.c_void_p),
    ]


class _NativeMessage(ctypes.Structure):
    _fields_ = [
        ('hwnd', ctypes.c_void_p), ('message', ctypes.c_uint32),
        ('wParam', ctypes.c_size_t), ('lParam', ctypes.c_ssize_t),
        ('time', ctypes.c_uint32), ('x', ctypes.c_long), ('y', ctypes.c_long),
    ]


class _WindowsShell:
    """Thin lazy binding around shell32 and user32."""
    def __init__(self):
        if os.name != 'nt':
            raise OSError('Windows notification balloons are available only on Windows')
        self._shell = ctypes.WinDLL('shell32', use_last_error=True)
        self._user = ctypes.WinDLL('user32', use_last_error=True)
        self._notify = self._shell.Shell_NotifyIconW
        self._notify.argtypes = (ctypes.c_uint32, ctypes.POINTER(NOTIFYICONDATAW))
        self._notify.restype = wintypes.BOOL
        self._load_icon = self._user.LoadIconW
        self._load_icon.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        self._load_icon.restype = ctypes.c_void_p

    def notify(self, command, data):
        return bool(self._notify(command, ctypes.byref(data)))

    def application_icon(self):
        # IDI_APPLICATION is a shared system icon. The pointer is static and
        # Shell_NotifyIconW does not take ownership of it.
        return self._load_icon(None, ctypes.cast(ctypes.c_void_p(32512), wintypes.LPCWSTR))


class _CallbackFilter(QAbstractNativeEventFilter):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def nativeEventFilter(self, event_type, message):
        try:
            address = int(message)
            msg = ctypes.cast(address, ctypes.POINTER(_NativeMessage)).contents
        except (TypeError, ValueError, OSError):
            return False, 0
        if self.owner._handle_native(msg.message, msg.wParam, msg.lParam):
            return True, 0
        return False, 0


class DesktopNotifications:
    """Lazy owner for this app's silent Windows balloon notification icon.

    ``show`` accepts caller-selected generic title/body text and a conversation
    ID. Clicking the balloon calls ``on_open(cid)``; it does not activate,
    raise, or focus the window. Call ``close`` during app shutdown.
    """
    def __init__(self, window, on_open, *, shell_api=None, app=None):
        self.window = window
        self.on_open = on_open
        self._shell_api = shell_api
        self._app = app
        self._filter = None
        self._added = False
        self._cid = None
        self._hwnd = 0
        self._icon_id = int(uuid.uuid4().int & 0xFFFF) or 1
        self._callback_message = WM_APP + 0x3B7

    def _get_api(self):
        if self._shell_api is None:
            self._shell_api = _WindowsShell()
        return self._shell_api

    def _get_app(self):
        if self._app is None:
            self._app = QCoreApplication.instance()
        return self._app

    @staticmethod
    def _bounded(value, limit):
        value = str(value).replace('\x00', '').replace('\r', ' ').replace('\n', ' ')
        return value[:limit]

    def _data(self, flags):
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = self._icon_id
        data.uFlags = flags
        data.uCallbackMessage = self._callback_message
        return data

    def _ensure_icon(self):
        if self._added:
            return True
        app = self._get_app()
        if app is None:
            return False
        try:
            api = self._get_api()
            self._hwnd = int(self.window.winId())
            icon = api.application_icon()
            data = self._data(NIF_MESSAGE | NIF_ICON | NIF_TIP)
            data.hIcon = icon
            data.szTip = 'MuseBridge notifications'
            if not api.notify(NIM_ADD, data):
                return False
            version = self._data(0)
            version.uVersion = NOTIFYICON_VERSION_4
            if not api.notify(NIM_SETVERSION, version):
                api.notify(NIM_DELETE, data)
                return False
            if self._filter is None:
                self._filter = _CallbackFilter(self)
                app.installNativeEventFilter(self._filter)
            self._added = True
            return True
        except (OSError, AttributeError, TypeError, ValueError):
            return False

    def show(self, cid, title, body):
        """Request one silent notification; return whether Shell accepted it."""
        if not isinstance(cid, str) or not cid:
            return False
        if not self._ensure_icon():
            return False
        title = self._bounded(title, 63)
        body = self._bounded(body, 255)
        # NOTIFYICONDATA's click callback identifies an icon, not a particular
        # balloon. Clear the old balloon and rotate the icon ID before showing
        # another, so queued callbacks from the old balloon cannot be retargeted.
        if self._cid is not None:
            clear = self._data(NIF_INFO)
            clear.szInfo = ''
            if not self._get_api().notify(NIM_MODIFY, clear):
                return False
            if not self._get_api().notify(NIM_DELETE, self._data(0)):
                return False
            self._added = False
            self._cid = None
            self._icon_id = (self._icon_id % 0xFFFF) + 1
            if not self._ensure_icon():
                return False
        data = self._data(NIF_INFO)
        data.szInfoTitle = title
        data.szInfo = body
        data.dwInfoFlags = NIIF_NOSOUND | NIIF_RESPECT_QUIET_TIME
        try:
            accepted = bool(self._get_api().notify(NIM_MODIFY, data))
            if accepted:
                self._cid = cid
            return accepted
        except (OSError, AttributeError, TypeError, ValueError):
            return False

    def _handle_native(self, message, wparam, lparam):
        if not self._added or message != self._callback_message:
            return False
        # V4 places the event in LOWORD(lParam), and the icon ID in
        # HIWORD(lParam). wParam contains anchor coordinates and is undefined
        # for balloon click events.
        event = int(lparam) & 0xFFFF
        icon_id = (int(lparam) >> 16) & 0xFFFF
        if icon_id != self._icon_id or event != NIN_BALLOONUSERCLICK:
            return False
        cid = self._cid
        if cid is not None:
            try:
                self.on_open(cid)
            except Exception:
                # A callback must not escape through Qt's native message pump.
                pass
        return True

    def close(self):
        """Remove this service's icon and native callback filter, if installed."""
        app = self._app
        if self._filter is not None and app is not None:
            try:
                app.removeNativeEventFilter(self._filter)
            except (AttributeError, RuntimeError):
                pass
        self._filter = None
        removed = True
        if self._added:
            try:
                removed = bool(self._get_api().notify(NIM_DELETE, self._data(0)))
            except (OSError, AttributeError, TypeError, ValueError):
                removed = False
        self._added = False
        self._cid = None
        return removed
