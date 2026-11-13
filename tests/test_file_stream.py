"""Bounded streaming reader tests for large workspace files."""
from __future__ import annotations

import codecs
import hashlib
from io import BytesIO
import json

import pytest

from musebridge.file_stream import read_text_stream
from musebridge.workspace_text import FileTextError, decode_document


class BoundedHandle(BytesIO):
    """Fail if the reader asks for an unbounded or oversized read."""

    def __init__(self, data):
        super().__init__(data)
        self.largest_read = 0

    def read(self, size=-1):
        assert 0 <= size <= 64 * 1024
        self.largest_read = max(self.largest_read, size)
        return super().read(size)


class ShortReadHandle(BoundedHandle):
    """Return at most two bytes to exercise incremental prefix detection."""

    def read(self, size=-1):
        assert 0 <= size <= 64 * 1024
        self.largest_read = max(self.largest_read, size)
        return BytesIO.read(self, min(size, 2))


def test_stream_matches_document_lines_hash_and_more_pointer():
    raw = b'alpha\r\nbeta\rgamma\nlast'
    payload, digest = read_text_stream(BoundedHandle(raw), start_line=2, max_lines=2)
    reference = decode_document(raw).read_lines(start_line=2, max_lines=2)
    assert payload == reference
    assert payload['text'] == '    2| beta\n    3| gamma'
    assert payload['total_lines'] == 4
    assert payload['more'] == 'file.read start_line=4'
    assert digest == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize(('raw', 'encoding'), [
    (codecs.BOM_UTF8 + 'snow 雪\r\n'.encode('utf-8'), 'UTF-8 BOM'),
    (codecs.BOM_UTF16_LE + 'snow 雪\r\n'.encode('utf-16-le'), 'UTF-16 LE BOM'),
    (codecs.BOM_UTF16_BE + 'snow 雪\r\n'.encode('utf-16-be'), 'UTF-16 BE BOM'),
    ('hello\r\n'.encode('utf-16-le'), 'UTF-16 LE'),
    ('hello\r\n'.encode('utf-16-be'), 'UTF-16 BE'),
])
def test_stream_supports_bom_and_common_bomless_encodings(raw, encoding):
    payload, digest = read_text_stream(BytesIO(raw), 'file.txt')
    assert payload['encoding'] == encoding
    expected = '    1| snow 雪' if 'snow' in payload['text'] else '    1| hello'
    assert payload['text'] == expected
    assert digest == hashlib.sha256(raw).hexdigest()


def test_detection_handles_short_reads_before_codec_selection():
    raw = 'hello\r\n'.encode('utf-16-le')
    payload, digest = read_text_stream(ShortReadHandle(raw), 'file.txt')
    assert payload['encoding'] == 'UTF-16 LE'
    assert payload['text'] == '    1| hello'
    assert digest == hashlib.sha256(raw).hexdigest()


def test_stream_counts_empty_final_and_mixed_newline_lines():
    payload, _digest = read_text_stream(BytesIO(b'a\r\nb\rc\n'))
    assert payload['total_lines'] == 3
    assert payload['text'] == '    1| a\n    2| b\n    3| c'
    empty, _digest = read_text_stream(BytesIO(b''))
    assert empty['total_lines'] == 0
    assert empty['text'] == ''


def test_large_line_is_bounded_and_explicitly_truncated():
    raw = b'x' * 3_000_000 + b'\nnext\n'
    handle = BoundedHandle(raw)
    payload, digest = read_text_stream(handle, 'large.txt')
    assert len(payload['text']) < 2100
    assert '[line truncated]' in payload['text']
    assert payload['total_lines'] == 2
    assert payload['more'] is None
    assert handle.largest_read == 64 * 1024
    assert digest == hashlib.sha256(raw).hexdigest()


def test_json_budget_counts_escaped_unicode_and_clips_first_line_visibly():
    raw = (('🌿\x01\\' * 5000) + '\nsecond\n').encode('utf-8')
    payload, _digest = read_text_stream(BytesIO(raw), budget=6000)
    assert '[line clipped by read budget]' in payload['text']
    assert len(json.dumps(payload, ensure_ascii=True).encode('utf-8')) < 24000
    assert payload['more'] == 'file.read start_line=2'


def test_many_lines_page_and_budget_pointer():
    raw = b'line\n' * 5000
    payload, _digest = read_text_stream(BytesIO(raw))
    assert payload['total_lines'] == 5000
    assert len(payload['text'].splitlines()) == 400
    assert payload['more'] == 'file.read start_line=401'
    small, _digest = read_text_stream(BytesIO(raw), max_lines=100, budget=250)
    assert small['text']
    assert small['more'] is not None
    assert len(json.dumps(small, ensure_ascii=True).encode('utf-8')) < 24000


@pytest.mark.parametrize(('path', 'raw', 'hint'), [
    ('picture.png', b'\x89PNG\r\n\x1a\nrest', 'screen.look'),
    ('report.pdf', b'%PDF-1.7\nrest', 'artifact.add'),
    ('draft.docx', b'PK\x03\x04rest', 'artifact.add'),
    ('unknown.bin', b'\x00\x01bad', 'Binary content'),
])
def test_binary_files_raise_safe_file_text_error(path, raw, hint):
    with pytest.raises(FileTextError) as captured:
        read_text_stream(BytesIO(raw), path)
    assert captured.value.code == 'binary'
    assert captured.value.no_effect
    assert hint in captured.value.hint


def test_malformed_utf8_is_classified_as_binary():
    with pytest.raises(FileTextError, match='Binary content'):
        read_text_stream(BytesIO(b'bad\xfftext'))


def test_reads_with_small_chunks_without_seeking_or_unbounded_read():
    raw = b'first\nsecond\n' * 10000
    handle = BoundedHandle(raw)
    payload, digest = read_text_stream(handle, start_line=9999, max_lines=2)
    assert payload['text'].startswith(' 9999| first\n10000| second')
    assert payload['total_lines'] == 20000
    assert digest == hashlib.sha256(raw).hexdigest()
    assert handle.largest_read == 64 * 1024
