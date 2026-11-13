"""Windows process probes for MB-26 shell jobs."""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip('win32job', reason='Windows Job Objects are required')

from musebridge.jobs import JobManager
from musebridge import shells


def terminal(manager, job_id, timeout=20):
    deadline = time.monotonic() + timeout
    cursor = 0
    while time.monotonic() < deadline:
        result = manager.wait(job_id, cursor=cursor, timeout_s=min(1, deadline - time.monotonic()))
        cursor = result['cursor']
        if result['state'] in {'succeeded', 'failed', 'cancelled', 'interrupted', 'outcome_unknown'}:
            return manager.status(job_id, cursor=0)
    raise AssertionError('job did not finish')


def test_quick_unicode_output_and_shared_stream_order(tmp_path):
    manager = JobManager(tmp_path / 'runtime')
    try:
        command = "[Console]::Write('α'); [Console]::Error.Write('ERR'); [Console]::Write('雪')"
        started = manager.start('ordered-stream', command, tmp_path, 15)
        result = terminal(manager, started['job_id'])
        assert result['state'] == 'succeeded'
        assert 'αERR雪' in result['output']
    finally:
        manager.close()


def test_nonzero_native_exit_code_is_preserved(tmp_path):
    manager = JobManager(tmp_path / 'runtime')
    try:
        started = manager.start('native-exit', 'cmd /d /c exit 7', tmp_path, 15)
        result = terminal(manager, started['job_id'])
        assert result['state'] == 'failed'
        assert result['exit_code'] == 7
    finally:
        manager.close()


def test_preview_keeps_first_and_last_output_for_two_megabytes(tmp_path):
    manager = JobManager(tmp_path / 'runtime')
    try:
        command = "[Console]::Write(('H'*8192)+('M'*2000000)+('T'*8192))"
        started = manager.start('large-preview', command, tmp_path, 30)
        result = terminal(manager, started['job_id'], timeout=45)
        preview = manager.preview(started['job_id'])
        assert result['state'] == 'succeeded'
        assert preview['total_chars'] == 2_016_384
        assert preview['head'] == 'H' * 8192
        assert preview['tail'].endswith('T' * 8192)
        assert preview['omitted_chars'] == preview['total_chars'] - len(preview['head']) - len(preview['tail'])
        assert preview['omitted_chars'] > 0
        assert preview['started'] is not None and preview['finished'] is not None
    finally:
        manager.close()


def test_small_preview_has_only_tail_without_duplicate_head(tmp_path):
    manager = JobManager(tmp_path / 'runtime')
    try:
        started = manager.start('small-preview', "Write-Output 'small output'", tmp_path, 15)
        terminal(manager, started['job_id'])
        preview = manager.preview(started['job_id'])
        assert preview['head'] == ''
        assert preview['tail'] == 'small output\r\n'
        assert preview['omitted_chars'] == 0
    finally:
        manager.close()


def test_shell_is_part_of_deduplication_but_default_normalizes(tmp_path):
    manager = JobManager(tmp_path / 'runtime')
    try:
        first = manager.start('shell-key', "Write-Output 'one'", tmp_path, 15, shell='PowerShell')
        repeated = manager.start('shell-key', "Write-Output 'one'", tmp_path, 15, shell='powershell')
        assert repeated['job_id'] == first['job_id']
        with pytest.raises(ValueError, match='different arguments'):
            manager.start('shell-key', "Write-Output 'one'", tmp_path, 15, shell='cmd')
        assert terminal(manager, first['job_id'])['state'] == 'succeeded'
    finally:
        manager.close()


def test_legacy_default_shell_retry_returns_existing_record_without_replay(tmp_path):
    manager = JobManager(tmp_path / 'runtime')
    marker = tmp_path / 'should-not-run.txt'
    try:
        command = "Set-Content -LiteralPath '" + str(marker) + "' -Value 'wrong'"
        cwd = str(tmp_path.resolve())
        timeout = 15.0
        legacy = json.dumps({'command': [command], 'cwd': cwd, 'timeout_s': timeout},
                            sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        job_id = 'legacy-job'
        with manager._lock:
            manager._db.execute(
                'INSERT INTO jobs(job_id,request_id,args_hash,command_json,cwd,timeout_s,state,created,finished) '
                'VALUES(?,?,?,?,?,?,?,?,?)',
                (job_id, 'legacy-request', hashlib.sha256(legacy.encode('utf-8')).hexdigest(),
                 json.dumps([command]), cwd, timeout, 'outcome_unknown', time.time(), time.time()),
            )
        replay = manager.start('legacy-request', command, tmp_path, timeout)
        assert replay['job_id'] == job_id
        assert replay['state'] == 'outcome_unknown'
        assert not marker.exists()
    finally:
        manager.close()


def test_cmd_shell_emits_utf8(tmp_path):
    manager = JobManager(tmp_path / 'runtime')
    try:
        command = f'"{sys.executable}" -c "print(\'shell-雪\')"'
        started = manager.start('cmd-utf8', command, tmp_path, 15, shell='cmd')
        result = terminal(manager, started['job_id'])
        assert result['state'] == 'succeeded', result
        assert 'shell-雪' in result['output']
    finally:
        manager.close()


def test_git_bash_emits_utf8_when_installed(tmp_path):
    try:
        shells.resolve_shell('bash')
    except RuntimeError:
        pytest.skip('Git Bash is not installed')
    manager = JobManager(tmp_path / 'runtime')
    try:
        started = manager.start('bash-utf8', "printf 'bash-雪\\n'", tmp_path, 15, shell='bash')
        result = terminal(manager, started['job_id'])
        assert result['state'] == 'succeeded', result
        assert 'bash-雪' in result['output']
    finally:
        manager.close()


def test_git_bash_discovery_is_relative_to_git_not_path_bash(tmp_path, monkeypatch):
    install = tmp_path / 'Git'
    git = install / 'cmd' / 'git.exe'
    bash = install / 'bin' / 'bash.exe'
    git.parent.mkdir(parents=True)
    bash.parent.mkdir(parents=True)
    git.touch()
    bash.touch()

    monkeypatch.setattr(shells.shutil, 'which', lambda name: str(git) if name in ('git.exe', 'git') else 'C:/Windows/System32/bash.exe')

    assert shells.resolve_shell('bash') == ('bash', str(bash))


def test_powershell_resolution_prefers_pwsh(monkeypatch):
    monkeypatch.setattr(shells.shutil, 'which', lambda name: {'pwsh.exe': 'C:/pwsh.exe', 'powershell.exe': 'C:/old.exe'}.get(name))
    assert shells.resolve_shell('powershell') == ('powershell', 'C:/pwsh.exe')
