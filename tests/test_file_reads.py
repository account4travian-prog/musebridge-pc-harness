import hashlib
import sqlite3
import threading

import pytest

from musebridge.catalog import Catalog
from musebridge.execution import migrate
from musebridge.file_reads import FileReadStore
from musebridge.workspace import Workspace


def test_receipts_are_per_conversation_per_root_and_survive_restart(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'file.txt').write_bytes(b'first')
    workspace = Workspace(root)
    state = tmp_path / 'state'
    catalog = Catalog(state)
    project = catalog.create_project('Fixture', root)
    one = catalog.create_conversation('One', project['id'])['id']
    two = catalog.create_conversation('Two', project['id'])['id']
    sha = hashlib.sha256(b'first').hexdigest()
    store = FileReadStore(catalog)
    store.remember(one, workspace, 'file.txt', sha)
    assert store.get(one, workspace, 'file.txt') == sha
    assert store.get(two, workspace, 'file.txt') is None
    assert store.get(one, workspace, 'other.txt') is None
    second_root = tmp_path / 'another'
    second_root.mkdir()
    assert store.get(one, Workspace(second_root), 'file.txt') is None
    catalog.close()
    catalog = Catalog(state)
    try:
        restored = FileReadStore(catalog)
        assert restored.get(one, workspace, 'file.txt') == sha
        assert restored.get(two, workspace, 'file.txt') is None
    finally:
        catalog.close()


def test_v2_copy_migration_is_additive_and_failure_rolls_back(tmp_path):
    original_dir = tmp_path / 'original'
    original = Catalog(original_dir)
    original.draft('main', 'Preserve owner draft')
    original.db.execute('DROP TABLE file_read_receipts')
    original.db.execute('PRAGMA user_version=2')
    original.db.commit()
    copy_dir = tmp_path / 'copy'
    copy_dir.mkdir()
    copied = sqlite3.connect(copy_dir / 'harness.sqlite')
    original.db.backup(copied)
    copied.close()
    original.close()
    old_bytes = (original_dir / 'harness.sqlite').read_bytes()
    db = sqlite3.connect(copy_dir / 'harness.sqlite')
    fixture = type('Fixture', (), {'db': db, 'lock': threading.RLock()})()

    def deny_receipts(action, name, *_rest):
        if action == sqlite3.SQLITE_CREATE_TABLE and name == 'file_read_receipts':
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    db.set_authorizer(deny_receipts)
    with pytest.raises(sqlite3.DatabaseError):
        migrate(fixture)
    db.set_authorizer(None)
    assert db.execute('PRAGMA user_version').fetchone()[0] == 2
    assert db.execute('SELECT text FROM drafts').fetchone()[0] == 'Preserve owner draft'
    migrate(fixture)
    from musebridge.execution import SCHEMA_VERSION
    assert db.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
    assert db.execute('SELECT COUNT(*) FROM file_read_receipts').fetchone()[0] == 0
    assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    db.close()
    assert (original_dir / 'harness.sqlite').read_bytes() == old_bytes
