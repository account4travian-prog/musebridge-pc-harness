"""Persisted work overview and silent, opt-in attention indicators."""
import time
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QLabel,QTreeWidget,QTreeWidgetItem,QCheckBox,QPushButton


def work_rows(worker):
    rows=[]
    for conversation in worker.catalog.conversations():
        cid=conversation['id'];runs=worker.catalog.rows('SELECT state,origin FROM runs WHERE conversation_id=? ORDER BY created DESC LIMIT 1',(cid,))
        pending=worker.catalog.rows("SELECT state,count(*) AS n FROM request_queue_v1 WHERE conversation_id=? AND state IN ('pending','sending','unknown') GROUP BY state",(cid,))
        links=worker.catalog.rows('SELECT job_id FROM job_links WHERE conversation_id=? ORDER BY rowid DESC LIMIT 100',(cid,))
        states=worker.router.jobs.states(link['job_id'] for link in links)
        active=sum(state in ('accepted','running') for state in states.values())
        uncertain=sum(p['n'] for p in pending if p['state']=='unknown')
        state='Idle'
        if runs:
            state={'sending':'Sending','acknowledged':'Muse working','remote_finished':'Muse turn ended','remote_reply_finished':'Muse reply ended','succeeded':'Local tool finished',
                   'failed':'Failed','interrupted':'Paused','unknown':'Inspect uncertain outcome','local':'Local work'}.get(runs[0]['state'],runs[0]['state'])
        if uncertain:state='Inspect uncertain send'
        elif active:state='Local jobs running'
        queued=sum(p['n'] for p in pending if p['state']=='pending')
        rows.append({'id':cid,'title':conversation['title'],'state':state,'active_jobs':active,'queued':queued,
            'queue_mode':'Enabled' if cid in worker.queue.enabled else 'Held','goal':conversation['goal_state']})
    return rows


def build_overview_page(w):
    page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(28,22,28,16)
    layout.addWidget(QLabel('Work across conversations'))
    hint=QLabel('Local jobs remain visible when you switch chats. A remote turn ending is separate from verified task completion. Queues run only in the selected conversation.');hint.setWordWrap(True);layout.addWidget(hint)
    w.overview_tree=QTreeWidget();w.overview_tree.setHeaderLabels(['Conversation','Activity','Local jobs','Queued','Queue','Goal']);w.overview_tree.setRootIsDecorated(False);w.overview_tree.setAccessibleName('All conversation work');layout.addWidget(w.overview_tree,1)
    def open_selected():
        item=w.overview_tree.currentItem()
        if item:w.refresh_conversations(item.data(0,Qt.ItemDataRole.UserRole));w.switch_conversation();w.tabs.setCurrentIndex(0)
    button=QPushButton('Open selected conversation');button.clicked.connect(open_selected);layout.addWidget(button)
    w.attention_enabled=QCheckBox('Show silent attention indicators for new finished or failed work')
    w.attention_enabled.setChecked(bool(w.worker.catalog.preference('attention_indicators',default=False)))
    w.attention_enabled.toggled.connect(lambda enabled:w.worker.catalog.preference('attention_indicators',enabled));layout.addWidget(w.attention_enabled)
    from .notifications import DesktopNotifications
    from .attention import AttentionTracker
    def open_notification(cid):
        if cid=='__overview__':w.tabs.setCurrentIndex(8)
        else:
            w.refresh_conversations(cid);w.switch_conversation();w.tabs.setCurrentIndex(0)
        # Only a deliberate notification click can raise the application.
        w.showNormal();w.raise_();w.activateWindow()
    w.desktop_notifier=DesktopNotifications(w,open_notification)
    w.desktop_attention=AttentionTracker(w.worker)
    w.desktop_notifications=QCheckBox('Silent desktop notifications while MuseBridge is open')
    w.desktop_notifications.setChecked(bool(w.worker.catalog.preference('desktop_notifications',default=False)))
    def desktop_changed(enabled):
        w.worker.catalog.preference('desktop_notifications',enabled);w.desktop_attention=AttentionTracker(w.worker)
        if not enabled:w.desktop_notifier.close()
    w.desktop_notifications.toggled.connect(desktop_changed);layout.addWidget(w.desktop_notifications)
    w.attention_label=QLabel('');w.attention_label.setWordWrap(True);layout.addWidget(w.attention_label)
    w._attention_since=time.time()
    return page


def refresh_overview(w):
    selected=w.overview_tree.currentItem();cid=selected.data(0,Qt.ItemDataRole.UserRole) if selected else w.current_id
    w.overview_tree.clear()
    for row in work_rows(w.worker):
        item=QTreeWidgetItem([row['title'],row['state'],str(row['active_jobs']),str(row['queued']),row['queue_mode'],row['goal']]);item.setData(0,Qt.ItemDataRole.UserRole,row['id']);w.overview_tree.addTopLevelItem(item)
        if row['id']==cid:w.overview_tree.setCurrentItem(item)
    w.overview_tree.setColumnWidth(0,240);w.overview_tree.setColumnWidth(1,190)


def refresh_attention(w):
    if w.desktop_notifications.isChecked():
        notice=w.desktop_attention.poll()
        if notice and (not w.isActiveWindow() or notice['conversation_id']!=w.current_id):
            accepted=w.desktop_notifier.show(notice['conversation_id'],'MuseBridge work update','Work finished or needs inspection. Click to review it in MuseBridge.')
            if not accepted:w.handle_activity('Desktop notification was unavailable; inspect Overview. Windows may suppress notifications.')
    if not w.attention_enabled.isChecked():return
    rows=w.worker.catalog.rows("SELECT conversation_id,kind FROM events WHERE created>? AND kind IN ('run.remote_finished','run.remote_reply_finished','run.failed','run.unknown','hook.admission_unknown') ORDER BY seq DESC LIMIT 20",(w._attention_since,))
    if not rows:return
    w._attention_since=time.time()
    names=[]
    for row in rows:
        conversation=w.worker.catalog.conversation(row['conversation_id'])
        name=conversation['title']
        if name not in names:names.append(name)
    w.attention_label.setText('Work updated: '+', '.join(names[:4])+'. Open the conversation to inspect its result.')
    w.tabs.setTabText(8,'Overview •')
    if w.tabs.currentIndex()==8:w.tabs.setTabText(8,'Overview')
