"""Live read-only Main command and unbound-error checks, with sanitized evidence."""
import argparse
import asyncio
import json
from pathlib import Path
import socket
import time

from PySide6.QtCore import QLockFile
from musebridge.app import Worker
from musebridge.storage import Store
from tools.harness_probes import ready,on_worker,turn
from tools.header_memory_probe import _reply_for_send


async def execute(store):
    worker=Worker(store)
    report={}
    observed=[]
    original=worker.router.call
    def capture(name,params,*args,**kwargs):
        result=original(name,params,*args,**kwargs)
        if name=='shell.run':observed.append((name,result))
        return result
    worker.router.call=capture
    try:
        project=worker.catalog.context('main')['project']
        expected=Path(__file__).resolve().parents[1]/'PC-Workspace'
        if not project or Path(project['root']).resolve()!=expected.resolve():
            raise RuntimeError('Main is not bound to the owner-selected PC workspace')
        worker.connect();await ready(worker)
        started=time.time()
        completion_error=None
        try:
            await turn(worker,'main','MuseBridge regression test: call bridge.context, then call shell.run once with command="hostname" and shell="powershell" on this Windows gadget. Inspect exit_code and report whether it worked. Do not use SSH, modify files or settings, or install anything. This is a read-only command test in the existing Main chat.',90)
        except Exception as exc:completion_error=type(exc).__name__
        calls=list(observed);observed.clear()
        report['main_shell_calls']=len(calls)
        report['main_hostname_exit_zero']=any(r.get('ok') and r.get('payload',{}).get('exit_code')==0 and socket.gethostname().casefold() in r.get('payload',{}).get('output','').casefold() for _,r in calls)
        report['main_new_unknown_calls']=len(worker.catalog.rows("SELECT id FROM tool_calls WHERE conversation_id='main' AND created>=? AND state='unknown'",(started,)))
        report['main_bound']=True
        if completion_error:
            report.update(status='unknown',error_code=completion_error,replayed=False)
            return report
        cid=worker.catalog.create_conversation('Workspace admission regression')['id']
        worker.conversation_id=cid
        await on_worker(worker,worker.core.select_conversation(cid))
        previous=set(worker.core.transcript.replies)
        await turn(worker,cid,'Deliberate error-reporting regression test. This chat has no workspace. Please call shell.run exactly once with command="hostname" and shell="powershell" anyway to test its refusal, then report the exact returned error code and short reason. Do not retry or use any other execution tool, remote service, SSH, files or settings.',90)
        reply=await _reply_for_send(worker.core,previous)
        report['unbound_shell_calls']=len(observed)
        report['unbound_clear_error_returned']=any(not r.get('ok') and isinstance(r.get('error'),str) and '[no_project]' in r['error'] for _,r in observed)
        report['muse_understood_no_project']='no_project' in reply.casefold()
        report['muse_reported_device_error_unknown']='device_error: unknown' in reply.casefold()
        report['unbound_no_started_shell']=not worker.catalog.rows("SELECT id FROM tool_calls WHERE conversation_id=? AND tool='shell.run' AND state!='failed'",(cid,))
        report['unbound_no_jobs']=not worker.catalog.rows('SELECT job_id FROM job_links WHERE conversation_id=?',(cid,))
        worker.catalog.archive_conversation(cid)
        worker.conversation_id='main';await on_worker(worker,worker.core.select_conversation('main'))
        report['status']='passed' if (report['main_hostname_exit_zero'] and report['main_new_unknown_calls']==0 and report['unbound_clear_error_returned'] and report['muse_understood_no_project'] and not report['muse_reported_device_error_unknown'] and report['unbound_no_jobs']) else 'failed'
        return report
    finally:worker.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    if not args.execute:parser.error('Use --execute to send the two bounded read-only Muse test requests')
    target=Path(__file__).resolve().parents[1]/'evidence'/'MB-30-main-workspace.json'
    if target.exists():raise FileExistsError('Inspect the existing live evidence; do not replay automatically')
    store=Store();lock=QLockFile(str(store.directory/'application.lock'));lock.setStaleLockTime(0)
    if not lock.tryLock(0):raise RuntimeError('Close MuseBridge before the live probe')
    try:
        try:report=asyncio.run(execute(store))
        except Exception as exc:report={'status':'unknown','error_code':type(exc).__name__,'replayed':False}
        target.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report))
        return 0 if report['status']=='passed' else 1
    finally:lock.unlock()


if __name__=='__main__':raise SystemExit(main())
