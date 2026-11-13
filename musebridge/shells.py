"""Shell discovery and direct command-line construction for Windows jobs."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import shutil
import subprocess


_SHELLS = ('powershell', 'cmd', 'bash')


def normalize_shell(shell: str | None) -> str:
    """Normalize an accepted shell name, defaulting to PowerShell."""
    value = 'powershell' if shell is None else shell
    if not isinstance(value, str):
        raise ValueError('shell must be powershell, cmd, or bash')
    value = value.casefold()
    if value not in _SHELLS:
        raise ValueError('shell must be powershell, cmd, or bash')
    return value


def resolve_shell(shell: str | None = None) -> tuple[str, str]:
    """Return the normalized shell and its executable path, if installed."""
    name = normalize_shell(shell)
    if name == 'powershell':
        executable = shutil.which('pwsh.exe') or shutil.which('powershell.exe')
    elif name == 'cmd':
        executable = shutil.which('cmd.exe')
    else:
        executable = _git_bash_path()
    if not executable:
        raise RuntimeError('shell_unavailable')
    return name, executable


def _git_bash_path() -> str | None:
    """Find Git Bash relative to Git for Windows; never select WSL bash."""
    git = shutil.which('git.exe') or shutil.which('git')
    if not git:
        return None
    git_path = Path(git).resolve()
    candidates = (
        git_path.parent / 'bash.exe',
        git_path.parent.parent / 'bin' / 'bash.exe',
        git_path.parent.parent / 'usr' / 'bin' / 'bash.exe',
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def command_argv(shell: str, executable: str, command: str) -> list[str]:
    """Build interpreter argv without asking CreateProcess to use a shell."""
    name = normalize_shell(shell)
    if not isinstance(command, str):
        raise ValueError('command must be a string')
    if name == 'powershell':
        script = (
            "$ProgressPreference='SilentlyContinue'; "
            "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
            "$OutputEncoding=[Text.UTF8Encoding]::new($false); "
            "$ErrorActionPreference='Stop'; "
            + command
            + "\nif ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }"
        )
        return [executable, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script]
    if name == 'cmd':
        return [executable, '/d', '/s', '/c', 'chcp 65001 >NUL & ' + command]
    return [executable, '--noprofile', '--norc', '-c', command]


def command_line(shell: str, executable: str, command: str) -> str:
    """Build the Windows command line, honoring cmd.exe's special /c quotes."""
    name = normalize_shell(shell)
    if name == 'cmd':
        script = 'chcp 65001 >NUL & ' + command
        return f'"{executable}" /d /s /c "{script}"'
    return subprocess.list2cmdline(command_argv(name, executable, command))


@lru_cache(maxsize=1)
def describe_shells() -> dict[str, dict[str, object]]:
    """Return installed shell names and cached versions without exposing paths."""
    result = {}
    for name in _SHELLS:
        try:
            _normalized, executable = resolve_shell(name)
        except RuntimeError:
            result[name] = {'available': False, 'version': None}
            continue
        version = _read_version(name, executable)
        result[name] = {'available': True, 'version': version}
    return result


def _read_version(shell: str, executable: str) -> str | None:
    if shell == 'powershell':
        arguments = [executable, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command',
                     '$PSVersionTable.PSVersion.ToString()']
    elif shell == 'cmd':
        arguments = [executable, '/d', '/c', 'ver']
    else:
        arguments = [executable, '--version']
    try:
        completed = subprocess.run(
            arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
            timeout=3, check=False,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = next((item.strip() for item in completed.stdout.splitlines() if item.strip()), None)
    return line[:120] if line else None
