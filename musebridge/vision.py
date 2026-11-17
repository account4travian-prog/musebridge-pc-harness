"""Owner-enabled screenshot continuations over the supported SDK image route.

Images are new, labelled turns in the same chat. This is not inline tool-result
vision or an undocumented interrupt API. No ambiguous delivery is replayed.
"""
import time
import uuid
from .media import from_bytes


class VisionService:
    def __init__(self,catalog,execution,artifacts):
        self.catalog,self.execution,self.artifacts=catalog,execution,artifacts
        self.enabled={};self.windows={}
        with catalog.lock,catalog.db:
            catalog.db.execute('''CREATE TABLE IF NOT EXISTS visual_feedback_v1(
                id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,parent_run_id TEXT NOT NULL,chain_id TEXT NOT NULL,
                request_id TEXT NOT NULL,artifact_id TEXT,revision INTEGER NOT NULL,generation INTEGER NOT NULL,
                state TEXT NOT NULL,created REAL NOT NULL,delivery_run_id TEXT,error_code TEXT,
                UNIQUE(conversation_id,parent_run_id,request_id))''')
            catalog.db.execute("UPDATE visual_feedback_v1 SET state='held' WHERE state='pending'")
            catalog.db.execute("UPDATE visual_feedback_v1 SET state='unknown',error_code='Interrupted' WHERE state IN ('capturing','sending')")

    def configure(self,cid,enabled,*,browser=True,window=None,max_steps=6):
        if type(enabled) is not bool or type(browser) is not bool or type(max_steps) is not int or not 1<=max_steps<=6:raise ValueError('Invalid visual feedback configuration')
        if not self.catalog.conversation(cid)['project_id']:raise ValueError('Choose a project for automatic visual feedback')
        scope=self.execution.scope(cid)
        if enabled:
            if window is not None:
                if not isinstance(window,dict) or type(window.get('window_handle')) is not int or type(window.get('process_id')) is not int:raise ValueError('Select an allowed desktop window')
                self.windows[scope]=dict(window)
            else:self.windows.pop(scope,None)
            self.enabled[scope]={'browser':browser,'max_steps':max_steps}
            # Explicit enable resumes only this selected conversation's held captures.
            with self.catalog.lock,self.catalog.db:
                self.catalog.db.execute("UPDATE visual_feedback_v1 SET state='pending',generation=? WHERE conversation_id=? AND state='held'",(self.execution.generation,cid))
        else:
            self.enabled.pop(scope,None);self.windows.pop(scope,None)
            for conversation in self.catalog.conversations(True):
                if self.execution.scope(conversation['id'])==scope:self.hold(conversation['id'],cancel=True)
        return self.status(cid)

    def status(self,cid):
        scope=self.execution.scope(cid)
        rows=self.catalog.rows('SELECT id,parent_run_id,chain_id,artifact_id,state,delivery_run_id,error_code FROM visual_feedback_v1 WHERE conversation_id=? ORDER BY created DESC LIMIT 30',(cid,))
        return {'enabled':scope in self.enabled,'browser':self.enabled.get(scope,{}).get('browser',False),
            'window':self.windows.get(scope),'max_steps':self.enabled.get(scope,{}).get('max_steps',6),'deliveries':rows,
            'transport':'Labelled automatic image continuation in this same chat; not inline tool-result vision.'}

    def hold(self,cid=None,*,cancel=False):
        with self.catalog.lock,self.catalog.db:
            sql="UPDATE visual_feedback_v1 SET state=? WHERE state='pending'";args=['cancelled' if cancel else 'held']
            if cid is not None:sql+=' AND conversation_id=?';args.append(cid)
            self.catalog.db.execute(sql,args)

    def pending(self,cid):
        return bool(self.catalog.rows("SELECT id FROM visual_feedback_v1 WHERE conversation_id=? AND state='pending' LIMIT 1",(cid,)))

    def capture(self,router,cid,run_id,request_id,source,guard,*,path=None,marks=True,region=None):
        if not isinstance(request_id,str) or not request_id or len(request_id)>150:raise ValueError('Use a stable visual observation request ID')
        old=self.catalog.rows('SELECT * FROM visual_feedback_v1 WHERE conversation_id=? AND parent_run_id=? AND request_id=?',(cid,run_id,request_id))
        if old:return self._receipt(old[0])
        scope=self.execution.scope(cid);config=self.enabled.get(scope)
        if not config:raise ValueError('Enable automatic visual feedback locally for this project first')
        if source not in ('browser','desktop','file'):raise ValueError('source must be browser, desktop or file')
        if source=='browser' and not config['browser']:raise ValueError('Automatic browser screenshots are not enabled')
        binding=self.windows.get(scope)
        if source=='desktop' and (not binding or self.execution.policy(cid)['profile']!='desktop'):raise ValueError('Select an allowed window locally and enable Desktop control')
        guard();parent=self.execution.run(cid,run_id)
        if parent['origin']=='voice':raise ValueError('Automatic image continuations require a text-chat task; finish the voice call and request the observation in chat')
        if parent['state'] not in ('sending','acknowledged'):raise ValueError('Visual feedback requires an active acknowledged user run')
        previous=self.catalog.rows('SELECT chain_id FROM visual_feedback_v1 WHERE conversation_id=? AND delivery_run_id=?',(cid,run_id))
        chain=previous[0]['chain_id'] if previous else run_id
        identifier=uuid.uuid4().hex
        with self.execution.lock,self.catalog.lock,self.catalog.db:
            guard()
            if self.catalog.db.execute('SELECT count(*) FROM visual_feedback_v1 WHERE chain_id=?',(chain,)).fetchone()[0]>=config['max_steps']:raise ValueError('Visual continuation limit reached; inspect results before requesting another task')
            if self.catalog.db.execute("SELECT 1 FROM visual_feedback_v1 WHERE conversation_id=? AND state IN ('capturing','pending','sending')",(cid,)).fetchone():raise ValueError('One screenshot continuation is already pending; finish this turn first')
            self.catalog.db.execute('INSERT INTO visual_feedback_v1 VALUES(?,?,?,?,?,NULL,?,?,?,?,NULL,NULL)',
                (identifier,cid,run_id,chain,request_id,self.execution.policy(cid)['revision'],self.execution.generation,'capturing',time.time()))
        try:
            if source=='file':
                from .workspace import Workspace
                context = self.catalog.context(cid)
                if not context['project']:raise ValueError('Choose a project for file image inspection')
                target = Workspace(context['project']['root'])._validate_operation_path(path)
                result = {'path': target}
            elif source=='browser':result=router.browser.call('browser.screenshot',{'conversation_id':cid},guard=guard)
            else:
                # A HWND alone can be reused. Recheck the granted process too.
                info=router.desktop.call('desktop.observe',{'conversation_id':cid,'window_handle':binding['window_handle']})
                if info['window']['process_id']!=binding['process_id']:raise ValueError('Granted window process changed; select it again locally')
                if marks:
                    info=router.desktop.call('desktop.snapshot',{'conversation_id':cid,'window_handle':binding['window_handle']})
                    if info['window']['process_id']!=binding['process_id']:raise ValueError('Granted window process changed; select it again locally')
                guard();result=router.desktop.call('desktop.screenshot',{'conversation_id':cid,'window_handle':binding['window_handle']})
                if marks and result.get('rectangle') and result['rectangle']!=info['window'].get('rectangle'):
                    raise ValueError('Window moved since the reference snapshot; request fresh labels')
            from .machine_images import prepare
            from pathlib import Path
            image_marks=[]
            if marks and source=='browser':image_marks=result.get('marks',[])
            elif marks and source=='desktop':
                state=router.desktop.refs.states.get((cid,binding['window_handle']),{})
                origin=info['window'].get('rectangle') or {'left':0,'top':0}
                for ref,record in state.get('refs',{}).items():
                    rect=record['node'].get('rectangle')
                    if rect:image_marks.append({'ref':ref,'rectangle':{'left':rect['left']-origin['left'],'top':rect['top']-origin['top']}})
            prepared=prepare(result['path'],router.desktop.artifact_dir/('observation-'+identifier+'.jpg'),image_marks,region)
            artifact=self.artifacts.import_file(cid,prepared['path'],source='Automatic '+source+' observation')
            if source == 'file':
                from_bytes(self.artifacts.data(cid,artifact['id']),artifact['mime_type'],artifact['filename'])
            with self.execution.lock,self.catalog.lock,self.catalog.db:
                guard()
                if self.enabled.get(scope) is not config:raise ValueError('Visual capture permission changed before delivery was queued')
                self.catalog.db.execute("UPDATE visual_feedback_v1 SET artifact_id=?,state='pending' WHERE id=?",(artifact['id'],identifier))
                self.catalog.event(cid,'vision.queued',{'feedback_id':identifier,'parent_run_id':run_id,'artifact_id':artifact['id']})
        except Exception as exc:
            with self.catalog.lock,self.catalog.db:self.catalog.db.execute("UPDATE visual_feedback_v1 SET state='failed',error_code=? WHERE id=?",(type(exc).__name__,identifier))
            raise
        return {**self._receipt(self._row(cid,identifier)),
                **{key:prepared[key] for key in ('marks_available','marks','original_size','size_pixels','region')}}

    def _row(self,cid,identifier):
        rows=self.catalog.rows('SELECT * FROM visual_feedback_v1 WHERE id=? AND conversation_id=?',(identifier,cid))
        if not rows:raise ValueError('Visual feedback belongs to another conversation')
        return rows[0]

    @staticmethod
    def _receipt(row):
        return {'feedback_id':row['id'],'artifact_id':row['artifact_id'],'state':row['state'],
            'instruction':'This is a saved screenshot, not pixels in this tool result. Finish this turn now; MuseBridge will automatically send the image in a labelled continuation, then continue the original task. Do not infer the image contents from its path.'}

    def claim(self,cid):
        if self.execution.scope(cid) not in self.enabled or self.execution.paused:return None
        rows=self.catalog.rows("SELECT * FROM visual_feedback_v1 WHERE conversation_id=? AND state='pending' ORDER BY created LIMIT 1",(cid,))
        if not rows:return None
        row=rows[0];parent=self.execution.run(cid,row['parent_run_id'])
        if parent['state'] not in ('remote_finished','remote_reply_finished'):return None
        with self.execution.lock,self.catalog.lock,self.catalog.db:
            self.validate_delivery(cid,row,expected_state='pending')
            if self.catalog.db.execute("UPDATE visual_feedback_v1 SET state='sending' WHERE id=? AND state='pending'",(row['id'],)).rowcount!=1:return None
        return row

    def validate_delivery(self,cid,row,expected_state='sending'):
        current=self._row(cid,row['id'])
        if current['state']!=expected_state:raise ValueError('Visual delivery is no longer pending; it was not replayed')
        if self.execution.scope(cid) not in self.enabled or self.execution.paused or current['generation']!=self.execution.generation:raise ValueError('Visual delivery permission was revoked')
        self.execution.check(cid,'vision.observe',current['revision'])

    def image(self,cid,row):
        meta=self.artifacts.get(cid,row['artifact_id'])
        return from_bytes(self.artifacts.data(cid,row['artifact_id']),meta['mime_type'],meta['filename'])

    def bind_run(self,cid,row,run_id):
        # Bind before network admission: remote tools can arrive before the HTTP
        # acknowledgment, and must already inherit this chain's image budget.
        with self.catalog.lock,self.catalog.db:
            cursor=self.catalog.db.execute("UPDATE visual_feedback_v1 SET delivery_run_id=? WHERE id=? AND conversation_id=? AND state='sending'",(run_id,row['id'],cid))
            if cursor.rowcount!=1:raise ValueError('Visual delivery is no longer sending')

    def settle(self,cid,row,state,run_id=None):
        if state not in ('acknowledged','unknown','cancelled'):raise ValueError('Invalid visual delivery outcome')
        with self.catalog.lock,self.catalog.db:
            self.catalog.db.execute("UPDATE visual_feedback_v1 SET state=?,delivery_run_id=? WHERE id=? AND conversation_id=? AND state='sending'",(state,run_id,row['id'],cid))
            self.catalog.event(cid,'vision.'+state,{'feedback_id':row['id'],'delivery_run_id':run_id})
