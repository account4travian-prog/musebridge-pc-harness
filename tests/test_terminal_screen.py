from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics, QResizeEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from concurrent.futures import Future
from types import SimpleNamespace

from musebridge.terminal_ui import _TerminalCompletions, _submit_terminal
from musebridge.terminal_screen import TerminalScreenModel, TerminalScreenWidget, _color


def test_vt_screen_tracks_cursor_colors_unicode_and_alternate_buffer():
    model=TerminalScreenModel(6,30)
    model.feed('hello Ω\x1b[2;4H\x1b[31;1m界\x1b[0m\x1b[?25l')
    assert model.screen.buffer[0][0].data=='h'
    assert model.screen.buffer[0][6].data=='Ω'
    cell=model.screen.buffer[1][3]
    assert cell.data=='界' and cell.fg=='red' and cell.bold
    assert model.screen.cursor.x==5 and model.screen.cursor.y==1
    assert not model.cursor_visible
    model.feed('\x1b[?25h')
    assert model.cursor_visible
    model.feed('\x1b[?1049h')
    assert model.alternate_screen
    model.feed('ALT')
    assert model.screen.buffer[0][0].data=='A'
    model.feed('\x1b[?1049l')
    assert not model.alternate_screen
    assert model.screen.buffer[0][0].data=='h'


def test_resize_and_keyboard_sequences_cover_editor_keys_and_control_chords():
    model=TerminalScreenModel(24,80)
    assert model.key_sequence(Qt.Key_Up)=='\x1b[A'
    assert model.key_sequence(Qt.Key_F5)=='\x1b[15~'
    assert model.key_sequence(Qt.Key_Delete)=='\x1b[3~'
    assert model.key_sequence(Qt.Key_C,Qt.ControlModifier,'c')=='\x03'
    assert model.key_sequence(Qt.Key_X,Qt.AltModifier,'x')=='\x1bx'
    model.feed('\x1b[?1h')
    assert model.key_sequence(Qt.Key_Left)=='\x1bOD'
    assert model.key_sequence(Qt.Key_Up,Qt.ControlModifier)=='\x1b[1;5A'
    model.resize(35,110)
    assert (model.screen.lines,model.screen.columns)==(35,110)


def test_unknown_and_control_sequences_are_not_rendered_as_text():
    model=TerminalScreenModel(5,20)
    model.feed('A\x1b]52;c;clipboard-data\x07B\x1b]8;;https://example.invalid\x07link\x1b]8;;\x07')
    line=''.join(model.screen.buffer[0][x].data for x in range(20)).rstrip()
    assert line=='ABlink'


def test_truecolor_hex_values_from_pyte_render_as_rgb():
    model=TerminalScreenModel(5,20)
    model.feed('\x1b[38;2;255;0;0mR')
    cell=model.screen.buffer[0][0]
    assert cell.fg=='ff0000'
    assert _color(cell.fg,'#000000').name()=='#ff0000'


def test_qtest_ctrl_c_and_ctrl_d_reach_terminal_as_control_bytes():
    app=QApplication.instance() or QApplication([])
    received=[]
    widget=TerminalScreenWidget(on_input=lambda value,_rid:received.append(value))
    QTest.keyClick(widget,Qt.Key_C,Qt.ControlModifier)
    QTest.keyClick(widget,Qt.Key_D,Qt.ControlModifier)
    assert received==['\x03','\x04']


def test_terminal_tool_submission_returns_before_future_and_does_not_retarget_completion():
    app=QApplication.instance() or QApplication([])
    future=Future()
    class Worker:
        def __init__(self):self.calls=[]
        def submit_terminal(self,name,params):
            self.calls.append((name,dict(params)))
            return future
    class Sessions:
        selected='term-1'
        def currentData(self):return self.selected
    worker=Worker();completed=[]
    window=SimpleNamespace(current_id='chat-1',worker=worker,terminal_sessions=Sessions(),_terminal_completions=_TerminalCompletions())
    params={'conversation_id':'chat-1','terminal_id':'term-1','text':'\x03','request_id':'request-1'}
    result=_submit_terminal(window,'terminal.write',params,'chat-1',completed.append,terminal_id='term-1')
    assert result is future and not future.done()
    assert worker.calls==[('terminal.write',params)]
    window.current_id='chat-2'
    future.set_result({'ok':True})
    app.processEvents()
    assert completed==[]


def test_pane_grid_uses_painted_consolas_metrics_and_keeps_key_state():
    app=QApplication.instance() or QApplication([])
    widget=TerminalScreenWidget()
    dimensions=[]
    widget.on_resize=lambda:dimensions.append(widget.fitted_grid_size())
    widget.resize(720,360)
    widget.resizeEvent(QResizeEvent(widget.size(),widget.size()))
    metrics=QFontMetrics(QFont('Consolas',12))
    expected=(max(5,min(200,widget.height()//metrics.height())),
              max(20,min(300,widget.width()//metrics.horizontalAdvance('M'))))
    assert widget.fitted_grid_size()==expected
    assert dimensions and dimensions[-1]==expected
    assert widget.model.key_sequence(Qt.Key_Right)=='\x1b[C'
