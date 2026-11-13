"""Owner-only opt-in for one queued verification prompt after unchecked edits."""
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel,
                               QPlainTextEdit, QPushButton, QVBoxLayout)


def show_finish_check_settings(window):
    """Save project settings without sending messages or resuming held work."""
    cid = window.current_id
    if not window.worker.catalog.context(cid).get('project'):
        window.handle_activity('Choose a project before configuring finish-line verification')
        return
    service = window.worker.router.finish_line
    state = service.configuration(cid)
    dialog = QDialog(window)
    dialog.setWindowTitle('Finish-line verification')
    dialog.resize(680, 380)
    layout = QVBoxLayout(dialog)
    hint = QLabel('After a dedicated workspace request finishes with unchecked file edits, '
                  'queue one prompt asking Muse to run this command and fix failures or explain why it does not apply. '
                  'Off by default. Stop Work, held queues and the five-round goal limit still apply. '
                  'Restart holds the queue; use Queue > Resume to allow queued work. '
                  'Saving does not execute the command or resume a queue.')
    hint.setWordWrap(True)
    layout.addWidget(hint)
    enabled = QCheckBox('Auto-check after changes')
    enabled.setChecked(state['enabled'])
    layout.addWidget(enabled)
    command = QPlainTextEdit(state['check_command'])
    command.setAccessibleName('Finish-line check command')
    command.setPlaceholderText('python -m pytest -q')
    layout.addWidget(command)
    output = QLabel('')
    output.setWordWrap(True)
    layout.addWidget(output)
    row = QHBoxLayout()
    save = QPushButton('Save')
    close = QPushButton('Close')
    def save_settings():
        try:
            service.configure(cid, command.toPlainText(), enabled.isChecked())
            output.setText('Settings saved. No command was run and no queue was resumed.')
        except (ValueError, OSError) as exc:
            output.setText(str(exc))
    save.clicked.connect(save_settings)
    close.clicked.connect(dialog.accept)
    row.addWidget(save)
    row.addWidget(close)
    layout.addLayout(row)
    dialog.exec()
