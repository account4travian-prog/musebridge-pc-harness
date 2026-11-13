"""Owner-selected tool discovery packs, applied on the next connection."""
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QCheckBox, QDialogButtonBox


class ToolPackDialog(QDialog):
    def __init__(self, catalog, parent=None):
        super().__init__(parent)
        self.catalog = catalog
        self.setWindowTitle('Tool packs')
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Choose the tools Muse sees. Reconnect to apply changes.'))
        self.core = QCheckBox('Core - files, commands, planning and recovery')
        self.core.setChecked(True)
        self.core.setEnabled(False)
        layout.addWidget(self.core)
        choices = catalog.preference('tool_packs',default=['browser','terminal'])
        if not isinstance(choices,list):choices=['browser','terminal']
        self.options = {}
        for key, label in [('browser','Browser - isolated web pages'),
                           ('terminal','Terminal - interactive command sessions'),
                           ('mcp','MCP - locally configured servers and grants')]:
            checkbox = QCheckBox(label)
            checkbox.setChecked(key in choices)
            layout.addWidget(checkbox)
            self.options[key] = checkbox
        note = QLabel('Desktop tools appear when a project uses Desktop control.\n'
                      'MCP tools appear only if an enabled server is configured.\n'
                      'Execution profiles and Stop Work still control what tools can do.')
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        self.catalog.preference('tool_packs',[name for name,control in self.options.items() if control.isChecked()])
        super().accept()


def show_tool_packs(window):
    dialog = ToolPackDialog(window.worker.catalog,window)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        window.handle_activity('Tool pack choice saved. Reconnect to apply the registered tool list.')
