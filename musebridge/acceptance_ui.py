"""Owner-operated real desktop/voice checks, with fresh files and explicit verdicts."""
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import uuid

from PySide6.QtCore import Qt, QTimer, QProcess, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QPlainTextEdit,QComboBox,QCheckBox)


SETUP_KEY = 'owner_acceptance_setup_v2'


def prompts(setup):
    marker = setup['marker']
    desktop = ('Use the desktop tools on the prepared Notepad document '+setup['input_name']+'. '
        'Enter exactly "MuseBridge desktop acceptance '+marker+'." and use Notepad Save As to save '
        'notepad-acceptance.txt in this project folder. Use desktop.snapshot and desktop.act for '
        'the typing and save; do not use shell/file tools to write it. The owner will foreground '
        'Notepad after sending and leave the PC idle. Wait for that; if recent activity blocks an '
        'action, wait at least 3 seconds and retry once. If another refusal remains, explain it '
        'without bypassing the guard. Read the result independently to verify the exact text.')
    voice = ('Create voice-acceptance.txt in this project containing exactly '
             '"MuseBridge spoken acceptance '+marker+'." Then read it back and tell me the result.')
    typed = 'Voice check follow-up: read voice-acceptance.txt and report its exact contents. Do not change any files.'
    return {'desktop':desktop,'voice':voice,'typed':typed}


def prepare_checks(worker, base_dir=None):
    old = worker.catalog.preference(SETUP_KEY)
    if old:
        try:
            row = worker.catalog.conversation(old['conversation_id'])
            if not row['archived'] and Path(old['root']).is_dir() and row['project_id']==old['project_id']:
                return old
        except (KeyError,ValueError):pass
    base = Path(base_dir) if base_dir else Path(tempfile.gettempdir())/'MuseBridge-owner-checks'
    marker = uuid.uuid4().hex[:8]
    root = base/marker
    root.mkdir(parents=True,exist_ok=False)
    input_name = 'MuseBridge-Notepad-input-'+marker+'.txt'
    (root/input_name).write_text('',encoding='utf-8')
    project = worker.catalog.create_project('Owner checks '+marker,root)
    conversation = worker.catalog.create_conversation('Notepad and voice acceptance',project['id'])
    setup = {'marker':marker,'root':str(root),'project_id':project['id'],
             'conversation_id':conversation['id'],'input_name':input_name}
    worker.router.set_profile(conversation['id'],'desktop')
    worker.catalog.draft(conversation['id'],prompts(setup)['desktop'])
    worker.catalog.preference(SETUP_KEY,setup)
    worker.catalog.preference('atlas_voice_conversation',conversation['id'])
    text = ('# MuseBridge owner checks\n\nThis is a disposable check workspace. '
        'The result files below do not exist until the real checks produce them.\n\n'
        '1. In Owner checks click Load desktop prompt, then Send in the harness. '
        'Click the prepared Notepad window and leave input idle for at least 3 seconds. '
        'Watch the typing and Save As; inspect notepad-acceptance.txt.\n\n'
        '2. Open Voice controls, confirm this test conversation, and Start voice call. '
        'Read this aloud:\n\n'+prompts(setup)['voice']+'\n\n'
        '3. Check mute/unmute, Interrupt spoken reply, Hang up, and send the typed follow-up. '
        'Check saved files, tick only what you actually observed, then Save observations.\n')
    (root/'START-HERE.md').write_text(text,encoding='utf-8')
    return setup


def check_results(setup):
    root = Path(setup['root'])
    result = {}
    for name, sentence in (('notepad-acceptance.txt','MuseBridge desktop acceptance '),
                           ('voice-acceptance.txt','MuseBridge spoken acceptance ')):
        path = root/name
        try:
            valid = (not path.is_symlink() and 0<path.stat().st_size<=65536 and
                     path.read_text(encoding='utf-8-sig').strip()==sentence+setup['marker']+'.')
        except (OSError,UnicodeError):valid = False
        result[name] = valid
    return result


def is_notepad(pid):
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000,False,pid)
    if not handle:return False
    try:
        size = wintypes.DWORD(32768);buffer = ctypes.create_unicode_buffer(size.value)
        return bool(kernel.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(size)) and
                    Path(buffer.value).name.casefold()=='notepad.exe')
    finally:kernel.CloseHandle(handle)


def place_test_notepad(meta, marker):
    """Place only the freshly named test document, without focus or z-order changes."""
    import win32api,win32gui,win32process,win32con
    hwnd = meta['window_handle']
    try:
        if marker not in win32gui.GetWindowText(hwnd):return False
        if win32process.GetWindowThreadProcessId(hwnd)[1]!=meta['process_id']:return False
        if not is_notepad(meta['process_id']):return False
        secondary = next((win32api.GetMonitorInfo(handle) for handle,_,_ in win32api.EnumDisplayMonitors()
                          if not win32api.GetMonitorInfo(handle)['Flags']&win32con.MONITORINFOF_PRIMARY),None)
        if not secondary:return False
        left,top,right,bottom = secondary['Work']
        win32gui.SetWindowPos(hwnd,0,left+35,top+45,min(850,right-left-70),min(650,bottom-top-90),
                             win32con.SWP_NOACTIVATE|win32con.SWP_NOZORDER)
        return True
    except Exception:return False


class OwnerChecks(QDialog):
    def __init__(self, owner):
        super().__init__(None,Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.owner,self.worker = owner,owner.worker
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose,False)
        self.setWindowTitle('MuseBridge · Owner checks')
        self.resize(650,660)
        self.setup = None
        self.closing = False
        self.listing = False
        self.auto_bind = False
        self.polls = 0
        layout = QVBoxLayout(self)
        hint = QLabel('Real Notepad and voice checks. Preparation creates a disposable workspace and drafts a prompt; it does not send messages or open the microphone.')
        hint.setWordWrap(True);layout.addWidget(hint)
        row = QHBoxLayout()
        prepare = QPushButton('Prepare checks and open Notepad');prepare.clicked.connect(self.prepare);row.addWidget(prepare)
        folder = QPushButton('Open check folder');folder.clicked.connect(self.open_folder);row.addWidget(folder);layout.addLayout(row)
        self.status = QLabel('Choose Prepare checks to begin.');self.status.setWordWrap(True);layout.addWidget(self.status)
        row = QHBoxLayout()
        self.windows = QComboBox();self.windows.setAccessibleName('Acceptance Notepad window');row.addWidget(self.windows,1)
        refresh = QPushButton('Refresh Notepad windows');refresh.clicked.connect(self.list_windows);row.addWidget(refresh)
        allow = QPushButton('Use selected window');allow.clicked.connect(self.bind_window);row.addWidget(allow);layout.addLayout(row)
        row = QHBoxLayout()
        for text, action in [('1. Load desktop prompt',lambda:self.load_prompt('desktop',focus=True)),
                             ('Teach skill v2…',self.teach),('2. Voice controls',self.open_voice)]:
            button = QPushButton(text);button.clicked.connect(action);row.addWidget(button)
        layout.addLayout(row)
        self.instructions = QPlainTextEdit();self.instructions.setReadOnly(True)
        self.instructions.setAccessibleName('Owner check instructions and spoken prompt');layout.addWidget(self.instructions,1)
        row = QHBoxLayout()
        follow = QPushButton('3. Load typed follow-up');follow.clicked.connect(lambda:self.load_prompt('typed',focus=True));row.addWidget(follow)
        check = QPushButton('Check saved files');check.clicked.connect(self.check_files);row.addWidget(check);layout.addLayout(row)
        self.result = QLabel('No result has been certified.');self.result.setWordWrap(True);layout.addWidget(self.result)
        self.observations = {}
        for key,label in [('desktop','I watched Notepad type and save the intended file'),
                          ('speech','I spoke the request and heard the response'),
                          ('controls','I checked mute, interruption and hang-up'),
                          ('typed','Typed PC work still worked after hang-up'),
                          ('stop','Recent input / Stop Work behaved as expected')]:
            box = QCheckBox(label);self.observations[key]=box;layout.addWidget(box)
        row = QHBoxLayout()
        record = QPushButton('Save my observations');record.clicked.connect(self.record);row.addWidget(record)
        close = QPushButton('Hide checks');close.clicked.connect(self.hide);row.addWidget(close);layout.addLayout(row)
        self.poll_timer = QTimer(self);self.poll_timer.setInterval(900);self.poll_timer.timeout.connect(self.list_windows)

    def prepare(self):
        self.setup = prepare_checks(self.worker)
        self.owner.open_conversation(self.setup['conversation_id'])
        self.instructions.setPlainText((Path(self.setup['root'])/'START-HERE.md').read_text(encoding='utf-8'))
        self.status.setText('Prepared '+self.worker.catalog.project(self.setup['project_id'])['name']+'. Notepad target discovery is starting.')
        path = str(Path(self.setup['root'])/self.setup['input_name'])
        executable = str(Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32'/'notepad.exe')
        launched, _pid = QProcess.startDetached(executable,[path])
        if not launched:self.status.setText('Workspace is ready; open the prepared Notepad input file, then Refresh Notepad windows.')
        self.auto_bind = bool(launched)
        self.polls = 0
        self.poll_timer.start()
        self.load_prompt('desktop')

    def list_windows(self):
        if not self.setup or self.listing:return
        if self.polls>=20:self.poll_timer.stop();return
        self.polls += 1;self.listing = True
        from .project_ui import background
        def got(result):
            self.listing = False
            if self.closing:return
            if not result['ok']:self.status.setText('Could not list Notepad windows; retry when the desktop is unlocked.');return
            self.windows.clear()
            for meta in result['payload']['windows']:
                if is_notepad(meta['process_id']):self.windows.addItem(meta['title'],meta)
            exact = [i for i in range(self.windows.count()) if self.setup['marker'] in self.windows.itemText(i)]
            if self.auto_bind and len(exact)==1:
                self.windows.setCurrentIndex(exact[0]);self.bind_window()
        def failed(_text):self.listing=False;self.status.setText('Notepad discovery failed; use Refresh to retry.')
        background(self,lambda:self.worker.router.call('desktop.windows',{'conversation_id':self.setup['conversation_id']}),got,failed)

    def bind_window(self):
        meta = self.windows.currentData()
        if not self.setup or not meta:return
        if self.setup['marker'] not in meta.get('title',''):
            self.status.setText('Choose the prepared Notepad input document; other Notepad documents are not acceptance targets.');return
        if not is_notepad(meta['process_id']):self.status.setText('The selected process is no longer Notepad. Refresh the list.');return
        placed = place_test_notepad(meta,self.setup['marker'])
        if not placed:
            self.status.setText('The prepared Notepad window changed or could not be placed on the secondary display. Refresh and select the test document before starting.');return
        self.worker.router.vision.configure(self.setup['conversation_id'],True,browser=False,window=meta)
        self.auto_bind = False;self.poll_timer.stop()
        self.status.setText('Ready: the test project has Desktop control and only its prepared Notepad screenshot grant. '+
            ('Notepad is on the secondary display. ' if placed else 'Place Notepad on the secondary display. ')+
            'Load the desktop prompt, Send in the harness, foreground Notepad, then leave input idle.')

    def load_prompt(self, kind, focus=False):
        if not self.setup:return
        self.owner.open_conversation(self.setup['conversation_id'])
        if self.owner.current_id!=self.setup['conversation_id']:return
        self.owner.input.setPlainText(prompts(self.setup)[kind]);self.owner.save_draft()
        if focus:
            self.hide()
            self.owner.showNormal();self.owner.raise_();self.owner.activateWindow()

    def open_voice(self):
        if not self.setup:return
        self.worker.catalog.preference('atlas_voice_conversation',self.setup['conversation_id'])
        from .voice_ui import show_voice
        dialog = show_voice(self.owner,getattr(self.owner,'companion',None))
        dialog.suggested_prompt.setText('Read aloud after the call connects:\n'+prompts(self.setup)['voice'])
        dialog.suggested_prompt.show()
        if not (self.worker.voice_active or (self.worker.voice_future and not self.worker.voice_future.done())):
            dialog.conversations.setCurrentIndex(dialog.conversations.findData(self.setup['conversation_id']))

    def teach(self):
        if not self.setup:return
        self.owner.open_conversation(self.setup['conversation_id'])
        from .teach_ui import show_teach
        show_teach(self.owner)

    def open_folder(self):
        if self.setup:QDesktopServices.openUrl(QUrl.fromLocalFile(self.setup['root']))

    def check_files(self):
        if not self.setup:return
        results = check_results(self.setup)
        self.result.setText(' · '.join(name+(': exact text verified' if ok else ': missing or does not match') for name,ok in results.items()))
        return results

    def record(self):
        if not self.setup:return
        receipt = {'recorded_at_utc':datetime.now(timezone.utc).isoformat(),'marker':self.setup['marker'],
            'file_checks':self.check_files(),'owner_observations':{key:box.isChecked() for key,box in self.observations.items()}}
        (Path(self.setup['root'])/'owner-observations.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
        self.status.setText('Saved your observations in the check folder. File checks and owner observations remain separate from 1.0 release acceptance.')

    def closeEvent(self,event):
        self.closing=True;self.poll_timer.stop();event.accept()

    def hideEvent(self,event):
        self.poll_timer.stop();super().hideEvent(event)


def show_owner_checks(owner,prepare=False):
    dialog = getattr(owner,'owner_checks',None)
    if dialog is None:dialog = owner.owner_checks = OwnerChecks(owner)
    dialog.closing = False
    if hasattr(owner,'companion'):
        from .companion import clamped_position
        area = owner.companion.available()
        dialog.move(clamped_position(owner.pos(),dialog.size(),area))
    dialog.show();dialog.raise_();dialog.activateWindow()
    if prepare:dialog.prepare()
    return dialog
