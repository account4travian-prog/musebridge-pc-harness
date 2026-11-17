"""Read/capture one owned, non-activating secondary-display window at a Qt scale.

No input is injected and Windows display settings are not changed. This exercises
scaled fixture geometry, not actual mixed-monitor input acceptance.
"""
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import threading

from PIL import Image
from PySide6.QtCore import Qt,QTimer,QObject,Signal
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QLabel,QLineEdit,QPushButton

from musebridge.desktop import DesktopTools,_capture_window_printwindow


class Result(QObject):
    done=Signal(dict)


def main():
    app=QApplication([])
    screens=app.screens()
    if len(screens)<2:
        print(json.dumps({'passed':False,'error_code':'SecondaryDisplayRequired'}))
        return 1
    window=QWidget(None,Qt.WindowType.Tool|Qt.WindowType.WindowDoesNotAcceptFocus)
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating,True)
    window.setWindowTitle('Owned MuseBridge DPI fixture')
    layout=QVBoxLayout(window)
    layout.addWidget(QLabel('MuseBridge scale fixture'))
    edit=QLineEdit('Geometry 593271');edit.setAccessibleName('DPI value')
    layout.addWidget(edit)
    layout.addWidget(QPushButton('Fixture button'))
    window.resize(480,240)
    area=screens[1].availableGeometry()
    window.move(area.x()+40,area.y()+40)
    window.show()
    hwnd=int(window.winId())
    result=Result();report={}
    def inspect():
        try:
            with tempfile.TemporaryDirectory(prefix='MuseBridge-dpi-') as directory:
                tool=DesktopTools(directory);tool.ocr_enabled=False
                snap=tool.call('desktop.snapshot',{'conversation_id':'owned-dpi-fixture','window_handle':hwnd})
                data=_capture_window_printwindow(hwnd,os.getpid())
                image=Image.open(BytesIO(data))
                rect=snap['window']['rectangle']
                geometry_matches=(abs(rect['right']-rect['left']-image.width)<=2 and
                                  abs(rect['bottom']-rect['top']-image.height)<=2)
                value_seen=any('593271' in node.get('value','') or '593271' in node.get('name','')
                               for node in snap['controls'])
                controls_inside=all(rect['left']<=node['rectangle']['left']<=node['rectangle']['right']<=rect['right'] and
                                    rect['top']<=node['rectangle']['top']<=node['rectangle']['bottom']<=rect['bottom']
                                    for node in snap['controls'])
                result.done.emit({'passed':geometry_matches and value_seen and controls_inside,
                    'qt_scale':float(os.environ.get('QT_SCALE_FACTOR','1')),
                    'secondary_display':True,'input_injected':False,'uia_value_verified':value_seen,
                    'rectangles_inside_window':controls_inside,'capture_geometry_matches':geometry_matches,
                    'capture_width':image.width,'capture_height':image.height})
        except Exception as exc:
            result.done.emit({'passed':False,'error_code':type(exc).__name__})
    def finish(value):
        if report:return
        report.update(value);window.close();app.quit()
    result.done.connect(finish)
    QTimer.singleShot(600,lambda:threading.Thread(target=inspect,daemon=True).start())
    QTimer.singleShot(25000,lambda:finish({'passed':False,'error_code':'SupervisorTimeout'}))
    app.exec()
    print(json.dumps(report),flush=True)
    return 0 if report.get('passed') else 1


if __name__=='__main__':raise SystemExit(main())
