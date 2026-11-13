"""Bounded, ignore-aware tree listing and search for selected workspaces."""

from __future__ import annotations

import fnmatch
import json
import os
import regex as safe_regex
import shutil
import subprocess
import threading
import time
from pathlib import Path

from pathspec.gitignore import GitIgnoreSpec

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_FILES = 5000
MAX_SECONDS = 8.0
MAX_OUTPUT_BYTES = 20_000
MAX_DEPTH = 16
MAX_PATTERN_CHARS = 4096
MAX_PATTERN_JSON_BYTES = 12_000
MAX_PATH_METADATA_CHARS = 256
_PRIVATE_NAMES = {".git", ".local"}


def _sort_key(path: Path) -> tuple[str, str]:
    return (path.name.casefold(), path.name)


def _serialized_size(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii"))


def _path_metadata(path: str) -> dict:
    if len(path) <= MAX_PATH_METADATA_CHARS:
        return {"path": path, "path_is_preview": False}
    edge = MAX_PATH_METADATA_CHARS // 3
    preview = path[:edge] + "…[path preview]…" + path[-edge:]
    return {"path": preview, "path_is_preview": True, "path_length_chars": len(path)}


def _spec(path: Path) -> GitIgnoreSpec | None:
    ignore_file = path / ".gitignore"
    try:
        if ignore_file.is_file() and not ignore_file.is_symlink():
            return GitIgnoreSpec.from_lines(ignore_file.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        pass
    return None


def _ignored(specs: list[tuple[Path, GitIgnoreSpec]], target: Path, is_dir: bool) -> bool:
    ignored = False
    for base, spec in specs:
        try:
            relative = target.relative_to(base).as_posix()
        except ValueError:
            continue
        check = spec.check_file(relative + ("/" if is_dir else ""))
        if check.include is not None:
            ignored = check.include
    return ignored


def _walk(workspace, start: Path, *, depth: int = MAX_DEPTH, deadline: float | None = None, status: dict | None = None):
    """Yield (path, is_dir, level); all entries are validated and symlinks skipped."""
    root = workspace.root
    initial_specs: list[tuple[Path, GitIgnoreSpec]] = []
    chain = [root]
    relative_parts = start.relative_to(root).parts
    chain.extend(root.joinpath(*relative_parts[:index]) for index in range(1, len(relative_parts) + 1))
    for directory in chain:
        try:
            directory = workspace._validate_operation_path(directory)
        except (OSError, ValueError):
            continue
        spec = _spec(directory)
        if spec:
            initial_specs.append((directory, spec))
    stack = [(start, 0, initial_specs)]
    scanned = 0
    while stack:
        directory, level, specs = stack.pop()
        if level >= depth or (deadline is not None and time.monotonic() >= deadline):
            continue
        local = _spec(directory)
        local_specs = specs + ([(directory, local)] if local else [])
        try:
            entries = []
            with os.scandir(directory) as scanner:
                for entry in scanner:
                    scanned += 1
                    if deadline is not None and time.monotonic() >= deadline:
                        if status is not None:
                            status["capped"] = True
                        break
                    if scanned > MAX_FILES:
                        if status is not None:
                            status["capped"] = True
                        break
                    entries.append(entry)
        except OSError:
            continue
        entries.sort(key=lambda e: (e.name.casefold(), e.name))
        children = []
        scan_cap_hit = bool(status and status.get("capped"))
        for entry in entries:
            if deadline is not None and time.monotonic() >= deadline:
                scan_cap_hit = True
                if status is not None:
                    status["capped"] = True
                break
            if entry.name.casefold() in _PRIVATE_NAMES or entry.is_symlink():
                continue
            try:
                target = workspace._validate_operation_path(entry.path)
                is_dir = entry.is_dir(follow_symlinks=False)
            except (OSError, ValueError):
                continue
            if _ignored(local_specs, target, is_dir):
                continue
            children.append((target, is_dir, level + 1))
        for item in children:
            yield item
        if scan_cap_hit:
            if status is not None:
                status["capped"] = True
            return
        for target, is_dir, child_level in reversed(children):
            if is_dir:
                stack.append((target, child_level, local_specs))


def _checked_dir(workspace, path: str) -> Path:
    target = workspace._validate_operation_path(path)
    if not target.is_dir():
        raise NotADirectoryError(str(target))
    return target


def list_tree(workspace, path: str = ".", depth: int = 2, limit: int = 200) -> dict:
    if not isinstance(depth, int) or isinstance(depth, bool) or not 0 <= depth <= MAX_DEPTH:
        raise ValueError(f"depth must be between 0 and {MAX_DEPTH}")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")
    limit = min(limit, 200)
    start = _checked_dir(workspace, path)
    deadline = time.monotonic() + MAX_SECONDS
    scan_status = {}
    entries = list(_walk(workspace, start, depth=depth, deadline=deadline, status=scan_status))
    # Stable directories-first ordering at each indentation level.
    by_parent: dict[Path, list[tuple[Path, bool, int]]] = {}
    for item in entries:
        by_parent.setdefault(item[0].parent, []).append(item)
    for values in by_parent.values():
        values.sort(key=lambda x: (not x[1], *_sort_key(x[0])))
    flattened = []

    def add(parent: Path):
        for item in by_parent.get(parent, []):
            flattened.append(item)
            if item[1]:
                add(item[0])

    add(start)
    scan_capped = scan_status.get("capped", False) or time.monotonic() >= deadline
    truncated = len(flattened) > limit or scan_capped
    shown = flattened[:limit]
    lines = []
    shown_dirs = {item[0] for item in shown if item[1]}
    for target, is_dir, level in shown:
        indent = "  " * (level - 1)
        label = target.name + ("/" if is_dir else "")
        if is_dir and truncated:
            descendants = sum(1 for p, _, _ in flattened[limit:] if p.is_relative_to(target))
            if descendants:
                label += f" ({descendants} entries not shown)"
        lines.append(indent + label)
    omitted = None if scan_capped else max(0, len(flattened) - len(shown))
    visible_count = len(lines)
    path_info = _path_metadata(start.relative_to(workspace.root).as_posix() or ".")
    while True:
        suffix = []
        if truncated:
            suffix = [f"… {omitted} entries omitted" if omitted is not None
                      else "… additional entries omitted (scan capped)"]
        payload = {**path_info, "tree": "\n".join(lines + suffix) or "(empty)",
                   "entries": visible_count, "total_entries": len(flattened),
                   "omitted_count": omitted, "truncated": truncated,
                   "count_exact": not scan_capped, "engine": "python"}
        if _serialized_size(payload) <= MAX_OUTPUT_BYTES:
            return payload
        if not lines:
            # path_info is already a marked preview, so this only protects
            # against unexpected future metadata additions.
            payload["tree"] = "(tree omitted to fit output budget)"
            payload["tree_is_preview"] = True
            payload["truncated"] = True
            if _serialized_size(payload) <= MAX_OUTPUT_BYTES:
                return payload
            raise ValueError("tree metadata exceeds the 20,000 byte response budget")
        lines.pop()
        visible_count -= 1
        truncated = True
        if omitted is not None:
            omitted += 1


def _is_ignored(workspace, path: Path, is_dir: bool) -> bool:
    specs = []
    rel = path.relative_to(workspace.root)
    current = workspace.root
    spec = _spec(current)
    if spec:
        specs.append((current, spec))
    for part in rel.parts[:-1] if not is_dir else rel.parts[:-1]:
        current = current / part
        spec = _spec(current)
        if spec:
            specs.append((current, spec))
    return _ignored(specs, path, is_dir)


def _files(workspace, start: Path, glob: str | None, deadline: float, status: dict | None = None):
    count = 0
    if start.is_file():
        if _is_ignored(workspace, start, False) or start.stat().st_size > MAX_FILE_BYTES:
            return
        relative = start.relative_to(workspace.root).as_posix()
        if not glob or fnmatch.fnmatchcase(relative, glob) or Path(relative).match(glob):
            yield start, relative
        return
    for path, is_dir, _ in _walk(workspace, start, deadline=deadline, status=status):
        if is_dir:
            continue
        count += 1
        if count > MAX_FILES:
            return
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        relative = path.relative_to(workspace.root).as_posix()
        if glob and not (fnmatch.fnmatchcase(relative, glob) or Path(relative).match(glob)):
            continue
        yield path, relative


def search_files(workspace, pattern: str, path: str = ".", glob: str | None = None,
                 mode: str = "content", context: int = 0, literal: bool = False,
                 ignore_case: bool = False, max_results: int = 50) -> dict:
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("pattern must be a non-empty string")
    if len(pattern) > MAX_PATTERN_CHARS:
        raise ValueError(f"pattern must be at most {MAX_PATTERN_CHARS} characters")
    if len(json.dumps(pattern, ensure_ascii=True, separators=(",", ":")).encode("ascii")) > MAX_PATTERN_JSON_BYTES:
        raise ValueError(f"pattern JSON encoding must be at most {MAX_PATTERN_JSON_BYTES} bytes")
    if mode not in {"content", "files", "count"}:
        raise ValueError("mode must be content, files, or count")
    if not isinstance(literal, bool) or not isinstance(ignore_case, bool):
        raise ValueError("literal and ignore_case must be booleans")
    if not isinstance(context, int) or isinstance(context, bool) or not 0 <= context <= 10:
        raise ValueError("context must be between 0 and 10")
    if not isinstance(max_results, int) or isinstance(max_results, bool) or max_results < 1:
        raise ValueError("max_results must be positive")
    max_results = min(max_results, 50)
    if glob is not None and not isinstance(glob, str):
        raise ValueError("glob must be a string")
    if glob is not None and len(glob) > MAX_PATTERN_CHARS:
        raise ValueError(f"glob must be at most {MAX_PATTERN_CHARS} characters")
    start = workspace._validate_operation_path(path)
    if not start.exists():
        raise FileNotFoundError(str(start))
    supplied = Path(path).expanduser()
    lexical = supplied if supplied.is_absolute() else workspace.root / supplied
    try:
        lexical = lexical.absolute()
        for candidate in [lexical, *lexical.parents]:
            if candidate == workspace.root.parent:
                break
            if candidate.is_symlink():
                raise ValueError("search does not follow symlinks")
            if candidate == workspace.root:
                break
    except OSError:
        pass
    flags = safe_regex.VERSION0 | (safe_regex.IGNORECASE if ignore_case else 0)
    try:
        safe_regex.compile(safe_regex.escape(pattern) if literal else pattern, flags)
    except safe_regex.error as exc:
        if not literal:
            raise ValueError(f"invalid regular expression: {exc}") from exc
        raise ValueError("pattern could not be compiled") from exc
    deadline = time.monotonic() + MAX_SECONDS
    rg = shutil.which("rg")
    private_roots = getattr(workspace, "_private_roots", ())
    if any(root != workspace.root and root.is_relative_to(workspace.root) for root in private_roots):
        rg = None
    if rg:
        return _search_rg(workspace, rg, pattern, start, glob, mode, context, literal, ignore_case, max_results, deadline)
    expression = safe_regex.compile(safe_regex.escape(pattern) if literal else pattern, flags)
    grouped = []
    total = 0
    files_scanned = 0
    capped = False
    timed_out = False
    scan_status = {}
    for file_path, relative in _files(workspace, start, glob, deadline, scan_status):
        files_scanned += 1
        try:
            raw = file_path.read_bytes()
        except OSError:
            continue
        if b"\0" in raw:
            continue
        lines = raw.decode("utf-8", errors="replace").splitlines()
        matches = []
        for index, line in enumerate(lines):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                if expression.search(line, timeout=remaining):
                    matches.append(index)
            except TimeoutError:
                timed_out = True
                break
        if not matches:
            continue
        if mode == "files":
            total += 1
            if len(grouped) < max_results:
                grouped.append({"path": relative})
            else:
                capped = True
        elif mode == "count":
            total += len(matches)
            if len(grouped) < max_results:
                grouped.append({"path": relative, "count": len(matches)})
            else:
                capped = True
        else:
            room = max_results - sum(len(group["matches"]) for group in grouped)
            take = matches[:max(0, room)]
            total += len(matches)
            if take:
                grouped.append({"path": relative, "matches": [
                    {"line": i + 1, "text": lines[i][:2048],
                     "before": lines[max(0, i-context):i], "after": lines[i+1:i+1+context]}
                    for i in take]})
            if len(take) < len(matches) or sum(len(g.get("matches", [])) for g in grouped) >= max_results:
                capped = True
        if timed_out or time.monotonic() >= deadline:
            capped = True
            break
    if time.monotonic() >= deadline:
        timed_out = True
        capped = True
    if scan_status.get("capped") or files_scanned >= MAX_FILES:
        capped = True
    unknown_omissions = timed_out or scan_status.get("capped", False)
    omitted = None if unknown_omissions else (max(0, total - max_results) if capped else 0)
    result = {"pattern": pattern, "mode": mode, "results": grouped, "count": total,
              "omitted_count": omitted, "truncated": capped, "engine": "python",
              "files_scanned": files_scanned,
              "count_exact": not unknown_omissions and files_scanned < MAX_FILES and time.monotonic() < deadline}
    if capped:
        result["hint"] = "Narrow the search with a more specific glob or pattern."
    return _trim_result(result)


def _search_rg(workspace, rg, pattern, start, glob, mode, context, literal, ignore_case, max_results, deadline):
    # Use rg for its mature gitignore/regex behavior. JSON lines are streamed to
    # a bounded in-memory buffer; kill the child when either cap is reached.
    reserved = "{.git,.local,credentials,credential,local-data}"
    args = [rg, "--json", "--hidden", "--no-require-git", "--color", "never",
            "--iglob", f"!**/{reserved}/**", "--iglob", f"!{reserved}/**",
            "--iglob", f"!**/{reserved}", "--iglob", f"!{reserved}",
            "--max-filesize", "4M", "--max-depth", str(MAX_DEPTH)]
    if literal:
        args.append("--fixed-strings")
    if ignore_case:
        args.append("--ignore-case")
    if glob:
        args.extend(["--glob", glob])
    if context and mode == "content":
        args.extend(["--context", str(context)])
    args.extend(["--", pattern, str(start)])
    try:
        process = subprocess.Popen(args, cwd=workspace.root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except OSError as exc:
        raise OSError("ripgrep could not be started") from exc
    chunks = bytearray()
    output_error = []

    def drain():
        assert process.stdout is not None
        while True:
            chunk = process.stdout.read(65536)
            if not chunk:
                return
            if len(chunks) + len(chunk) > 8 * 1024 * 1024:
                output_error.append(True)
                process.kill()
                return
            chunks.extend(chunk)

    reader = None
    try:
        reader = threading.Thread(target=drain, daemon=False)
        reader.start()
        while process.poll() is None:
            if time.monotonic() >= deadline:
                process.kill()
                raise OSError("search exceeded its time limit")
            if output_error:
                raise OSError("search output exceeded its 8 MiB processing limit")
            time.sleep(0.025)
        returncode = process.wait()
        reader.join()
        if output_error:
            raise OSError("search output exceeded its 8 MiB processing limit")
    except Exception:
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if reader is not None and reader.is_alive():
            reader.join()
    if returncode not in (0, 1):
        raise OSError("ripgrep search failed")
    grouped: dict[str, dict] = {}
    context_lines: dict[tuple[str, int], str] = {}
    match_events = []
    for raw_line in bytes(chunks).splitlines():
        try:
            event = json.loads(raw_line)
        except Exception:
            continue
        if event.get("type") not in {"match", "context"}:
            continue
        data = event.get("data", {})
        rawpath = data.get("path", {}).get("text")
        if not rawpath:
            continue
        try:
            target = workspace._validate_operation_path(rawpath)
        except (OSError, ValueError):
            continue
        relative = target.relative_to(workspace.root).as_posix()
        line_number = data.get("line_number")
        text = data.get("lines", {}).get("text", "").rstrip("\r\n")[:2048]
        if event.get("type") == "context":
            context_lines[(relative, line_number)] = text
        else:
            match_events.append((relative, line_number, text))
    if mode == "files":
        for relative, _, _ in match_events:
            grouped.setdefault(relative, {"path": relative})
        capped_total = len(grouped)
        results = list(grouped.values())[:max_results]
    elif mode == "count":
        for relative, _, _ in match_events:
            grouped.setdefault(relative, {"path": relative, "count": 0})["count"] += 1
        capped_total = sum(row["count"] for row in grouped.values())
        results = list(grouped.values())[:max_results]
    else:
        for relative, line_number, text in match_events:
            if len(match_events) > max_results and sum(len(g["matches"]) for g in grouped.values()) >= max_results:
                break
            before = [context_lines[(relative, n)] for n in range(max(1, line_number-context), line_number)
                      if (relative, n) in context_lines]
            after = [context_lines[(relative, n)] for n in range(line_number+1, line_number+context+1)
                     if (relative, n) in context_lines]
            grouped.setdefault(relative, {"path": relative, "matches": []})["matches"].append(
                {"line": line_number, "text": text, "before": before, "after": after})
        capped_total = len(match_events)
        results = list(grouped.values())
    truncated = capped_total > max_results
    omitted = max(0, capped_total - max_results)
    result = {"pattern": pattern, "mode": mode, "results": results, "count": capped_total,
              "omitted_count": omitted, "truncated": truncated, "engine": "rg"}
    if truncated:
        result["hint"] = "Narrow the search with a more specific glob or pattern."
    return _trim_result(result)


def _trim_result(result: dict) -> dict:
    while len(json.dumps(result, ensure_ascii=True).encode("ascii")) > MAX_OUTPUT_BYTES and result.get("results"):
        removed = result["results"].pop()
        result["truncated"] = True
        amount = len(removed.get("matches", [])) if isinstance(removed, dict) else 1
        current_omitted = result.get("omitted_count", 0)
        result["omitted_count"] = None if current_omitted is None else current_omitted + max(1, amount)
        result["hint"] = "Output was capped; narrow the search with a more specific glob or pattern."
    return result
