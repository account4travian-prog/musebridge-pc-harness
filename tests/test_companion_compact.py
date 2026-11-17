import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path

import pytest
from PySide6.QtCore import Qt,QPoint,QPointF,QEvent
from PySide6.QtGui import QEnterEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QFileDialog

from musebridge.app import Window,Worker
from musebridge.storage import Store


@pytest.fixture
def window(tmp_path):
    app=QApplication.instance() or QApplication([])
    w=Window(Worker(Store(tmp_path/'state')))
    yield w
    w.worker.core=None
    w.close();app.processEvents()


def test_idle_hover_and_escape_leave_main_draft_and_side_view_intact(window):
    w=window;pet=w.companion
    pet.present(explicit=True)
    assert pet.cutout_mode and pet.face.frame.hasAlphaChannel()
    assert pet.controls.isHidden() and pet.notice.isHidden() and pet.quick.isHidden()
    assert not pet.mask().contains(QPoint(2,2))  # Wide transparent margins pass input through.
    QApplication.sendEvent(pet,QEnterEvent(QPointF(110,20),QPointF(110,20),QPointF(110,20)))
    assert pet.controls.isVisible() and pet.handle.isHidden()
    QApplication.sendEvent(pet,QEvent(QEvent.Type.Leave));QTest.qWait(300)
    assert pet.controls.isHidden()
    selected=w.current_id
    w.input.setPlainText('side draft')
    QTest.mouseClick(pet.face,Qt.MouseButton.LeftButton)
    assert pet.quick.isVisible() and not pet.panel.isVisible()
    pet.quick.input.setPlainText('Main quick draft')
    assert pet.panel.input.toPlainText()=='Main quick draft'
    QTest.keyClick(pet.quick.input,Qt.Key.Key_Escape)
    assert pet.quick.isHidden() and pet.panel.input.toPlainText()=='Main quick draft'
    assert w.current_id==selected and w.input.toPlainText()=='side draft'


def test_quick_send_uses_main_and_ack_preserves_newer_mirrored_draft(window):
    w=window;pet=w.companion;calls=[]
    w.worker.send=lambda text,image,**kw:calls.append((text,kw)) or True
    pet.open_quick_chat();pet.quick.input.setPlainText('first Main request')
    QTest.keyClick(pet.quick.input,Qt.Key.Key_Return)
    assert calls==[('first Main request',{'conversation_id':'main'})]
    pet.quick.input.setPlainText('newer draft')
    w.worker.manual_ack.emit('main','first Main request')
    assert pet.quick.input.toPlainText()==pet.panel.input.toPlainText()=='newer draft'
    w.worker.manual_ack.emit(w.current_id,'newer draft')
    assert pet.quick.input.toPlainText()=='newer draft'


def test_quick_plus_imports_document_only_into_main_without_plugin_placeholders(window,tmp_path,monkeypatch):
    w=window;pet=w.companion
    source=tmp_path/'selected.md';source.write_text('Owner selected data',encoding='utf-8')
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a,**kw:(str(source),''))
    menu=pet.quick.add_menu()
    names=[a.text() for a in menu.actions() if not a.isSeparator()]
    assert 'Attach file…' in names and 'Available Main tools…' in names
    next(a for a in menu.actions() if a.text()=='Attach file…').trigger()
    assert len(w.worker.router.artifacts.drafts('main'))==1
    assert w.worker.router.artifacts.drafts(w.current_id)==[]
    assert 'selected.md' in pet.quick.attachments.text()
    assert not pet.panel.isVisible()


def test_voice_controls_open_without_starting_capture_and_keep_selected_side(window):
    from musebridge.voice_ui import show_voice
    w=window;selected=w.current_id
    other=w.worker.catalog.create_conversation('Voice target')['id']
    calls=[]
    w.worker.start_voice=lambda **kw:calls.append(kw) or True
    controls=show_voice(w,w.companion)
    assert calls==[] and w.worker.voice is None and controls.conversations.findData('main')==-1
    controls.conversations.setCurrentIndex(controls.conversations.findData(other))
    controls.start.click()
    assert calls==[{'conversation_id':other}]
    assert w.worker.conversation_id==w.current_id==selected
