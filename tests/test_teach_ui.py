import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPlainTextEdit, QPushButton, QWidget

from musebridge.context_snapshot import SKILL_NAME, SKILL_VERSION
from musebridge.teach_ui import CONFIRMATION_KEY, record_confirmation, show_teach, teach_request


class _Catalog:
    def __init__(self):
        self.preferences = {}

    def preference(self, key, value=None, default=None):
        if value is not None:
            self.preferences[key] = value
        return self.preferences.get(key, default)


class _Registered:
    def is_set(self):
        return True


class _Worker:
    def __init__(self):
        self.catalog = _Catalog()
        self.core = type("Core", (), {"session": type("Session", (), {"registered": _Registered()})()})()
        self.pending_sends = set()
        self.sent = []
        self.docs = [{"id": "doc-1", "filename": "draft.docx"}]
        self.router = type("Router", (), {})()
        self.router.artifacts = type("Artifacts", (), {"drafts": lambda _self, cid: self.docs})()

    def send(self, *args, **kwargs):
        self.sent.append((args, kwargs))
        return True


def _application():
    return QApplication.instance() or QApplication([])


def _window():
    _application()
    window = QWidget()
    window.current_id = "main"
    window.activity = QLabel()
    window.worker = _Worker()
    window.input = QPlainTextEdit()
    window.input.setPlainText("unsent composer draft")
    window.pending_image = object()
    return window


def _run_dialog(monkeypatch, click_text=None, observed=None):
    def exec_dialog(dialog):
        buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
        if observed is not None:
            observed.update({"title": dialog.windowTitle(), "buttons": list(buttons),
                             "preview": next((item.toPlainText() for item in dialog.findChildren(QPlainTextEdit)), "")})
        if click_text:
            buttons[click_text].click()
        return 0

    monkeypatch.setattr(QDialog, "exec", exec_dialog)


def test_request_and_confirmation_are_explicit_and_versioned():
    request = teach_request()
    assert "bridge.context(section=\"skill\")" in request
    assert SKILL_NAME in request and f"version {SKILL_VERSION}" in request
    assert "do not claim installation" in request
    catalog = _Catalog()
    assert catalog.preference(CONFIRMATION_KEY, default=0) == 0
    record_confirmation(catalog)
    assert catalog.preference(CONFIRMATION_KEY, default=0) == SKILL_VERSION


def test_opening_teach_dialog_does_not_send_or_record_confirmation(monkeypatch):
    window = _window()
    observed = {}
    _run_dialog(monkeypatch, observed=observed)
    show_teach(window)
    assert observed["title"] == "Teach Muse this PC"
    assert "Send skill request" in observed["buttons"]
    assert "Muse confirmed it saved this skill" in " ".join(observed["buttons"])
    assert SKILL_NAME in observed["preview"]
    assert window.worker.sent == []
    assert window.worker.catalog.preference(CONFIRMATION_KEY, default=0) == 0
    window.close()


def test_send_button_sends_only_skill_request_and_preserves_drafts(monkeypatch):
    window = _window()
    before_text = window.input.toPlainText()
    before_image = window.pending_image
    before_docs = list(window.worker.docs)
    _run_dialog(monkeypatch, click_text="Send skill request")
    show_teach(window)
    assert len(window.worker.sent) == 1
    args, kwargs = window.worker.sent[0]
    assert args[0] == teach_request() and args[1] is None
    assert kwargs == {"artifact_ids": (), "preserve_composer": True}
    assert window.input.toPlainText() == before_text
    assert window.pending_image is before_image
    assert window.worker.docs == before_docs
    assert window.worker.catalog.preference(CONFIRMATION_KEY, default=0) == 0
    window.close()


def test_acknowledged_teach_preserves_identical_composer_but_manual_send_clears(monkeypatch):
    import asyncio
    from concurrent.futures import Future
    from types import SimpleNamespace
    from musebridge.app import Worker,Window
    window=_window()
    window.input.setPlainText(teach_request())
    window.load_attachment=lambda:None
    class Signal:
        def __init__(self,callback=lambda *_:None):self.callback=callback
        def emit(self,*args):self.callback(*args)
    async def stop():pass
    async def send(*_args,**_kwargs):return {'message_id':'ack'}
    worker=window.worker
    worker.conversation_id='main';worker.core.send=send;worker.loop=object()
    worker.goal=SimpleNamespace(stop=stop)
    worker.router.vision=SimpleNamespace(hold=lambda *_args,**_kwargs:None)
    worker.status=worker.activity=worker.send_state=Signal()
    worker.manual_ack=Signal(lambda cid,text:Window.confirm_manual_draft(window,cid,text))
    def dispatch(coroutine,_loop):
        future=Future();future.set_result(asyncio.run(coroutine));return future
    monkeypatch.setattr(asyncio,'run_coroutine_threadsafe',dispatch)
    worker.send=lambda *args,**kwargs:Worker.send(worker,*args,**kwargs)
    _run_dialog(monkeypatch,click_text='Send skill request')
    show_teach(window)
    assert window.input.toPlainText()==teach_request()
    worker.send(teach_request(),None,artifact_ids=())
    assert window.input.toPlainText()==''
    window.close()


def test_send_requires_connection_and_confirmation_button_is_separate(monkeypatch):
    window = _window()
    window.worker.core = None
    _run_dialog(monkeypatch, click_text="Send skill request")
    show_teach(window)
    assert window.worker.sent == []
    assert "Connect before teaching" in window.activity.text()
    assert window.worker.catalog.preference(CONFIRMATION_KEY, default=0) == 0

    _run_dialog(monkeypatch, click_text="Muse confirmed it saved this skill — record confirmation")
    show_teach(window)
    assert window.worker.sent == []
    assert window.worker.catalog.preference(CONFIRMATION_KEY, default=0) == SKILL_VERSION
    assert "Recorded your confirmation" in window.activity.text()
    window.close()


def test_pending_send_blocks_skill_request_without_touching_composer(monkeypatch):
    window = _window()
    window.worker.pending_sends.add("main")
    before_text = window.input.toPlainText()
    _run_dialog(monkeypatch, click_text="Send skill request")
    show_teach(window)
    assert window.worker.sent == []
    assert window.input.toPlainText() == before_text
    assert "Wait for the current send" in window.activity.text()
    window.close()
