"""Owner settings and Changes feedback, using offscreen Qt and disposable files."""
import os
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QPlainTextEdit, QPushButton, QWidget

from musebridge.changes_ui import build_changes_page, refresh_changes
from musebridge.finish_check_settings import show_finish_check_settings
from test_finish_line import harness, begin, write


def test_changes_warning_updates_after_successful_command(harness):
    app = QApplication.instance() or QApplication([])
    _, _, router, cid, _ = harness
    begin(router, cid)
    write(router, cid)
    window = SimpleNamespace(current_id=cid, worker=SimpleNamespace(router=router))
    page = build_changes_page(window)
    try:
        refresh_changes(window)
        assert 'not checked since last edit' in window.changes_tree.topLevelItem(0).text(1)
        assert 'not checked since last edit' in window.change_notice.text()
        assert router.call('shell.run', {'conversation_id': cid, 'command': 'exit 0'})['ok']
        refresh_changes(window)
        assert 'not checked' not in window.changes_tree.topLevelItem(0).text(1)
        assert 'not checked' not in window.change_notice.text()
    finally:
        page.close()


def test_owner_settings_save_is_disabled_by_default_and_does_not_resume(harness, monkeypatch):
    app = QApplication.instance() or QApplication([])
    _, catalog, router, cid, queue = harness
    router.finish_line.configure(cid, '', False)
    queue.hold(cid)
    window = QWidget()
    window.current_id = cid
    window.worker = SimpleNamespace(router=router, catalog=catalog)
    window.handle_activity = lambda *_: None
    def dialog_exec(dialog):
        enabled = dialog.findChild(QCheckBox)
        assert enabled.text() == 'Auto-check after changes' and not enabled.isChecked()
        command = dialog.findChild(QPlainTextEdit)
        command.setPlainText('exit 0')
        enabled.setChecked(True)
        next(button for button in dialog.findChildren(QPushButton) if button.text() == 'Save').click()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, 'exec', dialog_exec)
    try:
        show_finish_check_settings(window)
        assert router.finish_line.configuration(cid) == {'enabled': True, 'check_command': 'exit 0'}
        assert cid in queue.held and not queue.rows(cid)
        assert catalog.rows('SELECT * FROM job_links') == []
    finally:
        window.close()
