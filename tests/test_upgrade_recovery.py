import sqlite3
import threading
from pathlib import Path
import pytest
from musebridge.catalog import Catalog
from musebridge.execution import migrate,SCHEMA_VERSION
from musebridge.tooling import ToolRouter
from musebridge.request_queue import RequestQueue


def legacy_profile(directory,root):
    directory.mkdir();db=sqlite3.connect(directory/'harness.sqlite')
    db.executescript('''CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,root TEXT,instructions TEXT);
      CREATE TABLE conversations(id TEXT PRIMARY KEY,project_id TEXT,title TEXT,remote_id TEXT,created REAL,plan TEXT DEFAULT '',goal TEXT DEFAULT '',goal_state TEXT DEFAULT 'idle');
      CREATE TABLE messages(conversation_id TEXT,id TEXT,role TEXT,text TEXT,created REAL,PRIMARY KEY(conversation_id,id));
      CREATE TABLE drafts(conversation_id TEXT PRIMARY KEY,text TEXT);
      CREATE TABLE outbox(id TEXT PRIMARY KEY,conversation_id TEXT,text TEXT,state TEXT,created REAL);
      CREATE TABLE preferences(key TEXT PRIMARY KEY,value TEXT);''')
    db.execute('INSERT INTO projects VALUES(?,?,?,?)',('project','Preserved',str(root),'Owner instructions'))
    db.execute('INSERT INTO conversations VALUES(?,?,?,?,?,?,?,?)',('chat','project','Existing','remote-chat',1,'Original plan','Original goal','paused'))
    db.execute('INSERT INTO messages VALUES(?,?,?,?,?)',('chat','message','Muse','Original reply',1))
    db.execute('INSERT INTO drafts VALUES(?,?)',('chat','Unsent owner draft'))
    db.execute('INSERT INTO outbox VALUES(?,?,?,?,?)',('send','chat','Do not replay','sending',1))
    db.commit();db.close()
    (directory/'pairing.dpapi').write_bytes(b'opaque-pairing-fixture')


def test_pre_expansion_profile_upgrade_backup_restore_preserves_identity_and_work(tmp_path):
    state=tmp_path/'state';project=tmp_path/'project';project.mkdir();legacy_profile(state,project)
    backup=sqlite3.connect(tmp_path/'backup.sqlite');old=sqlite3.connect(state/'harness.sqlite');old.backup(backup);old.close();backup.close()
    c=Catalog(state);r=ToolRouter(c,state);q=RequestQueue(c)
    try:
        assert c.draft('chat')=='Unsent owner draft'
        assert c.messages('chat')[0]['text']=='Original reply'
        assert c.context('chat')['plan']=='Original plan'
        assert c.conversation('chat')['remote_id']=='remote-chat'
        assert c.rows('SELECT state FROM outbox')[0]['state']=='unknown'
        assert not q.enabled and q.rows('chat')==[]
        assert (state/'pairing.dpapi').read_bytes()==b'opaque-pairing-fixture'
    finally:r.close();c.close()
    # Restore rehearsal uses a separate fixture, never an older writer on the upgraded DB.
    restored=tmp_path/'restored';restored.mkdir();source=sqlite3.connect(tmp_path/'backup.sqlite');target=sqlite3.connect(restored/'harness.sqlite');source.backup(target);source.close();target.close()
    c=Catalog(restored)
    try:assert c.draft('chat')=='Unsent owner draft' and c.context('chat')['goal']=='Original goal'
    finally:c.close()


def test_interrupted_execution_migration_rolls_back_and_can_resume(tmp_path):
    db=sqlite3.connect(tmp_path/'harness.sqlite')
    db.execute('CREATE TABLE owner_data(value TEXT)');db.execute("INSERT INTO owner_data VALUES('keep')");db.commit()
    class Fixture:pass
    fixture=Fixture();fixture.db=db;fixture.lock=threading.RLock()
    def deny_one_table(action,name,*rest):
        return sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_CREATE_TABLE and name=='tool_calls' else sqlite3.SQLITE_OK
    db.set_authorizer(deny_one_table)
    with pytest.raises(sqlite3.DatabaseError):migrate(fixture)
    db.set_authorizer(None)
    assert db.execute('PRAGMA user_version').fetchone()[0]==0
    assert db.execute("SELECT name FROM sqlite_master WHERE name IN ('runs','execution_scopes')").fetchall()==[]
    assert db.execute('SELECT value FROM owner_data').fetchone()[0]=='keep'
    migrate(fixture)
    assert db.execute('PRAGMA user_version').fetchone()[0]==SCHEMA_VERSION
    db.close()
