"""Shared visual tokens and native widgets for the MuseBridge shell."""
import html
import re
from PySide6.QtCore import Qt,Signal,QByteArray
from PySide6.QtGui import QIcon,QPixmap,QPainter,QTextDocument
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QPlainTextEdit,QToolButton,QTreeWidget,QStyle,QStyleOptionViewItem

COLORS={'canvas':'#ffffff','sidebar':'#f7f8fa','ink':'#20242b','muted':'#7a818f',
        'line':'#e8eaee','accent':'#5179e8','selection':'#e9eefb'}

STYLE='''
QMainWindow,QDialog { background:#ffffff; color:#20242b; }
QWidget { font-family:"Segoe UI"; font-size:13px; color:#20242b; }
QMenuBar { background:#f7f8fa; padding:3px 10px; color:#626b79; }
QMenuBar::item { padding:5px 10px; background:transparent; }
QMenuBar::item:selected,QMenu::item:selected { background:#e9eefb; border-radius:5px; }
QMenu { background:white; border:1px solid #e4e7ec; padding:6px; }
QMenu::item { padding:8px 24px 8px 12px; border-radius:5px; }
QFrame#sidebar { background:#f7f8fa; border:0; }
QLabel#brand { font-size:23px; font-weight:650; letter-spacing:-0.5px; }
QLabel#brandTag { color:#7a818f; font-size:9px; font-weight:600; letter-spacing:2px; }
QLabel#section { color:#87909e; font-size:12px; }
QPushButton,QToolButton { background:transparent; border:0; border-radius:7px; padding:7px 10px; }
QPushButton:hover,QToolButton:hover { background:#edf0f5; }
QPushButton:pressed,QToolButton:pressed { background:#e3e8f1; }
QPushButton:disabled,QToolButton:disabled { color:#afb6c2; }
QPushButton:focus,QToolButton:focus { border:1px solid #b9c8f2; }
QPushButton#newChat { background:white; border:1px solid #dce0e6; padding:10px; font-weight:600; }
QPushButton#newChat:hover { border-color:#b9c7ee; background:#fcfdff; }
QTreeWidget#navigation { background:transparent; border:0; outline:0; selection-background-color:#e9ecf1; selection-color:#20242b; }
QTreeWidget#navigation::item { min-height:32px; border-radius:7px; padding:1px 6px; }
QTreeWidget#navigation::item:selected { background:#e9ecf1; color:#20242b; }
QTreeWidget#navigation::item:hover { background:#edf0f4; }
QLineEdit { border:1px solid #e2e5ea; border-radius:7px; padding:7px 9px; background:white; selection-background-color:#dce6ff; }
QLineEdit#search { background:#f0f2f5; border:0; padding:8px 10px; }
QLineEdit:focus,QPlainTextEdit#planEditor:focus { border:1px solid #a4b9f1; }
QFrame#header { border:0; border-bottom:1px solid #edf0f3; background:white; }
QLabel#title { font-size:15px; font-weight:600; }
QLabel#subtitle { color:#818997; font-size:11px; }
QLabel#emptyTitle { font-size:25px; font-weight:600; letter-spacing:-0.4px; }
QLabel#emptyHint { color:#8a919d; font-size:13px; }
QTextBrowser#transcript { border:0; background:white; selection-background-color:#dce6ff; }
QFrame#composer { background:white; border:1px solid #e3e6ed; border-radius:21px; }
QPlainTextEdit#composerInput { background:transparent; border:0; padding:0; font-size:14px; selection-background-color:#dce6ff; }
QPushButton#send { background:#5179e8; color:white; font-size:22px; border-radius:17px; padding:0; }
QPushButton#send:hover { background:#4169d8; }
QPushButton#send:disabled { background:#c4d2f5; color:white; }
QToolButton#scope { color:#6b7381; font-size:11px; }
QLabel#footer { color:#9097a3; font-size:11px; }
QLabel#notice { color:#707989; font-size:12px; }
QTabBar::tab { background:transparent; border:0; border-bottom:2px solid transparent; padding:10px 4px 9px; margin-right:22px; color:#7b8391; }
QTabBar::tab:selected { color:#5179e8; border-bottom:2px solid #5179e8; }
QTabBar::tab:hover { color:#364154; }
QTreeWidget#activity { border:0; background:white; alternate-background-color:#fafbfc; }
QHeaderView::section { background:#fafbfc; border:0; border-bottom:1px solid #e8eaee; padding:9px; color:#7a818f; }
QPlainTextEdit#planEditor { border:1px solid #e2e5ea; border-radius:8px; background:#fcfcfd; padding:10px; }
QComboBox { border:1px solid #e2e5ea; border-radius:6px; padding:6px 9px; background:white; }
QScrollBar:vertical { background:transparent; width:8px; margin:3px 0; }
QScrollBar::handle:vertical { background:#d5d9e0; border-radius:4px; min-height:35px; }
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical { background:none; }
QSplitter::handle { background:#f0f1f4; width:1px; }
'''

PATHS={
 'plus':'M12 5v14M5 12h14', 'chat':'M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-5 3V6a2 2 0 0 1 1-2Z',
 'folder':'M3 7V5h7l2 2h9v12H3Z', 'search':'M16 16l5 5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z',
 'plan':'M9 5h11M9 12h11M9 19h11M3 5h1M3 12h1M3 19h1',
 'activity':'M3 12h4l3-8 4 16 3-8h4', 'more':'M5 12h.01M12 12h.01M19 12h.01',
 'sidebar':'M3 4h18v16H3ZM9 4v16', 'mic':'M9 5a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0ZM6 10v2a6 6 0 0 0 12 0v-2M12 18v4M8 22h8',
 'stop':'M6 6h12v12H6Z','copy':'M8 8h12v13H8ZM4 16V3h12',
 'settings':'M4 7h16M4 17h16M8 4v6M16 14v6', 'arrow':'M5 12h14M13 6l6 6-6 6',
 'export':'M12 16V3M7 8l5-5 5 5M4 15v6h16v-6',
 'attach':'M8 13l7-7a3 3 0 0 1 4 4l-9 9a5 5 0 0 1-7-7l9-9M6 15l8-8',
}


def icon(name,color='#7a818f',size=18):
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path d="{PATHS[name]}" fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    pixmap=QPixmap(size*2,size*2);pixmap.fill(Qt.GlobalColor.transparent)
    painter=QPainter(pixmap);QSvgRenderer(QByteArray(svg.encode())).render(painter);painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def tool_button(label,symbol,callback,parent=None):
    button=QToolButton(parent);button.setIcon(icon(symbol));button.setToolTip(label)
    button.setAccessibleName(label);button.clicked.connect(callback);return button


class Composer(QPlainTextEdit):
    sendRequested=Signal()
    imagePasted=Signal(object)
    def insertFromMimeData(self,source):
        if source.hasImage():self.imagePasted.emit(source.imageData())
        else:super().insertFromMimeData(source)
    def keyPressEvent(self,event):
        if event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            if not event.modifiers() & (Qt.KeyboardModifier.AltModifier|Qt.KeyboardModifier.MetaModifier):
                self.sendRequested.emit();event.accept();return
        super().keyPressEvent(event)


class NavigationTree(QTreeWidget):
    def drawBranches(self,painter,rect,index):
        # Groups stay expanded; omit native branch chrome in this flat sidebar.
        pass

    def drawRow(self,painter,option,index):
        option=QStyleOptionViewItem(option)
        option.state &= ~QStyle.StateFlag.State_HasFocus
        super().drawRow(painter,option,index)


def markdown_fragment(text):
    # Strip arbitrary user-provided HTML/images before parsing Markdown.
    document=QTextDocument()
    text=re.sub(r'!\[([^\]]*)\]\([^)]*\)',r'[Image: \1]',text)
    document.setMarkdown(text,QTextDocument.MarkdownFeature.MarkdownDialectGitHub|
                         QTextDocument.MarkdownFeature.MarkdownNoHTML)
    rendered=document.toHtml()
    return rendered.split('<body',1)[-1].split('>',1)[-1].rsplit('</body>',1)[0]
