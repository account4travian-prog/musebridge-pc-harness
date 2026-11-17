import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from musebridge.app import Window,Worker
from musebridge.storage import Store
from musebridge.presentation import Composer


def app():return QApplication.instance() or QApplication([])


def test_composer_enter_sends_shift_enter_adds_line():
    application=app();editor=Composer();sent=[]
    editor.sendRequested.connect(lambda:sent.append(editor.toPlainText()))
    editor.setPlainText('hello');editor.moveCursor(editor.textCursor().MoveOperation.End)
    QTest.keyClick(editor,Qt.Key.Key_Return)
    assert sent==['hello'] and editor.toPlainText()=='hello'
    QTest.keyClick(editor,Qt.Key.Key_Return,Qt.KeyboardModifier.ShiftModifier)
    assert editor.toPlainText()=='hello\n'
    QTest.keyClick(editor,Qt.Key.Key_Return,Qt.KeyboardModifier.ControlModifier)
    assert sent==['hello','hello\n']
    editor.close()


def test_new_chat_keeps_workspace_draft_plan_and_can_archive(tmp_path):
    application=app();w=Window(Worker(Store(tmp_path)))
    try:
        project=w.worker.catalog.create_project('Example',tmp_path)
        c=w.worker.catalog.create_conversation('Original',project['id'])
        w.refresh_conversations(c['id']);w.switch_conversation()
        w.input.setPlainText('unfinished message')
        w.goal_editor.setText('A useful goal');w.plan_editor.setPlainText('Step one')
        w.new_conversation();new_id=w.current_id
        assert new_id!=c['id']
        assert w.worker.catalog.conversation(new_id)['project_id']==project['id']
        assert w.worker.catalog.draft(c['id'])=='unfinished message'
        assert w.worker.catalog.context(c['id'])['plan']=='Step one'
        w.search.setText('Original')
        assert not w.nav_items[c['id']].isHidden()
        assert w.nav_items[new_id].isHidden()
        w.worker.queue.add(new_id,'Do not send after archive')
        assert new_id in w.worker.queue.enabled
        w.search.clear();w.archive_conversation()
        assert w.current_id!='main' and w.current_id!=new_id
        assert new_id not in w.nav_items
        assert w.worker.catalog.conversation(new_id)['archived']==1
        assert new_id in w.worker.queue.held and new_id not in w.worker.queue.enabled
        w.worker.catalog.archive_conversation(new_id,False);w.refresh_conversations()
        assert new_id in w.nav_items
        assert new_id in w.worker.queue.held
    finally:w.close()


def test_stream_updates_preserve_reading_position(tmp_path):
    application=app();w=Window(Worker(Store(tmp_path)))
    try:
        w.show();application.processEvents()
        long_text='\n\n'.join('Paragraph '+str(i)+' with enough text to create a long conversation.' for i in range(150))
        w.show_message('answer','Muse',long_text);application.processEvents()
        bar=w.chat.verticalScrollBar();assert bar.maximum()>100
        position=bar.maximum()//3;bar.setValue(position)
        w.show_message('answer','Muse',long_text+'\n\nA new streamed paragraph.')
        application.processEvents()
        assert abs(bar.value()-position)<4
    finally:w.close()


def test_invalid_plan_keeps_editor_open_and_does_not_continue(tmp_path):
    application=app();w=Window(Worker(Store(tmp_path)))
    try:
        original_id=w.current_id
        w.worker.catalog.plan(original_id,text='Saved plan',goal='Saved goal')
        w.load_plan();w.plan_editor.setPlainText('x'*16001)
        calls=[];w.worker.run_goal=lambda:calls.append('run')
        w.save_and_continue_goal()
        assert not calls and w.plan_dirty
        assert w.worker.catalog.context(original_id)['plan']=='Saved plan'
        w.new_conversation()
        assert w.current_id==original_id and len(w.plan_editor.toPlainText())==16001
        assert not w.close()
        w.plan_editor.setPlainText('Corrected plan')
        assert w.close()
        assert not w.activity_timer.isActive() and not w.draft_timer.isActive()
    finally:
        if w.plan_dirty:w.plan_editor.setPlainText('Corrected plan')
        w.close()


def test_changes_view_restores_through_policy_and_refuses_owner_conflict(tmp_path):
    application=app();w=Window(Worker(Store(tmp_path/'state')))
    root=tmp_path/'project';root.mkdir();(root/'example.txt').write_text('before')
    try:
        project=w.worker.catalog.create_project('Example',root)
        c=w.worker.catalog.create_conversation('Tracked changes',project['id'])
        w.refresh_conversations(c['id']);w.switch_conversation()
        read=w.worker.router.call('file.read',{'conversation_id':c['id'],'path':'example.txt'})
        assert read['ok'] and 'sha256' not in read['payload']
        written=w.worker.router.call('file.edit',{'conversation_id':c['id'],'path':'example.txt','old':'before','new':'after'})
        assert written['ok']
        w.tabs.setCurrentIndex(3)
        assert w.changes_tree.topLevelItemCount()==1 and '+after' in w.change_diff.toPlainText()
        w.profile.setCurrentIndex(w.profile.findData('read_only'))
        w.refresh_activity();assert not w.restore_button.isEnabled()
        w.profile.setCurrentIndex(w.profile.findData('project'));w.refresh_activity()
        (root/'example.txt').write_text('owner edit')
        w.restore_button.click()
        assert (root/'example.txt').read_text()=='owner edit'
        assert 'conflict' in w.change_notice.text()
        (root/'example.txt').write_text('after')
        w.restore_button.click()
        assert (root/'example.txt').read_text()=='before'
        assert not w.restore_button.isEnabled()
    finally:w.close()
