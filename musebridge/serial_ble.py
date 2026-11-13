"""Bounded newline JSON transport. ATT packets are base64, not decoded JSON.

Each firmware boot has a random epoch; each BLE connection a generation.
TX is acknowledged only after notify was accepted, preserving backpressure.
"""
import base64
import json
import queue
import threading
import time

MAX_LINE = 4096


def decode_line(line):
    if len(line) > MAX_LINE:
        raise ValueError('serial frame too large')
    obj = json.loads(line)
    if not isinstance(obj, dict) or obj.get('v') != 1:
        raise ValueError('unsupported serial protocol')
    return obj


class SerialTransport:
    def __init__(self, port, name, on_write, on_disconnect, serial_factory=None):
        hardware = serial_factory is None
        if serial_factory is None:
            from serial import Serial
            serial_factory = Serial
        self.serial = serial_factory(port, 115200, timeout=0.2, write_timeout=2)
        if hardware:
            # Windows initializes CH343 modem-control lines asserted. Release
            # BOOT, pulse RESET, then leave both lines released for application
            # startup. Merely opening the port can otherwise hold this clone.
            self.serial.dtr = False
            self.serial.rts = True
            time.sleep(0.15)
            self.serial.rts = False
        self.name = name
        self.on_write, self.on_disconnect = on_write, on_disconnect
        self.epoch = None
        self.generation = 0
        self.connected = False
        self._mtu = 23
        self._stop = threading.Event()
        self.ready = threading.Event()
        self.lock = threading.Lock()
        self.pending = {}
        self.sequence = 0
        self.error = None

    def _send(self, obj):
        raw = json.dumps({'v': 1, **obj}, separators=(',', ':')).encode() + b'\n'
        if len(raw) > MAX_LINE:
            raise ValueError('serial frame too large')
        with self.lock:
            self.serial.write(raw)

    def mtu(self):
        return self._mtu

    def send_packets(self, packets):
        epoch, generation = self.epoch, self.generation
        for packet in packets:
            if not self.connected or (epoch, generation) != (self.epoch, self.generation):
                raise ConnectionError('BLE connection changed')
            self.sequence += 1
            identifier = self.sequence
            ack = queue.Queue(maxsize=1)
            self.pending[identifier] = ack
            try:
                self._send({'op': 'tx', 'epoch': epoch, 'gen': generation,
                            'id': identifier, 'data': base64.b64encode(packet).decode()})
                response = ack.get(timeout=3)
                if not response:
                    raise ConnectionError('BLE notification refused')
            except queue.Empty:
                raise TimeoutError('BLE notification acknowledgment timed out') from None
            finally:
                self.pending.pop(identifier, None)
            time.sleep(0.02)

    def disconnect(self, delay=0):
        def close_peer():
            if not self._stop.is_set():
                self._send({'op': 'disconnect', 'epoch': self.epoch, 'gen': self.generation})
        timer = threading.Timer(delay, close_peer)
        timer.daemon = True
        timer.start()

    def stop(self):
        self._stop.set()

    def _event(self, obj):
        event = obj.get('event')
        if event == 'hello':
            if self.connected:
                self.on_disconnect()
            self.connected = False
            self.epoch = obj['epoch']
            self._send({'op': 'configure', 'epoch': self.epoch, 'name': self.name})
            return
        if obj.get('epoch') != self.epoch:
            return
        if event == 'ready':
            self.ready.set()
        elif event == 'connect':
            self.on_disconnect()
            self.generation = obj['gen']
            self.connected = True
            self._mtu = 23
        elif obj.get('gen') != self.generation:
            return
        elif event == 'disconnect':
            self.connected = False
            self.on_disconnect()
        elif event == 'mtu':
            self._mtu = max(23, min(256, int(obj['mtu'])))
        elif event == 'rx' and self.connected:
            packet = base64.b64decode(obj['data'], validate=True)
            if len(packet) > 512:
                raise ValueError('ATT write too large')
            self.on_write(packet)
        elif event == 'ack':
            pending = self.pending.get(obj.get('id'))
            if pending is not None and pending.empty():
                pending.put_nowait(obj.get('ok') is True)

    def run(self):
        start = time.monotonic()
        keepalive = 0
        buffered = bytearray()
        try:
            self._send({'op': 'hello'})
            while not self._stop.is_set():
                if self.epoch is not None and time.monotonic() - keepalive > 3:
                    self._send({'op': 'keepalive', 'epoch': self.epoch})
                    keepalive = time.monotonic()
                chunk = self.serial.read_until(b'\n', MAX_LINE + 1)
                if not chunk:
                    if not self.ready.is_set() and time.monotonic() - start > 12:
                        raise TimeoutError('No bridge firmware response on this USB port')
                    continue
                buffered.extend(chunk)
                if len(buffered) > MAX_LINE:
                    raise ValueError('oversized serial frame')
                if not buffered.endswith(b'\n'):
                    continue
                line = bytes(buffered)
                buffered.clear()
                # Boot ROM / Arduino startup text is not protocol data.
                if not line.startswith(b'{'):
                    continue
                self._event(decode_line(line))
        finally:
            self.connected = False
            self.on_disconnect()
            for pending in list(self.pending.values()):
                if pending.empty():
                    pending.put_nowait(False)
            self.serial.close()
