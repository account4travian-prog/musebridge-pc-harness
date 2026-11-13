"""Fast, non-blocking syntax warnings for text edits.

The in-process parsers cannot be preempted safely. To keep edit latency
predictable, inputs over 64 KiB are skipped (YAML uses a more conservative
16 KiB cap because dense mappings benchmark slower). PowerShell uses an
isolated parser process with a 300 ms total parse deadline and bounded cleanup.
Diagnostics never execute project code and never prevent publication.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import subprocess
import time
import tomllib
import xml.etree.ElementTree as ET

from .workspace_text import decode_document


MAX_IN_PROCESS_BYTES = 64 * 1024
MAX_YAML_BYTES = 16 * 1024
MAX_DIAGNOSTICS = 20
MAX_MESSAGE_CHARS = 240
MAX_POWERSHELL_BYTES = 64 * 1024
_SOURCES = {
    ".py": "python", ".json": "json", ".toml": "toml", ".xml": "xml",
    ".yaml": "yaml", ".yml": "yaml", ".ps1": "powershell",
}
_POWERSHELL_SCRIPT = r'''
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$source = [Console]::In.ReadToEnd()
$tokens = $null
$errors = $null
[System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors) | Out-Null
foreach ($error in @($errors | Select-Object -First 20)) {
  [Console]::Out.WriteLine(([string]$error.Extent.StartLineNumber) + "`t" + ([string]$error.ErrorId))
}
'''.strip()
_PS_LINE = re.compile(r"^([1-9][0-9]*)\t([A-Za-z][A-Za-z0-9_.-]{0,79})$")
_XML_ENTITY = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


def _warning(source: str, line: int | None, message: str) -> dict:
    cleaned = " ".join(str(message).replace("\x00", "").split())[:MAX_MESSAGE_CHARS]
    return {"source": source, "line": line, "message": cleaned or "Syntax error."}


def _toml_line(error: tomllib.TOMLDecodeError) -> int | None:
    value = getattr(error, "lineno", None)
    if type(value) is int and value > 0:
        return value
    match = re.search(r"\bline\s+(\d+)\b", str(error), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _parse_powershell(source: str, timeout: float) -> list[dict]:
    if timeout <= 0:
        return []
    deadline = time.monotonic() + timeout
    try:
        from .shells import resolve_shell
        _name, executable = resolve_shell("powershell")
    except (OSError, RuntimeError, ValueError):
        return []
    try:
        process = subprocess.Popen(
            [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", _POWERSHELL_SCRIPT],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=None, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, ValueError):
        return []
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired("powershell parser", timeout)
        output, _ = process.communicate(source.encode("utf-8"), timeout=remaining)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.communicate(timeout=0.05)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass
        return []
    except (OSError, ValueError):
        try:
            process.kill()
        except OSError:
            pass
        return []
    if process.returncode != 0 or not isinstance(output, bytes) or len(output) > 4096:
        return []
    diagnostics = []
    for line in output.decode("utf-8", "replace").splitlines()[:MAX_DIAGNOSTICS]:
        match = _PS_LINE.fullmatch(line.strip())
        if match:
            diagnostics.append(_warning("powershell", int(match.group(1)),
                                        "PowerShell parse error (" + match.group(2) + ")."))
    return diagnostics


def diagnose(path: str, data: bytes, budget_ms: int = 500) -> list[dict]:
    """Return concise syntax warnings for supported file types, or ``[]``.

    The 64 KiB cap (16 KiB for YAML) bounds in-process parsing; larger files
    are intentionally skipped rather than risking an uninterruptible parse
    beyond the time budget. PowerShell receives source only on stdin and is
    never asked to execute it. Its 300 ms deadline includes process startup;
    after kill, pipe cleanup is best-effort for at most another 50 ms. The
    operating system's process-creation call itself cannot be preempted.
    """
    if not isinstance(path, str) or not isinstance(data, bytes):
        return []
    if type(budget_ms) is not int or budget_ms <= 0:
        return []
    source_name = _SOURCES.get(Path(path).suffix.casefold())
    if not source_name:
        return []
    limit = (MAX_POWERSHELL_BYTES if source_name == "powershell" else
             MAX_YAML_BYTES if source_name == "yaml" else MAX_IN_PROCESS_BYTES)
    if len(data) > limit:
        return []
    started = time.monotonic()
    budget = budget_ms / 1000
    try:
        document = decode_document(data, path)
        if document.binary or document.text is None:
            return []
        source = document.text
        if source_name == "powershell":
            return _parse_powershell(source, min(0.3, max(0.0, budget - (time.monotonic() - started))))
        if time.monotonic() - started >= budget:
            return []
        if source_name == "python":
            ast.parse(source, filename="<project file>")
        elif source_name == "json":
            json.loads(source)
        elif source_name == "toml":
            tomllib.loads(source)
        elif source_name == "xml":
            # Do not let a diagnostics-only pass expand entity declarations.
            if _XML_ENTITY.search(source):
                return []
            ET.fromstring(source)
        elif source_name == "yaml":
            try:
                import yaml
            except ImportError:
                return []
            # Compose with BaseLoader so syntax is checked without invoking
            # application-specific constructors or rejecting custom tags.
            yaml.compose(source, Loader=yaml.BaseLoader)
        else:
            return []
    except ET.ParseError as exc:
        if time.monotonic() - started > budget:
            return []
        line = exc.position[0] if getattr(exc, "position", None) else None
        return [_warning("xml", line, str(exc))]
    except SyntaxError as exc:
        if time.monotonic() - started > budget or source_name != "python":
            return []
        return [_warning("python", exc.lineno, exc.msg)]
    except json.JSONDecodeError as exc:
        if time.monotonic() - started > budget:
            return []
        return [_warning("json", exc.lineno, exc.msg)]
    except tomllib.TOMLDecodeError as exc:
        if time.monotonic() - started > budget:
            return []
        return [_warning("toml", _toml_line(exc), str(exc))]
    except Exception as exc:
        if time.monotonic() - started > budget:
            return []
        if source_name == "yaml":
            mark = getattr(exc, "problem_mark", None)
            problem = getattr(exc, "problem", None) or "YAML parse error."
            line = mark.line + 1 if mark is not None and type(getattr(mark, "line", None)) is int else None
            return [_warning("yaml", line, problem)]
        # Malformed text, parser resource errors and unavailable parsers never
        # turn into an edit failure or expose an implementation traceback.
        return []
    if time.monotonic() - started > budget:
        return []
    return []
