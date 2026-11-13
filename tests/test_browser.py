import importlib.util
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from musebridge.browser import BrowserTools, MAX_FILL_CHARS, MAX_OBSERVE_CHARS


def test_specs_match_muse_tool_schema_and_require_conversation_id(tmp_path):
    specs = BrowserTools.specs()
    assert set(specs) == {
        "browser.open", "browser.observe", "browser.click", "browser.fill",
        "browser.screenshot", "browser.close",
        "browser.eval", "browser.select", "browser.check",
        "browser.navigate", "browser.snapshot", "browser.act",
    }
    assert all("conversation_id" in value["required"] for value in specs.values())
    assert specs["browser.fill"]["required"]["text"]["type"] == "string"
    specs.clear()
    assert BrowserTools.specs()
    browser = BrowserTools(tmp_path)
    try:
        with pytest.raises(ValueError, match="conversation_id"):
            browser.call("browser.observe", {})
        with pytest.raises(ValueError, match="unknown"):
            browser.call("browser.unknown", {"conversation_id": "c1"})
        with pytest.raises(ValueError, match="HTTP and HTTPS"):
            browser.call("browser.open", {"conversation_id": "c1", "url": "file:///secret"})
        with pytest.raises(ValueError, match="embedded credentials"):
            browser.call("browser.open", {"conversation_id": "c1", "url": "https://user:pass@example.test"})
        with pytest.raises(ValueError, match="up to"):
            browser.call("browser.fill", {"conversation_id": "c1", "selector": "#x", "text": "x" * (MAX_FILL_CHARS + 1)})
    finally:
        browser.close()


def test_calls_are_serialized_on_dedicated_worker_and_close_is_final(tmp_path, monkeypatch):
    owner_threads = []
    seen = []

    def dispatch(self, name, params):
        owner_threads.append(threading.get_ident())
        seen.append((name, params["conversation_id"]))
        return {"done": name}

    monkeypatch.setattr(BrowserTools, "_dispatch", dispatch)
    browser = BrowserTools(tmp_path)
    caller = threading.get_ident()
    try:
        assert browser.call("browser.observe", {"conversation_id": "a"}) == {"done": "browser.observe"}
        assert browser.call("browser.observe", {"conversation_id": "b"}) == {"done": "browser.observe"}
        assert seen == [("browser.observe", "a"), ("browser.observe", "b")]
        assert len(set(owner_threads)) == 1
        assert owner_threads[0] != caller
    finally:
        browser.close()
    with pytest.raises(OSError, match="closed"):
        browser.call("browser.observe", {"conversation_id": "a"})


needs_playwright = pytest.mark.skipif(importlib.util.find_spec("playwright") is None,
                                      reason="Playwright is not installed")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/large":
            body = ("<!doctype html><title>Large Page</title><p>" + "x" * (MAX_OBSERVE_CHARS + 100) + "</p>").encode()
        else:
            body = b"""<!doctype html><title>Local Browser Fixture</title>
            <p id="stored"></p><input id="entry"><button id="save" onclick="localStorage.setItem('value', document.querySelector('#entry').value); document.querySelector('#stored').textContent = localStorage.getItem('value')">Save</button>
            <script>document.querySelector('#stored').textContent = localStorage.getItem('value') || 'initial'</script>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


@pytest.fixture
def local_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


@needs_playwright
def test_local_browser_workflow_and_conversation_isolation(tmp_path, local_site):
    browser = BrowserTools(tmp_path / "artifacts")
    try:
        opened = browser.call("browser.open", {"conversation_id": "conversation-a", "url": local_site})
        assert opened["status"] == 200
        assert opened["title"] == "Local Browser Fixture"
        browser.call("browser.fill", {"conversation_id": "conversation-a", "selector": "#entry", "text": "private text"})
        browser.call("browser.click", {"conversation_id": "conversation-a", "selector": "#save"})
        observed_a = browser.call("browser.observe", {"conversation_id": "conversation-a"})
        assert "private text" in observed_a["text"]
        assert observed_a["truncated"] is False

        browser.call("browser.open", {"conversation_id": "conversation-b", "url": local_site})
        observed_b = browser.call("browser.observe", {"conversation_id": "conversation-b"})
        assert "initial" in observed_b["text"]
        assert "private text" not in observed_b["text"]

        artifact = browser.call("browser.screenshot", {"conversation_id": "conversation-a"})
        screenshot = Path(artifact["path"])
        assert screenshot.is_file()
        assert screenshot.stat().st_size == artifact["size"]
        assert artifact["size"] > 0
        assert artifact["size"] <= 2 * 1024 * 1024
        assert artifact["format"] == "jpeg"
        assert browser.call("browser.close", {"conversation_id": "conversation-a"}) == {"closed": True}
        assert browser.call("browser.close", {"conversation_id": "conversation-a"}) == {"closed": False}
    finally:
        browser.close()


@needs_playwright
def test_observe_text_is_capped(tmp_path, local_site):
    browser = BrowserTools(tmp_path)
    try:
        browser.call("browser.open", {"conversation_id": "large", "url": local_site + "large"})
        result = browser.call("browser.observe", {"conversation_id": "large"})
        assert len(result["text"]) == MAX_OBSERVE_CHARS
        assert result["truncated"] is True
    finally:
        browser.close()
