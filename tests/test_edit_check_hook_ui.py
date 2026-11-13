import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QTreeWidget, QWidget)

from musebridge.edit_checks import HOOK_EVENT, HOOK_NAME
from musebridge.project_ui import (generic_hooks, hooks_dialog, is_edit_check_hook,
                                   is_reserved_hook_name)


def _app():
    return QApplication.instance() or QApplication([])


def test_generic_hook_list_hides_only_reserved_auto_check():
    reserved = {"name": f"  {HOOK_NAME} ", "event": HOOK_EVENT}
    ordinary = {"name": "audit", "event": HOOK_EVENT}
    same_name_other_event = {"name": HOOK_NAME, "event": "run_ended"}
    assert is_edit_check_hook(reserved)
    assert generic_hooks([reserved, ordinary, same_name_other_event]) == [ordinary, same_name_other_event]
    assert is_reserved_hook_name(f" {HOOK_NAME}  ", "run_ended")
    assert not is_reserved_hook_name("Automatic edit check extra", HOOK_EVENT)


def test_hooks_dialog_hides_reserved_hook_and_rejects_name_collision(monkeypatch):
    _app()
    hooks = [
        {"hook_id": "reserved", "name": HOOK_NAME, "event": HOOK_EVENT,
         "enabled": True, "command": "Write-Output reserved", "timeout_s": 10, "tool_filter": []},
        {"hook_id": "ordinary", "name": "audit", "event": "after_file_edit",
         "enabled": False, "command": "Write-Output audit", "timeout_s": 30, "tool_filter": []},
    ]

    class HookService:
        def __init__(self):
            self.set_calls = []

        def list_hooks(self, _scope):
            return hooks

        def set_hook(self, *args, **kwargs):
            self.set_calls.append((args, kwargs))

        def refresh(self, _cid):
            return []

    service = HookService()
    project = {"id": "project-1", "root": "C:/fixture"}
    window = QWidget()
    window.current_id = "conversation-1"
    window.handle_activity = lambda *_args: None
    window.worker = type("Worker", (), {
        "catalog": type("Catalog", (), {"context": lambda _self, _cid: {"project": project}})(),
        "router": type("Router", (), {"hooks": service})(),
    })()
    seen = {}

    def run_dialog(dialog):
        if dialog.windowTitle() == "Configure hook":
            lines = dialog.findChildren(QLineEdit)
            lines[0].setText(f"  {HOOK_NAME}  ")
            dialog.findChild(QComboBox).setCurrentText(HOOK_EVENT)
            dialog.findChild(QPlainTextEdit).setPlainText("Write-Output should-not-register")
            return QDialog.DialogCode.Accepted
        tree = dialog.findChild(QTreeWidget)
        seen["visible_names"] = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
        buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
        buttons["New hook"].click()
        seen["output"] = next(item.toPlainText() for item in dialog.findChildren(QPlainTextEdit)
                               if item.isReadOnly())
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", run_dialog)
    try:
        hooks_dialog(window)
        assert seen["visible_names"] == ["audit"]
        assert "reserved" in seen["output"].casefold() or "dedicated project settings" in seen["output"].casefold()
        assert service.set_calls == []
    finally:
        window.close()
