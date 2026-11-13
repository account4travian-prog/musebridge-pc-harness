"""User-owned queued requests; ambiguous sends remain visible and held."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QTreeWidget,QTreeWidgetItem,QPlainTextEdit,QPushButton,QInputDialog


def build_queue_page(w):
    page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(28,22,28,16);layout.setSpacing(12)
    title=QLabel('Queued requests');title.setStyleSheet('font-size:18px;font-weight:600;');layout.addWidget(title)
    hint=QLabel('Queue a follow-up with Ctrl+Shift+Enter. Pending requests wait for the current turn to settle. Restart and Stop Work hold the queue.')
    hint.setObjectName('subtitle');hint.setWordWrap(True);layout.addWidget(hint)
    w.queue_tree=QTreeWidget();w.queue_tree.setObjectName('activity');w.queue_tree.setHeaderLabels(['Request','Delivery','Run']);w.queue_tree.setRootIsDecorated(False)
    w.queue_tree.setAccessibleName('Queued requests');layout.addWidget(w.queue_tree,1)
    w.queue_detail=QPlainTextEdit();w.queue_detail.setReadOnly(True);w.queue_detail.setMaximumHeight(150);layout.addWidget(w.queue_detail)
    row=QHBoxLayout()
    w.queue_resume=QPushButton('Resume pending');w.queue_resume.clicked.connect(lambda:resume_queue(w));row.addWidget(w.queue_resume)
    hold=QPushButton('Hold queue');hold.clicked.connect(lambda:hold_queue(w));row.addWidget(hold)
    w.queue_edit=QPushButton('Edit pending');w.queue_edit.clicked.connect(lambda:edit_queue(w));row.addWidget(w.queue_edit)
    w.queue_cancel=QPushButton('Cancel pending');w.queue_cancel.clicked.connect(lambda:cancel_queue(w));row.addWidget(w.queue_cancel)
    row.addStretch();layout.addLayout(row)
    w.queue_notice=QLabel();w.queue_notice.setObjectName('subtitle');w.queue_notice.setWordWrap(True);layout.addWidget(w.queue_notice)
    w.queue_tree.currentItemChanged.connect(lambda *_:show_queue(w));w.queue_snapshot=None
    return page


def refresh_queue(w):
    rows=w.worker.queue.rows(w.current_id);enabled=w.current_id in w.worker.queue.enabled
    fingerprint=(w.current_id,tuple((r['id'],r['state'],r['text']) for r in rows),enabled)
    if fingerprint==w.queue_snapshot:return
    w.queue_snapshot=fingerprint
    current=w.queue_tree.currentItem();selected=current.data(0,Qt.ItemDataRole.UserRole)['id'] if current else None
    w.queue_tree.blockSignals(True);w.queue_tree.clear()
    for data in rows:
        label=data['text'].splitlines()[0][:110] if data['text'].strip() else data['filename'] or 'Image'
        item=QTreeWidgetItem([label,data['state'],(data['run_id'] or '')[:8]])
        item.setData(0,Qt.ItemDataRole.UserRole,data);w.queue_tree.addTopLevelItem(item)
        if data['id']==selected:w.queue_tree.setCurrentItem(item)
    if not w.queue_tree.currentItem() and rows:w.queue_tree.setCurrentItem(w.queue_tree.topLevelItem(0))
    w.queue_tree.blockSignals(False);w.queue_tree.setColumnWidth(0,430);w.queue_tree.setColumnWidth(1,150)
    w.queue_resume.setEnabled(w.current_id!='main' and (not enabled or any(r['state']=='pending' for r in rows)))
    w.queue_notice.setText('Queue enabled. Unknown entries are never replayed.' if enabled else 'Queue held. Resume sends pending requests only; inspect uncertain outcomes before continuing.')
    show_queue(w)


def show_queue(w):
    item=w.queue_tree.currentItem();data=item.data(0,Qt.ItemDataRole.UserRole) if item else None
    w.queue_detail.setPlainText((data['text']+('\n\nImage: '+data['filename'] if data['filename'] else '')) if data else '')
    for button in (w.queue_edit,w.queue_cancel):button.setEnabled(bool(data and data['state']=='pending'))


def resume_queue(w):
    try:w.worker.queue.resume(w.current_id);refresh_queue(w)
    except ValueError as exc:w.queue_notice.setText(str(exc))


def hold_queue(w):
    w.worker.queue.hold(w.current_id);refresh_queue(w)


def edit_queue(w):
    item=w.queue_tree.currentItem()
    if not item:return
    data=item.data(0,Qt.ItemDataRole.UserRole)
    text,ok=QInputDialog.getMultiLineText(w,'Edit queued request','Request:',data['text'])
    if ok:
        try:w.worker.queue.edit(w.current_id,data['id'],text);refresh_queue(w)
        except ValueError as exc:w.queue_notice.setText(str(exc))


def cancel_queue(w):
    item=w.queue_tree.currentItem()
    if not item:return
    try:w.worker.queue.cancel(w.current_id,item.data(0,Qt.ItemDataRole.UserRole)['id']);refresh_queue(w)
    except ValueError as exc:w.queue_notice.setText(str(exc))
