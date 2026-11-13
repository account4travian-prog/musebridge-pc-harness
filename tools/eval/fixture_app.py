"""Owned local browser/desktop/vision fixtures for MB-25b evaluations.

All fixtures are synthetic. The browser server binds only to IPv4 loopback;
desktop windows are shown without activation. Nothing connects to a real site,
reads another window, changes OS settings, or touches the live Muse profile.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import urllib.parse

from PySide6.QtWidgets import QWidget


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def _browser_server(port: int, private_state_dir: Path) -> int:
    code = f'{secrets.randbelow(900000) + 100000:06d}'

    class Handler(BaseHTTPRequestHandler):
        server_version = 'MuseBridgeFixture/1'

        def log_message(self, _format, *args):
            # Request paths/form contents never go to the harness log.
            return

        def _respond(self, status, body='', *, headers=()):
            data = body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            for key, value in headers:
                self.send_header(key, value)
            self.end_headers()
            if data:
                self.wfile.write(data)

        def _body(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                return {}
            if not 0 <= length <= 16384:
                return {}
            values = urllib.parse.parse_qs(self.rfile.read(length).decode('utf-8', errors='replace'))
            return {key: entries[-1] for key, entries in values.items() if entries}

        def _signed_in(self):
            return 'mbfixture=1' in self.headers.get('Cookie', '')

        def do_GET(self):
            if self.path in ('/', '/login'):
                self._respond(200, '''<!doctype html><html><head><title>Local fixture sign in</title></head><body>
<h1>Fixture sign in</h1><form action="/login" method="post">
<label for="username">Username</label><input id="username" name="username" autocomplete="off">
<label for="password">Password</label><input id="password" name="password" type="password" autocomplete="off">
<button type="submit">Sign in</button></form></body></html>''')
                return
            if self.path == '/settings':
                if not self._signed_in():
                    self._respond(302, headers=(('Location', '/login'),))
                    return
                self._respond(200, '''<!doctype html><html><head><title>Fixture settings</title></head><body>
<h1>Fixture settings</h1><form action="/save" method="post">
<label for="mode">Mode</label><select id="mode" name="mode">
<option value="standard">Standard</option><option value="advanced">Advanced</option>
</select><button type="submit">Save</button></form></body></html>''')
                return
            if self.path == '/saved':
                if not self._signed_in() or not (private_state_dir / 'fixture_state.json').is_file():
                    self._respond(403, '<h1>No saved fixture setting</h1>')
                    return
                saved = json.loads((private_state_dir / 'fixture_state.json').read_text(encoding='utf-8'))
                self._respond(200, '<h1>Setting saved</h1><p>Confirmation code: ' + saved['confirmation_code'] + '</p>')
                return
            self._respond(404, '<h1>Not found</h1>')

        def do_POST(self):
            values = self._body()
            if self.path == '/login':
                if values.get('username') == 'fixture' and values.get('password') == 'test-only':
                    self._respond(303, headers=(('Location', '/settings'),
                                                ('Set-Cookie', 'mbfixture=1; Path=/; HttpOnly; SameSite=Strict')))
                else:
                    self._respond(401, '<h1>Fixture credentials not accepted</h1>')
                return
            if self.path == '/save':
                if not self._signed_in():
                    self._respond(403, '<h1>Sign in first</h1>')
                    return
                mode = values.get('mode')
                if mode not in ('standard', 'advanced'):
                    self._respond(400, '<h1>Choose a listed mode</h1>')
                    return
                _write_json(private_state_dir / 'fixture_state.json',
                            {'saved': True, 'mode': mode, 'confirmation_code': code})
                self._respond(303, headers=(('Location', '/saved'),))
                return
            self._respond(404, '<h1>Not found</h1>')

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    print(json.dumps({'url': f'http://127.0.0.1:{server.server_port}/login',
                      'confirmation_code': code}), flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
    return 0


def _desktop_window(state_dir: Path) -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication, QFormLayout, QLineEdit, QPushButton, QLabel, QVBoxLayout

    app = QApplication([])
    window = QWidget()
    window.setWindowTitle('MuseBridge evaluation fixture')
    window.setAccessibleName('MuseBridge evaluation fixture form')
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    window.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
    window.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    layout = QVBoxLayout(window)
    form = QFormLayout()
    fields = {}
    for key, label in (('name', 'Name'), ('topic', 'Topic'), ('amount', 'Amount')):
        field = QLineEdit()
        field.setObjectName(key)
        field.setAccessibleName(label)
        form.addRow(label, field)
        fields[key] = field
    layout.addLayout(form)
    status = QLabel('Enter the three requested fixture values.')
    status.setAccessibleName('Save status')
    layout.addWidget(status)
    button = QPushButton('Save')
    button.setAccessibleName('Save')

    def save():
        _write_json(state_dir / 'saved.json', {key: fields[key].text() for key in ('name', 'topic', 'amount')})
        status.setText('Saved')

    button.clicked.connect(save)
    layout.addWidget(button)
    _place_secondary(window, QGuiApplication, (380, 220))
    window.show()
    screen = _verify_secondary(window, app, QGuiApplication)
    print(json.dumps({'hwnd': int(window.winId()), 'pid': os.getpid(), **screen}), flush=True)
    return app.exec()


def _centered_rect(available, width: int, height: int):
    """Return a rectangle centered in a screen's available work area."""
    from PySide6.QtCore import QRect
    safe = available.adjusted(20, 20, -20, -20)
    width = min(int(width), safe.width())
    height = min(int(height), safe.height())
    x = safe.x() + (safe.width() - width) // 2
    y = safe.y() + (safe.height() - height) // 2
    return QRect(x,y,width,height)


def _place_secondary(window, gui_application, size):
    screens=list(gui_application.screens())
    primary=gui_application.primaryScreen()
    secondary=next((screen for screen in screens if screen is not primary),None)
    if secondary is None:
        raise RuntimeError('No non-primary display is available; fixture window was not opened')
    window.resize(*size)
    window.winId()
    native=window.windowHandle()
    if native is None:
        raise RuntimeError('Fixture native window could not be created')
    native.setScreen(secondary)
    window.setGeometry(_centered_rect(secondary.availableGeometry(), *size))
    return secondary


def _verify_secondary(window, app, gui_application):
    """Confirm the shown fixture is contained on a non-primary display."""
    app.processEvents()
    screen = window.screen()
    secondary = screen is not None and screen is not gui_application.primaryScreen()
    within = bool(secondary and screen.availableGeometry().contains(window.frameGeometry()))
    if not secondary or not within:
        window.close()
        raise RuntimeError('Fixture window did not land safely on a secondary display')
    return {'secondary_screen': True, 'within_secondary': True}


class _PixelCode(QWidget):
    def __init__(self, code):
        super().__init__()
        self._code = code
        self.setAccessibleName('Visual code fixture')

    def paintEvent(self, _event):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QFont, QPainter
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#101820'))
        font = QFont('Consolas', 34)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor('#f5f7fa'))
        painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), 'Reference code  ' + self._code)
        painter.end()


def _vision_window() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication, QVBoxLayout

    app = QApplication([])
    code = f'{secrets.randbelow(900000) + 100000:06d}'
    window = QWidget()
    window.setWindowTitle('MuseBridge visual fixture')
    window.setAccessibleName('MuseBridge visual fixture')
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    window.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
    window.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    layout = QVBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(_PixelCode(code))
    _place_secondary(window, QGuiApplication, (640, 240))
    window.show()
    screen = _verify_secondary(window, app, QGuiApplication)
    print(json.dumps({'hwnd': int(window.winId()), 'pid': os.getpid(), 'code': code, **screen}), flush=True)
    return app.exec()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('browser', 'desktop', 'vision'), required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args(argv)
    args.state_dir.mkdir(parents=True, exist_ok=True)
    state_dir = args.state_dir.resolve(strict=True)
    if args.mode == 'browser':
        if not 0 <= args.port <= 65535:
            parser.error('--port must be between 0 and 65535')
        private_state_dir = state_dir.parent / 'fixture-state'
        private_state_dir.mkdir(parents=True, exist_ok=False)
        return _browser_server(args.port, private_state_dir)
    if args.mode == 'desktop':
        return _desktop_window(state_dir)
    return _vision_window()


if __name__ == '__main__':
    raise SystemExit(main())
