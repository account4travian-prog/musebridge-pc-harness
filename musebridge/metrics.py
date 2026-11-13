"""Sanitized numeric tool telemetry; no arguments, output or credential values."""
import json
import re


def result_size(result):
    """Measure the encoded callback result, excluding transport framing."""
    return len(json.dumps(result, ensure_ascii=True).encode('utf-8'))


def error_summary(result, params=None):
    """Keep a short error reason while removing caller values and private paths."""
    if result.get('ok'):
        return None
    error = result.get('error', {})
    message = error.get('message', '') if isinstance(error, dict) else str(error)
    if not isinstance(message, str):
        message = 'Tool failed'
    values = []

    def collect(value):
        if isinstance(value, str) and value:
            values.append(value)
        elif isinstance(value, dict):
            for child in value.values():
                collect(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                collect(child)

    collect(params or {})
    for value in sorted(set(values), key=len, reverse=True):
        pattern = re.escape(value)
        if len(value) < 4:
            pattern = r'(?<!\w)' + pattern + r'(?!\w)'
        message = re.sub(pattern, '[argument]', message, flags=re.IGNORECASE)
    message = re.sub(r"(['\"])(?:[A-Za-z]:[\\/]|\\\\)[^'\"]*\1", '[path]', message)
    message = re.sub(r'\b[A-Za-z]:[\\/][^\r\n]+', '[path]', message)
    message = re.sub(r'https?://\S+', '[url]', message)
    message = re.sub(r'(?i)(?:mgst_|bearer\s+|access_token[=: ]+|refresh_token[=: ]+)\S+', '[credential]', message)
    message = re.sub(r'\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b', '[id]', message)
    return ' '.join(message.split())[:200] or 'Tool failed'
