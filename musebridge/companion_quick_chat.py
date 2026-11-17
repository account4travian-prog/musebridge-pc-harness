"""Compact Main composer sharing the existing panel's draft and attachments."""
from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QPushButton, QTextBrowser, QMenu

from .presentation import Composer, icon, markdown_fragment


class QuickChat(QWidget):
    def __init__(self, companion):
        super().__init__(companion)
        self.companion, self.panel = companion, companion.panel
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.reply = QTextBrowser()
        self.reply.setAccessibleName('Latest Main reply')
        self.reply.setOpenLinks(False)
        self.reply.anchorClicked.connect(lambda _:companion.open_chat())
        self.reply.setFixedHeight(115)
        self.reply.setStyleSheet('QTextBrowser {background:white;border:0;border-radius:14px;padding:10px;color:#27334b;font-size:12px;}')
        self.reply.hide()
        layout.addWidget(self.reply)
        self.bar = QFrame()
        self.bar.setObjectName('quickBar')
        self.bar.setStyleSheet('QFrame#quickBar {background:white;border-radius:22px;}')
        row = QHBoxLayout(self.bar)
        row.setContentsMargins(7, 5, 7, 5)
        row.setSpacing(3)
        self.add = QPushButton()
        self.add.setIcon(icon('plus'))
        self.add.setAccessibleName('Main attachments and tools')
        self.add.setToolTip('Attachments and Main controls')
        self.add.setFixedSize(30, 30)
        self.add.clicked.connect(self.show_add_menu)
        row.addWidget(self.add)
        self.input = Composer()
        self.input.setAccessibleName('Quick message to Atlas in Main')
        self.input.setPlaceholderText('Message Atlas · Main')
        self.input.setFixedHeight(36)
        self.input.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.input.setStyleSheet('QPlainTextEdit {border:0;background:white;color:#27334b;padding:6px 0;font-size:12px;}')
        self.input.setPlainText(self.panel.input.toPlainText())
        self.input.textChanged.connect(self.to_panel)
        self.panel.input.textChanged.connect(self.from_panel)
        self.input.sendRequested.connect(self.panel.submit)
        self.input.imagePasted.connect(self.panel.paste_image)
        self.input.installEventFilter(companion)
        row.addWidget(self.input, 1)
        self.send = QPushButton('↑')
        self.send.setAccessibleName('Send quick Main message')
        self.send.setStyleSheet('QPushButton {background:#98b4eb;color:white;border-radius:14px;font-size:18px;}')
        self.send.setFixedSize(28, 28)
        self.send.clicked.connect(self.panel.submit)
        row.addWidget(self.send)
        expand = QPushButton()
        expand.setIcon(icon('export'))
        expand.setAccessibleName('Open full Main history')
        expand.setToolTip('Full Main conversation')
        expand.setFixedSize(26, 28)
        expand.clicked.connect(companion.open_chat)
        row.addWidget(expand)
        layout.addWidget(self.bar)
        self.attachments = QPushButton()
        self.attachments.setAccessibleName('Review Main attachments')
        self.attachments.clicked.connect(self.show_add_menu)
        self.attachments.setStyleSheet('background:white;border-radius:10px;padding:5px;color:#34415c;')
        self.attachments.hide()
        layout.addWidget(self.attachments)
        self.feedback = QLabel('Main conversation')
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.feedback.setStyleSheet('background:white;border-radius:7px;padding:3px;color:#56627a;font-size:10px;')
        self.feedback.setFixedHeight(22)
        layout.addWidget(self.feedback)
        self.panel.stateChanged.connect(self.on_state)
        self.panel.attachmentsChanged.connect(self.refresh_attachments)
        self.panel.worker.send_state.connect(self.send_state)
        self.send.setEnabled('main' not in self.panel.worker.pending_sends)
        self.refresh_attachments()

    def to_panel(self):
        if self.panel.input.toPlainText() != self.input.toPlainText():
            self.panel.input.setPlainText(self.input.toPlainText())

    def from_panel(self):
        if self.input.toPlainText() != self.panel.input.toPlainText():
            blocker = QSignalBlocker(self.input)
            self.input.setPlainText(self.panel.input.toPlainText())
            del blocker

    def refresh_attachments(self):
        labels = self.panel.attachment_label.text()
        self.attachments.setText(labels[:60])
        self.attachments.setToolTip(labels)
        self.attachments.setVisible(bool(labels))
        if hasattr(self.companion, 'quick'):self.companion.reflow()

    def send_state(self, cid, pending):
        if cid == 'main':self.send.setEnabled(not pending)

    def on_state(self, text, animation):
        self.feedback.setText(text if len(text)<55 else 'Main · open full conversation for details')
        self.feedback.setToolTip(text)
        if text == 'Reply received':
            reply = next((body for role,body in reversed(self.panel.rows.values()) if role=='Muse'), '')
            self.reply.setHtml(markdown_fragment(reply[:1800]))
            self.reply.show()
        if hasattr(self.companion, 'quick'):self.companion.reflow()

    def add_menu(self):
        menu = QMenu(self)
        menu.addAction('Attach image…', lambda:self.panel.attach_image(parent=self))
        menu.addAction('Attach file…', lambda:self.panel.attach_document(parent=self))
        remove = menu.addAction('Remove attachments', self.panel.remove_attachment)
        remove.setEnabled(bool(self.panel.attachment_label.text()))
        menu.addSeparator()
        menu.addAction('Main workspace and permissions…', self.companion.open_chat)
        menu.addAction('Available Main tools…', self.panel.capabilities_dialog)
        menu.addAction('Voice conversation…', self.companion.open_voice)
        menu.addSeparator()
        menu.addAction('Stop Work', self.panel.worker.stop_work)
        return menu

    def show_add_menu(self):
        self.companion.menu_open = True
        try:self.add_menu().exec(self.add.mapToGlobal(self.add.rect().bottomLeft()))
        finally:self.companion.menu_open = False

    def wanted_height(self):
        return 76+(121 if not self.reply.isHidden() else 0)+(34 if self.panel.attachment_label.text() else 0)
