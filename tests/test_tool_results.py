from __future__ import annotations

import copy
import json
import re

import pytest

from musebridge.tool_results import normalize_error, shape_result


def encoded_size(value):
    return len(json.dumps(value, ensure_ascii=True).encode("ascii"))


def test_strips_internal_fields_hides_successful_hooks_and_preserves_ids():
    raw = {
        "ok": True,
        "payload": {
            "text": "user-authored file contents stay exactly as entered\n",
            "job_id": "job-123",
            "edit_id": "edit-456",
            "exit_code": 0,
            "execution": {"shell": "powershell", "limits": {"timeout_s": 30}},
            "after_edit_hooks": {"jobs": [{"hook": "format", "state": "succeeded"}], "skipped": []},
        },
        "execution": {"call_id": "private-call", "state": "succeeded"},
        "lifecycle": {"jobs": [{"hook": "audit", "state": "succeeded"}]},
    }
    original = copy.deepcopy(raw)
    shaped = shape_result(raw, "file.edit")
    assert shaped["payload"]["text"] == raw["payload"]["text"]
    assert shaped["payload"]["job_id"] == "job-123"
    assert shaped["payload"]["edit_id"] == "edit-456"
    assert shaped["payload"]["exit_code"] == 0
    assert shaped["payload"]["execution"]["shell"] == "powershell"
    assert "after_edit_hooks" not in shaped["payload"]
    assert "execution" not in shaped and "lifecycle" not in shaped
    assert "call_id" not in json.dumps(shaped)
    assert raw == original


def test_failed_blocked_unknown_hooks_only_are_reported_with_recovery():
    raw = {"ok": True, "payload": {"value": 1, "after_edit_hooks": {
        "jobs": [
            {"hook": "ok-hook", "job_id": "j-ok", "state": "succeeded"},
            {"hook": "bad-hook", "job_id": "j-bad", "state": "failed"},
        ],
        "skipped": [{"hook": "blocked-hook", "state": "outcome_unknown", "reason": "admission uncertain"}],
    }}}
    shaped = shape_result(raw, "file.write")
    issues = shaped["payload"]["hook_issues"]
    assert {item["hook"] for item in issues} == {"bad-hook", "blocked-hook"}
    assert all(item["hint"] for item in issues)
    assert "ok-hook" not in json.dumps(shaped)
    assert "hook.status" in shaped["payload"]["hook_hint"]


def test_errors_normalize_to_actionable_catalog_codes_and_sanitize_secrets_paths():
    error = {
        "code": "ValueError",
        "message": "changed since read at C:\\Users\\the owner\\AppData\\Local\\MuseBridge\\profile.db access_token=abc123 Bearer eyJhbGciOiJsecret",
        "hint": "Read again at \\\\server\\share\\Users\\the owner\\private.txt password='super secret'",
    }
    normalized = normalize_error(error, "file.edit")
    assert normalized["code"] == "changed_since_read"
    combined = normalized["message"] + normalized["hint"]
    for secret in ("abc123", "eyJhbGci", "super secret", "profile.db", "private.txt", "C:\\Users", "\\\\server"):
        assert secret not in combined
    shaped = shape_result({"ok": False, "error": error, "call_id": "call-x"}, "file.edit")
    assert shaped["error"]["code"] == "changed_since_read"
    assert "hint" in shaped["error"] and "call_id" not in json.dumps(shaped)
    assert "outcome" not in shaped["error"]


def test_unknown_outcome_is_preserved_after_error_normalization():
    shaped = shape_result({"ok": False, "error": {"code": "ValueError", "message": "post-publication failure",
        "outcome": "unknown"}}, "file.write")
    assert shaped["error"]["code"] == "outcome_unknown"
    assert shaped["error"]["outcome"] == "unknown"
    reshaped = shape_result(shaped, "file.write")
    assert reshaped["error"]["outcome"] == "unknown"


def test_cancelled_not_found_scope_and_owner_errors_keep_specific_codes():
    cases = [
        ({"code": "Cancelled", "message": "interruptible probe.sleep cancelled"}, "cancelled", "do not retry"),
        ({"code": "FileNotFoundError", "message": "missing file"}, "not_found", "file.list"),
        ({"code": "PermissionError", "message": "access denied"}, "permission_denied", "permits"),
        ({"code": "no_active_run", "message": "no active run"}, "no_active_run", "active run"),
        ({"code": "conversation_changed", "message": "conversation changed"}, "conversation_changed", "conversation_id"),
        ({"code": "ValueError", "message": "run scope revision is stale"}, "stale_scope", "scope revision"),
        ({"code": "ValueError", "message": "user activity was detected"}, "owner_active", "PC"),
    ]
    for error, expected, hint_part in cases:
        normalized = normalize_error(error, "shell.run")
        assert normalized["code"] == expected
        assert hint_part.casefold() in normalized["hint"].casefold()


@pytest.mark.parametrize(("code", "message", "expected"), [
    ("", "Read the file first: file.read path=src/a.py", "not_read"),
    ("", "Path escapes the selected workspace", "path_outside_project"),
    ("", "The file exceeds the 4 MiB limit", "too_large"),
    ("", "Still running as job abc", "timeout"),
    ("", "Before-tool outcome unknown", "outcome_unknown"),
])
def test_value_errors_map_to_plan_codes(code, message, expected):
    assert normalize_error({"code": code, "message": message}, "shell.run")["code"] == expected


def test_large_strings_keep_head_tail_mark_and_retrieval_hint_under_budget():
    text = "head marker\n" + ("middle-only\n" * 10000) + "tail marker\n"
    raw = {"ok": True, "payload": {"text": text, "job_id": "job-keep", "exit_code": 7}}
    shaped = shape_result(raw, "shell.run", budget=5000)
    assert encoded_size(shaped) <= 5000
    assert "head marker" in shaped["payload"]["text"]
    assert "tail marker" in shaped["payload"]["text"]
    assert "lines omitted or clipped" in shaped["payload"]["text"]
    marker = re.search(r"\[(\d+) lines omitted or clipped; (\d+) characters omitted;", shaped["payload"]["text"])
    assert marker is not None
    assert int(marker.group(2)) == shaped["payload"]["text_omitted_chars"]
    assert "shell.job" in shaped["hint"]
    assert shaped["payload"]["job_id"] == "job-keep"
    assert shaped["payload"]["exit_code"] == 7


def test_large_lists_keep_head_and_tail_records_with_count():
    records = [{"index": i, "edit_id": f"edit-{i}", "text": "x" * 120} for i in range(120)]
    shaped = shape_result({"ok": True, "payload": {"regions": records}}, "file.edit", budget=4000)
    payload = shaped["payload"]
    assert encoded_size(shaped) <= 4000
    assert payload["regions"][0]["index"] == 0
    assert payload["regions"][-1]["index"] == 119
    assert payload["regions_omitted_count"] > 0
    assert payload["regions_truncated"] is True
    assert payload["regions"][0]["edit_id"] == "edit-0"


def test_non_dict_payload_types_and_hook_issues_survive():
    rows = [{"id": 1}, {"id": 2}]
    shaped = shape_result({"ok": True, "payload": rows, "lifecycle": {
        "jobs": [], "skipped": [{"hook": "guard", "state": "blocked", "reason": "owner denied this hook"}]}}, "run.list")
    assert shaped["payload"] == rows
    assert shaped["hook_issues"][0]["hook"] == "guard"
    assert shape_result(shaped, "run.list")["hook_issues"] == shaped["hook_issues"]
    text = shape_result({"ok": True, "payload": "Markdown context", "lifecycle": {
        "jobs": [], "skipped": [{"reason": "conversation has no project scope"}]}}, "bridge.context")
    assert text["payload"] == "Markdown context"
    assert "hook_issues" not in text
    long_markdown = shape_result({"ok": True, "payload": "context\n" * 10000}, "bridge.context", budget=1000)
    assert isinstance(long_markdown["payload"], str)
    assert long_markdown["truncated"] is True
    assert long_markdown["payload_truncated"] is True
    assert encoded_size(long_markdown) <= 1000


def test_error_hook_issues_survive_reshaping():
    raw = {"ok": False, "error": {"code": "outcome_unknown", "outcome": "unknown", "message": "uncertain",
        "hook_issues": [{"hook": "cleanup", "state": "blocked", "reason": "owner denied"}]}}
    once = shape_result(raw, "file.write")
    twice = shape_result(once, "file.write")
    assert twice["error"]["hook_issues"] == once["error"]["hook_issues"]
    assert twice["error"]["outcome"] == "unknown"


def test_ordinary_hook_skips_are_not_reported_as_failures():
    for reason in ("conversation has no project scope", "read_only profile denies hooks", "work is paused"):
        shaped = shape_result({"ok": True, "payload": {"x": 1}, "lifecycle": {
            "jobs": [], "skipped": [{"reason": reason}]}}, "file.read")
        assert "hook_issues" not in shaped["payload"]


def test_nested_list_trimming_and_giant_identity_keep_strict_budget():
    nested = [[{"i": i, "text": "x" * 100} for i in range(80)]]
    shaped = shape_result({"ok": True, "payload": {"nested": nested}}, "file.edit", budget=1200)
    assert encoded_size(shaped) <= 1200
    marker = shaped["payload"]["nested"][0][1]
    assert marker["_tool_results_omitted"] > 0
    error = shape_result({"ok": False, "error": {"code": "ValueError", "message": "uncertain", "outcome": "unknown"}},
                         "file.write", budget=512)
    assert encoded_size(error) <= 512
    assert error["error"]["outcome"] == "unknown"
    giant = shape_result({"ok": True, "payload": {"job_id": "😀" * 100_000, "exit_code": 4}}, "shell.job", budget=512)
    assert encoded_size(giant) <= 512
    assert giant["payload"]["exit_code"] == 4
    assert giant["payload"]["job_id_is_preview"] is True


def test_image_base64_is_removed_whole_with_image_route_hint():
    image = "QUJD" * 10000
    shaped = shape_result({"ok": True, "payload": {"image_base64": image, "job_id": "j1"}}, "vision.observe", budget=1000)
    assert "image_base64" not in shaped["payload"]
    assert shaped["payload"]["image_omitted"] is True
    assert "image-turn route" in shaped["payload"]["image_hint"]
    assert "QUJD" not in json.dumps(shaped)
    assert encoded_size(shaped) <= 1000


def test_mcp_style_image_data_is_never_string_truncated():
    data = "R0lGODlh" * 5000
    shaped = shape_result({"ok": True, "payload": {"type": "image", "mimeType": "image/png", "data": data}},
                          "vision.observe", budget=1000)
    assert "data" not in shaped["payload"]
    assert shaped["payload"]["image_omitted"] is True
    assert "image-turn route" in shaped["payload"]["image_hint"]
    assert encoded_size(shaped) <= 1000


def test_malformed_result_cannot_become_success():
    shaped = shape_result({"payload": {"value": "ok"}}, "file.read")
    assert shaped["ok"] is False
    assert shaped["error"]["code"] == "bad_arguments"


def test_errors_always_have_catalog_code_message_hint_and_generic_example():
    shaped = shape_result({"ok": False, "error": {"message": "unexpected adapter failure"}}, "file.search")
    assert shaped["error"]["code"] == "bad_arguments"
    assert shaped["error"]["message"]
    assert "Example" in shaped["error"]["hint"]
    assert encoded_size(shaped) <= 24000


def test_binary_error_code_gets_document_or_image_guidance():
    result = normalize_error({"code": "binary", "message": "Binary content cannot be edited as text."}, "file.edit")
    assert result["code"] == "binary"
    assert "artifact.add" in result["hint"] and "image-turn route" in result["hint"]


def test_muse_tokens_and_full_urls_are_removed_only_from_error_text():
    error = normalize_error({"message": "failed mgst_abcdefghijklmnop https://user:pass@example.test/api?access_token=qwerty",
                             "hint": "try https://example.test?token=hidden"}, "shell.run")
    rendered = error["message"] + error["hint"]
    for secret in ("mgst_abcdefghijklmnop", "user:pass", "qwerty", "hidden", "example.test"):
        assert secret not in rendered
    text = "user data https://example.test?token=visible"
    shaped = shape_result({"ok": True, "payload": {"text": text}}, "file.read")
    assert shaped["payload"]["text"] == text
