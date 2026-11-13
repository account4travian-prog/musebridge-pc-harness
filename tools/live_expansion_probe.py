"""Disposable Windows contract + tracked edit + actual image delivery probe."""
import asyncio
import io
import json
import re
import secrets
import time
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from PySide6.QtCore import QLockFile
from musebridge.catalog import Catalog
from musebridge.storage import Store
from musebridge.tooling import ToolRouter
from musebridge.core import Core
from musebridge.media import from_bytes


async def main():
    store=Store();lock=QLockFile(str(store.directory/'application.lock'))
    if not lock.tryLock(0):raise RuntimeError('Close MuseBridge before running this probe')
    root=Path(__file__).resolve().parents[1]/'.local'/('expansion-probe-'+str(int(time.time())))
    root.mkdir(parents=True)
    catalog=Catalog(store.directory)
    project=catalog.create_project('Disposable expansion integration test',root,
        'Disposable test only. Use this PC project and no other services/devices. Do not update personal/global memory.')
    cid=catalog.create_conversation('Windows workflow and image test',project['id'])['id']
    router=ToolRouter(catalog,store.directory,lambda _:None)
    statuses=[]
    core=Core(store,lambda text:statuses.append(text),lambda *_:None,lambda _:None,
        catalog=catalog,router=router,conversation_id=cid)
    code=str(secrets.randbelow(900000)+100000)
    image=Image.new('RGB',(900,320),'white');draw=ImageDraw.Draw(image)
    font=ImageFont.truetype('C:/Windows/Fonts/consolab.ttf',105)
    draw.text((80,90),code,font=font,fill='black')
    draw.rectangle((660,100,790,230),fill=(0,100,230))
    output=io.BytesIO();image.save(output,format='PNG')
    attachment=from_bytes(output.getvalue(),'image/png','visual-fixture.png')
    (root/'visual-fixture.png').write_bytes(attachment.data)
    runner=asyncio.create_task(core.run());error=None;report={}
    try:
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            if core.session and core.session.registered.is_set() and (core._opened.is_set() or not core._remote_exists):break
            if runner.done():raise RuntimeError('Connection ended before readiness')
            await asyncio.sleep(.2)
        else:raise TimeoutError('Connection readiness timeout')
        print('Connected; sending one bounded image/workflow request',flush=True)
        await core.send('Run this bounded integration test using an existing development/verification skill or workflow on your VM if applicable. '
            'Keep the procedure on your VM and execute PC work through this registered Windows harness; no SSH/VPN or other devices. '
            'Call bridge.capabilities and bridge.context first. Use the current run_id and scope_revision for mutating tools. '
            'Read the six-digit code visible in the attached picture and create vision.txt with just that code using file.write create=true. '
            'Create workflow-used.txt naming the existing workflow you actually used, or honestly say no applicable workflow was available. '
            'Create probe.txt containing ready, read it, then use file.edit with the returned SHA256 to change ready to verified. '
            'Use job.start request_id=verify-expansion with Windows PowerShell: '
            "if ((Get-Content -Raw probe.txt) -ne 'verified') { exit 2 }; Write-Output 'verified'. "
            'Use job.wait to inspect exit code 0. Read change.list to confirm direct file edits were tracked. '
            'Finally call plan.update state=complete, recording what was actually verified. Do not expose private VM skill text or claim unperformed checks.',
            image=attachment,conversation_id=cid)
        print('Acknowledged; waiting for independent file/command results',flush=True)
        deadline=time.monotonic()+240
        while time.monotonic()<deadline:
            if catalog.context(cid)['goal_state']=='complete':break
            if runner.done():raise RuntimeError('Connection ended during probe')
            await asyncio.sleep(.5)
        jobs=catalog.rows('SELECT job_id FROM job_links WHERE conversation_id=?',(cid,))
        results=[router.jobs.status(row['job_id']) for row in jobs]
        reads={name:(root/name).read_text(encoding='utf-8').strip() if (root/name).is_file() else '' for name in ('probe.txt','vision.txt','workflow-used.txt')}
        edits=router.checkpoints.list(cid)['edits']
        tool_names=[row['tool'] for row in catalog.rows('SELECT tool FROM tool_calls WHERE conversation_id=?',(cid,))]
        report={'conversation_id':cid,'image_acknowledged':bool(catalog.rows('SELECT message_id FROM message_images WHERE conversation_id=?',(cid,))),
            'image_pixels_verified':reads['vision.txt']==code,'pc_edit_verified':reads['probe.txt']=='verified',
            'successful_check_job':any(row['state']=='succeeded' and row['exit_code']==0 for row in results),
            'workflow_report_present':bool(reads['workflow-used.txt']),'vm_skill_execution_trace_verified':False,
            'capabilities_called':'bridge.capabilities' in tool_names,'changes_called':'change.list' in tool_names,
            'tracked_edits':len(edits),'plan_state':catalog.context(cid)['goal_state'],
            'unknown_calls':len(catalog.rows("SELECT id FROM tool_calls WHERE conversation_id=? AND state='unknown'",(cid,)))}
        report['passed']=all(report[key] for key in ('image_acknowledged','image_pixels_verified','pc_edit_verified','successful_check_job','capabilities_called','changes_called')) and len(edits)>=4
    except Exception as exc:
        error=type(exc).__name__;report={'passed':False,'error_code':error}
    finally:
        core.stop.set()
        try:await asyncio.wait_for(runner,8)
        except (Exception,asyncio.CancelledError):runner.cancel();await asyncio.gather(runner,return_exceptions=True)
        (root/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        router.close();catalog.close();lock.unlock()
    print(json.dumps(report),flush=True)
    print('Sanitized report: '+str(root/'report.json'),flush=True)
    return report.get('passed',False)


if __name__=='__main__':raise SystemExit(0 if asyncio.run(main()) else 1)
