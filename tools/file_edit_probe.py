"""Run the bounded MB-27 file.edit description-versus-line probe.

This CLI is intentionally explicit and live-account only when invoked. It
requires the reviewed baseline and original P-3 receipt, holds the normal app
identity lock, and records only sanitized metrics and byte-comparison results.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import time

from PySide6.QtCore import QLockFile

from musebridge.app import Worker
from musebridge.storage import Store
from tools.harness_probes import new_chat, ready, turn


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_JSON = PROJECT_ROOT / 'evidence' / 'MB-27-edit-probes.json'
EVIDENCE_MD = PROJECT_ROOT / 'evidence' / 'MB-27-edit-probes.md'

ORIGINAL_PY = (
    b'def report(value):\r\n'
    b'    if value:\r\n'
    b'        return "old"\r\n'
    b'    return "none"\r\n'
)
ORIGINAL_MAKE = b'check:\r\n\tpython -m pytest\r\n'
EXPECTED_PY = (
    b'def report(value):\r\n'
    b'    if value:\r\n'
    b'        return "new"\r\n'
    b'    return "empty"\r\n'
)
EXPECTED_MAKE = b'check:\r\n\tpython -m pytest -q\r\n'

ALLOWED_TOOLS = {
    'bridge.context', 'bridge.health', 'bridge.capabilities', 'plan.update',
    'file.read', 'file.edit',
}
FORBIDDEN_TOOLS = {
    'file.write', 'shell.run', 'shell.job', 'job.start', 'terminal.start',
    'terminal.write', 'workspace.write',
}


def score_project(path: Path) -> dict:
    """Compare the fixture's final bytes with the required CRLF/tab result."""
    try:
        python_exact = (path / 'nested.py').read_bytes() == EXPECTED_PY
        makefile_exact = (path / 'Makefile').read_bytes() == EXPECTED_MAKE
    except OSError:
        python_exact = False
        makefile_exact = False
    return {
        'python_exact': python_exact,
        'makefile_exact': makefile_exact,
        'exact_bytes': bool(python_exact and makefile_exact),
    }


def failure_count(result: dict) -> int:
    """Count independent probe failures without double-counting error calls."""
    failures = int(result.get('failed_edit_calls', 0))
    if result.get('edit_calls') != 3:
        failures += 1
    if result.get('unexpected_tools'):
        failures += 1
    if not result.get('settled'):
        failures += 1
    if not result.get('exact_bytes'):
        failures += 1
    return failures


def choose_mode(exact_failures: int, line_failures: int) -> str:
    """Choose the mode with fewer failures, retaining exact mode on ties."""
    return 'line' if line_failures < exact_failures else 'exact'


def _prompt(mode: str) -> str:
    common = (
        'This is a bounded editing probe in a disposable project. First call '
        'file.read on nested.py and Makefile. Do not change anything until both '
        'reads complete. Then make exactly three separate file.edit calls, one '
        'per requested change. Do not use file.write, shell, or any substitute '
        'mutation tool. Preserve CRLF and the literal tab in Makefile. Finish '
        'after verifying the resulting files with file.read. Use current run_id '
        'and scope_revision for each mutation.'
    )
    if mode == 'exact':
        changes = (
            ' Edit 1: in nested.py replace this exact two-line text (the strings '
            'below use LF between lines): `    if value:\n        return "old"` '
            'with `    if value:\n        return "new"`. Edit 2: replace '
            '`    return "none"` with `    return "empty"`. Edit 3: replace the '
            'Makefile text consisting of a literal tab followed by '
            '`python -m pytest` with a literal tab followed by '
            '`python -m pytest -q`.'
        )
    else:
        changes = (
            ' Use inclusive line ranges. Edit 1: nested.py line 3 only, replacing '
            'it with eight spaces followed by `return "new"`. Edit 2: nested.py '
            'line 4 only, replacing it with four spaces followed by `return "empty"`. '
            'Edit 3: Makefile line 2 only, replacing it with a literal tab followed '
            'by `python -m pytest -q`. Do not include line endings in replacement '
            'text.'
        )
    return common + changes


async def _run_mode(worker: Worker, root: Path, mode: str) -> dict:
    """Create a fresh chat, run one edit turn, and score independent evidence."""
    if worker.core:
        await ready(worker)
    cid, path = await new_chat(worker, root, 'mb27-file-edit-' + mode)
    (path / 'nested.py').write_bytes(ORIGINAL_PY)
    (path / 'Makefile').write_bytes(ORIGINAL_MAKE)
    if not worker.core:
        worker.connect()
        await ready(worker)
    settled = False
    state = await turn(worker, cid, _prompt(mode), 240)
    settled = bool(state.get('finished') and state.get('state') in {
        'remote_finished', 'remote_reply_finished',
    })
    rows = worker.catalog.rows(
        'SELECT tool,state FROM tool_calls WHERE conversation_id=?', (cid,),
    )
    edit_rows = [row for row in rows if row['tool'] == 'file.edit']
    failed_edit_calls = sum(row['state'] != 'succeeded' for row in edit_rows)
    unexpected_tools = sorted({row['tool'] for row in rows} - ALLOWED_TOOLS)
    score = score_project(path)
    result = {
        'mode': mode,
        'settled': settled,
        'run_state': state.get('state'),
        'edit_calls': len(edit_rows),
        'failed_edit_calls': failed_edit_calls,
        'forbidden_substitute_used': bool(set(FORBIDDEN_TOOLS) & {row['tool'] for row in rows}),
        'unexpected_tools': unexpected_tools,
        **score,
    }
    result['failure_count'] = failure_count(result)
    return result


def _write_reports(report: dict) -> None:
    """Atomically persist sanitized JSON and a readable summary."""
    EVIDENCE_JSON.parent.mkdir(parents=True, exist_ok=True)
    json_tmp = EVIDENCE_JSON.with_suffix('.json.tmp')
    md_tmp = EVIDENCE_MD.with_suffix('.md.tmp')
    json_tmp.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    exact = report.get('modes', {}).get('exact', {})
    line = report.get('modes', {}).get('line', {})
    lines = [
        '# MB-27 file.edit probe', '',
        'Disposable CRLF/tab fixture; one fresh chat per mode. Results are '
        'independently compared against required final bytes. No transcript, '
        'profile path, or conversation identifier is recorded.', '',
        '| Mode | Settled | Edit calls | Failed edit calls | Exact bytes | Failure count |',
        '|---|---:|---:|---:|---:|---:|',
    ]
    for name, value in (('exact', exact), ('line', line)):
        if value:
            lines.append(
                f"| {name} | {bool(value.get('settled'))} | "
                f"{value.get('edit_calls', 'N/A')} | "
                f"{value.get('failed_edit_calls', 'N/A')} | "
                f"{bool(value.get('exact_bytes'))} | "
                f"{value.get('failure_count', 'N/A')} |"
            )
    if report.get('chosen_mode'):
        lines.extend(['', f"Chosen description mode: **{report['chosen_mode']}**."])
    if report.get('interruption'):
        lines.extend(['', 'Probe stopped: ' + report['interruption']['error_code'] + '.'])
    md_tmp.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    json_tmp.replace(EVIDENCE_JSON)
    md_tmp.replace(EVIDENCE_MD)


def _require_inputs() -> None:
    """Require the reviewed controlled baseline and original P-3 result."""
    baseline = PROJECT_ROOT / 'evidence' / 'eval' / 'v1-baseline-complete-2026-11-12.json'
    original = PROJECT_ROOT / 'evidence' / 'MB-25-probes.json'
    try:
        baseline_data = json.loads(baseline.read_text(encoding='utf-8'))
        original_data = json.loads(original.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise RuntimeError('Reviewed baseline or original P-3 receipt is unavailable') from exc
    if (baseline_data.get('label') != 'v1-baseline-complete'
            or baseline_data.get('harness_version') != '0.6.1'):
        raise RuntimeError('Reviewed complete baseline label is missing')
    baseline_results = baseline_data.get('results', [])
    if (len(baseline_results) != 12
            or any(row.get('status') in ('failed-runner', 'unknown') for row in baseline_results)):
        raise RuntimeError('Reviewed complete baseline is not fully scored')
    receipt = original_data.get('P-3-baseline', {})
    if not (receipt.get('finished') and receipt.get('edit_calls') == 3
            and receipt.get('failed_edit_calls') == 0
            and receipt.get('python_exact') and receipt.get('makefile_exact')):
        raise RuntimeError('Original P-3 exact-edit receipt is incomplete')


async def run_probe() -> int:
    """Run exact then line mode while holding the existing app identity lock."""
    _require_inputs()
    if EVIDENCE_JSON.exists() or EVIDENCE_MD.exists():
        raise RuntimeError('MB-27 probe report already exists; refusing overwrite')
    store = Store()
    lock = QLockFile(str(store.directory / 'application.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        raise RuntimeError('Close the normal MuseBridge app before running probes')
    root = PROJECT_ROOT / '.local' / ('mb27-edit-probes-' + str(int(time.time())))
    worker = None
    report = {'schema_version': 1, 'modes': {}, 'chosen_mode': None}
    try:
        root.mkdir(parents=True)
        print('P-3 mode exact: started', flush=True)
        worker = Worker(store)
        exact = await _run_mode(worker, root, 'exact')
        report['modes']['exact'] = exact
        _write_reports(report)
        print('P-3 mode exact: ' + ('passed' if exact['failure_count'] == 0 else 'scored'), flush=True)
        print('P-3 mode line: started', flush=True)
        line = await _run_mode(worker, root, 'line')
        report['modes']['line'] = line
        report['chosen_mode'] = choose_mode(exact['failure_count'], line['failure_count'])
        _write_reports(report)
        print('P-3 mode line: ' + ('passed' if line['failure_count'] == 0 else 'scored'), flush=True)
    except Exception as exc:
        report['interruption'] = {'error_code': type(exc).__name__, 'replayed': False}
        _write_reports(report)
        print('P-3 probe stopped: ' + type(exc).__name__, flush=True)
    finally:
        try:
            if worker:
                worker.close()
        except Exception as exc:
            report['cleanup'] = {'error_code': type(exc).__name__}
            try:
                _write_reports(report)
            finally:
                lock.unlock()
        else:
            lock.unlock()
    return 0 if report.get('chosen_mode') else 1


def main(argv=None) -> int:
    """Validate the empty CLI contract and run the bounded probe."""
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    return asyncio.run(run_probe())


if __name__ == '__main__':
    raise SystemExit(main())
