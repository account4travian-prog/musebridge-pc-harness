import json
from pathlib import Path

import pytest

from tools.header_memory_probe import (
    REPORT_JSON,
    REPORT_MD,
    header_metrics,
    read_skill_confirmation,
    sanitize_report,
    score_final_reply,
    validate_v2_core_report,
    write_report,
)


def test_v2_core_gate_requires_all_twelve_successes(tmp_path):
    path = tmp_path / "v2-core.json"
    path.write_text(json.dumps({
        "label": "v2-core", "harness_version": "0.6.1",
        "results": [{"success": True, "status": "passed"} for _ in range(12)],
    }), encoding="utf-8")
    assert validate_v2_core_report(path) == {
        "label": "v2-core", "task_count": 12, "all_passed": True,
    }
    bad = json.loads(path.read_text(encoding="utf-8"))
    bad["results"][5]["success"] = False
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(RuntimeError):
        validate_v2_core_report(path)


def test_skill_confirmation_is_read_only_and_version_gated(tmp_path):
    import sqlite3

    database = tmp_path / "harness.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE preferences(key TEXT PRIMARY KEY,value TEXT)")
    connection.execute("INSERT INTO preferences VALUES(?,?)",
                        ("musebridge_skill_confirmed_version", "1"))
    connection.commit()
    connection.close()
    before = database.read_bytes()
    assert read_skill_confirmation(tmp_path) == 1
    assert database.read_bytes() == before


def test_final_score_requires_fact_and_no_final_tools():
    assert score_final_reply("The project name is Amber Kite 4F92.", "Amber Kite 4F92", 0) == {
        "contains_private_fact": True, "no_tool_calls": True, "final_tool_calls": 0,
    }
    assert not score_final_reply("I do not remember.", "Amber Kite 4F92", 0)["contains_private_fact"]
    assert not score_final_reply("Amber Kite 4F92", "Amber Kite 4F92", 1)["no_tool_calls"]


def test_report_is_sanitized_and_never_overwritten(tmp_path):
    private_fact = "Private Jade 9C31"
    report = {
        "status": "finished", "baseline": {"label": "v2-core", "task_count": 12},
        "modes": [{"repeat_every": 0, "finished": True,
                   "final_contains_private_fact": True, "final_no_tool_calls": True,
                   "final_tool_calls": 0, "filler_tool_calls": 0, "total_tool_calls": 0,
                   "header": {"send_count": 14, "header_present_turns": [1],
                              "preamble_bytes": [100] + [0] * 13},
                   "private_project_name": private_fact, "transcript": "secret reply",
                   "conversation_id": "secret-id", "profile_path": "C:/private"}],
    }
    safe = sanitize_report(report)
    encoded = json.dumps(safe)
    for secret in (private_fact, "secret reply", "secret-id", "C:/private"):
        assert secret not in encoded
    write_report(tmp_path, report)
    assert (tmp_path / REPORT_JSON).exists()
    assert (tmp_path / REPORT_MD).exists()
    before = (tmp_path / REPORT_JSON).read_bytes()
    with pytest.raises(FileExistsError):
        write_report(tmp_path, report)
    assert (tmp_path / REPORT_JSON).read_bytes() == before


def test_header_metrics_report_only_presence_indices_and_byte_counts():
    class Catalog:
        @staticmethod
        def rows(_sql, _args):
            return [
                {"data": json.dumps({"preamble_bytes": 300})},
                {"data": json.dumps({"preamble_bytes": 0})},
                {"data": json.dumps({"preamble_bytes": 280})},
            ]

    assert header_metrics(Catalog(), "private-cid") == {
        "send_count": 3, "header_present_turns": [1, 3], "preamble_bytes": [300, 0, 280],
    }
