import base64
import asyncio
from io import BytesIO
import json
import pytest
import threading
from types import SimpleNamespace

from PIL import Image

from musebridge import execution
from musebridge.catalog import Catalog
from tools.harness_probes import ProbeRouter, require_baseline


def test_probe_tools_are_temporary_and_image_has_the_stock_camera_envelope(tmp_path, monkeypatch):
    original = execution.READ_TOOLS
    monkeypatch.setattr(execution, 'READ_TOOLS', original | {'probe.sleep', 'probe.picture', 'camera.capture'})
    catalog = Catalog(tmp_path / 'state')
    project = catalog.create_project('Probe', tmp_path)
    cid = catalog.create_conversation('Probe', project['id'])['id']
    router = ProbeRouter(catalog, tmp_path / 'state')
    try:
        assert router.registration_specs()['probe.sleep']['timeout_ms'] == 200000
        assert execution.effect_of('probe.picture') == 'read'
        result = router.call('probe.picture', {'conversation_id': cid}, expected_conversation_id=cid, remote=True)
        assert result['ok']
        assert set(result['payload']) == {'format', 'data_base64'}
        assert result['payload']['format'] == 'jpeg-base64'
        jpeg = base64.b64decode(result['payload']['data_base64'], validate=True)
        assert 0 < len(jpeg) < 120000
        image = Image.open(BytesIO(jpeg))
        assert image.size == (640, 200) and image.format == 'JPEG'
        assert router.probe_results[(cid, 'probe.picture', None)]['completed']
        assert not router.call('probe.picture', {'conversation_id': cid}, expected_conversation_id=cid)['ok']
    finally:
        router.close()
        catalog.close()
    assert 'probe.picture' not in original


def test_sleep_probe_is_interruptible_and_does_not_accept_arbitrary_duration(tmp_path, monkeypatch):
    monkeypatch.setattr(execution, 'READ_TOOLS', execution.READ_TOOLS | {'probe.sleep'})
    catalog = Catalog(tmp_path / 'state')
    router = ProbeRouter(catalog, tmp_path / 'state')
    try:
        router.probe_stop.set()
        result = router.call('probe.sleep', {'conversation_id': 'main', 'seconds': 150}, expected_conversation_id='main')
        assert not result['ok'] and result['error']['code'] == 'cancelled'
        assert not router.probe_results[('main', 'probe.sleep', 150)]['completed']
        bad = router.call('probe.sleep', {'conversation_id': 'main', 'seconds': 999}, expected_conversation_id='main')
        assert bad['error']['code'] == 'bad_arguments'
    finally:
        router.close()
        catalog.close()


def test_sleep_result_carries_code_only_after_the_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(execution, 'READ_TOOLS', execution.READ_TOOLS | {'probe.sleep'})
    catalog = Catalog(tmp_path / 'state')
    router = ProbeRouter(catalog, tmp_path / 'state')
    waited = []
    monkeypatch.setattr(router.probe_stop, 'wait', lambda seconds: waited.append(seconds) or False)
    try:
        result = router.call('probe.sleep', {'conversation_id': 'main', 'seconds': 45}, expected_conversation_id='main')
        assert waited == [45]
        assert result['ok'] and result['payload']['slept_s'] == 45
        record = router.probe_results[('main', 'probe.sleep', 45)]
        assert result['payload']['code'] == record['code']
        assert record['completed']
        assert len(record['code']) == 6
        rows = catalog.rows('SELECT result_bytes,error_message FROM tool_calls')
        assert rows[0]['result_bytes'] == len(json.dumps(result, ensure_ascii=True).encode())
        assert rows[0]['error_message'] is None
    finally:
        router.close()
        catalog.close()


def test_baseline_gate_requires_all_six_core_tasks_and_a_valid_scored_run(tmp_path):
    with pytest.raises(RuntimeError, match='v1-baseline'):
        require_baseline(tmp_path)
    report = {'label': 'v1-baseline', 'harness_version': '0.6.1',
              'results': [{'task_id': f'{n:02}_fixture', 'core': True, 'status': 'passed'} for n in range(1, 7)]}
    path = tmp_path / 'v1-baseline-date.json'
    path.write_text(json.dumps(report), encoding='utf-8')
    require_baseline(tmp_path)
    report['results'].pop()
    path.write_text(json.dumps(report), encoding='utf-8')
    with pytest.raises(RuntimeError):
        require_baseline(tmp_path)


def test_cleanup_restores_router_and_unlocks_even_when_worker_close_raises(tmp_path, monkeypatch):
    from tools import harness_probes as probes
    saved_router = probes.tooling.ToolRouter
    saved_tools = execution.READ_TOOLS
    unlocked = []
    reports = []

    class Lock:
        def __init__(self, _path):
            pass

        def setStaleLockTime(self, _value):
            pass

        def tryLock(self, _value):
            return True

        def unlock(self):
            unlocked.append(True)

    class FakeWorker:
        def __init__(self, _store):
            self.router = SimpleNamespace(probe_stop=threading.Event())

        def close(self):
            raise RuntimeError('synthetic cleanup failure')

    async def broken_probe(*_args):
        raise TimeoutError('synthetic unknown result')

    monkeypatch.setattr(probes, '__file__', str(tmp_path / 'tools' / 'probes.py'))
    monkeypatch.setattr(probes, 'require_baseline', lambda: None)
    monkeypatch.setattr(probes, 'Store', lambda: SimpleNamespace(directory=tmp_path))
    monkeypatch.setattr(probes, 'QLockFile', Lock)
    monkeypatch.setattr(probes, 'Worker', FakeWorker)
    monkeypatch.setattr(probes, 'code_probe', broken_probe)
    monkeypatch.setattr(probes, 'write_report', lambda report: reports.append(dict(report)))
    assert asyncio.run(probes.main([])) == 1
    assert unlocked == [True]
    assert reports[-1]['interruption']['replayed'] is False
    assert reports[-1]['cleanup']['error_code'] == 'RuntimeError'
    assert probes.tooling.ToolRouter is saved_router
    assert execution.READ_TOOLS is saved_tools
