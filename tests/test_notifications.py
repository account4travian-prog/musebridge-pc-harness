import ctypes

from musebridge.notifications import (
    DesktopNotifications, NIIF_NOSOUND, NIIF_RESPECT_QUIET_TIME,
    NIF_INFO, NIM_ADD, NIM_DELETE, NIM_MODIFY, NIM_SETVERSION,
    NOTIFYICONDATAW,
)


class FakeShell:
    def __init__(self):
        self.calls=[]
        self.fail_modify=False
        self.fail_version=False

    def application_icon(self):
        return 123

    def notify(self, command, data):
        saved=NOTIFYICONDATAW()
        ctypes.memmove(ctypes.byref(saved),ctypes.byref(data),ctypes.sizeof(saved))
        self.calls.append((command,saved))
        return not ((command==NIM_MODIFY and self.fail_modify) or (command==NIM_SETVERSION and self.fail_version))


class FakeApp:
    def __init__(self):self.installed=[];self.removed=[]
    def installNativeEventFilter(self, event_filter):self.installed.append(event_filter)
    def removeNativeEventFilter(self, event_filter):self.removed.append(event_filter)


class FakeWindow:
    def winId(self):return 0x123456


def test_import_and_construction_are_inert_until_show_is_called():
    shell=FakeShell();app=FakeApp();opened=[]
    service=DesktopNotifications(FakeWindow(),opened.append,shell_api=shell,app=app)
    assert shell.calls==[] and app.installed==[] and opened==[]


def test_show_registers_version4_icon_and_silent_respecting_balloon():
    shell=FakeShell();app=FakeApp();opened=[]
    service=DesktopNotifications(FakeWindow(),opened.append,shell_api=shell,app=app)
    assert service.show('conversation-1','A title','A body')
    assert [call[0] for call in shell.calls]==[NIM_ADD,NIM_SETVERSION,NIM_MODIFY]
    assert shell.calls[0][1].cbSize==ctypes.sizeof(NOTIFYICONDATAW)
    assert shell.calls[1][1].uVersion==4
    balloon=shell.calls[2][1]
    assert balloon.uFlags==NIF_INFO
    assert balloon.dwInfoFlags==NIIF_NOSOUND|NIIF_RESPECT_QUIET_TIME
    assert balloon.szInfoTitle=='A title' and balloon.szInfo=='A body'
    assert len(app.installed)==1
    assert not opened


def test_balloon_click_calls_only_conversation_callback_and_close_removes_owned_icon():
    shell=FakeShell();app=FakeApp();opened=[]
    service=DesktopNotifications(FakeWindow(),opened.append,shell_api=shell,app=app)
    assert service.show('conversation-2','Generic','New activity')
    packed=(service._icon_id << 16)|0x0406
    assert not service._handle_native(service._callback_message,0x12340000,packed)
    packed=(service._icon_id << 16)|0x0405
    assert service._handle_native(service._callback_message,0x12340000,packed)
    assert opened==['conversation-2']
    assert service.close()
    assert shell.calls[-1][0]==NIM_DELETE
    assert app.removed==app.installed


def test_balloon_text_is_bounded_and_failed_shell_delivery_returns_false():
    shell=FakeShell();app=FakeApp()
    service=DesktopNotifications(FakeWindow(),lambda _cid:None,shell_api=shell,app=app)
    shell.fail_modify=True
    assert not service.show('conversation-3','T'*100+'\n','B'*300+'\x00')
    title=shell.calls[-1][1].szInfoTitle
    body=shell.calls[-1][1].szInfo
    assert len(title)==63 and '\n' not in title
    assert len(body)==255 and '\x00' not in body
    assert service._cid is None


def test_version4_setup_failure_removes_icon_and_refuses_notification():
    shell=FakeShell();shell.fail_version=True;app=FakeApp()
    service=DesktopNotifications(FakeWindow(),lambda _cid:None,shell_api=shell,app=app)
    assert not service.show('conversation','Title','Body')
    assert [call[0] for call in shell.calls]==[NIM_ADD,NIM_SETVERSION,NIM_DELETE]
    assert not app.installed


def test_replacing_balloon_clears_old_before_committing_new_route():
    shell=FakeShell();app=FakeApp();opened=[]
    service=DesktopNotifications(FakeWindow(),opened.append,shell_api=shell,app=app)
    assert service.show('first','Title','Body')
    old_icon_id=service._icon_id
    assert service.show('second','Title','Body')
    assert shell.calls[-5][1].szInfo==''
    assert service._cid=='second'
    assert service._icon_id!=old_icon_id
    old_click=(old_icon_id << 16)|0x0405
    assert not service._handle_native(service._callback_message,0,old_click)
    assert opened==[]
    packed=(service._icon_id << 16)|0x0405
    service._handle_native(service._callback_message,0,packed)
    assert opened==['second']


def test_notifyicondata_v4_struct_layout_has_expected_windows_x64_offsets():
    assert ctypes.sizeof(NOTIFYICONDATAW)==976
    assert NOTIFYICONDATAW.szInfo.offset==304
    assert NOTIFYICONDATAW.guidItem.offset==952
    assert NOTIFYICONDATAW.hBalloonIcon.offset==968
