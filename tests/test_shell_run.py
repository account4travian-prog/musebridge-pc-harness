import json
from pathlib import Path
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import win32api
import win32con
import win32event

from musebridge.catalog import Catalog
from musebridge.tooling import ToolRouter


@pytest.fixture
def harness(tmp_path):
    root = tmp_path / 'project with spaces'
    root.mkdir()
    catalog = Catalog(tmp_path / 'state')
    project = catalog.create_project('Fixture', root)
    first = catalog.create_conversation('One', project['id'])['id']
    second = catalog.create_conversation('Two', project['id'])['id']
    router = ToolRouter(catalog, tmp_path / 'state')
    try:
        yield root, catalog, router, first, second
    finally:
        router.close()
        catalog.close()


def call(router, cid, name, **params):
    return router.call(name, {'conversation_id': cid, **params})


def test_one_call_returns_output_nonzero_exit_and_utf8(harness):
    root, catalog, router, cid, _ = harness
    result = call(router, cid, 'shell.run', command='Write-Output "café 雪 π"; cmd /c exit 3')
    assert result['ok'], result
    payload = result['payload']
    assert payload['exit_code'] == 3 and not payload['running']
    assert 'café 雪 π' in payload['output']
    rows = catalog.rows('SELECT tool,state FROM tool_calls')
    assert rows == [{'tool': 'shell.run', 'state': 'succeeded'}]
    assert router.registration_specs()['shell.run']['timeout_ms'] == 40000


def test_long_command_returns_handle_and_wait_completes(harness):
    _, _, router, cid, other = harness
    router.shell.inline_cap_s = 0.1
    started = call(router, cid, 'shell.run', command='Start-Sleep -Milliseconds 650; Write-Output done', timeout_s=5)
    assert started['ok'] and started['payload']['running']
    identifier = started['payload']['job_id']
    assert call(router, cid, 'shell.job', action='list')['payload']['jobs']
    assert not call(router, other, 'shell.job', job_id=identifier, action='cancel')['ok']
    result = call(router, cid, 'shell.job', job_id=identifier, action='wait', wait_s=3)
    assert result['ok'] and not result['payload']['running']
    assert result['payload']['exit_code'] == 0 and 'done' in result['payload']['output']
    assert call(router, cid, 'shell.job', action='list')['payload']['jobs'] == []


def test_read_only_and_stop_work_remain_effective_during_inline_wait(harness):
    root, catalog, router, cid, _ = harness
    router.set_profile(cid, 'read_only')
    assert not call(router, cid, 'shell.run', command='Set-Content forbidden no')['ok']
    assert not (root / 'forbidden').exists()
    router.set_profile(cid, 'project')
    router.shell.inline_cap_s = 10
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(call, router, cid, 'shell.run', command='Start-Sleep -Seconds 15', timeout_s=20)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            links = catalog.rows('SELECT job_id FROM job_links WHERE conversation_id=?', (cid,))
            if links and router.jobs.status(links[0]['job_id'])['state'] == 'running':
                break
            time.sleep(0.02)
        else:
            pytest.fail('Owned command never started')
        stopped = time.monotonic()
        router.stop_all()
        assert time.monotonic() - stopped < 2
        result = pending.result(timeout=5)
    assert result['ok'] and result['payload']['state'] == 'cancelled'
    assert not call(router, cid, 'shell.run', command='Set-Content forbidden no')['ok']
    assert not (root / 'forbidden').exists()
    assert call(router, cid, 'shell.job', action='list')['ok']


def test_large_output_keeps_true_edges_under_result_budget(harness):
    root, _, router, cid, _ = harness
    (root / 'large.py').write_text("import sys\nprint('FIRST-UNIQUE',flush=True)\nsys.stdout.write('x'*2_000_000)\nprint('LAST-UNIQUE',flush=True)\n", encoding='utf-8')
    executable = str(Path(sys.executable)).replace("'", "''")
    result = call(router, cid, 'shell.run', command=f"& '{executable}' -u large.py", timeout_s=20)
    assert result['ok'] and result['payload']['exit_code'] == 0
    payload = result['payload']
    assert payload['truncated'] and payload['omitted_chars'] > 1_000_000
    assert 'FIRST-UNIQUE' in payload['output'] and 'LAST-UNIQUE' in payload['output']
    assert len(json.dumps(result, ensure_ascii=True).encode()) < 24000
    page = call(router, cid, 'shell.job', action='status', job_id=payload['job_id'], cursor=0)
    assert page['ok'] and page['payload']['retained_prefix_missing']
    assert page['payload']['cursor'] > 0


def test_shell_cancel_terminates_a_real_grandchild(harness):
    root, _, router, cid, _ = harness
    (root / 'launcher.py').write_text(
        "import subprocess,sys,time\nfrom pathlib import Path\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\n"
        "Path('grandchild.pid').write_text(str(child.pid))\ntime.sleep(30)\n", encoding='utf-8')
    executable = str(Path(sys.executable)).replace("'", "''")
    result = call(router, cid, 'shell.run', command=f"& '{executable}' -u launcher.py", background=True, timeout_s=40)
    identifier = result['payload']['job_id']
    deadline = time.monotonic() + 6
    while not (root / 'grandchild.pid').exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    pid = int((root / 'grandchild.pid').read_text())
    handle = win32api.OpenProcess(win32con.SYNCHRONIZE, False, pid)
    try:
        cancelled = call(router, cid, 'shell.job', action='cancel', job_id=identifier)
        assert cancelled['ok'] and cancelled['payload']['state'] == 'cancelled'
        assert win32event.WaitForSingleObject(handle, 3000) == win32con.WAIT_OBJECT_0
    finally:
        win32api.CloseHandle(handle)


def test_unavailable_shell_reports_a_hint_without_admitting_a_job(harness, monkeypatch):
    from musebridge import shells
    _, catalog, router, cid, _ = harness
    original = shells.resolve_shell

    def resolve(name):
        if name == 'bash':
            raise RuntimeError('shell_unavailable')
        return original(name)

    monkeypatch.setattr(shells, 'resolve_shell', resolve)
    shells.describe_shells.cache_clear()
    try:
        result = call(router, cid, 'shell.run', command='echo no', shell='bash')
        assert not result['ok'] and result['error']['code'] == 'shell_unavailable'
        assert 'Available shells:' in result['error']['hint']
        assert catalog.rows('SELECT job_id FROM job_links') == []
        assert 'bash' not in router.registration_specs()['shell.run']['optional']['shell']['description']
    finally:
        shells.describe_shells.cache_clear()


def test_unicode_log_pages_fit_budget_without_skipping_retained_text(harness):
    root, _, router, cid, _ = harness
    text = '🌿' * 3500
    (root / 'unicode_log.py').write_text(f'print({text!r})\n', encoding='utf-8')
    executable = str(Path(sys.executable)).replace("'", "''")
    result = call(router, cid, 'shell.run', command=f"& '{executable}' unicode_log.py")
    assert result['ok'] and result['payload']['exit_code'] == 0
    cursor = 0
    pieces = []
    for _ in range(10):
        page = call(router, cid, 'shell.job', action='status', job_id=result['payload']['job_id'], cursor=cursor)
        assert page['ok']
        assert len(json.dumps(page, ensure_ascii=True).encode()) < 24000
        payload = page['payload']
        pieces.append(payload['output'])
        assert payload['cursor'] > cursor
        cursor = payload['cursor']
        if not payload['more']:
            break
    assert ''.join(pieces).strip() == text


def test_future_log_cursor_is_rejected_instead_of_moving_backwards(harness):
    _, _, router, cid, _ = harness
    result = call(router, cid, 'shell.run', command='Write-Output fixture')
    assert result['ok']
    identifier = result['payload']['job_id']
    page = call(router, cid, 'shell.job', action='status', job_id=identifier, cursor=10**9)
    assert not page['ok']
    assert 'output_end' in page['error']['message']
    assert call(router, cid, 'shell.job', action='status', job_id=identifier)['payload']['exit_code'] == 0
