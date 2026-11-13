"""Native local screen for owned interactive ConPTY sessions."""
import shutil
import uuid

from PySide6.QtCore import QObject, Qt, Signal, Slot, QTimer
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QPushButton,QLineEdit,QCheckBox

from .terminal_screen import TerminalScreenModel, TerminalScreenWidget


class _TerminalCompletions(QObject):
    """Deliver executor Future completions back on the Qt GUI thread."""
    completed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.completed.connect(self._dispatch, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _dispatch(self, payload):
        callback, result = payload
        callback(result)

    def submit(self, worker, name, params, callback):
        future = worker.submit_terminal(name, params)
        def finished(done):
            try:
                result = done.result()
            except Exception as exc:
                result = {'ok': False, 'error': {'message': str(exc)}}
            try:
                self.completed.emit((callback, result))
            except RuntimeError:
                # The window may close while an already queued terminal action
                # is finishing; do not let a deleted Qt object escape a worker.
                pass
        future.add_done_callback(finished)
        return future


def _submit_terminal(w, name, params, cid, callback, terminal_id=None):
    def finish(result):
        # The target conversation was captured in params before enqueueing; do
        # not retarget a completion to whichever chat the user later selects.
        if not getattr(w,'_closed',False) and w.current_id == cid and (terminal_id is None or w.terminal_sessions.currentData() == terminal_id):
            callback(result)
    try:
        return w._terminal_completions.submit(w.worker, name, params, finish)
    except Exception as exc:
        finish({'ok': False, 'error': {'message': str(exc)}})
        return None


def _schedule_fit_resize(w):
    timer=getattr(w,'terminal_resize_timer',None)
    if timer and getattr(w,'terminal_fit',None) and w.terminal_fit.isChecked():
        timer.start()


def _queue_fit_resize(w, cid, identifier, size):
    rows,cols=size
    if not w.terminal_fit.isChecked() or cid!=w.current_id or identifier!=w.terminal_sessions.currentData():
        return
    marker=(cid,identifier,rows,cols)
    w._terminal_resize_inflight=marker
    params={'conversation_id':cid,'terminal_id':identifier,'rows':rows,'cols':cols}
    def completed(result):
        if w._terminal_resize_inflight!=marker:return
        w._terminal_resize_inflight=None
        if getattr(w,'_closed',False):return
        key=(cid,identifier)
        if result.get('ok'):
            w._terminal_session_size[key]=(rows,cols)
            if w.terminal_fit.isChecked() and w.current_id==cid and w.terminal_sessions.currentData()==identifier:
                w.terminal_rows.setText(str(rows));w.terminal_cols.setText(str(cols))
        elif w.current_id==cid and w.terminal_sessions.currentData()==identifier:
            w.terminal_notice.setText(result['error']['message'])
        latest=w._terminal_fit_latest
        w._terminal_fit_latest=None
        if (latest and w.terminal_fit.isChecked() and latest[0]==w.current_id
                and latest[1]==w.terminal_sessions.currentData()):
            if latest[2]!=w._terminal_session_size.get((latest[0],latest[1])):
                _queue_fit_resize(w,latest[0],latest[1],latest[2])
    try:
        w._terminal_completions.submit(w.worker,'terminal.resize',params,completed)
    except Exception as exc:
        w._terminal_resize_inflight=None
        if w.current_id==cid and w.terminal_sessions.currentData()==identifier:
            w.terminal_notice.setText(str(exc))


def _apply_fit_resize(w):
    if not w.terminal_fit.isChecked():return
    cid=w.current_id;identifier=w.terminal_sessions.currentData()
    if not cid or not identifier:return
    desired=w.terminal_screen.fitted_grid_size()
    if w._terminal_resize_inflight:
        # One operation may be running in the FIFO executor. Keep only the
        # latest desired size and reconcile after its acknowledgement.
        w._terminal_fit_latest=(cid,identifier,desired)
        return
    current=w._terminal_session_size.get((cid,identifier))
    if current==desired:return
    _queue_fit_resize(w,cid,identifier,desired)


def build_terminal_page(w):
    page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(28,22,28,16);layout.setSpacing(10)
    w._terminal_completions = _TerminalCompletions(w)
    hint=QLabel('Owned interactive sessions · local VT screen. Commands run with your Windows account permissions.')
    hint.setObjectName('subtitle');hint.setWordWrap(True);layout.addWidget(hint)
    row=QHBoxLayout();w.terminal_sessions=QComboBox();w.terminal_sessions.setAccessibleName('Owned terminal session');row.addWidget(w.terminal_sessions,1)
    start=QPushButton('New PowerShell');start.clicked.connect(lambda:start_terminal(w));row.addWidget(start)
    stop=QPushButton('Stop session');stop.clicked.connect(lambda:stop_terminal(w));row.addWidget(stop)
    layout.addLayout(row)
    w.terminal_screen_model=TerminalScreenModel()
    w.terminal_screen=TerminalScreenWidget(w.terminal_screen_model,lambda text,rid:send_terminal(w,text,rid))
    w.terminal_screen.on_resize=lambda:_schedule_fit_resize(w)
    layout.addWidget(w.terminal_screen,1)
    w.terminal_input=QLineEdit();w.terminal_input.setPlaceholderText('Send literal terminal input');w.terminal_input.setAccessibleName('Terminal input')
    w.terminal_input.returnPressed.connect(lambda:send_terminal(w,w.terminal_input.text()+'\r',clear=True))
    input_row=QHBoxLayout();input_row.addWidget(w.terminal_input,1)
    send=QPushButton('Send');send.clicked.connect(lambda:send_terminal(w,w.terminal_input.text()+'\r',clear=True));input_row.addWidget(send)
    interrupt=QPushButton('Ctrl+C');interrupt.clicked.connect(lambda:send_terminal(w,'\x03'));input_row.addWidget(interrupt);layout.addLayout(input_row)
    size_row=QHBoxLayout();rows=QLineEdit('24');cols=QLineEdit('80');rows.setMaximumWidth(70);cols.setMaximumWidth(70)
    size_row.addWidget(QLabel('Rows'));size_row.addWidget(rows);size_row.addWidget(QLabel('Columns'));size_row.addWidget(cols)
    resize=QPushButton('Resize session');size_row.addWidget(resize)
    w.terminal_fit=QCheckBox('Fit terminal to pane');w.terminal_fit.setChecked(True);size_row.addWidget(w.terminal_fit)
    size_row.addStretch();layout.addLayout(size_row)
    w.terminal_rows=rows;w.terminal_cols=cols
    w._terminal_session_size={};w._terminal_resize_inflight=None;w._terminal_fit_latest=None
    w.terminal_resize_timer=QTimer(w);w.terminal_resize_timer.setSingleShot(True);w.terminal_resize_timer.setInterval(200)
    w.terminal_resize_timer.timeout.connect(lambda:_apply_fit_resize(w))
    w.terminal_notice=QLabel('Choose a project conversation to start an interactive session.');w.terminal_notice.setObjectName('subtitle');w.terminal_notice.setWordWrap(True);layout.addWidget(w.terminal_notice)
    def resize_session():
        identifier=w.terminal_sessions.currentData()
        if not identifier:return
        try:r=int(rows.text());c=int(cols.text())
        except ValueError:w.terminal_notice.setText('Rows and columns must be numbers');return
        cid=w.current_id
        w.terminal_notice.setText('Resizing terminal…')
        _submit_terminal(w,'terminal.resize',{'conversation_id':cid,'terminal_id':identifier,'rows':r,'cols':c},cid,
                         lambda result:(w._terminal_session_size.__setitem__((cid,identifier),(r,c)) if result.get('ok') else None,
                                        w.terminal_notice.setText('Terminal resized' if result.get('ok') else result['error']['message'])),terminal_id=identifier)
    def fit_changed(enabled):
        rows.setEnabled(not enabled);cols.setEnabled(not enabled);resize.setEnabled(not enabled)
        if enabled:_schedule_fit_resize(w)
        else:
            w.terminal_resize_timer.stop();w._terminal_fit_latest=None
    w.terminal_fit.toggled.connect(fit_changed)
    fit_changed(True)
    resize.clicked.connect(resize_session);w.terminal_sessions.currentIndexChanged.connect(lambda *_:refresh_terminal(w))
    return page


def _read_vt(w, cid, identifier, session):
    model=w.terminal_screen_model
    reset=False
    if getattr(w,'_terminal_screen_id',None)!=identifier:
        model=TerminalScreenModel(session['rows'],session['cols']);w.terminal_screen_model=model
        w.terminal_screen.model=model;w._terminal_screen_id=identifier;reset=True
    offset=model.offset
    page=w.worker.router.terminals.status(cid,identifier,offset,include_raw=True)
    if page['truncated']:
        model.reset();offset=0;reset=True
        page=w.worker.router.terminals.status(cid,identifier,0,include_raw=True)
    raw=page.get('raw_output','')
    end=page['cursor']
    if raw:model.stream.feed(raw);model.offset=end
    cursor=end
    for _ in range(15):
        if cursor>=page['output_end']:break
        page=w.worker.router.terminals.status(cid,identifier,cursor,include_raw=True)
        if page['cursor']<=cursor:break
        raw=page.get('raw_output','')
        if raw:model.stream.feed(raw);model.offset=page['cursor']
        cursor=page['cursor']
    w.terminal_screen.update_screen(rows=session['rows'],cols=session['cols'])
    if reset:w.terminal_screen.update()


def refresh_terminal(w):
    cid=w.current_id;router=w.worker.router;selected=w.terminal_sessions.currentData()
    sessions=router.terminals.list(cid)
    existing=[w.terminal_sessions.itemData(i) for i in range(w.terminal_sessions.count())]
    ids=[s['terminal_id'] for s in sessions]
    if existing!=ids:
        w.terminal_sessions.blockSignals(True);w.terminal_sessions.clear()
        for session in sessions:w.terminal_sessions.addItem(session['program']+' · '+session['terminal_id'][:8],session['terminal_id'])
        index=w.terminal_sessions.findData(selected);w.terminal_sessions.setCurrentIndex(max(0,index));w.terminal_sessions.blockSignals(False)
    identifier=w.terminal_sessions.currentData()
    if not identifier:
        w._terminal_session_key=None
        w.terminal_screen_model.reset();w.terminal_screen.update();return
    session=next(s for s in sessions if s['terminal_id']==identifier)
    w._terminal_session_size[(cid,identifier)]=(session['rows'],session['cols'])
    session_key=(cid,identifier)
    if getattr(w,'_terminal_session_key',None)!=session_key:
        w._terminal_session_key=session_key
        w._terminal_fit_latest=None
        _schedule_fit_resize(w)
    _read_vt(w,cid,identifier,session)
    w.terminal_notice.setText(session['state']+' · '+str(session['rows'])+'×'+str(session['cols'])+' · input delivery is journaled; uncertain input is not resent')


def start_terminal(w):
    executable=shutil.which('powershell.exe')
    if not executable:w.terminal_notice.setText('Windows PowerShell is unavailable');return
    cid=w.current_id
    w.terminal_notice.setText('Starting terminal…')
    def done(result):
        if result.get('ok'):refresh_terminal(w)
        else:w.terminal_notice.setText(result['error']['message'])
    _submit_terminal(w,'terminal.start',{'conversation_id':cid,'argv':[executable,'-NoLogo','-NoProfile'],'request_id':str(uuid.uuid4())},cid,done)


def send_terminal(w,literal=None,request_id=None,clear=False):
    identifier=w.terminal_sessions.currentData()
    if not identifier or literal is None:return
    cid=w.current_id;shown_text=w.terminal_input.text()
    _submit_terminal(w,'terminal.write',{'conversation_id':cid,'terminal_id':identifier,'text':literal,'request_id':request_id or str(uuid.uuid4())},cid,
        lambda result:(w.terminal_input.clear() if result.get('ok') and clear and w.terminal_input.text()==shown_text else None,
                       w.terminal_notice.setText(result['error']['message']) if not result.get('ok') else None),terminal_id=identifier)


def stop_terminal(w):
    identifier=w.terminal_sessions.currentData()
    if not identifier:return
    cid=w.current_id
    w.terminal_notice.setText('Stopping terminal…')
    def done(result):
        if result.get('ok'):refresh_terminal(w)
        else:w.terminal_notice.setText(result['error']['message'])
    _submit_terminal(w,'terminal.stop',{'conversation_id':cid,'terminal_id':identifier},cid,done,terminal_id=identifier)
