import json
import sqlite3

import pytest

from tools import harness_stats


def make_database(directory, *, extended=True):
    database = directory / "harness.sqlite"
    connection = sqlite3.connect(database)
    if extended:
        connection.execute("""CREATE TABLE tool_calls(
            id TEXT,run_id TEXT,conversation_id TEXT,tool TEXT,state TEXT,created REAL,
            finished REAL,error_code TEXT,result_bytes INTEGER,error_message TEXT)""")
    else:
        connection.execute("""CREATE TABLE tool_calls(
            id TEXT,run_id TEXT,conversation_id TEXT,tool TEXT,state TEXT,created REAL,
            finished REAL,error_code TEXT)""")
    connection.execute("CREATE TABLE events(seq INTEGER,conversation_id TEXT,kind TEXT,data TEXT,created REAL)")
    if extended:
        rows = [
            ("1", "r1", "c", "file.read", "succeeded", 100, 101, None, 12, None),
            ("2", "r1", "c", "file.read", "failed", 101, 103, "ValueError", 0, "bad arguments"),
            ("3", "r2", "c", "file.read", "succeeded", 102, 112, None, 32, None),
            ("4", "r2", "c", "file.read", "failed", 103, None, "ValueError", None, "bad arguments"),
            ("5", "r2", "c", "file.read", "unknown", 104, 106, "Interrupted", None, "interrupted"),
            ("6", "r3", "c", "file.write", "succeeded", 105, 115, None, 40, None),
            ("7", "r4", "c", "file.write", "failed", 200, 204, "PermissionError", 0, "permission denied"),
        ]
        connection.executemany("INSERT INTO tool_calls VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
        events = [
            (1, "c", "send.context", json.dumps({"send_id": "s0", "preamble_bytes": 99}), 100),
            (2, "c", "send.context", json.dumps({"send_id": "s1", "run_id": "r1", "preamble_bytes": 1200}), 101),
            (3, "c", "send.context", json.dumps({"send_id": "s2", "preamble_bytes": 300}), 103),
            (4, "c", "send.context", json.dumps({"send_id": "s3", "preamble_bytes": 800}), 105),
            (5, "c", "send.context", json.dumps({"preamble_bytes": 777}), 106),
        ]
        connection.executemany("INSERT INTO events VALUES(?,?,?,?,?)", events)
    else:
        connection.execute("INSERT INTO tool_calls VALUES(?,?,?,?,?,?,?,?)",
                           ("old", "old-run", "c", "file.read", "succeeded", 1, None, None))
    connection.commit()
    connection.close()
    return database


def test_summarize_reports_six_aggregates_and_quantile_boundaries(tmp_path):
    database = make_database(tmp_path)
    before_version = sqlite3.connect(database).execute("PRAGMA user_version").fetchone()[0]

    report = harness_stats.summarize(tmp_path)

    assert report["calls_per_tool"] == [
        {"tool": "file.read", "calls": 5}, {"tool": "file.write", "calls": 2}]
    failures = {row["tool"]: row for row in report["failure_rate_per_tool"]}
    assert failures["file.read"]["failures"] == 3
    assert failures["file.read"]["failure_rate"] == 0.6
    assert failures["file.write"]["failure_rate"] == 0.5
    assert report["top_errors"] == [
        {"message": "bad arguments", "count": 2},
        {"message": "interrupted", "count": 1},
        {"message": "permission denied", "count": 1},
    ]
    durations = {row["tool"]: row for row in report["duration_seconds_per_tool"]}
    assert durations["file.read"] == {"tool": "file.read", "samples": 4,
                                       "median_seconds": 2.0, "p90_seconds": 10.0}
    assert durations["file.write"]["median_seconds"] == 7.0
    assert durations["file.write"]["p90_seconds"] == 10.0
    assert report["calls_per_run"] == {"runs": 4, "mean_calls": 1.75,
        "median_calls": 1.5, "p90_calls": 3,
        "by_run": [{"run_id": "r2", "calls": 3}, {"run_id": "r1", "calls": 2},
                   {"run_id": "r3", "calls": 1}, {"run_id": "r4", "calls": 1}]}
    assert report["preamble_bytes_per_send"] == {
        "samples": 5, "median_bytes": 777, "p90_bytes": 1200}
    assert report['result_bytes'] == {'samples': 5, 'missing': 2, 'total': 84}
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == before_version
        assert {row[1] for row in connection.execute("PRAGMA table_info(tool_calls)")} >= {
            "result_bytes", "error_message"}


def test_time_window_is_inclusive_and_accepts_epoch_or_utc_iso(tmp_path):
    make_database(tmp_path)
    report = harness_stats.summarize(tmp_path, since="1970-01-01T00:01:41Z", until=105)
    assert report["calls_per_tool"] == [
        {"tool": "file.read", "calls": 4}, {"tool": "file.write", "calls": 1}]
    assert report["calls_per_run"] == {"runs": 3, "mean_calls": 1.667,
        "median_calls": 1, "p90_calls": 3,
        "by_run": [{"run_id": "r2", "calls": 3}, {"run_id": "r1", "calls": 1},
                   {"run_id": "r3", "calls": 1}]}
    assert report["preamble_bytes_per_send"]["samples"] == 3
    assert report["preamble_bytes_per_send"]["median_bytes"] == 800


def test_old_schema_null_fields_and_no_send_events_are_supported(tmp_path):
    make_database(tmp_path, extended=False)
    report = harness_stats.summarize(tmp_path)
    assert report["calls_per_tool"] == [{"tool": "file.read", "calls": 1}]
    assert report["top_errors"] == []
    assert report["duration_seconds_per_tool"] == [
        {"tool": "file.read", "samples": 0, "median_seconds": None, "p90_seconds": None}]
    assert report["calls_per_run"]["runs"] == 1
    assert report["preamble_bytes_per_send"] == {
        "samples": 0, "median_bytes": None, "p90_bytes": None}


def test_bad_range_and_missing_database_fail_without_echoing_path(tmp_path):
    with pytest.raises(ValueError, match="since must not"):
        harness_stats.summarize(tmp_path, since=20, until=10)
    with pytest.raises(ValueError, match="UTC ISO"):
        harness_stats.summarize(tmp_path, since="yesterday")
    with pytest.raises(FileNotFoundError, match="database was not found") as error:
        harness_stats.summarize(tmp_path)
    assert str(tmp_path) not in str(error.value)


def test_cli_prints_all_six_reports_and_supports_json(tmp_path, capsys):
    make_database(tmp_path)
    harness_stats.main(["--data-dir", str(tmp_path)])
    output = capsys.readouterr().out
    for title in ("Calls per tool", "Failure rate per tool", "Top 10 sanitized errors",
                  "Median and p90 duration per tool", "Calls per run", "Preamble bytes per send"):
        assert title in output
    harness_stats.main([str(tmp_path), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert len(report) == 7
