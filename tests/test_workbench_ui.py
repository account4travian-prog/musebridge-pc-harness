import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication
from musebridge.app import Window,Worker
from musebridge.storage import Store
from musebridge.artifacts_ui import attach_artifact,refresh_artifacts
from musebridge.overview_ui import work_rows,refresh_overview,refresh_attention


def app():return QApplication.instance() or QApplication([])


def test_stop_resume_revokes_terminal_input_waiting_in_the_ui_executor(tmp_path):
    import threading
    worker=Worker(Store(tmp_path/'state'));project=worker.catalog.create_project('Fixture',tmp_path)
    cid=worker.catalog.create_conversation('Terminal',project['id'])['id']
    entered=threading.Event();release=threading.Event();effects=[]
    def blocker():entered.set();release.wait(5)
    blocked=worker.terminal_executor.submit(blocker);assert entered.wait(2)
    original=worker.router._dispatch
    def dispatched(name,*args,**kwargs):effects.append(name);return {'ok':True,'payload':{}}
    worker.router._dispatch=dispatched
    try:
        pending=worker.submit_terminal('terminal.start',{'conversation_id':cid,'argv':['fixture.exe'],'request_id':'queued'})
        worker.router.paused=True;worker.router.paused=False
        release.set();blocked.result(3)
        assert pending.result(3)['ok'] is False and effects==[]
    finally:release.set();worker.router._dispatch=original;worker.close()


def test_document_attachment_and_queue_refusal_preserve_the_draft(tmp_path):
    application=app();w=Window(Worker(Store(tmp_path/'state')))
    try:
        root=tmp_path/'project';root.mkdir();project=w.worker.catalog.create_project('Example',root)
        cid=w.worker.catalog.create_conversation('Documents',project['id'])['id'];w.refresh_conversations(cid);w.switch_conversation()
        source=tmp_path/'document.md';source.write_text('A selected document',encoding='utf-8')
        artifact=w.worker.router.artifacts.import_file(cid,source);refresh_artifacts(w);attach_artifact(w)
        assert [d['id'] for d in w.worker.router.artifacts.drafts(cid)]==[artifact['id']]
        assert 'document.md' in w.attachment_label.text()
        w.input.setPlainText('Use the selected document');w.enqueue_message()
        assert w.input.toPlainText()=='Use the selected document' and w.worker.queue.rows(cid)==[]
        assert w.worker.router.artifacts.drafts(cid)
        w.remove_attachment();assert not w.worker.router.artifacts.drafts(cid)
        # An unrelated/queued user echo cannot clear a newer composer draft.
        w.on_conversation_message(cid,'echo','You','Use the selected document')
        assert w.input.toPlainText()=='Use the selected document'
        w.confirm_manual_draft(cid,'Use the selected document');assert not w.input.toPlainText()
    finally:w.close()


def test_overview_preserves_background_job_visibility_and_attention_is_opt_in(tmp_path):
    application=app();w=Window(Worker(Store(tmp_path/'state')))
    try:
        root=tmp_path/'project';root.mkdir();project=w.worker.catalog.create_project('Example',root)
        cid=w.worker.catalog.create_conversation('Background',project['id'])['id']
        result=w.worker.router.call('job.start',{'conversation_id':cid,'request_id':'background','command':'Start-Sleep -Seconds 20'})
        assert result['ok']
        w.worker.queue.add(cid,'Explicit follow-up');w.worker.queue.hold(cid)
        row=next(r for r in work_rows(w.worker) if r['id']==cid)
        assert row['active_jobs']==1 and row['queued']==1 and row['queue_mode']=='Held'
        refresh_overview(w);assert w.overview_tree.topLevelItemCount()==3
        w.worker.catalog.event(cid,'run.remote_finished',{'run_id':'fixture'})
        refresh_attention(w);assert not w.attention_label.text()
        w.attention_enabled.setChecked(True);refresh_attention(w)
        assert 'Background' in w.attention_label.text() and '•' in w.tabs.tabText(8)
        assert w.current_id!='main' and w.current_id!=cid
        w.worker.stop_work()
    finally:w.close()
