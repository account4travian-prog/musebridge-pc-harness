from pathlib import Path
import subprocess
import sys

root = Path.cwd()
files = [root / name for name in ("legacy.py", "api.py", "cli.py", "adapter.py", "test_names.py")]
all_bytes = b"".join(path.read_bytes() for path in files)
if b"legacy_label" in all_bytes or b"display_label" not in all_bytes:
    raise SystemExit("rename is incomplete")
for path in files:
    data = path.read_bytes()
    if b"\n" in data.replace(b"\r\n", b""):
        raise SystemExit(f"line endings changed in {path.name}")
result = subprocess.run([sys.executable, "-m", "compileall", "-q", "."], cwd=root, capture_output=True, text=True, timeout=30,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
if result.returncode:
    print((result.stdout + result.stderr)[-2000:])
    raise SystemExit(result.returncode)
tests = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=root, capture_output=True, text=True, timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
if tests.returncode:
    print((tests.stdout + tests.stderr)[-2000:])
    raise SystemExit(tests.returncode)
print("rename complete; CRLF preserved; compileall passed")
