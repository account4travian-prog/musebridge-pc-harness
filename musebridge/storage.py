"""User-bound DPAPI storage; no plaintext provisioning tokens on disk."""
import json
import os
import threading
from pathlib import Path

import win32crypt


def data_directories(base=None):
    """Known MuseBridge-only locations, including Windows packaged-app redirection."""
    base=Path(base or os.environ['LOCALAPPDATA'])
    candidates=[base/'MuseBridge']
    candidates.extend(base.glob('Packages/OpenAI.Codex_*/LocalCache/Local/MuseBridge'))
    override=os.environ.get('MUSEBRIDGE_DATA_DIR')
    if override:candidates.append(Path(override).expanduser())
    return candidates


def default_directory():
    override=os.environ.get('MUSEBRIDGE_DATA_DIR')
    if override:
        selected=Path(override).expanduser()
        if not selected.is_absolute():raise ValueError('MUSEBRIDGE_DATA_DIR must be an absolute path')
        return selected
    candidates=data_directories();existing=[]
    for path in candidates:
        if not any((path/name).is_file() for name in ('identity.dpapi','pairing.dpapi','harness.sqlite')):continue
        duplicate=False
        for index,prior in enumerate(existing):
            try:duplicate=path.samefile(prior)
            except OSError:duplicate=path==prior
            if duplicate:
                # Prefer the explicit physical cache location over its MSIX
                # virtual alias, so recorded paths also work outside Codex.
                if 'LocalCache' in path.parts:existing[index]=path
                break
        if not duplicate:existing.append(path)
    if len(existing)>1:
        raise RuntimeError('Multiple MuseBridge profiles exist. Set MUSEBRIDGE_DATA_DIR to the intended profile; no profiles were merged or changed.')
    return existing[0] if existing else candidates[0]


class Store:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else default_directory()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def load(self, name):
        with self.lock:
            path = self.directory / (name + '.dpapi')
            if not path.exists():
                return None
            raw = win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 0)[1]
            return json.loads(raw)

    def save(self, name, value):
        with self.lock:
            path = self.directory / (name + '.dpapi')
            raw = json.dumps(value).encode()
            encrypted = win32crypt.CryptProtectData(raw, 'MuseBridge', None, None, None, 0)
            temporary = path.with_suffix('.tmp')
            with temporary.open('wb') as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)

    def delete(self, name):
        with self.lock:
            (self.directory / (name + '.dpapi')).unlink(missing_ok=True)
