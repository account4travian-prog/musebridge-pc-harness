"""Current-user local IPC for asking an existing MuseBridge window to show."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import time

from PySide6.QtCore import QIODevice, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket


ACTIVATION_MESSAGE = b"show\n"
MAX_MESSAGE_BYTES = 16
CLIENT_TIMEOUT_MS = 1000


def _server_name(directory) -> str:
    profile = Path(directory).expanduser().resolve(strict=False)
    identity = os.path.normcase(str(profile)).casefold().encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()
    return "MuseBridge-activation-" + digest


def request_activation(directory) -> bool:
    """Send the one allowed activation message to this profile's local instance."""
    socket = QLocalSocket()
    deadline = time.monotonic() + 1.0
    sent = False
    try:
        socket.connectToServer(_server_name(directory), QIODevice.OpenModeFlag.ReadWrite)
        if not socket.waitForConnected(max(1, int((deadline - time.monotonic()) * 1000))):
            return False
        if socket.write(ACTIVATION_MESSAGE) != len(ACTIVATION_MESSAGE):
            return False
        socket.disconnectFromServer()
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms > 0:
            # On Windows QLocalSocket may keep bytes queued while a named-pipe
            # peer drains them; a short bounded grace period lets Qt finish the
            # graceful close without waiting for an application-level reply.
            time.sleep(min(0.1, remaining_ms / 1000))
        sent = True
        return True
    except Exception:
        return False
    finally:
        if not sent:
            socket.abort()
        socket.deleteLater()


def install_activation(window, directory, callback):
    """Listen for one exact ``show\n`` message and invoke the owner callback."""
    if not callable(callback):
        raise ValueError("activation callback must be callable")
    server = QLocalServer(window)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    name = _server_name(directory)
    if not server.listen(name):
        message = server.errorString()
        server.close()
        server.deleteLater()
        raise OSError("Could not install local activation listener: " + message)
    server._activation_clients = set()

    def accept_connections():
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            if socket is None:
                continue
            socket.setReadBufferSize(MAX_MESSAGE_BYTES + 1)
            server._activation_clients.add(socket)
            state = {"buffer": bytearray(), "done": False, "timed_out": False}
            timer = QTimer(socket)
            timer.setSingleShot(True)
            timer.setInterval(CLIENT_TIMEOUT_MS)

            def reject_slow_client(st=state, client=socket):
                if st["done"]:
                    return
                st["timed_out"] = True
                st["done"] = True
                try:
                    client.abort()
                except RuntimeError:
                    pass
                server._activation_clients.discard(client)
                try:
                    client.deleteLater()
                except RuntimeError:
                    pass

            def collect_data(st=state, client=socket):
                if st["done"]:
                    return
                try:
                    allowed = MAX_MESSAGE_BYTES + 1 - len(st["buffer"])
                    chunk = bytes(client.read(min(allowed, client.bytesAvailable())))
                except RuntimeError:
                    st["done"] = True
                    return
                if len(st["buffer"]) + len(chunk) > MAX_MESSAGE_BYTES:
                    st["done"] = True
                    client.abort()
                    server._activation_clients.discard(client)
                    client.deleteLater()
                    return
                st["buffer"].extend(chunk)

            def finish_client(st=state, client=socket, local_timer=timer, collect=collect_data):
                if st["done"]:
                    return
                collect(st, client)
                if st["done"]:
                    return
                if client.state() != QLocalSocket.LocalSocketState.UnconnectedState:
                    return
                try:
                    local_timer.stop()
                except RuntimeError:
                    return
                st["done"] = True
                valid = not st["timed_out"] and bytes(st["buffer"]) == ACTIVATION_MESSAGE
                if valid:
                    try:
                        callback()
                    except Exception:
                        pass
                try:
                    server._activation_clients.discard(client)
                    client.deleteLater()
                except RuntimeError:
                    pass

            def schedule_finish(st=state, client=socket, local_timer=timer,
                                collect=collect_data, finish=finish_client):
                if st["done"]:
                    return
                collect(st, client)
                if st["done"]:
                    return
                if client.state() == QLocalSocket.LocalSocketState.UnconnectedState:
                    QTimer.singleShot(0, finish)

            socket.readyRead.connect(collect_data)
            socket.disconnected.connect(schedule_finish)
            timer.timeout.connect(reject_slow_client)
            timer.start()
            collect_data(state, socket)
            if socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
                schedule_finish()

    server.newConnection.connect(accept_connections)
    return server
