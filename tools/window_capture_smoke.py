"""Capture two owned non-activating synthetic windows, never other applications."""
from io import BytesIO
import json
import os
from pathlib import Path
import threading
import time
from PIL import Image
from PySide6.QtCore import Qt,QTimer,Signal,QObject
from PySide6.QtGui import QColor,QPainter
from PySide6.QtWidgets import QApplication,QWidget
from musebridge.desktop import _capture_window_printwindow


class PaintFixture(QWidget):
    def __init__(self,color):
        super().__init__(None,Qt.WindowType.Tool|Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating,True)
        self.setWindowTitle('Owned MuseBridge capture fixture');self.color=color;self.resize(480,280);self.move(40,40)
    def paintEvent(self,event):
        painter=QPainter(self);painter.fillRect(self.rect(),QColor(*self.color));painter.end()


class Result(QObject):
    done=Signal(dict)


def main():
    app=QApplication([]);target=PaintFixture((210,30,40));cover=PaintFixture((20,210,40));result=Result();report={}
    target.show();cover.show()
    hwnd=int(target.winId())
    def capture():
        try:
            data=_capture_window_printwindow(hwnd,os.getpid())
            image=Image.open(BytesIO(data)).convert('RGB');r,g,b=image.getpixel((image.width//2,image.height//2))
            result.done.emit({'target_pixels_verified':r>170 and g<70 and b<85,'width':image.width,'height':image.height})
        except Exception as exc:result.done.emit({'target_pixels_verified':False,'error_code':type(exc).__name__})
    def finish(value):
        report.update(value);target.close();cover.close();app.quit()
    result.done.connect(finish);QTimer.singleShot(500,lambda:threading.Thread(target=capture,daemon=True).start())
    QTimer.singleShot(12000,lambda:finish({'target_pixels_verified':False,'error_code':'SupervisorTimeout'}))
    app.exec();path=Path(__file__).resolve().parents[1]/'.local'/('window-capture-smoke-'+str(int(time.time()))+'.json')
    path.write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)
    return report.get('target_pixels_verified',False)


if __name__=='__main__':raise SystemExit(0 if main() else 1)
