import asyncio
from musebridge.voice import MutedAudio
from types import SimpleNamespace
import pytest
from musebridge.voice import resolve_voice_target,VoiceController,VoicePreflightError


def test_muted_audio_emits_silence_not_buffered_microphone():
    class Audio:
        async def frames(self):
            yield b'\x01\x02'
            yield b'\x03\x04'
        def write(self,pcm):pass
        def flush(self):pass
        def close(self):pass
    async def check():
        audio=MutedAudio(Audio())
        iterator=audio.frames()
        assert await anext(iterator)==b'\x01\x02'
        audio.muted=True
        assert await anext(iterator)==b'\0\0'
    asyncio.run(check())


def test_voice_target_is_captured_independently_of_view_and_global_remote_flag():
    class Catalog:
        def conversation(self,cid):return {'id':cid,'archived':False}
    async def scenario():
        calls=[]
        core=SimpleNamespace(catalog=Catalog(),conversation_id='side-a',parallel_enabled=True,_remote_exists=False)
        async def ensure(cid):
            calls.append(cid);core.conversation_id='side-b'
            return SimpleNamespace(exists=True)
        core.live_chats=SimpleNamespace(ensure=ensure)
        core.remote_id=lambda cid:'remote-'+cid
        voice=VoiceController(core,lambda _:None,conversation_id='side-a')
        core.conversation_id='side-b'
        assert await resolve_voice_target(core,voice.conversation_id)=='remote-side-a'
        assert calls==['side-a'] and core.conversation_id=='side-b'
        with pytest.raises(VoicePreflightError,match='Main remains text-only'):
            await resolve_voice_target(core,'main')
        async def not_created(cid):return SimpleNamespace(exists=False)
        core.live_chats.ensure=not_created
        with pytest.raises(VoicePreflightError,match='first text'):
            await resolve_voice_target(core,'new-side')
    asyncio.run(scenario())


def test_view_selection_does_not_hang_up_a_targeted_voice_call(tmp_path):
    from musebridge.app import Worker
    from musebridge.storage import Store
    from concurrent.futures import Future
    import threading
    worker=Worker(Store(tmp_path))
    seen=threading.Event()
    class Core:
        parallel_enabled=False
        def live_conversations(self):return ()
        async def select_conversation(self,cid):seen.set()
    try:
        worker.core=Core();worker.voice_future=Future();worker.voice_active=True
        worker.select('another-side')
        assert seen.wait(2)
        assert not worker.voice_future.cancelled() and worker.voice_active
    finally:
        worker.core=None;worker.voice_active=False;worker.close()
