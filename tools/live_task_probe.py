"""Real Muse structured-task acceptance in one disposable Windows project."""
import asyncio
import json
import time
from pathlib import Path
from PySide6.QtCore import QLockFile
from musebridge.catalog import Catalog
from musebridge.storage import Store
from musebridge.tooling import ToolRouter
from musebridge.core import Core


async def main():
    store=Store();lock=QLockFile(str(store.directory/'application.lock'))
    if not lock.tryLock(0):raise RuntimeError('Close MuseBridge before this probe')
    root=Path(__file__).resolve().parents[1]/'.local'/('task-probe-'+str(int(time.time())))
    root.mkdir(parents=True);(root/'input.txt').write_text('check-me',encoding='utf-8')
    catalog=Catalog(store.directory);project=catalog.create_project('Disposable task verification test',root,
        'Disposable test only; do not use other devices/services or update personal memory.')
    cid=catalog.create_conversation('Structured task and check test',project['id'])['id']
    router=ToolRouter(catalog,store.directory,lambda _:None)
    core=Core(store,lambda _:None,lambda *_:None,lambda _:None,catalog=catalog,router=router,conversation_id=cid)
    runner=asyncio.create_task(core.run());report={}
    try:
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            if core.session and core.session.registered.is_set() and (core._opened.is_set() or not core._remote_exists):break
            if runner.done():raise RuntimeError('Connection ended')
            await asyncio.sleep(.2)
        else:raise TimeoutError('Connection readiness timeout')
        print('Connected; testing actual task/check tools',flush=True)
        await core.send('Run one bounded structured-task test in this Windows project. First use bridge.context. '
            'Create exactly one task with task.create title="Verify the input" criteria="Check input.txt with the PowerShell test" verification_mode=job_exit. '
            'Use task.update state=active. Start a check with job.start request_id=verify-task-input, verification_task_id equal to the task ID, '
            'verification_paths=["input.txt"], command: '
            "if ((Get-Content -Raw input.txt) -ne 'check-me') { exit 3 }; Write-Output 'checked'. "
            'Wait with job.wait for exit 0, then call task.verify_job with the task/job IDs and paths=["input.txt"]. '
            'Only after that receipt passes, task.update state=complete. Finally plan.update state=complete. '
            'Use fresh run_id and scope_revision from this message/context for mutations. Do not modify input.txt or use other devices/services.',conversation_id=cid)
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            if catalog.context(cid)['goal_state']=='complete':break
            if runner.done():raise RuntimeError('Connection ended')
            await asyncio.sleep(.5)
        tasks=router.tasks.list(cid)
        passed=len(tasks)==1 and tasks[0]['state']=='complete' and bool(tasks[0].get('verification',{}).get('passed'))
        (root/'input.txt').write_text('owner changed this after verification',encoding='utf-8')
        stale=router.tasks.list(cid)
        invalidated=bool(stale and stale[0]['state']=='awaiting_review' and catalog.context(cid)['goal_state']=='paused')
        report={'conversation_id':cid,'task_completed_with_receipt':passed,'owner_edit_invalidated_receipt':invalidated,
            'passed':passed and invalidated,'unknown_calls':len(catalog.rows("SELECT id FROM tool_calls WHERE conversation_id=? AND state='unknown'",(cid,)))}
    except Exception as exc:report={'passed':False,'error_code':type(exc).__name__}
    finally:
        core.stop.set()
        try:await asyncio.wait_for(runner,8)
        except (Exception,asyncio.CancelledError):runner.cancel();await asyncio.gather(runner,return_exceptions=True)
        (root/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        router.close();catalog.close();lock.unlock()
    print(json.dumps(report),flush=True);print('Sanitized report: '+str(root/'report.json'),flush=True)
    return report.get('passed',False)


if __name__=='__main__':raise SystemExit(0 if asyncio.run(main()) else 1)
