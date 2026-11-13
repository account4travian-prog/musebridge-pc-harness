import asyncio
import base64
import json
import queue
import sys
import threading

import pytest
from musebridge.core import Core, Transcript
from musebridge.serial_ble import SerialTransport, decode_line
from musebridge.storage import Store
from musebridge.pairing import OnlineNetwork, get_identity


def test_dpapi_and_stable_identity(tmp_path):
    store = Store(tmp_path)
    secret = 'private-test-token'
    store.save('pairing', {'access_token': secret})
    assert secret.encode() not in (tmp_path / 'pairing.dpapi').read_bytes()
    assert Store(tmp_path).load('pairing') == {'access_token': secret}
    assert get_identity(store) == get_identity(Store(tmp_path))
    store.delete('pairing')
    assert store.load('pairing') is None


def test_no_linux_service_imports():
    assert 'musegadget.executor' not in sys.modules
    assert 'musegadget.service' not in sys.modules
    assert 'musegadget.ble_server' not in sys.modules


def test_transcript_updates_same_message_and_suppresses_echo():
    transcript = Transcript()
    transcript.remember('user-1')
    assert transcript.feed({'type': 'event', 'event': 'message.user',
        'payload': {'id': 'user-1', 'text': 'hello'}}) is None
    event = {'type': 'event', 'event': 'message.assistant',
             'payload': {'id': 'reply', 'display_text': 'Hi'}}
    result = transcript.feed(event)
    assert result == ('reply', 'Muse', 'Hi')
    event['payload']['display_text'] = 'Hi again'
    assert transcript.feed(event) == ('reply', 'Muse', 'Hi again')
    assert len(transcript.replies) == 1


class FakeSerial:
    def __init__(self, *args, **kwargs):
        self.writes = []
        self.closed = False
    def write(self, value):
        self.writes.append(json.loads(value))
    def close(self):
        self.closed = True


def transport():
    writes, disconnects = [], []
    instance = SerialTransport('COM1', 'MuseGadgetABCDEF', writes.append,
                               lambda: disconnects.append(True), serial_factory=FakeSerial)
    return instance, writes, disconnects


def test_old_boot_and_connection_packets_are_ignored():
    bridge, writes, _ = transport()
    bridge._event({'event': 'hello', 'epoch': 100})
    bridge._event({'event': 'connect', 'epoch': 100, 'gen': 2})
    packet = {'event': 'rx', 'epoch': 100, 'gen': 1, 'data': base64.b64encode(b'stale').decode()}
    bridge._event(packet)
    assert writes == []
    packet['gen'] = 2
    bridge._event(packet)
    assert writes == [b'stale']
    packet['epoch'] = 99
    bridge._event(packet)
    assert len(writes) == 1


def test_tx_requires_matching_notification_ack():
    bridge, _, _ = transport()
    bridge._event({'event': 'hello', 'epoch': 100})
    bridge._event({'event': 'connect', 'epoch': 100, 'gen': 2})
    def ack():
        import time
        while not bridge.pending:
            time.sleep(.001)
        identifier = next(iter(bridge.pending))
        bridge._event({'event': 'ack', 'epoch': 100, 'gen': 2, 'id': identifier, 'ok': True})
    worker = threading.Thread(target=ack)
    worker.start()
    bridge.send_packets([b'hello'])
    worker.join()
    assert bridge.pending == {}
    bridge.connected = False
    with pytest.raises(ConnectionError):
        bridge.send_packets([b'late'])


def test_frame_validation():
    for raw in (b'[]', b'{"v":2}', b'x' * 4097):
        with pytest.raises(ValueError):
            decode_line(raw)


def test_partial_serial_lines_survive_timeout():
    bridge, _, _ = transport()
    chunks = iter([b'{"v":1,"event":"hel', b'', b'lo","epoch":123}\n'])
    def read(*args):
        try:
            return next(chunks)
        except StopIteration:
            bridge.stop()
            return b''
    bridge.serial.read_until = read
    bridge.run()
    assert bridge.epoch == 123
    assert bridge.serial.closed


def test_health_probe_only_and_disconnected_send(tmp_path):
    async def check():
        core = Core(Store(tmp_path), lambda _: None, lambda *a: None, lambda _: None)
        assert core.tool('bridge.health', {})['ok']
        assert not core.tool('system.run', {})['ok']
        with pytest.raises(ConnectionError):
            await core.send('hello')
        await core.run()  # No pairing: must return, not open a network connection.
    asyncio.run(check())


def test_network_scan_contract():
    assert OnlineNetwork().current_connection_entry()['secure'] is False


@pytest.mark.parametrize('response', [
    {'message_id': 'sent-1'},
    {'result': {'message_id': 'sent-1'}},
])
def test_send_unwraps_sdk_result_before_reading_ack(tmp_path, response):
    async def check():
        shown, statuses = [], []
        core = Core(Store(tmp_path), lambda _: None, lambda *a: shown.append(a), statuses.append)
        class Session:
            chat_subscribed = asyncio.Event()
            calls = 0
            async def send_chat(self, text):
                self.calls += 1
                return {'ok': True, 'status': 200, 'response': response}
        session = Session()
        session.chat_subscribed.set()
        core.session = session
        await core.send('Delivered message')
        assert session.calls == 1
        assert shown == [('sent-1', 'You', 'Delivered message')]
        assert statuses == ['Message acknowledged']
        assert 'sent-1' in core.transcript.seen
    asyncio.run(check())


@pytest.mark.parametrize('result,exception', [
    ({'ok': True, 'response': {}}, ValueError),
    ({'ok': True}, ValueError),
    ({'ok': False, 'status': 403}, Exception),
])
def test_rejected_or_ambiguous_send_is_never_replayed(tmp_path, result, exception):
    async def check():
        shown = []
        core = Core(Store(tmp_path), lambda _: None, lambda *a: shown.append(a), lambda _: None)
        class Session:
            chat_subscribed = asyncio.Event()
            calls = 0
            async def send_chat(self, text):
                self.calls += 1
                return result
        session = Session()
        session.chat_subscribed.set()
        core.session = session
        with pytest.raises(exception):
            await core.send('Do this once')
        assert session.calls == 1
        assert shown == []
    asyncio.run(check())
