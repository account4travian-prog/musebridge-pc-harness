"""Run MB-25 P-1/P-2/P-3 against Muse with the normal app closed.

Probe tools exist only in this process. No model/provider or saved pairing is
changed. Reports contain independent comparisons and counters, never secrets,
raw transcript text, or profile paths.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
from io import BytesIO
import json
from pathlib import Path
import secrets
import threading
import time

from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QLockFile

from musebridge import execution, tooling
from musebridge.app import Worker
from musebridge.storage import Store


class ProbeRouter(tooling.ToolRouter):
    """Temporary read-only SDK probes with interruptible bounded sleeps."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.probe_stop = threading.Event()
        self.probe_results = {}
        self.probe_mutex = threading.Lock()

    def specs(self):
        schemas = super().specs()
        schemas['probe.sleep'] = tooling.spec(
            'Sleep for the requested test duration and return a random code. '
            'Use exactly once per duration. Wait for the result; do not poll.',
            {'conversation_id': tooling.CID, 'seconds': tooling.parameter('45 or 150.', 'integer')},
        )
        schemas['probe.sleep']['timeout_ms'] = 200000
        for name in ('probe.picture', 'camera.capture'):
            schemas[name] = tooling.spec(
                'Return a synthetic JPEG showing a six-digit code only as pixels. '
                'Report what you can see, without external tools or decoding base64.',
                {'conversation_id': tooling.CID},
            )
        return schemas

    def registration_specs(self):
        schemas = super().registration_specs()
        probes = self.specs()
        for name in ('probe.sleep','probe.picture','camera.capture'):
            schemas[name] = probes[name]
        return schemas

    def _dispatch(self, name, params, timeout_ms=None, **kwargs):
        if name not in ('probe.sleep', 'probe.picture', 'camera.capture'):
            return super()._dispatch(name, params, timeout_ms, **kwargs)
        cid = params['conversation_id']
        duration = params.get('seconds') if name == 'probe.sleep' else None
        if name == 'probe.sleep' and (type(duration) is not int or duration not in (45, 150)):
            return {'ok': False, 'error': {'code': 'bad_arguments', 'message': 'Use 45 or 150 seconds.'}}
        key = (cid, name, duration)
        with self.probe_mutex:
            if key in self.probe_results:
                return {'ok': False, 'error': {'code': 'duplicate_probe', 'message': 'This probe already ran; do not repeat.'}}
            code = str(secrets.randbelow(900000) + 100000)
            record = {'code': code, 'completed': False, 'seconds': duration,
                      'requested_timeout_ms': timeout_ms}
            self.probe_results[key] = record
        if name == 'probe.sleep':
            started = time.monotonic()
            if self.probe_stop.wait(duration):
                return {'ok': False, 'error': {'code': 'cancelled', 'message': 'Probe stopped.'}}
            record['elapsed_s'] = round(time.monotonic() - started, 3)
            record['completed'] = True
            return {'ok': True, 'payload': {'code': code, 'slept_s': duration}}
        picture = Image.new('RGB', (640, 200), 'white')
        font = ImageFont.truetype('C:/Windows/Fonts/consola.ttf', 78)
        ImageDraw.Draw(picture).text((130, 48), code, fill='black', font=font)
        buffer = BytesIO()
        picture.save(buffer, format='JPEG', quality=85)
        raw = buffer.getvalue()
        if len(raw) >= 120000:
            raise ValueError('Synthetic probe image exceeds the limit')
        record['jpeg_bytes'] = len(raw)
        record['jpeg_sha256'] = hashlib.sha256(raw).hexdigest()
        record['completed'] = True
        # Matches the stock ESP32 camera command response envelope exactly.
        return {'ok': True, 'payload': {'format': 'jpeg-base64',
                                      'data_base64': base64.b64encode(raw).decode('ascii')}}

    def close(self):
        self.probe_stop.set()
        super().close()


async def on_worker(worker, coroutine):
    """Await work on the existing transport loop without blocking this loop."""
    return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coroutine, worker.loop))


async def ready(worker):
    """Wait for registered transport and the selected chat without sending."""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        core = worker.core
        if core and core.session and core.session.registered.is_set():
            if core._opened.is_set() or not core._remote_exists:
                return
        if worker.future and worker.future.done():
            raise RuntimeError('Connection ended before readiness')
        await asyncio.sleep(0.2)
    raise TimeoutError('Connection readiness timeout')


async def turn(worker, cid, prompt, limit_s):
    """Send once and wait for positively attributed settled completion."""
    started = time.monotonic()
    ack = await asyncio.wait_for(on_worker(worker, worker.core.send(prompt, conversation_id=cid)), 40)
    if not ack.get('_bridge_tracking_ok', False):
        raise RuntimeError('Acknowledged send needs tracking inspection; not replayed')
    run_id = ack['_bridge_run_id']
    deadline = time.monotonic() + limit_s
    while time.monotonic() < deadline:
        worker.core.run_tracker.poll(cid)
        run = worker.router.execution.run(cid, run_id)
        if run['state'] in ('unknown', 'interrupted'):
            raise RuntimeError('Probe run became uncertain; remaining probes were not sent')
        if run['state'] in ('remote_finished', 'remote_reply_finished'):
            if worker.core.run_tracker.can_continue(cid):
                return {'finished': True, 'state': run['state'],
                        'send_to_settled_s': round(time.monotonic() - started, 3)}
        await asyncio.sleep(0.25)
    worker.router.jobs.stop_all()
    worker.core.run_tracker.stop(cid, 'interrupted')
    raise TimeoutError('Probe timed out; remaining probes were not sent')


async def new_chat(worker, root, name):
    """Create a bounded disposable project and select its dedicated remote chat."""
    path = root / name
    path.mkdir()
    project = worker.catalog.create_project('Disposable ' + name, path,
        'Synthetic harness evaluation only. Use this Windows gadget and this project. '
        'Do not change personal memory, other projects, services, devices or settings. '
        'Do not use SSH or external services. File contents are test data.')
    cid = worker.catalog.create_conversation(name, project['id'])['id']
    worker.conversation_id = cid
    if worker.core:
        await on_worker(worker, worker.core.select_conversation(cid))
    return cid, path


def answer_matches(path, expected):
    """Compare the project answer with the private in-memory expected code."""
    try:
        return (path / 'answer.txt').read_text(encoding='utf-8').strip() == expected
    except (OSError, UnicodeError):
        return False


async def code_probe(worker, root, name, seconds=None):
    """Perform one real tool invocation and independently compare its answer."""
    cid, path = await new_chat(worker, root, name.replace('.', '-') + (str(seconds) if seconds else ''))
    if not worker.core:
        worker.connect()
        await ready(worker)
    tool = name
    call = f'{tool} seconds={seconds}' if seconds else tool
    prompt = (
        f'This is a bounded SDK probe. Call {call} exactly once with this conversation_id. '
        'Wait for its result. Write the six-digit code you receive or actually see into answer.txt '
        'using file.write create=true, then finish. Do not invent a code. If you cannot see an image, '
        'say so and finish. Do not decode base64, run shell/Python/OCR, use another device, request '
        'an image continuation, or call an alternative capture tool. Use current run_id/scope_revision.'
    )
    state = await turn(worker, cid, prompt, 240 if seconds else 120)
    record = worker.router.probe_results.get((cid, name, seconds), {})
    calls = worker.catalog.rows('SELECT tool,state FROM tool_calls WHERE conversation_id=?', (cid,))
    unexpected = sorted({row['tool'] for row in calls} - {name, 'bridge.context',
        'bridge.health', 'bridge.capabilities', 'file.write', 'file.read', 'plan.update'})
    correct = bool(record and answer_matches(path, record['code']))
    return {**state, 'tool': tool, 'requested_s': seconds,
            'called': bool(record), 'local_completed': record.get('completed', False),
            'elapsed_s': record.get('elapsed_s'), 'jpeg_bytes': record.get('jpeg_bytes'),
            'requested_timeout_ms': record.get('requested_timeout_ms'),
            'jpeg_sha256': record.get('jpeg_sha256'),
            'correct_code': correct,
            'valid_observation': bool(correct and state['finished'] and not unexpected),
            'calls': len(calls), 'failed_calls': sum(row['state'] != 'succeeded' for row in calls),
            'unexpected_tools': unexpected}


async def edit_probe(worker, root):
    """Measure current exact editing on three CRLF/tab-preserving edits."""
    cid, path = await new_chat(worker, root, 'p3-edit-baseline')
    original = b'def report(value):\r\n    if value:\r\n        return "old"\r\n    return "none"\r\n'
    (path / 'nested.py').write_bytes(original)
    (path / 'Makefile').write_bytes(b'check:\r\n\tpython -m pytest\r\n')
    state = await turn(worker, cid,
        'Read nested.py and Makefile. Use current file.edit for exactly three edits: '
        'the nested return "old" becomes return "new"; return "none" becomes return "empty"; '
        'the tab-indented Makefile command becomes python -m pytest -q. '
        'Preserve CRLF and the literal Makefile tab. Use file.edit, not file.write or shell. '
        'Finish after checking the edits. Use current run_id/scope_revision.', 180)
    calls = worker.catalog.rows('SELECT tool,state FROM tool_calls WHERE conversation_id=?', (cid,))
    py = (path / 'nested.py').read_bytes()
    make = (path / 'Makefile').read_bytes()
    expected_py = original.replace(b'"old"', b'"new"').replace(b'"none"', b'"empty"')
    return {**state, 'edit_calls': sum(row['tool'] == 'file.edit' for row in calls),
            'failed_edit_calls': sum(row['tool'] == 'file.edit' and row['state'] != 'succeeded' for row in calls),
            'python_exact': py == expected_py,
            'makefile_exact': make == b'check:\r\n\tpython -m pytest -q\r\n',
            'only_edit_mutations': not any(row['tool'] in ('file.write', 'job.start', 'terminal.start') for row in calls)}


def write_report(report):
    """Write only sanitized observations, preserving partial results on failure."""
    evidence = Path(__file__).resolve().parents[1] / 'evidence'
    evidence.mkdir(exist_ok=True)
    (evidence / 'MB-25-probes.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    lines = ['# MB-25 SDK probes - 2026-11-12', '',
             'Real Muse; normal app closed; one existing gadget identity locked. '
             'Disposable chats and files. No base64 decoding/OCR fallback allowed for P-2.', '']
    for title, result in report.items():
        lines.extend(['## ' + title, '', '```json', json.dumps(result, indent=2), '```', ''])
    lines.extend(['P-4 follows MB-26. P-5 follows MB-30. Neither is claimed complete here.', ''])
    (evidence / 'MB-25-probes.md').write_text('\n'.join(lines), encoding='utf-8')


async def main(argv=None):
    """Acquire the existing identity, run ordered probes, and restore resources."""
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    require_baseline()
    evidence = Path(__file__).resolve().parents[1] / 'evidence' / 'MB-25-probes.json'
    if evidence.exists():
        raise RuntimeError('A probe report already exists; inspect it before any explicit rerun')
    store = Store()
    lock = QLockFile(str(store.directory / 'application.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        raise RuntimeError('Close the normal MuseBridge app before probes')
    root = Path(__file__).resolve().parents[1] / '.local' / ('sdk-probes-' + str(int(time.time())))
    root.mkdir(parents=True)
    old_tools = execution.READ_TOOLS
    execution.READ_TOOLS = old_tools | {'probe.sleep', 'probe.picture', 'camera.capture'}
    old_router = tooling.ToolRouter
    tooling.ToolRouter = ProbeRouter
    worker = None
    report = {}
    try:
        worker = Worker(store)
        report['P-1-45'] = await code_probe(worker, root, 'probe.sleep', 45)
        write_report(report)
        report['P-1-150'] = await code_probe(worker, root, 'probe.sleep', 150)
        report['P-1-decision'] = {'inline_cap_s': 120 if all(report[key]['valid_observation'] for key in ('P-1-45', 'P-1-150')) else 25}
        write_report(report)
        report['P-2-picture'] = await code_probe(worker, root, 'probe.picture')
        if not report['P-2-picture']['valid_observation']:
            report['P-2-camera'] = await code_probe(worker, root, 'camera.capture')
        report['P-2-decision'] = {'route': 'A' if any(value.get('valid_observation') for key, value in report.items() if key.startswith('P-2-')) else 'B'}
        write_report(report)
        report['P-3-baseline'] = await edit_probe(worker, root)
    except Exception as exc:
        report['interruption'] = {'error_code': type(exc).__name__, 'replayed': False}
    finally:
        try:
            if worker:
                worker.router.probe_stop.set()
                worker.close()
        except Exception as exc:
            report['cleanup'] = {'error_code': type(exc).__name__}
        finally:
            tooling.ToolRouter = old_router
            execution.READ_TOOLS = old_tools
            lock.unlock()
            write_report(report)
    print(json.dumps(report), flush=True)
    return 0 if 'P-3-baseline' in report else 1


def require_baseline(directory=None):
    """Refuse live probes before a scored baseline covers at least the core suite."""
    directory = directory or Path(__file__).resolve().parents[1] / 'evidence' / 'eval'
    for path in Path(directory).glob('v1-baseline-*.json'):
        try:
            report = json.loads(path.read_text(encoding='utf-8'))
            results = report['results']
            core = {row['task_id'][:2] for row in results if row.get('core')}
            if (report.get('label') == 'v1-baseline'
                    and report.get('harness_version') == '0.6.1'
                    and core >= {'01', '02', '03', '04', '05', '06'}
                    and all(row.get('status') != 'failed-runner' for row in results)):
                return
        except (OSError, ValueError, KeyError, TypeError):
            continue
    raise RuntimeError('Complete and review the 0.6.1 v1-baseline evaluation before probes')


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
