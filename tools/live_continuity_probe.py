"""Explicit follow-up to the interrupted workbench fixture, then a fresh chat."""
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
    worker=Worker(store);cid='<conversation-id>';worker.select(cid)
    root=Path(worker.catalog.context(cid)['project']['root']);report={}
    worker.connect()
    async def ready(cid):
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            core=worker.core
            if core and core.conversation_id==cid and core.session and core.session.registered.is_set() and (core._opened.is_set() or not core._remote_exists):return core
            await asyncio.sleep(.2)
        raise TimeoutError('Chat not ready')
    async def send_and_wait(cid,text):
        core=await ready(cid)
        ack=await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(core.send(text,conversation_id=cid),worker.loop))
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            run=worker.router.execution.run(cid,ack['_bridge_run_id'])
            if run['state']=='remote_finished':return
            if run['state'] in ('unknown','interrupted'):raise RuntimeError('Run outcome uncertain; no replay')
            await asyncio.sleep(.5)
        raise TimeoutError('Run did not settle')
    try:
        print('Testing continuity and a new remote chat',flush=True)
        existing=worker.router.continuity.list(cid)['notes']
        if not any(n['text']=='Continuity acceptance marker 8f24d9' for n in existing):await send_and_wait(cid,'This is a new, explicitly authorized fixture request after the earlier test stopped early. '
            'The existing workbench files already succeeded; do not repeat their side effects. '
            'Call project.note kind=progress text="Continuity acceptance marker 8f24d9" source="disposable acceptance test". '
            'Call project.handoff and confirm it includes that marker. Finally plan.update state=complete. '
            'Use the current run_id/scope_revision. Do not use other devices/services or personal memory.')
        notes=worker.router.continuity.list(cid)['notes']
        branch=worker.router.continuity.create_handoff(cid,'Disposable handoff acceptance')
        newcid=branch['conversation']['id'];worker.select(newcid)
        await send_and_wait(newcid,'Use the attached handoff seed to find the exact continuity acceptance marker. '
            'Write its full text into handoff-result.txt with file.write create=true in this project, '
            'then inspect/wait for after-edit hook jobs and plan.update state=complete. '
            'Do not reuse source task IDs, alter original files, use other devices/services or personal memory.')
        calls=worker.catalog.rows('SELECT tool,state FROM tool_calls WHERE conversation_id IN (?,?)',(cid,newcid))
        target=root/'handoff-result.txt'
        report={'source_conversation_id':cid,'new_conversation_id':newcid,
            'note_recorded':any(n['text']=='Continuity acceptance marker 8f24d9' for n in notes),
            'handoff_tool_called':any(c['tool']=='project.handoff' for c in calls),
            'new_chat_received_seed':target.exists() and target.read_text(encoding='utf-8').strip()=='Continuity acceptance marker 8f24d9',
            'seed_consumed_after_ack':worker.router.continuity.retrieve_seed(newcid) is None,
            'unknown_calls':sum(c['state']=='unknown' for c in calls)}
        report['passed']=all(report[k] for k in ('note_recorded','handoff_tool_called','new_chat_received_seed','seed_consumed_after_ack'))
    except Exception as exc:report={'passed':False,'error_code':type(exc).__name__}
    finally:
        worker.close();lock.unlock();(root/('continuity-report-'+str(int(time.time()))+'.json')).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)
    return report.get('passed',False)


if __name__=='__main__':raise SystemExit(0 if asyncio.run(main()) else 1)
