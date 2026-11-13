import json
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import pytest
from PySide6.QtWidgets import QApplication
from musebridge.app import Worker,Window
from musebridge.storage import Store
from musebridge.hook_plugins import parse_manifest,install_disabled
from musebridge.attention import AttentionTracker
from musebridge.overview_ui import refresh_attention


def test_plugin_import_is_validated_disabled_and_does_not_overwrite(tmp_path):
    w=Worker(Store(tmp_path/'state'));project=w.catalog.create_project('Fixture',tmp_path)
    try:
        manifest=parse_manifest(json.dumps({'format_version':1,'name':'formatter','hooks':[{'name':'format','event':'after_tool','tools':['file.write'],'command':'Write-Output configured'}]}).encode())
        installed=install_disabled(w.router.hooks,'project:'+project['id'],manifest)
        assert len(installed)==1 and installed[0]['enabled'] is False
        assert installed[0]['tool_filter']==['file.write']
        with pytest.raises(ValueError,match='already exist'):install_disabled(w.router.hooks,'project:'+project['id'],manifest)
        for invalid in (b'bad JSON',b'{"format_version":9}',b'x'*65537):
            with pytest.raises(ValueError):parse_manifest(invalid)
        assert len(w.router.hooks.list_hooks('project:'+project['id']))==1
        assert w.router.jobs.finished_summary()==[]
    finally:w.close()


def test_attention_cursor_coalesces_and_rate_limits_without_replaying_startup_history(tmp_path):
    w=Worker(Store(tmp_path/'state'))
    try:
        a=w.catalog.create_conversation('A')['id'];b=w.catalog.create_conversation('B')['id']
        w.catalog.event(a,'run.remote_finished',{})
        tracker=AttentionTracker(w);assert tracker.poll(0) is None
        w.catalog.event(a,'run.remote_finished',{});w.catalog.event(b,'run.unknown',{})
        notice=tracker.poll(1);assert notice=={'conversation_id':'__overview__','count':2}
        assert tracker.poll(2) is None
        w.catalog.event(a,'run.remote_reply_finished',{})
        assert tracker.poll(3) is None
        assert tracker.poll(16)=={'conversation_id':a,'count':1}
        assert tracker.poll(30) is None
    finally:w.close()


def test_desktop_notifications_are_opt_in_and_periodic_attention_never_focuses_window(tmp_path):
    app=QApplication.instance() or QApplication([]);window=Window(Worker(Store(tmp_path/'state')))
    calls=[]
    class FakeNotifier:
        def show(self,*args):calls.append(args);return True
        def close(self):return True
    window.desktop_notifier=FakeNotifier()
    try:
        cid=window.worker.catalog.create_conversation('Background')['id']
        window.worker.catalog.event(cid,'run.remote_finished',{})
        refresh_attention(window);assert calls==[] and not window.desktop_notifications.isChecked()
        window.desktop_notifications.setChecked(True)
        refresh_attention(window);assert calls==[]  # enabling does not show old history
        window.worker.catalog.event(cid,'run.remote_reply_finished',{})
        refresh_attention(window);assert len(calls)==1 and calls[0][0]==cid
        assert 'Background' not in calls[0][2] and window.current_id=='main'
        refresh_attention(window);assert len(calls)==1
    finally:window.close()
