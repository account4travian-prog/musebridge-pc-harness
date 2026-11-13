from pathlib import Path
import sys
import time
import pytest
import win32api,win32con,win32event
from musebridge.catalog import Catalog
from musebridge.tooling import ToolRouter
from musebridge.terminals import TerminalManager


def wait_for(manager,cid,terminal,predicate,seconds=15):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        result=manager.status(cid,terminal)
        if predicate(result):return result
        time.sleep(.03)
    raise AssertionError('Terminal fixture did not reach expected state: '+str(result))


def test_real_conpty_input_dedup_resize_and_job_tree_cancellation(tmp_path):
    root=tmp_path/'project';root.mkdir();catalog=Catalog(tmp_path/'state');project=catalog.create_project('Terminal',root)
    cid=catalog.create_conversation('Terminal',project['id'])['id'];router=ToolRouter(catalog,tmp_path/'state');manager=TerminalManager(catalog,router.jobs,tmp_path/'state')
    fixture=root/'fixture.py';fixture.write_text("import sys,os,time,subprocess\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'])\nPath('child.pid').write_text(str(p.pid))\nprint('PROMPT',flush=True)\nvalue=input()\nprint('ANSWER:'+value,flush=True)\ntime.sleep(120)\n",encoding='utf-8')
    child_handle=None
    try:
        terminal=manager.start(cid,[sys.executable,'-u',str(fixture)],'.','interactive')['terminal_id']
        wait_for(manager,cid,terminal,lambda r:'PROMPT' in r['output'])
        manager.write(cid,terminal,'hello Ω\r','one-input');duplicate=manager.write(cid,terminal,'hello Ω\r','one-input')
        assert duplicate['duplicate']
        with pytest.raises(ValueError):manager.write(cid,terminal,'different\r','one-input')
        result=wait_for(manager,cid,terminal,lambda r:'ANSWER:hello Ω' in r['output'])
        assert result['output'].count('ANSWER:hello Ω')==1
        manager.resize(cid,terminal,35,110)
        pid=int((root/'child.pid').read_text());child_handle=win32api.OpenProcess(win32con.SYNCHRONIZE,False,pid)
        other=catalog.create_conversation('Other')['id']
        with pytest.raises(ValueError):manager.status(other,terminal)
        manager.stop(cid,terminal)
        assert win32event.WaitForSingleObject(child_handle,5000)==win32event.WAIT_OBJECT_0
        assert manager.status(cid,terminal)['state']=='cancelled'
    finally:
        if child_handle:win32api.CloseHandle(child_handle)
        manager.close();router.close();catalog.close()


def test_terminal_normal_exit_and_restart_never_relaunches_process(tmp_path):
    root=tmp_path/'project';root.mkdir();catalog=Catalog(tmp_path/'state');project=catalog.create_project('Terminal',root)
    cid=catalog.create_conversation('Terminal',project['id'])['id'];router=ToolRouter(catalog,tmp_path/'state');manager=TerminalManager(catalog,router.jobs,tmp_path/'state')
    try:
        terminal=manager.start(cid,[sys.executable,'-u','-c',"print('TERMINAL_DONE')"],'.','once')['terminal_id']
        result=wait_for(manager,cid,terminal,lambda r:r['state'] not in ('accepted','running'))
        assert result['state']=='succeeded' and result['exit_code']==0
        assert 'TERMINAL_DONE' in result['output']
        again=manager.start(cid,[sys.executable,'-u','-c',"print('TERMINAL_DONE')"],'.','once')
        assert again['terminal_id']==terminal
        with pytest.raises(ValueError):manager.write(cid,terminal,'late\r','late')
    finally:manager.close();router.close();catalog.close()


def test_real_conpty_keeps_vt_for_local_screen_and_accepts_arrow_sequences(tmp_path):
    root=tmp_path/'project';root.mkdir();catalog=Catalog(tmp_path/'state');project=catalog.create_project('Terminal',root)
    cid=catalog.create_conversation('Terminal',project['id'])['id'];router=ToolRouter(catalog,tmp_path/'state');manager=TerminalManager(catalog,router.jobs,tmp_path/'state')
    fixture=root/'vt_fixture.py'
    fixture.write_text("import sys,ctypes\nk=ctypes.windll.kernel32\nh=k.GetStdHandle(-10)\nm=ctypes.c_uint()\nk.GetConsoleMode(h,ctypes.byref(m))\nk.SetConsoleMode(h,(m.value & ~6)|0x200)\nsys.stdout.write('\\x1b[2J\\x1b[HREADY Ω\\x1b[2;4HRED\\x1b[31m!\\x1b[0m\\n');sys.stdout.flush()\nvalue=sys.stdin.read(3)\nprint('KEY:'+repr(value),flush=True)\n",encoding='utf-8')
    try:
        terminal=manager.start(cid,[sys.executable,'-u',str(fixture)],'.','vt')['terminal_id']
        wait_for(manager,cid,terminal,lambda r:'READY' in r['output'])
        local=manager.status(cid,terminal,include_raw=True)
        remote=manager.status(cid,terminal)
        assert '\x1b[2J' in local['raw_output'] and '\x1b[31m' in local['raw_output']
        assert '\x1b[' not in remote['output']
        manager.write(cid,terminal,'\x1b[A','arrow')
        result=wait_for(manager,cid,terminal,lambda r:"KEY:'\\x1b[A'" in r['output'])
        assert "KEY:'\\x1b[A'" in result['output']
    finally:manager.stop_all();manager.close();router.close();catalog.close()
