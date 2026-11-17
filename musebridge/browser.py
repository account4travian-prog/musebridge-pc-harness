"""Isolated, serialized Playwright tools for MuseBridge conversations.

Chromium is launched headlessly by this component. It never attaches to an
existing browser, launches a persistent profile, or reads browser profiles.
"""

from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit


MAX_OBSERVE_CHARS = 12_000
MAX_FILL_CHARS = 10_000
MAX_SELECTOR_CHARS = 500
MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024  # Raw capture; screen.look resizes before image admission.
COMMAND_TIMEOUT_SECONDS = 25
NAVIGATION_TIMEOUT_MS = 15_000
ACTION_TIMEOUT_MS = 5_000


def _param(description: str, kind: str = "string") -> dict:
    return {"type": kind, "description": description}


CONVERSATION_ID = _param("Exact conversation_id from the MuseBridge context. Required to isolate browser state per conversation.")


_SPECS = {
    'browser.select': {'description':'Select a dropdown option by value.',
        'required':{'conversation_id':CONVERSATION_ID,'selector':_param('Select locator.'),'text':_param('Option value.')}, 'optional':{}},
    'browser.check': {'description':'Set a checkbox state.',
        'required':{'conversation_id':CONVERSATION_ID,'selector':_param('Checkbox locator.'),'checked':_param('Checked state.','boolean')}, 'optional':{}},
    "browser.eval": {
        "description": "Evaluate bounded JavaScript in this chat's owned browser page.",
        "required": {"conversation_id": CONVERSATION_ID, "js": _param("JavaScript expression, at most 16000 characters.")},
        "optional": {},
    },
    "browser.open": {
        "description": "Open an HTTP or HTTPS URL in this conversation's isolated headless browser.",
        "required": {"conversation_id": CONVERSATION_ID, "url": _param("HTTP or HTTPS URL to open.")},
        "optional": {},
    },
    "browser.observe": {
        "description": "Read the current page title, URL, and bounded visible page text for this conversation.",
        "required": {"conversation_id": CONVERSATION_ID},
        "optional": {},
    },
    "browser.click": {
        "description": "Click one element selected by a Playwright locator in this conversation's page.",
        "required": {"conversation_id": CONVERSATION_ID, "selector": _param("CSS or Playwright selector; it must identify one actionable element.")},
        "optional": {},
    },
    "browser.fill": {
        "description": "Fill a text input or editable element selected by a Playwright locator.",
        "required": {"conversation_id": CONVERSATION_ID, "selector": _param("CSS or Playwright selector for one editable element."),
                     "text": _param("Text to enter, up to 10000 characters.")},
        "optional": {},
    },
    "browser.screenshot": {
        "description": "Save a bounded JPEG screenshot artifact for this conversation and return its local path.",
        "required": {"conversation_id": CONVERSATION_ID},
        "optional": {},
    },
    "browser.close": {
        "description": "Close and discard this conversation's isolated browser context.",
        "required": {"conversation_id": CONVERSATION_ID},
        "optional": {},
    },
}
_SPECS.update({
    'browser.navigate':{'description':'Navigate and return a reference snapshot.',
        'required':{'conversation_id':CONVERSATION_ID,'url':_param('HTTP or HTTPS URL')},'optional':{}},
    'browser.snapshot':{'description':'Read a bounded ARIA snapshot with element references.',
        'required':{'conversation_id':CONVERSATION_ID},'optional':{'filter':_param('Text or role filter')}},
    'browser.act':{'description':'Act on a snapshotted reference and return changes.',
        'required':{'conversation_id':CONVERSATION_ID,'action':_param('Action')},'optional':{}},
})


class BrowserTools:
    """Own a headless Chromium instance on a single dedicated worker thread."""

    def __init__(self, artifact_dir: str | Path):
        self.artifact_dir = Path(artifact_dir).expanduser().resolve()
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self._queue: queue.Queue = queue.Queue(maxsize=32)
        self._closed = False
        self._close_lock = threading.Lock()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="MuseBridge-Playwright", daemon=True)
        self._thread.start()
        if not self._ready.wait(5):
            self.close()
            raise OSError("browser worker did not start")

    @staticmethod
    def specs() -> dict:
        # Return a detached schema so caller mutation cannot affect dispatch.
        return {name: {"description": spec["description"],
                       "required": {key: dict(value) for key, value in spec["required"].items()},
                       "optional": {key: dict(value) for key, value in spec["optional"].items()}}
                for name, spec in _SPECS.items()}

    @staticmethod
    def _validate_url(value: object) -> str:
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError("url must be a string up to 4096 characters")
        parsed = urlsplit(value)
        if parsed.scheme.casefold() not in ("http", "https") or not parsed.hostname:
            raise ValueError("only absolute HTTP and HTTPS URLs are allowed")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("URLs containing embedded credentials are not allowed")
        return value

    @staticmethod
    def _validate_cid(value: object) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise ValueError("conversation_id must be a non-empty string up to 256 characters")
        if any(ord(char) < 32 for char in value):
            raise ValueError("conversation_id cannot contain control characters")
        return value

    @staticmethod
    def _validate_selector(value: object) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_SELECTOR_CHARS:
            raise ValueError(f"selector must be a non-empty string up to {MAX_SELECTOR_CHARS} characters")
        return value

    def _validate(self, name: str, params: dict) -> dict:
        if name not in _SPECS:
            raise ValueError("unknown browser tool")
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        required = _SPECS[name]["required"]
        missing = [key for key in required if key not in params]
        if missing:
            raise ValueError("missing parameter: " + ", ".join(missing))
        cid = self._validate_cid(params["conversation_id"])
        clean = {"conversation_id": cid}
        if name in ("browser.open",'browser.navigate'):
            clean["url"] = self._validate_url(params["url"])
        elif name in ('browser.snapshot','browser.act'):
            clean.update({key:params[key] for key in ('filter','action','ref','selector','text','key','value','amount','seconds') if key in params})
            if 'selector' in clean:clean['selector']=self._validate_selector(clean['selector'])
        elif name == "browser.eval":
            script = params['js']
            if not isinstance(script,str) or not script.strip() or len(script) > 16000:
                raise ValueError('js must be a nonempty JavaScript expression under 16000 characters')
            clean['js'] = script
        elif name in ("browser.click", "browser.fill", 'browser.select','browser.check'):
            clean["selector"] = self._validate_selector(params["selector"])
            if name in ("browser.fill", 'browser.select'):
                text = params["text"]
                if not isinstance(text, str) or len(text) > MAX_FILL_CHARS:
                    raise ValueError(f"text must be a string up to {MAX_FILL_CHARS} characters")
                clean["text"] = text
            if name == 'browser.check':
                if type(params['checked']) is not bool:raise ValueError('checked must be a boolean')
                clean['checked'] = params['checked']
        return clean

    def call(self, name: str, params: dict, *, guard=None) -> dict:
        clean = self._validate(name, params)
        with self._close_lock:
            if self._closed:
                raise OSError("browser tools are closed")
            reply = queue.Queue(maxsize=1)
            try:
                self._queue.put((name, clean, reply, guard), timeout=0.25)
            except queue.Full as exc:
                raise OSError("browser command queue is full") from exc
        try:
            success, value = reply.get(timeout=COMMAND_TIMEOUT_SECONDS)
        except queue.Empty as exc:
            # The queued side effect may still complete. Do not replay it.
            raise OSError("browser command timed out; its outcome may be unknown") from exc
        if success:
            return value
        raise value

    def _run(self) -> None:
        self._playwright = None
        self._browser = None
        self._contexts: dict[str, tuple[object, object]] = {}
        from .browser_refs import BrowserRefs
        self.refs=BrowserRefs()
        self._ready.set()
        while True:
            item = self._queue.get()
            if item is None:
                break
            name, params, reply, guard = item
            try:
                if guard:guard()
                result = self._dispatch(name, params)
                reply.put((True, result))
            except Exception as exc:
                # Bound library and navigation errors before returning them to
                # the chat control channel.
                message = str(exc).replace("\x00", "")[:500] or type(exc).__name__
                reply.put((False, exc if getattr(exc,'code',None) else ValueError(f"{type(exc).__name__}: {message}")))
        self._shutdown_on_worker()

    def _ensure_browser(self) -> None:
        if self._browser is not None and self._browser.is_connected():
            return
        if self._playwright is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise OSError("Playwright is not installed in this Python environment") from exc
            self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(headless=True)
        except Exception as exc:
            raise OSError("Could not launch headless Chromium; install the Playwright Chromium browser") from exc

    def _get_page(self, cid: str):
        self._ensure_browser()
        pair = self._contexts.get(cid)
        if pair is None or pair[0].pages == []:
            if pair is None and len(self._contexts) >= 8:
                raise ValueError('Eight browser conversations are open; close one before opening another')
            context = self._browser.new_context(accept_downloads=False, service_workers="block")
            context.set_default_timeout(ACTION_TIMEOUT_MS)
            context.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
            context.route("**/*", self._route_http_only)
            page = context.new_page()
            self._contexts[cid] = (context, page)
            return page
        context, page = pair
        if page.is_closed():
            page = context.new_page()
            self._contexts[cid] = (context, page)
        return page

    @staticmethod
    def _route_http_only(route) -> None:
        """Keep redirects and page-initiated navigations on web schemes."""
        try:
            scheme = urlsplit(route.request.url).scheme.casefold()
        except ValueError:
            scheme = ""
        if scheme in ("http", "https"):
            route.continue_()
        else:
            route.abort()

    @staticmethod
    def _page_summary(page) -> dict:
        title = page.title()
        return {"url": page.url[:4096], "title": title[:1000]}

    def _dispatch(self, name: str, params: dict) -> dict:
        cid = params["conversation_id"]
        if name == "browser.close":
            self.refs.forget(cid)
            pair = self._contexts.pop(cid, None)
            if pair:
                pair[0].close()
            return {"closed": pair is not None}
        page = self._get_page(cid)
        if name=='browser.navigate':
            page.goto(params['url'],wait_until='domcontentloaded',timeout=NAVIGATION_TIMEOUT_MS)
            return self.refs.snapshot(cid,page,full=True)
        if name=='browser.snapshot':return self.refs.snapshot(cid,page,params.get('filter'))
        if name=='browser.act':return self.refs.act(cid,page,params)
        if name == 'browser.eval':
            # CDP terminates synchronous evaluation at its own deadline. Do not
            # await arbitrary promises, which can leave an invocation unbounded.
            # https://chromedevtools.github.io/devtools-protocol/tot/Runtime/#method-evaluate
            session = page.context.new_cdp_session(page)
            try:
                evaluated = session.send('Runtime.evaluate', {'expression': params['js'],
                    'timeout': 3000, 'returnByValue': True, 'awaitPromise': False,
                    'silent': True, 'allowUnsafeEvalBlockedByCSP': False})
                if evaluated.get('exceptionDetails'):
                    raise ValueError('JavaScript evaluation failed; narrow the expression and inspect browser.snapshot.')
                value = evaluated.get('result', {})
                if len(json.dumps(value,ensure_ascii=True).encode())>8000:
                    return {'value':None,'truncated':True,'hint':'JavaScript result exceeds 8 KB. Return a smaller value.'}
                if value.get('subtype') == 'promise':
                    return {'value': None, 'hint': 'Promise was not awaited. Return a synchronous value or inspect state in a later call.'}
                return {'value': value.get('value'), 'type': value.get('type'),
                        'unserializable_value': value.get('unserializableValue')}
            finally:
                session.detach()
        if name == "browser.open":
            response = page.goto(params["url"], wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
            summary = self._page_summary(page)
            summary["status"] = response.status if response is not None else None
            return summary
        if name == "browser.observe":
            text = page.locator("body").inner_text(timeout=ACTION_TIMEOUT_MS)
            clipped = text[:MAX_OBSERVE_CHARS]
            result = self._page_summary(page)
            result.update({"text": clipped, "truncated": len(text) > len(clipped)})
            result['controls']=page.locator('button,input,a,select,textarea,[role="button"]').evaluate_all('''nodes =>
              nodes.slice(0,80).map(n => ({tag:n.tagName.toLowerCase(),id:n.id.slice(0,200),
                name:(n.getAttribute('name')||'').slice(0,200),role:(n.getAttribute('role')||'').slice(0,100),
                label:(n.getAttribute('aria-label')||n.innerText||'').slice(0,200),
                type:(n.getAttribute('type')||'').slice(0,40)}))''')
            return result
        if name == "browser.click":
            page.locator(params["selector"]).click(timeout=ACTION_TIMEOUT_MS)
            return self._page_summary(page)
        if name == "browser.fill":
            page.locator(params["selector"]).fill(params["text"], timeout=ACTION_TIMEOUT_MS)
            return self._page_summary(page)
        if name == 'browser.select':
            page.locator(params['selector']).select_option(value=params['text'],timeout=ACTION_TIMEOUT_MS)
            return self._page_summary(page)
        if name == 'browser.check':
            page.locator(params['selector']).set_checked(params['checked'],timeout=ACTION_TIMEOUT_MS)
            return self._page_summary(page)
        if name == "browser.screenshot":
            return self._screenshot(cid, page)
        raise ValueError("unknown browser tool")

    def _screenshot(self, cid: str, page) -> dict:
        if cid not in self.refs.states:self.refs.snapshot(cid,page)
        safe_id = hashlib.sha256(cid.encode("utf-8")).hexdigest()[:20]
        directory = self.artifact_dir / safe_id
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / (f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}.jpg")
        page.screenshot(path=str(target), type="jpeg", quality=70, full_page=False,
                        animations="disabled", timeout=ACTION_TIMEOUT_MS)
        size = target.stat().st_size
        if size > MAX_SCREENSHOT_BYTES:
            target.unlink(missing_ok=True)
            raise ValueError(f"screenshot exceeds {MAX_SCREENSHOT_BYTES} byte limit")
        state=self.refs.states.get(cid,{})
        marks=[{'ref':ref,'rectangle':{'left':item['rectangle'][0],'top':item['rectangle'][1]}}
               for ref,item in state.get('refs',{}).items()]
        return {"path": str(target), "size": size, "format": "jpeg",'marks':marks}

    def _shutdown_on_worker(self) -> None:
        for context, _page in self._contexts.values():
            try:
                context.close()
            except Exception:
                pass
        self._contexts.clear()
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._queue.put(None, timeout=1)
            except queue.Full:
                # Give the worker's current bounded action a chance to finish.
                try:
                    self._queue.put(None, timeout=COMMAND_TIMEOUT_SECONDS)
                except queue.Full:
                    return
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=COMMAND_TIMEOUT_SECONDS + 2)

    def __enter__(self) -> "BrowserTools":
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()
