import asyncio
import json
from musebridge.catalog import Catalog
from musebridge.core import Core
from musebridge.storage import Store
from musegadget.link_client import SideChat


def test_new_side_conversation_routes_context_and_persists_ack(tmp_path):
    async def check():
        catalog=Catalog(tmp_path)
        project=catalog.create_project('project',tmp_path,'Use meaningful tests')
        conversation=catalog.create_conversation('side',project['id'])
        shown=[]
        core=Core(Store(tmp_path),lambda _:None,lambda *a:None,lambda _:None,
            catalog=catalog,conversation_message=lambda *a:shown.append(a))
        class Session:
            registered=asyncio.Event()
            chat_subscribed=asyncio.Event()
            exists=False
            sent=[]
            async def open_side_chat(self,sid):return SideChat(sid,self.exists)
            async def send_chat(self,text,session_id=None):
                self.sent.append((text,session_id));self.exists=True
                return {'ok':True,'response':{'message_id':'user-1','session_id':session_id,'is_thread':True}}
            async def subscribe_chat(self,sid=None,opened=None):
                self.chat_subscribed.set()
                if opened:opened.set()
                try:
                    await asyncio.Event().wait()
                    yield {}
                finally:self.chat_subscribed.clear()
        session=Session();session.registered.set();core.session=session
        await core.select_conversation(conversation['id'])
        assert not core._remote_exists
        await core.send('Inspect this project')
        assert session.sent[0][1]==conversation['remote_id']
        assert '[MuseBridge]' in session.sent[0][0] and 'bridge.context' in session.sent[0][0]
        assert 'Use meaningful tests' not in session.sent[0][0]
        assert shown==[(conversation['id'],'user-1','You','Inspect this project')]
        assert catalog.messages(conversation['id'])[0]['text']=='Inspect this project'
        assert catalog.rows('SELECT state FROM outbox')[0]['state']=='acknowledged'
        core._chat_task.cancel();await asyncio.gather(core._chat_task,return_exceptions=True)
        catalog.close()
    asyncio.run(check())
