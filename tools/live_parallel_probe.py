"""Two disposable projects/chats on one locked gadget identity; no uncertain replay."""
import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
import json
from pathlib import Path
import time

from tools.eval.run_eval import _create_worker,_close_worker,_stop_task_owned_work


async def probe(worker,root):
    core=worker.core
    catalog=worker.catalog
    chats=[]
    runs=[]
    roots=[]
    session=core.session
    report={'passed':False}
    try:
        for name in ('alpha','beta'):
            target=root/name
            target.mkdir(parents=True)
            project=catalog.create_project('Disposable parallel '+name,target,
                'Only this disposable fixture. No other devices, services, credentials or personal memory.')
            chats.append(catalog.create_conversation('Parallel fixture '+name,project['id'])['id'])
            roots.append(target)
        worker.conversation_id=chats[0]
        await core.select_conversation(chats[0])
        first=await core.send('Use this Work binding with every PC tool call. Only this disposable project. '
            'Run exactly this PowerShell with shell.run: Start-Sleep -Seconds 20; [IO.File]::WriteAllText("alpha.txt","alpha") '
            'Then read alpha.txt to verify its contents, and reply briefly. Do not use other devices, services or memory.',conversation_id=chats[0])
        if not first.get('_bridge_tracking_ok'):raise RuntimeError('First send tracking uncertain; no retry')
        runs.append(first['_bridge_run_id'])
        deadline=time.monotonic()+100
        while time.monotonic()<deadline:
            commands=catalog.rows("SELECT state FROM tool_calls WHERE run_id=? AND tool='shell.run'",(runs[0],))
            if any(item['state']=='running' for item in commands):break
            await asyncio.sleep(.15)
        else:raise TimeoutError('First chat did not start its command')
        worker.conversation_id=chats[1]
        await core.select_conversation(chats[1])
        second=await core.send('Use this Work binding with every PC tool call. Only this disposable project. '
            'Use file.write to create beta.txt containing exactly beta, then file.read to verify, and reply briefly. '
            'Do not use other devices, services or personal memory.',conversation_id=chats[1])
        if not second.get('_bridge_tracking_ok'):raise RuntimeError('Second send tracking uncertain; no retry')
        runs.append(second['_bridge_run_id'])
        report['both_requests_active']=all(worker.router.execution.run(cid,run)['state'] in ('sending','acknowledged') for cid,run in zip(chats,runs))
        worker.conversation_id='main'
        await core.select_conversation('main')
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            for cid in chats:core.run_tracker.poll(cid)
            states=[worker.router.execution.run(cid,run)['state'] for cid,run in zip(chats,runs)]
            if any(state in ('unknown','interrupted','failed') for state in states):raise RuntimeError('A parallel outcome became uncertain')
            if all(state in ('remote_finished','remote_reply_finished') for state in states):break
            await asyncio.sleep(.2)
        else:raise TimeoutError('Parallel requests did not finish')
        report['run_states']=states
        report['one_session']=core.session is session
        report['main_view_did_not_interrupt']=core.conversation_id=='main'
        report['effects_correct']=((roots[0]/'alpha.txt').read_bytes()==b'alpha' and (roots[1]/'beta.txt').read_bytes()==b'beta')
        report['no_cross_files']=not (roots[0]/'beta.txt').exists() and not (roots[1]/'alpha.txt').exists()
        calls=[catalog.rows("SELECT created,finished FROM tool_calls WHERE run_id=? AND effect='project_write' AND state='succeeded'",(run,)) for run in runs]
        report['overlapping_tool_intervals']=any(a['created']<b['finished'] and b['created']<a['finished'] for a in calls[0] for b in calls[1])
        report['passed']=all(report[key] for key in ('both_requests_active','one_session','main_view_did_not_interrupt','effects_correct','no_cross_files','overlapping_tool_intervals'))
    except Exception as exc:
        report['error_code']=type(exc).__name__
    finally:
        for cid in chats:
            _stop_task_owned_work(worker,cid)
            catalog.archive_conversation(cid,True)
    return report


def main():
    root=Path(__file__).resolve().parents[1]/'.local'/('parallel-probe-'+str(int(time.time())))
    root.mkdir(parents=True)
    worker=None
    try:
        worker=_create_worker()
        future=asyncio.run_coroutine_threadsafe(probe(worker,root),worker.loop)
        report=future.result(timeout=360)
    except Exception as exc:report={'passed':False,'error_code':type(exc).__name__}
    finally:
        if worker:_close_worker(worker)
    target=Path(__file__).resolve().parents[1]/'evidence'/'MB-36a-live-parallel.json'
    if target.exists():target=target.with_name('MB-36a-live-parallel-'+str(int(time.time()))+'.json')
    target.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report),flush=True)
    return report.get('passed',False)


if __name__=='__main__':raise SystemExit(0 if main() else 1)
