"""Bounded live Muse test against an explicitly disposable project/conversation.

Never print credentials or private chat. Produces only sanitized status/results.
"""
import asyncio
import json
import time
from pathlib import Path

from PySide6.QtCore import QLockFile
from musebridge.catalog import Catalog
from musebridge.core import Core
from musebridge.storage import Store
from musebridge.tooling import ToolRouter


async def main():
    store=Store()
    lock=QLockFile(str(store.directory/'application.lock'))
    if not lock.tryLock(0):
        raise RuntimeError('Close MuseBridge before running the live test')
    root=Path(__file__).resolve().parents[1]/'.local'/('live-smoke-'+str(int(time.time())))
    root.mkdir(parents=True)
    catalog=Catalog(store.directory)
    project=catalog.create_project('Disposable harness integration test',root,
        'This is a disposable integration test. Only work in this project. Do not update global/personal memory.')
    conversation=catalog.create_conversation('Harness integration test',project['id'])
    cid=conversation['id']
    router=ToolRouter(catalog,store.directory,lambda text:print(text,flush=True))
    core=Core(store,lambda text:print(text,flush=True),lambda *args:None,
              lambda text:print(text,flush=True),catalog=catalog,router=router,conversation_id=cid)
    runner=asyncio.create_task(core.run())
    try:
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            if core.session and core.session.registered.is_set() and (core._opened.is_set() or not core._remote_exists):break
            if runner.done():raise RuntimeError('Connection exited before test')
            await asyncio.sleep(.2)
        else:raise TimeoutError('Connection readiness timeout')
        await core.send('Run this bounded MuseBridge integration test. Use bridge.context first. '
            'Create probe.txt containing exactly ready using file.write create=true. Read it and use file.edit '
            'with its returned SHA256 to replace ready with verified. Use job.start request_id=verify-file '
            'with Windows PowerShell: if ((Get-Content -Raw probe.txt) -ne \'verified\') { exit 2 }; Write-Output \'verified\'. '
            'Use job.wait to verify the exit code is 0. Then call plan.update with state=complete and a short plan '
            'recording the verified steps. Do not use other devices, services or personal memory. '
            'Do not claim success unless these local tools actually succeed.')
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            if catalog.context(cid)['goal_state']=='complete':break
            await asyncio.sleep(.5)
        jobs=catalog.rows('SELECT job_id FROM job_links WHERE conversation_id=?',(cid,))
        results=[router.jobs.status(j['job_id']) for j in jobs]
        passed=(root/'probe.txt').exists() and (root/'probe.txt').read_text()=='verified' and any(
            j['state']=='succeeded' and j['exit_code']==0 for j in results)
        report={'conversation_id':cid,'project_root':str(root),'file_verified':(root/'probe.txt').exists() and
            (root/'probe.txt').read_text()=='verified','jobs':[{'state':j['state'],'exit_code':j['exit_code']} for j in results],
            'plan_state':catalog.context(cid)['goal_state'],'passed':passed}
        (root/'report.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report),flush=True)
        return passed
    finally:
        core.stop.set()
        try:await asyncio.wait_for(runner,8)
        except (Exception,asyncio.CancelledError):
            runner.cancel();await asyncio.gather(runner,return_exceptions=True)
        router.close();catalog.close();lock.unlock()


if __name__=='__main__':
    raise SystemExit(0 if asyncio.run(main()) else 1)
