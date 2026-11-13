"""MB-28 safety at the callback boundary without model-supplied bookkeeping."""
import json

import pytest

from musebridge.catalog import Catalog
from musebridge.tooling import ToolRouter


@pytest.fixture
def harness(tmp_path):
    catalog = Catalog(tmp_path / 'state')
    roots, cids = [], []
    for title in ('Alpha', 'Beta'):
        root = tmp_path / title
        root.mkdir()
        project = catalog.create_project(title, root)
        cids.append(catalog.create_conversation(title, project['id'])['id'])
        roots.append(root)
    router = ToolRouter(catalog, tmp_path / 'state')
    try:
        yield roots, cids, router
    finally:
        router.close()
        catalog.close()


def test_inference_requires_exactly_one_active_chat_and_reports_candidates(harness):
    roots, cids, router = harness
    empty = router.call('file.list', {}, remote=True)
    assert empty['error']['code'] == 'ambiguous_conversation'
    run = router.execution.begin_run(cids[0])
    result = router.call('file.write', {'path': 'one.txt', 'text': 'one'}, remote=True)
    assert result['ok'] and (roots[0] / 'one.txt').read_text() == 'one'
    assert router.execution.calls(cids[0], run)[0]['state'] == 'succeeded'
    assert not list(roots[1].iterdir())
    router.execution.begin_run(cids[1])
    ambiguous = router.call('file.write', {'path': 'ambiguous.txt', 'text': 'no'}, remote=True)
    assert ambiguous['error']['code'] == 'ambiguous_conversation'
    assert all(title in ambiguous['error']['hint'] for title in ('Alpha', 'Beta'))
    assert not any((root / 'ambiguous.txt').exists() for root in roots)


def test_stop_work_current_profile_and_cross_chat_boundaries_need_no_run_id(harness):
    roots, cids, router = harness
    router.execution.begin_run(cids[0])
    params = {'conversation_id': cids[0], 'path': 'file.txt', 'text': 'safe'}
    router.paused = True
    assert router.call('file.write', params, remote=True)['error']['code'] == 'paused'
    router.paused = False
    assert not router.call('file.write', params, remote=True)['ok']
    router.execution.begin_run(cids[0])
    router.set_profile(cids[0], 'read_only')
    assert router.call('file.write', params, remote=True)['error']['code'] == 'read_only'
    router.set_profile(cids[0], 'project')
    assert router.call('file.write', params, remote=True)['ok']
    wrong = {**params, 'conversation_id': cids[1], 'path': 'wrong.txt'}
    assert not router.call('file.write', wrong, remote=True, expected_conversation_id=cids[0])['ok']
    assert not (roots[1] / 'wrong.txt').exists()


def test_legacy_run_revision_still_refused_and_inference_cannot_cross_callback(harness):
    roots, cids, router = harness
    run = router.execution.begin_run(cids[1])
    params = {'path': 'wrong.txt', 'text': 'no'}
    assert not router.call('file.write', params, remote=True, expected_conversation_id=cids[0])['ok']
    assert not (roots[1] / 'wrong.txt').exists()
    explicit = {**params, 'conversation_id': cids[0], 'run_id': run}
    assert not router.call('file.write', explicit, remote=True)['ok']
    rev = router.execution.policy(cids[0])['revision']
    router.set_profile(cids[0], 'read_only')
    router.set_profile(cids[0], 'project')
    assert not router.call('file.write', {**explicit, 'run_id': None, 'scope_revision': rev}, remote=True)['ok']
    router.execution.state(cids[1], run, 'remote_finished')
    assert not router.call('file.write', {**params, 'conversation_id': cids[1], 'run_id': run}, remote=True)['ok']


def test_registered_schema_omits_run_revision_and_makes_chat_optional(harness):
    _, _, router = harness
    for name, schema in router.registration_specs().items():
        assert 'conversation_id' not in schema['required']
        assert 'run_id' not in {**schema['required'], **schema['optional']}
        assert 'scope_revision' not in {**schema['required'], **schema['optional']}
    params = {'conversation_id': harness[1][0], 'path': 'new.txt', 'text': 'content'}
    router.execution.begin_run(harness[1][0])
    before = params.copy()
    result = router.call('file.write', params, remote=True)
    assert result['ok'] and params == before
    assert 'execution' not in result and 'lifecycle' not in result
    assert 'call_id' not in json.dumps(result)


def test_repeated_failures_are_per_run_and_reset_on_success_or_changed_arguments(harness):
    _, cids, router = harness
    router.execution.begin_run(cids[0])
    args = {'conversation_id': cids[0], 'path': 'missing.txt'}
    first = router.call('file.read', args)
    second = router.call('file.read', args)
    third = router.call('file.read', args)
    assert all(not r['ok'] and r['error']['hint'] for r in (first, second, third))
    assert 'failed 3 times' not in second['error']['hint']
    assert 'failed 3 times' in third['error']['hint']
    assert router.call('file.list', {'conversation_id': cids[0]})['ok']
    assert 'failed 3 times' not in router.call('file.read', args)['error']['hint']
    router.call('file.read', {**args, 'path': 'different.txt'})
    assert 'failed 3 times' not in router.call('file.read', args)['error']['hint']


def test_response_budget_preserves_actual_effect_and_measures_shaped_wire(harness, monkeypatch):
    _, cids, router = harness
    run = router.execution.begin_run(cids[0])
    raw = {'ok': True, 'payload': {'output': 'begin\n' + '\u8449' * 30000 + '\nend',
           'exit_code': 9, 'job_id': 'retained-job'}, 'execution': {'call_id': 'private'}}
    monkeypatch.setattr(router, '_dispatch', lambda *a, **kw: raw)
    result = router.call('shell.run', {'conversation_id': cids[0], 'command': 'synthetic'}, remote=True)
    encoded = json.dumps(result, ensure_ascii=True).encode()
    assert result['ok'] and len(encoded) <= 24000
    assert result['payload']['exit_code'] == 9 and result['payload']['job_id'] == 'retained-job'
    assert result['payload']['output'].startswith('begin') and result['payload']['output'].endswith('end')
    assert 'private' not in json.dumps(result)
    assert router.execution.calls(cids[0], run)[0]['result_bytes'] == len(encoded)
    assert len(raw['payload']['output']) > 30000


def test_malformed_callback_and_local_id_generation_do_not_bypass_policy(harness):
    roots, cids, router = harness
    assert not router.call(['not-a-name'], {})['ok']
    assert router.call('file.read', [], expected_conversation_id=cids[0])['error']['hint']
    router.execution.begin_run(cids[0])
    result = router.call('job.start', {'conversation_id': cids[0], 'command': 'Write-Output generated'}, remote=True)
    assert result['ok']
    job = result['payload']['job_id']
    assert router.call('job.wait', {'conversation_id': cids[0], 'job_id': job})['ok']
    router.set_profile(cids[0], 'read_only')
    denied = router.call('job.start', {'conversation_id': cids[0], 'command': 'Set-Content prohibited yes'}, remote=True)
    assert not denied['ok'] and not (roots[0] / 'prohibited').exists()


def test_generated_effect_ids_do_not_restart_identical_precheck(harness):
    roots, cids, router = harness
    cid = cids[0]
    project_id = router.catalog.conversation(cid)['project_id']
    router.hooks.set_hook('project:' + project_id, 'count', 'before_tool',
        "[IO.File]::AppendAllText((Join-Path (Get-Location) 'hook-count.txt'),'x')",
        enabled=True, tool_filter=['job.start'])
    router.execution.begin_run(cid)
    params = {'conversation_id': cid, 'command': 'Write-Output independent-effect'}
    first = router.call('job.start', params, remote=True)
    second = router.call('job.start', params, remote=True)
    assert first['ok'] and second['ok']
    assert first['payload']['job_id'] != second['payload']['job_id']
    assert (roots[0] / 'hook-count.txt').read_text() == 'x'
    assert 'request_id' not in params


def test_empty_file_and_search_results_are_explicit(harness):
    roots, cids, router = harness
    (roots[0] / 'empty.txt').write_bytes(b'')
    result = router.call('file.read', {'conversation_id': cids[0], 'path': 'empty.txt'})
    assert result['ok'] and result['payload']['message'] == 'File is empty.'
    result = router.call('file.search', {'conversation_id': cids[0], 'pattern': 'absent'})
    assert result['ok'] and result['payload']['message'] == 'No matches.'
