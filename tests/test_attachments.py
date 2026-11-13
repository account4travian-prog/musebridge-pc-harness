import asyncio
import io
from PIL import Image
import pytest
from musebridge.media import from_bytes
from musebridge.attachments import Attachments
from musebridge.catalog import Catalog
from musebridge.core import Core


def fixture_image(color='blue'):
    stream=io.BytesIO();Image.new('RGB',(80,60),color).save(stream,format='PNG')
    return from_bytes(stream.getvalue(),'image/png','test.png')


def test_image_send_keeps_text_scope_and_acknowledged_projection(tmp_path):
    async def scenario():
        catalog=Catalog(tmp_path);cid=catalog.create_conversation('Images')['id']
        attachments=Attachments(catalog);image=fixture_image();attachments.draft(cid,image)
        core=Core(None,lambda _:None,lambda *_:None,lambda _:None,catalog=catalog,conversation_id=cid)
        class Session:
            chat_subscribed=asyncio.Event()
            calls=[]
            async def send_image(self,data,mime_type,session_id,message='',filename=None):
                self.calls.append((data,mime_type,session_id,message,filename))
                return {'ok':True,'response':{'message_id':'image-user','session_id':session_id,'is_thread':True}}
        core.session=Session();core.session.chat_subscribed.set()
        try:
            await core.send('Describe this image',image=image,conversation_id=cid)
            call=core.session.calls[0]
            assert call[:3]==(image.data,'image/png',cid)
            assert 'Describe this image' in call[3] and cid in call[3]
            assert call[4]=='test.png'
            assert attachments.draft(cid) is None
            assert attachments.message(cid,'image-user')==image
            assert catalog.messages(cid)[0]['text']=='Describe this image'
        finally:catalog.close()
    asyncio.run(scenario())


def test_unknown_image_send_preserves_draft_and_is_not_replayed(tmp_path):
    async def scenario():
        catalog=Catalog(tmp_path);attachments=Attachments(catalog);image=fixture_image()
        attachments.draft('main',image)
        core=Core(None,lambda _:None,lambda *_:None,lambda _:None,catalog=catalog)
        class Session:
            chat_subscribed=asyncio.Event();calls=0
            async def send_image(self,*a,**kw):self.calls+=1;raise ConnectionError('Response lost')
        core.session=Session();core.session.chat_subscribed.set()
        with pytest.raises(ConnectionError):await core.send('',image=image)
        assert core.session.calls==1
        assert attachments.draft('main')==image
        assert catalog.rows('SELECT state FROM outbox')[0]['state']=='unknown'
        catalog.close();catalog=Catalog(tmp_path)
        assert Attachments(catalog).draft('main')==image
        catalog.close()
    asyncio.run(scenario())


def test_acknowledging_old_image_does_not_clear_replacement_draft(tmp_path):
    catalog=Catalog(tmp_path);attachments=Attachments(catalog)
    first=fixture_image('blue');replacement=fixture_image('red')
    attachments.draft('main',replacement)
    attachments.acknowledge('main',['old-message'],first)
    assert attachments.draft('main')==replacement
    assert attachments.message('main','old-message')==first
    catalog.close()
