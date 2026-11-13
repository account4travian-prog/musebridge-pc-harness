"""Existing SDK voice lifecycle, on a voice-only transport beside control."""
import asyncio
from musegadget import muse_api
from musegadget.link_client import DeviceDescription,LinkSession
from musegadget.voice_call import VoiceCall
from .pairing import get_identity


class MutedAudio:
    def __init__(self,audio):
        self.audio=audio
        self.muted=False

    async def frames(self):
        async for frame in self.audio.frames():
            yield b'\0'*len(frame) if self.muted else frame

    def write(self,pcm):self.audio.write(pcm)
    def flush(self):self.audio.flush()
    def close(self):self.audio.close()


class VoiceController:
    def __init__(self,core,status,audio_factory=None):
        self.core,self.status=core,status
        self.audio_factory=audio_factory
        self.audio=None
        self.call=None
        self.task=None

    async def run(self):
        core=self.core
        if core.session is None or not core.session.registered.is_set():
            raise ConnectionError('Connect to Muse first')
        if not core._remote_exists:
            raise ValueError('Send a first text message in this conversation before calling')
        cid=core.conversation_id
        thread_id=core.remote_id()
        if core.catalog and cid == 'main':
            raise ValueError('Use a dedicated conversation for voice to keep tools and call in the same thread')
        pairing=await core.refresh(core.store.load('pairing'))
        vms,status=await asyncio.to_thread(muse_api.fetch_vms_with_status,pairing['access_token'],
                                         muse_api.api_root(pairing.get('api_url_v2','')))
        matches=[v for v in vms if (v['vm_id'] or v['vm_name']) == core.vm_id]
        if len(matches)!=1:raise ConnectionError('Selected Muse runtime is unavailable')
        ident=get_identity(core.store)
        session=LinkSession(noise_host=pairing.get('noise_host') or 'hatch.metaaivm.com',
            vm_id=core.vm_id,vm_auth_token=matches[0]['vm_auth_token'],
            device=DeviceDescription(ident.node_id,'MuseBridge voice','0.7.0',{}),
            run_command=lambda *args:{'ok':False,'error':'voice-only'},
            register_device=False,app_id='hatch-web')
        stop=asyncio.Event()
        connection=asyncio.create_task(session.run(stop))
        voice_run=None
        try:
            await session.wait_transport_ready(35)
            if self.audio_factory is None:
                from musegadget.voice_audio import LiveAudio
                factory=LiveAudio
            else:factory=self.audio_factory
            # Capture begins only here, after explicit user initiation and readiness.
            self.audio=MutedAudio(factory())
            self.call=VoiceCall(session,device_id=ident.node_id,thread_id=thread_id,half_duplex=True)
            if core.router:
                voice_run=core.router.execution.begin_run(cid,'voice')
                core.router.execution.state(cid,voice_run,'acknowledged')
            self.status('Voice active · '+str(thread_id))
            return await self.call.run(self.audio.frames(),self.audio,realtime_input=True)
        finally:
            if voice_run:core.router.execution.state(cid,voice_run,'interrupted')
            if self.audio:self.audio.close()
            self.audio=None
            self.call=None
            stop.set()
            try:await asyncio.wait_for(connection,3)
            except (Exception,asyncio.CancelledError):
                connection.cancel();await asyncio.gather(connection,return_exceptions=True)
            self.status('Voice ended')

    def mute(self,muted):
        if self.audio:self.audio.muted=muted

    async def interrupt(self):
        if self.call:await self.call.barge_in()
