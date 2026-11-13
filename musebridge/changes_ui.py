"""Review direct file edits and restore through the common execution boundary."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QTreeWidget,QTreeWidgetItem,QPlainTextEdit,QPushButton,QSplitter


def build_changes_page(w):
    page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(28,22,28,16);layout.setSpacing(12)
    title=QLabel('Tracked file changes');title.setStyleSheet('font-size:18px;font-weight:600;');layout.addWidget(title)
    hint=QLabel('File edits and completed run snapshots, including shell changes. Restores do not undo installs, network calls, databases or external effects.')
    hint.setObjectName('subtitle');hint.setWordWrap(True);layout.addWidget(hint)
    split=QSplitter(Qt.Orientation.Vertical)
    w.changes_tree=QTreeWidget();w.changes_tree.setObjectName('activity');w.changes_tree.setHeaderLabels(['File','Change','Run'])
    w.changes_tree.setRootIsDecorated(False);w.changes_tree.setAccessibleName('Tracked file changes');split.addWidget(w.changes_tree)
    w.change_diff=QPlainTextEdit();w.change_diff.setReadOnly(True);w.change_diff.setAccessibleName('Selected file diff')
    w.change_diff.setPlaceholderText('Select a tracked edit to inspect its diff.')
    w.change_diff.setStyleSheet('font-family:Consolas;font-size:12px;border:1px solid #e5e8ee;border-radius:7px;padding:8px;')
    split.addWidget(w.change_diff);split.setSizes([160,260]);layout.addWidget(split,1)
    row=QHBoxLayout();w.change_notice=QLabel('No tracked edits in this conversation.');w.change_notice.setObjectName('subtitle')
    w.change_notice.setWordWrap(True);row.addWidget(w.change_notice,1)
    w.restore_button=QPushButton('Restore selected edit');w.restore_button.setEnabled(False)
    w.restore_button.setToolTip('Restore only if the current file still matches this edit’s after state.')
    w.restore_button.clicked.connect(lambda:restore_change(w));row.addWidget(w.restore_button);layout.addLayout(row)
    w.changes_tree.currentItemChanged.connect(lambda *_:show_change(w));w.change_snapshot=None
    return page


def refresh_changes(w):
    rows=w.worker.router.checkpoints.list(w.current_id)['edits']
    latest=w.worker.router.shadow.latest(w.current_id)
    if latest:
        snapshot=w.worker.router.shadow.review(w.current_id,latest)
        rows=[{**item,'edit_id':'shadow:'+latest+':'+item['path'],'state':'finished'} for item in snapshot['edits']]+rows
    feedback={run:w.worker.router.finish_line.summary(w.current_id,run)
              for run in {row['run_id'] for row in rows}}
    fingerprint=(w.current_id,tuple((r['edit_id'],r['state'],r['restored']) for r in rows),
                 tuple((run,state.get('revision'),state.get('checked_revision')) for run,state in sorted(feedback.items())),
                 w.worker.router.execution.policy(w.current_id)['profile'])
    if fingerprint==w.change_snapshot:return
    w.change_snapshot=fingerprint
    current=w.changes_tree.currentItem();selected=current.data(0,Qt.ItemDataRole.UserRole)['edit_id'] if current else None
    w.changes_tree.blockSignals(True);w.changes_tree.clear()
    for data in rows:
        data['unverified_changes']=feedback[data['run_id']]['unverified_changes']
        state='Restored' if data['restored'] else data['change_kind'].capitalize()
        if data['unverified_changes']:state+=' - not checked since last edit'
        item=QTreeWidgetItem([data['path'],state,data['run_id'][:8]])
        item.setToolTip(0,data['path']);item.setToolTip(1,state);item.setToolTip(2,data['run_id']);item.setData(0,Qt.ItemDataRole.UserRole,data)
        w.changes_tree.addTopLevelItem(item)
        if data['edit_id']==selected:w.changes_tree.setCurrentItem(item)
    if not w.changes_tree.currentItem() and rows:w.changes_tree.setCurrentItem(w.changes_tree.topLevelItem(0))
    w.changes_tree.blockSignals(False);w.changes_tree.setColumnWidth(0,300);w.changes_tree.setColumnWidth(1,310)
    show_change(w)


def show_change(w):
    item=w.changes_tree.currentItem()
    if not item:
        w.change_diff.clear();w.restore_button.setEnabled(False);w.change_notice.setText('No tracked edits in this conversation.');return
    data=item.data(0,Qt.ItemDataRole.UserRole)
    if data['state']!='finished':
        w.change_diff.clear();w.change_notice.setText('This edit was interrupted before its after snapshot was recorded. Restore is unavailable.')
        w.restore_button.setEnabled(False);return
    if data.get('source')=='shadow_git':
        w.change_diff.setPlainText(data['diff'])
    else:
        result=w.worker.router.call('change.diff',{'conversation_id':w.current_id,'edit_id':data['edit_id']},expected_conversation_id=w.current_id)
        w.change_diff.setPlainText(result['payload']['diff'] if result['ok'] else result['error']['message'])
    w.change_notice.setText(('Restored · ' if data['restored'] else '')+data['path']+
                           (' - not checked since last edit' if data.get('unverified_changes') else ''))
    profile=w.worker.router.execution.policy(w.current_id)['profile']
    w.restore_button.setEnabled(profile!='read_only' and not w.worker.router.paused and not data['restored'] and data['change_kind']!='unchanged')


def restore_change(w):
    item=w.changes_tree.currentItem()
    if not item:return
    data=item.data(0,Qt.ItemDataRole.UserRole);cid=w.current_id
    params=({'run':data['run_id'],'path':data['path']} if data.get('source')=='shadow_git' else {'edit_id':data['edit_id']})
    result=w.worker.router.call('change.restore',{'conversation_id':cid,**params},expected_conversation_id=cid)
    if result['ok']:
        refresh_changes(w);w.handle_activity('Restored tracked edit: '+data['path'])
    else:
        w.change_notice.setText(result['error']['message']);w.handle_activity('Restore refused: '+result['error']['message'])
