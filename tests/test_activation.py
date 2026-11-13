import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time

import pytest
from PySide6.QtCore import QObject, QIODevice
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from musebridge.activation import (
    ACTIVATION_MESSAGE,
    MAX_MESSAGE_BYTES,
    _server_name,
    install_activation,
    request_activation,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class OwnerWindow(QObject):
    pass


def _pump(app, predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    app.processEvents()
    return predicate()


def _send_raw(app, directory, data):
    socket = QLocalSocket()
    socket.connectToServer(_server_name(directory), QIODevice.OpenModeFlag.ReadWrite)
    assert socket.waitForConnected(700), socket.errorString()
    assert socket.write(data) == len(data)
    socket.disconnectFromServer()
    time.sleep(0.1)
    socket.abort()


def test_request_activation_invokes_owner_callback_once(app, tmp_path):
    window = OwnerWindow()
    calls = []
    server = install_activation(window, tmp_path, lambda: calls.append("show"))
    try:
        assert server.parent() is window
        # The listener is installed, but no server events are processed until
        # after the other-instance request has returned.
        assert request_activation(tmp_path) is True
        assert _pump(app, lambda: len(calls) == 1)
        app.processEvents()
        assert calls == ["show"]
    finally:
        server.close()
        app.processEvents()


def test_multiple_queued_clients_each_invoke_once(app, tmp_path):
    window = OwnerWindow()
    calls = []
    server = install_activation(window, tmp_path, lambda: calls.append("show"))
    try:
        assert request_activation(tmp_path) is True
        assert request_activation(tmp_path) is True
        assert _pump(app, lambda: len(calls) == 2)
        assert calls == ["show", "show"]
    finally:
        server.close()
        app.processEvents()


@pytest.mark.parametrize("payload", [b"show", b"SHOW\n", b"show\nextra", b"x" * (MAX_MESSAGE_BYTES + 1)])
def test_invalid_commands_are_ignored(app, tmp_path, payload):
    window = OwnerWindow()
    calls = []
    server = install_activation(window, tmp_path, lambda: calls.append("show"))
    try:
        _send_raw(app, tmp_path, payload)
        _pump(app, lambda: False, timeout=0.05)
        assert calls == []
    finally:
        server.close()
        app.processEvents()


def test_profile_names_are_hashed_and_isolated(tmp_path):
    first = _server_name(tmp_path / "one")
    second = _server_name(tmp_path / "two")
    equivalent = _server_name(tmp_path / "." / "one")
    assert first == equivalent
    assert first != second
    assert str(tmp_path) not in first
    assert len(first) < 100


def test_server_restricts_access_to_current_user(app, tmp_path):
    window = OwnerWindow()
    server = install_activation(window, tmp_path, lambda: None)
    try:
        assert server.socketOptions() == QLocalServer.SocketOption.UserAccessOption
        assert server.isListening()
    finally:
        server.close()
        app.processEvents()


def test_listener_cleanup_stops_future_activation_connections(app, tmp_path):
    window = OwnerWindow()
    first = install_activation(window, tmp_path, lambda: None)
    assert first.isListening()
    first.close()
    app.processEvents()
    assert not first.isListening()
    assert request_activation(tmp_path) is False
