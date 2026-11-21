"""Owned ConPTY sessions supervised by the existing Windows JobManager."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import sys
import threading
import time
import uuid


def plain_output(text):
    text=re.sub(r'\x1b\][^\x07]*(?:\x07|\x1b\\)','',text)
    text=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',text)
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]','',text)


class TerminalManager:
    def __init__(self,catalog,jobs,state_dir):
        self.catalog=catalog;self.jobs=jobs;self.lock=threading.RLock()
        path=Path(state_dir)/'terminals';path.mkdir(parents=True,exist_ok=True);self.path=path/'terminal.sqlite'
        self.db=sqlite3.connect(self.path,check_same_thread=False);self.db.row_factory=sqlite3.Row
        with self.db:
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('''CREATE TABLE IF NOT EXISTS sessions(
                id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,request_key TEXT NOT NULL UNIQUE,args_hash TEXT NOT NULL,
                argv_json TEXT NOT NULL,cwd TEXT NOT NULL,rows INTEGER NOT NULL,cols INTEGER NOT NULL,
                state TEXT NOT NULL,job_id TEXT,created REAL NOT NULL)''')
            self.db.execute('''CREATE TABLE IF NOT EXISTS inputs(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,request_key TEXT NOT NULL UNIQUE,
                args_hash TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL)''')
            self.db.execute("UPDATE sessions SET state='unknown' WHERE state IN ('accepted','running')")
            self.db.execute("UPDATE inputs SET state='unknown' WHERE state='sending'")

    def _session(self,cid,identifier):
        self.catalog.conversation(cid)
        with self.lock:
            row=self.db.execute('SELECT * FROM sessions WHERE id=? AND conversation_id=?',(identifier,cid)).fetchone()
            if not row:raise ValueError('Terminal belongs to another or unknown conversation')
            return dict(row)

    @staticmethod
    def _size(rows,cols):
        if type(rows) is not int or type(cols) is not int or not 5<=rows<=200 or not 20<=cols<=300:raise ValueError('Terminal size must be 5..200 rows and 20..300 columns')

    def start(self,cid,argv,cwd,request_id,rows=24,cols=80):
        self._size(rows,cols)
        if not isinstance(argv,list) or not argv or len(argv)>50 or any(not isinstance(a,str) or '\0' in a for a in argv) or len(json.dumps(argv))>16000:raise ValueError('Terminal argv must be a bounded string list')
        if not Path(argv[0]).is_absolute() or not Path(argv[0]).is_file():raise ValueError('Terminal executable must be an absolute existing path')
        if not isinstance(request_id,str) or not request_id or len(request_id)>200:raise ValueError('Invalid terminal request ID')
        c=self.catalog.conversation(cid)
        if not c['project_id']:raise ValueError('Choose a project for an interactive terminal')
        from .workspace import Workspace
        project=self.catalog.project(c['project_id']);workspace=Workspace(project['root'])
        if Path(project['root']).resolve(strict=True)!=Path(project['root']):raise ValueError('Terminal project root identity changed')
        selected=workspace.resolve(cwd)
        if not selected.is_dir():raise ValueError('Terminal working directory must be inside the selected project')
        key=cid+':'+request_id;digest=hashlib.sha256(json.dumps([argv,str(selected),rows,cols]).encode()).hexdigest()
        with self.lock,self.db:
            previous=self.db.execute('SELECT * FROM sessions WHERE request_key=?',(key,)).fetchone()
            if previous:
                if previous['args_hash']!=digest:raise ValueError('Terminal request ID was reused with different arguments')
                return self.status(cid,previous['id'])
            if self.db.execute("SELECT count(*) FROM sessions WHERE state IN ('accepted','running')").fetchone()[0]>=4:raise ValueError('At most four interactive terminals can be active')
            identifier=uuid.uuid4().hex
            self.db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (identifier,cid,key,digest,json.dumps(argv),str(selected),rows,cols,'accepted',None,time.time()))
        try:
            helper=[sys.executable,'-m','musebridge.terminal_host','--state',str(self.path),'--session',identifier]
            if os.name == 'nt':
                # JobManager's public execution contract is PowerShell. Every
                # argument is a literal single-quoted value, never interpolated code.
                script='& '+' '.join("'"+arg.replace("'","''")+"'" for arg in helper)+'; exit $LASTEXITCODE'
            else:
                # On Linux the job runs under bash: quote each argument for
                # POSIX sh. The host itself is still ConPTY-only (winpty), so
                # it fails there with a clear import error until a POSIX PTY
                # host lands; the launcher no longer masks it as a bash error.
                script=shlex.join(helper)
            job=self.jobs.start('terminal:'+key,script,str(selected),timeout_s=3600)
            with self.catalog.lock,self.catalog.db:self.catalog.db.execute('INSERT OR IGNORE INTO job_links VALUES(?,?)',(job['job_id'],cid))
            with self.lock,self.db:self.db.execute("UPDATE sessions SET job_id=? WHERE id=?",(job['job_id'],identifier))
        except BaseException:
            with self.lock,self.db:self.db.execute("UPDATE sessions SET state='unknown' WHERE id=?",(identifier,))
            raise
        return self.status(cid,identifier)

    def list(self,cid):
        self.catalog.conversation(cid)
        with self.lock:ids=[r[0] for r in self.db.execute('SELECT id FROM sessions WHERE conversation_id=? ORDER BY created DESC LIMIT 100',(cid,))]
        return [self.status(cid,identifier) for identifier in ids]

    def status(self,cid,identifier,cursor=0,include_raw=False):
        session=self._session(cid,identifier);job=self.jobs.status(session['job_id'],cursor) if session['job_id'] else None
        if job and job['state'] not in ('accepted','running'):
            state=job['state']
            with self.lock,self.db:
                self.db.execute('UPDATE sessions SET state=? WHERE id=?',(state,identifier))
                self.db.execute("UPDATE inputs SET state='unknown' WHERE session_id=? AND state IN ('pending','sending')",(identifier,))
        else:state=session['state']
        with self.lock:
            inputs=[dict(r) for r in self.db.execute('SELECT seq,request_key,kind,state FROM inputs WHERE session_id=? ORDER BY seq DESC LIMIT 30',(identifier,))]
        result={'terminal_id':identifier,'conversation_id':cid,'job_id':session['job_id'],'state':state,
            'program':Path(json.loads(session['argv_json'])[0]).name,'rows':session['rows'],'cols':session['cols'],
            'output':plain_output(job['output']) if job else '', 'cursor':job['cursor'] if job else 0,
            'output_end':job['output_end'] if job else 0,'truncated':job['truncated'] if job else False,
            'exit_code':job['exit_code'] if job else None,'inputs':inputs}
        # Raw VT sequences are available only to the in-process renderer. Tool
        # and remote callers retain the established sanitized text response.
        if include_raw:result['raw_output']=job['output'] if job else ''
        return result

    def _input(self,cid,identifier,kind,payload,request_id):
        session=self._session(cid,identifier)
        if self.status(cid,identifier)['state'] not in ('accepted','running'):raise ValueError('Terminal is no longer active')
        if not isinstance(request_id,str) or not request_id or len(request_id)>200:raise ValueError('Invalid terminal input request ID')
        key=identifier+':'+request_id;encoded=json.dumps(payload);digest=hashlib.sha256((kind+encoded).encode()).hexdigest()
        with self.lock,self.db:
            old=self.db.execute('SELECT seq,args_hash,state FROM inputs WHERE request_key=?',(key,)).fetchone()
            if old:
                if old['args_hash']!=digest:raise ValueError('Input request ID was reused with different input')
                return {'seq':old['seq'],'state':old['state'],'duplicate':True}
            if self.db.execute("SELECT count(*) FROM inputs WHERE session_id=? AND state='pending'",(identifier,)).fetchone()[0]>=100:raise ValueError('Terminal input queue is full')
            cursor=self.db.execute("INSERT INTO inputs(session_id,request_key,args_hash,kind,payload,state,created) VALUES(?,?,?,?,?,'pending',?)",(identifier,key,digest,kind,encoded,time.time()))
            return {'seq':cursor.lastrowid,'state':'pending','duplicate':False}

    def write(self,cid,identifier,text,request_id):
        if not isinstance(text,str) or not text or len(text.encode())>10000 or '\0' in text:raise ValueError('Terminal input must be 1..10000 bytes without NUL')
        return self._input(cid,identifier,'write',{'text':text},request_id)

    def resize(self,cid,identifier,rows,cols):
        self._size(rows,cols)
        result=self._input(cid,identifier,'resize',{'rows':rows,'cols':cols},str(uuid.uuid4()))
        with self.lock,self.db:self.db.execute('UPDATE sessions SET rows=?,cols=? WHERE id=? AND conversation_id=?',(rows,cols,identifier,cid))
        return result

    def stop(self,cid,identifier):
        session=self._session(cid,identifier)
        result=self.jobs.cancel(session['job_id']) if session['job_id'] else {'state':'unknown'}
        with self.lock,self.db:self.db.execute('UPDATE sessions SET state=? WHERE id=?',(result['state'],identifier))
        return self.status(cid,identifier)

    def stop_all(self):
        self.revoke_inputs()
        with self.lock:rows=[dict(r) for r in self.db.execute("SELECT id,conversation_id FROM sessions WHERE state IN ('accepted','running')")]
        for row in rows:self.stop(row['conversation_id'],row['id'])

    def revoke_inputs(self,cids=None):
        with self.lock,self.db:
            if cids is None:self.db.execute("UPDATE inputs SET state='unknown' WHERE state='pending'")
            else:
                for cid in cids:
                    self.db.execute("UPDATE inputs SET state='unknown' WHERE state='pending' AND session_id IN (SELECT id FROM sessions WHERE conversation_id=?)",(cid,))

    def close(self):
        self.stop_all()
        with self.lock:self.db.close()
