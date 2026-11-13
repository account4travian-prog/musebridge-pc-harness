"""Durable execution ownership and local policy; no model or agent loop."""
from contextlib import contextmanager
import threading
import time
import uuid


SCHEMA_VERSION = 5
PROFILES = ('read_only', 'project', 'desktop')
READ_TOOLS = frozenset(('bridge.context','bridge.capabilities','run.list','run.calls',
    'workspace.list','workspace.search','file.read','file.list','file.search','git.status','job.status','job.wait',
    'browser.observe','browser.screenshot','desktop.windows','desktop.observe','desktop.screenshot',
    'change.list','change.diff','task.list','task.check_files','project.notes','project.handoff','artifact.list','artifact.read','artifact.add','vision.status',
    'git.diff','git.branches','git.worktrees','hook.status','mcp.servers','mcp.tools','mcp.read','terminal.list','terminal.read',
    'change.review','browser.snapshot','desktop.snapshot','mcp.list'))
CLEANUP_TOOLS = frozenset(('job.cancel','browser.close','mcp.close','terminal.stop','shell.job','terminal.close'))
WRITE_TOOLS = frozenset(('file.write','file.edit','plan.update','job.start','shell.run','change.restore',
    'task.create','task.update','task.verify_job','project.note','conversation.handoff','git.worktree.create','mcp.call',
    'terminal.start','terminal.write','terminal.resize','vision.observe','screen.look','task.set','task.verify','terminal.open','terminal.send'))
BROWSER_TOOLS = frozenset(('browser.open','browser.fill','browser.click','browser.navigate','browser.act','browser.eval','browser.select','browser.check'))


def requires_project(name):
    return (name.startswith(('file.','workspace.','git.','change.','terminal.','mcp.','project.'))
            or name in ('shell.run','job.start','task.verify','task.check_files','task.verify_job',
                        'artifact.add','screen.look','vision.observe','conversation.handoff'))


def effect_of(name):
    if name in READ_TOOLS:return 'read'
    if name in CLEANUP_TOOLS:return 'cleanup'
    if name in WRITE_TOOLS:return 'project_write'
    if name in BROWSER_TOOLS:return 'browser_action'
    if name in ('desktop.act','desktop.launch'):return 'desktop_action'
    raise ValueError('Tool has no declared execution effect')


def migrate(catalog):
    """One atomic additive migration; refuse newer schemas before changing them."""
    with catalog.lock, catalog.db:
        catalog.db.execute('BEGIN IMMEDIATE')
        version=catalog.db.execute('PRAGMA user_version').fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError('Harness database requires a newer MuseBridge build')
        catalog.db.execute('''CREATE TABLE IF NOT EXISTS execution_scopes(
            scope_key TEXT PRIMARY KEY,profile TEXT NOT NULL,revision INTEGER NOT NULL)''')
        catalog.db.execute('''CREATE TABLE IF NOT EXISTS runs(
            id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,project_id TEXT,origin TEXT NOT NULL,
            state TEXT NOT NULL,created REAL NOT NULL,finished REAL)''')
        if 'scope_revision' not in {r[1] for r in catalog.db.execute('PRAGMA table_info(runs)')}:
            catalog.db.execute('ALTER TABLE runs ADD COLUMN scope_revision INTEGER NOT NULL DEFAULT 1')
        catalog.db.execute('''CREATE TABLE IF NOT EXISTS tool_calls(
            id TEXT PRIMARY KEY,run_id TEXT NOT NULL,conversation_id TEXT NOT NULL,tool TEXT NOT NULL,
            effect TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,finished REAL,error_code TEXT)''')
        catalog.db.execute('CREATE INDEX IF NOT EXISTS run_conversation ON runs(conversation_id,created)')
        catalog.db.execute('CREATE INDEX IF NOT EXISTS call_run ON tool_calls(run_id,created)')
        call_columns = {row[1] for row in catalog.db.execute('PRAGMA table_info(tool_calls)')}
        if 'result_bytes' not in call_columns:
            catalog.db.execute('ALTER TABLE tool_calls ADD COLUMN result_bytes INTEGER')
        if 'error_message' not in call_columns:
            catalog.db.execute('ALTER TABLE tool_calls ADD COLUMN error_message TEXT')
        catalog.db.execute('''CREATE TABLE IF NOT EXISTS file_read_receipts(
            conversation_id TEXT NOT NULL,project_root TEXT NOT NULL,path TEXT NOT NULL,
            sha256 TEXT NOT NULL,observed REAL NOT NULL,
            PRIMARY KEY(conversation_id,project_root,path))''')
        catalog.db.execute('''CREATE TABLE IF NOT EXISTS finish_requests_v1(
            id TEXT PRIMARY KEY,revision INTEGER NOT NULL DEFAULT 0,
            checked INTEGER NOT NULL DEFAULT 0,queued TEXT,
            rounds INTEGER NOT NULL DEFAULT 0,round_limit INTEGER NOT NULL DEFAULT 0)''')
        catalog.db.execute('''CREATE TABLE IF NOT EXISTS finish_runs_v1(
            run_id TEXT PRIMARY KEY,request_id TEXT NOT NULL)''')
        catalog.db.execute('''CREATE TABLE IF NOT EXISTS finish_changes_v1(
            request_id TEXT NOT NULL,run_id TEXT NOT NULL,path TEXT NOT NULL,
            revision INTEGER NOT NULL,PRIMARY KEY(request_id,path))''')
        catalog.db.execute('''CREATE TABLE IF NOT EXISTS finish_checks_v1(
            job_id TEXT PRIMARY KEY,request_id TEXT NOT NULL,revision INTEGER NOT NULL,
            approved INTEGER NOT NULL DEFAULT 1)''')
        catalog.db.execute('''CREATE TABLE IF NOT EXISTS request_queue_v1(
            id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,text TEXT NOT NULL,state TEXT NOT NULL,
            created REAL NOT NULL,updated REAL NOT NULL,mime_type TEXT,filename TEXT,image BLOB,run_id TEXT)''')
        queue_columns = {row[1] for row in catalog.db.execute('PRAGMA table_info(request_queue_v1)')}
        for name in ('automatic_run', 'automatic_request'):
            if name not in queue_columns:
                catalog.db.execute('ALTER TABLE request_queue_v1 ADD COLUMN ' + name + ' TEXT')
        catalog.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS queue_automatic_request '
                           'ON request_queue_v1(automatic_request) WHERE automatic_request IS NOT NULL')
        catalog.db.execute('''CREATE TABLE IF NOT EXISTS shadow_runs_v1(
            run_id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,project_id TEXT NOT NULL,
            root TEXT NOT NULL,start_tree TEXT,end_tree TEXT,state TEXT NOT NULL,note TEXT NOT NULL)''')
        catalog.db.execute('PRAGMA user_version=5')


class Execution:
    def __init__(self,catalog):
        self.catalog=catalog
        self.lock=threading.RLock()
        self.paused=False
        self.generation=0
        with catalog.lock,catalog.db:
            now=time.time()
            catalog.db.execute("UPDATE runs SET state='unknown',finished=? WHERE state IN ('sending','acknowledged','local')",(now,))
            catalog.db.execute("UPDATE tool_calls SET state='unknown',finished=?,error_code='Interrupted' WHERE state IN ('accepted','running')",(now,))

    def scope(self,cid):
        c=self.catalog.conversation(cid)
        return 'project:'+c['project_id'] if c['project_id'] else 'chat:'+cid

    def policy(self,cid):
        with self.lock:
            key=self.scope(cid)
            rows=self.catalog.rows('SELECT * FROM execution_scopes WHERE scope_key=?',(key,))
            return {'profile':rows[0]['profile'] if rows else 'project',
                    'revision':rows[0]['revision'] if rows else 1,'paused':self.paused}

    def set_profile(self,cid,profile):
        if profile not in PROFILES:raise ValueError('Unknown execution profile')
        with self.lock,self.catalog.lock,self.catalog.db:
            policy=self.policy(cid);key=self.scope(cid)
            if profile!=policy['profile']:
                self.catalog.db.execute('INSERT OR REPLACE INTO execution_scopes VALUES(?,?,?)',
                    (key,profile,policy['revision']+1))
                self.catalog.event(cid,'policy.changed',{'profile':profile,'revision':policy['revision']+1})
            return self.policy(cid)

    def pause(self,value):
        with self.lock:
            if self.paused!=bool(value):
                self.generation+=1
                if value:
                    with self.catalog.lock,self.catalog.db:
                        for c in self.catalog.conversations(include_archived=True):
                            self.catalog.db.execute("UPDATE runs SET state='interrupted',finished=? WHERE conversation_id=? AND state IN ('sending','acknowledged')",(time.time(),c['id']))
            self.paused=bool(value)

    def check(self,cid,name,revision=None):
        policy=self.policy(cid);effect=effect_of(name)
        if revision is not None and (type(revision) is not int or revision!=policy['revision']):
            raise ValueError('Execution scope changed; refresh bridge.capabilities')
        if self.paused and name not in READ_TOOLS|CLEANUP_TOOLS:
            raise ValueError('Work is paused locally')
        if name.startswith('desktop.') and policy['profile']!='desktop':
            raise ValueError('Enable Desktop control locally before desktop actions')
        if policy['profile']=='read_only' and effect not in ('read','cleanup'):
            raise ValueError('Read only profile does not allow this tool')
        return effect,policy

    @contextmanager
    def admission(self,cid,name,revision=None):
        # Mutating adapters keep this gate through their actual local effect.
        # Read/wait paths must not hold it while waiting for long-running work.
        with self.lock:
            effect,policy=self.check(cid,name,revision)
            stamp=(policy['revision'],self.generation,self.scope(cid))
        def guard():
            if self.scope(cid)!=stamp[2]:raise ValueError('Workspace changed; start a new request in the selected workspace')
            current=self.check(cid,name,stamp[0])
            if stamp[1]!=self.generation:raise ValueError('Admission revoked by Stop Work; refresh context')
            return current
        yield effect,guard

    def begin_run(self,cid,origin='message'):
        c=self.catalog.conversation(cid);identifier=str(uuid.uuid4())
        with self.lock,self.catalog.lock,self.catalog.db:
            self.catalog.db.execute('INSERT INTO runs(id,conversation_id,project_id,origin,state,created,scope_revision) VALUES(?,?,?,?,?,?,?)',
                (identifier,cid,c['project_id'],origin,'local' if origin=='tool' else 'sending',time.time(),self.policy(cid)['revision']))
            self.catalog.event(cid,'run.started',{'run_id':identifier,'origin':origin})
        callback=getattr(self,'on_run_started',None)
        if callback and origin!='tool':callback(cid,identifier)
        return identifier

    def run(self,cid,run_id):
        rows=self.catalog.rows('SELECT * FROM runs WHERE id=? AND conversation_id=?',(run_id,cid))
        if not rows:raise ValueError('Run belongs to a different or unknown conversation')
        return rows[0]

    def active_run(self,cid):
        rows=self.catalog.rows("SELECT id FROM runs WHERE conversation_id=? AND state IN ('sending','acknowledged') ORDER BY created DESC LIMIT 1",(cid,))
        return rows[0]['id'] if rows else None

    def state(self,cid,run_id,state):
        if state not in ('acknowledged','remote_finished','remote_reply_finished','succeeded','failed','interrupted','unknown'):
            raise ValueError('Invalid run state')
        with self.catalog.lock,self.catalog.db:
            row=self.run(cid,run_id)
            if row['state'] not in ('sending','acknowledged','local'):return
            self.catalog.db.execute('UPDATE runs SET state=?,finished=? WHERE id=?',
                (state,None if state=='acknowledged' else time.time(),run_id))
            self.catalog.event(cid,'run.'+state,{'run_id':run_id})
        callback=getattr(self,'on_run_finished',None)
        if callback and state!='acknowledged':callback(cid,run_id)

    def start_call(self,cid,name,effect,run_id=None):
        own_run=False
        if run_id:
            row=self.run(cid,run_id)
            if row['project_id']!=self.catalog.conversation(cid)['project_id']:raise ValueError('Run belongs to the previous workspace; send a new request')
            if row['state'] not in ('sending','acknowledged','local'):raise ValueError('Run is no longer active')
            if row['scope_revision']!=self.policy(cid)['revision']:raise ValueError('Run scope changed; send a new request with current capabilities')
        else:
            run_id=self.active_run(cid)
            if run_id and self.run(cid,run_id)['project_id']!=self.catalog.conversation(cid)['project_id']:
                raise ValueError('Active request belongs to the previous workspace; send a new request')
            if not run_id:run_id=self.begin_run(cid,'tool');own_run=True
        identifier=str(uuid.uuid4())
        with self.catalog.lock,self.catalog.db:
            self.catalog.db.execute('INSERT INTO tool_calls(id,run_id,conversation_id,tool,effect,state,created,finished,error_code) VALUES(?,?,?,?,?,?,?,NULL,NULL)',
                (identifier,run_id,cid,name,effect,'accepted',time.time()))
            self.catalog.event(cid,'tool.accepted',{'call_id':identifier,'run_id':run_id,'tool':name})
        return identifier,run_id,own_run

    def call_started(self,cid,call_id,run_id,name):
        with self.catalog.lock,self.catalog.db:
            self.catalog.db.execute("UPDATE tool_calls SET state='running' WHERE id=?",(call_id,))
            self.catalog.event(cid,'tool.started',{'call_id':call_id,'run_id':run_id,'tool':name})

    def finish_call(self,cid,call_id,run_id,name,state,error_code=None,own_run=False):
        with self.catalog.lock,self.catalog.db:
            self.catalog.db.execute('UPDATE tool_calls SET state=?,finished=?,error_code=? WHERE id=?',
                (state,time.time(),error_code,call_id))
            self.catalog.event(cid,'tool.result',{'call_id':call_id,'run_id':run_id,'tool':name,
                'ok':state=='succeeded','state':state,'error_code':error_code})
        if own_run:self.state(cid,run_id,state)

    def record_statistics(self, call_id, result_bytes, error_message=None):
        """Store payload size and an already-sanitized error, never tool arguments."""
        with self.catalog.lock, self.catalog.db:
            self.catalog.db.execute(
                'UPDATE tool_calls SET result_bytes=?,error_message=? WHERE id=?',
                (result_bytes, error_message[:200] if error_message else None, call_id),
            )

    def runs(self,cid):
        self.catalog.conversation(cid)
        return self.catalog.rows('SELECT * FROM runs WHERE conversation_id=? ORDER BY created DESC LIMIT 100',(cid,))

    def calls(self,cid,run_id):
        self.run(cid,run_id)
        return self.catalog.rows('SELECT * FROM tool_calls WHERE run_id=? AND conversation_id=? ORDER BY created LIMIT 200',(run_id,cid))


class TurnRuns:
    """Attribute terminal evidence with the pinned SDK, including pre-ack events."""
    def __init__(self,execution):
        self.execution=execution;self.active={};self.scopes={};self.request_ids={};self.task_ids={};self.settling={};self.reply_finished=set()

    def begin(self,cid,origin='message'):
        from musegadget.muse_turn import MuseTurn,ReplayScope
        self.stop(cid,'interrupted')
        remote=self.execution.catalog.conversation(cid)['remote_id']
        scope=self.scopes.setdefault(cid,ReplayScope())
        identifier=self.execution.begin_run(cid,origin)
        self.active[cid]=(identifier,MuseTurn(remote,owns_chat=bool(remote),replay_scope=scope))
        self.request_ids[cid]=set();self.task_ids[cid]=set()
        return identifier

    def acknowledge(self,cid,identifier,ack):
        current=self.active.get(cid)
        if not current or current[0]!=identifier:return
        from musegadget.muse_turn import _acknowledged_message_ids
        self.request_ids[cid]=set(_acknowledged_message_ids(ack))
        self.execution.state(cid,identifier,'acknowledged')
        self._updates(cid,current[1].acknowledge(ack))

    def feed(self,cid,event):
        current=self.active.get(cid)
        if current:
            if not current[1].owns_chat and event.get('event')=='task.status':
                # Main chat is shared with phone/web. Parentless task terminals
                # cannot identify which request ended, even if the SDK emits one.
                payload=event.get('payload',{})
                parent=payload.get('reply_to_message_id') or payload.get('parent_message_id')
                task=payload.get('task_id')
                if parent not in self.request_ids[cid] and (not task or task not in self.task_ids[cid]):return
                if task:self.task_ids[cid].add(task)
            self._updates(cid,current[1].feed(event))
        elif cid in self.settling:self.settling[cid].feed(event)
        elif cid in self.scopes:
            remote=self.execution.catalog.conversation(cid)['remote_id']
            self.scopes[cid].observe_idle(event,remote)

    def _updates(self,cid,updates):
        from musegadget.muse_turn import TaskFinished
        if any(isinstance(update,TaskFinished) for update in updates):
            self._finish(cid,'remote_finished')

    def _finish(self,cid,state):
        current=self.active.pop(cid,None)
        if current:
            self.execution.state(cid,current[0],state);self.settling[cid]=current[1]
            if state=='remote_reply_finished':self.reply_finished.add(cid)
            self.request_ids.pop(cid,None);self.task_ids.pop(cid,None)
            callback=getattr(self.execution,'on_remote_finished',None)
            if callback:callback(cid,current[0])

    def poll(self,cid,now=None):
        """SDK-supported reply completion when no task-status event is emitted.

        Empty silence, unfinished text, foreign replies and running task status
        are insufficient. This is answer completion, never goal verification.
        """
        current=self.active.get(cid)
        if not current:return
        turn=current[1];clock=time.monotonic() if now is None else now
        if any(r.bound and r.done for r in turn.replies.values()) and turn.complete(clock):
            self._finish(cid,'remote_reply_finished')

    def can_continue(self,cid,now=None):
        self.poll(cid,now)
        if cid in self.active:return False
        turn=self.settling.get(cid)
        if turn:
            clock=time.monotonic() if now is None else now
            if not (turn.complete(clock) if cid in self.reply_finished else turn.settled(clock)):return False
            turn.retire();self.settling.pop(cid,None);self.reply_finished.discard(cid)
        return True

    def stop(self,cid,state='unknown'):
        self.reply_finished.discard(cid)
        settling=self.settling.pop(cid,None)
        if settling:settling.retire()
        current=self.active.pop(cid,None)
        self.request_ids.pop(cid,None);self.task_ids.pop(cid,None)
        if current:
            current[1].retire();self.execution.state(cid,current[0],state)

    def stop_all(self):
        for cid in list(set(self.active)|set(self.settling)):self.stop(cid)
