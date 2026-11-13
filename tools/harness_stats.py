"""Read-only summaries of tool calls and send-context sizes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import statistics
from urllib.parse import quote


def _timestamp(value, name):
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a UTC ISO timestamp or Unix epoch")
    text = value.strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{name} must be a UTC ISO timestamp or Unix epoch") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).timestamp()


def _percentile(values, percentile):
    """Nearest-rank percentile, returning None when there are no observations."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def _duration_summary(values):
    return {
        "samples": len(values),
        "median_seconds": round(statistics.median(values), 3) if values else None,
        "p90_seconds": round(_percentile(values, 0.9), 3) if values else None,
    }


def _connect_readonly(database):
    # URI-escape the path because Windows paths may contain spaces, #, or ?.
    uri = "file:" + quote(str(database.resolve()).replace("\\", "/"), safe="/:\\") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def summarize(directory, since=None, until=None):
    """Return six aggregate tables without constructing or migrating Catalog."""
    since_ts = _timestamp(since, "since")
    until_ts = _timestamp(until, "until")
    if since_ts is not None and until_ts is not None and since_ts > until_ts:
        raise ValueError("since must not be after until")
    database = Path(directory) / "harness.sqlite"
    if not database.is_file():
        raise FileNotFoundError("harness database was not found")

    connection = _connect_readonly(database)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "tool_calls" not in tables:
            raise ValueError("tool-call history is unavailable")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(tool_calls)")}
        required = {"tool", "state", "created", "finished", "run_id"}
        if not required.issubset(columns):
            raise ValueError("tool-call history has an unsupported schema")
        where = []
        args = []
        if since_ts is not None:
            where.append("created >= ?")
            args.append(since_ts)
        if until_ts is not None:
            where.append("created <= ?")
            args.append(until_ts)
        clause = " WHERE " + " AND ".join(where) if where else ""
        optional = [name for name in ("result_bytes", "error_message", "error_code") if name in columns]
        select = "tool, state, created, finished, run_id"
        if "error_message" in optional:
            select += ", error_message"
        if "result_bytes" in optional:
            select += ", result_bytes"
        if "error_code" in optional:
            select += ", error_code"
        rows = [dict(row) for row in connection.execute(f"SELECT {select} FROM tool_calls{clause}", args)]

        calls = {}
        failures = {}
        durations = {}
        run_calls = {}
        errors = {}
        for row in rows:
            tool = row["tool"] or "(unknown)"
            calls[tool] = calls.get(tool, 0) + 1
            if row["state"] in {"failed", "unknown", "interrupted", "outcome_unknown", "rejected"}:
                failures[tool] = failures.get(tool, 0) + 1
            start, end = row["created"], row["finished"]
            if start is not None and end is not None:
                duration = float(end) - float(start)
                if duration >= 0:
                    durations.setdefault(tool, []).append(duration)
            run_id = row["run_id"]
            if run_id is not None:
                run_calls[run_id] = run_calls.get(run_id, 0) + 1
            message = row.get("error_message") or row.get("error_code")
            if isinstance(message, str) and message.strip():
                sanitized = message.strip()[:200]
                errors[sanitized] = errors.get(sanitized, 0) + 1

        calls_per_tool = [
            {"tool": tool, "calls": count}
            for tool, count in sorted(calls.items(), key=lambda item: (-item[1], item[0]))
        ]
        failure_rate_per_tool = [
            {"tool": tool, "calls": count, "failures": failures.get(tool, 0),
             "failure_rate": round(failures.get(tool, 0) / count, 6)}
            for tool, count in sorted(calls.items(), key=lambda item: (-item[1], item[0]))
        ]
        duration_per_tool = [
            {"tool": tool, **_duration_summary(durations.get(tool, []))}
            for tool in sorted(calls)
        ]
        run_values = list(run_calls.values())
        calls_per_run = {
            "runs": len(run_values),
            "mean_calls": round(statistics.mean(run_values), 3) if run_values else None,
            "median_calls": statistics.median(run_values) if run_values else None,
            "p90_calls": _percentile(run_values, 0.9),
            "by_run": [
                {"run_id": run_id, "calls": count}
                for run_id, count in sorted(run_calls.items(), key=lambda item: (-item[1], item[0]))
            ],
        }

        preamble_values = []
        if "events" in tables:
            event_columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
            if {"kind", "data", "created"}.issubset(event_columns):
                event_where = ["kind='send.context'"]
                event_args = []
                if since_ts is not None:
                    event_where.append("created >= ?")
                    event_args.append(since_ts)
                if until_ts is not None:
                    event_where.append("created <= ?")
                    event_args.append(until_ts)
                query = "SELECT data FROM events WHERE " + " AND ".join(event_where)
                for event in connection.execute(query, event_args):
                    try:
                        value = json.loads(event["data"]).get("preamble_bytes")
                    except (TypeError, json.JSONDecodeError, AttributeError):
                        continue
                    if type(value) is int and value >= 0:
                        preamble_values.append(value)

        preamble_summary = {
            "samples": len(preamble_values),
            "median_bytes": statistics.median(preamble_values) if preamble_values else None,
            "p90_bytes": _percentile(preamble_values, 0.9),
        }
        return {
            "calls_per_tool": calls_per_tool,
            "failure_rate_per_tool": failure_rate_per_tool,
            "top_errors": [
                {"message": message, "count": count}
                for message, count in sorted(errors.items(), key=lambda item: (-item[1], item[0]))[:10]
            ],
            "duration_seconds_per_tool": duration_per_tool,
            "calls_per_run": calls_per_run,
            "preamble_bytes_per_send": preamble_summary,
            "result_bytes": {
                "samples": sum(type(row.get('result_bytes')) is int for row in rows),
                "missing": sum(row.get('result_bytes') is None for row in rows),
                "total": sum(row.get('result_bytes') or 0 for row in rows),
            },
        }
    finally:
        connection.close()


def _print_table(title, rows, columns):
    print(title)
    if not rows:
        print("(no data)")
        return
    headers = [label for label, _key in columns]
    values = [[str(row.get(key, "")) for _label, key in columns] for row in rows]
    widths = [max(len(header), *(len(row[index]) for row in values)) for index, header in enumerate(headers)]
    print(" | ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("-+-".join("-" * width for width in widths))
    for row in values:
        print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def _print_report(report):
    _print_table("Calls per tool", report["calls_per_tool"], [("Tool", "tool"), ("Calls", "calls")])
    _print_table("Failure rate per tool", report["failure_rate_per_tool"],
                 [("Tool", "tool"), ("Calls", "calls"), ("Failures", "failures"), ("Failure rate", "failure_rate")])
    _print_table("Top 10 sanitized errors", report["top_errors"], [("Count", "count"), ("Message", "message")])
    _print_table("Median and p90 duration per tool (seconds)", report["duration_seconds_per_tool"],
                 [("Tool", "tool"), ("Samples", "samples"), ("Median", "median_seconds"), ("P90", "p90_seconds")])
    per_run = report["calls_per_run"]
    print("Calls per run")
    print(f"Runs: {per_run['runs']}; mean: {per_run['mean_calls']}; median: {per_run['median_calls']}; p90: {per_run['p90_calls']}")
    _print_table("Run detail", per_run["by_run"], [("Run ID", "run_id"), ("Calls", "calls")])
    _print_table("Preamble bytes per send", [report["preamble_bytes_per_send"]],
                 [("Sends", "samples"), ("Median bytes", "median_bytes"), ("P90 bytes", "p90_bytes")])
    _print_table("Result byte coverage", [report['result_bytes']],
                 [("Measured calls", "samples"), ("Missing", "missing"), ("Total bytes", "total")])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only MuseBridge tool usage statistics")
    parser.add_argument("data_dir", nargs="?", help="MuseBridge data directory")
    parser.add_argument("--data-dir", dest="data_dir_option", help="MuseBridge data directory")
    parser.add_argument("--since", help="Inclusive UTC ISO timestamp or Unix epoch")
    parser.add_argument("--until", help="Inclusive UTC ISO timestamp or Unix epoch")
    parser.add_argument("--json", action="store_true", help="Print structured JSON")
    args = parser.parse_args(argv)
    directory = args.data_dir_option or args.data_dir
    if not directory:
        parser.error("provide --data-dir")
    report = summarize(directory, args.since, args.until)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=True))
    else:
        _print_report(report)


if __name__ == "__main__":
    main()
