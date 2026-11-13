from PySide6.QtWidgets import QApplication
from musebridge.catalog import Catalog
from musebridge.pack_settings import ToolPackDialog


def test_pack_choices_persist_only_on_owner_save(tmp_path):
    application = QApplication.instance() or QApplication([])
    catalog = Catalog(tmp_path)
    try:
        dialog = ToolPackDialog(catalog)
        assert dialog.core.isChecked() and not dialog.core.isEnabled()
        assert dialog.options['browser'].isChecked() and dialog.options['terminal'].isChecked()
        assert not dialog.options['mcp'].isChecked()
        dialog.options['browser'].setChecked(False)
        dialog.reject()
        assert catalog.preference('tool_packs',default=['browser','terminal']) == ['browser','terminal']
        dialog = ToolPackDialog(catalog)
        dialog.options['browser'].setChecked(False)
        dialog.options['mcp'].setChecked(True)
        dialog.accept()
        assert catalog.preference('tool_packs') == ['terminal','mcp']
        restored = ToolPackDialog(catalog)
        assert restored.options['mcp'].isChecked() and not restored.options['browser'].isChecked()
        restored.reject()
    finally:
        catalog.close()
