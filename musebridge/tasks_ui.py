"""Structured task progress with inspectable local check receipts."""
import json
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QTreeWidget,QTreeWidgetItem,
    QPlainTextEdit,QPushButton,QSplitter,QDialog,QFormLayout,QLineEdit,QComboBox,QListWidget,
    QAbstractItemView,QDialogButtonBox)


def build_tasks_page(w):
    page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(28,22,28,16);layout.setSpacing(12)
    title=QLabel('Tasks & checks');title.setStyleSheet('font-size:18px;font-weight:600;');layout.addWidget(title)
    hint=QLabel('Check receipts show what ran. File changes invalidate checks bound to their earlier content.')
    hint.setObjectName('subtitle');hint.setWordWrap(True);layout.addWidget(hint)
    split=QSplitter(Qt.Orientation.Vertical)
    w.tasks_tree=QTreeWidget();w.tasks_tree.setObjectName('activity');w.tasks_tree.setHeaderLabels(['Task','State','Verification'])
    w.tasks_tree.setRootIsDecorated(False);w.tasks_tree.setAccessibleName('Structured tasks');split.addWidget(w.tasks_tree)
    w.task_detail=QPlainTextEdit();w.task_detail.setReadOnly(True);w.task_detail.setAccessibleName('Task criteria and check receipt');split.addWidget(w.task_detail)
    split.setSizes([190,220]);layout.addWidget(split,1)
    row=QHBoxLayout();w.task_add=QPushButton('New task');w.task_add.clicked.connect(lambda:new_task(w));row.addWidget(w.task_add)
    w.task_start=QPushButton('Start');w.task_start.clicked.connect(lambda:update_task(w,'active'));row.addWidget(w.task_start)
    w.task_complete=QPushButton('Mark complete');w.task_complete.clicked.connect(lambda:update_task(w,'complete'));row.addWidget(w.task_complete)
    w.task_review=QPushButton('Accept manual review');w.task_review.clicked.connect(lambda:review_task(w));row.addWidget(w.task_review)
    row.addStretch();layout.addLayout(row)
    w.task_notice=QLabel('No structured tasks in this conversation.');w.task_notice.setObjectName('subtitle');w.task_notice.setWordWrap(True);layout.addWidget(w.task_notice)
    w.tasks_tree.currentItemChanged.connect(lambda *_:show_task(w));w.task_snapshot=None
    return page


def refresh_tasks(w):
    tasks=w.worker.router.tasks.list(w.current_id)
    policy=w.worker.router.execution.policy(w.current_id)
    fingerprint=(w.current_id,json.dumps(tasks,sort_keys=True),policy['profile'],policy['paused'])
    if fingerprint==w.task_snapshot:return
    w.task_snapshot=fingerprint
    current=w.tasks_tree.currentItem();selected=current.data(0,Qt.ItemDataRole.UserRole)['task_id'] if current else None
    w.tasks_tree.blockSignals(True);w.tasks_tree.clear()
    for task in tasks:
        verification=task.get('verification') or {}
        label='Stale' if verification.get('stale') else 'Passed' if verification.get('passed') else 'Not verified'
        item=QTreeWidgetItem([task['title'],task['state'].replace('_',' '),label]);item.setData(0,Qt.ItemDataRole.UserRole,task)
        w.tasks_tree.addTopLevelItem(item)
        if task['task_id']==selected:w.tasks_tree.setCurrentItem(item)
    if not w.tasks_tree.currentItem() and tasks:w.tasks_tree.setCurrentItem(w.tasks_tree.topLevelItem(0))
    w.tasks_tree.blockSignals(False);w.tasks_tree.setColumnWidth(0,360);w.tasks_tree.setColumnWidth(1,160)
    writable=policy['profile']!='read_only' and not policy['paused'];w.task_add.setEnabled(writable)
    show_task(w)


def show_task(w):
    item=w.tasks_tree.currentItem();policy=w.worker.router.execution.policy(w.current_id)
    writable=policy['profile']!='read_only' and not policy['paused']
    for button in (w.task_start,w.task_complete,w.task_review):button.setEnabled(bool(item) and writable)
    if not item:
        w.task_detail.clear();w.task_notice.setText('No structured tasks in this conversation.');return
    task=item.data(0,Qt.ItemDataRole.UserRole)
    w.task_detail.setPlainText(task['title']+'\n\nCriteria: '+(task['criteria'] or 'No additional criteria recorded')+
        '\n\nVerification mode: '+task['verification_mode']+'\n\n'+json.dumps(task,indent=2,ensure_ascii=False))
    w.task_review.setEnabled(writable and task['verification_mode']=='manual' and task['state']!='complete')
    w.task_notice.setText(task.get('blocked_reason') or task.get('details') or 'Job checks verify execution and the bound inputs; they are not an independent semantic judgment.')


def new_task(w):
    dialog=QDialog(w);dialog.setWindowTitle('New task');dialog.resize(530,440);form=QFormLayout(dialog)
    title=QLineEdit();title.setMaxLength(200);criteria=QPlainTextEdit();criteria.setMaximumHeight(100)
    mode=QComboBox();mode.addItem('Successful check job','job_exit');mode.addItem('Manual review','manual')
    dependencies=QListWidget();dependencies.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
    for task in w.worker.router.tasks.list(w.current_id):
        dependencies.addItem(task['title']);dependencies.item(dependencies.count()-1).setData(Qt.ItemDataRole.UserRole,task['task_id'])
    form.addRow('Title',title);form.addRow('Acceptance criteria',criteria);form.addRow('Verification',mode);form.addRow('Depends on',dependencies)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);form.addRow(buttons)
    if dialog.exec():
        result=w.worker.router.call('task.create',{'conversation_id':w.current_id,'title':title.text(),'criteria':criteria.toPlainText(),
            'verification_mode':mode.currentData(),'dependencies':[item.data(Qt.ItemDataRole.UserRole) for item in dependencies.selectedItems()]})
        refresh_tasks(w)
        if not result['ok']:w.task_notice.setText(result['error']['message'])


def update_task(w,state):
    item=w.tasks_tree.currentItem()
    if not item:return
    task=item.data(0,Qt.ItemDataRole.UserRole)
    result=w.worker.router.call('task.update',{'conversation_id':w.current_id,'task_id':task['task_id'],'state':state})
    refresh_tasks(w)
    if not result['ok']:w.task_notice.setText(result['error']['message'])


def review_task(w):
    item=w.tasks_tree.currentItem()
    if not item:return
    result=w.worker.router.review_task(w.current_id,item.data(0,Qt.ItemDataRole.UserRole)['task_id'])
    refresh_tasks(w)
    if not result['ok']:w.task_notice.setText(result['error']['message'])
