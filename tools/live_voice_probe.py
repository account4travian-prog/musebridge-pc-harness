"""Test voice admission/hangup with generated silence; never opens a microphone."""
import asyncio
import json
import wave
from pathlib import Path
from PySide6.QtCore import QLockFile
from musebridge.catalog import Catalog
from musebridge.core import Core
from musebridge.storage import Store
from musebridge.tooling import ToolRouter
from musebridge.voice import VoiceController
from musegadget.voice_audio import WavAudio


async def main():
    store=Store();lock=QLockFile(str(store.directory/'application.lock'))
    if not lock.tryLock(0):raise RuntimeError('Close MuseBridge before voice probe')
    catalog=Catalog(store.directory)
    conversations=catalog.rows("SELECT * FROM conversations WHERE title='Harness integration test' ORDER BY created DESC LIMIT 1")
    if not conversations:raise ValueError('Run live harness probe first')
    cid=conversations[0]['id']
    root=Path(__file__).resolve().parents[1]/'.local'/'voice-probe';root.mkdir(parents=True,exist_ok=True)
    source=root/'silence.wav';output=root/'reply.wav'
    with wave.open(str(source),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(b'\0'*1920)
    router=ToolRouter(catalog,store.directory)
    core=Core(store,lambda s:print(s,flush=True),lambda *a:None,lambda _:None,
              catalog=catalog,router=router,conversation_id=cid)
    connection=asyncio.create_task(core.run());voice=None;task=None;reference=None
    report={'microphone_opened':False,'admitted':False,'end_call_acknowledged':False}
    try:
        for _ in range(150):
            if core.session and core.session.registered.is_set() and core._opened.is_set():break
            await asyncio.sleep(.2)
        else:raise TimeoutError('Control not ready')
        voice=VoiceController(core,lambda s:print(s,flush=True),audio_factory=lambda:WavAudio(source,output))
        task=asyncio.create_task(voice.run())
        for _ in range(250):
            if voice.call:
                reference=voice.call
                if reference.ready.is_set():
                    report['admitted']=True;break
            if task.done():
                await task;break
            await asyncio.sleep(.2)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        if reference:
            report['end_call_acknowledged']=reference.end_call_acknowledged
            report['cleanup_error']=type(reference.cleanup_error).__name__ if reference.cleanup_error else None
        report['control_still_registered']=bool(core.session and core.session.registered.is_set())
    except Exception as exc:
        report['error']=type(exc).__name__
    finally:
        if task and not task.done():
            task.cancel();await asyncio.gather(task,return_exceptions=True)
        core.stop.set()
        try:await asyncio.wait_for(connection,8)
        except (Exception,asyncio.CancelledError):
            connection.cancel();await asyncio.gather(connection,return_exceptions=True)
        router.close();catalog.close();lock.unlock()
    (root/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report),flush=True)
    return report['admitted'] and report['end_call_acknowledged']


if __name__=='__main__':
    raise SystemExit(0 if asyncio.run(main()) else 1)
