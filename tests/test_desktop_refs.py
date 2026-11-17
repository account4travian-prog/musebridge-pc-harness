import pytest

from test_desktop import guarded_desktop
from musebridge.desktop_refs import DesktopStaleRef,key_chord


def test_refused_desktop_action_reports_no_effect_and_explicit_handoff(guarded_desktop):
    from musebridge.desktop import DesktopActionRefused
    from musebridge.tool_results import normalize_error
    tool,root,edit,button,state=guarded_desktop
    state['idle']=0
    with pytest.raises(DesktopActionRefused) as caught:
        tool.call('desktop.act',{'conversation_id':'c','window_handle':100,
            'action':'invoke','automation_id':'saveButton'})
    assert caught.value.no_effect and button.invoked==0
    error=normalize_error(caught.value,'desktop.act')
    assert error['code']=='desktop_unavailable'
    assert 'do not bypass' in error['hint']


def test_snapshot_uses_outer_capture_bounds_instead_of_uia_client_bounds(guarded_desktop,monkeypatch):
    tool,root,edit,button,state=guarded_desktop
    tool.ocr_enabled=False
    outer={'left':-7,'top':-29,'right':109,'bottom':60}
    monkeypatch.setattr(tool,'_window_rect',lambda *_:outer)
    snapshot=tool.call('desktop.snapshot',{'conversation_id':'c','window_handle':100})
    assert snapshot['window']['rectangle']==outer
    assert snapshot['controls'][0]['rectangle']!=outer


def test_snapshot_refs_click_diff_text_and_explicit_snapshot_invalidation(guarded_desktop):
    tool,root,edit,button,state=guarded_desktop
    tool.ocr_enabled=False
    params={'conversation_id':'c','window_handle':100}
    snapshot=tool.call('desktop.snapshot',params)
    edit_ref=next(node['ref'] for node in snapshot['controls'] if node['control_type']=='Edit')
    button_ref=next(node['ref'] for node in snapshot['controls'] if node['control_type']=='Button')
    result=tool.call('desktop.act',{**params,'ref':edit_ref,'action':'set_text','text':'typed'})
    assert edit.text=='typed' and 'typed' in str(result)
    tool.call('desktop.act',{**params,'ref':button_ref,'action':'click'})
    assert button.invoked==1
    tool.call('desktop.snapshot',params)
    with pytest.raises(DesktopStaleRef):tool.call('desktop.act',{**params,'ref':button_ref,'action':'click'})


def test_removed_runtime_reference_wrong_chat_and_owner_activity_are_refused(guarded_desktop):
    tool,root,edit,button,state=guarded_desktop
    tool.ocr_enabled=False
    params={'conversation_id':'c','window_handle':100}
    snap=tool.call('desktop.snapshot',params)
    ref=next(node['ref'] for node in snap['controls'] if node['control_type']=='Button')
    with pytest.raises(DesktopStaleRef):tool.call('desktop.act',{**params,'conversation_id':'other','ref':ref,'action':'click'})
    state['idle']=0
    with pytest.raises(ValueError,match='activity'):tool.call('desktop.act',{**params,'ref':ref,'action':'click'})
    assert button.invoked==0
    state['idle']=5000
    root._children.remove(button)
    with pytest.raises(DesktopStaleRef):tool.call('desktop.act',{**params,'ref':ref,'action':'click'})


def test_keys_are_bounded_chords_and_cannot_close_or_inject_scripts():
    assert key_chord('ctrl+a')=='^a'
    assert key_chord('ctrl+shift+s')=='^+s'
    assert key_chord('tab')=='{TAB}'
    for value in ('alt+f4','alt+F4','win+l','ctrl+{F4}','ctrl+ctrl+a{ENTER}'):
        with pytest.raises(ValueError):key_chord(value)


def test_mouse_fallback_rechecks_activity_after_uia_pattern_failure(guarded_desktop):
    tool,root,edit,button,state=guarded_desktop
    tool.ocr_enabled=False
    params={'conversation_id':'c','window_handle':100}
    snapshot=tool.call('desktop.snapshot',params)
    ref=next(node['ref'] for node in snapshot['controls'] if node['control_type']=='Button')
    class NoPatternInterfaceError(Exception):pass
    clicks=[]
    def unavailable():
        state['idle']=0
        raise NoPatternInterfaceError()
    button.invoke=unavailable
    button.click_input=lambda:clicks.append(True)
    with pytest.raises(ValueError,match='activity'):
        tool.call('desktop.act',{**params,'ref':ref,'action':'click'})
    assert clicks==[]
