"""Owner-only settings dialog for the bounded after-edit check hook."""
from PySide6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QVBoxLayout)

from .edit_checks import EditChecks, HOOK_TIMEOUT_S


def show_edit_check_settings(window):
    """Show and save a local check command for the selected project."""
    cid = window.current_id
    service = EditChecks(window.worker.router)
    if not window.worker.catalog.context(cid).get("project"):
        window.handle_activity("Choose a project before configuring edit checks")
        return

    state = service.configuration(cid)
    dialog = QDialog(window)
    dialog.setWindowTitle("Automatic check after edits")
    dialog.resize(680, 460)
    layout = QVBoxLayout(dialog)
    hint = QLabel(
        "This runs one local PowerShell command after matching file edits. "
        f"It has a {int(HOOK_TIMEOUT_S)} second timeout and is off by default. "
        "The command uses your Windows account permissions; it is not an OS sandbox. "
        "Saving stores the command and does not run it. A failed check does not undo or reject the file edit."
    )
    hint.setWordWrap(True)
    layout.addWidget(hint)

    form = QFormLayout()
    enabled = QCheckBox("Enable automatic check after matching edits")
    enabled.setChecked(state["enabled"])
    command = QPlainTextEdit(state["command"])
    command.setPlaceholderText("python -m pytest -q")
    command.setMinimumHeight(100)
    path_filter = QLineEdit(state["path_filter"])
    path_filter.setPlaceholderText("**/*.py (blank matches every edited file)")
    form.addRow(enabled)
    form.addRow("PowerShell command", command)
    form.addRow("Project-relative glob", path_filter)
    layout.addLayout(form)

    output = QPlainTextEdit()
    output.setReadOnly(True)
    output.setMaximumHeight(100)
    layout.addWidget(output)

    buttons = QHBoxLayout()
    save = QPushButton("Save")
    close = QPushButton("Close")

    def save_settings():
        try:
            result = service.configure(cid, command.toPlainText(), path_filter.text(), enabled.isChecked())
        except (ValueError, OSError) as exc:
            output.setPlainText(str(exc))
            return
        output.setPlainText("Settings saved. No command was run.")
        if result["enabled"]:
            window.handle_activity("Automatic edit check enabled for this project")
        else:
            window.handle_activity("Automatic edit check disabled for this project")

    save.clicked.connect(save_settings)
    close.clicked.connect(dialog.accept)
    buttons.addWidget(save)
    buttons.addWidget(close)
    buttons.addStretch()
    layout.addLayout(buttons)
    dialog.exec()
