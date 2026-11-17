"""Shape tool responses for safe, bounded delivery to Muse."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

DEFAULT_BUDGET = 24_000
MIN_BUDGET = 512
_ROOT_INTERNAL_KEYS = {"execution", "lifecycle"}
_INTERNAL_KEYS = {"call_id", "lifecycle", "after_edit_hooks", "hook_summary", "hook_summaries"}
_IDENTITY_KEYS = {"job_id", "edit_id", "exit_code", "request_id", "run_id", "invocation_id", "hook_id", "code", "state"}
_BASE64_KEYS = {"base64", "data_base64", "image_base64", "image_data_base64", "encoded_image", "image", "image_data"}
_CODES = {
    'project_busy', 'desktop_unavailable',
    "ambiguous_conversation", "no_project", "read_only", "paused", "needs_desktop_control",
    "owner_active", "not_read", "changed_since_read", "no_match", "ambiguous_match",
    "path_outside_project", "too_large", "stale_ref", "shell_unavailable", "timeout",
    "outcome_unknown", "bad_arguments", "binary", "not_found", "permission_denied",
    "no_active_run", "conversation_changed", "stale_scope", "cancelled",
    "manual_review_required", "check_required", "restore_scope_required", "verification_hook_failed", "hook_outcome_unknown",
}

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(access[_-]?token|refresh[_-]?token|api[_-]?key|client[_-]?secret|password|passwd|secret|authorization)\b"
    r"[\"']?\s*([:=])\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_MUSE_TOKEN = re.compile(r"(?i)\bmgst_[A-Za-z0-9._~+/=-]+")
_URL = re.compile(r"(?i)\bhttps?://[^\s<>\"']+")
_WINDOWS_PATH = re.compile(r"(?i)(?<![\w])(?:[A-Z]:[\\/]|\\\\)[^\r\n<>\"|?*]+")

_DEFAULT_HINTS = {
    'project_busy':'Wait for the other project writer to finish, or create a separate worktree.',
    'manual_review_required': 'Ask the owner to review this task in the local Tasks view; a model cannot approve it.',
    'check_required': 'Provide task.verify(command="...") or store a check with task.set before verifying.',
    'restore_scope_required': 'Review the changes, then pass a project-relative path or a run to change.restore.',
    'verification_hook_failed': 'Inspect bridge.context(section="hooks"); fix the failed pre-check before asking for a receipt.',
    'hook_outcome_unknown': 'Inspect bridge.context(section="hooks") before retrying; the hook may already have run.',
    "ambiguous_conversation": "Pass the conversation_id from bridge.context.",
    "no_project": "Ask the owner to choose a project for this conversation.",
    "read_only": "The owner set Read only. Explain the proposed change and ask them to switch to Project work.",
    "paused": "The owner pressed Stop Work. Stop and report where you got to.",
    "needs_desktop_control": "Ask the owner to enable Desktop control for this project.",
    "owner_active": "The owner is using the PC. Wait and retry once, or ask them.",
    "not_read": "Read the file first with file.read, then retry the edit.",
    "changed_since_read": "Read the file again before editing; its contents changed.",
    "no_match": "Use the closest-text guidance, copy the source exactly, or use start_line/end_line.",
    "ambiguous_match": "Add surrounding text to old or set replace_all if every match should change.",
    "path_outside_project": "Use a relative path inside the selected project. Use shell.run outside it only if the profile allows.",
    "too_large": "Narrow the request with a smaller line range, page, or file.",
    "stale_ref": "Take a new snapshot; references change after the page or window changes.",
    "shell_unavailable": "Choose one of the shells listed by bridge.context.",
    "timeout": "Inspect the returned job with shell.job wait or cancel it; do not start a duplicate command.",
    "outcome_unknown": "Check the result before retrying; the operation may already have happened.",
    "binary": "Use artifact.add and artifact.read for supported documents, or the image-turn route for image pixels.",
    "not_found": "Check the project-relative path with file.list or file.search, then retry with the exact path.",
    "permission_denied": "Check that the selected project permits this operation and that the file is not in use.",
    "no_active_run": "Start a new request in this conversation and bind the tool call to its active run.",
    "conversation_changed": "Use the current conversation_id from bridge.context and retry in that conversation.",
    "stale_scope": "Start a new request using the current conversation and scope revision from bridge.context.",
    "cancelled": "Stop the interrupted operation and report where it got to; do not retry automatically.",
}


def normalize_error(error: Any, name: str = "") -> dict:
    """Return a plan-code error with sanitized, actionable message and hint."""
    if isinstance(error, BaseException):
        raw_code = "outcome_unknown" if getattr(error, "outcome", None) == "unknown" else getattr(error, "code", "")
        raw_message = str(error)
        raw_hint = getattr(error, "hint", "")
    elif isinstance(error, dict):
        raw_code = "outcome_unknown" if error.get("outcome") == "unknown" else error.get("code", "")
        raw_message = error.get("message", "")
        raw_hint = error.get("hint", "")
    else:
        raw_code = ""
        raw_message = str(error or "")
        raw_hint = ""
    code = _classify_error(str(raw_code), str(raw_message))
    message = _sanitize(str(raw_message)).strip() or "The tool could not complete this request."
    hint = _sanitize(str(raw_hint)).strip()
    if not hint or code == "bad_arguments" and not any(token in hint.casefold() for token in ("example", "bridge.context", "expected:")):
        hint = _hint_for(code, name)
    if code == "bad_arguments" and not any(token in hint.casefold() for token in ("example", "bridge.context", "expected:")):
        hint = f"{hint} Check bridge.context for the current contract."
    if code == "bad_arguments" and "binary" in message.casefold() and name.startswith("file."):
        hint = "This is a binary file. Use artifact.add and artifact.read for supported documents, or the image-turn route for image pixels."
    return {"code": code, "message": message[:2000], "hint": hint[:2000]}


def _classify_error(code: str, message: str) -> str:
    normalized = code.strip().casefold().replace("-", "_")
    if normalized in _CODES:
        return normalized
    text = f"{normalized} {message}".casefold()
    rules = (
        (("project_required",), "no_project"),
        (("outcome unknown", "outcome_unknown", "interrupted after", "may or may not have happened"), "outcome_unknown"),
        (("ambiguous conversation", "multiple active conversations"), "ambiguous_conversation"),
        (("no project", "choose a project"), "no_project"),
        (("read_only", "read only", "read-only"), "read_only"),
        (("stop work", "paused"), "paused"),
        (("desktop control", "needs_desktop_control"), "needs_desktop_control"),
        (("owner is using", "owner_active"), "owner_active"),
        (("not_read", "read the file first", "must be read first"), "not_read"),
        (("sha-256 mismatch", "changed since", "stale version", "changed during publication"), "changed_since_read"),
        (("no literal match", "no_match", "found 0 matches"), "no_match"),
        (("ambiguous_match", "found multiple matches", "found 2 matches", "found 3 matches"), "ambiguous_match"),
        (("escapes the selected workspace", "private application data", "outside the project", "path_outside_project"), "path_outside_project"),
        (("too_large", "exceeds the", "file exceeds", "result exceeds"), "too_large"),
        (("stale_ref", "snapshot reference", "reference is stale"), "stale_ref"),
        (("shell_unavailable", "shell was not found", "shell is unavailable"), "shell_unavailable"),
        (("filenotfounderror", "no such file or directory", "file not found"), "not_found"),
        (("permissionerror", "permission denied", "access is denied"), "permission_denied"),
        (("no_active_run", "no active run"), "no_active_run"),
        (("conversation_changed", "conversation changed"), "conversation_changed"),
        (("stale_scope", "scope revision", "run scope changed", "policy revision changed"), "stale_scope"),
        (("user activity was detected", "foreground window changed", "owner_active"), "owner_active"),
        (("cancelled", "canceled", "probe.sleep interrupted"), "cancelled"),
        (("timeout", "timed out", "still running as job"), "timeout"),
    )
    for markers, result in rules:
        if any(marker in text for marker in markers):
            return result
    return "bad_arguments"


def _hint_for(code: str, name: str) -> str:
    if code in _DEFAULT_HINTS:
        return _DEFAULT_HINTS[code]
    examples = {
        'task.set': 'Inspect error.items and current tasks, then correct the rejected entries. Example: task.set(tasks=[{"title":"Run checks"}]).',
        "file.read": 'Example: file.read(path="src/app.py", start_line=1, max_lines=100).',
        "file.edit": 'Example: file.read(path="src/app.py"), then file.edit with old/new.',
        "file.write": 'Example: file.write(path="notes.txt", text="draft\\n").',
        "file.search": 'Example: file.search(pattern="TODO", glob="*.py", context=1).',
        "file.list": 'Example: file.list(path="src", depth=2).',
        "shell.run": 'Check bridge.context for the selected project and shell; narrow the command.',
        "shell.job": 'Use shell.job(action="status", job_id="<returned job_id>").',
        "artifact.read": 'Use artifact.list to inspect owned snapshots, then read a smaller offset and length.',
    }
    return examples.get(name, "Check bridge.context for the selected project and available tools, then retry with a narrower request.")


def _sanitize(text: str) -> str:
    text = _SECRET_ASSIGNMENT.sub(lambda match: match.group(1) + match.group(2) + "[redacted]", text)
    text = _BEARER.sub("Bearer [redacted]", text)
    text = _MUSE_TOKEN.sub("[redacted Muse token]", text)
    text = _URL.sub("[url redacted]", text)
    text = _WINDOWS_PATH.sub("[path redacted]", text)
    return text


def shape_result(result: dict, name: str, budget: int = DEFAULT_BUDGET) -> dict:
    """Remove private envelope fields and bound the complete JSON response."""
    if not isinstance(result, dict):
        result = {"ok": False, "error": {"code": "bad_arguments", "message": "Invalid tool result type."}}
    if type(budget) is not int or budget < MIN_BUDGET:
        raise ValueError(f"budget must be an integer of at least {MIN_BUDGET}")
    source = copy.deepcopy(result)
    if type(source.get("ok")) is not bool:
        return {"ok": False, "error": normalize_error({"code": "bad_arguments",
            "message": "Tool returned a result without a valid success status."}, name)}
    hook_issues = _hook_issues(source)
    shaped = _strip_internal(source, root=True)
    if shaped.get("ok") is False:
        original_error = shaped.get("error", {})
        normalized_error = normalize_error(original_error, name)
        if isinstance(original_error, dict) and original_error.get("outcome") in {"failed", "unknown"}:
            normalized_error["outcome"] = original_error["outcome"]
        if isinstance(original_error, dict) and isinstance(original_error.get("hook_issues"), list):
            normalized_error["hook_issues"] = original_error["hook_issues"]
        if isinstance(original_error,dict):
            for key in ('items','tasks'):
                if isinstance(original_error.get(key),list):
                    normalized_error[key] = original_error[key]
            for item in normalized_error.get('items',[]):
                if isinstance(item,dict) and 'error' in item:
                    item['error'] = normalize_error(item['error'],name)
        shaped = {"ok": False, "error": normalized_error}
        if hook_issues:
            shaped["error"]["hook_issues"] = _merge_hook_issues(shaped["error"].get("hook_issues", []), hook_issues)
    else:
        payload = shaped.get("payload", {})
        shaped = {"ok": True, "payload": payload}
        if hook_issues:
            if isinstance(payload, dict):
                payload["hook_issues"] = _merge_hook_issues(payload.get("hook_issues", []), hook_issues)
                payload["hook_hint"] = "The tool result may still be valid; inspect hook.status before retrying any associated action."
            else:
                shaped["hook_issues"] = _merge_hook_issues(shaped.get("hook_issues", []), hook_issues)
                shaped["hook_hint"] = "The tool result may still be valid; inspect hook.status before retrying any associated action."
    internal_stripped = _strip_internal(source, root=True)
    for key in ("truncated", "hint", "hook_issues", "hook_hint", "payload_omitted_count"):
        if key in internal_stripped and key not in shaped:
            shaped[key] = internal_stripped[key]
    _fit_budget(shaped, name, budget)
    return shaped


def _strip_internal(value: Any, *, root: bool = False) -> Any:
    if isinstance(value, dict):
        return {key: _strip_internal(item) for key, item in value.items()
                if not (isinstance(key, str) and key.casefold() in _INTERNAL_KEYS)
                and not (root and isinstance(key, str) and key.casefold() in _ROOT_INTERNAL_KEYS)}
    if isinstance(value, list):
        return [_strip_internal(item) for item in value]
    return value


def _hook_issues(value: Any) -> list[dict]:
    issues = []
    failure_states = {"failed", "blocked", "unknown", "outcome_unknown", "cancelled", "timed_out"}

    def visit(node):
        if isinstance(node, dict):
            for key, child in node.items():
                if isinstance(key, str) and key.casefold() in {"lifecycle", "after_edit_hooks", "hook_summary", "hook_summaries"}:
                    blocks = child if isinstance(child, list) else [child]
                    for block in blocks:
                        if not isinstance(block, dict):
                            continue
                        for item in block.get("jobs", []):
                            if isinstance(item, dict) and str(item.get("state", "")).casefold() in failure_states:
                                issues.append(_hook_issue(item, "failed"))
                        for item in block.get("skipped", []):
                            if isinstance(item, dict) and _is_hook_rejection(item, failure_states):
                                issues.append(_hook_issue(item, "blocked"))
                else:
                    visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    unique = []
    seen = set()
    for issue in issues:
        marker = (issue.get("hook"), issue.get("job_id"), issue.get("state"), issue.get("reason"))
        if marker not in seen:
            seen.add(marker)
            unique.append(issue)
    return unique


def _is_hook_rejection(item: dict, failure_states: set[str]) -> bool:
    state = str(item.get("state", "")).casefold()
    reason = str(item.get("reason", "")).casefold()
    if any(text in reason for text in ("conversation has no project scope", "no project scope",
            "work is paused", "read_only profile", "read-only profile", "read only profile")):
        return False
    if state in failure_states:
        return True
    return bool(item.get("hook") and reason)


def _hook_issue(item: dict, default_state: str) -> dict:
    issue = {key: item[key] for key in ("hook", "job_id", "invocation_id", "state", "reason") if key in item}
    issue.setdefault("state", default_state)
    if "reason" not in issue:
        issue["reason"] = "Hook did not complete successfully."
    issue["reason"] = _sanitize(str(issue["reason"]))[:500]
    issue["hint"] = "Inspect hook.status for this hook; check the original tool result before retrying."
    return issue


def _merge_hook_issues(existing: Any, added: list[dict]) -> list[dict]:
    combined = list(existing) if isinstance(existing, list) else []
    combined.extend(added)
    unique = []
    seen = set()
    for item in combined:
        marker = json.dumps(item, ensure_ascii=True, sort_keys=True, default=str)
        if marker not in seen:
            seen.add(marker)
            unique.append(item)
    return unique


def _dump(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, default=str).encode("ascii")


def _fit_budget(result: dict, name: str, budget: int) -> None:
    if len(_dump(result)) <= budget:
        return
    _omit_oversized_base64(result)
    hint = _retrieval_hint(name)
    result["truncated"] = True
    result["hint"] = hint
    shortened_strings = set()
    for _ in range(256):
        if len(_dump(result)) <= budget:
            return
        changed = False
        for container, key, text in _string_candidates(result, shortened_strings):
            if _shorten_string(container, key, text, hint, budget):
                shortened_strings.add((id(container), key))
                changed = True
                break
        if changed:
            continue
        for container, key, records in _list_candidates(result):
            if _shorten_list(container, key, records, hint):
                changed = True
                break
        if changed:
            continue
        _drop_optional_fields(result)
        if len(_dump(result)) <= budget:
            return
        break
    if len(_dump(result)) > budget:
        # Keep the stable envelope and actionable recovery text if an unusual
        # tool result consists entirely of non-shapeable fields.
        is_error = result.get("ok") is False
        if is_error:
            error = result["error"]
            fallback = {"ok": False, "error": {"code": str(error.get("code", "bad_arguments"))[:80],
                "message": "Tool response exceeded the result budget.", "hint": hint}, "truncated": True}
            if error.get("outcome") == "unknown" or error.get("code") == "outcome_unknown":
                fallback["error"]["outcome"] = "unknown"
            result.clear()
            result.update(fallback)
        else:
            payload = result.get("payload", {})
            keep = {}
            if isinstance(payload, dict):
                keep = {key: payload[key] for key in ("job_id", "edit_id", "exit_code", "state") if key in payload}
                for key, value in list(keep.items()):
                    if len(_dump(value)) > 256:
                        keep[key] = _id_preview(value)
                        keep[key + "_is_preview"] = True
            result.clear()
            if isinstance(payload, str):
                result.update({"ok": True, "payload": "[result omitted to fit the response budget]", "hint": hint, "truncated": True})
            elif isinstance(payload, list):
                result.update({"ok": True, "payload": [], "payload_omitted_count": len(payload), "hint": hint, "truncated": True})
            else:
                result.update({"ok": True, "payload": {**keep, "hint": hint, "truncated": True}, "truncated": True})
    if len(_dump(result)) > budget:
        is_error = result.get("ok") is False
        previous_error = result.get("error", {}) if is_error else {}
        preserve_unknown = previous_error.get("outcome") == "unknown" or previous_error.get("code") == "outcome_unknown"
        result.clear()
        if is_error:
            code = "outcome_unknown" if preserve_unknown else str(previous_error.get("code", "bad_arguments"))[:80]
            error = {"code": code, "message": "Tool response exceeded the result budget.",
                     "hint": "Check the operation result before retrying."}
            if preserve_unknown:
                error["outcome"] = "unknown"
            result.update({"ok": False, "error": error, "truncated": True})
        else:
            result.update({"ok": True, "payload": "[result omitted to fit the response budget]", "truncated": True})


def _retrieval_hint(name: str) -> str:
    if name.startswith("file."):
        return "Use file.read with a narrower start_line/max_lines range or file.search to inspect more."
    if name.startswith("shell.") or name.startswith("job."):
        return "Use shell.job with a later cursor, or redirect future output to a project file."
    if name.startswith("artifact."):
        return "Use artifact.read with a smaller offset and length."
    return "Use a narrower request or inspect the referenced result with its tool-specific read operation."


def _walk_strings(value: Any, parent=None, key=None):
    if isinstance(value, dict):
        for child_key, child in value.items():
            if isinstance(child, str):
                yield value, child_key, child
            else:
                yield from _walk_strings(child, value, child_key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, str):
                yield value, index, child
            else:
                yield from _walk_strings(child, value, index)


def _string_candidates(value, blocked=()):
    protected = _IDENTITY_KEYS | {"mime_type", "kind", "encoding", "state", "code"}
    candidates = []
    for parent, key, text in _walk_strings(value):
        if (id(parent), key) in blocked:
            continue
        is_identity = isinstance(key, str) and key.casefold() in protected
        if is_identity and (parent.get(key + "_is_preview", False) or len(_dump(text)) <= 1024):
            continue
        is_image_parent = isinstance(parent, dict) and (str(parent.get("type", "")).casefold() == "image"
            or str(parent.get("mime_type", parent.get("mimeType", ""))).casefold().startswith("image/"))
        if isinstance(key, str) and (key.casefold() in _BASE64_KEYS or is_image_parent and key.casefold() in {"data", "content"}):
            continue
        weight = len(_dump(text))
        if weight > 128:
            candidates.append((weight, parent, key, text))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [(parent, key, text) for _weight, parent, key, text in candidates]


def _shorten_string(parent, key, text: str, hint: str, budget: int) -> bool:
    max_encoded_chars = max(128, budget // 4)
    low, high = 0, len(text)
    preview = f"… [{len(text)} characters omitted; {hint}] …"
    best_kept = 0
    while low <= high:
        keep = (low + high) // 2
        head = int(keep * 0.30)
        tail = keep - head
        omitted_chars = len(text) - keep
        lines_omitted = _omitted_line_count(text, head, tail)
        marker = f"… [{lines_omitted} lines omitted or clipped; {omitted_chars} characters omitted; {hint}] …"
        candidate = text[:head] + marker + (text[-tail:] if tail else "")
        if len(_dump(candidate)) <= max_encoded_chars:
            preview = candidate
            best_kept = keep
            low = keep + 1
        else:
            high = keep - 1
    if len(_dump(preview)) >= len(_dump(text)):
        return False
    if isinstance(key, str) and key.casefold() in {"path", "filepath", "file_path"}:
        parent[key + "_is_preview"] = True
        parent.setdefault(key + "_original_length", len(text))
    elif isinstance(key, str) and key.casefold() in _IDENTITY_KEYS:
        parent[key + "_is_preview"] = True
        parent.setdefault(key + "_original_length", len(text))
    elif isinstance(key, str):
        parent[key + "_truncated"] = True
        parent[key + "_omitted_chars"] = len(text) - best_kept
    parent[key] = preview
    return True


def _omitted_line_count(text: str, head: int, tail: int) -> int:
    end = len(text) - tail
    first_line = text.count("\n", 0, head) + 1
    last_line = text.count("\n", 0, end) + 1
    if end > 0 and text[end - 1] == "\n":
        last_line -= 1
    return max(1, last_line - first_line + 1)


def _walk_lists(value: Any, parent=None):
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, list):
                yield value, key, child
                for index, item in enumerate(child):
                    if isinstance(item, list):
                        yield child, index, item
                        yield from _walk_lists(item, child)
                    else:
                        yield from _walk_lists(item, child)
            else:
                yield from _walk_lists(child, value)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, list):
                yield value, index, child
                yield from _walk_lists(child, value)
            else:
                yield from _walk_lists(child, value)


def _list_candidates(value):
    candidates = [(len(_dump(records)), parent, key, records)
                  for parent, key, records in _walk_lists(value) if len(records) > 2]
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [(parent, key, records) for _size, parent, key, records in candidates]


def _largest_list(value):
    candidates = _list_candidates(value)
    return candidates[0] if candidates else None


def _shorten_list(parent, key, records: list, hint: str) -> bool:
    original = list(records)
    before_size = len(_dump(records))
    nested_markers = [item for item in records if isinstance(item, dict) and "_tool_results_omitted" in item]
    prior_omitted = sum(int(item.get("_tool_results_omitted", 0)) for item in nested_markers)
    visible = [item for item in records if item not in nested_markers]
    old_count = len(visible)
    keep = max(2, int(old_count * 0.60))
    head = max(1, int(keep * 0.30))
    tail = max(0, keep - head)
    retained = visible[:head] + (visible[-tail:] if tail else [])
    omitted = prior_omitted + old_count - len(retained)
    if isinstance(parent, dict) and isinstance(key, str):
        count_key = key + "_omitted_count"
        parent[count_key] = parent.get(count_key, 0) + old_count - len(retained)
        parent[key + "_truncated"] = True
        parent.setdefault("hint", hint)
    elif isinstance(parent, list) and isinstance(key, int):
        retained.insert(head, {"_tool_results_omitted": omitted, "hint": hint})
    records[:] = retained
    if len(_dump(records)) >= before_size:
        records[:] = original
        return False
    return True


def _id_preview(value: Any) -> str:
    text = str(value)
    head = text[:32]
    tail = text[-16:] if len(text) > 48 else ""
    return f"{head}…[identifier preview; {len(text)} characters]…{tail}"


def _omit_oversized_base64(value: Any) -> None:
    if isinstance(value, dict):
        is_image_parent = str(value.get("type", "")).casefold() == "image" or str(
            value.get("mime_type", value.get("mimeType", ""))).casefold().startswith("image/")
        for key in list(value):
            child = value[key]
            lowered = key.casefold() if isinstance(key, str) else ""
            is_image_data = lowered in _BASE64_KEYS or (is_image_parent and lowered in {"data", "content"})
            if is_image_data and isinstance(child, (str, bytes)):
                del value[key]
                value["image_omitted"] = True
                value["image_hint"] = "Use the image-turn route to inspect image pixels; image data was omitted, never shortened."
            else:
                _omit_oversized_base64(child)
    elif isinstance(value, list):
        for child in value:
            _omit_oversized_base64(child)


def _drop_optional_fields(result: dict) -> None:
    payload = result.get("payload")
    if isinstance(payload, dict):
        for key in list(payload):
            if key not in _IDENTITY_KEYS and key not in {"hint", "truncated"}:
                del payload[key]
                payload["omitted_fields"] = payload.get("omitted_fields", 0) + 1
                return
