"""Disposable restore rehearsals; project bytes/index and later owner work are oracles."""
import os
import subprocess
import time

import pytest

from test_finish_line import harness, begin, finish


def git(root,*args):
    return subprocess.run(['git',*args],cwd=root,check=True,capture_output=True,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


def test_shell_change_review_and_byte_restore_preserve_real_git(harness):
    root,_,router,cid,_=harness
    git(root,'init')
    git(root,'config','user.name','Fixture')
    git(root,'config','user.email','fixture@invalid')
    (root/'data.bin').write_bytes(b'original\x00\r\n')
    (root/'.gitattributes').write_text('* text\n')
    git(root,'add','.')
    git(root,'commit','-m','fixture')
    original_index=(root/'.git/index').read_bytes()
    original_head=(root/'.git/HEAD').read_bytes()
    run=begin(router,cid)
    assert router.call('shell.run',{'conversation_id':cid,'command':
        '[IO.File]::WriteAllBytes("data.bin", [byte[]](99,104,97,110,103,101,100,0,13,10))'})['ok']
    finish(router,cid,run)
    review=router.call('change.review',{'conversation_id':cid,'run':run})
    assert review['ok'],review
    assert review['payload']['edits'],review
    assert review['payload']['edits'][0]['path']=='data.bin'
    assert review['payload']['edits'][0]['source']=='shadow_git'
    restored=router.call('change.restore',{'conversation_id':cid,'path':'data.bin'})
    assert restored['ok'],restored
    assert (root/'data.bin').read_bytes()==b'original\x00\r\n'
    assert (root/'.git/index').read_bytes()==original_index
    assert (root/'.git/HEAD').read_bytes()==original_head


def test_restore_conflict_preflights_every_file_before_any_mutation(harness):
    root,_,router,cid,_=harness
    (root/'a.txt').write_text('original a')
    (root/'b.txt').write_text('original b')
    run=begin(router,cid)
    (root/'a.txt').write_text('run a')
    (root/'b.txt').write_text('run b')
    finish(router,cid,run)
    (root/'b.txt').write_text('owner work')
    result=router.call('change.restore',{'conversation_id':cid,'run':run})
    assert not result['ok']
    assert (root/'a.txt').read_text()=='run a'
    assert (root/'b.txt').read_text()=='owner work'


def test_new_deleted_and_oversized_owner_file_are_not_confused(harness):
    root,_,router,cid,_=harness
    (root/'deleted.txt').write_bytes(b'before\r\n')
    run=begin(router,cid)
    (root/'deleted.txt').unlink()
    (root/'new.txt').write_bytes(b'new\r\n')
    finish(router,cid,run)
    (root/'deleted.txt').write_bytes(b'x'*(8*1024*1024+1))
    result=router.call('change.restore',{'conversation_id':cid,'run':run})
    assert not result['ok'] and (root/'new.txt').exists()
    assert (root/'deleted.txt').stat().st_size==8*1024*1024+1
    (root/'deleted.txt').unlink()
    assert router.call('change.restore',{'conversation_id':cid,'run':run})['ok']
    assert not (root/'new.txt').exists()
    assert (root/'deleted.txt').read_bytes()==b'before\r\n'


def test_missing_git_ignore_and_reparse_fallback(harness):
    root,_,router,cid,_=harness
    (root/'.gitignore').write_text('ignored/\n')
    (root/'ignored').mkdir()
    (root/'ignored/private.txt').write_text('excluded')
    (root/'node_modules').mkdir()
    (root/'node_modules/package').write_text('excluded')
    run=begin(router,cid)
    (root/'ordinary').write_text('visible')
    finish(router,cid,run)
    assert [x['path'] for x in router.shadow.review(cid,run)['edits']]==['ordinary']
    router.shadow.git=None
    run=begin(router,cid)
    note=router.shadow.review(cid,run)['note']
    assert 'Git unavailable' in note


def test_snapshot_import_failure_keeps_previous_tree_restorable(harness,monkeypatch):
    root,catalog,router,cid,_=harness
    (root/'file').write_bytes(b'original')
    run=begin(router,cid)
    (root/'file').write_bytes(b'run')
    finish(router,cid,run)
    def fail(*_):raise OSError('fixture interrupted snapshot')
    original=router.shadow.snapshot
    monkeypatch.setattr(router.shadow,'snapshot',fail)
    interrupted=begin(router,cid)
    assert router.shadow.review(cid,interrupted)['edits']==[]
    monkeypatch.setattr(router.shadow,'snapshot',original)
    assert router.call('change.restore',{'conversation_id':cid,'run':run})['ok']
    assert (root/'file').read_bytes()==b'original'


def test_twenty_thousand_file_warm_snapshot_under_five_seconds(harness):
    root,catalog,router,cid,_=harness
    for group in range(100):
        folder=root/str(group)
        folder.mkdir()
        for index in range(200):(folder/str(index)).write_bytes(b'fixture bytes\r\n')
    project=catalog.conversation(cid)['project_id']
    first=router.shadow.snapshot(project,root)
    started=time.monotonic()
    second=router.shadow.snapshot(project,root)
    elapsed=time.monotonic()-started
    assert first==second
    assert elapsed<5,f'Warm snapshot took {elapsed:.3f}s'
