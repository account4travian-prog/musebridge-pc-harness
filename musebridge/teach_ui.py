"""Owner initiated VM skill teaching and explicit confirmation."""
from .context_snapshot import SKILL_NAME, SKILL_VERSION

CONFIRMATION_KEY = 'musebridge_skill_confirmed_version'


def teach_request():
    return ('Please call bridge.context(section="skill") on MuseBridge Windows PC, then save '
            'that document as your VM skill "'+SKILL_NAME+'" (version '+str(SKILL_VERSION)+'). '
            'Keep your existing VM skills and workflows. Confirm whether the skill was actually saved, '
            'including its name and version. If you cannot save it, explain the limitation; do not claim installation.')


def record_confirmation(catalog):
    catalog.preference(CONFIRMATION_KEY,SKILL_VERSION)


def show_teach(window):
    from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QPlainTextEdit,QPushButton
    dialog=QDialog(window);dialog.setWindowTitle('Teach Muse this PC');dialog.resize(620,360)
    layout=QVBoxLayout(dialog)
    label=QLabel('Send this request in the current chat. When Muse confirms it saved the skill, reopen this dialog and record that confirmation.')
    label.setWordWrap(True);layout.addWidget(label)
    preview=QPlainTextEdit(teach_request());preview.setReadOnly(True);layout.addWidget(preview)
    send=QPushButton('Send skill request');layout.addWidget(send)
    def submit():
        if window.current_id in window.worker.pending_sends:
            window.activity.setText('Wait for the current send to finish.');return
        core=window.worker.core
        if not core or not core.session or not core.session.registered.is_set():
            window.activity.setText('Connect before teaching Muse this PC.');return
        # Send independently of the composer; preserve its image and document drafts.
        if window.worker.send(teach_request(),None,artifact_ids=(),preserve_composer=True):
            dialog.accept()
    send.clicked.connect(submit)
    confirm=QPushButton('Muse confirmed it saved this skill — record confirmation');layout.addWidget(confirm)
    def acknowledge():
        record_confirmation(window.worker.catalog)
        window.activity.setText('Recorded your confirmation of the Muse VM skill v'+str(SKILL_VERSION)+'.')
        dialog.accept()
    confirm.clicked.connect(acknowledge)
    close=QPushButton('Close');close.clicked.connect(dialog.reject);layout.addWidget(close)
    dialog.exec()
