import hashlib
import json

import pytest

from musebridge.catalog import Catalog
from musebridge.file_reads import FileReadStore
from musebridge.tooling import ToolRouter


@pytest.fixture
def harness(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    catalog = Catalog(tmp_path / 'state')
    project = catalog.create_project('Fixture', root)
    cid = catalog.create_conversation('One', project['id'])['id']
    other = catalog.create_conversation('Two', project['id'])['id']
    router = ToolRouter(catalog, tmp_path / 'state')
    try:
        yield root, catalog, router, cid, other
    finally:
        router.close()
        catalog.close()


def call(router, cid, name, **params):
    return router.call(name, {'conversation_id': cid, **params})


def test_numbered_read_pages_and_hidden_versions(harness):
    root, _, router, cid, _ = harness
    (root / 'many.txt').write_text(''.join(f'line {n}\n' for n in range(5000)), encoding='utf-8')
    result = call(router, cid, 'file.read', path='many.txt')
    assert result['ok']
    payload = result['payload']
    assert payload['total_lines'] == 5000 and len(payload['text'].splitlines()) == 400
    assert payload['text'].startswith('    1| line 0')
    assert payload['more'] == 'file.read start_line=401'
    assert not any('sha' in key or 'base64' in key for key in payload)
    assert len(json.dumps(result).encode()) < 24000
    next_page = call(router, cid, 'file.read', path='many.txt', start_line=401, max_lines=2)
    assert next_page['payload']['text'].startswith('  401| line 400')


def test_read_receipts_refuse_unread_cross_chat_and_owner_changes(harness):
    root, catalog, router, cid, other = harness
    path = root / 'code.py'
    path.write_bytes(b'value = 1\n')
    denied = call(router, cid, 'file.edit', path='code.py', old='1', new='2')
    assert denied['error']['code'] == 'not_read'
    assert 'file.read' in denied['error']['hint'] and denied['error']['outcome'] == 'failed'
    assert call(router, cid, 'file.read', path='code.py')['ok']
    assert call(router, other, 'file.edit', path='code.py', old='1', new='2')['error']['code'] == 'not_read'
    path.write_bytes(b'value = 3\n')
    stale = call(router, cid, 'file.edit', path='code.py', old='3', new='4')
    assert stale['error']['code'] == 'changed_since_read'
    assert path.read_bytes() == b'value = 3\n'
    assert router.checkpoints.list(cid)['edits'] == []
    call(router, cid, 'file.read', path='code.py')
    router.files.reads = FileReadStore(catalog)
    assert call(router, cid, 'file.edit', path='code.py', old='3', new='4')['ok']
    row = catalog.rows("SELECT state,error_code FROM tool_calls WHERE error_code='not_read'")[0]
    assert row['state'] == 'failed'


def test_multiedit_is_atomic_preserves_utf16_bom_and_crlf(harness):
    root, _, router, cid, _ = harness
    path = root / 'code.py'
    before = b'\xff\xfe' + 'def value():\r\n    return "old"\r\n# tail\r\n'.encode('utf-16-le')
    path.write_bytes(before)
    call(router, cid, 'file.read', path='code.py')
    first = {'old': 'def value():\n    return "old"', 'new': 'def value():\n    return "new"'}
    failed = call(router, cid, 'file.edit', path='code.py', edits=[first, {'old': 'missing', 'new': 'x'}])
    assert failed['error']['code'] == 'no_match' and 'Closest lines' in failed['error']['hint']
    assert path.read_bytes() == before and router.checkpoints.list(cid)['edits'] == []
    result = call(router, cid, 'file.edit', path='code.py', edits=[first, {'old': '# tail', 'new': '# done'}])
    assert result['ok'] and result['payload']['encoding'] == 'UTF-16 LE BOM'
    expected = before.replace('"old"'.encode('utf-16-le'), '"new"'.encode('utf-16-le')).replace('tail'.encode('utf-16-le'), 'done'.encode('utf-16-le'))
    assert path.read_bytes() == expected
    assert 'sha256' not in result['payload']
    restored = call(router, cid, 'change.restore', edit_id=result['payload']['edit_id'])
    assert restored['ok'] and path.read_bytes() == before


def test_range_insert_and_whitespace_fallback_update_the_receipt(harness):
    root, _, router, cid, _ = harness
    path = root / 'config.txt'
    path.write_bytes(b'first  \r\nsecond\r\nthird\r\n')
    call(router, cid, 'file.read', path='config.txt')
    changed = call(router, cid, 'file.edit', path='config.txt', old='first\nsecond', new='one\ntwo')
    assert changed['ok']
    assert changed['payload']['diagnostics'][0]['matched_ignoring_trailing_whitespace']
    inserted = call(router, cid, 'file.edit', path='config.txt', start_line=2, end_line=1, new='inserted')
    assert inserted['ok']
    assert path.read_bytes() == b'one\r\ninserted\r\ntwo\r\nthird\r\n'


def test_create_parents_script_newlines_and_explicit_overwrite(harness):
    root, _, router, cid, other = harness
    created = call(router, cid, 'file.write', path='nested/run.ps1', text='Write-Output one\nWrite-Output two\n')
    assert created['ok'], created
    assert created['payload']['created_directory_count'] == 1
    assert created['payload']['lines'] == 2
    assert 'folders are retained' in created['payload']['directory_note']
    assert (root / 'nested/run.ps1').read_bytes() == b'Write-Output one\r\nWrite-Output two\r\n'
    denied = call(router, other, 'file.write', path='nested/run.ps1', text='new\n')
    assert denied['error']['code'] == 'not_read'
    replaced = call(router, other, 'file.write', path='nested/run.ps1', text='new\n', overwrite=True)
    assert replaced['ok'] and (root / 'nested/run.ps1').read_bytes() == b'new\r\n'
    assert call(router, other, 'file.edit', path='nested/run.ps1', old='new', new='updated')['ok']


def test_long_display_path_is_explicit_preview_with_bounded_json(harness):
    _, _, router, _, _ = harness
    path = '\u4e2d' * 10000 + '/file.txt'
    payload = router.files._bounded_result({'path': path, 'size': 10})
    assert 'path' not in payload and payload['path_truncated']
    assert payload['path_preview'].endswith('/file.txt')
    assert len(json.dumps(payload, ensure_ascii=True).encode()) < 2200


def test_large_read_streams_but_edit_limit_and_binary_guidance_remain(harness):
    root, _, router, cid, _ = harness
    data = b'first\r\n' + b'x' * (5 * 1024 * 1024) + b'\r\nlast\r\n'
    (root / 'big.txt').write_bytes(data)
    result = call(router, cid, 'file.read', path='big.txt', start_line=3)
    assert result['ok'] and result['payload']['text'] == '    3| last'
    assert result['payload']['total_lines'] == 3
    assert router.files.reads.get(cid, router.files.workspace(cid), 'big.txt') == hashlib.sha256(data).hexdigest()
    denied = call(router, cid, 'file.edit', path='big.txt', old='first', new='new')
    assert denied['error']['code'] == 'too_large'
    assert (root / 'big.txt').read_bytes() == data
    (root / 'big.pdf').write_bytes(b'%PDF-' + b'x' * (5 * 1024 * 1024))
    binary = call(router, cid, 'file.read', path='big.pdf')['payload']
    assert binary['binary'] and binary['kind'] == 'pdf' and 'artifact.add' in binary['hint']


def test_binary_guidance_and_existing_private_write_guards(harness):
    root, _, router, cid, _ = harness
    (root / 'image.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    image = call(router, cid, 'file.read', path='image.png')['payload']
    assert image['binary'] and image['kind'] == 'image' and 'screen.look' in image['hint']
    denied = call(router, cid, 'file.write', path='../outside.txt', text='no')
    assert not denied['ok'] and denied['error']['code'] == 'path_outside_project'
    (root / '.git').mkdir()
    assert not call(router, cid, 'file.write', path='.git/config', text='no')['ok']
    assert not call(router, cid, 'file.write', path='.local/secret', text='no')['ok']


def test_two_megabyte_edit_is_checkpointed_and_restorable(harness):
    root, _, router, cid, _ = harness
    path = root / 'large.txt'
    original = b'marker-A\n' + b'x' * (2 * 1024 * 1024)
    path.write_bytes(original)
    assert call(router, cid, 'file.read', path='large.txt')['ok']
    result = call(router, cid, 'file.edit', path='large.txt', old='marker-A', new='marker-B')
    assert result['ok'] and path.read_bytes().startswith(b'marker-B')
    assert result['payload']['size'] == len(original)
    assert call(router, cid, 'change.restore', edit_id=result['payload']['edit_id'])['ok']
    assert path.read_bytes() == original


def test_after_edit_feedback_failure_keeps_known_publication(harness, monkeypatch):
    root, _, router, cid, _ = harness
    original = router.hooks.run_event

    def fail_after(cid, event, *args, **kwargs):
        if event == 'after_file_edit':
            raise ValueError('synthetic post-publication failure')
        return original(cid, event, *args, **kwargs)

    monkeypatch.setattr(router.hooks, 'run_event', fail_after)
    result = call(router, cid, 'file.write', path='published.txt', text='published')
    assert result['ok'] and 'saved' in result['payload']['feedback_warning']
    assert (root / 'published.txt').read_text() == 'published'
    workspace = router.files.workspace(cid)
    assert router.files.reads.get(cid, workspace, 'published.txt') == hashlib.sha256(b'published').hexdigest()


def test_index_tools_use_the_project_and_validate_boolean_flags(harness):
    root, _, router, cid, _ = harness
    (root / '.gitignore').write_text('ignored/\n', encoding='utf-8')
    (root / 'ignored').mkdir()
    (root / 'ignored/hidden.txt').write_text('needle', encoding='utf-8')
    (root / 'visible.txt').write_text('needle\nother\n', encoding='utf-8')
    listed = call(router, cid, 'file.list')
    assert listed['ok'] and 'hidden.txt' not in listed['payload']['tree']
    searched = call(router, cid, 'file.search', pattern='needle', literal=True)
    assert searched['ok'] and searched['payload']['count'] == 1
    bad = call(router, cid, 'file.search', pattern='needle', literal='false')
    assert not bad['ok'] and bad['error']['code'] == 'bad_arguments'
