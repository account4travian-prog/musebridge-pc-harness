"""Persistent image drafts and acknowledged local attachment projections."""
import time
from .media import ImageAttachment


class Attachments:
    def __init__(self,catalog):
        self.catalog=catalog
        with catalog.lock,catalog.db:
            catalog.db.execute('''CREATE TABLE IF NOT EXISTS image_drafts(
                conversation_id TEXT PRIMARY KEY,mime_type TEXT NOT NULL,filename TEXT NOT NULL,data BLOB NOT NULL)''')
            catalog.db.execute('''CREATE TABLE IF NOT EXISTS message_images(
                conversation_id TEXT NOT NULL,message_id TEXT NOT NULL,mime_type TEXT NOT NULL,
                filename TEXT NOT NULL,data BLOB NOT NULL,created REAL NOT NULL,
                PRIMARY KEY(conversation_id,message_id))''')

    def draft(self,cid,image=None,*,clear=False):
        self.catalog.conversation(cid)
        with self.catalog.lock,self.catalog.db:
            if clear:self.catalog.db.execute('DELETE FROM image_drafts WHERE conversation_id=?',(cid,))
            elif image is not None:
                if not isinstance(image,ImageAttachment):raise ValueError('Expected a validated image attachment')
                self.catalog.db.execute('INSERT OR REPLACE INTO image_drafts VALUES(?,?,?,?)',
                    (cid,image.mime_type,image.filename,image.data))
            row=self.catalog.db.execute('SELECT * FROM image_drafts WHERE conversation_id=?',(cid,)).fetchone()
            return ImageAttachment(bytes(row['data']),row['mime_type'],row['filename']) if row else None

    def acknowledge(self,cid,message_ids,image,*,clear_draft=True):
        if not isinstance(image,ImageAttachment):raise ValueError('Expected a validated image attachment')
        if not message_ids:return
        with self.catalog.lock,self.catalog.db:
            for message_id in message_ids:
                self.catalog.db.execute('INSERT OR REPLACE INTO message_images VALUES(?,?,?,?,?,?)',
                    (cid,message_id,image.mime_type,image.filename,image.data,time.time()))
            row=self.catalog.db.execute('SELECT data FROM image_drafts WHERE conversation_id=?',(cid,)).fetchone()
            if clear_draft and row and bytes(row['data'])==image.data:
                self.catalog.db.execute('DELETE FROM image_drafts WHERE conversation_id=?',(cid,))

    def message(self,cid,message_id):
        rows=self.catalog.rows('SELECT mime_type,filename,data FROM message_images WHERE conversation_id=? AND message_id=?',(cid,message_id))
        if not rows:return None
        row=rows[0];return ImageAttachment(bytes(row['data']),row['mime_type'],row['filename'])

    def metadata(self,cid,message_id):
        rows=self.catalog.rows('SELECT filename,mime_type,length(data) AS size FROM message_images WHERE conversation_id=? AND message_id=?',(cid,message_id))
        return rows[0] if rows else None
