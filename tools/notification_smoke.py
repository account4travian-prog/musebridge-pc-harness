"""One synthetic silent native notification; no account, focus or settings changes."""
import json
from pathlib import Path
import time
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication,QWidget
from musebridge.notifications import DesktopNotifications


def main():
    app=QApplication([]);window=QWidget();window.setWindowTitle('MuseBridge notification fixture')
    report={'shell_accepted':False,'clicked':False,'closed':False}
    service=DesktopNotifications(window,lambda cid:report.update(clicked=True))
    report['shell_accepted']=service.show('synthetic-notification','MuseBridge notification test','This is a silent test notification. No action is needed.')
    def finish():
        report['closed']=service.close();app.quit()
    QTimer.singleShot(4500,finish);app.exec();window.close()
    target=Path(__file__).resolve().parents[1]/'.local'/('notification-smoke-'+str(int(time.time()))+'.json')
    target.write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)
    return report['shell_accepted'] and report['closed']


if __name__=='__main__':raise SystemExit(0 if main() else 1)
