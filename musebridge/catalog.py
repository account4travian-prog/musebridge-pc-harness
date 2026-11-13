"""Durable projects, conversations, plans and message projections.

The local log is a companion record; Muse owns its remote reasoning history.
No local LLM history is synthesized from this database.
"""
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path


class Catalog:
    def __init__(self, directory):
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(Path(directory) / 'harness.sqlite'), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        from .execution import SCHEMA_VERSION
        if self.db.execute('PRAGMA user_version').fetchone()[0]>SCHEMA_VERSION:
            self.db.close();raise RuntimeError('Harness database requires a newer MuseBridge build')
        with self.db:
            self.db.executescript('''
              CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT,root TEXT,instructions TEXT);
              CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,project_id TEXT,title TEXT,
                remote_id TEXT,created REAL,plan TEXT DEFAULT '',goal TEXT DEFAULT '',goal_state TEXT DEFAULT 'idle');
              CREATE TABLE IF NOT EXISTS messages(conversation_id TEXT,id TEXT,role TEXT,text TEXT,
                created REAL,PRIMARY KEY(conversation_id,id));
              CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,conversation_id TEXT,
                kind TEXT,data TEXT,created REAL);
              CREATE TABLE IF NOT EXISTS drafts(conversation_id TEXT PRIMARY KEY,text TEXT);
              CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY,conversation_id TEXT,text TEXT,state TEXT,created REAL);
              CREATE TABLE IF NOT EXISTS preferences(key TEXT PRIMARY KEY,value TEXT);
            ''')
            columns={row[1] for row in self.db.execute('PRAGMA table_info(conversations)')}
            if 'archived' not in columns:
                self.db.execute('ALTER TABLE conversations ADD COLUMN archived INTEGER NOT NULL DEFAULT 0')
            self.db.execute("INSERT OR IGNORE INTO conversations(id,title,created) VALUES('main','Main Muse chat',?)", (time.time(),))
            self.db.execute("UPDATE outbox SET state='unknown' WHERE state='sending'")
        from .execution import migrate
        migrate(self)

    def rows(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args)]

    def project(self, identifier):
        rows = self.rows('SELECT * FROM projects WHERE id=?', (identifier,))
        if not rows:
            raise ValueError('Unknown project')
        return rows[0]

    def projects(self):
        return self.rows('SELECT * FROM projects ORDER BY name')

    def create_project(self, name, root, instructions=''):
        path = Path(root).resolve(strict=True)
        if not path.is_dir() or not name.strip() or len(name) > 200 or len(instructions) > 16000:
            raise ValueError('Invalid project')
        identifier = str(uuid.uuid4())
        with self.lock, self.db:
            self.db.execute('INSERT INTO projects VALUES(?,?,?,?)', (identifier,name.strip(),str(path),instructions))
        return self.project(identifier)

    def update_project(self, identifier, instructions):
        self.project(identifier)
        if len(instructions) > 16000:
            raise ValueError('Project instructions exceed 16000 characters')
        with self.lock, self.db:
            self.db.execute('UPDATE projects SET instructions=? WHERE id=?', (instructions,identifier))

    def conversation(self, identifier):
        rows = self.rows('SELECT * FROM conversations WHERE id=?', (identifier,))
        if not rows:
            raise ValueError('Unknown conversation')
        return rows[0]

    def conversations(self,include_archived=False):
        return self.rows('SELECT * FROM conversations'+('' if include_archived else ' WHERE archived=0')+' ORDER BY created DESC')

    def rename_conversation(self,identifier,title):
        self.conversation(identifier)
        if identifier=='main' or not title.strip() or len(title)>200:
            raise ValueError('Choose a conversation title of 1..200 characters')
        with self.lock,self.db:
            self.db.execute('UPDATE conversations SET title=? WHERE id=?',(title.strip(),identifier))

    def archive_conversation(self,identifier,archived=True):
        self.conversation(identifier)
        if identifier=='main':raise ValueError('The main chat cannot be archived')
        with self.lock,self.db:
            self.db.execute('UPDATE conversations SET archived=? WHERE id=?',(int(bool(archived)),identifier))

    def preference(self,key,value=None,default=None):
        with self.lock,self.db:
            if value is not None:
                self.db.execute('INSERT OR REPLACE INTO preferences VALUES(?,?)',(key,json.dumps(value)))
            rows=self.rows('SELECT value FROM preferences WHERE key=?',(key,))
            return json.loads(rows[0]['value']) if rows else default

    def create_conversation(self, title, project_id=None):
        if not title.strip() or len(title) > 200:
            raise ValueError('Invalid conversation title')
        if project_id:
            self.project(project_id)
        identifier = str(uuid.uuid4())
        with self.lock, self.db:
            self.db.execute('INSERT INTO conversations(id,project_id,title,remote_id,created) VALUES(?,?,?,?,?)',
                            (identifier,project_id,title.strip(),identifier,time.time()))
        return self.conversation(identifier)

    def event(self, conversation_id, kind, data):
        with self.lock, self.db:
            self.db.execute('INSERT INTO events(conversation_id,kind,data,created) VALUES(?,?,?,?)',
                            (conversation_id,kind,json.dumps(data,ensure_ascii=False),time.time()))

    def message(self, conversation_id, identifier, role, text):
        if role not in ('You','Muse') or len(text.encode('utf-8')) > 65536:
            raise ValueError('Invalid message projection')
        with self.lock, self.db:
            self.db.execute('INSERT INTO messages VALUES(?,?,?,?,?) ON CONFLICT(conversation_id,id) '
                            'DO UPDATE SET text=excluded.text,role=excluded.role',
                            (conversation_id,identifier,role,text,time.time()))

    def messages(self, conversation_id):
        return self.rows('SELECT * FROM (SELECT * FROM messages WHERE conversation_id=? ORDER BY created DESC LIMIT 256) ORDER BY created',
                         (conversation_id,))

    def draft(self, conversation_id, text=None):
        with self.lock, self.db:
            if text is not None:
                if len(text.encode()) > 65536:
                    raise ValueError('Draft too large')
                self.db.execute('INSERT OR REPLACE INTO drafts VALUES(?,?)',(conversation_id,text))
            rows = self.rows('SELECT text FROM drafts WHERE conversation_id=?',(conversation_id,))
            return rows[0]['text'] if rows else ''

    def plan(self, conversation_id, text=None, goal=None, state=None):
        self.conversation(conversation_id)
        with self.lock, self.db:
            for key,value in [('plan',text),('goal',goal),('goal_state',state)]:
                if value is None:
                    continue
                if not isinstance(value,str) or len(value) > 16000:
                    raise ValueError('Invalid plan/goal')
                if key == 'goal_state' and value not in ('idle','active','paused','complete','blocked'):
                    raise ValueError('Invalid goal state')
                self.db.execute('UPDATE conversations SET '+key+'=? WHERE id=?',(value,conversation_id))
            if text is not None or goal is not None or state is not None:
                self.event(conversation_id,'plan.updated',{'plan':text,'goal':goal,'state':state})
        return self.conversation(conversation_id)

    def admit_send(self, conversation_id, text):
        identifier = str(uuid.uuid4())
        with self.lock,self.db:
            self.db.execute('INSERT INTO outbox VALUES(?,?,?,?,?)',(identifier,conversation_id,text,'sending',time.time()))
        return identifier

    def settle_send(self, identifier, state):
        if state not in ('acknowledged','unknown','rejected'):
            raise ValueError('Invalid send state')
        with self.lock,self.db:
            self.db.execute('UPDATE outbox SET state=? WHERE id=?',(state,identifier))

    def context(self, conversation_id):
        conversation = self.conversation(conversation_id)
        project = self.project(conversation['project_id']) if conversation['project_id'] else None
        data = {'conversation_id':conversation_id,'project':project,'goal':conversation['goal'],
                'goal_state':conversation['goal_state'],'plan':conversation['plan']}
        if project:
            try:
                from .workspace import Workspace
                workspace=Workspace(project['root'])
                instructions=workspace._validate_operation_path('AGENTS.md')
                if instructions.is_file():
                    with instructions.open('r',encoding='utf-8',errors='replace') as stream:
                        data['project_agents_md']=stream.read(16000)
            except (OSError,ValueError,RuntimeError):
                # Keep a generic status only; a rejected symlink/private path
                # must not expose the resolved target in context output.
                data['project_agents_md']='[AGENTS.md unavailable: blocked by workspace path guard]'
        if hasattr(self,'tasks'):
            tasks=self.tasks.list(conversation_id)
            data['tasks']=[{'task_id':t['task_id'],'title':t['title'],'state':t['state'],
                'criteria':t['criteria'][:250],'dependencies':t['dependencies'],'verification_mode':t['verification_mode']} for t in tasks[:20]]
            data['task_count']=len(tasks);data['tasks_truncated']=len(tasks)>20
            if data['goal_state']=='complete' and any(t['state']!='complete' for t in tasks):
                data['reported_goal_state']='complete';data['goal_state']='paused';data['goal_verification_stale']=True
        if hasattr(self,'continuity'):
            notes=self.continuity.list(conversation_id,5)
            data['project_facts']=[n for n in notes['notes'] if n['effective']]
            data['project_facts_truncated']=notes['truncated']
        return data

    def close(self):
        with self.lock:
            self.db.close()
