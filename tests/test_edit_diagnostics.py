import json
from pathlib import Path
import time

import pytest

from musebridge import edit_diagnostics as diagnostics


@pytest.mark.parametrize("path,data,source,line", [
    ("src/broken.py", b"def work():\nreturn 1\n", "python", 2),
    ("settings.json", b'{\n  "items": ]\n}', "json", 2),
    ("settings.toml", b"enabled =\n", "toml", 1),
    ("document.xml", b"<root>\n  <item></root>", "xml", 2),
])
def test_supported_parsers_return_line_warning_without_path(tmp_path, path, data, source, line):
    private_path = str(tmp_path / "private-folder" / path)
    result = diagnostics.diagnose(private_path, data)
    assert len(result) == 1
    assert result[0]["source"] == source
    assert result[0]["line"] == line
    assert result[0]["message"]
    assert private_path not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)


@pytest.mark.parametrize("path,data", [
    ("valid.py", b"def work():\n    return 1\n"),
    ("valid.json", b'{"enabled": true, "count": 2}'),
    ("valid.toml", b"enabled = true\ncount = 2\n"),
    ("valid.xml", b"<root><item /></root>"),
])
def test_valid_supported_text_returns_no_warning(path, data):
    assert diagnostics.diagnose(path, data) == []


def test_yaml_base_loader_accepts_custom_tags_without_constructing_objects():
    pytest.importorskip("yaml")
    assert diagnostics.diagnose("config.yaml", b"value: !application/special marker\n") == []
    invalid = diagnostics.diagnose("config.yaml", b"root:\n  child: [\n")
    assert len(invalid) == 1
    assert invalid[0]["source"] == "yaml"
    assert invalid[0]["line"] is not None


def test_dense_yaml_uses_conservative_16k_cap_and_stays_under_budget(monkeypatch):
    yaml = pytest.importorskip("yaml")
    dense = ("a: 1\n" * (diagnostics.MAX_YAML_BYTES // 5)).encode()
    started = time.monotonic()
    assert diagnostics.diagnose("dense.yaml", dense) == []
    assert time.monotonic() - started < 0.5

    monkeypatch.setattr(yaml, "compose", lambda *_args, **_kwargs: pytest.fail("large YAML reached parser"))
    larger = ("a: 1\n" * 13000).encode()[:64 * 1024]
    started = time.monotonic()
    assert diagnostics.diagnose("dense.yaml", larger) == []
    assert time.monotonic() - started < 0.5


def test_bom_and_utf16_sources_are_decoded_before_syntax_check():
    utf8_bom = b"\xef\xbb\xbf" + b"def broken(:\n    pass\n"
    utf16 = "{\n  \"value\": ]\n}".encode("utf-16")
    assert diagnostics.diagnose("broken.py", utf8_bom)[0]["line"] == 1
    assert diagnostics.diagnose("broken.json", utf16)[0]["line"] == 2


def test_unsupported_html_and_binary_data_are_silently_skipped():
    assert diagnostics.diagnose("page.html", b"<div><span></div>") == []
    assert diagnostics.diagnose("broken.py", b"\x00\xff\x00") == []
    assert diagnostics.diagnose("binary.xml", b"\x00\xff\x00") == []


def test_four_mib_input_is_skipped_before_inprocess_parser(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics.ast, "parse", lambda *_args, **_kwargs: pytest.fail("large input reached parser"))
    data = b"not valid python\n" * (4 * 1024 * 1024 // 17 + 1)
    started = time.monotonic()
    assert diagnostics.diagnose(str(tmp_path / "large.py"), data[:4 * 1024 * 1024]) == []
    assert time.monotonic() - started < 0.5


def test_invalid_arguments_unknown_extensions_and_nonpositive_budget_are_noops():
    assert diagnostics.diagnose("file.py", "not bytes") == []
    assert diagnostics.diagnose("file.py", b"bad", budget_ms=0) == []
    assert diagnostics.diagnose("file.unknown", b"bad") == []


def test_powershell_parser_uses_fixed_stdin_script_and_short_timeout(monkeypatch):
    import musebridge.shells

    source = "Write-Output 'not interpolated into process args'\n"
    exe = r"C:\Program Files\PowerShell\pwsh.exe"
    observed = {}

    class FakeProcess:
        returncode = 0

        def communicate(self, input=None, timeout=None):
            observed["input"] = input
            observed["timeout"] = timeout
            return b"5\tUnexpectedToken\n", None

    def popen(argv, **kwargs):
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(musebridge.shells, "resolve_shell", lambda name: ("powershell", exe))
    monkeypatch.setattr(diagnostics.subprocess, "Popen", popen)
    result = diagnostics.diagnose("script.ps1", source.encode("utf-8"))
    assert result == [{"source": "powershell", "line": 5,
                       "message": "PowerShell parse error (UnexpectedToken)."}]
    assert observed["input"] == source.encode("utf-8")
    assert source not in observed["argv"][-1]
    assert observed["timeout"] <= 0.3
    assert observed["kwargs"]["stdin"] == diagnostics.subprocess.PIPE
    assert "-NoProfile" in observed["argv"]


def test_powershell_timeout_kills_parser_and_returns_no_warning(monkeypatch):
    import musebridge.shells

    class FakeProcess:
        returncode = None

        def __init__(self):
            self.calls = 0
            self.killed = False

        def communicate(self, input=None, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise diagnostics.subprocess.TimeoutExpired("pwsh", timeout)
            assert timeout <= 0.05
            return b"", None

        def kill(self):
            self.killed = True

    process = FakeProcess()
    monkeypatch.setattr(musebridge.shells, "resolve_shell", lambda name: ("powershell", "pwsh.exe"))
    monkeypatch.setattr(diagnostics.subprocess, "Popen", lambda *_args, **_kwargs: process)
    assert diagnostics.diagnose("slow.ps1", b"Write-Output 1") == []
    assert process.killed and process.calls == 2


def test_missing_powershell_and_entity_declarations_are_skipped(monkeypatch):
    import musebridge.shells

    def unavailable(_name):
        raise RuntimeError("missing")

    monkeypatch.setattr(musebridge.shells, "resolve_shell", unavailable)
    assert diagnostics.diagnose("script.ps1", b"Write-Output 1") == []
    assert diagnostics.diagnose("doc.xml", b"<!DOCTYPE x [<!ENTITY a 'a'>]><x>&a;</x>") == []
