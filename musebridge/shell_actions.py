"""Navigation and presentation behavior, kept separate from the Muse runtime."""
import asyncio
import html
import json
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import Qt,QUrl,QTimer,QPoint,QBuffer,QIODevice
from PySide6.QtGui import QDesktopServices,QTextCursor,QPixmap
from PySide6.QtWidgets import (QApplication,QTreeWidgetItem,QMenu,QInputDialog,QFileDialog,
    QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QPlainTextEdit,QTreeWidget,QSplitter)
from .presentation import icon,markdown_fragment


class ShellActions:
    def enqueue_message(self):
        try:
            if self.worker.router.artifacts.drafts(self.current_id):raise ValueError('Send attached documents directly before queueing another request')
            c=self.worker.catalog.conversation(self.current_id)
            if c['title']=='New conversation' and self.input.toPlainText().strip():
                self.worker.catalog.rename_conversation(self.current_id,self.input.toPlainText().strip().splitlines()[0][:70]);self.refresh_conversations();self.load_header()
            self.worker.queue.add(self.current_id,self.input.toPlainText(),self.pending_image)
            self.input.clear();self.remove_attachment();self.save_draft()
            self.handle_activity('Request queued. It will wait for the current turn to settle.')
        except ValueError as exc:self.handle_activity(str(exc))

    def load_attachment(self):
        self.pending_image=self.worker.attachments.draft(self.current_id)
        documents=self.worker.router.artifacts.drafts(self.current_id)
        self.attachment_row.setVisible(self.pending_image is not None or bool(documents))
        labels=([self.pending_image.filename] if self.pending_image else [])+[d['filename'] for d in documents]
        self.attachment_label.setText(' · '.join(labels));self.attachment_label.setToolTip('Preview image or view attached documents')

    def set_attachment(self,image):
        self.worker.attachments.draft(self.current_id,image)
        self.load_attachment();self.handle_activity('Image attached. It will be sent when you send this message.')

    def attach_image(self):
        filename,_=QFileDialog.getOpenFileName(self,'Attach image','','Images (*.png *.jpg *.jpeg *.gif *.webp)')
        if filename:
            from .media import load_image
            try:self.set_attachment(load_image(filename))
            except ValueError as exc:self.handle_activity(str(exc))

    def paste_image(self,image):
        from .media import from_bytes
        try:
            buffer=QBuffer();buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if not image.save(buffer,'PNG'):raise ValueError('Clipboard image could not be encoded')
            self.set_attachment(from_bytes(bytes(buffer.data()),'image/png','clipboard.png'))
        except (ValueError,AttributeError) as exc:self.handle_activity(str(exc))

    def remove_attachment(self):
        self.worker.attachments.draft(self.current_id,clear=True);self.worker.router.artifacts.clear_drafts(self.current_id);self.load_attachment()

    def preview_attachment(self):
        if not self.pending_image:self.tabs.setCurrentIndex(7);return
        self.preview_image(self.pending_image)

    def preview_image(self,image):
        dialog=QDialog(self);dialog.setWindowTitle(image.filename);layout=QVBoxLayout(dialog)
        pixmap=QPixmap();pixmap.loadFromData(image.data)
        label=QLabel();label.setPixmap(pixmap.scaled(850,650,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation));layout.addWidget(label)
        dialog.exec()

    def refresh_navigation(self):
        self.navigation.blockSignals(True);self.navigation.clear();self.nav_items={}
        conversations=self.worker.catalog.conversations()
        main=next(c for c in conversations if c['id']=='main')
        item=QTreeWidgetItem([main['title']]);item.setIcon(0,icon('chat'))
        item.setData(0,Qt.ItemDataRole.UserRole,'main');self.navigation.addTopLevelItem(item);self.nav_items['main']=item
        projects=self.worker.catalog.projects()
        groups={p['id']:p['name'] for p in projects}
        groups[None]='Conversations'
        for pid,name in groups.items():
            rows=[c for c in conversations if c['id']!='main' and c['project_id']==pid]
            if not rows and pid is None:continue
            parent=QTreeWidgetItem([name]);parent.setIcon(0,icon('folder','#6d86c4'))
            parent.setData(0,Qt.ItemDataRole.UserRole+1,pid)
            parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.navigation.addTopLevelItem(parent)
            if pid:
                project=self.worker.catalog.project(pid);parent.setToolTip(0,project['root'])
            for c in rows:
                item=QTreeWidgetItem([c['title']]);item.setData(0,Qt.ItemDataRole.UserRole,c['id'])
                item.setToolTip(0,c['title']);parent.addChild(item);self.nav_items[c['id']]=item
            parent.setExpanded(True)
        if self.current_id in self.nav_items:self.navigation.setCurrentItem(self.nav_items[self.current_id])
        self.navigation.blockSignals(False);self.filter_navigation()

    def navigation_selected(self,item,previous=None):
        if item is None:return
        cid=item.data(0,Qt.ItemDataRole.UserRole)
        if cid:
            index=self.conversations.findData(cid)
            if index>=0:self.conversations.setCurrentIndex(index)

    def filter_navigation(self,*unused):
        query=self.search.text().strip().casefold()
        for i in range(self.navigation.topLevelItemCount()):
            parent=self.navigation.topLevelItem(i)
            if not parent.childCount():
                parent.setHidden(bool(query and query not in parent.text(0).casefold()));continue
            group_match=query in parent.text(0).casefold()
            matches=False
            for j in range(parent.childCount()):
                child=parent.child(j);match=not query or group_match or query in child.text(0).casefold()
                child.setHidden(not match);matches|=match
            parent.setHidden(not matches and bool(query));parent.setExpanded(True)

    def navigation_menu(self,position):
        item=self.navigation.itemAt(position)
        if not item:return
        cid=item.data(0,Qt.ItemDataRole.UserRole)
        if cid:
            self.navigation_selected(item)
            self.conversation_menu(self.navigation.viewport().mapToGlobal(position))

    def toggle_sidebar(self):
        self.sidebar.setVisible(not self.sidebar.isVisible())
        self.worker.catalog.preference('sidebar_hidden',self.sidebar.isHidden())

    def connection_menu(self):
        menu=QMenu(self)
        menu.addAction('Connect',self.worker.connect)
        menu.addAction('Disconnect',self.worker.disconnect)
        menu.addSeparator();menu.addAction('Pair this PC…',self.pair_dialog)
        menu.exec(self.status_pill.mapToGlobal(self.status_pill.rect().topRight()))

    def conversation_menu(self,position=None):
        menu=QMenu(self)
        menu.addAction('New conversation',self.new_conversation)
        from .project_ui import continue_new_chat,notes_dialog,git_dialog,hooks_dialog,mcp_dialog,vision_dialog
        menu.addAction('Continue in new chat…',lambda:continue_new_chat(self))
        menu.addAction('Project continuity…',lambda:notes_dialog(self))
        menu.addAction('Git review / worktrees…',lambda:git_dialog(self))
        menu.addAction('Local lifecycle hooks…',lambda:hooks_dialog(self))
        from .edit_check_settings import show_edit_check_settings
        menu.addAction('Checks after file edits…',lambda:show_edit_check_settings(self))
        from .finish_check_settings import show_finish_check_settings
        menu.addAction('Finish-line verification…',lambda:show_finish_check_settings(self))
        menu.addAction('Automatic visual feedback…',lambda:vision_dialog(self))
        menu.addAction('Local MCP integrations…',lambda:mcp_dialog(self))
        menu.addAction('Queue current message',self.enqueue_message)
        menu.addAction('Artifacts / attach document…',lambda:self.tabs.setCurrentIndex(7))
        menu.addAction('Work across conversations',lambda:self.tabs.setCurrentIndex(8))
        rename=menu.addAction('Rename…',self.rename_conversation);rename.setEnabled(self.current_id!='main')
        menu.addAction('Export conversation…',self.export_conversation)
        menu.addAction('Copy latest reply',self.copy_latest_reply)
        menu.addSeparator();menu.addAction('Project instructions…',self.instructions_dialog)
        menu.addAction('Plan & goal',lambda:self.tabs.setCurrentIndex(2))
        menu.addAction('Jobs',self.jobs_dialog)
        menu.addSeparator();menu.addAction('Continue goal',self.continue_goal)
        menu.addAction('Stop work',self.worker.stop_work);menu.addAction('Resume work',self.worker.resume_work)
        menu.addSeparator();menu.addAction('End voice',self.worker.end_voice)
        mute=menu.addAction('Mute microphone');mute.setCheckable(True)
        mute.setChecked(bool(self.worker.voice and self.worker.voice.audio and self.worker.voice.audio.muted))
        mute.toggled.connect(self.worker.mute_voice)
        menu.addAction('Interrupt spoken reply',self.worker.interrupt_voice)
        menu.addSeparator();archive=menu.addAction('Archive locally',self.archive_conversation);archive.setEnabled(self.current_id!='main')
        archive.setToolTip('Hide this conversation locally. Its data and Muse chat remain available.')
        menu.exec(position if isinstance(position,QPoint) else self.send.mapToGlobal(self.send.rect().topLeft()))

    def rename_conversation(self):
        c=self.worker.catalog.conversation(self.current_id)
        if self.current_id=='main':return
        title,ok=QInputDialog.getText(self,'Rename conversation','Title:',text=c['title'])
        if ok and title.strip():
            try:self.worker.catalog.rename_conversation(self.current_id,title)
            except ValueError as exc:self.handle_activity(str(exc));return
            self.refresh_conversations();self.load_header()

    def archive_conversation(self):
        if self.current_id=='main':return
        if not self.save_draft() or (self.plan_dirty and not self.save_plan(quiet=True)):return
        self.worker.catalog.archive_conversation(self.current_id)
        self.refresh_conversations('main');self.switch_conversation()
        self.handle_activity('Conversation archived locally. Restore it from View → Archived conversations.')

    def restore_conversation(self):
        rows=[c for c in self.worker.catalog.conversations(True) if c['archived']]
        if not rows:self.handle_activity('No archived conversations');return
        labels=[c['title']+' · '+c['id'][:8] for c in rows]
        title,ok=QInputDialog.getItem(self,'Restore conversation','Archived conversation:',labels,0,False)
        if ok:
            c=rows[labels.index(title)];self.worker.catalog.archive_conversation(c['id'],False)
            self.refresh_conversations(c['id']);self.switch_conversation()

    def export_conversation(self):
        c=self.worker.catalog.conversation(self.current_id)
        filename,_=QFileDialog.getSaveFileName(self,'Export conversation','conversation.md','Markdown (*.md)')
        if not filename:return
        text='# '+c['title']+'\n\n'+'\n\n'.join('## '+role+'\n\n'+body for role,body in self.rows.values())
        try:Path(filename).write_text(text,encoding='utf-8');self.handle_activity('Conversation exported')
        except OSError as exc:self.handle_activity('Export failed: '+str(exc))

    def copy_latest_reply(self):
        for role,text in reversed(list(self.rows.values())):
            if role=='Muse':QApplication.clipboard().setText(text);self.handle_activity('Reply copied');return
        self.handle_activity('No Muse reply to copy yet')

    def find_in_chat(self):
        query,ok=QInputDialog.getText(self,'Find in conversation','Find:')
        if ok and query:
            self.tabs.setCurrentIndex(0)
            if not self.chat.find(query):
                self.chat.moveCursor(QTextCursor.MoveOperation.Start)
                if not self.chat.find(query):self.handle_activity('No matching text in this conversation')

    def open_chat_link(self,url):
        if url.scheme() in ('https','http'):QDesktopServices.openUrl(url)
        elif url.scheme()=='muse-image':
            image=self.worker.attachments.message(self.current_id,url.path())
            if image:self.preview_image(image)

    def open_workspace(self):
        menu=self.workspace_menu()
        menu.exec(self.scope_button.mapToGlobal(self.scope_button.rect().bottomLeft()))

    def workspace_menu(self):
        """Build the same-chat workspace chooser for the scope control."""
        menu=QMenu(self)
        project=self.worker.catalog.context(self.current_id)['project']
        if project:
            menu.addAction('Open Folder',lambda:self.open_bound_workspace(project['root']))
            menu.addSeparator()
        menu.addAction('Attach existing workspace…',self.attach_existing_workspace)
        menu.addAction('Create workspace from folder…',self.create_workspace_from_folder)
        return menu

    def open_bound_workspace(self,root):
        QDesktopServices.openUrl(QUrl.fromLocalFile(root))

    def attach_existing_workspace(self):
        projects=self.worker.catalog.projects()
        if not projects:
            self.handle_activity('No local workspaces are saved yet. Choose Create workspace from folder.');return
        labels=[f"{item['name']} — {item['root']}" for item in projects]
        selected,ok=QInputDialog.getItem(self,'Attach workspace to this chat','Workspace:',labels,0,False)
        if ok and selected in labels:
            self._bind_workspace(projects[labels.index(selected)]['id'])

    def create_workspace_from_folder(self):
        root=QFileDialog.getExistingDirectory(self,'Choose workspace folder')
        if not root:return
        path=Path(root).resolve()
        for project in self.worker.catalog.projects():
            if Path(project['root']).resolve()==path:
                self.handle_activity('This folder is already a saved workspace. Choose Attach existing workspace.');return
        name,ok=QInputDialog.getText(self,'Create workspace','Workspace name:',text=path.name)
        if not ok or not name.strip():return
        try:
            project=self.worker.catalog.create_project(name.strip(),path)
        except (ValueError,OSError) as exc:
            self.handle_activity('Workspace could not be created: '+str(exc));return
        self._bind_workspace(project['id'])

    def _bind_workspace(self,project_id):
        if not self.save_draft() or (self.plan_dirty and not self.save_plan(quiet=True)):return
        from .workspace_binding import bind_workspace
        try:
            result=bind_workspace(self.worker.router,self.current_id,project_id)
        except (ValueError,OSError,RuntimeError) as exc:
            self.handle_activity('Workspace was not attached: '+str(exc));return
        self.load_header();self.load_plan();self.refresh_navigation();self.refresh_activity()
        self.handle_activity('Workspace attached to this conversation.' if result['changed'] else 'This conversation already uses that workspace.')

    def toggle_voice(self):
        if self.worker.voice_active:self.worker.end_voice()
        else:self.worker.start_voice()

    def update_status(self,text):
        self.status.setText(text);self.status_pill.setToolTip(text)
        connected=text.startswith('Connected') or text.startswith('New conversation ready')
        waiting=any(word in text.lower() for word in ('connecting','pairing '))
        label='Connected' if connected else 'Connecting…' if waiting else 'Offline'
        color='#4d9a79' if connected else '#c19346' if waiting else '#9aa1ad'
        self.status_pill.setText('●  '+label);self.status_pill.setStyleSheet('text-align:left; color:'+color+'; font-size:12px;')
        if not connected and not waiting and text not in ('Disconnected','Offline'):
            self.handle_activity(text)

    def handle_activity(self,text):
        self.activity.setText(text)
        error=any(term in text.lower() for term in ('failed','unavailable','not confirmed','refused'))
        self.activity.setStyleSheet('color:'+('#b35d58' if error else '#7e8795')+';')
        if text.startswith('Voice active'):
            self.voice_button.setIcon(icon('mic','#5179e8'));self.voice_button.setToolTip('End voice call')
        elif text.startswith('Voice ended') or text.startswith('Voice unavailable'):
            self.voice_button.setIcon(icon('mic'));self.voice_button.setToolTip('Start voice call')
        self.refresh_activity()

    def load_header(self):
        c=self.worker.catalog.conversation(self.current_id);context=self.worker.catalog.context(self.current_id)
        project=context['project'];self.title_label.setText(c['title']);self.title_label.setToolTip(c['title'])
        policy=self.worker.router.execution.policy(self.current_id)
        self.profile.blockSignals(True);self.profile.setCurrentIndex(self.profile.findData(policy['profile']));self.profile.blockSignals(False)
        self.project_label.setText(project['name'] if project else 'Your Muse · main conversation' if self.current_id=='main' else 'Your Muse · separate conversation')
        self.project_label.setToolTip(project['root'] if project else '')
        self.scope_button.setText(project['name'] if project else 'Conversation only')
        self.scope_button.setToolTip(project['root'] if project else 'Choose a workspace to enable file and command tools')
        self.empty_hint.setText('Describe a task in '+project['name']+'. Muse can work with its files and tools.' if project else 'Start a conversation, or choose a workspace to work on a project.')
        self.goal_button.setEnabled(bool(context['goal']));self.goal_button.setToolTip('Continue saved goal' if context['goal'] else 'Set a goal in the Plan tab first')

    def change_profile(self,*unused):
        self.worker.router.set_profile(self.current_id,self.profile.currentData())
        self.handle_activity('Execution profile: '+self.profile.currentText()+'. Applies to conversations in this project.')

    def capabilities_dialog(self):
        from .capabilities import manifest
        dialog=QDialog(self);dialog.setWindowTitle('Windows harness capabilities');dialog.resize(820,650)
        layout=QVBoxLayout(dialog)
        label=QLabel('Muse uses its existing VM skills with these Windows tools.');label.setWordWrap(True);layout.addWidget(label)
        view=QPlainTextEdit();view.setReadOnly(True);view.setPlainText(json.dumps(manifest(self.worker.router,self.current_id),indent=2,ensure_ascii=False));layout.addWidget(view,1)
        close=QPushButton('Close');close.clicked.connect(dialog.accept);layout.addWidget(close)
        dialog.exec()

    def load_plan(self):
        context=self.worker.catalog.context(self.current_id)
        for widget in (self.goal_editor,self.goal_state,self.plan_editor):widget.blockSignals(True)
        self.goal_editor.setText(context['goal']);self.goal_state.setCurrentText(context['goal_state']);self.plan_editor.setPlainText(context['plan'])
        for widget in (self.goal_editor,self.goal_state,self.plan_editor):widget.blockSignals(False)
        self.plan_dirty=False;self.plan_snapshot=(context['goal'],context['goal_state'],context['plan'])

    def mark_plan_dirty(self,*unused):
        self.plan_dirty=True

    def save_plan(self,quiet=False):
        try:
            self.worker.catalog.plan(self.current_id,self.plan_editor.toPlainText(),self.goal_editor.text(),self.goal_state.currentText())
            self.plan_dirty=False;self.load_header()
            if not quiet:self.handle_activity('Plan saved')
            return True
        except ValueError as exc:
            self.handle_activity(str(exc));return False

    def save_and_continue_goal(self):
        if self.save_plan():self.continue_goal()

    def refresh_activity(self):
        if not hasattr(self,'pages'):return
        from .overview_ui import refresh_attention
        refresh_attention(self)
        if self.tabs.currentIndex()==0:return
        if self.tabs.currentIndex()==7:
            from .artifacts_ui import refresh_artifacts
            refresh_artifacts(self);return
        if self.tabs.currentIndex()==8:
            from .overview_ui import refresh_overview
            refresh_overview(self);return
        if self.tabs.currentIndex()==3:
            from .changes_ui import refresh_changes
            refresh_changes(self);return
        if self.tabs.currentIndex()==4:
            from .tasks_ui import refresh_tasks
            refresh_tasks(self);return
        if self.tabs.currentIndex()==5:
            from .queue_ui import refresh_queue
            refresh_queue(self);return
        if self.tabs.currentIndex()==6:
            from .terminal_ui import refresh_terminal
            refresh_terminal(self);return
        if self.tabs.currentIndex()==2:
            context=self.worker.catalog.context(self.current_id)
            snapshot=(context['goal'],context['goal_state'],context['plan'])
            if not self.plan_dirty and snapshot!=self.plan_snapshot:self.load_plan();self.load_header()
            return
        events=self.worker.catalog.rows('SELECT * FROM events WHERE conversation_id=? ORDER BY seq DESC LIMIT 100',(self.current_id,))
        self.activity_tree.clear()
        for event in events:
            data=json.loads(event['data']);kind=event['kind'];title=data.get('tool',kind.replace('.',' '))
            result=data.get('state') or (('Completed' if data.get('ok') else 'Failed') if kind=='tool.result' else data.get('reason') or data.get('profile') or kind.split('.')[-1])
            item=QTreeWidgetItem([datetime.fromtimestamp(event['created']).strftime('%H:%M:%S'),title,str(result)])
            item.setToolTip(0,json.dumps(data,indent=2));self.activity_tree.addTopLevelItem(item)
        self.activity_tree.setColumnWidth(0,95);self.activity_tree.setColumnWidth(1,250)

    def save_ui_state(self):
        self.worker.catalog.preference('last_conversation',self.current_id)
        self.worker.catalog.preference('window_geometry',bytes(self.saveGeometry()).hex())
        self.worker.catalog.preference('splitter_sizes',self.splitter.sizes())

    def open_jobs(self):
        cid=self.current_id
        dialog=QDialog(self);dialog.setWindowTitle('Conversation jobs');dialog.resize(850,590)
        layout=QVBoxLayout(dialog);layout.setContentsMargins(20,18,20,18);layout.setSpacing(12)
        title=QLabel('Jobs');title.setStyleSheet('font-size:20px;font-weight:600;');layout.addWidget(title)
        hint=QLabel('Command output and exit codes for this conversation.');hint.setObjectName('subtitle');layout.addWidget(hint)
        split=QSplitter(Qt.Orientation.Vertical);table=QTreeWidget();table.setHeaderLabels(['Job','State','Exit code'])
        table.setRootIsDecorated(False);table.setObjectName('activity');split.addWidget(table)
        output=QPlainTextEdit();output.setReadOnly(True);output.setPlaceholderText('Select a job to inspect its output.')
        output.setStyleSheet('font-family:Consolas;font-size:12px;border:1px solid #e5e8ee;border-radius:7px;padding:8px;')
        split.addWidget(output);split.setSizes([160,280]);layout.addWidget(split,1)
        row=QHBoxLayout();detail=QLabel('');detail.setObjectName('subtitle');row.addWidget(detail,1)
        copy=QPushButton('Copy output');copy.clicked.connect(lambda:QApplication.clipboard().setText(output.toPlainText()));row.addWidget(copy)
        cancel=QPushButton('Cancel job');row.addWidget(cancel);layout.addLayout(row)
        def show_output(*unused):
            item=table.currentItem()
            if not item:cancel.setEnabled(False);return
            job_id=item.data(0,Qt.ItemDataRole.UserRole)
            first=self.worker.router.jobs.status(job_id)
            pages=[first['output']];cursor=first['cursor']
            for _ in range(15):
                if cursor>=first.get('output_end',cursor):break
                page=self.worker.router.jobs.status(job_id,cursor)
                if page['cursor']<=cursor:break
                pages.append(page['output']);cursor=page['cursor']
            text=('Earlier output was truncated.\n\n' if first['truncated'] else '')+''.join(pages)
            if text!=output.toPlainText():
                bar=output.verticalScrollBar();bottom=bar.maximum()-bar.value()<30;position=bar.value()
                output.setPlainText(text);bar.setValue(bar.maximum() if bottom else min(position,bar.maximum()))
            detail.setText(first['error'] or first['state'].replace('_',' ').capitalize())
            cancel.setEnabled(first['state'] in ('accepted','running'))
        def refresh():
            current=table.currentItem();selected=current.data(0,Qt.ItemDataRole.UserRole) if current else None
            table.blockSignals(True);table.clear()
            links=self.worker.catalog.rows('SELECT job_id FROM job_links WHERE conversation_id=? ORDER BY rowid DESC LIMIT 100',(cid,))
            for entry in links:
                job=self.worker.router.jobs.status(entry['job_id'])
                item=QTreeWidgetItem([job['job_id'][:8],job['state'].replace('_',' ').capitalize(),str(job['exit_code']) if job['exit_code'] is not None else '—'])
                item.setToolTip(0,job['job_id']);item.setData(0,Qt.ItemDataRole.UserRole,job['job_id']);table.addTopLevelItem(item)
                if job['job_id']==selected:table.setCurrentItem(item)
            if not table.currentItem() and table.topLevelItemCount():table.setCurrentItem(table.topLevelItem(0))
            table.blockSignals(False);table.setColumnWidth(0,170);table.setColumnWidth(1,230);show_output()
            if not links:detail.setText('No jobs have run in this conversation yet.')
        def cancel_job():
            item=table.currentItem()
            if not item:return
            cancel.setEnabled(False);job_id=item.data(0,Qt.ItemDataRole.UserRole)
            asyncio.run_coroutine_threadsafe(asyncio.to_thread(self.worker.router.jobs.cancel,job_id),self.worker.loop)
            detail.setText('Cancellation requested…')
        cancel.clicked.connect(cancel_job);table.currentItemChanged.connect(show_output)
        timer=QTimer(dialog);timer.setInterval(1000);timer.timeout.connect(refresh);refresh();timer.start()
        dialog.exec();timer.stop()
