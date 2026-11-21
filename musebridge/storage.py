"""User-bound secret storage; no plaintext provisioning tokens on disk.

Windows keeps the original DPAPI files (``<name>.dpapi``). On Linux the
same Store API is backed by :class:`musebridge.linux_support.SecretStore`:
the Linux keyring (Secret Service) with a 0600 file fallback
(``<name>.secret`` in the profile directory).
"""
import json
import os
import sys
import threading
from pathlib import Path

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import win32crypt
else:
    from .linux_support import SecretStore


def _linux_data_base():
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base)
    return Path.home() / ".local" / "share"


def data_directories(base=None):
    """Known MuseBridge-only locations, including Windows packaged-app redirection."""
    override = os.environ.get("MUSEBRIDGE_DATA_DIR")
    if _IS_WINDOWS:
        base = Path(base or os.environ["LOCALAPPDATA"])
        candidates = [base / "MuseBridge"]
        candidates.extend(base.glob("Packages/OpenAI.Codex_*/LocalCache/Local/MuseBridge"))
    else:
        base = Path(base) if base else _linux_data_base()
        candidates = [base / "MuseBridge"]
    if override:
        candidates.append(Path(override).expanduser())
    return candidates


def _secret_names():
    return ("identity.dpapi", "pairing.dpapi", "harness.sqlite") if _IS_WINDOWS else (
        "identity.secret", "pairing.secret", "harness.sqlite")


def default_directory():
    override = os.environ.get("MUSEBRIDGE_DATA_DIR")
    if override:
        selected = Path(override).expanduser()
        if not selected.is_absolute():
            raise ValueError("MUSEBRIDGE_DATA_DIR must be an absolute path")
        return selected
    candidates = data_directories()
    existing = []
    for path in candidates:
        if not any((path / name).is_file() for name in _secret_names()):
            continue
        duplicate = False
        for index, prior in enumerate(existing):
            try:
                duplicate = path.samefile(prior)
            except OSError:
                duplicate = path == prior
            if duplicate:
                # Prefer the explicit physical cache location over its MSIX
                # virtual alias, so recorded paths also work outside Codex.
                if "LocalCache" in path.parts:
                    existing[index] = path
                break
        if not duplicate:
            existing.append(path)
    if len(existing) > 1:
        raise RuntimeError("Multiple MuseBridge profiles exist. Set MUSEBRIDGE_DATA_DIR to the intended profile; no profiles were merged or changed.")
    return existing[0] if existing else candidates[0]


class Store:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else default_directory()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._secrets = None if _IS_WINDOWS else SecretStore(directory=self.directory)

    def load(self, name):
        with self.lock:
            if _IS_WINDOWS:
                path = self.directory / (name + ".dpapi")
                if not path.exists():
                    return None
                raw = win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 0)[1]
                return json.loads(raw)
            raw = self._secrets.unprotect(name)
            if raw is None:
                return None
            return json.loads(raw)

    def save(self, name, value):
        with self.lock:
            raw = json.dumps(value).encode()
            if _IS_WINDOWS:
                path = self.directory / (name + ".dpapi")
                encrypted = win32crypt.CryptProtectData(raw, "MuseBridge", None, None, None, 0)
                temporary = path.with_suffix(".tmp")
                with temporary.open("wb") as stream:
                    stream.write(encrypted)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                return
            self._secrets.protect(name, raw)

    def delete(self, name):
        with self.lock:
            if _IS_WINDOWS:
                (self.directory / (name + ".dpapi")).unlink(missing_ok=True)
                return
            self._secrets.delete(name)
