"""Explicitly targeted voice controls reachable from the floating companion."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QPushButton,QCheckBox


class VoiceControls(QDialog):
    def __init__(self, owner):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.owner, self.worker = owner, owner.worker
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setWindowTitle('Atlas · Voice')
        self.resize(410, 265)
        layout = QVBoxLayout(self)
        title = QLabel('Voice conversation')
        title.setStyleSheet('font-size:18px;font-weight:600;')
        layout.addWidget(title)
        hint = QLabel('Choose the dedicated conversation for this call. Main stays separate. Send one text request in that conversation first.')
        hint.setWordWrap(True);layout.addWidget(hint)
        self.conversations = QComboBox()
        self.conversations.setAccessibleName('Voice conversation target')
        layout.addWidget(self.conversations)
        self.status = QLabel('Microphone is off until you start a call.')
        self.status.setWordWrap(True);layout.addWidget(self.status)
        self.suggested_prompt = QLabel()
        self.suggested_prompt.setWordWrap(True)
        self.suggested_prompt.setTextFormat(Qt.TextFormat.PlainText)
        self.suggested_prompt.hide();layout.addWidget(self.suggested_prompt)
        row = QHBoxLayout()
        self.start = QPushButton('Start voice call');self.start.clicked.connect(self.start_call);row.addWidget(self.start)
        self.hangup = QPushButton('Hang up');self.hangup.clicked.connect(self.worker.end_voice);row.addWidget(self.hangup)
        layout.addLayout(row)
        self.mute = QCheckBox('Mute microphone');self.mute.toggled.connect(self.worker.mute_voice);layout.addWidget(self.mute)
        self.interrupt = QPushButton('Interrupt spoken reply');self.interrupt.clicked.connect(self.worker.interrupt_voice);layout.addWidget(self.interrupt)
        row = QHBoxLayout()
        open_chat = QPushButton('Open selected conversation');open_chat.clicked.connect(self.open_selected);row.addWidget(open_chat)
        hide = QPushButton('Hide controls');hide.clicked.connect(self.hide);row.addWidget(hide);layout.addLayout(row)
        self.worker.activity.connect(self.on_activity)
        self.timer = QTimer(self);self.timer.setInterval(250);self.timer.timeout.connect(self.refresh_controls)
        self.reload_targets()
        self.refresh_controls()

    def reload_targets(self):
        busy = self.worker.voice_active or (self.worker.voice_future and not self.worker.voice_future.done())
        previous = (getattr(self.worker.voice,'conversation_id',None) if busy else None) or self.conversations.currentData() or self.worker.catalog.preference('atlas_voice_conversation', default=self.owner.current_id)
        self.conversations.clear()
        for row in self.worker.catalog.conversations():
            if row['id']=='main':continue
            project = self.worker.catalog.context(row['id'])['project']
            self.conversations.addItem((project['name']+' · ' if project else '')+row['title'],row['id'])
        index = self.conversations.findData(previous)
        if index>=0:self.conversations.setCurrentIndex(index)

    def start_call(self):
        cid = self.conversations.currentData()
        if not cid:return
        self.worker.catalog.preference('atlas_voice_conversation',cid)
        if self.worker.start_voice(conversation_id=cid):self.status.setText('Starting the selected call… microphone waits for the voice transport.')
        self.refresh_controls()

    def refresh_controls(self):
        busy = bool(self.worker.voice_active or (self.worker.voice_future and not self.worker.voice_future.done()))
        voice = self.worker.voice
        self.conversations.setEnabled(not busy)
        self.start.setEnabled(not busy and self.conversations.currentData() is not None)
        self.hangup.setEnabled(busy)
        self.mute.setEnabled(bool(voice and voice.audio))
        self.mute.blockSignals(True)
        self.mute.setChecked(bool(voice and voice.audio and voice.audio.muted))
        self.mute.blockSignals(False)
        self.interrupt.setEnabled(bool(voice and voice.call))

    def on_activity(self,text):
        if text.startswith(('Voice active','Voice ended','Voice unavailable')):
            self.status.setText('Voice active in the selected conversation.' if text.startswith('Voice active') else text)
            self.refresh_controls()

    def open_selected(self):
        cid = self.conversations.currentData()
        if cid:
            self.owner.open_conversation(cid)
            self.owner.showNormal();self.owner.raise_();self.owner.activateWindow()

    def showEvent(self,event):
        self.timer.start();super().showEvent(event)

    def hideEvent(self,event):
        self.timer.stop();super().hideEvent(event)

    def shutdown(self):
        self.timer.stop();self.hide()


def show_voice(owner, companion=None):
    controls = getattr(owner, 'voice_controls', None)
    if controls is None:
        controls = owner.voice_controls = VoiceControls(owner)
    elif not (owner.worker.voice_active or (owner.worker.voice_future and not owner.worker.voice_future.done())):
        controls.reload_targets()
    if companion:
        controls.move(companion.pos())
        area = companion.available()
        from .companion import clamped_position
        controls.move(clamped_position(controls.pos(),controls.size(),area))
    controls.show();controls.raise_();controls.activateWindow()
    return controls
