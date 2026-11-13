"""Time and memory bounded private PrintWindow worker; stdout is JPEG only."""
from __future__ import annotations

import ctypes
from io import BytesIO
import sys

from .document_worker import limit_windows_process_memory
from .desktop import MAX_SCREENSHOT_BYTES, _render_window_printwindow


def _set_dpi_awareness():
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        setter = user32.SetThreadDpiAwarenessContext
        setter.argtypes = (ctypes.c_void_p,)
        setter.restype = ctypes.c_void_p
        setter(ctypes.c_void_p(-4))  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        # Per-monitor-awareness is best effort; dimensions are still measured
        # directly with GetWindowRect in this process.
        pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if len(argv) != 2:
            return 2
        hwnd, expected_pid = int(argv[0]), int(argv[1])
        if hwnd <= 0 or expected_pid <= 0 or expected_pid > 0xFFFFFFFF:
            return 2
        limit_windows_process_memory()
        _set_dpi_awareness()
        image = _render_window_printwindow(hwnd, expected_pid)
        output = BytesIO()
        image.save(output, format="JPEG", quality=75, optimize=True)
        data = output.getvalue()
        if len(data) > MAX_SCREENSHOT_BYTES:
            return 4
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
        return 0
    except BaseException:
        # Never emit exception text, filenames, paths, or user-window data.
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
