import asyncio
import html
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict

from PySide6.QtCore import QObject, Signal, QTimer, QLockFile, Qt
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QTextBrowser, QPlainTextEdit, QComboBox,
    QLineEdit, QFormLayout, QDialog, QDialogButtonBox, QFileDialog, QInputDialog, QMessageBox)
from serial.tools.list_ports import comports

from .core import Core
from .pairing import pair
from .storage import Store
from .catalog import Catalog


class Worker(QObject):
    status = Signal(str)
    message = Signal(str, str, str)
    activity = Signal(str)
    conversation_message = Signal(str,str,str,str)
    send_state = Signal(str,bool)
    manual_ack = Signal(str,str)
    send_error = Signal(str,str)
    def __init__(self,store=None):
        super().__init__()
        self.store = store or Store()
        self.catalog = Catalog(self.store.directory)
        from .attachments import Attachments
        self.attachments=Attachments(self.catalog)
        from .tooling import ToolRouter
        self.router = ToolRouter(self.catalog,self.store.directory,self.activity.emit)
        self.conversation_id = 'main'
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.core = None
        self.future = None
        self.pair_stop = threading.Event()
        self.pair_thread = None
        self.voice = None
        self.voice_future = None
        self.voice_active = False
        self.goal_future = None
        self.pending_sends=set()
        self.terminal_executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='MuseTerminalInput')
        from .goals import GoalRunner
        self.goal=GoalRunner(self.catalog,self.goal_send,
            lambda cid,text:self.activity.emit(text) if cid == self.conversation_id else None)
        from .request_queue import RequestQueue
        self.queue=RequestQueue(self.catalog)
        self.queue_future=asyncio.run_coroutine_threadsafe(self.drive_queue(),self.loop)

    async def drive_queue(self):
        """Poll live chats independently; a slow send cannot hold another chat's queue."""
        tasks={}
        try:
            while True:
                await asyncio.sleep(.4)
                core=self.core
                cids=set(core.live_conversations()) if core and hasattr(core,'live_conversations') else {self.conversation_id}
                cids.update(self.queue.enabled)
                if core and getattr(core,'parallel_enabled',False):await core.live_chats.reap()
                for cid in cids:
                    previous=tasks.get(cid)
                    if previous and not previous.done():continue
                    if previous and not previous.cancelled():
                        error=previous.exception()
                        if error:self.activity.emit('Chat continuation held ('+type(error).__name__+').')
                    tasks[cid]=asyncio.create_task(Worker.drive_conversation(self,cid))
        finally:
            for task in tasks.values():task.cancel()
            await asyncio.gather(*tasks.values(),return_exceptions=True)

    async def drive_conversation(self,cid):
        """One bounded continuation attempt with readiness and ownership captured by chat."""
        from .request_queue import send_next
        core=self.core
        if not core:return
        if self.catalog.conversation(cid)['archived']:
            self.queue.hold(cid)
            self.router.vision.hold(cid)
            return
        ready=(core.chat_ready(cid) if hasattr(core,'chat_ready') else
               core.conversation_id==cid and (not core._remote_exists or bool(core.session and core.session.chat_subscribed.is_set())))
        goal=getattr(self,'goal',None)
        goal_busy=(self.goal_future and not self.goal_future.done() and getattr(goal,'_active_cid',None) in (None,cid))
        if core and core.run_tracker:core.run_tracker.poll(cid)
        if (core and ready and core.run_tracker
                and not self.router.execution.active_run(cid)
                and core.run_tracker.can_continue(cid)
                and not goal_busy):
            self.queue_finish_check(cid)
        if core and ready and not self.router.paused and not self.voice_active and cid not in self.pending_sends and not goal_busy:
            if core.session and core.session.registered.is_set() and (not core.run_tracker or core.run_tracker.can_continue(cid)):
                try:
                    if await self.send_visual_feedback(cid):return
                except Exception as exc:
                    self.router.vision.hold(cid);self.activity.emit('Visual feedback held ('+type(exc).__name__+'); inspect Visual feedback settings before resuming.')
        if cid not in self.queue.enabled or not ready:return
        if not self.queue.has_pending(cid):return
        if self.router.paused or self.voice_active or cid in self.pending_sends:return
        if goal_busy:return
        if not core.session or not core.session.registered.is_set():return
        if self.router.execution.active_run(cid):return
        if core.run_tracker and not core.run_tracker.can_continue(cid):return
        self.pending_sends.add(cid);self.send_state.emit(cid,True)
        async def send(queued_cid,text,image,continuation_of=None):
            stamp=self.router.execution.generation
            ack=await core.send(text,conversation_id=queued_cid,image=image,clear_image_draft=False,
                continuation_of=continuation_of,finish_check=bool(continuation_of),
                send_guard=lambda:self.queue_send_guard(queued_cid,stamp))
            if not ack.get('_bridge_tracking_ok',True):self.queue.hold(queued_cid)
            return ack
        try:
            sent=await send_next(self.queue,cid,send,validate=self.router.finish_line.delivery_allowed)
            if sent:self.activity.emit('Queued request acknowledged' if cid in self.queue.enabled else 'Queued request acknowledged; queue held for inspection.')
        except asyncio.CancelledError:raise
        except Exception as exc:
            self.queue.hold(cid);self.activity.emit('Queue held: send outcome uncertain ('+type(exc).__name__+'). Inspect before resuming.')
        finally:self.pending_sends.discard(cid);self.send_state.emit(cid,False)

    def queue_finish_check(self,cid):
        """Inspect only the latest completed request, after the turn tracker settles."""
        rows=self.catalog.rows("SELECT id FROM runs WHERE conversation_id=? AND origin!='tool' ORDER BY created DESC LIMIT 1",(cid,))
        if rows:
            try:self.router.finish_line.queue_after_run(cid,rows[0]['id'],self.queue)
            except Exception as exc:
                self.queue.hold(cid)
                self.activity.emit('Finish-line check held ('+type(exc).__name__+'); inspect the queue before resuming.')

    def queue_send_guard(self,cid,generation):
        """Recheck queue holds after asynchronous header preparation, before network send."""
        if (cid not in self.queue.enabled or cid in self.queue.held or self.router.paused
                or self.voice_active or self.router.execution.generation!=generation
                or self.router.catalog.conversation(cid)['archived']):
            raise ValueError('Queued request held or revoked before sending')

    def connect(self):
        if self.pair_thread and self.pair_thread.is_alive():
            self.status.emit('Finish or cancel pairing first')
            return
        if self.future and not self.future.done():
            return
        async def start():
            self.core = Core(self.store, self.status.emit, self.message.emit, self.activity.emit,
                catalog=self.catalog,router=self.router,conversation_id=self.conversation_id,
                conversation_message=self.conversation_message.emit,event_observer=self.observe_event)
            await self.core.run()
        self.future = asyncio.run_coroutine_threadsafe(start(), self.loop)

    def observe_event(self,cid,event):
        """A lost chat stream holds only its automation and wakes its goal waiter."""
        result=self.goal.feed(cid,event)
        if event.get('type')=='disconnect' or event.get('event')=='connection.closed':
            self.queue.hold(cid)
            self.router.vision.hold(cid)
        return result

    def submit_terminal(self,name,params):
        # Submitted on the Qt thread: the one owned executor preserves key order
        # while allowing Stop Work to remain responsive during a hook/check.
        captured=dict(params);stamp=self.router.execution.generation
        captured.setdefault('scope_revision',self.router.execution.policy(captured['conversation_id'])['revision'])
        return self.terminal_executor.submit(self.router.call,name,captured,admission_generation=stamp)

    def disconnect(self):
        self.queue.hold()
        self.router.vision.hold()
        self.end_voice()
        if self.goal_future and not self.goal_future.done():
            asyncio.run_coroutine_threadsafe(self.goal.stop(),self.loop)
        self.pair_stop.set()
        if self.core:
            self.loop.call_soon_threadsafe(self.core.stop.set)

    def send(self, text, image=None, *, artifact_ids=None,preserve_composer=False,conversation_id=None):
        if not self.core:
            self.status.emit('Connect to Muse first; message was not sent')
            return False
        cid=conversation_id or self.conversation_id
        artifact_ids=(tuple(row['id'] for row in self.router.artifacts.drafts(cid))
                      if artifact_ids is None else tuple(artifact_ids))
        if cid in self.pending_sends:
            self.activity.emit('A message is awaiting acknowledgment in this conversation');return False
        self.router.vision.hold(cid,cancel=True)
        self.pending_sends.add(cid)
        self.send_state.emit(cid,True)
        core=self.core
        async def manual_send():
            if getattr(self.goal,'_active_cid',None) in (None,cid):await self.goal.stop()
            return await core.send(text,conversation_id=cid,image=image,artifact_ids=artifact_ids)
        future = asyncio.run_coroutine_threadsafe(manual_send(), self.loop)
        def complete(f):
            self.pending_sends.discard(cid)
            self.send_state.emit(cid,False)
            if not f.cancelled() and f.exception():
                warning=('Send not confirmed (' + type(f.exception()).__name__ +
                         '). It may have reached Muse; it was not resent.')
                self.activity.emit(warning)
                if hasattr(self,'send_error'):self.send_error.emit(cid,warning)
            elif not f.cancelled() and not preserve_composer:self.manual_ack.emit(cid,text)
        future.add_done_callback(complete)
        return True

    def select(self,identifier):
        # Voice captures its own conversation; changing the viewed chat is presentation.
        self.conversation_id = identifier
        if self.core:
            future = asyncio.run_coroutine_threadsafe(self.core.select_conversation(identifier),self.loop)
            future.add_done_callback(lambda f:self.activity.emit('Conversation switch failed: '+type(f.exception()).__name__)
                if not f.cancelled() and f.exception() else None)

    def stop_work(self):
        self.queue.hold()
        self.router.vision.hold(cancel=True)
        self.end_voice()
        asyncio.run_coroutine_threadsafe(self.goal.stop(),self.loop)
        self.router.paused=True
        asyncio.run_coroutine_threadsafe(asyncio.to_thread(self.router.stop_all),self.loop)
        for conversation in self.catalog.conversations(include_archived=True):
            if conversation['goal_state'] == 'active':
                self.catalog.plan(conversation['id'],state='paused')
        self.activity.emit('Work paused; local jobs cancelled. Muse may still be responding remotely.')

    def resume_work(self):
        self.router.resume()
        self.activity.emit('Local work enabled')

    def start_voice(self,conversation_id=None):
        if not self.core:
            self.activity.emit('Voice unavailable · connect before starting voice.');return False
        if self.voice_active or (self.voice_future and not self.voice_future.done()):return False
        from .voice import VoiceController,VoicePreflightError
        cid=conversation_id or self.conversation_id
        self.voice=VoiceController(self.core,self.activity.emit,conversation_id=cid)
        async def call():
            self.voice_active=True
            try:
                if getattr(self.goal,'_active_cid',None) in (None,cid):await self.goal.stop()
                return await self.voice.run()
            finally:self.voice_active=False
        self.voice_future=asyncio.run_coroutine_threadsafe(call(),self.loop)
        def ended(f):
            if not f.cancelled() and f.exception():
                error=f.exception()
                detail=str(error) if isinstance(error,VoicePreflightError) else type(error).__name__
                self.activity.emit('Voice unavailable · '+detail+'. Use the selected dedicated conversation and check PC audio devices.')
        self.voice_future.add_done_callback(ended)
        return True

    def end_voice(self):
        if self.voice_future and not self.voice_future.done():self.voice_future.cancel()

    def mute_voice(self,muted):
        if self.voice:self.loop.call_soon_threadsafe(self.voice.mute,muted)

    def interrupt_voice(self):
        if self.voice:asyncio.run_coroutine_threadsafe(self.voice.interrupt(),self.loop)

    async def goal_send(self,cid,prompt):
        if self.router.paused or not self.core:
            raise ConnectionError('Goal conversation is no longer active')
        deadline=self.loop.time()+8
        while self.router.vision.pending(cid) and self.core.run_tracker and not self.core.run_tracker.can_continue(cid):
            if self.loop.time()>deadline:raise RuntimeError('Visual continuation did not settle; goal paused for inspection')
            await asyncio.sleep(.1)
        visual=await self.send_visual_feedback(cid,return_ack=True,goal_round=True)
        if visual:
            self.goal_last_run=visual.get('_bridge_run_id')
            return visual
        from .request_queue import send_next
        first=self.goal.round_number==1
        source=None if first else getattr(self,'goal_last_run',None)
        if source:self.queue_finish_check(cid)
        sent_ack=None
        async def send_check(queued_cid,text,image,continuation_of=None):
            nonlocal sent_ack
            stamp=self.router.execution.generation
            sent_ack=await self.core.send(text,conversation_id=queued_cid,
                continuation_of=continuation_of,goal_round=True,finish_check=True,
                send_guard=lambda:self.queue_send_guard(queued_cid,stamp))
            if not sent_ack.get('_bridge_tracking_ok',True):self.queue.hold(queued_cid)
            return sent_ack
        if source:
            await send_next(self.queue,cid,send_check,automatic_only=True,
                            validate=self.router.finish_line.delivery_allowed)
        ack=sent_ack or await self.core.send(prompt,conversation_id=cid,continuation_of=source,goal_round=True)
        self.goal_last_run=ack.get('_bridge_run_id')
        return ack

    async def send_visual_feedback(self,cid,return_ack=False,goal_round=False):
        core=self.core
        if not core:return False
        if core.run_tracker and not core.run_tracker.can_continue(cid):return False
        row=self.router.vision.claim(cid)
        if row is None:return False
        self.pending_sends.add(cid);self.send_state.emit(cid,True)
        try:
            image=self.router.vision.image(cid,row)
            ack=await core.send('Automatic visual feedback for the screenshot you requested. Inspect the attached image, continue the original user task, and verify the result. Use vision.observe again only if another observation is needed; the chain is bounded to six images.',
                conversation_id=cid,image=image,clear_image_draft=False,visual_feedback=row,goal_round=goal_round)
            self.router.vision.settle(cid,row,'acknowledged',ack.get('_bridge_run_id'))
            if not ack.get('_bridge_tracking_ok',True):self.router.vision.hold(cid)
            self.activity.emit('Automatic visual feedback acknowledged')
            return ack if return_ack else True
        except (Exception,asyncio.CancelledError):
            self.router.vision.settle(cid,row,'unknown');self.router.vision.hold(cid);raise
        finally:self.pending_sends.discard(cid);self.send_state.emit(cid,False)

    def run_goal(self):
        if self.goal_future and not self.goal_future.done():return
        self.goal_future=asyncio.run_coroutine_threadsafe(self.goal.run(self.conversation_id,max_rounds=5),self.loop)

    def begin_pair(self, port, token):
        if self.future and not self.future.done():
            self.status.emit('Disconnect before pairing')
            return
        if self.pair_thread and self.pair_thread.is_alive():
            return
        self.pair_stop = threading.Event()
        def run():
            try:
                pair(self.store, port, token, self.status.emit, self.pair_stop)
            except Exception as exc:
                self.status.emit('Pairing stopped (' + type(exc).__name__ +
                                 '). Check cable, port and SDK token; then retry.')
        self.pair_thread = threading.Thread(target=run, daemon=True)
        self.pair_thread.start()

    def close(self):
        self.router.paused=True
        self.terminal_executor.shutdown(wait=True,cancel_futures=True)
        self.end_voice()
        self.disconnect()
        if self.future:
            try:
                self.future.result(timeout=5)
            except Exception:
                self.future.cancel()
        if self.pair_thread:
            self.pair_thread.join(timeout=3)
        async def drain():
            await self.goal.stop()
            tasks=[t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for task in tasks:task.cancel()
            if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        try:asyncio.run_coroutine_threadsafe(drain(),self.loop).result(timeout=12)
        except Exception:pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)
        self.router.close()
        self.catalog.close()


from .shell_actions import ShellActions
from .chat_view import ChatPresentation


class Window(ChatPresentation,ShellActions,QMainWindow):
    def __init__(self,worker=None):
        super().__init__()
        self.setWindowTitle('MuseBridge')
        self.worker=worker or Worker()
        self.current_id=self.worker.catalog.preference('last_conversation',default='main')
        self.current_id=self.side_conversation(self.current_id)
        self.worker.conversation_id=self.current_id
        self.rows=OrderedDict();self.plan_dirty=False;self.plan_snapshot=None
        self._closed=False
        from .shell import build_shell
        build_shell(self)
        self.worker.status.connect(self.update_status)
        self.worker.activity.connect(self.handle_activity)
        self.worker.message.connect(self.show_message)
        self.worker.conversation_message.connect(self.on_conversation_message)
        self.worker.send_state.connect(lambda cid,pending:self.send.setEnabled(not pending) if cid==self.current_id else None)
        self.worker.manual_ack.connect(self.confirm_manual_draft)
        self.input.textChanged.connect(lambda:self.draft_timer.start())
        self.goal_editor.textChanged.connect(self.mark_plan_dirty)
        self.goal_state.currentTextChanged.connect(self.mark_plan_dirty)
        self.plan_editor.textChanged.connect(self.mark_plan_dirty)
        self.refresh_conversations()
        self.conversations.currentIndexChanged.connect(self.switch_conversation)
        self.load_conversation();self.update_status('Offline')
        geometry=self.worker.catalog.preference('window_geometry')
        if geometry:
            try:self.restoreGeometry(bytes.fromhex(geometry))
            except ValueError:pass
        sizes=self.worker.catalog.preference('splitter_sizes')
        if sizes:self.splitter.setSizes(sizes)
        self.sidebar.setVisible(not self.worker.catalog.preference('sidebar_hidden',default=False))
        from .companion import AtlasCompanion
        self.companion=AtlasCompanion(self)

    def side_conversation(self,preferred=None):
        rows=[c for c in self.worker.catalog.conversations() if c['id']!='main']
        if preferred and any(c['id']==preferred for c in rows):return preferred
        return rows[0]['id'] if rows else self.worker.catalog.create_conversation('New conversation')['id']

    def open_conversation(self,cid):
        if cid=='main':
            self.companion.open_chat();return
        self.refresh_conversations(cid);self.switch_conversation();self.tabs.setCurrentIndex(0)

    def show_companion(self):
        if hasattr(self,'companion'):self.companion.present(explicit=True)

    def showEvent(self,event):
        super().showEvent(event)
        if hasattr(self,'companion'):self.companion.present()

    def refresh_conversations(self,selected=None):
        if selected=='main':
            if hasattr(self,'companion'):self.companion.open_chat()
            selected=self.current_id
        self.conversations.blockSignals(True)
        self.conversations.clear()
        for c in self.worker.catalog.conversations():
            if c['id']=='main':continue
            self.conversations.addItem(c['title'],c['id'])
        index=self.conversations.findData(selected or self.current_id)
        self.conversations.setCurrentIndex(max(0,index))
        self.conversations.blockSignals(False)
        self.refresh_navigation()

    def save_draft(self):
        self.draft_timer.stop()
        try:
            self.worker.catalog.draft(self.current_id,self.input.toPlainText())
            return True
        except ValueError:
            self.activity.setText('Draft exceeds 64 KiB; shorten it before sending')
            return False

    def switch_conversation(self,*unused):
        identifier=self.conversations.currentData()
        if identifier and identifier != self.current_id:
            if not self.save_draft() or (self.plan_dirty and not self.save_plan(quiet=True)):
                self.refresh_conversations(self.current_id);return
            self.current_id=identifier
            self.worker.select(identifier)
            self.worker.catalog.preference('last_conversation',identifier)
            self.load_conversation()
            self.refresh_navigation()

    def load_conversation(self):
        self.rows=OrderedDict((m['id'],(m['role'],m['text'])) for m in self.worker.catalog.messages(self.current_id))
        self.render_messages(force_scroll=True)
        self.input.blockSignals(True)
        self.input.setPlainText(self.worker.catalog.draft(self.current_id))
        self.input.blockSignals(False)
        self.send.setEnabled(self.current_id not in self.worker.pending_sends)
        self.load_attachment()
        self.load_header();self.load_plan();self.refresh_activity()

    def on_conversation_message(self,cid,identifier,role,text):
        if cid == self.current_id:
            self.show_message(identifier,role,text)

    def new_project(self):
        root=QFileDialog.getExistingDirectory(self,'Choose project folder')
        if not root:return
        from pathlib import Path
        name,ok=QInputDialog.getText(self,'New workspace','Workspace name:',text=Path(root).name)
        if not ok or not name.strip():return
        try:
            project=self.worker.catalog.create_project(name,root)
            conversation=self.worker.catalog.create_conversation(name,project['id'])
            self.refresh_conversations(conversation['id']);self.switch_conversation()
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,'Project unavailable',str(exc))

    def new_conversation(self):
        project_id=self.worker.catalog.conversation(self.current_id)['project_id']
        conversation=self.worker.catalog.create_conversation('New conversation',project_id)
        self.refresh_conversations(conversation['id']);self.switch_conversation()
        self.tabs.setCurrentIndex(0);self.input.setFocus()

    def instructions_dialog(self):
        project=self.worker.catalog.context(self.current_id)['project']
        if not project:
            self.activity.setText('Select a project conversation first');return
        text,ok=QInputDialog.getMultiLineText(self,'Project instructions',
            'Instructions shared with Muse when sending a project message:',project['instructions'])
        if ok:
            try:self.worker.catalog.update_project(project['id'],text)
            except ValueError as exc:QMessageBox.warning(self,'Instructions',str(exc))

    def plan_dialog(self):
        self.tabs.setCurrentIndex(2)

    def continue_goal(self):
        if self.plan_dirty and not self.save_plan(quiet=True):return
        context=self.worker.catalog.context(self.current_id)
        if not context['goal']:
            self.activity.setText('Set a goal with Plan / goal first');return
        if self.worker.router.paused:
            self.activity.setText('Resume work before continuing the goal');return
        self.worker.catalog.plan(self.current_id,state='active')
        self.worker.run_goal()

    def jobs_dialog(self):
        self.open_jobs()

    def submit(self):
        text = self.input.toPlainText()
        if text.strip() or self.pending_image is not None or self.worker.router.artifacts.drafts(self.current_id):
            if self.current_id in self.worker.pending_sends:return
            c=self.worker.catalog.conversation(self.current_id)
            if c['id']!='main' and c['title']=='New conversation':
                title=text.strip().splitlines()[0][:70] if text.strip() else self.pending_image.filename if self.pending_image else 'Attached documents'
                self.worker.catalog.rename_conversation(self.current_id,title)
                self.refresh_conversations();self.load_header()
            self.worker.send(text,self.pending_image)
            # Retain the draft until a confirmed user row arrives.

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'chat'):self.fit_transcript()

    def pair_dialog(self):
        dialog = QDialog(self); dialog.setWindowTitle('Pair this PC with Muse')
        form = QFormLayout(dialog)
        hint = QLabel('Connect the flashed ESP32 through its USB-UART socket.\n'
                      'In the Muse phone app: Devices → Developer mode → Add Device.\n'
                      'Pairing follows the SDK’s phone-confirmation policy.')
        form.addRow(hint)
        ports = QComboBox()
        for port in comports():
            ports.addItem(port.device + ' — ' + port.description, port.device)
        form.addRow('Board port', ports)
        token = QLineEdit(); token.setEchoMode(QLineEdit.EchoMode.Password)
        token.setPlaceholderText('mgst_… from gadgets.muse.ai'); form.addRow('SDK token', token)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); form.addRow(buttons)
        if dialog.exec() and ports.currentData():
            self.worker.begin_pair(ports.currentData(), token.text().strip())
        token.clear()

    def closeEvent(self, event):
        if self._closed:event.accept();return
        if not self.save_draft() or (self.plan_dirty and not self.save_plan(quiet=True)):
            event.ignore();return
        if hasattr(self,'companion') and not self.companion.panel.save_draft():
            self.companion.open_chat();event.ignore();return
        self.draft_timer.stop();self.activity_timer.stop()
        self.terminal_resize_timer.stop()
        self.desktop_notifier.close()
        if hasattr(self,'companion'):self.companion.shutdown()
        if hasattr(self,'voice_controls'):self.voice_controls.shutdown()
        if hasattr(self,'owner_checks'):self.owner_checks.close()
        self.save_ui_state()
        self.worker.close()
        self._closed=True
        event.accept()


def main():
    app = QApplication(sys.argv)
    from pathlib import Path
    from PySide6.QtGui import QIcon
    icon = Path(__file__).parent / 'assets' / 'musebridge.ico'
    if icon.is_file():app.setWindowIcon(QIcon(str(icon)))
    directory = Store().directory
    lock = QLockFile(str(directory / 'application.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        if '--activate-existing' in sys.argv:
            from .activation import request_activation
            if request_activation(directory):return 0
        return 2  # Never register the same device identity twice.
    app.setStyle('Fusion')
    window = Window()
    if '--secondary-screen' in sys.argv:
        from .display import place_on_secondary
        window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        try:
            place_on_secondary(window, app)
        except RuntimeError:
            window.worker.close()
            return 3
    def show_existing():
        if '--secondary-screen' in sys.argv:
            from .display import place_on_secondary
            try:place_on_secondary(window,app)
            except RuntimeError:return
        window.showNormal()
        window.raise_()
        window.activateWindow()
    from .activation import install_activation
    try:
        window.activation_server = install_activation(window,directory,show_existing)
    except (RuntimeError,OSError):
        window.worker.close()
        return 4
    window.show()
    if '--owner-checks' in sys.argv:
        from .acceptance_ui import show_owner_checks
        QTimer.singleShot(700,lambda:show_owner_checks(window,prepare=True))
    if '--connect' in sys.argv:
        QTimer.singleShot(0,window.worker.connect)
    if '--smoke' in sys.argv:
        QTimer.singleShot(400, window.close)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
