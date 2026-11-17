import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtWidgets import QApplication
from musebridge.app import Window,Worker
from musebridge.storage import Store


def test_native_window_remains_responsive_and_renders_text_safely(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = Window(Worker(Store(tmp_path)))
    window.show()
    app.processEvents()
    window.show_message('one', 'Muse', '<script>test</script>')
    assert '<script>test</script>' in window.chat.toPlainText()
    window.show_message('one', 'Muse', 'Updated result')
    assert window.chat.toPlainText().count('Updated result') == 1
    assert '<script>' not in window.chat.toPlainText()
    window.close()
    assert not window.worker.thread.is_alive()


def test_project_conversations_restore_distinct_drafts_and_history(tmp_path):
    app=QApplication.instance() or QApplication([])
    window=Window(Worker(Store(tmp_path)))
    project=window.worker.catalog.create_project('Test',tmp_path)
    conversation=window.worker.catalog.create_conversation('Separate chat',project['id'])
    window.companion.panel.input.setPlainText('main draft')
    window.refresh_conversations(conversation['id']);window.switch_conversation()
    window.input.setPlainText('project draft')
    window.worker.catalog.message(conversation['id'],'stored','Muse','Persistent reply')
    window.close()
    second=Window(Worker(Store(tmp_path)))
    assert second.current_id==conversation['id']
    assert second.input.toPlainText()=='project draft'
    second.refresh_conversations('main');second.switch_conversation()
    assert second.current_id==conversation['id']
    assert second.companion.panel.input.toPlainText()=='main draft'
    second.refresh_conversations(conversation['id']);second.switch_conversation()
    assert second.input.toPlainText()=='project draft'
    assert 'Persistent reply' in second.chat.toPlainText()
    assert 'Test' in second.project_label.text()
    second.close()
