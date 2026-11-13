"""Exercise complete task orchestration with deterministic remote-turn doubles."""
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from musebridge.catalog import Catalog
from musebridge.tooling import ToolRouter
from tools.eval import run_eval


@pytest.fixture
def worker(tmp_path):
    catalog = Catalog(tmp_path / 'state')
    router = ToolRouter(catalog, tmp_path / 'state')
    value = SimpleNamespace(catalog=catalog, router=router, core=None)
    try:
        yield value
    finally:
        router.close()
        catalog.close()


@pytest.mark.parametrize('make_initial_edit', [False, True])
def test_staged_undo_cannot_pass_without_an_observed_first_edit(tmp_path, monkeypatch, worker, make_initial_edit):
    task = next(task for task in run_eval.discover_tasks() if task['id'] == '10_undo_change')
    sends = []
    monkeypatch.setattr(run_eval, '_select_conversation', lambda *_: None)
    monkeypatch.setattr(run_eval, '_await_ready', lambda *_: None)

    def send(_worker, cid, prompt, _deadline):
        sends.append(prompt)
        root = Path(worker.catalog.context(cid)['project']['root'])
        note = root / 'notes.txt'
        if len(sends) == 1 and make_initial_edit:
            note.write_bytes(note.read_bytes().replace(b'original', b'reviewed'))
        elif len(sends) == 2:
            note.write_bytes(note.read_bytes().replace(b'reviewed', b'original'))
        identifier = worker.router.execution.begin_run(cid)
        worker.catalog.event(cid, 'send.context', {'run_id': identifier, 'preamble_bytes': 10})
        worker.router.execution.state(cid, identifier, 'remote_finished')
        return identifier

    monkeypatch.setattr(run_eval, '_send_once', send)
    result = run_eval._run_one(task, worker, tmp_path / 'case')
    assert result['success'] is make_initial_edit
    assert result['intermediate_verified'] is make_initial_edit
    assert result['status'] == ('passed' if make_initial_edit else 'failed-intermediate')
    assert len(sends) == (2 if make_initial_edit else 1)
    assert result['turns'] == len(sends)


def test_unknown_send_is_never_retried_and_next_task_can_complete(tmp_path, monkeypatch, worker):
    task = next(task for task in run_eval.discover_tasks() if task['id'] == '04_find_log')
    monkeypatch.setattr(run_eval, '_select_conversation', lambda *_: None)
    monkeypatch.setattr(run_eval, '_await_ready', lambda *_: None)
    sends = []

    def unknown(_worker, cid, prompt, _deadline):
        sends.append(cid)
        send_id = worker.catalog.admit_send(cid, prompt)
        worker.catalog.settle_send(send_id, 'unknown')
        return None

    monkeypatch.setattr(run_eval, '_send_once', unknown)
    failed = run_eval._run_one(task, worker, tmp_path / 'first')
    assert failed['status'] == 'unknown-send' and not failed['success']
    assert len(sends) == 1

    def success(_worker, cid, prompt, _deadline):
        sends.append(cid)
        root = Path(worker.catalog.context(cid)['project']['root'])
        (root / 'answer.txt').write_text('2026-11-12T10:34:56Z', encoding='utf-8')
        identifier = worker.router.execution.begin_run(cid)
        worker.catalog.event(cid, 'send.context', {'run_id': identifier, 'preamble_bytes': 10})
        worker.router.execution.state(cid, identifier, 'remote_finished')
        return identifier

    monkeypatch.setattr(run_eval, '_send_once', success)
    passed = run_eval._run_one(task, worker, tmp_path / 'second')
    assert passed['status'] == 'passed' and passed['success']
    assert len(sends) == 2 and sends[0] != sends[1]


def test_native_failure_retains_placement_evidence():
    task = {'id': '11_desktop_form', 'limits': {'fixture': 'desktop', 'core': False, 'max_turns': 4},
            '_secondary_display_verified': True}
    result = run_eval._task_result(task, 'failed-timeout', time.monotonic(), run_eval._empty_metrics())
    assert not result['success']
    assert result['secondary_display_verified'] is True


def test_no_admission_is_definitely_not_sent(tmp_path, monkeypatch, worker):
    task = next(task for task in run_eval.discover_tasks() if task['id'] == '04_find_log')
    monkeypatch.setattr(run_eval, '_await_ready', lambda *_: None)
    monkeypatch.setattr(run_eval, '_select_conversation', lambda *_: None)
    monkeypatch.setattr(run_eval, '_send_once', lambda *_: None)
    result = run_eval._run_one(task, worker, tmp_path / 'case')
    assert result['status'] == 'not-sent'
    assert result['turns'] == 0
    assert not result['success']
