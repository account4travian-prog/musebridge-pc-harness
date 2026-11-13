"""Adapt structured tool failures to the stock Muse Gadget error-string wire form."""
from __future__ import annotations

import copy
import json
import re
from typing import Any

from .tool_results import normalize_error


MAX_WIRE_BYTES = 24_000
_SAFE_TASK_ID = re.compile(r"^[0-9a-fA-F]{32}$")
_TASK_STATES = {"pending", "active", "blocked", "awaiting_review", "complete"}


def _wire_size(result: dict) -> int:
    return len(json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode("ascii"))


def _safe_task_id(value: Any) -> str | None:
    if isinstance(value, str) and _SAFE_TASK_ID.fullmatch(value):
        return value
    return None


def _error_details(error: Any, name: str) -> tuple[list[dict], list[dict]]:
    if not isinstance(error, dict):
        return [], []
    items = []
    for item in error.get("items", [])[:20] if isinstance(error.get("items"), list) else []:
        if not isinstance(item, dict):
            continue
        entry = {}
        index = item.get("index")
        if type(index) is int and 0 <= index < 1000:
            entry["index"] = index
        task_id = _safe_task_id(item.get("task_id"))
        if task_id:
            entry["task_id"] = task_id
        if isinstance(item.get("error"), dict):
            normalized = normalize_error(item["error"], name)
            entry["error"] = {
                "code": normalized["code"],
                "message": normalized["message"][:180],
                "hint": normalized["hint"][:120],
            }
        items.append(entry)

    tasks = []
    for task in error.get("tasks", [])[:30] if isinstance(error.get("tasks"), list) else []:
        if not isinstance(task, dict):
            continue
        task_id = _safe_task_id(task.get("task_id"))
        if not task_id:
            continue
        state = task.get("state")
        tasks.append({"task_id": task_id,
                      "state": state if isinstance(state, str) and state in _TASK_STATES else "unknown"})
    return items, tasks


def sdk_result(result: dict) -> dict:
    """Return successes unchanged and failures in the SDK's ``error: str`` form.

    Error text is normalized again defensively and the complete returned JSON is
    capped using its ASCII-escaped representation, as used by the wire serializer.
    """
    if not isinstance(result, dict) or result.get("ok") is not False:
        return result

    source = copy.deepcopy(result.get("error", {}))
    normalized = normalize_error(source)
    code = normalized["code"]
    message = normalized["message"][:1000]
    hint = normalized["hint"][:500]
    outcome = source.get("outcome") if isinstance(source, dict) else None
    if outcome == "unknown" or code == "outcome_unknown":
        suffix = "Outcome unknown: inspect current state before retrying; do not replay automatically."
        if suffix.casefold() not in message.casefold() and suffix.casefold() not in hint.casefold():
            message = (message + " " + suffix).strip()

    items, tasks = _error_details(source, "task.set")
    raw_item_count = len(source.get("items", [])) if isinstance(source, dict) and isinstance(source.get("items"), list) else 0
    raw_task_count = len(source.get("tasks", [])) if isinstance(source, dict) and isinstance(source.get("tasks"), list) else 0
    items_omitted = max(0, raw_item_count - len(items))
    tasks_omitted = max(0, raw_task_count - len(tasks))
    truncated = items_omitted > 0 or tasks_omitted > 0

    while True:
        sections = []
        if items or items_omitted:
            detail = {"items": items}
            if items_omitted:
                detail["items_omitted"] = items_omitted
                truncated = True
            sections.append(detail)
        if tasks or tasks_omitted:
            detail = {"current_tasks": tasks}
            if tasks_omitted:
                detail["tasks_omitted"] = tasks_omitted
                truncated = True
            sections.append(detail)
        if truncated:
            sections.append({"truncated": True,
                             "hint": "Use task.list for current task IDs and inspect the rejected task.set entries."})
        text = f"[{code}] {message}\nNext: {hint or 'Inspect bridge.context for the current project and available tools.'}"
        if sections:
            text += "\nDetails (JSON): " + json.dumps(sections, ensure_ascii=False, separators=(",", ":"))
        candidate = {"ok": False, "error": text}
        if _wire_size(candidate) <= MAX_WIRE_BYTES:
            return candidate
        if len(items) >= len(tasks) and items:
            items.pop()
            items_omitted += 1
            truncated = True
        elif tasks:
            tasks.pop()
            tasks_omitted += 1
            truncated = True
        elif len(message) > 80:
            message = message[: max(80, len(message) // 2)]
            truncated = True
        elif len(hint) > 80:
            hint = hint[: max(80, len(hint) // 2)]
            truncated = True
        else:
            fallback = {
                "ok": False,
                "error": f"[{code}] Tool failure details exceeded the response budget.\n"
                         "Next: Inspect task.list or the specific tool result; do not assume success.",
            }
            if _wire_size(fallback) > MAX_WIRE_BYTES:
                fallback["error"] = "[bad_arguments] Tool failed; inspect current state before retrying."
            return fallback
