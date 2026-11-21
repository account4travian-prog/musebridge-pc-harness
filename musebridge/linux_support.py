"""Linux platform support for MuseBridge.

Replaces the Windows-only pieces with Linux equivalents:

- secrets: Linux keyring (Secret Service) with a 0600 file fallback,
  instead of DPAPI (``win32crypt``).
- shell: bash resolved from PATH, instead of PowerShell / cmd.
- platform: Wayland / X11 session, desktop, and Bluetooth detection.

Only the standard library is required. ``keyring`` is optional: when it is
installed and a Secret Service is reachable it is used, otherwise the file
fallback is used and the caller is told so.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

SERVICE_NAME = "musebridge-linux"


def config_dir() -> Path:
    """Per-user config dir, created with 0700 permissions."""
    path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "musebridge-linux"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def is_wayland() -> bool:
    return os.environ.get("XDG_SESSION_TYPE") == "wayland"


def session_type() -> str:
    return os.environ.get("XDG_SESSION_TYPE", "unknown")


def desktop_environment() -> str:
    return os.environ.get("XDG_CURRENT_DESKTOP", "unknown")


def has_bluetooth() -> bool:
    """True when a Bluetooth controller is present and unblocked."""
    if Path("/sys/class/bluetooth").exists() and any(Path("/sys/class/bluetooth").iterdir()):
        return True
    if shutil.which("bluetoothctl") is None:
        return False
    result = subprocess.run(
        ["bluetoothctl", "list"], capture_output=True, text=True, timeout=10
    )
    return "Controller" in result.stdout


def resolve_bash() -> str | None:
    return shutil.which("bash")


def bash_argv(command: str) -> list[str]:
    """argv for running one command through bash, mirroring shells.command_argv."""
    executable = resolve_bash() or "/bin/bash"
    return [executable, "-c", command]


class SecretStore:
    """Byte secret storage for pairing and identity material.

    Prefers the Linux keyring (Secret Service over D-Bus). Falls back to a
    0600 file and reports that it did so, because a file relies on Unix
    permissions where DPAPI binds to the user account.

    ``directory`` is the harness profile directory. The file fallback lives
    there, next to the profile's other state, and the keyring username is
    scoped to it, so switching profiles (MUSEBRIDGE_DATA_DIR) switches
    secrets too, the way per-directory DPAPI files do on Windows.
    """

    def __init__(self, service: str = SERVICE_NAME, directory=None) -> None:
        self.service = service
        self.directory = Path(directory) if directory is not None else None
        self._keyring = None
        try:
            import keyring  # type: ignore

            keyring.get_password(service, "__musebridge_probe__")
            self._keyring = keyring
        except Exception:
            self._keyring = None

    @property
    def using_keyring(self) -> bool:
        return self._keyring is not None

    def _account(self, name: str) -> str:
        if self.directory is None:
            return name
        digest = hashlib.sha256(str(self.directory).encode("utf-8")).hexdigest()[:12]
        return f"{name}@{digest}"

    def _file_path(self, name: str) -> Path:
        base = self.directory if self.directory is not None else config_dir()
        return base / (name + ".secret")

    def protect(self, name: str, raw: bytes) -> None:
        if self._keyring is not None:
            import base64

            self._keyring.set_password(
                self.service, self._account(name), base64.b64encode(raw).decode("ascii")
            )
            return
        path = self._file_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        os.chmod(path, 0o600)

    def unprotect(self, name: str) -> bytes | None:
        if self._keyring is not None:
            import base64

            value = self._keyring.get_password(self.service, self._account(name))
            if value is None:
                return None
            return base64.b64decode(value.encode("ascii"))
        path = self._file_path(name)
        if not path.exists():
            return None
        return path.read_bytes()

    def delete(self, name: str) -> None:
        if self._keyring is not None:
            try:
                self._keyring.delete_password(self.service, self._account(name))
            except Exception:
                pass
        path = self._file_path(name)
        if path.exists():
            path.unlink()


def platform_info() -> dict[str, object]:
    """One dict describing this machine for the port plan and diagnostics."""
    return {
        "os": "linux",
        "session_type": session_type(),
        "desktop": desktop_environment(),
        "wayland": is_wayland(),
        "bluetooth": has_bluetooth(),
        "bash": resolve_bash(),
        "python": os.sys.version.split()[0],
    }
