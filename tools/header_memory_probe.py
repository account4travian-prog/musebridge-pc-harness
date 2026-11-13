"""Prepare or run MB-30 P-5 after the local Teach confirmation gate.

The default command only performs local readiness checks. The live probe sends
messages only with --execute, and only after the owner-confirmed skill version
has been recorded in the existing local profile.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sqlite3
import time

from PySide6.QtCore import QLockFile

from musebridge.storage import Store
from tools.harness_probes import new_chat, on_worker, ready, turn


EXPECTED_CORE_TASKS = 12
SKILL_CONFIRMATION_KEY = "musebridge_skill_confirmed_version"
REPORT_JSON = "MB-30-header-memory.json"
REPORT_MD = "MB-30-header-memory.md"
FILLER_REQUESTS = (
    "Reply with only the plural of berry. Do not call tools.",
    "Name one primary color other than red. Do not call tools.",
    "Reverse the letters in lamp. Reply with only the result; do not call tools.",
    "How many days are in a week? Reply with only the number; do not call tools.",
    "Name one common fruit. Do not call tools.",
    "Spell window backward. Reply with only the result; do not call tools.",
    "Give one synonym for calm. Do not call tools.",
    "What is 17 plus 5? Reply with only the number; do not call tools.",
    "Name a weekday beginning with F. Do not call tools.",
    "Give the opposite of hot. Reply with one word; do not call tools.",
    "How many letters are in snow? Reply with only the number; do not call tools.",
    "Name one punctuation mark. Do not call tools.",
)


def validate_v2_core_report(path: Path) -> dict:
    """Require the accepted 0.6.1 twelve-task report without returning task details."""
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        results = report["results"]
        accepted = (report.get("label") == "v2-core"
                    and report.get("harness_version") == "0.6.1"
                    and len(results) == EXPECTED_CORE_TASKS
                    and all(row.get("success") is True and row.get("status") == "passed"
                            for row in results))
    except (OSError, ValueError, KeyError, TypeError):
        accepted = False
        results = []
    if not accepted:
        raise RuntimeError("The accepted v2-core 12-task evaluation report is required before P-5.")
    return {"label": "v2-core", "task_count": len(results), "all_passed": True}


def read_skill_confirmation(directory: Path) -> int:
    """Read the local Teach-version preference through SQLite read-only mode."""
    database = Path(directory) / "harness.sqlite"
    if not database.is_file():
        return 0
    uri = database.resolve().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=2)
        try:
            row = connection.execute("SELECT value FROM preferences WHERE key=?",
                                     (SKILL_CONFIRMATION_KEY,)).fetchone()
        finally:
            connection.close()
        value = json.loads(row[0]) if row else 0
        return value if type(value) is int and value >= 0 else 0
    except (sqlite3.Error, ValueError, OSError, json.JSONDecodeError):
        return 0


def score_final_reply(reply: str, private_project_name: str, tool_calls: int) -> dict:
    """Score only the returned answer and final-turn call count."""
    return {
        "contains_private_fact": isinstance(reply, str) and private_project_name in reply,
        "no_tool_calls": type(tool_calls) is int and tool_calls == 0,
        "final_tool_calls": tool_calls if type(tool_calls) is int and tool_calls >= 0 else None,
    }


def header_metrics(catalog, cid: str) -> dict:
    """Return numeric preamble measurements, never send content or identifiers."""
    rows = catalog.rows("SELECT data FROM events WHERE conversation_id=? AND kind='send.context' ORDER BY seq", (cid,))
    values = []
    for row in rows:
        try:
            data = json.loads(row["data"])
            value = data.get("preamble_bytes")
            values.append(value if type(value) is int and value >= 0 else 0)
        except (ValueError, TypeError):
            values.append(0)
    return {
        "send_count": len(values),
        "header_present_turns": [index + 1 for index, value in enumerate(values) if value > 0],
        "preamble_bytes": values,
    }


def sanitize_report(report: dict) -> dict:
    """Project private runtime data onto the documented safe report fields."""
    safe_modes = []
    for mode in report.get("modes", []):
        safe_modes.append({
            "repeat_every": mode.get("repeat_every"),
            "finished": bool(mode.get("finished")),
            "final_contains_private_fact": bool(mode.get("final_contains_private_fact")),
            "final_no_tool_calls": bool(mode.get("final_no_tool_calls")),
            "final_tool_calls": mode.get("final_tool_calls"),
            "filler_tool_calls": mode.get("filler_tool_calls"),
            "total_tool_calls": mode.get("total_tool_calls"),
            "header": mode.get("header", {}),
        })
    return {
        "probe": "P-5",
        "status": report.get("status", "unknown"),
        "baseline": report.get("baseline", {}),
        "modes": safe_modes,
        "replayed_uncertain_request": False,
        "error_code": report.get("error_code"),
    }


def write_report(directory: Path, report: dict) -> None:
    """Create two sanitized reports exclusively; never replace earlier evidence."""
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / REPORT_JSON
    markdown_path = directory / REPORT_MD
    if json_path.exists() or markdown_path.exists():
        raise FileExistsError("P-5 report already exists; inspect it before any rerun.")
    safe = sanitize_report(report)
    safe["finished_at"] = datetime.now(timezone.utc).isoformat()
    json_created = False
    try:
        with json_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(safe, stream, indent=2, ensure_ascii=True)
            stream.write("\n")
        json_created = True
        lines = ["# MB-30 P-5 header-memory probe", "", "Sanitized actual-reply and tool-count measurements.", "",
                 "```json", json.dumps(safe, indent=2, ensure_ascii=True), "```", ""]
        with markdown_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(lines))
    except BaseException:
        if json_created:
            json_path.unlink(missing_ok=True)
        raise


def _latest_run_id(catalog, cid: str, previous: set[str]) -> str:
    rows = catalog.rows("SELECT id FROM runs WHERE conversation_id=? ORDER BY created DESC LIMIT 20", (cid,))
    for row in rows:
        if row["id"] not in previous:
            return row["id"]
    raise RuntimeError("The send has no newly attributed local run; stop without retrying.")


async def _reply_for_send(core, previous_ids: set[str], timeout_s: float = 12.0) -> str:
    """Return the settled new assistant message without persisting its text here."""
    deadline = time.monotonic() + timeout_s
    last_key = None
    last_text = None
    stable_since = None
    while time.monotonic() < deadline:
        candidates = [(key, reply.text) for key, reply in core.transcript.replies.items()
                      if key not in previous_ids]
        if candidates:
            key, text = candidates[-1]
            if key == last_key and text == last_text:
                if stable_since is not None and time.monotonic() - stable_since >= 0.35:
                    return text
            else:
                last_key, last_text, stable_since = key, text, time.monotonic()
        await asyncio.sleep(0.05)
    raise TimeoutError("No settled assistant reply was observed; do not resend this request.")


async def _send_observed(worker, cid: str, prompt: str) -> tuple[str, str]:
    previous_replies = set(worker.core.transcript.replies)
    previous_runs = {row["id"] for row in worker.catalog.rows(
        "SELECT id FROM runs WHERE conversation_id=?", (cid,))}
    await turn(worker, cid, prompt, 90)
    run_id = _latest_run_id(worker.catalog, cid, previous_runs)
    reply = await _reply_for_send(worker.core, previous_replies)
    return run_id, reply


def _tool_counts(catalog, cid: str, run_id: str) -> int:
    return len(catalog.rows("SELECT id FROM tool_calls WHERE conversation_id=? AND run_id=?", (cid, run_id)))


async def run_mode(worker, root: Path, repeat_every: int) -> dict:
    """Run one complete fresh-chat P-5 sequence; any uncertain send aborts its caller."""
    chat_name = "p5-" + secrets.token_hex(5)
    cid, _project_path = await new_chat(worker, root, chat_name)
    private_name = worker.catalog.context(cid)["project"]["name"]

    async def set_policy():
        manager = getattr(worker.core, "header_manager", None)
        if manager is None or not hasattr(manager, "repeat_every"):
            raise RuntimeError("The connected Core has no repeatable header policy yet.")
        manager.repeat_every = repeat_every

    await on_worker(worker, set_policy())
    before_bytes = header_metrics(worker.catalog, cid)["send_count"]
    prompt = "Reply with exactly READY. Do not call tools."
    first_run, _first_reply = await _send_observed(worker, cid, prompt)
    calls_by_run = [_tool_counts(worker.catalog, cid, first_run)]

    for request in FILLER_REQUESTS:
        run_id, _reply = await _send_observed(worker, cid, request)
        calls_by_run.append(_tool_counts(worker.catalog, cid, run_id))

    final_prompt = ("What exact project name was shown in the PC header? Reply with only that name. "
                    "Do not call tools.")
    final_run, final_reply = await _send_observed(worker, cid, final_prompt)
    final_calls = _tool_counts(worker.catalog, cid, final_run)
    calls_by_run.append(final_calls)
    metrics = header_metrics(worker.catalog, cid)
    if metrics["send_count"] - before_bytes != 14:
        raise RuntimeError("The P-5 sequence did not produce fourteen attributable sends.")
    score = score_final_reply(final_reply, private_name, final_calls)
    return {
        "repeat_every": repeat_every,
        "finished": True,
        "final_contains_private_fact": score["contains_private_fact"],
        "final_no_tool_calls": score["no_tool_calls"],
        "final_tool_calls": score["final_tool_calls"],
        "filler_tool_calls": sum(calls_by_run[1:-1]),
        "total_tool_calls": sum(calls_by_run),
        "header": metrics,
    }


async def execute_probe(store, report_directory: Path, baseline: dict) -> dict:
    """Run first-only, then repeat-every-eight only after a definite first-mode failure."""
    from musebridge.app import Worker

    worker = Worker(store)
    report = {"status": "running", "baseline": baseline, "modes": []}
    root = Path(__file__).resolve().parents[1] / ".local" / ("header-memory-" + secrets.token_hex(6))
    root.mkdir(parents=True)
    try:
        worker.connect()
        await ready(worker)
        first = await run_mode(worker, root, 0)
        report["modes"].append(first)
        if not (first["final_contains_private_fact"] and first["final_no_tool_calls"]):
            second = await run_mode(worker, root, 8)
            report["modes"].append(second)
        report["status"] = "finished"
    except Exception as exc:
        report["status"] = "aborted"
        report["error_code"] = type(exc).__name__
    finally:
        worker.close()
    write_report(report_directory, report)
    return report


async def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true",
                        help="Send the live probe; requires the local Teach confirmation gate.")
    args = parser.parse_args(argv)
    repository = Path(__file__).resolve().parents[1]
    baseline_path = repository / "evidence" / "eval" / "v2-core-2026-11-13.json"
    baseline = validate_v2_core_report(baseline_path)
    store = Store()
    confirmed_version = read_skill_confirmation(store.directory)
    if args.execute and confirmed_version < 1:
        raise RuntimeError("P-5 is gated until the owner confirms the installed skill in Muse.")
    report_directory = repository / "evidence"
    if args.execute and any((report_directory / name).exists() for name in (REPORT_JSON, REPORT_MD)):
        raise FileExistsError("P-5 report already exists; inspect it before any rerun.")
    lock = QLockFile(str(store.directory / "application.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        raise RuntimeError("Close MuseBridge before preparing or running P-5.")
    try:
        if not args.execute:
            print(json.dumps({"probe": "P-5", "status": "prepared-only",
                              "baseline_tasks": baseline["task_count"],
                              "skill_confirmed": confirmed_version >= 1,
                              "live_execution": False}), flush=True)
            return 0
        report = await execute_probe(store, report_directory, baseline)
        return 0 if report["status"] == "finished" else 1
    finally:
        lock.unlock()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
