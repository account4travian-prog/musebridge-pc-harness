"""Pure text decoding and edit planning for workspace file tools."""
from __future__ import annotations

from dataclasses import dataclass
import difflib
from collections import deque
from io import StringIO
import json
from pathlib import PurePath
import re


_UTF8_BOM = b'\xef\xbb\xbf'
_UTF16_LE_BOM = b'\xff\xfe'
_UTF16_BE_BOM = b'\xfe\xff'
_IMAGE_EXTENSIONS = {'.bmp', '.gif', '.ico', '.jpeg', '.jpg', '.png', '.tif', '.tiff', '.webp'}
_BINARY_EXTENSIONS = {'.docx', '.pdf'}
_LINE_TRUNCATION = ' … [line truncated]'


class FileTextError(ValueError):
    """A known prepublication file-text failure with a safe recovery hint."""

    def __init__(self, code: str, hint: str):
        self.code = code
        self.hint = hint
        self.no_effect = True
        super().__init__(hint)


@dataclass(frozen=True)
class TextDocument:
    """Decoded text metadata and the exact source bytes used for editing."""

    binary: bool
    kind: str
    encoding: str | None
    bom: bytes
    newline: str
    text: str | None
    original_bytes: bytes

    def read_lines(self, start_line: int = 1, max_lines: int = 400,
                   budget: int = 20000) -> dict:
        """Return numbered, bounded text lines or a binary-file notice."""
        if self.binary:
            return _binary_payload(self.original_bytes, self.kind)
        if type(start_line) is not int or start_line < 1:
            raise ValueError('start_line must be a positive integer')
        if type(max_lines) is not int or max_lines < 1:
            raise ValueError('max_lines must be a positive integer')
        if type(budget) is not int or budget < 1:
            raise ValueError('budget must be a positive integer')

        text = self.text or ''
        total_lines = text.count('\n') + (1 if text and not text.endswith('\n') else 0)
        output = []
        used = 2  # JSON string quotes; the budget measures serialized bytes.
        line_number = start_line
        stop = min(total_lines, start_line + max_lines - 1)
        cursor = _line_offset(text, start_line) if start_line <= total_lines else len(text)
        while line_number <= stop:
            newline = text.find('\n', cursor)
            if newline < 0:
                line = text[cursor:]
                next_cursor = len(text)
            else:
                line = text[cursor:newline]
                next_cursor = newline + 1
            numbered = _numbered_line(line_number, line)
            size = _json_content_size(numbered) + (2 if output else 0)
            if used + size > budget:
                if not output:
                    numbered = _clip_numbered_line(line_number, line, budget)
                    if numbered:
                        output.append(numbered)
                        used += _json_content_size(numbered)
                        line_number += 1
                break
            output.append(numbered)
            used += size
            line_number += 1
            cursor = next_cursor

        more = None
        if line_number <= total_lines:
            more = f'file.read start_line={line_number}'
        return {'binary': False, 'kind': 'text', 'encoding': _encoding_label(self),
                'text': '\n'.join(output), 'total_lines': total_lines,
                'start_line': start_line, 'more': more}

    def encode_text(self, text: str) -> bytes:
        """Encode normalized text using the document's original encoding."""
        if self.binary or self.encoding is None:
            raise FileTextError('binary', 'Binary content cannot be edited as text.')
        if not isinstance(text, str):
            raise ValueError('text must be a string')
        normalized = _normalize_newlines(text)
        encoded = normalized.replace('\n', self.newline).encode(self.encoding, errors='strict')
        return self.bom + encoded

    def apply_edits(self, edits: list[dict]) -> tuple[bytes, dict]:
        """Apply ordered exact/range edits in memory; return bytes only on success."""
        if self.binary or self.text is None:
            raise FileTextError('binary', 'Binary content cannot be edited as text.')
        if not isinstance(edits, list) or not edits:
            raise ValueError('edits must be a non-empty list')

        working = self.text
        tracked_spans = []
        total_regions = 0
        diagnostics = []
        for index, edit in enumerate(edits):
            if not isinstance(edit, dict):
                raise ValueError('each edit must be an object')
            if 'start_line' in edit or 'end_line' in edit:
                working, tracked_spans, new_spans, count, omitted, ignored = _apply_line_edit(
                    working, edit, tracked_spans)
            else:
                working, tracked_spans, new_spans, count, omitted, ignored = _apply_text_edit(
                    working, edit, tracked_spans)
            tracked_spans.extend(new_spans)
            total_regions += count
            if ignored:
                diagnostics.append({'edit_index': index,
                                    'matched_ignoring_trailing_whitespace': True})

        selected_spans = _select_first_last(tracked_spans)
        changed_regions = [_region_payload(working, start, end, ignored)
                           for start, end, ignored in selected_spans]
        omitted_regions = max(0, total_regions - len(changed_regions))
        result = {'changed_regions': changed_regions, 'diagnostics': diagnostics,
                  'encoding': _encoding_label(self), 'newline': self.newline}
        if omitted_regions:
            result['changed_regions_omitted'] = omitted_regions
            result['changed_regions_hint'] = 'Use file.read around the first and last changed regions for context.'
        return self.encode_text(working), result


def decode_document(data: bytes, path: str = '') -> TextDocument:
    """Decode common UTF text formats, retaining original bytes and encoding."""
    if not isinstance(data, bytes):
        raise TypeError('data must be bytes')
    kind = _binary_kind(data, path)
    if kind != 'text':
        return TextDocument(True, kind, None, b'', '\n', None, data)

    encoding = 'utf-8'
    bom = b''
    content = data
    if data.startswith(_UTF8_BOM):
        encoding = 'utf-8'
        bom = _UTF8_BOM
        content = data[len(_UTF8_BOM):]
    elif data.startswith(_UTF16_LE_BOM):
        encoding = 'utf-16-le'
        bom = _UTF16_LE_BOM
        content = data[len(_UTF16_LE_BOM):]
    elif data.startswith(_UTF16_BE_BOM):
        encoding = 'utf-16-be'
        bom = _UTF16_BE_BOM
        content = data[len(_UTF16_BE_BOM):]
    else:
        encoding = _detect_utf16_without_bom(data) or 'utf-8'

    try:
        text = content.decode(encoding, errors='strict')
    except UnicodeDecodeError:
        return TextDocument(True, 'binary', None, b'', '\n', None, data)
    if '\0' in text:
        return TextDocument(True, 'binary', None, b'', '\n', None, data)
    newline = _dominant_newline(text)
    return TextDocument(False, 'text', encoding, bom, newline,
                        _normalize_newlines(text), data)


def _binary_kind(data: bytes, path: str) -> str:
    suffix = PurePath(str(path).replace('\\', '/')).suffix.casefold()
    if suffix in _IMAGE_EXTENSIONS:
        return 'image'
    if suffix == '.pdf' or data.startswith(b'%PDF-'):
        return 'pdf'
    if suffix == '.docx':
        return 'docx'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image'
    if suffix in {'.jpg', '.jpeg'} or data.startswith(b'\xff\xd8\xff'):
        return 'image'
    if suffix == '.gif' or data.startswith((b'GIF87a', b'GIF89a')):
        return 'image'
    return 'text'


def _binary_payload(data: bytes, kind: str) -> dict:
    hints = {
        'image': 'Use screen.look to inspect image content.',
        'pdf': 'Use artifact.add, then artifact.read, to inspect PDF content.',
        'docx': 'Use artifact.add, then artifact.read, to inspect document content.',
    }
    return {'binary': True, 'size': len(data), 'kind': kind,
            'hint': hints.get(kind, 'Binary content cannot be read as text.')}


def _encoding_label(document: TextDocument) -> str:
    names = {'utf-8': 'UTF-8', 'utf-16-le': 'UTF-16 LE', 'utf-16-be': 'UTF-16 BE'}
    label = names.get(document.encoding, str(document.encoding))
    return label + (' BOM' if document.bom else '')


def _detect_utf16_without_bom(data: bytes) -> str | None:
    sample = data[:512]
    if len(sample) < 4:
        return None
    even_zeros = sum(byte == 0 for byte in sample[0::2])
    odd_zeros = sum(byte == 0 for byte in sample[1::2])
    even_count = (len(sample) + 1) // 2
    odd_count = len(sample) // 2
    if odd_zeros >= max(2, odd_count // 3) and even_zeros <= even_count // 10:
        return 'utf-16-le'
    if even_zeros >= max(2, even_count // 3) and odd_zeros <= odd_count // 10:
        return 'utf-16-be'
    return None


def _dominant_newline(text: str) -> str:
    crlf = text.count('\r\n')
    without_crlf = text.replace('\r\n', '')
    lf = without_crlf.count('\n')
    cr = without_crlf.count('\r')
    if crlf >= lf and crlf >= cr and crlf:
        return '\r\n'
    if cr > lf and cr:
        return '\r'
    return '\n'


def _normalize_newlines(text: str) -> str:
    return text.replace('\r\n', '\n').replace('\r', '\n')


def _line_contents(text: str) -> list[str]:
    if not text:
        return []
    lines = text.split('\n')
    if text.endswith('\n'):
        lines.pop()
    return lines


def _numbered_line(number: int, line: str) -> str:
    if len(line) > 2000:
        line = line[:2000] + _LINE_TRUNCATION
    return f'{number:5d}| {line}'


def _json_content_size(text: str) -> int:
    """Count bytes in a JSON-escaped string excluding its quote pair."""
    encoded = json.dumps(text, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    return len(encoded) - 2


def _clip_numbered_line(number: int, line: str, budget: int) -> str:
    prefix = f'{number:5d}| '
    marker = ' … [line clipped by read budget]'
    if _json_content_size(prefix + marker) > budget - 2:
        return ''
    output = [prefix]
    used = _json_content_size(prefix)
    for character in line:
        char_size = _json_content_size(character)
        if used + char_size + _json_content_size(marker) > budget - 2:
            break
        output.append(character)
        used += char_size
    return ''.join(output) + marker


def _iter_matches(text: str, needle: str):
    if not needle:
        return
    position = 0
    while True:
        position = text.find(needle, position)
        if position < 0:
            return
        yield position
        position += len(needle)


def _canonical_trailing_whitespace(text: str) -> tuple[str, list[int]]:
    """Build an rstrip-per-line view and a boundary-to-source offset map."""
    chars = []
    boundaries = [0]
    cursor = 0
    while cursor < len(text):
        newline = text.find('\n', cursor)
        line_end = len(text) if newline < 0 else newline
        raw_line = text[cursor:line_end]
        trimmed = raw_line.rstrip(' \t')
        for index, character in enumerate(trimmed):
            chars.append(character)
            boundaries.append(cursor + index + 1)
        if newline >= 0:
            chars.append('\n')
            boundaries.append(newline + 1)
            cursor = newline + 1
        else:
            cursor = len(text)
    return ''.join(chars), boundaries


def _line_number(text: str, position: int) -> int:
    return text.count('\n', 0, max(0, position)) + 1


def _line_offset(text: str, line_number: int) -> int:
    if line_number <= 1:
        return 0
    offset = 0
    for _index in range(line_number - 1):
        newline = text.find('\n', offset)
        if newline < 0:
            return len(text)
        offset = newline + 1
    return offset


def _visible(text: str) -> str:
    return text.replace('\t', '⇥').replace(' ', '·')


def _no_match_hint(text: str, old: str) -> str:
    source_lines = _line_contents(text)
    target_lines = _line_contents(old)[:5]
    width = max(1, len(target_lines))
    target = '\n'.join(line[:240] for line in target_lines)
    windows = []
    window_count = max(1, len(source_lines) - width + 1)
    step = max(1, (window_count + 1999) // 2000)
    starts = list(range(0, window_count, step))
    if starts[-1] != window_count - 1:
        starts.append(window_count - 1)
    for start in starts:
        candidate = '\n'.join(line[:240] for line in source_lines[start:start + width])
        ratio = difflib.SequenceMatcher(None, target, candidate).ratio()
        windows.append((ratio, start, source_lines[start:start + width]))
    windows.sort(key=lambda value: (-value[0], value[1]))
    closest = windows[:3]
    if not closest:
        return 'No literal match was found; the document has no nearby lines.'
    lines = []
    for _ratio, start, content in closest:
        for offset, line in enumerate(content):
            lines.append(f'{start + offset + 1:5d}| {_visible(line[:240])}')
    return 'No literal match. Closest lines (spaces shown as ·, tabs as ⇥):\n' + '\n'.join(lines)


def _region_payload(text: str, start: int, end: int,
                    matched_ignoring_trailing_whitespace: bool) -> dict:
    start_line = _line_number(text, start)
    end_line = max(start_line, _line_number(text, end))
    total_lines = text.count('\n') + (1 if text and not text.endswith('\n') else 0)
    first = max(1, start_line - 3)
    last = min(total_lines, end_line + 3)
    snippet_lines = []
    cursor = _line_offset(text, first) if first <= total_lines else len(text)
    for number in range(first, last + 1):
        newline = text.find('\n', cursor)
        if newline < 0:
            line = text[cursor:]
            cursor = len(text)
        else:
            line = text[cursor:newline]
            cursor = newline + 1
        snippet_lines.append(_numbered_line(number, line))
    snippet = '\n'.join(snippet_lines)
    return {'start_line': start_line, 'end_line': end_line, 'text': snippet,
            'matched_ignoring_trailing_whitespace': matched_ignoring_trailing_whitespace}


def _apply_text_edit(text: str, edit: dict, prior_spans) -> tuple[str, list, list, int, int, bool]:
    old = edit.get('old')
    new = edit.get('new')
    replace_all = edit.get('replace_all', False)
    if not isinstance(old, str) or not isinstance(new, str) or not old:
        raise ValueError('text edits require a non-empty old string and a new string')
    if type(replace_all) is not bool:
        raise ValueError('replace_all must be a boolean')
    old = _normalize_newlines(old)
    new = _normalize_newlines(new)
    exact_matches = _iter_matches(text, old)
    first_exact = next(exact_matches, None)
    ignored_trailing = False
    if first_exact is None:
        canonical, boundaries = _canonical_trailing_whitespace(text)
        canonical_old = _canonical_trailing_whitespace(old)[0]
        first_canonical = next(_iter_matches(canonical, canonical_old), None) if canonical_old else None
        if first_canonical is None:
            raise FileTextError('no_match', _no_match_hint(text, old))
        ignored_trailing = True
        intervals = ((boundaries[position], boundaries[position + len(canonical_old)])
                     for position in _iter_matches(canonical, canonical_old))
    else:
        intervals = ((position, position + len(old))
                     for position in _iter_matches(text, old))

    first_interval = next(intervals, None)
    if first_interval is None:
        raise FileTextError('no_match', _no_match_hint(text, old))
    second_interval = next(intervals, None)
    if second_interval is not None and not replace_all:
        line_numbers = ', '.join(str(_line_number(text, start))
                                 for start, _end in (first_interval, second_interval))
        raise FileTextError('ambiguous_match',
                            f'Match occurs on lines {line_numbers} and possibly more; set replace_all=true or narrow old.')
    if replace_all:
        if ignored_trailing:
            intervals = ((boundaries[position], boundaries[position + len(canonical_old)])
                         for position in _iter_matches(canonical, canonical_old))
        else:
            intervals = ((position, position + len(old))
                         for position in _iter_matches(text, old))
    else:
        intervals = iter((first_interval,))
    new_text, mapped_spans, new_spans, count, omitted = _apply_replacements(
        text, intervals, new, prior_spans, ignored_trailing)
    return new_text, mapped_spans, new_spans, count, omitted, ignored_trailing


def _apply_line_edit(text: str, edit: dict, prior_spans) -> tuple[str, list, list, int, int, bool]:
    start_line = edit.get('start_line')
    end_line = edit.get('end_line')
    new = edit.get('new')
    if type(start_line) is not int or type(end_line) is not int:
        raise ValueError('start_line and end_line must be integers')
    if not isinstance(new, str):
        raise ValueError('new must be a string')
    lines = _line_contents(text)
    if start_line < 1 or end_line < start_line - 1 or start_line > len(lines) + 1 or end_line > len(lines):
        raise FileTextError('line_range', 'Line range is outside the current document; read it again before editing.')
    start_index = start_line - 1
    end_index = end_line
    replacement = _line_contents(_normalize_newlines(new))
    updated_lines = lines[:start_index] + replacement + lines[end_index:]
    final_newline = text.endswith('\n')
    updated = '\n'.join(updated_lines) + ('\n' if final_newline and updated_lines else '')
    starts = [0]
    for index, line in enumerate(lines):
        has_terminator = index < len(lines) - 1 or final_newline
        starts.append(starts[-1] + len(line) + (1 if has_terminator else 0))
    old_start = starts[start_index]
    old_end = starts[end_index]
    prefix = text[:old_start]
    suffix = text[old_end:]
    new_end = len(updated) - len(suffix) if suffix else len(updated)
    replacement_text = updated[len(prefix):new_end]
    updated, mapped_spans, new_spans, count, omitted = _apply_replacements(
        text, ((old_start, old_end),), replacement_text, prior_spans, False)
    return updated, mapped_spans, new_spans, count, omitted, False


def _apply_replacements(text: str, intervals, replacement: str, prior_spans,
                        ignored_trailing: bool) -> tuple[str, list, list, int, int]:
    """Apply sorted non-overlapping intervals in one forward string pass."""
    endpoints = []
    for index, (start, end, _ignored) in enumerate(prior_spans):
        endpoints.append((start, index, 'start'))
        endpoints.append((end, index, 'end'))
    endpoints.sort(key=lambda item: item[0])
    mapped = [[None, None] for _span in prior_spans]
    endpoint_index = 0
    output = StringIO()
    output_length = 0
    source_cursor = 0
    count = 0
    first = []
    last = deque(maxlen=10)

    for start, end in intervals:
        if start < source_cursor or end < start or end > len(text):
            raise ValueError('edit matches overlap or fall outside the document')
        unchanged = text[source_cursor:start]
        output.write(unchanged)
        output_length += len(unchanged)
        while endpoint_index < len(endpoints) and endpoints[endpoint_index][0] < start:
            position, span_index, role = endpoints[endpoint_index]
            mapped[span_index][0 if role == 'start' else 1] = output_length - (start - position)
            endpoint_index += 1
        new_start = output_length
        new_end = new_start + len(replacement)
        while endpoint_index < len(endpoints) and endpoints[endpoint_index][0] <= end:
            position, span_index, role = endpoints[endpoint_index]
            if start == end:
                mapped_value = new_end if role == 'start' else new_start
            elif position == end:
                mapped_value = new_end
            elif position == start:
                mapped_value = new_start
            else:
                mapped_value = new_start if role == 'start' else new_end
            mapped[span_index][0 if role == 'start' else 1] = mapped_value
            endpoint_index += 1
        output.write(replacement)
        output_length = new_end
        source_cursor = end
        span = (new_start, new_end, ignored_trailing)
        if count < 10:
            first.append((count, span))
        last.append((count, span))
        count += 1

    output.write(text[source_cursor:])
    output_length += len(text) - source_cursor
    while endpoint_index < len(endpoints):
        position, span_index, role = endpoints[endpoint_index]
        mapped[span_index][0 if role == 'start' else 1] = output_length - (len(text) - position)
        endpoint_index += 1

    tail_spans = list(last)
    selected = first + [item for item in tail_spans if item[0] >= 10]
    new_spans = [span for _index, span in selected]
    mapped_spans = [(start, end, ignored) for (start, end, ignored), pair in zip(prior_spans, mapped)
                    for start, end in [(pair[0], pair[1])]]
    return output.getvalue(), mapped_spans, new_spans, count, max(0, count - len(new_spans))


def _select_first_last(spans) -> list[tuple[int, int, bool]]:
    if len(spans) <= 20:
        return list(spans)
    return list(spans[:10]) + list(spans[-10:])
