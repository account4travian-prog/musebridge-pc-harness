import contextlib
import io
import json
from types import SimpleNamespace

import pytest

from musebridge import desktop
from musebridge.desktop import DesktopTools, MAX_TREE_NODES


class Rect:
    left, top, right, bottom = 1, 2, 101, 52


def jpeg_bytes(color="white"):
    from PIL import Image
    stream = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(stream, format="JPEG")
    return stream.getvalue()


class Control:
    def __init__(self, name, control_type, automation_id="", children=None, handle=100, pid=200):
        self._name = name
        self.handle = handle
        self._pid = pid
        self._children = children or []
        self.element_info = SimpleNamespace(automation_id=automation_id, control_type=control_type,
                                            class_name="Fake")
        self.invoked = 0
        self.focused = 0
        self.text = ""

    def window_text(self):
        return self._name if not self.text else self.text

    def process_id(self):
        return self._pid

    def class_name(self):
        return "Fake"

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def rectangle(self):
        return Rect()

    def children(self):
        return self._children

    def invoke(self):
        self.invoked += 1

    def set_focus(self):
        self.focused += 1

    def set_edit_text(self, text):
        self.text = text

class FakeDesktop:
    def __init__(self, window):
        self._window = window

    def window(self, handle):
        assert handle == self._window.handle
        return SimpleNamespace(wrapper_object=lambda: self._window)

    def windows(self, **_kwargs):
        return [self._window]


@pytest.fixture
def guarded_desktop(tmp_path, monkeypatch):
    original_capture_helper = desktop._capture_window_printwindow
    button = Control("Save", "Button", "saveButton", handle=100, pid=200)
    edit = Control("", "Edit", "messageInput", handle=100, pid=200)
    root = Control("Test Window", "Window", children=[edit, button], handle=100, pid=200)
    state = {"session": (1, True), "idle": 5000, "foreground": 100,
             "target_integrity": 0x2000, "current_integrity": 0x2000,
             "valid": True, "target_session": 1}
    monkeypatch.setattr(desktop, "_com_initialized", contextlib.nullcontext)
    monkeypatch.setattr(desktop, "_desktop", lambda: FakeDesktop(root))
    monkeypatch.setattr(desktop, "_session_state", lambda: state["session"])
    monkeypatch.setattr(desktop, "_idle_ms", lambda: state["idle"])
    monkeypatch.setattr(desktop, "_foreground_hwnd", lambda: state["foreground"])
    monkeypatch.setattr(desktop, "_process_session_id", lambda _pid: state["target_session"])
    monkeypatch.setattr(desktop, "_integrity_level",
                        lambda pid: state["current_integrity"] if pid != 200 else state["target_integrity"])
    monkeypatch.setattr(desktop, "_is_window", lambda _hwnd: state["valid"])
    monkeypatch.setattr(desktop, "_window_pid", lambda _hwnd: 200)
    monkeypatch.setattr(desktop, "_capture_window_printwindow",
                        lambda _hwnd, _pid: jpeg_bytes())
    instance = DesktopTools(tmp_path)
    instance._original_capture_helper = original_capture_helper
    yield instance, root, edit, button, state
    instance.close()


def test_specs_require_conversation_and_expose_only_narrow_actions(tmp_path):
    specs = DesktopTools.specs()
    assert set(specs) == {"desktop.windows", "desktop.observe", "desktop.act", "desktop.screenshot", "desktop.snapshot"}
    assert all("conversation_id" in spec["required"] for spec in specs.values())
    assert specs["desktop.act"]["required"]["action"]["type"] == "string"
    assert specs["desktop.act"]["required"]["action"]["description"] == "One of focus, invoke, set_text."
    tool = DesktopTools(tmp_path)
    try:
        with pytest.raises(ValueError, match="conversation_id"):
            tool.call("desktop.windows", {})
        with pytest.raises(ValueError, match="unknown"):
            tool.call("desktop.execute", {"conversation_id": "c"})
    finally:
        tool.close()


def test_validation_rejects_unknown_actions_missing_or_ambiguous_selectors(tmp_path):
    tool = DesktopTools(tmp_path)
    base = {"conversation_id": "c", "window_handle": 100}
    try:
        with pytest.raises(ValueError, match="action must"):
            tool.call("desktop.act", {**base, "action": "type_keys", "selector": "0"})
        with pytest.raises(ValueError, match="selector or at least"):
            tool.call("desktop.act", {**base, "action": "invoke"})
        with pytest.raises(ValueError, match="selector path"):
            tool.call("desktop.act", {**base, "action": "invoke", "selector": "../../x"})
        with pytest.raises(ValueError, match="only valid"):
            tool.call("desktop.act", {**base, "action": "invoke", "selector": "0", "text": "x"})
    finally:
        tool.close()


def test_observe_returns_bounded_ui_tree_and_stable_selectors(guarded_desktop):
    tool, _root, _edit, _button, _state = guarded_desktop
    result = tool.call("desktop.observe", {"conversation_id": "c", "window_handle": 100})
    assert result["window"]["window_handle"] == 100
    assert result["count"] == 3
    assert result["controls"][0]["selector"] == "0"
    assert result["controls"][1]["selector"] == "0.0"
    assert result["controls"][1]["automation_id"] == "messageInput"
    assert result["controls"][2]["control_type"] == "Button"


def test_action_requires_unique_control_match_or_observe_selector(guarded_desktop):
    tool, root, _edit, button, _state = guarded_desktop
    root.children().append(Control("Save", "Button", "saveAgain", handle=100, pid=200))
    with pytest.raises(ValueError, match="ambiguous"):
        tool.call("desktop.act", {"conversation_id": "c", "window_handle": 100, "action": "invoke", "name": "Save"})
    result = tool.call("desktop.act", {"conversation_id": "c", "window_handle": 100,
                                       "action": "invoke", "selector": "0.1"})
    assert result["verified"] is False
    assert result["invoked"] is True
    assert button.invoked == 1


@pytest.mark.parametrize("state_key,value,message", [
    ("session", (1, False), "locked"),
    ("idle", 1999, "user activity"),
    ("target_integrity", 0x3000, "higher integrity"),
    ("foreground", 777, "not foreground"),
    ("valid", False, "no longer valid"),
    ("target_session", 2, "outside the current interactive session"),
])
def test_mutating_actions_refuse_unsafe_session_or_target(guarded_desktop, state_key, value, message):
    tool, _root, _edit, button, state = guarded_desktop
    state[state_key] = value
    with pytest.raises(ValueError, match=message):
        tool.call("desktop.act", {"conversation_id": "c", "window_handle": 100,
                                  "action": "invoke", "automation_id": "saveButton"})
    assert button.invoked == 0


def test_focus_is_explicit_and_required_before_background_mutation(guarded_desktop, monkeypatch):
    tool, _root, _edit, button, state = guarded_desktop
    state["foreground"] = 777
    with pytest.raises(ValueError, match="explicit focus"):
        tool.call("desktop.act", {"conversation_id": "c", "window_handle": 100,
                                  "action": "invoke", "automation_id": "saveButton"})
    assert button.invoked == 0
    monkeypatch.setattr(desktop, "_foreground_hwnd", lambda: 100)
    focused = tool.call("desktop.act", {"conversation_id": "c", "window_handle": 100,
                                        "action": "focus", "automation_id": "saveButton"})
    assert focused["verified"]
    assert button.focused == 1


def test_foreground_change_during_action_preparation_is_refused(guarded_desktop, monkeypatch):
    tool, _root, _edit, button, _state = guarded_desktop
    sequence = iter((100, 777))
    monkeypatch.setattr(desktop, "_foreground_hwnd", lambda: next(sequence))
    with pytest.raises(ValueError, match="foreground window changed"):
        tool.call("desktop.act", {"conversation_id": "c", "window_handle": 100,
                                  "action": "invoke", "automation_id": "saveButton"})
    assert button.invoked == 0


def test_set_text_only_accepts_edit_controls_and_verifies_without_echoing_text(guarded_desktop):
    tool, _root, edit, _button, _state = guarded_desktop
    result = tool.call("desktop.act", {"conversation_id": "c", "window_handle": 100,
                                       "action": "set_text", "automation_id": "messageInput",
                                       "text": "private typed text"})
    assert result["verified"] is True
    assert "private typed text" not in str(result)
    assert edit.text == "private typed text"
    with pytest.raises(ValueError, match="only for UIA Edit"):
        tool.call("desktop.act", {"conversation_id": "c", "window_handle": 100,
                                  "action": "set_text", "automation_id": "saveButton", "text": "x"})
    with pytest.raises(ValueError, match="up to"):
        tool.call("desktop.act", {"conversation_id": "c", "window_handle": 100,
                                  "action": "set_text", "automation_id": "messageInput",
                                  "text": "x" * (desktop.MAX_ACTION_TEXT + 1)})


def test_desktop_windows_and_screenshot_return_bounded_artifact_paths(guarded_desktop):
    tool, _root, _edit, _button, _state = guarded_desktop
    windows = tool.call("desktop.windows", {"conversation_id": "c"})
    assert windows["count"] == 1
    assert windows["windows"][0]["window_handle"] == 100
    image = tool.call("desktop.screenshot", {"conversation_id": "private/c", "window_handle": 100})
    from pathlib import Path
    path = Path(image["path"])
    assert path.is_file()
    assert path.stat().st_size == image["size"] <= 8 * 1024 * 1024
    assert image["format"] == "jpeg"


def test_printwindow_capture_failure_never_saves_an_artifact(guarded_desktop, monkeypatch):
    tool, _root, _edit, _button, _state = guarded_desktop
    def fail(_hwnd, _pid):
        raise OSError("selected window could not render")
    monkeypatch.setattr(desktop, "_capture_window_printwindow", fail)
    with pytest.raises(OSError, match="render"):
        tool.call("desktop.screenshot", {"conversation_id": "c", "window_handle": 100})
    assert not list(tool.artifact_dir.rglob("*.jpg"))


def test_window_move_during_capture_refuses_misaligned_image(guarded_desktop,monkeypatch):
    tool,root,edit,button,state=guarded_desktop
    rectangles=iter(({'left':1,'top':2,'right':101,'bottom':52},
                     {'left':10,'top':2,'right':110,'bottom':52}))
    monkeypatch.setattr(tool,'_window_rect',lambda *_:next(rectangles))
    with pytest.raises(ValueError,match='moved or resized'):
        tool.call('desktop.screenshot',{'conversation_id':'c','window_handle':100})
    assert not list(tool.artifact_dir.rglob('*.jpg'))


def test_printwindow_worker_timeout_is_bounded_and_saves_no_artifact(guarded_desktop, monkeypatch):
    tool, _root, _edit, _button, _state = guarded_desktop
    monkeypatch.setattr(desktop, "_capture_window_printwindow", tool._original_capture_helper)
    def timeout(*args, **kwargs):
        raise __import__("subprocess").TimeoutExpired(args[0], desktop.WINDOW_CAPTURE_TIMEOUT)
    monkeypatch.setattr(desktop.subprocess, "run", timeout)
    with pytest.raises(OSError, match="timed out"):
        tool.call("desktop.screenshot", {"conversation_id": "c", "window_handle": 100})
    assert not list(tool.artifact_dir.rglob("*.jpg"))


@pytest.mark.parametrize("pids", [(201,), (200, 201)])
def test_screenshot_rechecks_expected_window_process_before_and_after_capture(guarded_desktop, monkeypatch, pids):
    tool, _root, _edit, _button, _state = guarded_desktop
    sequence = iter(pids)
    monkeypatch.setattr(desktop, "_window_pid", lambda _hwnd: next(sequence))
    with pytest.raises(ValueError, match="process changed"):
        tool.call("desktop.screenshot", {"conversation_id": "c", "window_handle": 100})
    assert not list(tool.artifact_dir.rglob("*.jpg"))


@pytest.mark.parametrize("width,height", [(0, 10), (20_001, 1), (4001, 4000)])
def test_printwindow_dib_dimensions_are_bounded(width, height):
    with pytest.raises(ValueError, match="safe screenshot dimensions"):
        desktop._capture_dimensions(width, height)
    assert desktop._capture_dimensions(4000, 4000) == 64_000_000


def test_tree_has_a_hard_node_cap(guarded_desktop):
    tool, root, _edit, _button, _state = guarded_desktop
    root._children = [Control("😀" * 256, "Button", f"id{i}", handle=100, pid=200)
                      for i in range(MAX_TREE_NODES + 10)]
    result = tool.call("desktop.observe", {"conversation_id": "c", "window_handle": 100})
    assert result["count"] <= MAX_TREE_NODES
    assert result["truncated"] is True
    assert len(json.dumps(result).encode("utf-8")) <= 150_000
