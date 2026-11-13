"""Run isolated MuseBridge evaluation tasks and write sanitized score reports.

One disposable project and one Muse conversation are created per task. This
runner is intentionally a real-Muse CLI, never imported by the desktop app.
Do not retry sends with an uncertain outcome.
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timezone
from importlib import metadata
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid


REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = Path(__file__).resolve().parent / 'tasks'
FIXTURE_APP = Path(__file__).resolve().parent / 'fixture_app.py'
REPORT_SCHEMA = 1
RUN_TERMINAL = {'remote_finished', 'remote_reply_finished', 'succeeded', 'failed', 'unknown', 'interrupted'}
RUN_COMPLETED = {'remote_finished', 'remote_reply_finished'}
CALL_FAILED = {'failed', 'rejected', 'unknown', 'interrupted', 'outcome_unknown', 'cancelled'}
PLACEHOLDERS = {'fixture_url', 'window_handle'}


class EvalError(RuntimeError):
    """Safe, concise runner failure; exception text never includes private paths."""


def validate_label(label: str) -> str:
    """Validate a report label suitable for paths and console output."""
    if not isinstance(label, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,39}', label):
        raise ValueError('Label must be 1..40 letters, digits, underscores, or hyphens')
    return label


def load_task(task_dir: Path) -> dict:
    """Load and validate one task definition without starting a fixture."""
    task_dir = Path(task_dir)
    required = ('seed', 'prompt.txt', 'check.py', 'limits.json')
    if any(not (task_dir / name).exists() for name in required):
        raise ValueError('Task is missing a seed, prompt, checker, or limits file')
    limits = json.loads((task_dir / 'limits.json').read_text(encoding='utf-8'))
    if not isinstance(limits, dict):
        raise ValueError('Task limits must be a JSON object')
    max_minutes = limits.get('max_minutes')
    max_turns = limits.get('max_turns')
    check_timeout = limits.get('check_timeout_s')
    if isinstance(max_minutes, bool) or not isinstance(max_minutes, (int, float)) or not 0 < max_minutes <= 180:
        raise ValueError('max_minutes must be a number in (0, 180]')
    if type(max_turns) is not int or not 1 <= max_turns <= 20:
        raise ValueError('max_turns must be an integer in 1..20')
    if type(check_timeout) is not int or not 1 <= check_timeout <= 600:
        raise ValueError('check_timeout_s must be an integer in 1..600')
    if type(limits.get('core')) is not bool:
        raise ValueError('core must be a JSON boolean')
    if limits.get('fixture') not in (None, 'browser', 'desktop', 'vision'):
        raise ValueError('fixture must be null, browser, desktop, or vision')
    prompt = (task_dir / 'prompt.txt').read_text(encoding='utf-8')
    followup = task_dir / 'followup.txt'
    return {'id': task_dir.name, 'path': task_dir.resolve(), 'limits': limits,
            'prompt': prompt, 'followup': followup.read_text(encoding='utf-8') if followup.is_file() else None}


def discover_tasks(tasks_dir: Path = TASKS_DIR) -> list[dict]:
    """Load numbered task definitions in stable directory order."""
    tasks = [load_task(path) for path in sorted(Path(tasks_dir).iterdir()) if path.is_dir() and re.match(r'^\d{2}_', path.name)]
    if not tasks:
        raise ValueError('No numbered evaluation task directories were found')
    return tasks


def select_tasks(tasks: list[dict], requested=None) -> list[dict]:
    """Select all, core, or explicitly requested task IDs/numbers."""
    if not requested:
        return tasks
    wanted = []
    for item in requested:
        wanted.extend(part.strip() for part in str(item).split(',') if part.strip())
    if wanted == ['core']:
        selected = [task for task in tasks if task['limits']['core']]
        if len(selected) != 6:
            raise ValueError('The core suite must contain exactly six tasks')
        return selected
    by_id = {task['id']: task for task in tasks}
    by_number = {task['id'][:2]: task for task in tasks}
    result = []
    for item in wanted:
        task = by_id.get(item) or by_number.get(item.zfill(2))
        if task is None:
            raise ValueError('Unknown task selection: ' + item)
        if task not in result:
            result.append(task)
    return result


def copy_seed(seed: Path, destination: Path) -> None:
    """Copy a seed without following symlinks outside the task definition."""
    seed = Path(seed).resolve(strict=True)
    if not seed.is_dir():
        raise ValueError('Task seed must be a directory')
    for item in seed.rglob('*'):
        if item.is_symlink():
            raise ValueError('Task seed may not contain symlinks')
    Path(destination).mkdir(parents=True, exist_ok=False)
    for item in seed.iterdir():
        target = Path(destination) / item.name
        if item.is_dir():
            shutil.copytree(item, target, symlinks=False)
        else:
            shutil.copy2(item, target)


def checker_environment(expected=None) -> dict[str, str]:
    """Small checker environment; never mutate the runner/model process env."""
    allowed = ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'PATHEXT', 'COMSPEC')
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env['PYTHONUTF8'] = '1'
    if expected is not None:
        env['MUSEBRIDGE_EVAL_EXPECTED'] = json.dumps(expected, ensure_ascii=False, separators=(',', ':'))
    return env


def verify_intermediate_edit(project: Path, seed: Path) -> bool:
    original = Path(seed, 'notes.txt')
    current = Path(project, 'notes.txt')
    if not original.is_file() or not current.is_file():
        return False
    try:
        before = original.read_bytes()
        after = current.read_bytes()
    except OSError:
        return False
    return after != before and b'status: reviewed' in after


def _task_root(base: Path, task_id: str) -> Path:
    return base / ('task' + task_id[:2]) / 'project'


def _task_prompt(task: dict, fixture: dict | None) -> str:
    prompt = task['prompt']
    replacements = {}
    if fixture:
        if fixture['kind'] == 'browser':
            replacements['fixture_url'] = fixture['url']
        else:
            replacements['window_handle'] = str(fixture['hwnd'])
    for key in PLACEHOLDERS:
        token = '{' + key + '}'
        if token in prompt:
            if key not in replacements:
                raise ValueError('Task prompt requires a fixture value that was not created')
            prompt = prompt.replace(token, replacements[key])
    if re.search(r'\{[A-Za-z_][A-Za-z0-9_]*\}', prompt):
        raise ValueError('Task prompt has an unknown placeholder')
    return prompt


def _minimal_process_env() -> dict[str, str]:
    env = checker_environment()
    env.pop('MUSEBRIDGE_EVAL_EXPECTED', None)
    return env


def _read_fixture_line(proc, timeout: float) -> dict:
    if proc.stdout is None:
        raise EvalError('Fixture did not provide startup metadata')
    result_queue: queue.Queue = queue.Queue(maxsize=1)
    def read_one():
        try:
            result_queue.put(proc.stdout.readline())
        except Exception:
            result_queue.put('')
    threading.Thread(target=read_one, daemon=True).start()
    try:
        line = result_queue.get(timeout=max(0.1, timeout))
    except queue.Empty:
        raise EvalError('Fixture startup timed out') from None
    if not line:
        raise EvalError('Fixture exited before startup')
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        raise EvalError('Fixture startup metadata was invalid') from None
    return data


def _start_fixture(mode: str, project: Path, timeout: float) -> tuple[subprocess.Popen, dict]:
    private_state_dir = Path(project).parent / 'fixture-state' if mode == 'browser' else None
    args = [sys.executable, '-u', str(FIXTURE_APP), '--mode', mode, '--state-dir', str(project)]
    if mode == 'browser':
        args.extend(['--port', '0'])
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    proc = subprocess.Popen(args, cwd=REPO_ROOT, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, encoding='utf-8', errors='replace',
                            env=_minimal_process_env(), creationflags=flags)
    try:
        details = _read_fixture_line(proc, min(timeout, 20.0))
        if mode == 'browser':
            parsed = urlsplit(details.get('url', ''))
            if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port:
                raise EvalError('Browser fixture did not bind an approved loopback URL')
            code = details.get('confirmation_code')
            if not isinstance(code, str) or not re.fullmatch(r'\d{6}', code):
                raise EvalError('Browser fixture did not provide a six-digit checker value')
            return proc, {'kind': mode, 'url': details['url'], 'confirmation_code': code,
                          'private_state_dir': str(project.parent / 'fixture-state')}
        hwnd, pid = details.get('hwnd'), details.get('pid')
        if type(hwnd) is not int or hwnd <= 0 or type(pid) is not int or pid <= 0:
            raise EvalError('Window fixture returned invalid ownership metadata')
        result = {'kind': mode, 'hwnd': hwnd, 'pid': pid}
        if mode in ('desktop', 'vision'):
            if details.get('secondary_screen') is not True or details.get('within_secondary') is not True:
                raise EvalError('Native fixture did not verify placement on a secondary display')
            result['secondary_display_verified'] = True
        if mode == 'vision':
            code = details.get('code')
            if not isinstance(code, str) or not re.fullmatch(r'\d{6}', code):
                raise EvalError('Vision fixture did not provide a six-digit checker value')
            result['code'] = code
        return proc, result
    except Exception:
        _stop_fixture(proc)
        if private_state_dir is not None:
            try:
                (private_state_dir / 'fixture_state.json').unlink(missing_ok=True)
                private_state_dir.rmdir()
            except OSError:
                pass
        raise


def _stop_fixture(proc) -> None:
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
        except OSError:
            pass
    if proc.stdout is not None:
        try:
            proc.stdout.close()
        except OSError:
            pass


def _await_ready(worker, timeout=90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        core = worker.core
        if core and core.session and core.session.registered.is_set() and (core._opened.is_set() or not core._remote_exists):
            return
        if worker.future and worker.future.done():
            raise EvalError('Muse connection ended before evaluation startup')
        time.sleep(0.2)
    raise EvalError('Muse connection readiness timed out')


def _select_conversation(worker, cid, timeout=30.0) -> None:
    worker.conversation_id = cid
    future = asyncio.run_coroutine_threadsafe(worker.core.select_conversation(cid), worker.loop)
    try:
        future.result(timeout=timeout)
    except FutureTimeout:
        future.cancel()
        raise EvalError('Conversation setup timed out') from None
    except Exception as exc:
        raise EvalError('Conversation setup failed (' + type(exc).__name__ + ')') from None


def _event_run_ids(catalog, cid, since) -> tuple[list[str], list[dict]]:
    rows = catalog.rows("SELECT data,created FROM events WHERE conversation_id=? AND kind='send.context' AND created>=? ORDER BY seq", (cid, since))
    ids=[]
    contexts=[]
    for row in rows:
        try:
            data=json.loads(row['data'])
        except (TypeError, json.JSONDecodeError):
            continue
        run_id=data.get('run_id')
        if isinstance(run_id,str) and run_id:
            if run_id not in ids:
                ids.append(run_id)
            contexts.append(data)
    return ids,contexts


def _vision_states(catalog, cid, since) -> list[str]:
    return [row['state'] for row in catalog.rows(
        'SELECT state FROM visual_feedback_v1 WHERE conversation_id=? AND created>=? ORDER BY created', (cid,since))]


def _stop_tracking(worker, cid) -> None:
    core=worker.core
    if core and core.run_tracker:
        worker.loop.call_soon_threadsafe(core.run_tracker.stop,cid,'unknown')


def _tracker_can_continue(worker, cid, timeout=1.0) -> bool:
    core=worker.core
    if not core or not core.run_tracker:return True
    async def check():
        return core.run_tracker.can_continue(cid)
    future=asyncio.run_coroutine_threadsafe(check(),worker.loop)
    try:
        return bool(future.result(timeout=timeout))
    except Exception:
        return False


def _send_once(worker, cid, prompt, deadline) -> str | None:
    """Send once and return its run ID; a timeout is an unknown outcome."""
    remaining=deadline-time.monotonic()
    if remaining<=0:
        return None
    future=asyncio.run_coroutine_threadsafe(worker.core.send(prompt,conversation_id=cid),worker.loop)
    try:
        ack=future.result(timeout=remaining)
    except FutureTimeout:
        future.cancel()
        _stop_tracking(worker,cid)
        return None
    except Exception:
        # Core owns its outbox and will settle an interrupted admission as
        # unknown. This task is never submitted again.
        return None
    if not isinstance(ack,dict) or not ack.get('_bridge_tracking_ok',True):
        _stop_tracking(worker,cid)
        return None
    run_id=ack.get('_bridge_run_id')
    return run_id if isinstance(run_id,str) and run_id else None


def _wait_for_runs(worker, cid, since, deadline, max_turns, initial_run_id=None) -> tuple[str, list[str]]:
    known=[]
    if initial_run_id:
        known.append(initial_run_id)
    while time.monotonic() < deadline:
        discovered, _contexts = _event_run_ids(worker.catalog,cid,since)
        for run_id in discovered:
            if run_id not in known:
                known.append(run_id)
        if len(known)>max_turns:
            _stop_tracking(worker,cid)
            return 'failed-turn-limit',known
        if known:
            states=[]
            for run_id in known:
                try:
                    states.append(worker.router.execution.run(cid,run_id)['state'])
                except ValueError:
                    states.append('sending')
            visuals=_vision_states(worker.catalog,cid,since)
            waiting_visual=any(state in ('pending','capturing','sending') for state in visuals)
            if all(state in RUN_TERMINAL for state in states) and not waiting_visual:
                if not _tracker_can_continue(worker,cid):
                    time.sleep(min(0.2,max(0.01,deadline-time.monotonic())))
                    continue
                if any(state in ('unknown','interrupted') for state in states):
                    return 'unknown-run',known
                if all(state in RUN_COMPLETED for state in states):
                    return 'completed',known
                return 'failed-run',known
        time.sleep(min(0.2,max(0.01,deadline-time.monotonic())))
    _stop_tracking(worker,cid)
    return 'failed-timeout',known


def _task_metrics(catalog, cid, run_ids, contexts) -> dict:
    calls=[]
    if run_ids:
        marks=','.join('?' for _ in run_ids)
        calls=catalog.rows(f'SELECT state,result_bytes,tool FROM tool_calls WHERE conversation_id=? AND run_id IN ({marks}) ORDER BY created', [cid,*run_ids])
    total=len(calls)
    failed=sum(row['state'] in CALL_FAILED for row in calls)
    sizes=[row['result_bytes'] for row in calls if isinstance(row['result_bytes'],int) and row['result_bytes']>=0]
    preambles=[context.get('preamble_bytes') for context in contexts
               if type(context.get('preamble_bytes')) is int and context['preamble_bytes']>=0]
    return {'tools_calls':total,'failed_calls':failed,
            'failed_call_rate':round(failed/total,6) if total else None,
            'result_bytes':sum(sizes) if sizes else None,
            'result_bytes_samples':len(sizes),'result_bytes_missing':total-len(sizes),
            'preamble_bytes':sum(preambles) if preambles else None,
            'preamble_bytes_samples':len(preambles),
            'browser_action_succeeded':any(row['tool'] in ('browser.open','browser.navigate','browser.click','browser.fill','browser.act') and row['state']=='succeeded' for row in calls),
            'desktop_action_succeeded':any(row['tool']=='desktop.act' and row['state']=='succeeded' for row in calls)}


def _run_checker(task, project, expected, timeout_s) -> dict:
    env=checker_environment(expected)
    try:
        from tools.eval.checker_process import run_checker_process
        exit_code=run_checker_process([sys.executable,str(task['path']/'check.py')],project,env,timeout_s)
    except OSError as exc:
        return {'status':'failed-check-launch','passed':False,'exit_code':None,'error_code':type(exc).__name__}
    if exit_code is None:
        return {'status':'failed-check-timeout','passed':False,'exit_code':None}
    return {'status':'passed' if exit_code==0 else 'failed-check',
            'passed':exit_code==0,'exit_code':int(exit_code)}


def _stop_task_owned_work(worker, cid) -> None:
    """Stop only jobs, terminals, and browser state attached to this eval chat."""
    if not cid:
        return
    try:
        job_rows=worker.catalog.rows('SELECT job_id FROM job_links WHERE conversation_id=?',(cid,))
        for row in job_rows:
            try:
                status=worker.router.jobs.status(row['job_id'])
                if status['state'] in ('accepted','running'):
                    worker.router.jobs.cancel(row['job_id'])
            except Exception:
                pass
    except Exception:
        pass
    try:
        for session in worker.router.terminals.list(cid):
            if session['state'] in ('accepted','running'):
                worker.router.terminals.stop(cid,session['terminal_id'])
    except Exception:
        pass
    try:
        worker.router.browser.call('browser.close',{'conversation_id':cid})
    except Exception:
        pass
    try:
        if worker.router.vision.status(cid)['enabled']:
            worker.router.vision.configure(cid,False)
    except Exception:
        pass


def _task_result(task, status, started, metrics, *, check=None, turns=0, intermediate=None,
                 secondary_display_verified=None) -> dict:
    result={'task_id':task['id'],'core':task['limits']['core'],'status':status,'success':False,
            'turns':turns,'max_turns':task['limits']['max_turns'],
            'wall_time':round(max(0.0,time.monotonic()-started),3),**metrics}
    if check:
        result['check_status']=check['status']
        if check.get('exit_code') is not None:
            result['check_exit_code']=check['exit_code']
        if check.get('error_code'):
            result['check_error_code']=check['error_code']
        result['success']=bool(check['passed'] and status=='passed')
    if intermediate is not None:
        result['intermediate_verified']=bool(intermediate)
    if task['limits']['fixture'] in ('desktop', 'vision'):
        verified = secondary_display_verified
        if verified is None:
            verified = task.get('_secondary_display_verified', False)
        result['secondary_display_verified'] = bool(verified)
    if task['id'] == '09_browser_setting':
        result['capability_exercised'] = bool(metrics.get('browser_action_succeeded'))
    elif task['id'] == '11_desktop_form':
        result['capability_exercised'] = bool(metrics.get('desktop_action_succeeded'))
    return result


def _run_one(task, worker, case_root: Path) -> dict:
    task = dict(task)
    started=time.monotonic()
    deadline=started+float(task['limits']['max_minutes'])*60
    fixture_proc=None
    cid=None
    run_ids=[]
    contexts=[]
    expected=None
    private_state=None
    try:
        task_id=task['id']
        if task_id=='10_undo_change' and not task.get('followup'):
            return _task_result(task,'failed-task-definition',started,{'tools_calls':0,'failed_calls':0,
                'failed_call_rate':None,'result_bytes':None,'result_bytes_samples':0,'result_bytes_missing':0,
                'preamble_bytes':None,'preamble_bytes_samples':0})
        project=_task_root(case_root,task_id)
        copy_seed(task['path']/'seed',project)
        project_record=worker.catalog.create_project('MB-25b '+task_id,project,
            'Synthetic harness evaluation. Work only in this project and the provided fixture. '
            'Use this MuseBridge device. Do not change personal memory, install globally, '
            'or access other projects, devices or external services.')
        conversation=worker.catalog.create_conversation('MB-25b '+task_id,project_record['id'])
        cid=conversation['id']
        _await_ready(worker, min(60, max(1, deadline-time.monotonic())))
        fixture=None
        mode=task['limits']['fixture']
        if mode:
            remaining=deadline-time.monotonic()
            fixture_proc,fixture=_start_fixture(mode,project,max(1,remaining))
            task['_secondary_display_verified'] = fixture.get('secondary_display_verified', False)
            if mode=='browser':
                private_state = Path(fixture['private_state_dir']) / 'fixture_state.json'
                expected={'confirmation_code':fixture['confirmation_code']}
            elif mode=='desktop':
                worker.router.set_profile(cid,'desktop')
            elif mode=='vision':
                worker.router.set_profile(cid,'desktop')
                worker.router.vision.configure(cid,True,browser=False,
                    window={'window_handle':fixture['hwnd'],'process_id':fixture['pid']})
                expected={'code':fixture['code']}
        prompt=_task_prompt(task,fixture)
        _select_conversation(worker,cid,min(30,max(1,deadline-time.monotonic())))
        prompt_started=time.time()
        task_start=prompt_started
        run_id=_send_once(worker,cid,prompt,deadline)
        turns=1
        if not run_id:
            _stop_task_owned_work(worker,cid)
            run_ids,contexts=_event_run_ids(worker.catalog,cid,task_start)
            metrics=_task_metrics(worker.catalog,cid,run_ids,contexts)
            admitted = worker.catalog.rows('SELECT state FROM outbox WHERE conversation_id=?', (cid,))
            status = 'unknown-send' if admitted or contexts else 'not-sent'
            return _task_result(task,status,started,metrics,turns=len(contexts))
        initial_state,run_ids=_wait_for_runs(worker,cid,task_start,deadline,task['limits']['max_turns'],run_id)
        discovered,contexts=_event_run_ids(worker.catalog,cid,task_start)
        turns=max(turns,len(contexts))
        for value in discovered:
            if value not in run_ids:run_ids.append(value)
        intermediate=None
        if initial_state!='completed':
            if initial_state in ('failed-timeout','failed-turn-limit','unknown-run'):
                _stop_task_owned_work(worker,cid)
            metrics=_task_metrics(worker.catalog,cid,run_ids,contexts)
            return _task_result(task,initial_state,started,metrics,turns=turns)
        # The reversible-edit task is deliberately staged: prove the edit
        # existed before sending the separately stored undo request.
        if task_id=='10_undo_change':
            intermediate=verify_intermediate_edit(project,task['path']/'seed')
            if not intermediate:
                metrics=_task_metrics(worker.catalog,cid,run_ids,contexts)
                return _task_result(task,'failed-intermediate',started,metrics,turns=turns,intermediate=False)
            if turns>=task['limits']['max_turns']:
                metrics=_task_metrics(worker.catalog,cid,run_ids,contexts)
                return _task_result(task,'failed-turn-limit',started,metrics,turns=turns,intermediate=True)
            followup=task['followup']
            task_for_prompt={**task,'prompt':followup}
            followup_text=_task_prompt(task_for_prompt,fixture)
            followup_id=_send_once(worker,cid,followup_text,deadline)
            turns+=1
            if not followup_id:
                _stop_task_owned_work(worker,cid)
                discovered,contexts=_event_run_ids(worker.catalog,cid,task_start)
                for value in discovered:
                    if value not in run_ids:run_ids.append(value)
                metrics=_task_metrics(worker.catalog,cid,run_ids,contexts)
                return _task_result(task,'unknown-send',started,metrics,turns=max(turns,len(contexts)),intermediate=True)
            follow_state,run_ids=_wait_for_runs(worker,cid,task_start,deadline,task['limits']['max_turns'],followup_id)
            if follow_state!='completed':
                if follow_state in ('failed-timeout','failed-turn-limit','unknown-run'):
                    _stop_task_owned_work(worker,cid)
                discovered,contexts=_event_run_ids(worker.catalog,cid,task_start)
                turns=max(turns,len(contexts))
                for value in discovered:
                    if value not in run_ids:run_ids.append(value)
                metrics=_task_metrics(worker.catalog,cid,run_ids,contexts)
                return _task_result(task,follow_state,started,metrics,turns=turns,intermediate=True)
        discovered,contexts=_event_run_ids(worker.catalog,cid,task_start)
        turns=max(turns,len(contexts))
        for value in discovered:
            if value not in run_ids:run_ids.append(value)
        _stop_fixture(fixture_proc)
        fixture_proc=None
        if mode == 'browser':
            try:
                browser_state = json.loads(private_state.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                browser_state = None
            expected['fixture_state'] = browser_state
        remaining=deadline-time.monotonic()
        if remaining<=0:
            metrics=_task_metrics(worker.catalog,cid,run_ids,contexts)
            return _task_result(task,'failed-timeout',started,metrics,turns=turns,intermediate=intermediate)
        check=_run_checker(task,project,expected,min(task['limits']['check_timeout_s'],max(1,int(remaining))))
        metrics=_task_metrics(worker.catalog,cid,run_ids,contexts)
        capability_ok = True
        if task_id == '09_browser_setting':
            capability_ok = metrics['browser_action_succeeded']
        elif task_id == '11_desktop_form':
            capability_ok = metrics['desktop_action_succeeded']
        status='passed' if check['passed'] and (intermediate is None or intermediate) and capability_ok else check['status']
        if check['passed'] and not capability_ok:
            status = 'failed-capability-not-exercised'
        result = _task_result(task,status,started,metrics,check=check,turns=turns,intermediate=intermediate,
                              secondary_display_verified=fixture.get('secondary_display_verified') if fixture else None)
        return result
    except EvalError as exc:
        status='failed-fixture' if 'Fixture' in str(exc) or 'fixture' in str(exc).lower() else 'failed-runner'
        if cid:
            run_ids,contexts=_event_run_ids(worker.catalog,cid,started)
            metrics=_task_metrics(worker.catalog,cid,run_ids,contexts)
        else:metrics=_empty_metrics()
        return _task_result(task,status,started,metrics,turns=len(run_ids))
    except Exception as exc:
        # Error class is useful diagnostics; exception text can contain paths,
        # tool arguments, or environment details and is intentionally omitted.
        metrics=_task_metrics(worker.catalog,cid,run_ids,contexts) if cid else _empty_metrics()
        result=_task_result(task,'failed-runner',started,metrics,turns=len(run_ids))
        result['error_code']=type(exc).__name__
        return result
    finally:
        _stop_fixture(fixture_proc)
        _stop_task_owned_work(worker,cid)
        if private_state is not None:
            try:
                private_state.unlink(missing_ok=True)
                private_state.parent.rmdir()
            except OSError:
                pass


def _empty_metrics():
    return {'tools_calls':0,'failed_calls':0,'failed_call_rate':None,'result_bytes':None,
            'result_bytes_samples':0,'result_bytes_missing':0,'preamble_bytes':None,'preamble_bytes_samples':0,
            'browser_action_succeeded':False,'desktop_action_succeeded':False}


def _harness_version() -> str:
    try:
        return metadata.version('musebridge')
    except metadata.PackageNotFoundError:
        try:
            import tomllib
            data=tomllib.loads((REPO_ROOT/'pyproject.toml').read_text(encoding='utf-8'))
            version=data['project']['version']
            return version if isinstance(version,str) else 'unknown'
        except (OSError,KeyError,ValueError):
            return 'unknown'


def aggregate(results: list[dict]) -> dict:
    """Summarize only measured task and tool-call values."""
    calls=sum(row.get('tools_calls',0) for row in results)
    failures=sum(row.get('failed_calls',0) for row in results)
    bytes_values=[row['result_bytes'] for row in results if isinstance(row.get('result_bytes'),int)]
    preambles=[row['preamble_bytes'] for row in results if isinstance(row.get('preamble_bytes'),int)]
    successful=sum(bool(row.get('success')) for row in results)
    return {'tasks':len(results),'successes':successful,'success_rate':round(successful/len(results),6) if results else None,
            'tools_calls':calls,'failed_calls':failures,'failed_call_rate':round(failures/calls,6) if calls else None,
            'wall_time':round(sum(row.get('wall_time',0) for row in results),3),
            'result_bytes':sum(bytes_values) if bytes_values else None,'result_bytes_samples':sum(row.get('result_bytes_samples',0) for row in results),
            'result_bytes_missing':sum(row.get('result_bytes_missing',0) for row in results),
            'preamble_bytes':sum(preambles) if preambles else None,
            'preamble_bytes_samples':sum(row.get('preamble_bytes_samples',0) for row in results)}


def report_markdown(report: dict) -> str:
    """Render a sanitized human-readable report."""
    aggregate=report['aggregate']
    lines=[f"# MuseBridge evaluation: {report['label']}",'',
           f"Date: {report['date']} · Harness: {report['harness_version']}",'',
           f"Success: {aggregate['successes']}/{aggregate['tasks']} ({aggregate['success_rate']}) · "
           f"Tool calls: {aggregate['tools_calls']} · Failed-call rate: {aggregate['failed_call_rate']} · "
           f"Wall time: {aggregate['wall_time']}s",'',
           '| Task | Status | Success | Capability | Secondary display | Tool calls | Failed-call rate | Wall time (s) | Result bytes | Preamble bytes |',
           '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in report['results']:
        rate='—' if row['failed_call_rate'] is None else str(row['failed_call_rate'])
        capability = '—' if 'capability_exercised' not in row else ('yes' if row['capability_exercised'] else 'no')
        secondary = '—' if 'secondary_display_verified' not in row else ('yes' if row['secondary_display_verified'] else 'no')
        values=[row['task_id'],row['status'],'yes' if row['success'] else 'no',capability,secondary,
                str(row['tools_calls']),rate,str(row['wall_time']),
                str(row['result_bytes']) if row['result_bytes'] is not None else '—',
                str(row['preamble_bytes']) if row['preamble_bytes'] is not None else '—']
        lines.append('| '+' | '.join(value.replace('|','\\|') for value in values)+' |')
    lines.append('')
    lines.append('Tool errors, prompts, outputs, tool arguments, fixture codes, profile paths, and conversation identifiers are omitted.')
    return '\n'.join(lines)+'\n'


def write_report(report: dict, output_dir: Path, *, now=None) -> tuple[Path,Path]:
    """Write immutable JSON and Markdown report files."""
    output_dir=Path(output_dir)
    output_dir.mkdir(parents=True,exist_ok=True)
    day=(now or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()
    base=output_dir/f"{report['label']}-{day}"
    json_path=Path(str(base)+'.json')
    md_path=Path(str(base)+'.md')
    if json_path.exists() or md_path.exists():
        raise FileExistsError('A report for this label and date already exists; choose a new label')
    payload=json.dumps(report,ensure_ascii=False,indent=2)+'\n'
    with json_path.open('x',encoding='utf-8',newline='\n') as stream:stream.write(payload)
    try:
        with md_path.open('x',encoding='utf-8',newline='\n') as stream:stream.write(report_markdown(report))
    except Exception:
        json_path.unlink(missing_ok=True)
        raise
    return json_path,md_path


def _write_progress_checkpoint(path: Path, label: str, started: datetime,
                               results: list[dict]) -> None:
    """Persist completed-task metrics in ignored state, without run content."""
    payload={'schema_version':REPORT_SCHEMA,'harness_version':_harness_version(),
             'label':label,'started_at':started.isoformat(),
             'completed_tasks':len(results),'results':results,'aggregate':aggregate(results)}
    target=Path(path)/'progress.json'
    temporary=target.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(temporary,target)


def _create_worker():
    # Imports are deferred so pure/offline functions never inspect a profile.
    from PySide6.QtCore import QLockFile
    from musebridge.app import Worker
    from musebridge.storage import Store
    if 'MUSEBRIDGE_EVAL_EXPECTED' in os.environ:
        raise EvalError('Remove MUSEBRIDGE_EVAL_EXPECTED from the process environment before running the evaluator')
    store=Store()
    lock=QLockFile(str(store.directory/'application.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        raise EvalError('Close MuseBridge before running the real-Muse evaluation')
    worker=None
    try:
        worker=Worker(store)
        worker.eval_lock=lock
        worker.connect()
        _await_ready(worker)
        return worker
    except Exception:
        if worker is not None:
            try:worker.close()
            except Exception:pass
        lock.unlock()
        raise


def _close_worker(worker):
    lock=getattr(worker,'eval_lock',None)
    try:
        worker.close()
    finally:
        if lock:
            lock.unlock()


def run_selected(tasks: list[dict], label: str, *, worker_factory=None, output_dir=None,
                 case_root=None, now=None) -> dict:
    """Run selected tasks sequentially with one locked Muse identity."""
    label=validate_label(label)
    if os.name!='nt':
        raise EvalError('The real-Muse eval runner requires Windows')
    if 'MUSEBRIDGE_EVAL_EXPECTED' in os.environ:
        raise EvalError('Remove MUSEBRIDGE_EVAL_EXPECTED from the process environment before running the evaluator')
    if worker_factory is None:
        worker_factory=_create_worker
    started=now or datetime.now(timezone.utc)
    case_root=Path(case_root or REPO_ROOT/'.local').resolve()
    case_root.mkdir(parents=True,exist_ok=True)
    # Keep each run directory unique even when two runs start in one second.
    stamp=started.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    run_base=case_root/f'eval-{label}-{stamp}'
    suffix=1
    while run_base.exists():
        run_base=case_root/f'eval-{label}-{stamp}-{suffix:02d}'
        suffix+=1
    run_base.mkdir(parents=True,exist_ok=False)
    worker=worker_factory()
    results=[]
    try:
        for task in tasks:
            print('Eval task '+task['id']+': started',flush=True)
            result=_run_one(task,worker,run_base)
            results.append(result)
            _write_progress_checkpoint(run_base,label,started,results)
            print('Eval task '+task['id']+': '+result['status']+
                  ('; success' if result['success'] else '; not passed'),flush=True)
    finally:
        _close_worker(worker)
    finished=datetime.now(timezone.utc)
    report={'schema_version':REPORT_SCHEMA,'harness_version':_harness_version(),'label':label,
            'date':finished.date().isoformat(),'started_at':started.isoformat(),
            'finished_at':finished.isoformat(),'results':results,'aggregate':aggregate(results)}
    json_path,md_path=write_report(report,output_dir or REPO_ROOT/'evidence'/'eval',now=finished)
    report['report_path']=str(json_path.relative_to(REPO_ROOT))
    print('Evaluation report: '+str(json_path.relative_to(REPO_ROOT)))
    print('Evaluation summary: '+str(md_path.relative_to(REPO_ROOT)))
    return report


def main(argv=None) -> int:
    """Parse CLI options and run or list evaluation tasks."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tasks',nargs='+',help='Task numbers/IDs or the word core (default: all)')
    parser.add_argument('--label',default='v1-baseline',help='Report label, for example v1-baseline')
    parser.add_argument('--list',action='store_true',help='List available task IDs and core membership without connecting')
    args=parser.parse_args(argv)
    try:
        label=validate_label(args.label)
        tasks=select_tasks(discover_tasks(),args.tasks)
        if args.list:
            for task in tasks:
                print(task['id']+(' [core]' if task['limits']['core'] else ''))
            return 0
        if os.name!='nt':raise EvalError('The real-Muse eval runner requires Windows')
        if 'MUSEBRIDGE_EVAL_EXPECTED' in os.environ:
            raise EvalError('Remove MUSEBRIDGE_EVAL_EXPECTED from the process environment before running the evaluator')
        run_selected(tasks,label)
        return 0
    except (ValueError,FileExistsError,EvalError,OSError,RuntimeError) as exc:
        message=str(exc) if isinstance(exc,(ValueError,EvalError)) and not isinstance(exc,OSError) else type(exc).__name__
        print(type(exc).__name__+': '+message,file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
