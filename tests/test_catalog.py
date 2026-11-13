from musebridge.catalog import Catalog
import pytest
import sqlite3


def test_projects_conversations_drafts_plans_survive_restart(tmp_path):
    catalog = Catalog(tmp_path)
    project = catalog.create_project('Example', tmp_path, 'Run tests before reporting done')
    first = catalog.create_conversation('First', project['id'])
    second = catalog.create_conversation('Second', project['id'])
    assert first['remote_id'] != second['remote_id']
    catalog.message(first['id'],'m','Muse','first')
    catalog.message(first['id'],'m','Muse','updated')
    catalog.draft(second['id'],'unsent')
    catalog.plan(first['id'],text='1. inspect\n2. verify',goal='Fix example',state='active')
    catalog.close()
    catalog = Catalog(tmp_path)
    assert catalog.messages(first['id'])[0]['text'] == 'updated'
    assert catalog.messages(second['id']) == []
    assert catalog.draft(second['id']) == 'unsent'
    assert catalog.context(first['id'])['project']['instructions'].startswith('Run tests')
    assert catalog.context(first['id'])['goal_state'] == 'active'
    catalog.close()


def test_incomplete_outbox_is_never_replayed(tmp_path):
    catalog = Catalog(tmp_path)
    identifier = catalog.admit_send('main','side effect request')
    catalog.close()
    catalog = Catalog(tmp_path)
    assert catalog.rows('SELECT state FROM outbox WHERE id=?',(identifier,))[0]['state'] == 'unknown'
    assert catalog.messages('main') == []
    with pytest.raises(ValueError):
        catalog.plan('main',state='invented')
    catalog.close()


def test_pre_archive_database_migrates_without_losing_conversation(tmp_path):
    db=sqlite3.connect(tmp_path/'harness.sqlite')
    db.executescript("""
      CREATE TABLE conversations(id TEXT PRIMARY KEY,project_id TEXT,title TEXT,
        remote_id TEXT,created REAL,plan TEXT DEFAULT '',goal TEXT DEFAULT '',goal_state TEXT DEFAULT 'idle');
      INSERT INTO conversations(id,title,remote_id,created,plan) VALUES('legacy','Existing chat','remote-123',1,'Keep this plan');
    """)
    db.close()
    catalog=Catalog(tmp_path)
    catalog.draft('legacy','Keep this draft');catalog.message('legacy','m1','Muse','Keep this reply')
    catalog.rename_conversation('legacy','Renamed chat');catalog.archive_conversation('legacy')
    catalog.preference('sidebar_hidden',False);catalog.close()
    catalog=Catalog(tmp_path)
    assert 'legacy' not in [c['id'] for c in catalog.conversations()]
    existing=catalog.conversation('legacy')
    assert (existing['title'],existing['remote_id'],existing['plan'])==('Renamed chat','remote-123','Keep this plan')
    assert catalog.draft('legacy')=='Keep this draft'
    assert catalog.messages('legacy')[0]['text']=='Keep this reply'
    assert catalog.preference('sidebar_hidden',default=True) is False
    catalog.archive_conversation('legacy',False)
    assert 'legacy' in [c['id'] for c in catalog.conversations()]
    catalog.close()
