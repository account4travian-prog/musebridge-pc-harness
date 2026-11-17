"""Offline coverage for the MB-25b harness and its local fixtures."""
from __future__ import annotations

from datetime import datetime, timezone
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request

import pytest

from tools.eval import compare, run_eval


def test_fixed_final_label_cannot_expand_into_a_report_path():
    assert run_eval.validate_label('v1.0')=='v1.0'
    for value in ('../v1.0','v1.0/other','..','v1.0\\other'):
        with pytest.raises(ValueError):run_eval.validate_label(value)


ROOT = Path(__file__).resolve().parents[1]


def test_discovery_selection_and_staged_task_contract():
    tasks = run_eval.discover_tasks()
    assert len(tasks) == 12
    assert len(run_eval.select_tasks(tasks, ['core'])) == 6
    task10 = next(task for task in tasks if task['id'] == '10_undo_change')
    assert task10['followup']
    assert 'change status' in task10['prompt'].lower()
    assert 'undo' in task10['followup'].lower()


def test_intermediate_gate_requires_real_reviewed_change(tmp_path):
    seed = tmp_path / 'seed'
    project = tmp_path / 'project'
    seed.mkdir()
    project.mkdir()
    original = b'title: fixture\nstatus: draft\n'
    (seed / 'notes.txt').write_bytes(original)
    (project / 'notes.txt').write_bytes(original)
    assert not run_eval.verify_intermediate_edit(project, seed)
    (project / 'notes.txt').write_bytes(b'title: fixture\nstatus: reviewed\n')
    assert run_eval.verify_intermediate_edit(project, seed)


def test_checker_environment_is_isolated_and_expected_is_private(monkeypatch):
    monkeypatch.setenv('MUSEBRIDGE_DATA_DIR', 'private-path')
    monkeypatch.delenv('MUSEBRIDGE_EVAL_EXPECTED', raising=False)
    before = dict(os.environ)
    env = run_eval.checker_environment({'code': '123456'})
    assert env['MUSEBRIDGE_EVAL_EXPECTED'] == '{"code":"123456"}'
    assert 'MUSEBRIDGE_DATA_DIR' not in env
    assert dict(os.environ) == before


def test_browser_fixture_emits_code_only_for_checker_and_contract_passes(tmp_path):
    task = next(task for task in run_eval.discover_tasks() if task['id'] == '09_browser_setting')
    project = tmp_path / 'project'
    project.mkdir()
    proc, fixture = run_eval._start_fixture('browser', project, 10)
    try:
        prompt = run_eval._task_prompt(task, fixture)
        assert fixture['confirmation_code'] not in prompt
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        login = urllib.parse.urlencode({'username': 'fixture', 'password': 'test-only'}).encode()
        with opener.open(urllib.request.Request(fixture['url'], data=login)) as response:
            assert response.status == 200
            assert 'Mode' in response.read().decode()
        base = fixture['url'].rsplit('/', 1)[0]

        def save(mode):
            payload = urllib.parse.urlencode({'mode': mode}).encode()
            with opener.open(urllib.request.Request(base + '/save', data=payload)) as response:
                return response.read().decode()

        save('standard')
        (project / 'answer.txt').write_text(fixture['confirmation_code'], encoding='utf-8')
        private_state_path = Path(fixture['private_state_dir']) / 'fixture_state.json'
        standard_state = json.loads(private_state_path.read_text(encoding='utf-8'))
        check = run_eval._run_checker(task, project,
                                      {'confirmation_code': fixture['confirmation_code'],
                                       'fixture_state': standard_state}, 10)
        assert not check['passed']
        save('advanced')
        advanced_state = json.loads(private_state_path.read_text(encoding='utf-8'))
        check = run_eval._run_checker(task, project,
                                      {'confirmation_code': fixture['confirmation_code'],
                                       'fixture_state': advanced_state}, 10)
        assert check['passed']
        assert advanced_state == {'saved': True, 'mode': 'advanced',
                                  'confirmation_code': fixture['confirmation_code']}
        assert not (project / 'fixture_state.json').exists()
    finally:
        run_eval._stop_fixture(proc)


def test_native_fixture_metadata_requires_secondary_display(monkeypatch):
    monkeypatch.setattr(run_eval, '_read_fixture_line', lambda *_: {
        'hwnd': 12, 'pid': 34, 'secondary_screen': False, 'within_secondary': True
    })

    class FakeProcess:
        stdout = None

        def poll(self):
            return 0

    monkeypatch.setattr(run_eval.subprocess, 'Popen', lambda *args, **kwargs: FakeProcess())
    with pytest.raises(run_eval.EvalError, match='secondary display'):
        run_eval._start_fixture('desktop', Path('.'), 1)


def test_centered_fixture_rect_respects_margins():
    from PySide6.QtCore import QRect
    available = QRect(1920, 0, 1920, 1032)
    result = __import__('tools.eval.fixture_app', fromlist=['_centered_rect'])._centered_rect(
        available, 640, 240)
    assert result.center().x() == available.center().x()
    assert result.center().y() == available.center().y()
    assert available.adjusted(20, 20, -20, -20).contains(result)


def test_compare_preserves_missing_and_reports_success_transitions():
    before = {'label': 'base', 'results': [
        {'task_id': '01', 'success': False, 'tools_calls': 2, 'result_bytes': None},
        {'task_id': '02', 'success': True, 'tools_calls': 1}],
        'aggregate': {'success_rate': .5}}
    after = {'label': 'new', 'results': [
        {'task_id': '01', 'success': True, 'tools_calls': 1, 'result_bytes': None},
        {'task_id': '03', 'success': True}], 'aggregate': {'success_rate': .5}}
    result = compare.compare_reports(before, after)
    first = result['tasks'][0]
    assert first['deltas']['success'] == 'no → yes'
    assert first['deltas']['result_bytes'] is None
    assert result['tasks'][1]['status'] == 'missing-after'
    assert result['tasks'][2]['status'] == 'missing-before'
    assert not result['task_sets_match']
    assert 'not a like-for-like comparison' in compare.format_comparison(result)


def test_report_is_immutable_and_sanitized(tmp_path):
    report = {'label': 'offline', 'date': '2026-11-12', 'harness_version': 'x',
              'aggregate': {'tasks': 1, 'successes': 0, 'success_rate': 0,
                            'tools_calls': 0, 'failed_call_rate': None, 'wall_time': 0,
                            'result_bytes': None, 'preamble_bytes': None},
              'results': [{'task_id': '01_example', 'status': 'failed-check', 'success': False,
                           'tools_calls': 0, 'failed_call_rate': None, 'wall_time': 0,
                           'result_bytes': None, 'preamble_bytes': None}]}
    json_path, md_path = run_eval.write_report(report, tmp_path,
                                               now=datetime(2026, 11, 12, tzinfo=timezone.utc))
    assert json_path.is_file() and md_path.is_file()
    with pytest.raises(FileExistsError):
        run_eval.write_report(report, tmp_path,
                              now=datetime(2026, 11, 12, tzinfo=timezone.utc))
