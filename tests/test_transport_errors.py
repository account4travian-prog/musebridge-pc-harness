import json
import struct

from musegadget.link_client import encode_message

from musebridge.transport_errors import MAX_WIRE_BYTES, sdk_result


def _encoded_size(result):
    return len(json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode("ascii"))


def test_stock_sdk_protocol_accepts_human_readable_error_string():
    structured = {
        "ok": False,
        "error": {"code": "no_project", "message": "ChooseProjectBeforeCommand",
                  "hint": "Choose a project before running a command."},
    }
    result = sdk_result(structured)
    assert result["ok"] is False
    assert "[no_project]" in result["error"]
    assert "ChooseProjectBeforeCommand" in result["error"]
    assert "Next: Choose a project" in result["error"]

    frame = encode_message({"method": "link.result", "id": "fixture-invoke", **result})
    (length,) = struct.unpack_from("<I", frame)
    message = json.loads(frame[4:4 + length])
    assert message["ok"] is False
    assert isinstance(message["error"], str)
    assert "[no_project]" in message["error"]


def test_successes_are_returned_unchanged():
    success = {"ok": True, "payload": {"answer": "done"}}
    assert sdk_result(success) is success


def test_unknown_outcome_keeps_inspect_and_do_not_replay_guidance():
    result = sdk_result({"ok": False, "error": {
        "code": "outcome_unknown", "message": "The operation timed out.",
        "hint": "Inspect the result before retrying.", "outcome": "unknown",
    }})
    assert result["ok"] is False
    assert "Outcome unknown" in result["error"]
    assert "inspect current state" in result["error"]
    assert "do not replay automatically" in result["error"]


def test_task_set_item_errors_and_current_ids_are_visible_without_private_fields():
    task_id = "a" * 32
    result = sdk_result({"ok": False, "error": {
        "code": "bad_arguments", "message": "One task was rejected.",
        "hint": "Inspect the task entries.", "call_id": "private-call-id",
        "lifecycle": {"secret": "private"},
        "items": [{"index": 1, "task_id": task_id, "error": {
            "code": "no_project", "message": r"Choose project C:\Users\Example\AppData\secret.txt",
            "hint": "Choose the project again.",
        }}],
        "tasks": [{"task_id": task_id, "state": "active", "title": "do not include title"}],
    }})
    text = result["error"]
    assert "Details (JSON)" in text
    assert task_id in text
    assert "no_project" in text
    assert "[path redacted]" in text
    assert "private-call-id" not in text
    assert "do not include title" not in text
    assert "C:\\Users" not in text


def test_unicode_error_and_long_details_fit_ascii_escaped_wire_budget():
    emoji = "😀" * 2_000
    task_id = "b" * 32
    result = sdk_result({"ok": False, "error": {
        "code": "bad_arguments", "message": emoji, "hint": emoji,
        "items": [{"index": index, "task_id": task_id, "error": {
            "code": "bad_arguments", "message": emoji, "hint": emoji,
        }} for index in range(20)],
        "tasks": [{"task_id": f"{index:032x}", "state": "active"} for index in range(30)],
    }})
    assert result["ok"] is False
    assert "[bad_arguments]" in result["error"]
    assert "Next:" in result["error"]
    assert "truncated" in result["error"]
    assert "task.list" in result["error"]
    assert _encoded_size(result) <= MAX_WIRE_BYTES
