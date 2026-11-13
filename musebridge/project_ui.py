"""Explicit local project administration; configuration is never a remote tool."""
import json
from PySide6.QtCore import QObject,Signal,Qt,QThreadPool
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QPlainTextEdit,
    QFormLayout,QLineEdit,QComboBox,QCheckBox,QDialogButtonBox,QTreeWidget,QTreeWidgetItem,QFileDialog)
from .edit_checks import HOOK_EVENT as EDIT_CHECK_EVENT, HOOK_NAME as EDIT_CHECK_HOOK_NAME


class PanelJob(QObject):
    done=Signal(object)
    error=Signal(str)


def background(dialog,fn,done,error):
    job=PanelJob(dialog)
    if not hasattr(dialog,'_jobs'):dialog._jobs=[]
    dialog._jobs.append(job)
    job.done.connect(done,Qt.ConnectionType.QueuedConnection);job.error.connect(error,Qt.ConnectionType.QueuedConnection)
    def run():
        try:result=fn()
        except Exception as exc:
            try:job.error.emit(str(exc))
            except RuntimeError:pass
        else:
            try:job.done.emit(result)
            except RuntimeError:pass
    QThreadPool.globalInstance().start(run)


def form_buttons(dialog,form):
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);form.addRow(buttons)


def continue_new_chat(w):
    if not w.save_draft() or (w.plan_dirty and not w.save_plan(quiet=True)):return
    result=w.worker.router.call('conversation.handoff',{'conversation_id':w.current_id})
    if not result['ok']:w.handle_activity(result['error']['message']);return
    cid=result['payload']['conversation']['id'];w.refresh_conversations(cid);w.switch_conversation();w.tabs.setCurrentIndex(0)
    w.handle_activity('New chat prepared with a project summary. It will be included when you send its first message.')


def notes_dialog(w):
    cid=w.current_id;dialog=QDialog(w);dialog.setWindowTitle('Project continuity');dialog.resize(850,600)
    layout=QVBoxLayout(dialog);layout.addWidget(QLabel('Source-linked facts and corrections; existing Muse memory and skills stay on its VM.'))
    tree=QTreeWidget();tree.setHeaderLabels(['Kind','Fact','Version']);layout.addWidget(tree,1)
    detail=QPlainTextEdit();detail.setReadOnly(True);detail.setMaximumHeight(170);layout.addWidget(detail)
    def reload():
        tree.clear()
        for note in w.worker.router.continuity.list(cid)['notes']:
            item=QTreeWidgetItem([note['kind']+(' (superseded)' if not note['effective'] else ''),note['text'][:110],str(note['version'])])
            item.setData(0,Qt.ItemDataRole.UserRole,note);tree.addTopLevelItem(item)
        tree.setColumnWidth(0,150);tree.setColumnWidth(1,550)
    def selected(*unused):
        item=tree.currentItem();detail.setPlainText(json.dumps(item.data(0,Qt.ItemDataRole.UserRole),indent=2,ensure_ascii=False) if item else '')
    def add(correct=False):
        item=tree.currentItem();old=item.data(0,Qt.ItemDataRole.UserRole) if correct and item else None
        form=QDialog(dialog);form.setWindowTitle('Correct fact' if old else 'Record fact');fields=QFormLayout(form)
        kind=QComboBox();kind.addItems(['decision','constraint','progress']);text=QPlainTextEdit();source=QLineEdit()
        if old:kind.setCurrentText(old['kind']);kind.setEnabled(False);text.setPlainText(old['text']);source.setText(old['source'])
        fields.addRow('Kind',kind);fields.addRow('Fact',text);fields.addRow('Source',source);form_buttons(form,fields)
        if form.exec():
            params={'conversation_id':cid,'kind':kind.currentText(),'text':text.toPlainText(),'source':source.text()}
            if old:params['supersedes']=old['note_id']
            result=w.worker.router.call('project.note',params)
            if result['ok']:reload()
            else:detail.setPlainText(result['error']['message'])
    row=QHBoxLayout();add_button=QPushButton('Record fact');add_button.clicked.connect(lambda:add());row.addWidget(add_button)
    correct=QPushButton('Correct selected');correct.clicked.connect(lambda:add(True));row.addWidget(correct);row.addStretch();layout.addLayout(row)
    tree.currentItemChanged.connect(selected);reload();dialog.exec()


def git_dialog(w):
    cid=w.current_id;dialog=QDialog(w);dialog.setWindowTitle('Git review and isolated workspaces');dialog.resize(950,700)
    layout=QVBoxLayout(dialog);hint=QLabel('Inspect changes or create a fresh worktree. The original checkout is never switched or cleaned.');hint.setWordWrap(True);layout.addWidget(hint)
    output=QPlainTextEdit();output.setReadOnly(True);layout.addWidget(output,1)
    row=QHBoxLayout();buttons=[]
    def show(name,params=None):
        for b in buttons:b.setEnabled(False)
        def done(result):
            output.setPlainText(json.dumps(result,indent=2,ensure_ascii=False))
            for b in buttons:b.setEnabled(True)
        background(dialog,lambda:w.worker.router.call(name,{'conversation_id':cid,**(params or {})}),done,lambda error:done({'error':error}))
    for label,name,params in [('Status','git.status',{}),('Diff','git.diff',{}),('Staged diff','git.diff',{'staged':True}),('Branches','git.branches',{}),('Worktrees','git.worktrees',{})]:
        button=QPushButton(label);button.clicked.connect(lambda checked=False,n=name,p=params:show(n,p));row.addWidget(button);buttons.append(button)
    layout.addLayout(row)
    create_row=QHBoxLayout();branch=QLineEdit();branch.setPlaceholderText('Fresh branch name');base=QLineEdit();base.setPlaceholderText('Base ref (default HEAD)')
    create=QPushButton('Create isolated worktree');create_row.addWidget(branch,1);create_row.addWidget(base,1);create_row.addWidget(create);layout.addLayout(create_row)
    def worktree():
        create.setEnabled(False);params={'conversation_id':cid,'branch':branch.text()}
        if base.text().strip():params['base']=base.text().strip()
        def done(result):
            create.setEnabled(True)
            if not result['ok']:output.setPlainText(result['error']['message']);return
            newcid=result['payload']['conversation']['id'];dialog.accept();w.refresh_conversations(newcid);w.switch_conversation()
            w.handle_activity('Isolated worktree created. Work in this separate project conversation.')
        background(dialog,lambda:w.worker.router.call('git.worktree.create',params),done,lambda error:(create.setEnabled(True),output.setPlainText(error)))
    create.clicked.connect(worktree);show('git.status');dialog.exec()


def hooks_dialog(w):
    cid=w.current_id;project=w.worker.catalog.context(cid)['project']
    if not project:w.handle_activity('Choose a project for lifecycle hooks');return
    scope='project:'+project['id'];dialog=QDialog(w);dialog.setWindowTitle('Local project hooks');dialog.resize(850,560)
    layout=QVBoxLayout(dialog);hint=QLabel('Only hooks explicitly configured here run. Commands use Windows PowerShell and your account permissions.');hint.setWordWrap(True);layout.addWidget(hint)
    tree=QTreeWidget();tree.setHeaderLabels(['Name','Event','Enabled']);layout.addWidget(tree,1)
    output=QPlainTextEdit();output.setReadOnly(True);output.setMaximumHeight(150);layout.addWidget(output)
    def reload():
        tree.clear()
        for hook in generic_hooks(w.worker.router.hooks.list_hooks(scope)):
            item=QTreeWidgetItem([hook['name'],hook['event'],'Yes' if hook['enabled'] else 'No']);item.setData(0,Qt.ItemDataRole.UserRole,hook);tree.addTopLevelItem(item)
        tree.setColumnWidth(0,250);tree.setColumnWidth(1,220)
    def edit(existing=False):
        item=tree.currentItem();old=item.data(0,Qt.ItemDataRole.UserRole) if existing and item else None
        if old and is_edit_check_hook(old):
            output.setPlainText('Automatic edit check is managed by its dedicated project settings dialog.')
            return
        form=QDialog(dialog);form.setWindowTitle('Configure hook');fields=QFormLayout(form)
        name=QLineEdit(old['name'] if old else '');event=QComboBox();event.addItems(['before_tool','after_tool','after_file_edit','before_verification','run_ended'])
        tools=QLineEdit(', '.join(old.get('tool_filter',[])) if old else '');tools.setPlaceholderText('Exact tool names, comma separated; empty = all work tools')
        timeout=QLineEdit(str(old['timeout_s']) if old else '30')
        command=QPlainTextEdit(old['command'] if old else '');enabled=QCheckBox('Enable this hook');enabled.setChecked(bool(old and old['enabled']))
        if old:event.setCurrentText(old['event'])
        if old:name.setEnabled(False);event.setEnabled(False)
        fields.addRow('Name',name);fields.addRow('Event',event);fields.addRow('Tool filter',tools);fields.addRow('Timeout seconds',timeout);fields.addRow('PowerShell command',command);fields.addRow(enabled);form_buttons(form,fields)
        if form.exec():
            if is_reserved_hook_name(name.text(),event.currentText()):
                output.setPlainText('That hook is reserved for Automatic edit check. Configure it in the dedicated project settings dialog.')
                return
            try:w.worker.router.hooks.set_hook(scope,name.text(),event.currentText(),command.toPlainText(),enabled.isChecked(),timeout_s=float(timeout.text()),tool_filter=[t.strip() for t in tools.text().split(',') if t.strip()]);reload()
            except (ValueError,OSError) as exc:output.setPlainText(str(exc))
    row=QHBoxLayout();new=QPushButton('New hook');new.clicked.connect(lambda:edit());row.addWidget(new)
    change=QPushButton('Edit selected');change.clicked.connect(lambda:edit(True));row.addWidget(change)
    results=QPushButton('Invocation results');results.clicked.connect(lambda:output.setPlainText(json.dumps(w.worker.router.hooks.refresh(cid),indent=2)));row.addWidget(results);row.addStretch();layout.addLayout(row)
    def import_plugin():
        from pathlib import Path
        from .hook_plugins import parse_manifest,install_disabled
        path,_=QFileDialog.getOpenFileName(dialog,'Import local hook plugin','','Hook plugin (*.json)')
        if not path:return
        try:
            with Path(path).open('rb') as stream:manifest=parse_manifest(stream.read(65537))
            review=QDialog(dialog);review.setWindowTitle('Review hook plugin — installed disabled');review.resize(780,560);rv=QVBoxLayout(review)
            label=QLabel('Review every command. Import stores disabled hooks; enable individual hooks only when you want them to execute. No Python code or repository plugin is loaded automatically.');label.setWordWrap(True);rv.addWidget(label)
            preview=QPlainTextEdit();preview.setReadOnly(True);preview.setPlainText(json.dumps(manifest,indent=2));rv.addWidget(preview,1)
            install=QPushButton('Install disabled hooks');install.clicked.connect(review.accept);rv.addWidget(install)
            close=QPushButton('Cancel');close.clicked.connect(review.reject);rv.addWidget(close)
            if review.exec():install_disabled(w.worker.router.hooks,scope,manifest);reload();output.setPlainText('Plugin installed disabled. Edit a hook to enable it.')
        except (ValueError,OSError) as exc:output.setPlainText(str(exc))
    importer=QPushButton('Import hook plugin…');importer.clicked.connect(import_plugin);layout.addWidget(importer)
    reload();dialog.exec()


def is_edit_check_hook(hook):
    """Return whether a stored hook is the reserved automatic edit check."""
    return (isinstance(hook, dict)
            and hook.get('name', '').strip().casefold() == EDIT_CHECK_HOOK_NAME.casefold()
            and hook.get('event') == EDIT_CHECK_EVENT)


def is_reserved_hook_name(name, event):
    """Return whether a generic hook form collides with reserved owner settings."""
    return isinstance(name, str) and name.strip().casefold() == EDIT_CHECK_HOOK_NAME.casefold()


def generic_hooks(hooks):
    """Hide the reserved auto-check entry from generic lifecycle-hook editing."""
    return [hook for hook in hooks if not is_edit_check_hook(hook)]


def vision_dialog(w):
    cid=w.current_id;service=w.worker.router.vision
    if not w.worker.catalog.context(cid)['project']:w.handle_activity('Choose a project for automatic visual feedback');return
    state=service.status(cid);dialog=QDialog(w);dialog.setWindowTitle('Automatic visual feedback');dialog.resize(750,550)
    layout=QVBoxLayout(dialog);hint=QLabel('When Muse requests an observation, capture an enabled source and send its image automatically after that turn ends. Continue in the same chat, up to six images per chain. Images cannot grant new permissions. Grants pause on app restart.');hint.setWordWrap(True);layout.addWidget(hint)
    enabled=QCheckBox('Enable automatic visual continuations for this project');enabled.setChecked(state['enabled']);layout.addWidget(enabled)
    browser=QCheckBox('Allow this conversation’s owned headless browser');browser.setChecked(state['browser'] or not state['enabled']);layout.addWidget(browser)
    windows=QComboBox();windows.addItem('No desktop window allowed',None);layout.addWidget(windows)
    if state['window']:windows.addItem(state['window'].get('title','Selected window'),state['window']);windows.setCurrentIndex(1)
    output=QPlainTextEdit();output.setReadOnly(True);output.setPlainText(json.dumps(state,indent=2));layout.addWidget(output,1)
    def list_windows():
        def got(result):
            if not result['ok']:output.setPlainText(result['error']['message']);return
            windows.clear();windows.addItem('No desktop window allowed',None)
            for meta in result['payload']['windows']:windows.addItem(meta['title'],meta)
        background(dialog,lambda:w.worker.router.call('desktop.windows',{'conversation_id':cid}),got,output.setPlainText)
    choose=QPushButton('List windows for an explicit desktop grant');choose.clicked.connect(list_windows);layout.addWidget(choose)
    def apply():
        try:
            service.configure(cid,enabled.isChecked(),browser=browser.isChecked(),window=windows.currentData());output.setPlainText(json.dumps(service.status(cid),indent=2));w.handle_activity('Visual feedback settings saved')
        except (ValueError,OSError) as exc:output.setPlainText(str(exc))
    row=QHBoxLayout();save=QPushButton('Apply / resume selected chat');save.clicked.connect(apply);row.addWidget(save)
    cancel=QPushButton('Cancel pending images');cancel.clicked.connect(lambda:(service.hold(cid,cancel=True),output.setPlainText(json.dumps(service.status(cid),indent=2))));row.addWidget(cancel)
    close=QPushButton('Close');close.clicked.connect(dialog.accept);row.addWidget(close);layout.addLayout(row);dialog.exec()


def mcp_dialog(w):
    cid=w.current_id
    if not w.worker.catalog.context(cid)['project']:w.handle_activity('Choose a project for MCP servers');return
    bridge=w.worker.router.mcp;dialog=QDialog(w);dialog.setWindowTitle('Local MCP integrations');dialog.resize(900,630)
    layout=QVBoxLayout(dialog);hint=QLabel('Configure a local stdio server and explicit tool grants. Remote tools cannot enable servers or grant themselves access.');hint.setWordWrap(True);layout.addWidget(hint)
    tree=QTreeWidget();tree.setHeaderLabels(['Server','Enabled']);layout.addWidget(tree,1)
    output=QPlainTextEdit();output.setReadOnly(True);layout.addWidget(output,1)
    def reload():
        tree.clear()
        for server in bridge.servers(cid):
            item=QTreeWidgetItem([server['name'],'Yes' if server['enabled'] else 'No']);item.setData(0,Qt.ItemDataRole.UserRole,server);tree.addTopLevelItem(item)
    def edit(existing=False):
        item=tree.currentItem();selected=item.data(0,Qt.ItemDataRole.UserRole) if existing and item else None
        old=bridge._config(cid,selected['id'])[0] if selected else None
        form=QDialog(dialog);form.setWindowTitle('Configure stdio server');form.resize(650,480);fields=QFormLayout(form)
        name=QLineEdit(old['name'] if old else '');command=QLineEdit(old['command'] if old else '')
        browse=QPushButton('Choose executable');browse.clicked.connect(lambda:command.setText(QFileDialog.getOpenFileName(form,'Server executable','','Executables (*.exe)')[0] or command.text()))
        args=QPlainTextEdit(old['args_json'] if old else '[]');grants=QPlainTextEdit(old['grants_json'] if old else '{}')
        args.setMaximumHeight(100);grants.setMaximumHeight(100);enabled=QCheckBox('Enable server');enabled.setChecked(bool(old and old['enabled']))
        fields.addRow('Name',name);fields.addRow('Executable',command);fields.addRow(browse);fields.addRow('Arguments (JSON list)',args);fields.addRow('Grants (tool: read/project_write)',grants);fields.addRow(enabled);form_buttons(form,fields)
        if form.exec():
            # Capture Qt field values on the UI thread before background work.
            values=(name.text(),command.text(),args.toPlainText(),grants.toPlainText(),enabled.isChecked())
            background(dialog,lambda:bridge.configure_server(cid,values[0],values[1],json.loads(values[2]),json.loads(values[3]),values[4],selected['id'] if selected else None),lambda _:reload(),output.setPlainText)
    def tools():
        item=tree.currentItem()
        if not item:return
        server=item.data(0,Qt.ItemDataRole.UserRole)
        background(dialog,lambda:bridge.tools(cid,server['id']),lambda data:output.setPlainText(json.dumps(data,indent=2)),output.setPlainText)
    def close_server():
        item=tree.currentItem()
        if item:
            server=item.data(0,Qt.ItemDataRole.UserRole);background(dialog,lambda:bridge.stop_server(server['id']),lambda _:output.setPlainText('Connection closed. Configuration is retained.'),output.setPlainText)
    row=QHBoxLayout()
    for label,callback in [('New server',lambda:edit()),('Edit selected',lambda:edit(True)),('Discover schemas',tools),('Close connection',close_server)]:
        button=QPushButton(label);button.clicked.connect(callback);row.addWidget(button)
    row.addStretch();layout.addLayout(row);reload();dialog.exec()
