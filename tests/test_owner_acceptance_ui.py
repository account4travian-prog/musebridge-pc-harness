import json
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path

from PySide6.QtWidgets import QApplication
from musebridge.app import Worker,Window
from musebridge.storage import Store
from musebridge.acceptance_ui import prepare_checks,prompts,check_results,OwnerChecks


def test_prepare_is_local_idempotent_and_keeps_results_absent_until_real_checks(tmp_path):
    worker=Worker(Store(tmp_path/'state'))
    try:
        setup=prepare_checks(worker,tmp_path/'checks');root=Path(setup['root'])
        assert (root/setup['input_name']).read_bytes()==b''
        assert (root/'START-HERE.md').is_file()
        assert not any(check_results(setup).values())
        assert worker.router.execution.policy(setup['conversation_id'])['profile']=='desktop'
        assert worker.router.execution.policy('main')['profile']=='project'
        assert worker.catalog.draft(setup['conversation_id'])==prompts(setup)['desktop']
        assert worker.core is None and worker.voice is None
        assert worker.catalog.rows('SELECT * FROM outbox')==[]
        assert prepare_checks(worker,tmp_path/'checks')==setup
        assert len(worker.catalog.projects())==1
    finally:worker.close()


def test_result_checker_requires_fresh_exact_marker_and_does_not_certify_speech(tmp_path):
    worker=Worker(Store(tmp_path/'state'))
    try:
        setup=prepare_checks(worker,tmp_path/'checks');root=Path(setup['root'])
        (root/'notepad-acceptance.txt').write_text('MuseBridge desktop acceptance wrong.',encoding='utf-8')
        assert not check_results(setup)['notepad-acceptance.txt']
        (root/'notepad-acceptance.txt').write_text('MuseBridge desktop acceptance '+setup['marker']+'.\n',encoding='utf-8-sig')
        assert check_results(setup)['notepad-acceptance.txt']
        assert not check_results(setup)['voice-acceptance.txt']
    finally:worker.close()


def test_owner_setup_launches_only_prepared_input_and_grants_only_test_notepad(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([])
    w=Window(Worker(Store(tmp_path/'state')))
    try:
        setup=prepare_checks(w.worker,tmp_path/'checks')
        dialog=w.owner_checks=OwnerChecks(w)
        launches=[]
        monkeypatch.setattr('musebridge.acceptance_ui.QProcess.startDetached',lambda *args:launches.append(args) or (True,123))
        monkeypatch.setattr('musebridge.acceptance_ui.is_notepad',lambda pid:pid==123)
        monkeypatch.setattr('musebridge.acceptance_ui.place_test_notepad',lambda *args:True)
        monkeypatch.setattr('musebridge.project_ui.background',lambda dialog,fn,done,error:done(fn()))
        original=w.worker.router.call
        def call(name,params,*args,**kw):
            if name=='desktop.windows':return {'ok':True,'payload':{'windows':[
                {'window_handle':456,'process_id':123,'title':setup['input_name']+' - Notepad'},
                {'window_handle':999,'process_id':999,'title':'Important unrelated document'}]}}
            return original(name,params,*args,**kw)
        monkeypatch.setattr(w.worker.router,'call',call)
        monkeypatch.setattr(w.worker,'send',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('No automatic send allowed')))
        dialog.prepare();dialog.list_windows()
        assert len(launches)==1 and launches[0][1]==[str(Path(setup['root'])/setup['input_name'])]
        assert w.current_id==setup['conversation_id']
        grant=w.worker.router.vision.status(setup['conversation_id'])
        assert grant['enabled'] and grant['window']['window_handle']==456 and not grant['browser']
        assert not w.worker.router.vision.status('main')['enabled']
        assert w.worker.voice is None and not dialog.poll_timer.isActive()
        assert all(not box.isChecked() for box in dialog.observations.values())
        dialog.record()
        receipt=json.loads((Path(setup['root'])/'owner-observations.json').read_text())
        assert not any(receipt['file_checks'].values()) and not any(receipt['owner_observations'].values())
    finally:w.close();app.processEvents()
