"""Explicit user queue with restart holds and no ambiguous-send replay."""
import asyncio
import time
import uuid
from .media import ImageAttachment


class RequestQueue:
    def __init__(self,catalog):
        self.catalog=catalog;self.enabled=set()
        self.held={item['id'] for item in catalog.conversations(include_archived=True)}
        with catalog.lock,catalog.db:
            catalog.db.execute("UPDATE request_queue_v1 SET state='unknown',updated=? WHERE state='sending'",(time.time(),))

    def add(self,cid,text,image=None,*,automatic_run=None,automatic_request=None):
        self.catalog.conversation(cid)
        if cid=='main':raise ValueError('Use a separate conversation for queued work; main-chat completion is shared with phone/web')
        if not isinstance(text,str) or len(text.encode())>65536 or (not text.strip() and image is None):raise ValueError('Queue message is empty or too large')
        if image is not None and not isinstance(image,ImageAttachment):raise ValueError('Expected a validated image')
        identifier=str(uuid.uuid4());now=time.time()
        with self.catalog.lock,self.catalog.db:
            if self.catalog.db.execute("SELECT count(*) FROM request_queue_v1 WHERE conversation_id=? AND state='pending'",(cid,)).fetchone()[0]>=100:
                raise ValueError('This conversation already has 100 pending requests')
            if automatic_request:
                existing=self.catalog.db.execute('SELECT id FROM request_queue_v1 WHERE automatic_request=?', (automatic_request,)).fetchone()
                if existing:return existing[0]
            self.catalog.db.execute('INSERT INTO request_queue_v1 '
                '(id,conversation_id,text,state,created,updated,mime_type,filename,image,run_id,automatic_run,automatic_request) '
                'VALUES(?,?,?,?,?,?,?,?,?,NULL,?,?)',
                (identifier,cid,text,'pending',now,now,image.mime_type if image else None,image.filename if image else None,image.data if image else None,automatic_run,automatic_request))
            self.catalog.event(cid,'queue.added',{'queue_id':identifier})
        if not automatic_run:self.held.discard(cid)
        if cid not in self.held and not self.catalog.rows("SELECT id FROM request_queue_v1 WHERE conversation_id=? AND state='unknown' LIMIT 1",(cid,)):
            self.enabled.add(cid)
        return identifier

    def has_pending(self,cid):
        return bool(self.catalog.rows("SELECT id FROM request_queue_v1 WHERE conversation_id=? AND state='pending' LIMIT 1",(cid,)))

    def rows(self,cid):
        self.catalog.conversation(cid)
        return self.catalog.rows('SELECT id,conversation_id,text,state,created,updated,filename,run_id,automatic_run FROM request_queue_v1 WHERE conversation_id=? ORDER BY created LIMIT 200',(cid,))

    def resume(self,cid):
        self.catalog.conversation(cid)
        if cid=='main':raise ValueError('Queue requires a dedicated conversation')
        self.enabled.add(cid)
        self.held.discard(cid)

    def hold(self,cid=None):
        if cid is None:
            self.held.update(item['id'] for item in self.catalog.conversations(include_archived=True))
            self.enabled.clear()
        else:
            self.enabled.discard(cid)
            self.held.add(cid)

    def edit(self,cid,identifier,text):
        if not isinstance(text,str) or not text.strip() or len(text.encode())>65536:raise ValueError('Queue text is empty or too large')
        with self.catalog.lock,self.catalog.db:
            cursor=self.catalog.db.execute("UPDATE request_queue_v1 SET text=?,updated=? WHERE id=? AND conversation_id=? AND state='pending'",(text,time.time(),identifier,cid))
            if cursor.rowcount!=1:raise ValueError('Only a pending request in this conversation can be edited')

    def cancel(self,cid,identifier):
        with self.catalog.lock,self.catalog.db:
            cursor=self.catalog.db.execute("UPDATE request_queue_v1 SET state='cancelled',updated=? WHERE id=? AND conversation_id=? AND state='pending'",(time.time(),identifier,cid))
            if cursor.rowcount!=1:raise ValueError('Only a pending request can be cancelled here; use Stop Work for admitted work')

    def claim(self,cid,automatic_only=False):
        if cid not in self.enabled:return None
        with self.catalog.lock,self.catalog.db:
            clause=' AND automatic_run IS NOT NULL' if automatic_only else ''
            row=self.catalog.db.execute("SELECT * FROM request_queue_v1 WHERE conversation_id=? AND state='pending'"+clause+" ORDER BY created LIMIT 1",(cid,)).fetchone()
            if not row:return None
            if self.catalog.db.execute("UPDATE request_queue_v1 SET state='sending',updated=? WHERE id=? AND state='pending'",(time.time(),row['id'])).rowcount!=1:return None
            return dict(row)

    def settle(self,cid,identifier,state,run_id=None):
        if state not in ('acknowledged','unknown'):raise ValueError('Invalid queued send outcome')
        with self.catalog.lock,self.catalog.db:
            self.catalog.db.execute("UPDATE request_queue_v1 SET state=?,updated=?,run_id=? WHERE id=? AND conversation_id=? AND state='sending'",(state,time.time(),run_id,identifier,cid))
            self.catalog.event(cid,'queue.'+state,{'queue_id':identifier,'run_id':run_id})
        if state=='unknown':self.hold(cid)


async def send_next(queue,cid,send,*,automatic_only=False,validate=None):
    """One claim; the caller controls readiness and continuation boundaries."""
    entry=queue.claim(cid,automatic_only)
    if entry is None:return False
    if entry['automatic_run'] and validate and not validate(cid,entry['automatic_run']):
        with queue.catalog.lock,queue.catalog.db:
            queue.catalog.db.execute("UPDATE request_queue_v1 SET state='cancelled',updated=? WHERE id=?",(time.time(),entry['id']))
        return False
    image=ImageAttachment(bytes(entry['image']),entry['mime_type'],entry['filename']) if entry['image'] is not None else None
    try:
        if entry['automatic_run']:
            ack=await send(cid,entry['text'],image,continuation_of=entry['automatic_run'])
        else:
            ack=await send(cid,entry['text'],image)
    except (Exception,asyncio.CancelledError):
        queue.settle(cid,entry['id'],'unknown');raise
    queue.settle(cid,entry['id'],'acknowledged',ack.get('_bridge_run_id'))
    return True
