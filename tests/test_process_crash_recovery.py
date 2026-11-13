"""Bounded process-exit rehearsal; never targets the live paired profile."""
import subprocess
import sys
from pathlib import Path
from musebridge.catalog import Catalog
from musebridge.execution import Execution
from musebridge.request_queue import RequestQueue
from musebridge.tooling import ToolRouter


CHILD=r'''
import os,sys
from pathlib import Path
from musebridge.catalog import Catalog
from musebridge.execution import Execution
from musebridge.request_queue import RequestQueue
state=Path(sys.argv[1]);root=Path(sys.argv[2]);crash=sys.argv[3]=='crash'
c=Catalog(state);p=c.create_project('Crash fixture',root);cid=c.create_conversation('Preserve me',p['id'])['id']
c.draft(cid,'Unsent draft');e=Execution(c);q=RequestQueue(c);key=q.add(cid,'Never replay automatically')
run=e.begin_run(cid);call=e.start_call(cid,'file.write','project_write',run);e.call_started(cid,call[0],run,'file.write')
if crash:
 q.claim(cid)
 with (root/'side-effect.txt').open('a',encoding='utf-8') as f:f.write('once');f.flush();os.fsync(f.fileno())
 os._exit(73)
e.finish_call(cid,call[0],run,'file.write','succeeded');e.state(cid,run,'remote_finished');c.close()
'''


def test_process_exit_after_effect_preserves_data_and_never_replays(tmp_path):
    for mode in ('baseline','crash'):
        boundary=tmp_path/mode;boundary.mkdir();state=boundary/'state';root=boundary/'project';root.mkdir()
        # Supervisor is pytest's process; child has no live account or network calls.
        result=subprocess.run([sys.executable,'-c',CHILD,str(state),str(root),mode],capture_output=True,timeout=15,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        assert result.returncode==(73 if mode=='crash' else 0),result.stderr.decode(errors='replace')[-500:]
        c=Catalog(state);e=Execution(c);q=RequestQueue(c)
        try:
            cid=c.conversations()[1]['id'] if c.conversations()[0]['id']=='main' else c.conversations()[0]['id']
            assert c.draft(cid)=='Unsent draft' and not q.enabled
            assert q.rows(cid)[0]['state']==('unknown' if mode=='crash' else 'pending')
            assert c.rows('SELECT state FROM runs')[0]['state']==('unknown' if mode=='crash' else 'remote_finished')
            assert c.db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            if mode=='crash':
                assert c.rows('SELECT state FROM tool_calls')[0]['state']=='unknown'
                assert (root/'side-effect.txt').read_text()=='once'
            router=ToolRouter(c,state)
            try:
                fresh=router.call('file.write',{'conversation_id':cid,'path':'fresh.txt','text':'usable after recovery','create':True})
                assert fresh['ok'] and (root/'fresh.txt').read_text()=='usable after recovery'
                assert q.claim(cid) is None
                if mode=='crash':assert (root/'side-effect.txt').read_text()=='once'
            finally:router.close()
        finally:c.close()
