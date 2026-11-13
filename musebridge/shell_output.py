"""Shape retained shell output at the result boundary without touching its log."""

from __future__ import annotations

import json
import re


_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\](?:[^\x07\x1b]|\x1b(?!\\))*(?:\x07|\x1b\\|$)")
_NO_OUTPUT = "Command produced no output (exit 0)."


def clean_output(text: str) -> str:
    """Remove terminal control sequences and keep the latest CR progress state."""
    text = _OSC.sub("", _CSI.sub("", text))
    text = text.replace("\r\n", "\n")
    return "\n".join(line.rsplit("\r", 1)[-1] for line in text.split("\n"))


def _json_size(text: str) -> int:
    return len(json.dumps(text, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))


def _marker(omitted: int) -> str:
    return (f"\n[Middle output omitted ({omitted} characters); inspect retained log with shell.job, "
            "narrow the command, or redirect future output to a project file.]\n")


def _candidate(head: str, tail: str, head_keep: int, tail_keep: int, omitted: int) -> tuple[str, int]:
    newly_omitted = len(head) - head_keep + len(tail) - tail_keep
    total_omitted = omitted + newly_omitted
    kept_head = head[:head_keep] if head_keep else ""
    kept_tail = tail[-tail_keep:] if tail_keep else ""
    return kept_head + (_marker(total_omitted) if total_omitted else "") + kept_tail, total_omitted


def _allocate(total: int, head_len: int, tail_len: int) -> tuple[int, int]:
    """Retain about 30% of available characters at the head and 70% at tail."""
    head_keep = min(head_len, int(total * 0.30))
    tail_keep = min(tail_len, total - head_keep)
    remaining = total - head_keep - tail_keep
    if remaining:
        extra_tail = min(tail_len - tail_keep, remaining)
        tail_keep += extra_tail
        remaining -= extra_tail
    if remaining:
        head_keep += min(head_len - head_keep, remaining)
    return head_keep, tail_keep


def shape_output(head: str, tail: str, omitted_chars: int = 0, *, max_bytes: int = 20000) -> dict:
    """Return clean, JSON-budgeted output while retaining the head and tail.

    ``head`` and ``tail`` are non-overlapping driver previews. Existing skipped
    characters are supplied in ``omitted_chars``; any further clipping is added
    to that count. The job's raw output ring remains untouched.
    """
    if not isinstance(head, str) or not isinstance(tail, str):
        raise ValueError("head and tail must be strings")
    if type(omitted_chars) is not int or omitted_chars < 0:
        raise ValueError("omitted_chars must be a non-negative integer")
    if type(max_bytes) is not int or max_bytes < _json_size(_NO_OUTPUT):
        raise ValueError("max_bytes is too small for the JSON output notice")

    clean_head, clean_tail = clean_output(head), clean_output(tail)
    if not clean_head and not clean_tail and omitted_chars == 0:
        return {"output": _NO_OUTPUT, "truncated": False, "omitted_chars": 0}

    full, total_omitted = _candidate(clean_head, clean_tail, len(clean_head), len(clean_tail), omitted_chars)
    if _json_size(full) <= max_bytes:
        return {"output": full, "truncated": bool(total_omitted), "omitted_chars": total_omitted}

    if not clean_head and omitted_chars == 0:
        split = int(len(clean_tail) * 0.30)
        clean_head, clean_tail = clean_tail[:split], clean_tail[split:]

    lo, hi = 0, len(clean_head) + len(clean_tail)
    best = None
    while lo <= hi:
        keep = (lo + hi) // 2
        head_keep, tail_keep = _allocate(keep, len(clean_head), len(clean_tail))
        candidate, clipped = _candidate(clean_head, clean_tail, head_keep, tail_keep, omitted_chars)
        if _json_size(candidate) <= max_bytes:
            best = (candidate, clipped)
            lo = keep + 1
        else:
            hi = keep - 1

    if best is None:
        # The requested budget is smaller than the explanatory marker. Keep a
        # minimal honest marker when possible and otherwise an empty JSON string.
        marker = "[output omitted]"
        candidate = marker if _json_size(marker) <= max_bytes else ""
        return {"output": candidate, "truncated": True,
                "omitted_chars": omitted_chars + len(clean_head) + len(clean_tail)}

    output, clipped = best
    return {"output": output, "truncated": True, "omitted_chars": clipped}
