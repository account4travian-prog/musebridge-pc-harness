"""Pure text codec/editor tests for MB-27 workspace file tools."""
from __future__ import annotations

import codecs

import pytest

from musebridge.workspace_text import FileTextError, decode_document


def test_multiline_lf_old_matches_crlf_and_preserves_source_newlines():
    source = b'alpha  \r\nbeta\r\nkeep\r\n'
    document = decode_document(source, 'notes.txt')
    result, payload = document.apply_edits([{'old': 'alpha\nbeta', 'new': 'ALPHA\nBETA'}])
    assert result == b'ALPHA\r\nBETA\r\nkeep\r\n'
    assert payload['changed_regions'][0]['matched_ignoring_trailing_whitespace']
    assert payload['diagnostics'] == [{'edit_index': 0,
                                       'matched_ignoring_trailing_whitespace': True}]


@pytest.mark.parametrize(('source', 'expected_encoding'), [
    (codecs.BOM_UTF8 + 'café\r\n'.encode('utf-8'), 'utf-8'),
    (codecs.BOM_UTF16_LE + 'snow 雪\r\n'.encode('utf-16-le'), 'utf-16-le'),
    (codecs.BOM_UTF16_BE + 'snow 雪\r\n'.encode('utf-16-be'), 'utf-16-be'),
])
def test_bom_encoding_and_newline_roundtrip(source, expected_encoding):
    document = decode_document(source, 'source.txt')
    assert not document.binary
    assert document.encoding == expected_encoding
    assert document.encode_text(document.text) == source


@pytest.mark.parametrize(('source', 'expected_encoding'), [
    ('PowerShell no BOM\r\n'.encode('utf-16-le'), 'utf-16-le'),
    ('PowerShell no BOM\r\n'.encode('utf-16-be'), 'utf-16-be'),
])
def test_detects_common_bomless_utf16(source, expected_encoding):
    document = decode_document(source, 'powershell-output.txt')
    assert document.encoding == expected_encoding
    assert document.text == 'PowerShell no BOM\n'
    assert document.encode_text(document.text) == source


def test_tabs_and_spaces_no_match_hint_shows_whitespace_and_nearby_line():
    document = decode_document(b'def f():\n\treturn 1\n')
    with pytest.raises(FileTextError) as captured:
        document.apply_edits([{'old': 'def f():\n    return 1', 'new': 'def f():\n    return 2'}])
    assert captured.value.code == 'no_match'
    assert captured.value.no_effect is True
    assert '⇥return·1' in captured.value.hint
    assert 'line' in captured.value.hint.lower()


def test_ambiguous_matches_report_lines_and_replace_all_is_supported():
    document = decode_document(b'old\nkeep\nold\n')
    with pytest.raises(FileTextError) as captured:
        document.apply_edits([{'old': 'old', 'new': 'new'}])
    assert captured.value.code == 'ambiguous_match'
    assert '1, 3' in captured.value.hint
    changed, _payload = document.apply_edits([{'old': 'old', 'new': 'new', 'replace_all': True}])
    assert changed == b'new\nkeep\nnew\n'


def test_edit_batch_is_ordered_and_known_failure_has_no_publication_bytes():
    document = decode_document(b'alpha\nbeta\n')
    with pytest.raises(FileTextError) as captured:
        document.apply_edits([
            {'old': 'alpha', 'new': 'changed'},
            {'old': 'missing', 'new': 'never'},
        ])
    assert captured.value.no_effect is True
    assert document.original_bytes == b'alpha\nbeta\n'
    changed, _payload = document.apply_edits([
        {'old': 'alpha', 'new': 'changed'},
        {'old': 'beta', 'new': 'done'},
    ])
    assert changed == b'changed\ndone\n'


def test_line_range_replace_and_insert_at_end():
    document = decode_document(b'one\ntwo\nthree\n')
    changed, payload = document.apply_edits([
        {'start_line': 2, 'end_line': 2, 'new': 'TWO'},
        {'start_line': 4, 'end_line': 3, 'new': 'four'},
    ])
    assert changed == b'one\nTWO\nthree\nfour\n'
    assert payload['changed_regions'][0]['start_line'] == 2
    assert payload['changed_regions'][1]['start_line'] == 4


def test_changed_region_line_numbers_follow_later_earlier_insertions():
    document = decode_document(b'a\nb\nc\nd\n')
    changed, payload = document.apply_edits([
        {'old': 'd', 'new': 'D'},
        {'start_line': 1, 'end_line': 0, 'new': 'top'},
    ])
    assert changed == b'top\na\nb\nc\nD\n'
    assert {region['start_line'] for region in payload['changed_regions']} == {1, 5}


def test_changed_region_line_numbers_follow_later_earlier_deletions():
    document = decode_document(b'a\nb\nc\nd\ne\n')
    changed, payload = document.apply_edits([
        {'old': 'e', 'new': 'E'},
        {'start_line': 1, 'end_line': 2, 'new': ''},
    ])
    assert changed == b'c\nd\nE\n'
    assert {region['start_line'] for region in payload['changed_regions']} == {1, 3}


def test_read_lines_pages_large_document_and_limits_long_lines_and_budget():
    document = decode_document(('line\n' * 5000).encode('utf-8'), 'many.txt')
    page = document.read_lines()
    assert page['total_lines'] == 5000
    assert len(page['text'].splitlines()) == 400
    assert page['more'] == 'file.read start_line=401'
    next_page = document.read_lines(start_line=401, max_lines=400)
    assert next_page['text'].startswith('  401| line')

    long_line = decode_document(('x' * 2500).encode('utf-8')).read_lines()
    assert len(long_line['text'].split(' … [line truncated]')[0].split('| ', 1)[1]) == 2000
    tiny = document.read_lines(max_lines=10, budget=24)
    assert tiny['text']
    assert tiny['more'] is not None


def test_read_budget_counts_json_escaping_and_marks_budget_clipped_lines():
    import json

    line = ('🌿\x01\\' * 1200) + '\n'
    payload = decode_document(line.encode('utf-8')).read_lines(budget=8000)
    assert '[line clipped by read budget]' in payload['text']
    assert len(json.dumps(payload, ensure_ascii=True).encode('utf-8')) < 24000

    default_payload = decode_document(('🌿' * 4000).encode('utf-8')).read_lines()
    assert '[line clipped by read budget]' in default_payload['text']
    assert len(json.dumps(default_payload, ensure_ascii=True).encode('utf-8')) < 24000


def test_replace_all_on_large_repetitive_text_is_linear_and_bounds_snippets():
    import json

    document = decode_document(('x\n' * 100000).encode('utf-8'))
    changed, payload = document.apply_edits([{'old': 'x', 'new': 'y', 'replace_all': True}])
    assert changed == b'y\n' * 100000
    assert len(payload['changed_regions']) <= 20
    assert payload['changed_regions_omitted'] >= 99980
    assert 'file.read' in payload['changed_regions_hint']
    assert len(json.dumps(payload, ensure_ascii=True).encode('utf-8')) < 24000


@pytest.mark.parametrize(('path', 'data', 'kind', 'hint_text'), [
    ('picture.png', b'\x89PNG\r\n\x1a\n\x00', 'image', 'screen.look'),
    ('report.pdf', b'%PDF-1.7\x00', 'pdf', 'artifact.add'),
    ('draft.docx', b'PK\x03\x04\x00', 'docx', 'artifact.add'),
])
def test_binary_payload_reports_kind_size_and_hint(path, data, kind, hint_text):
    document = decode_document(data, path)
    assert document.binary
    assert document.kind == kind
    payload = document.read_lines()
    assert payload['binary'] is True
    assert payload['size'] == len(data)
    assert payload['kind'] == kind
    assert hint_text in payload['hint']


def test_utf8_bom_is_named_in_read_payload():
    document = decode_document(codecs.BOM_UTF8 + b'hello\n')
    assert document.read_lines()['encoding'] == 'UTF-8 BOM'
