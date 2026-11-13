"""Bounded-memory readers for large workspace text files."""
from __future__ import annotations

import codecs
import hashlib
import json
from typing import BinaryIO

from .workspace_text import FileTextError, _binary_kind, _detect_utf16_without_bom


_CHUNK_SIZE = 64 * 1024
_LINE_LIMIT = 2000
_LINE_TRUNCATION = ' … [line truncated]'
_BUDGET_TRUNCATION = ' … [line clipped by read budget]'


def read_text_stream(binary_handle: BinaryIO, path: str = '', start_line: int = 1,
                     max_lines: int = 400, budget: int = 18000) -> tuple[dict, str]:
    """Read a numbered text page without retaining the file or full lines.

    The digest covers every byte read from the handle, including any BOM. Only
    bounded chunks, at most 2,000 characters for the current requested line,
    and the budget-limited page are retained.
    """
    if type(start_line) is not int or start_line < 1:
        raise ValueError('start_line must be a positive integer')
    if type(max_lines) is not int or max_lines < 1:
        raise ValueError('max_lines must be a positive integer')
    if type(budget) is not int or budget < 1:
        raise ValueError('budget must be a positive integer')

    digest = hashlib.sha256()
    first_read = binary_handle.read(_CHUNK_SIZE)
    if not isinstance(first_read, bytes):
        raise TypeError('binary_handle must return bytes')
    digest.update(first_read)
    first_buffer = bytearray(first_read)
    eof = not first_read
    while len(first_buffer) < 512 and not eof:
        sample = binary_handle.read(512 - len(first_buffer))
        if not isinstance(sample, bytes):
            raise TypeError('binary_handle must return bytes')
        if not sample:
            eof = True
            break
        digest.update(sample)
        first_buffer.extend(sample)
    first = bytes(first_buffer)
    prefix = first[:512]
    kind = _binary_kind(prefix, path)
    if kind != 'text':
        _raise_binary(kind)

    encoding, bom, initial = _select_encoding(first)
    decoder = codecs.getincrementaldecoder(encoding)(errors='strict')
    page_end = start_line + max_lines - 1
    page_lines: list[str] = []
    line_number = 1
    total_lines = 0
    line_prefix: list[str] = []
    line_length = 0
    line_active = False
    pending_cr = False
    budget_full = False
    binary_text = False
    serialized_text_size = 2

    def retain_line() -> bool:
        return start_line <= line_number <= page_end and not budget_full

    def finish_line() -> None:
        nonlocal line_number, total_lines, line_prefix, line_length, line_active
        nonlocal budget_full, serialized_text_size
        total_lines += 1
        if start_line <= line_number <= page_end and not budget_full:
            display = ''.join(line_prefix)
            if line_length > _LINE_LIMIT:
                display += _LINE_TRUNCATION
            numbered = f'{line_number:5d}| {display}'
            inner_size = _json_string_bytes(numbered) - 2
            separator_cost = 2 if page_lines else 0
            if serialized_text_size + inner_size + separator_cost <= budget:
                page_lines.append(numbered)
                serialized_text_size += inner_size + separator_cost
            elif not page_lines:
                clipped = _fit_first_line(line_number, display, budget)
                if clipped:
                    page_lines.append(clipped)
                budget_full = True
            else:
                budget_full = True
        line_number += 1
        line_prefix = []
        line_length = 0
        line_active = False

    def consume_text(text: str) -> None:
        nonlocal pending_cr, line_length, line_active, binary_text
        if binary_text:
            return
        for char in text:
            if char == '\x00':
                binary_text = True
                continue
            if pending_cr:
                pending_cr = False
                if char == '\n':
                    continue
            if char == '\r' or char == '\n':
                finish_line()
                pending_cr = char == '\r'
                continue
            line_active = True
            line_length += 1
            if retain_line() and len(line_prefix) < _LINE_LIMIT:
                line_prefix.append(char)

    try:
        if initial:
            consume_text(decoder.decode(initial, final=False))
        if not eof:
            while True:
                chunk = binary_handle.read(_CHUNK_SIZE)
                if not isinstance(chunk, bytes):
                    raise TypeError('binary_handle must return bytes')
                if not chunk:
                    break
                digest.update(chunk)
                consume_text(decoder.decode(chunk, final=False))
        consume_text(decoder.decode(b'', final=True))
    except UnicodeDecodeError:
        _raise_binary('binary')
    if binary_text:
        _raise_binary('binary')
    if line_active:
        finish_line()

    text = '\n'.join(page_lines)
    next_line = start_line + len(page_lines)
    if budget_full:
        more = f'file.read start_line={next_line}'
    elif next_line <= total_lines:
        more = f'file.read start_line={next_line}'
    else:
        more = None
    # The text-field budget reserves room for the JSON metadata envelope.
    payload = {
        'binary': False,
        'kind': 'text',
        'encoding': _encoding_label(encoding, bool(bom)),
        'text': text,
        'total_lines': total_lines,
        'start_line': start_line,
        'more': more,
    }
    return payload, digest.hexdigest()


def _select_encoding(first: bytes) -> tuple[str, bytes, bytes]:
    """Select the codec using the same BOM and no-BOM rules as workspace_text."""
    if first.startswith(codecs.BOM_UTF8):
        return 'utf-8', codecs.BOM_UTF8, first[len(codecs.BOM_UTF8):]
    if first.startswith(codecs.BOM_UTF16_LE):
        return 'utf-16-le', codecs.BOM_UTF16_LE, first[len(codecs.BOM_UTF16_LE):]
    if first.startswith(codecs.BOM_UTF16_BE):
        return 'utf-16-be', codecs.BOM_UTF16_BE, first[len(codecs.BOM_UTF16_BE):]
    encoding = _detect_utf16_without_bom(first) or 'utf-8'
    return encoding, b'', first


def _encoding_label(encoding: str, bom: bool) -> str:
    """Format codec metadata exactly like TextDocument.read_lines."""
    names = {'utf-8': 'UTF-8', 'utf-16-le': 'UTF-16 LE', 'utf-16-be': 'UTF-16 BE'}
    label = names.get(encoding, encoding)
    return label + (' BOM' if bom else '')


def _json_string_bytes(value: str) -> int:
    return len(json.dumps(value, ensure_ascii=True).encode('utf-8'))


def _fit_first_line(number: int, display: str, budget: int) -> str:
    """Keep a visible marker when the first requested line exceeds the budget."""
    marker = _BUDGET_TRUNCATION
    prefix = f'{number:5d}| '
    minimal = prefix + marker
    if _json_string_bytes(minimal) > budget:
        # Tiny caller budgets still receive an explicit marker where possible.
        minimal = (prefix + marker)[:max(1, budget // 6)]
    output = prefix
    for char in display:
        candidate = output + char + marker
        if _json_string_bytes(candidate) > budget:
            break
        output += char
    clipped = output + marker
    return clipped if _json_string_bytes(clipped) <= budget else minimal


def _raise_binary(kind: str) -> None:
    """Raise the safe inspection hint used by workspace file tools."""
    hints = {
        'image': 'Binary image content cannot be read as text. Use screen.look to inspect image content.',
        'pdf': 'Binary PDF content cannot be read as text. Use artifact.add, then artifact.read, to inspect PDF content.',
        'docx': 'Binary DOCX content cannot be read as text. Use artifact.add, then artifact.read, to inspect document content.',
        'binary': 'Binary content cannot be read as text.',
    }
    raise FileTextError('binary', hints.get(kind, hints['binary']))
