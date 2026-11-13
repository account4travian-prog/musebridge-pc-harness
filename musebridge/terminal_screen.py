"""A bounded, local VT screen model and native Qt terminal widget.

pyte implements the VT screen state. This deliberately does not execute OSC
commands, links, clipboard requests, or terminal host control sequences.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from copy import copy

import pyte
from pyte.screens import Cursor, StaticDefaultDict
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeyEvent, QPainter
from PySide6.QtWidgets import QWidget


class _VTScreen(pyte.Screen):
    """pyte screen with the common DEC alternate-screen mode implemented."""
    _ALT_MODES = {47, 1047, 1049}

    def __init__(self, columns, lines):
        super().__init__(columns, lines)
        self._primary_state = None

    def _enter_alternate(self):
        if self._primary_state is not None:
            return
        self._primary_state = (self.buffer, self.cursor, self.mode.copy(), self.margins,
                               self.savepoints, self.charset, self.g0_charset,
                               self.g1_charset, self.tabstops)
        self.buffer = defaultdict(lambda: StaticDefaultDict(self.default_char))
        self.cursor = Cursor(0, 0)
        self.margins = None
        self.savepoints = []
        self.charset = 0
        self.g0_charset = pyte.screens.cs.LAT1_MAP
        self.g1_charset = pyte.screens.cs.VT100_MAP
        self.tabstops = set(range(8, self.columns, 8))
        self.dirty.update(range(self.lines))

    def _leave_alternate(self):
        if self._primary_state is None:
            return
        (self.buffer, self.cursor, self.mode, self.margins, self.savepoints,
         self.charset, self.g0_charset, self.g1_charset, self.tabstops) = self._primary_state
        self._primary_state = None
        self.dirty.update(range(self.lines))

    def set_mode(self, *modes, **kwargs):
        private = kwargs.get('private', False)
        if private and self._ALT_MODES.intersection(modes):
            self._enter_alternate()
        super().set_mode(*modes, **kwargs)

    def reset_mode(self, *modes, **kwargs):
        private = kwargs.get('private', False)
        leave = private and bool(self._ALT_MODES.intersection(modes))
        super().reset_mode(*modes, **kwargs)
        if leave:
            self._leave_alternate()


class TerminalScreenModel:
    """Incremental VT parser with explicit restart/truncation recovery."""

    def __init__(self, rows=24, cols=80):
        self.rows = max(5, min(200, int(rows)))
        self.cols = max(20, min(300, int(cols)))
        self.reset()

    def reset(self):
        self.screen = _VTScreen(self.cols, self.rows)
        self.stream = pyte.Stream(self.screen)
        self.offset = 0

    def feed(self, text, end_offset=None):
        if text:
            self.stream.feed(text)
            self.offset = end_offset if end_offset is not None else self.offset + len(text)

    def resize(self, rows, cols):
        self.rows = max(5, min(200, int(rows)))
        self.cols = max(20, min(300, int(cols)))
        self.screen.resize(lines=self.rows, columns=self.cols)

    @property
    def cursor_visible(self):
        return bool(getattr(self.screen, 'cursor', None) and self.screen.cursor.hidden is False)

    @property
    def alternate_screen(self):
        return self.screen._primary_state is not None

    def key_sequence(self, key, modifiers=Qt.NoModifier, text=''):
        """Return bytes/text for standard terminal keys and Ctrl chords."""
        ctrl = bool(modifiers & Qt.ControlModifier)
        alt = bool(modifiers & Qt.AltModifier)
        shift = bool(modifiers & Qt.ShiftModifier)
        sequence = None
        arrows = {Qt.Key_Up: 'A', Qt.Key_Down: 'B', Qt.Key_Right: 'C', Qt.Key_Left: 'D'}
        if key in arrows:
            modifier = 1 + int(shift) + 2 * int(alt) + 4 * int(ctrl)
            if modifier > 1:
                sequence = f'\x1b[1;{modifier}{arrows[key]}'
            else:
                sequence = ('\x1bO' if 32 in self.screen.mode else '\x1b[') + arrows[key]
        elif key in (Qt.Key_Home, Qt.Key_End):
            sequence = '\x1b[H' if key == Qt.Key_Home else '\x1b[F'
        elif key in (Qt.Key_Insert, Qt.Key_Delete, Qt.Key_PageUp, Qt.Key_PageDown):
            sequence = {Qt.Key_Insert:'\x1b[2~',Qt.Key_Delete:'\x1b[3~',Qt.Key_PageUp:'\x1b[5~',Qt.Key_PageDown:'\x1b[6~'}[key]
        elif Qt.Key_F1 <= key <= Qt.Key_F12:
            sequence = {Qt.Key_F1:'\x1bOP',Qt.Key_F2:'\x1bOQ',Qt.Key_F3:'\x1bOR',Qt.Key_F4:'\x1bOS',Qt.Key_F5:'\x1b[15~',Qt.Key_F6:'\x1b[17~',Qt.Key_F7:'\x1b[18~',Qt.Key_F8:'\x1b[19~',Qt.Key_F9:'\x1b[20~',Qt.Key_F10:'\x1b[21~',Qt.Key_F11:'\x1b[23~',Qt.Key_F12:'\x1b[24~'}[key]
        elif key in (Qt.Key_Return, Qt.Key_Enter): sequence='\r'
        elif key == Qt.Key_Backspace: sequence='\x7f'
        elif key == Qt.Key_Tab: sequence='\x1b[Z' if shift else '\t'
        elif key == Qt.Key_Escape: sequence='\x1b'
        elif ctrl and Qt.Key_A <= key <= Qt.Key_Z:
            sequence=chr(int(key)-int(Qt.Key_A)+1)
        elif ctrl and key in (Qt.Key_Space, Qt.Key_At): sequence='\x00'
        elif ctrl and key in (Qt.Key_BracketLeft,): sequence='\x1b'
        elif ctrl and key == Qt.Key_Backslash: sequence='\x1c'
        elif ctrl and key == Qt.Key_BracketRight: sequence='\x1d'
        elif ctrl and key == Qt.Key_AsciiCircum: sequence='\x1e'
        elif ctrl and key == Qt.Key_Underscore: sequence='\x1f'
        elif ctrl and key == Qt.Key_Question: sequence='\x7f'
        elif text and not (modifiers & (Qt.ControlModifier | Qt.MetaModifier)):
            sequence=text
        if sequence is not None and alt and not sequence.startswith('\x1b'):
            sequence='\x1b'+sequence
        return sequence


_ANSI = {'black':'#000000','red':'#cc3333','green':'#22aa55','brown':'#aa8800',
         'blue':'#3366cc','magenta':'#bb55bb','cyan':'#22aaaa','white':'#cccccc',
         'brightblack':'#666666','brightred':'#ff5555','brightgreen':'#55ff55','brightbrown':'#ffff55',
         'brightblue':'#5555ff','brightmagenta':'#ff55ff','brightcyan':'#55ffff','brightwhite':'#ffffff'}


def _color(value, fallback):
    if not value or value == 'default': return QColor(fallback)
    if value in _ANSI: return QColor(_ANSI[value])
    if isinstance(value, str) and value.startswith('#'): return QColor(value)
    if isinstance(value, str) and len(value)==6 and all(ch in '0123456789abcdefABCDEF' for ch in value):
        return QColor('#'+value)
    try:
        n=int(value)
        if n < 16: return QColor(list(_ANSI.values())[n])
        if n < 232:
            n-=16; levels=(0,95,135,175,215,255)
            return QColor(levels[n//36],levels[(n//6)%6],levels[n%6])
        g=8+(n-232)*10;return QColor(g,g,g)
    except (ValueError, TypeError): return QColor(fallback)


class TerminalScreenWidget(QWidget):
    """Native painted VT grid. Input is handed to caller for journaled writes."""
    def __init__(self, model=None, on_input=None, parent=None):
        super().__init__(parent)
        self.model=model or TerminalScreenModel()
        self.on_input=on_input
        self.on_resize=None
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName('Interactive terminal screen')
        self.setMinimumSize(360, 180)
        self.setStyleSheet('background:#101216;')

    def fitted_grid_size(self):
        """Rows and columns visible at the renderer's current font metrics."""
        metrics=QFontMetrics(QFont('Consolas',12))
        cell_width=max(1,metrics.horizontalAdvance('M'))
        cell_height=max(1,metrics.height())
        rows=max(5,min(200,self.height()//cell_height))
        cols=max(20,min(300,self.width()//cell_width))
        return rows,cols

    def update_screen(self, text='', rows=None, cols=None, reset=False, end_offset=None):
        if reset: self.model.reset()
        if rows is not None and cols is not None: self.model.resize(rows, cols)
        self.model.feed(text, end_offset=end_offset)
        self.update()

    def paintEvent(self, _event):
        screen=self.model.screen; painter=QPainter(self)
        painter.fillRect(self.rect(), QColor('#101216'))
        font=QFont('Consolas', 12);painter.setFont(font);fm=painter.fontMetrics()
        cw=max(1,fm.horizontalAdvance('M'));ch=max(1,fm.height())
        max_cols=min(self.model.cols,max(1,self.width()//cw)); max_rows=min(self.model.rows,max(1,self.height()//ch))
        for y in range(max_rows):
            line=screen.buffer.get(y,{})
            for x in range(max_cols):
                cell=line.get(x)
                if not cell: continue
                bg=_color(cell.bg,'#101216');fg=_color(cell.fg,'#d8dee9')
                if cell.reverse:fg,bg=bg,fg
                rect=(x*cw,y*ch,cw,ch)
                if bg.name()!='#101216':painter.fillRect(*rect,bg)
                painter.setPen(fg)
                style=QFont(font);style.setBold(cell.bold);style.setItalic(cell.italics);style.setUnderline(cell.underscore)
                painter.setFont(style);painter.drawText(x*cw,(y+1)*ch-fm.descent(),cell.data if cell.data!=' ' else ' ')
        cursor=screen.cursor
        if self.hasFocus() and self.model.cursor_visible and cursor.y<max_rows and cursor.x<max_cols:
            painter.fillRect(cursor.x*cw,cursor.y*ch+ch-2,cw,2,QColor('#f2f4f8'))
        painter.end()

    def keyPressEvent(self, event: QKeyEvent):
        seq=self.model.key_sequence(event.key(),event.modifiers(),event.text())
        if seq is not None and self.on_input:
            self.on_input(seq,uuid.uuid4().hex)
            event.accept();return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.on_resize:
            self.on_resize()
