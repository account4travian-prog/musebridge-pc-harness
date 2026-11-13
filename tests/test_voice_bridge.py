import asyncio
from musebridge.voice import MutedAudio


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
