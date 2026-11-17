"""Companion routing, retained Main state and window lifecycle regressions."""
import asyncio
import os
import time
from io import BytesIO

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PIL import Image
import pytest
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from musebridge.app import Window, Worker
from musebridge.companion import clamped_position
from musebridge.media import from_bytes
from musebridge.overview_ui import refresh_overview
from musebridge.storage import Store


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    w = Window(Worker(Store(tmp_path/'state')))
    yield w
    w.worker.core = None
    w.companion.panel.input.setPlainText('')
    w.close()
    app.processEvents()


def wait_for(predicate):
    deadline = time.monotonic()+3
    while not predicate() and time.monotonic() < deadline:QTest.qWait(10)
    assert predicate()


def test_main_is_not_a_side_tab_and_opening_it_keeps_selection_draft_and_plan(window):
    w = window
    selected = w.current_id
    assert selected != 'main'
    assert 'main' not in w.nav_items and w.conversations.findData('main') == -1
    w.input.setPlainText('side draft')
    w.plan_editor.setPlainText('side plan in progress')
    w.companion.present(explicit=True)
    QTest.mouseClick(w.companion.face, Qt.MouseButton.LeftButton)
    assert w.companion.quick.isVisible() and not w.companion.panel.isVisible()
    assert w.current_id == w.worker.conversation_id == selected
    assert w.input.toPlainText() == 'side draft'
    assert w.plan_editor.toPlainText() == 'side plan in progress'
    # Compatibility navigation and overview must open the same Main panel.
    w.companion.panel.close()
    w.refresh_conversations('main');w.switch_conversation()
    assert w.current_id == selected and w.companion.panel.isVisible()
    refresh_overview(w)
    for i in range(w.overview_tree.topLevelItemCount()):
        item = w.overview_tree.topLevelItem(i)
        if item.data(0, Qt.ItemDataRole.UserRole) == 'main':w.overview_tree.setCurrentItem(item);break
    next(b for b in w.pages.widget(8).findChildren(QPushButton)
         if b.text() == 'Open selected conversation').click()
    assert w.current_id == selected


def test_simultaneous_main_and_side_sends_keep_targets_replies_and_acknowledgments_separate(window):
    w = window
    selected = w.current_id
    calls, gates = [], {}
    class Core:
        parallel_enabled = False
        def live_conversations(self):return ()
        async def send(self, text, **kwargs):
            cid = kwargs['conversation_id']
            gate = gates[cid] = asyncio.Event()
            calls.append((cid, text, kwargs))
            await gate.wait()
            if kwargs['image']:
                w.worker.attachments.acknowledge(cid, ['ack-'+cid], kwargs['image'])
            return {'message_id':'ack-'+cid}
    w.worker.core = Core()
    main = w.companion.panel
    raw = BytesIO();Image.new('RGB', (2, 2), 'blue').save(raw, 'PNG')
    attachment = from_bytes(raw.getvalue(), 'image/png', 'main-image.png')
    main.set_attachment(attachment)
    main.input.setPlainText('Main request')
    w.input.setPlainText('side request')
    main.submit();w.submit()
    wait_for(lambda:len(calls)==2)
    assert {(cid, text) for cid, text, _ in calls} == {('main', 'Main request'), (selected, 'side request')}
    assert next(kw for cid, _, kw in calls if cid=='main')['image'] == attachment
    assert next(kw for cid, _, kw in calls if cid==selected)['image'] is None
    assert w.current_id == w.worker.conversation_id == selected
    assert not main.send.isEnabled() and not w.send.isEnabled()
    w.worker.conversation_message.emit('main', 'same-id', 'Muse', 'Main <script>reply</script>')
    w.worker.conversation_message.emit(selected, 'same-id', 'Muse', 'Side reply')
    assert 'Main <script>reply</script>' in main.chat.toPlainText()
    assert 'Side reply' not in main.chat.toPlainText()
    assert 'Side reply' in w.chat.toPlainText() and 'Main' not in w.chat.toPlainText()
    # Typing a newer draft while Main awaits its acknowledgment must survive.
    main.input.setPlainText('newer Main draft')
    w.worker.loop.call_soon_threadsafe(gates['main'].set)
    wait_for(lambda:'main' not in w.worker.pending_sends)
    QTest.qWait(20)
    assert main.input.toPlainText() == 'newer Main draft'
    assert w.input.toPlainText() == 'side request' and not w.send.isEnabled()
    assert w.worker.attachments.draft('main') is None
    w.worker.loop.call_soon_threadsafe(gates[selected].set)
    wait_for(lambda:not w.worker.pending_sends)
    wait_for(lambda:w.input.toPlainText()=='')
    assert main.input.toPlainText() == 'newer Main draft'
    assert 'main' not in w.worker.queue.enabled


def test_unknown_main_send_retains_draft_and_attachment_and_does_not_clear_side(window):
    w = window
    class Core:
        parallel_enabled = False
        def live_conversations(self):return ()
        async def send(self, *args, **kwargs):raise ConnectionError('lost acknowledgment')
    w.worker.core = Core()
    main = w.companion.panel
    raw = BytesIO();Image.new('RGB', (2, 2), 'blue').save(raw, 'PNG')
    attachment = from_bytes(raw.getvalue(), 'image/png', 'uncertain.png')
    main.set_attachment(attachment)
    w.input.setPlainText('unrelated side draft')
    main.input.setPlainText('keep on uncertain send')
    main.submit()
    wait_for(lambda:'not confirmed' in main.activity.text())
    assert main.input.toPlainText() == 'keep on uncertain send'
    assert w.worker.attachments.draft('main') == attachment
    assert w.input.toPlainText() == 'unrelated side draft'
    assert 'not resent' in main.activity.text()
    assert not w.companion.panel.isVisible()  # Background results cannot show it.


def test_preexisting_main_history_and_draft_survive_restart_and_last_side_archive(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path/'state')
    worker = Worker(store)
    worker.catalog.preference('last_conversation', 'main')
    worker.catalog.draft('main', 'retained Main draft')
    worker.catalog.message('main', 'old', 'Muse', 'retained Main history')
    w = Window(worker)
    selected = w.current_id
    try:
        assert selected != 'main' and 'retained Main history' in w.companion.panel.chat.toPlainText()
        assert w.companion.panel.input.toPlainText() == 'retained Main draft'
        w.archive_conversation()
        assert w.current_id not in (selected, 'main')
        assert worker.catalog.conversation(selected)['archived'] == 1
        assert worker.catalog.draft('main') == 'retained Main draft'
        w.companion.panel.input.setPlainText('saved Main draft')
        side = w.current_id
    finally:w.close()
    second = Window(Worker(store))
    try:
        assert second.current_id == side
        assert second.companion.panel.input.toPlainText() == 'saved Main draft'
        assert 'retained Main history' in second.companion.panel.chat.toPlainText()
        assert 'main' not in second.nav_items
    finally:second.close();app.processEvents()


def test_main_workspace_and_policy_controls_do_not_rebind_side_chat(window, tmp_path):
    w = window
    main = w.companion.panel
    project = w.worker.catalog.create_project('Main workspace', tmp_path)
    side = w.current_id
    main.input.setPlainText('preserve Main text')
    main._bind_workspace(project['id'])
    main.profile.setCurrentIndex(main.profile.findData('read_only'))
    assert w.worker.catalog.conversation('main')['project_id'] == project['id']
    assert w.worker.catalog.conversation(side)['project_id'] is None
    assert w.worker.router.execution.policy('main')['profile'] == 'read_only'
    assert w.worker.catalog.draft('main') == 'preserve Main text'
    assert w.current_id == side and w.worker.conversation_id == side


def test_hide_minimize_recover_and_shutdown_preserve_main_draft(window):
    w = window
    w.show();QApplication.processEvents()
    companion = w.companion
    assert companion.isVisible()
    companion.open_chat()
    companion.panel.input.setPlainText('Main draft at hide')
    companion.hide_companion()
    assert not companion.isVisible() and not companion.panel.isVisible()
    assert w.worker.catalog.draft('main') == 'Main draft at hide'
    w.show_companion()
    assert companion.isVisible() and not companion.panel.isVisible()
    w.showMinimized();QApplication.processEvents()
    assert companion.isVisible()
    w.close()
    assert companion.closed and not companion.isVisible() and not companion.panel.isVisible()
    assert not w.worker.thread.is_alive()


def test_oversized_main_draft_refuses_app_close_without_stopping_timers(window):
    w = window
    w.companion.panel.input.setPlainText('x'*65537)
    assert not w.close()
    assert w.worker.thread.is_alive() and w.activity_timer.isActive()
    assert '64 KiB' in w.companion.panel.activity.text()
    w.companion.panel.input.setPlainText('recovered draft')
    assert w.close()


def test_artwork_failure_keeps_previous_avatar_and_preferences(window, tmp_path):
    pet = window.companion
    assert not pet.face.frame.isNull()
    pet.set_dark(True);pet.set_reduced_motion(True)
    source = tmp_path/'custom.png'
    Image.new('RGB', (16, 16), 'red').save(source)
    pet.import_avatar(source)
    previous = pet.face.frame.pixelColor(8, 8)
    source.write_bytes(b'invalid image')
    with pytest.raises(ValueError):pet.import_avatar(source)
    assert pet.face.frame.pixelColor(8, 8) == previous
    assert pet.catalog.preference('atlas_dark') and pet.catalog.preference('atlas_reduced_motion')
    assert pet.player is None  # Offscreen tests never allocate media decoders.
    pet.use_bundled()
    assert pet.custom == '' and not pet.face.frame.isNull()


def test_position_clamping_supports_removed_displays_and_negative_coordinates():
    available = QRect(-1920, 0, 1920, 1040)
    size = QSize(160, 230)
    for position in (QPoint(-4000, -50), QPoint(8000, 9000), QPoint(-1500, 100)):
        result = clamped_position(position, size, available)
        assert available.contains(QRect(result, size))


def test_connection_loss_cannot_leave_main_looking_connected_or_expose_side_title(window):
    w = window
    w.worker.status.emit('Connected - unrelated side title')
    assert w.companion.panel.connection.text() == 'Connected'
    w.worker.status.emit('Connection unavailable (ConnectionError); retrying…')
    assert 'unavailable' in w.companion.panel.connection.text()
    assert 'unavailable' in w.companion.notice.text()
    assert w.companion.connected is False
    w.worker.status.emit('Send not confirmed in another chat')
    assert 'unavailable' in w.companion.panel.connection.text()


def test_replayed_main_history_does_not_signal_new_reply_or_show_a_window(window):
    panel = window.companion.panel
    panel.rows['old'] = ('Muse', 'Already saved reply')
    updates = []
    panel.stateChanged.connect(lambda *args:updates.append(args))
    window.worker.conversation_message.emit('main', 'old', 'Muse', 'Already saved reply')
    assert updates == [] and not panel.isVisible()
    window.worker.conversation_message.emit('main', 'new', 'Muse', 'New reply')
    assert updates == [('Reply received', 'milestone')] and not panel.isVisible()
