import shutil
import sqlite3

from musebridge.jobs import JobManager


def test_job_output_migration_upgrades_a_copy_without_inventing_lost_head(tmp_path):
    original = tmp_path / 'original.sqlite'
    db = sqlite3.connect(original)
    db.execute('''CREATE TABLE jobs (
        job_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
        args_hash TEXT NOT NULL, command_json TEXT NOT NULL, cwd TEXT NOT NULL,
        timeout_s REAL NOT NULL, state TEXT NOT NULL, created REAL NOT NULL,
        started REAL, finished REAL, exit_code INTEGER, output TEXT NOT NULL DEFAULT '',
        output_base INTEGER NOT NULL DEFAULT 0, error TEXT)''')
    for identifier, output, base in [('whole', 'beginning and end', 0), ('truncated', 'tail', 1000)]:
        db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (identifier, identifier, 'digest', '["echo fixture"]', str(tmp_path),
             10, 'succeeded', 1, 2, 3, 0, output, base, None))
    db.commit()
    db.close()
    original_bytes = original.read_bytes()
    copy = tmp_path / 'copy'
    copy.mkdir()
    shutil.copy2(original, copy / 'jobs.sqlite3')
    manager = JobManager(copy)
    try:
        rows = manager._db.execute('SELECT job_id,state,output_head,output_total,output,output_base FROM jobs ORDER BY job_id').fetchall()
        assert rows == [('truncated', 'succeeded', '', 1004, 'tail', 1000),
                        ('whole', 'succeeded', 'beginning and end', 17, 'beginning and end', 0)]
        assert manager._db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert original.read_bytes() == original_bytes
    finally:
        manager.close()
