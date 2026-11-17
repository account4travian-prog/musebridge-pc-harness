"""Main's independent presentation; never changes the harness's selected chat."""
from collections import OrderedDict

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTextBrowser, QWidget, QComboBox, QFileDialog)

from .chat_view import ChatPresentation
from .presentation import Composer, STYLE
from .shell_actions import ShellActions


def connection_label(text):
    """Connection updates are global; selected-chat titles and send errors are not."""
    if text.startswith(('Connected', 'New conversation ready')):return 'Connected'
    if text.startswith('Connecting'):return 'Connecting…'
    if text.startswith(('Disconnected', 'Offline')):return 'Offline'
    if text.startswith('Connection unavailable'):return 'Connection unavailable · retrying'
    if text.startswith(('Not paired', 'Saved pairing is invalid')):return 'Pairing unavailable'
    return None


class MainChatPanel(ChatPresentation, ShellActions, QDialog):
    stateChanged = Signal(str, str)
    attachmentsChanged = Signal()
    current_id = 'main'

    def __init__(self, owner):
        # No native owner: minimizing the harness must not minimize this panel.
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.owner, self.worker = owner, owner.worker
        self.shutting_down = False
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setWindowTitle('Atlas · Main')
        self.setStyleSheet(STYLE)
        self.resize(440, 560)
        self.setMinimumSize(360, 420)
        self.rows = OrderedDict((m['id'], (m['role'], m['text']))
                                for m in self.worker.catalog.messages('main'))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        header = QHBoxLayout()
        title = QLabel('Atlas · Main')
        title.setStyleSheet('font-size:18px;font-weight:600;')
        header.addWidget(title, 1)
        expand = QPushButton('Expand')
        expand.clicked.connect(self.toggle_expanded)
        header.addWidget(expand)
        hide = QPushButton('Hide')
        hide.clicked.connect(self.close)
        header.addWidget(hide)
        layout.addLayout(header)
        self.connection = QLabel('Offline')
        self.connection.setObjectName('subtitle')
        layout.addWidget(self.connection)
        self.chat = QTextBrowser()
        self.chat.setOpenLinks(False)
        self.chat.setOpenExternalLinks(False)
        self.chat.setAccessibleName('Main conversation transcript')
        self.chat.anchorClicked.connect(self.open_chat_link)
        layout.addWidget(self.chat, 1)
        self.scope_button = QPushButton()
        self.scope_button.clicked.connect(self.open_workspace)
        self.scope_button.setAccessibleName('Main workspace')
        self.profile = QComboBox()
        for label, value in [('Read only', 'read_only'), ('Project work', 'project'),
                             ('Desktop control', 'desktop')]:
            self.profile.addItem(label, value)
        self.profile.setAccessibleName('Main execution profile')
        self.profile.currentIndexChanged.connect(self.change_profile)
        scope = QHBoxLayout()
        scope.addWidget(self.scope_button, 1)
        scope.addWidget(self.profile)
        layout.addLayout(scope)
        self.input = Composer()
        self.input.setAccessibleName('Message Atlas in Main')
        self.input.setPlaceholderText('Message Atlas in Main…')
        self.input.setFixedHeight(70)
        self.input.setPlainText(self.worker.catalog.draft('main'))
        self.input.sendRequested.connect(self.submit)
        self.input.imagePasted.connect(self.paste_image)
        layout.addWidget(self.input)
        self.attachment_row = QWidget()
        attachments = QHBoxLayout(self.attachment_row)
        attachments.setContentsMargins(0, 0, 0, 0)
        self.attachment_label = QPushButton()
        self.attachment_label.clicked.connect(self.preview_attachment)
        attachments.addWidget(self.attachment_label, 1)
        remove = QPushButton('Remove')
        remove.clicked.connect(self.remove_attachment)
        attachments.addWidget(remove)
        layout.addWidget(self.attachment_row)
        controls = QHBoxLayout()
        image = QPushButton('Image')
        image.clicked.connect(self.attach_image)
        controls.addWidget(image)
        document = QPushButton('File')
        document.clicked.connect(self.attach_document)
        controls.addWidget(document)
        controls.addStretch()
        stop = QPushButton('Stop Work')
        stop.clicked.connect(self.worker.stop_work)
        controls.addWidget(stop)
        self.send = QPushButton('Send')
        self.send.clicked.connect(self.submit)
        controls.addWidget(self.send)
        layout.addLayout(controls)
        self.activity = QLabel('Main chat · project conversations stay in the harness')
        self.activity.setWordWrap(True)
        self.activity.setObjectName('notice')
        layout.addWidget(self.activity)
        voice = QLabel('Voice is available in dedicated harness conversations.')
        voice.setObjectName('footer')
        layout.addWidget(voice)
        self.draft_timer = QTimer(self)
        self.draft_timer.setSingleShot(True)
        self.draft_timer.setInterval(250)
        self.draft_timer.timeout.connect(self.save_draft)
        self.input.textChanged.connect(self.draft_timer.start)
        self.worker.conversation_message.connect(self.on_message)
        self.worker.manual_ack.connect(self.confirm_manual_draft)
        self.worker.send_state.connect(self.on_send_state)
        self.worker.send_error.connect(self.on_send_error)
        self.worker.status.connect(self.on_connection)
        self.load_attachment()
        self.load_header()
        self.render_messages(force_scroll=True)
        self.send.setEnabled('main' not in self.worker.pending_sends)

    def on_connection(self, text):
        # Other chats' send errors are not Main's connection state.
        label = connection_label(text)
        if label is not None:self.connection.setText(label)

    def load_attachment(self):
        ShellActions.load_attachment(self)
        self.attachmentsChanged.emit()

    def on_message(self, cid, identifier, role, text):
        if self.shutting_down or cid != 'main':return
        previous = self.rows.get(identifier)
        self.show_message(identifier, role, text)
        if role == 'Muse' and text.strip() and previous != (role, text[:65536]):
            self.set_state('Reply received', 'milestone')

    def on_send_state(self, cid, pending):
        if self.shutting_down or cid != 'main':return
        self.send.setEnabled(not pending)
        if pending:self.set_state('Sending to Main…', 'working')

    def on_send_error(self, cid, text):
        if not self.shutting_down and cid == 'main':self.set_state(text, 'idle')

    def confirm_manual_draft(self, cid, text):
        if self.shutting_down or cid != 'main':return
        ChatPresentation.confirm_manual_draft(self, cid, text)
        if self.activity.text().startswith('Sending'):
            self.set_state('Sent · waiting for a Main reply', 'working')

    def set_state(self, text, animation='idle'):
        self.activity.setText(text)
        self.stateChanged.emit(text, animation)

    def handle_activity(self, text):
        self.set_state(text)

    def save_draft(self):
        self.draft_timer.stop()
        try:
            self.worker.catalog.draft('main', self.input.toPlainText())
            return True
        except ValueError:
            self.handle_activity('Main draft exceeds 64 KiB; shorten it before closing.')
            return False

    def submit(self):
        text = self.input.toPlainText()
        if 'main' in self.worker.pending_sends or not self.save_draft():return
        if not (text.strip() or self.pending_image or self.worker.router.artifacts.drafts('main')):return
        if not self.worker.send(text, self.pending_image, conversation_id='main'):
            self.handle_activity('Connect to Muse first; Main message was not sent.')

    def load_header(self):
        project = self.worker.catalog.context('main')['project']
        self.scope_button.setText(project['name'] if project else 'Attach Main workspace…')
        self.scope_button.setToolTip(project['root'] if project else 'Choose Main’s workspace independently of the side tabs')
        self.profile.blockSignals(True)
        self.profile.setCurrentIndex(self.profile.findData(self.worker.router.execution.policy('main')['profile']))
        self.profile.blockSignals(False)

    def _bind_workspace(self, project_id):
        from .workspace_binding import bind_workspace
        if not self.save_draft():return
        if 'main' in self.worker.pending_sends:
            self.handle_activity('Wait for Main’s send acknowledgment before changing its workspace.');return
        try:bind_workspace(self.worker.router, 'main', project_id)
        except (ValueError, OSError, RuntimeError) as exc:
            self.handle_activity('Main workspace was not attached: '+str(exc));return
        self.load_header()
        self.owner.refresh_navigation()
        self.handle_activity('Workspace attached to Main.')

    def attach_image(self, *, parent=None):
        filename, _ = QFileDialog.getOpenFileName(parent or self, 'Attach image to Main', '', 'Images (*.png *.jpg *.jpeg *.gif *.webp)')
        if not filename:return
        from .media import load_image
        try:self.set_attachment(load_image(filename))
        except ValueError as exc:self.handle_activity(str(exc))

    def attach_document(self, *, parent=None):
        filename, _ = QFileDialog.getOpenFileName(parent or self, 'Attach file to Main')
        if not filename:return
        try:
            artifact = self.worker.router.artifacts.import_file('main', filename)
            self.worker.router.artifacts.attach('main', artifact['id'])
            self.load_attachment()
        except (ValueError, OSError) as exc:self.handle_activity('Attachment failed: '+str(exc))

    def preview_attachment(self):
        if self.pending_image:self.preview_image(self.pending_image)
        else:self.handle_activity('Attached files: '+', '.join(row['filename'] for row in self.worker.router.artifacts.drafts('main')))

    def toggle_expanded(self):
        self.resize(700, 760) if self.width() < 600 else self.resize(440, 560)
        if hasattr(self.owner, 'companion'):self.owner.companion.place_panel()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_transcript()

    def closeEvent(self, event):
        if not self.save_draft():event.ignore();return
        event.accept()

    def reject(self):
        self.close()
