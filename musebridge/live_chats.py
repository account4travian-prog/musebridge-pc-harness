"""Several bounded subscriptions over one gadget session; selection is presentation only."""
import asyncio
from dataclasses import dataclass,field
import time


class ConversationLimit(ValueError):
    no_effect=True
    def __init__(self):
        super().__init__('Three project chats are working. Wait for one to settle before sending in another chat.')


@dataclass
class ChatState:
    cid:str
    remote:str|None
    opened:asyncio.Event=field(default_factory=asyncio.Event)
    task:object=None
    exists:bool=True
    idle_since:float|None=None


class LiveChats:
    def __init__(self,core):
        self.core=core
        self.states={}
        self.lock=asyncio.Lock()

    def ready(self,cid):
        state=self.states.get(cid)
        return bool(state and (not state.exists or state.opened.is_set() and state.task and not state.task.done()))

    def _busy(self,cid):
        router=self.core.router
        if router and router.execution.active_run(cid):return True
        return bool(router and router.shell._list(cid)['jobs'])

    async def _remove(self,cid):
        state=self.states.pop(cid,None)
        if state and state.task:
            state.task.cancel()
            await asyncio.gather(state.task,return_exceptions=True)
        if state:state.opened.clear()
        if cid!=self.core.conversation_id:self.core.transcripts.pop(cid,None)

    async def ensure(self,cid,*,refresh=False):
        """Subscribe independently, or reserve a side-chat slot until its first send."""
        async with self.lock:
            session=self.core.session
            if session is None:raise ConnectionError('Connect before sending')
            state=self.states.get(cid)
            if state and not refresh and self.ready(cid):return state
            if state and state.task:
                state.task.cancel()
                await asyncio.gather(state.task,return_exceptions=True)
            if not state:
                projects=[key for key in self.states if key!='main']
                if cid!='main' and len(projects)>=3:
                    disposable=next((key for key in projects if key!=self.core.conversation_id and not self._busy(key)
                        and (not self.core.run_tracker or self.core.run_tracker.can_continue(key))),None)
                    if disposable is None:raise ConversationLimit()
                    await self._remove(disposable)
                remote=self.core.catalog.conversation(cid)['remote_id'] if self.core.catalog else None
                state=ChatState(cid,remote)
                self.states[cid]=state
            state.opened.clear()
            state.idle_since=None
            try:
                state.exists=(await session.open_side_chat(state.remote)).exists if state.remote else True
                if state.exists:
                    state.task=asyncio.create_task(self.core.watch_chat(session,cid,state.remote,state.opened))
                    def ended(task):
                        state.opened.clear()
                        if task.cancelled():return
                        task.exception()
                        if self.core.run_tracker:self.core.run_tracker.stop(cid,'unknown')
                        if self.core.event_observer:self.core.event_observer(cid,{'type':'disconnect'})
                        self.core.activity('Chat subscription ended; reconnect this chat before continuing.')
                    state.task.add_done_callback(ended)
                    await asyncio.wait_for(state.opened.wait(),20)
                else:state.task=None
                return state
            except BaseException:
                await self._remove(cid)
                raise

    async def reap(self):
        """Retain settled background subscriptions briefly, then release their slots."""
        async with self.lock:
            now=time.monotonic()
            for cid,state in list(self.states.items()):
                if cid in ('main',self.core.conversation_id) or self._busy(cid):
                    state.idle_since=None
                    continue
                if self.core.run_tracker and not self.core.run_tracker.can_continue(cid):continue
                if state.idle_since is None:state.idle_since=now
                elif now-state.idle_since>=60:await self._remove(cid)

    async def close(self):
        """Retire every subscription without reconnecting or replaying sends."""
        for cid in list(self.states):await self._remove(cid)
