"""Verify feedback follows committed edits without holding mutation gates."""
import threading
import time

import pytest

from musebridge.catalog import Catalog
from musebridge.tooling import ToolRouter


@pytest.fixture
def harness(tmp_path):
    root=tmp_path/'project';root.mkdir()
    catalog=Catalog(tmp_path/'state')
    project=catalog.create_project('Edit feedback',root)
    cid=catalog.create_conversation('Check',project['id'])['id']
    router=ToolRouter(catalog,tmp_path/'state')
    try:yield root,catalog,router,cid
    finally:router.close();catalog.close()


def call(router,cid,name,**params):
    return router.call(name,{'conversation_id':cid,**params})


def test_broken_python_is_saved_reported_then_repaired(harness):
    root,catalog,router,cid=harness
    result=call(router,cid,'file.write',path='sample.py',text='if True\n    pass\n')
    assert result['ok'] and (root/'sample.py').read_text()=='if True\n    pass\n'
    assert 'does not parse' in result['payload']['diagnostic_warning']
    assert any(d.get('source')=='python' and d.get('line')==1 for d in result['payload']['diagnostics'])
    assert call(router,cid,'file.read',path='sample.py')['ok']
    fixed=call(router,cid,'file.edit',path='sample.py',old='if True',new='if True:')
    assert fixed['ok'] and not fixed['payload'].get('diagnostic_warning')
    assert not fixed['payload'].get('diagnostics')
    assert {row['state'] for row in catalog.rows("SELECT state FROM tool_calls WHERE tool IN ('file.write','file.edit')")}=={'succeeded'}


def test_pause_can_acquire_gate_while_feedback_waits(harness,monkeypatch):
    root,_catalog,router,cid=harness
    entered=threading.Event();release=threading.Event();paused=threading.Event();results=[]
    def feedback(*_args):
        entered.set()
        assert release.wait(3)
        return {'state':'interrupted','passed':False}
    monkeypatch.setattr(router.edit_checks,'after_edit',feedback)
    thread=threading.Thread(target=lambda:results.append(call(router,cid,'file.write',path='done.txt',text='published')))
    thread.start()
    try:
        assert entered.wait(3) and (root/'done.txt').read_text()=='published'
        pause_thread=threading.Thread(target=lambda:(router.execution.pause(True),paused.set()))
        pause_thread.start()
        assert paused.wait(.5),'Post-edit feedback held the mutation gate and blocked Stop'
        pause_thread.join(1)
    finally:release.set();thread.join(4)
    assert results[0]['ok']


def test_four_mib_file_write_feedback_is_bounded(harness):
    root,_catalog,router,cid=harness
    content='#'+('x'*(4*1024*1024-2))+'\n'
    started=time.monotonic()
    result=call(router,cid,'file.write',path='large.py',text=content)
    # Publication and durable checkpoint I/O have separate budgets. The syntax
    # helper's dedicated test checks its 500ms cap on this size.
    assert result['ok'] and (root/'large.py').stat().st_size==4*1024*1024
    assert not result['payload'].get('diagnostic_warning')
    assert time.monotonic()-started<5


def test_stop_between_dispatch_and_publication_is_definitive_refusal(harness,monkeypatch):
    root,catalog,router,cid=harness
    original=router.files.write
    def stop_before_publication(*args,**kwargs):
        router.execution.pause(True)
        return original(*args,**kwargs)
    monkeypatch.setattr(router.files,'write',stop_before_publication)
    result=call(router,cid,'file.write',path='must-not-exist.txt',text='denied')
    assert not result['ok'] and result['error']['outcome']=='failed'
    assert not (root/'must-not-exist.txt').exists()
    assert catalog.rows("SELECT state FROM tool_calls WHERE tool='file.write'")==[{'state':'failed'}]


def test_stop_then_resume_during_parsing_does_not_admit_late_hook(harness,monkeypatch):
    import musebridge.edit_diagnostics
    root,_catalog,router,cid=harness
    router.edit_checks.configure(cid,"[IO.File]::WriteAllText('late.txt','must not run')",'*.py',True)
    def stop_and_resume(*_args):
        router.execution.pause(True);router.execution.pause(False)
        return []
    monkeypatch.setattr(musebridge.edit_diagnostics,'diagnose',stop_and_resume)
    result=call(router,cid,'file.write',path='saved.py',text='x=1\n')
    assert result['ok'] and (root/'saved.py').exists()
    assert 'saved' in result['payload']['feedback_warning']
    assert not (root/'late.txt').exists()
    assert router.jobs._db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0


def test_failed_owner_check_is_visible_once_and_preserves_the_edit(harness):
    root,catalog,router,cid=harness
    router.edit_checks.configure(cid,'1..25 | ForEach-Object { "line $_" }; exit 3','**/*.py',True)
    result=call(router,cid,'file.write',path='code.py',text='value=1\n')
    assert result['ok'] and (root/'code.py').read_text()=='value=1\n'
    check=result['payload']['check_on_edit']
    assert check['passed'] is False and check['exit_code']==3
    assert check['output_tail'].splitlines()==['line '+str(n) for n in range(6,26)]
    assert 'after_edit_hooks' not in result['payload']
    assert catalog.rows("SELECT state FROM tool_calls WHERE tool='file.write'")==[{'state':'succeeded'}]
    assert call(router,cid,'file.write',path='unmatched.txt',text='outside filter')['ok']
    assert router.jobs._db.execute('SELECT count(*) FROM jobs').fetchone()[0]==1


def test_absolute_in_scope_path_uses_project_relative_check_filter(harness):
    root,_catalog,router,cid=harness
    router.edit_checks.configure(cid,'exit 0','src/*.py',True)
    result=call(router,cid,'file.write',path=str(root/'src'/'sample.py'),text='value=1\n')
    assert result['ok'] and result['payload']['path']=='src/sample.py'
    assert result['payload']['check_on_edit']['passed'] is True
    assert call(router,cid,'file.read',path=str(root/'src'/'sample.py'))['ok']
    changed=call(router,cid,'file.edit',path=str(root/'src'/'sample.py'),old='value=1',new='value=2')
    assert changed['ok'] and changed['payload']['check_on_edit']['passed'] is True
    assert not _catalog.rows("SELECT state FROM tool_calls WHERE state='unknown'")
