"""Persist the file version observed by each individual conversation."""
import os
import threading
import time


class FileReadStore:
    """Keep an internal hash receipt; hashes never belong in tool results."""

    def __init__(self, catalog):
        self.catalog = catalog
        self.lock = threading.RLock()
        self.cache = {}

    def _key(self, cid, workspace, path):
        target = workspace.resolve(path)
        relative = target.relative_to(workspace.root).as_posix()
        return (cid, os.path.normcase(str(workspace.root)), os.path.normcase(relative))

    def get(self, cid, workspace, path):
        """Return this chat's observed hash, including after a normal restart."""
        key = self._key(cid, workspace, path)
        with self.lock:
            if key not in self.cache:
                rows = self.catalog.rows(
                    'SELECT sha256 FROM file_read_receipts WHERE conversation_id=? AND project_root=? AND path=?', key)
                if not rows:
                    return None
                self.cache[key] = rows[0]['sha256']
            return self.cache[key]

    def remember(self, cid, workspace, path, sha256):
        """Commit the observed byte version before publishing it to the cache."""
        key = self._key(cid, workspace, path)
        with self.lock:
            with self.catalog.lock, self.catalog.db:
                self.catalog.db.execute('''INSERT INTO file_read_receipts VALUES(?,?,?,?,?)
                    ON CONFLICT(conversation_id,project_root,path) DO UPDATE SET
                    sha256=excluded.sha256,observed=excluded.observed''', (*key, sha256, time.time()))
            self.cache[key] = sha256
