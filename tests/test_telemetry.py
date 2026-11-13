import asyncio
import json
import sqlite3
from pathlib import Path

from musebridge.catalog import Catalog
from musebridge.core import Core
from musebridge.execution import SCHEMA_VERSION
from musebridge.metrics import result_size
from musebridge.tooling import ToolRouter


def test_v1_database_copy_upgrades_without_losing_existing_records(tmp_path):
    original = tmp_path / 'original.sqlite'
    db = sqlite3.connect(original)
    db.executescript('''
        CREATE TABLE tool_calls(id TEXT PRIMARY KEY,run_id TEXT,conversation_id TEXT,tool TEXT,
            effect TEXT,state TEXT,created REAL,finished REAL,error_code TEXT);
        INSERT INTO tool_calls VALUES('c','r','main','file.read','read','failed',1,2,'Missing');
        CREATE TABLE drafts(conversation_id TEXT PRIMARY KEY,text TEXT);
        INSERT INTO drafts VALUES('main','preserved draft');
        PRAGMA user_version=1;
    ''')
    directory = tmp_path / 'copy'
    directory.mkdir()
    copied = sqlite3.connect(directory / 'harness.sqlite')
    db.backup(copied)
    copied.close()
    db.close()
    before = original.read_bytes()
    catalog = Catalog(directory)
    try:
        row = catalog.rows('SELECT * FROM tool_calls')[0]
        assert row['id'] == 'c' and row['error_code'] == 'Missing'
        assert row['result_bytes'] is None and row['error_message'] is None
        assert catalog.draft('main') == 'preserved draft'
        assert catalog.db.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
        assert catalog.db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert original.read_bytes() == before
    finally:
        catalog.close()


def test_payload_metrics_count_rejected_calls_without_saving_arguments(tmp_path):
    catalog = Catalog(tmp_path / 'state')
    project = catalog.create_project('Fixture', tmp_path)
    cid = catalog.create_conversation('Metrics', project['id'])['id']
    router = ToolRouter(catalog, tmp_path / 'state')
    run = router.execution.begin_run(cid)
    try:
        result = router.call('file.read', {'conversation_id': cid, 'path': 'owner-secret.txt'}, expected_conversation_id=cid)
        rejected = router.call('file.read', {'path': 'another-secret.txt'}, expected_conversation_id=cid, remote=True)
        assert not result['ok'] and not rejected['ok']
        rows = router.execution.calls(cid, run)
        assert len(rows) == 2
        assert rows[0]['result_bytes'] == result_size(result)
        assert rows[1]['result_bytes'] == result_size(rejected)
        for row in rows:
            assert len(row['error_message']) <= 200
            assert 'owner-secret' not in row['error_message']
            assert 'another-secret' not in row['error_message']
            assert str(tmp_path) not in row['error_message']
    finally:
        router.close()
        catalog.close()


def test_send_records_numeric_preamble_size_without_copying_message(tmp_path):
    async def scenario():
        catalog = Catalog(tmp_path)
        cid = catalog.create_conversation('Metrics')['id']
        router = ToolRouter(catalog, tmp_path)
        core = Core(None, lambda _: None, lambda *_: None, lambda _: None,
                    catalog=catalog, router=router, conversation_id=cid)
        class Session:
            chat_subscribed = asyncio.Event()
            async def send_chat(self, wire, session_id=None):
                self.wire = wire
                return {'ok': True, 'response': {'message_id': 'u', 'session_id': cid, 'is_thread': True}}
        core.session = Session()
        core.session.chat_subscribed.set()
        text = 'Private synthetic message'
        try:
            await core.send(text)
            row = catalog.rows("SELECT data FROM events WHERE kind='send.context'")[0]
            data = json.loads(row['data'])
            assert data['preamble_bytes'] == len(core.session.wire.encode()) - len(text.encode())
            assert data['user_bytes'] == len(text.encode())
            assert text not in row['data']
        finally:
            router.close()
            catalog.close()
    asyncio.run(scenario())


def test_statistics_failure_does_not_turn_a_published_edit_into_tool_failure(tmp_path, monkeypatch):
    catalog = Catalog(tmp_path / 'state')
    project = catalog.create_project('Fixture', tmp_path)
    cid = catalog.create_conversation('Metrics', project['id'])['id']
    router = ToolRouter(catalog, tmp_path / 'state')
    def fail_statistics(*args):
        raise sqlite3.OperationalError('synthetic metric write failure')
    monkeypatch.setattr(router.execution, 'record_statistics', fail_statistics)
    try:
        result = router.call('file.write', {'conversation_id': cid, 'path': 'published.txt', 'text': 'kept', 'create': True})
        assert result['ok'] and (tmp_path / 'published.txt').read_text() == 'kept'
        assert router.statistics_failures == 1
        assert catalog.rows('SELECT state FROM tool_calls')[0]['state'] == 'succeeded'
    finally:
        router.close()
        catalog.close()
