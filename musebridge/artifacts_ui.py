"""Local previews and deliberate attachment/export of immutable file snapshots."""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QTreeWidget,QTreeWidgetItem,QPushButton,QFileDialog,QDialog,QPlainTextEdit


def build_artifacts_page(w):
    page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(28,22,28,16)
    layout.addWidget(QLabel('Artifacts & documents'))
    hint=QLabel('Immutable files for this conversation. UTF-8, PDF, and Word documents can be read by Muse; images must be explicitly attached.');hint.setWordWrap(True);layout.addWidget(hint)
    w.artifact_tree=QTreeWidget();w.artifact_tree.setHeaderLabels(['File','Type','Size','Source']);w.artifact_tree.setRootIsDecorated(False);w.artifact_tree.setAccessibleName('Conversation artifacts');layout.addWidget(w.artifact_tree,1)
    row=QHBoxLayout()
    for text,callback in [('Import file…',lambda:import_artifact(w)),('Preview',lambda:preview_artifact(w)),('Attach to message',lambda:attach_artifact(w)),('Save copy…',lambda:export_artifact(w))]:
        button=QPushButton(text);button.clicked.connect(callback);row.addWidget(button)
    row.addStretch();layout.addLayout(row)
    w.artifact_notice=QLabel('');w.artifact_notice.setWordWrap(True);layout.addWidget(w.artifact_notice)
    return page


def refresh_artifacts(w):
    selected=w.artifact_tree.currentItem();identifier=selected.data(0,Qt.ItemDataRole.UserRole) if selected else None
    w.artifact_tree.clear()
    for meta in w.worker.router.artifacts.list(w.current_id):
        item=QTreeWidgetItem([meta['filename'],meta['kind'],str(meta['size'])+' B',meta['source']]);item.setData(0,Qt.ItemDataRole.UserRole,meta['id']);w.artifact_tree.addTopLevelItem(item)
        if meta['id']==identifier:w.artifact_tree.setCurrentItem(item)
    if not w.artifact_tree.currentItem() and w.artifact_tree.topLevelItemCount():w.artifact_tree.setCurrentItem(w.artifact_tree.topLevelItem(0))
    w.artifact_tree.setColumnWidth(0,240)


def selected(w):
    item=w.artifact_tree.currentItem()
    if item is None:raise ValueError('Select an artifact first')
    return item.data(0,Qt.ItemDataRole.UserRole)


def import_artifact(w):
    filename,_=QFileDialog.getOpenFileName(w,'Import document or artifact')
    if not filename:return
    try:
        meta=w.worker.router.artifacts.import_file(w.current_id,filename)
        refresh_artifacts(w);w.artifact_notice.setText('Imported '+meta['filename']+'. Select Attach to include it with your next message.')
    except (ValueError,OSError) as exc:w.handle_activity(str(exc))


def preview_artifact(w):
    try:
        store=w.worker.router.artifacts;identifier=selected(w);meta=store.get(w.current_id,identifier)
        if meta['kind']=='image':
            from .media import from_bytes
            w.preview_image(from_bytes(store.data(w.current_id,identifier),meta['mime_type'],meta['filename']));return
        if meta['kind']=='document':
            from .project_ui import background
            cid=w.current_id;filename=meta['filename']
            w.artifact_notice.setText('Reading document text…')
            background(w,lambda:store.read(cid,identifier,offset=0,length=10000),
                lambda result:_show_document_preview(w,cid,filename,result),
                lambda error:w.artifact_notice.setText('Document preview failed: '+error))
            return
        dialog=QDialog(w);dialog.setWindowTitle(meta['filename']);dialog.resize(800,600);layout=QVBoxLayout(dialog)
        text=QPlainTextEdit();text.setReadOnly(True)
        if meta['kind']=='text':
            result=store.read(w.current_id,identifier,offset=0,length=10000)
            content=result['text']
            if result.get('next_offset') is not None:content+='\n\n[Preview limited to the first 10,000 characters. Attach to message to provide paged document text.]'
        else:content='Binary artifact: no text extraction or executable preview. Use Save copy to export its original bytes.'
        text.setPlainText(content)
        layout.addWidget(text);close=QPushButton('Close');close.clicked.connect(dialog.accept);layout.addWidget(close);dialog.exec()
    except (ValueError,OSError) as exc:w.handle_activity(str(exc))


def attach_artifact(w):
    try:
        store=w.worker.router.artifacts;identifier=selected(w);meta=store.get(w.current_id,identifier)
        if meta['kind']=='image':
            from .media import from_bytes
            w.set_attachment(from_bytes(store.data(w.current_id,identifier),meta['mime_type'],meta['filename']))
        elif meta['kind']=='text':
            store.attach(w.current_id,identifier);w.load_attachment();w.handle_activity('Document attached; Muse will receive its reference with your next send.')
        elif meta['kind']=='document':
            from .project_ui import background
            cid=w.current_id;filename=meta['filename']
            w.artifact_notice.setText('Reading document before attachment…')
            background(w,lambda:store.read(cid,identifier,offset=0,length=1),
                lambda result:_finish_document_attach(w,store,cid,identifier,filename,result),
                lambda error:w.artifact_notice.setText('Document was not attached: '+error))
            return
        else:raise ValueError('This binary format is stored for export. Text extraction is unavailable; attach a UTF-8 export instead.')
        w.tabs.setCurrentIndex(0)
    except (ValueError,OSError) as exc:w.handle_activity(str(exc))


def _show_document_preview(w,cid,filename,result):
    if w.current_id!=cid:
        w.artifact_notice.setText('Document preview finished for the previous conversation.')
        return
    content=result['text'] if result['text'] is not None else result.get('limitation','Text extraction unavailable.')
    if result['text'] is not None and result.get('next_offset') is not None:
        content+='\n\n[Preview limited to the first 10,000 characters. Attach to message to provide paged document text.]'
    if result.get('extraction'):content+='\n\n[Extraction: '+result['extraction']['coverage']+']'
    w.artifact_notice.setText('')
    dialog=QDialog(w);dialog.setWindowTitle(filename);dialog.resize(800,600);layout=QVBoxLayout(dialog)
    text=QPlainTextEdit();text.setReadOnly(True);text.setPlainText(content);layout.addWidget(text)
    close=QPushButton('Close');close.clicked.connect(dialog.accept);layout.addWidget(close);dialog.exec()


def _finish_document_attach(w,store,cid,identifier,filename,result):
    if w.current_id!=cid:
        w.artifact_notice.setText('Document was not attached because the conversation changed.')
        return
    if result['text'] is None:
        w.artifact_notice.setText('Document was not attached: '+result.get('limitation','Text extraction unavailable.'))
        return
    try:
        store.attach(cid,identifier);w.load_attachment();w.handle_activity('Document attached; Muse will receive its reference with your next send.')
        w.artifact_notice.setText('')
        w.tabs.setCurrentIndex(0)
    except (ValueError,OSError) as exc:
        w.artifact_notice.setText('Document was not attached: '+str(exc))


def export_artifact(w):
    try:
        store=w.worker.router.artifacts;identifier=selected(w);meta=store.get(w.current_id,identifier)
        filename,_=QFileDialog.getSaveFileName(w,'Save artifact copy',meta['filename'])
        if filename:Path(filename).write_bytes(store.data(w.current_id,identifier));w.artifact_notice.setText('Saved original artifact bytes.')
    except (ValueError,OSError) as exc:w.handle_activity(str(exc))
