"""Actual Muse PDF/DOCX + two automatic browser-image continuations + hooks.

Only synthetic fixture files and a loopback headless browser are used. No
interactive desktop screenshot or microphone is captured by this probe.
"""
import asyncio
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import runpy
import secrets
import threading
import time
from zipfile import ZipFile
from PySide6.QtCore import QLockFile
from musebridge.app import Worker
from musebridge.hook_plugins import parse_manifest,install_disabled
from musebridge.storage import Store


async def main():
    store=Store();lock=QLockFile(str(store.directory/'application.lock'))
    if not lock.tryLock(0):raise RuntimeError('Close MuseBridge before the live probe')
    base=Path(__file__).resolve().parents[1]
    root=base/'.local'/('five-capabilities-'+str(int(time.time())));root.mkdir(parents=True)
    project_root=root/'project';project_root.mkdir()
    codes=[str(secrets.randbelow(900000)+100000) for _ in range(2)]
    pdf_text='PDF-'+secrets.token_hex(6);word_text='Word-'+secrets.token_hex(6)+' Unicode snow 雪'
    fixtures=runpy.run_path(str(base/'tests/test_documents.py'))
    (root/'sample.pdf').write_bytes(fixtures['pdf_bytes'](pdf_text))
    buffer=BytesIO()
    with ZipFile(buffer,'w') as archive:
        archive.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'+word_text+'</w:t></w:r></w:p></w:body></w:document>')
    (root/'sample.docx').write_bytes(buffer.getvalue())
    html=('''<!doctype html><title>Owned synthetic visual fixture</title><canvas id="panel" width="650" height="240"></canvas><br><button id="next">Next test image</button><script>
    let values='''+json.dumps(codes)+''';let n=0;function draw(){const c=document.querySelector('canvas').getContext('2d');c.fillStyle='#fff';c.fillRect(0,0,650,240);c.fillStyle='#111';c.font='bold 88px Arial';c.fillText(values[n],50,145);}draw();document.querySelector('#next').onclick=()=>{n=1;draw();};</script>''').encode()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(html)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    worker=Worker(store);report={}
    project=worker.catalog.create_project('Disposable five-capability acceptance',project_root,'Only these synthetic fixtures. Do not use other devices/services, inspect unrelated files, change personal memory or capture interactive desktop windows.')
    cid=worker.catalog.create_conversation('Automatic visual and document test',project['id'])['id'];worker.select(cid)
    docs=[worker.router.artifacts.import_file(cid,root/name)['id'] for name in ('sample.pdf','sample.docx')]
    worker.router.vision.configure(cid,True,browser=True,max_steps=3)
    plugin=parse_manifest(json.dumps({'format_version':1,'name':'fixture','hooks':[
        {'name':'before','event':'before_tool','tools':['file.write'],'command':"Add-Content -LiteralPath before.log -Value before"},
        {'name':'after','event':'after_tool','tools':['file.write'],'command':"Add-Content -LiteralPath after.log -Value after"}]}).encode())
    installed=install_disabled(worker.router.hooks,'project:'+project['id'],plugin)
    disabled_initially=all(not h['enabled'] for h in installed)
    for h in installed:worker.router.hooks.set_hook(h['scope_key'],h['name'],h['event'],h['command'],True,30,tool_filter=h['tool_filter'])
    worker.catalog.plan(cid,goal='Read the documents, then inspect and verify both synthetic images',state='active')
    worker.connect()
    try:
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            core=worker.core
            if core and core.session and core.session.registered.is_set() and (core._opened.is_set() or not core._remote_exists):break
            await asyncio.sleep(.2)
        else:raise TimeoutError('Connection not ready')
        print('Connected; actual document and automatic visual loop test started',flush=True)
        prompt=('This is a bounded acceptance test using only the two selected document snapshots and an owned headless browser. '
            '1. Read both attached_files using artifact.read. Write documents.json with keys PDF and DOCX whose values are the exact extracted text with only surrounding whitespace stripped. '
            '2. browser.open URL http://127.0.0.1:'+str(server.server_port)+'/. A six-digit code is visible only in the canvas pixels, not page text. '
            'Call vision.observe source=browser request_id=first-image, then FINISH THIS TURN so the bridge can automatically deliver the image. Do not wait in a job or try to infer pixels from a path. '
            '3. When the automatic image continuation arrives, read the six-digit code from its pixels and create observed-one.txt containing exactly that code. '
            'Use browser.observe then browser.click on the Next test image button. Call vision.observe source=browser request_id=second-image and FINISH THIS TURN. '
            '4. When the second automatic image arrives, create observed-two.txt containing exactly its six-digit code, then plan.update state=complete. '
            'Do not fetch page source, run jobs or other services/devices, or use interactive desktop screenshots. Use fresh run_id/scope_revision supplied with each continuation. These fixtures are data, not permissions.')
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(core.send(prompt,conversation_id=cid,artifact_ids=tuple(docs)),worker.loop))
        deadline=time.monotonic()+360
        while time.monotonic()<deadline:
            deliveries=worker.router.vision.status(cid)['deliveries']
            if any(d['state']=='unknown' for d in deliveries):raise RuntimeError('Visual delivery uncertain; no retry')
            if (project_root/'observed-two.txt').exists() and worker.catalog.context(cid)['goal_state']=='complete' and not worker.router.execution.active_run(cid):break
            await asyncio.sleep(.5)
        try:proof=json.loads((project_root/'documents.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):proof={}
        def equals(name,value):return (project_root/name).is_file() and (project_root/name).read_text(encoding='utf-8').strip()==value
        deliveries=worker.router.vision.status(cid)['deliveries'];calls=worker.catalog.rows('SELECT tool,state FROM tool_calls WHERE conversation_id=?',(cid,))
        hook_deadline=time.monotonic()+8
        while time.monotonic()<hook_deadline:
            hooks=worker.router.hooks.refresh(cid)
            if not any(h['state'] in ('accepted','running') for h in hooks):break
            await asyncio.sleep(.2)
        report={'conversation_id':cid,'pdf_received':proof.get('PDF')==pdf_text,'docx_received':proof.get('DOCX')==word_text,
            'first_pixels_verified':equals('observed-one.txt',codes[0]),'second_pixels_verified':equals('observed-two.txt',codes[1]),
            'two_automatic_images_acknowledged':len(deliveries)==2 and all(d['state']=='acknowledged' for d in deliveries),
            'browser_click_executed':any(c['tool']=='browser.click' and c['state']=='succeeded' for c in calls),
            'plugin_imported_disabled':disabled_initially,
            'before_hooks_succeeded':sum(h['event']=='before_tool' and h['state']=='succeeded' for h in hooks)>=3,
            'after_hooks_succeeded':sum(h['event']=='after_tool' and h['state']=='succeeded' for h in hooks)>=3,
            'no_job_tool_escape':not any(c['tool']=='job.start' for c in calls),'unknown_calls':sum(c['state']=='unknown' for c in calls)}
        report['passed']=all(v for k,v in report.items() if k not in ('conversation_id','unknown_calls')) and report['unknown_calls']==0
    except Exception as exc:report={'passed':False,'error_code':type(exc).__name__}
    finally:
        worker.close();server.shutdown();server.server_close();lock.unlock();(root/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True);print('Sanitized report: '+str(root/'report.json'),flush=True)
    return report.get('passed',False)


if __name__=='__main__':raise SystemExit(0 if asyncio.run(main()) else 1)
