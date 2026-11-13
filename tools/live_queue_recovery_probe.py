"""Three explicit requests, including a reply-only middle turn, in a fresh fixture."""
import asyncio
import json
from pathlib import Path
import time
from PySide6.QtCore import QLockFile
from musebridge.app import Worker
from musebridge.storage import Store


async def main():
    store=Store();lock=QLockFile(str(store.directory/'application.lock'))
    if not lock.tryLock(0):raise RuntimeError('Close MuseBridge before this probe')
    root=Path(__file__).resolve().parents[1]/'.local'/('queue-recovery-'+str(int(time.time())));root.mkdir(parents=True)
    worker=Worker(store);report={};p=worker.catalog.create_project('Disposable reply-only queue acceptance',root,'Only this fixture; no other devices/services or personal memory.')
    cid=worker.catalog.create_conversation('Three ordered fixture requests',p['id'])['id'];worker.select(cid);worker.connect()
    try:
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            core=worker.core
            if core and core.session and core.session.registered.is_set() and (core._opened.is_set() or not core._remote_exists):break
            await asyncio.sleep(.2)
        else:raise TimeoutError('Connection not ready')
        worker.queue.add(cid,'Use current run_id/scope_revision. Create first.txt containing exactly one with file.write create=true, then plan.update state=complete. Only this fixture; no other devices/services or personal memory.')
        worker.queue.add(cid,'Read-only request: use file.read to verify first.txt contains one, then reply briefly READ_OK. Do not mutate files, plans or personal memory.')
        worker.queue.add(cid,'Use current run_id/scope_revision. Create third.txt containing exactly after-reply with file.write create=true, then plan.update state=complete. Only this fixture; no other devices/services or personal memory.')
        print('Testing three queued requests across a reply-only turn',flush=True)
        deadline=time.monotonic()+240
        while time.monotonic()<deadline:
            entries=worker.queue.rows(cid)
            if any(row['state']=='unknown' for row in entries):raise RuntimeError('Send uncertain; no replay')
            runs=[worker.router.execution.run(cid,row['run_id']) for row in entries if row['run_id']]
            if len(runs)==3 and all(r['state'] in ('remote_finished','remote_reply_finished') for r in runs):break
            await asyncio.sleep(.5)
        output=root/'third.txt'
        report={'conversation_id':cid,'three_acknowledged':len(entries)==3 and all(r['state']=='acknowledged' for r in entries),
            'ordered_completion_boundaries':len(runs)==3 and all(runs[i]['finished'] and runs[i]['finished']<=runs[i+1]['created'] for i in range(2)),
            'third_effect_verified':output.exists() and output.read_text().strip()=='after-reply',
            'run_states':[r['state'] for r in runs],
            'unknown_calls':len(worker.catalog.rows("SELECT id FROM tool_calls WHERE conversation_id=? AND state='unknown'",(cid,)))}
        report['passed']=all(report[k] for k in ('three_acknowledged','ordered_completion_boundaries','third_effect_verified')) and all(r['state'] in ('remote_finished','remote_reply_finished') for r in runs)
    except Exception as exc:report={'passed':False,'error_code':type(exc).__name__}
    finally:
        worker.close();lock.unlock();(root/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True);return report.get('passed',False)


if __name__=='__main__':raise SystemExit(0 if asyncio.run(main()) else 1)
