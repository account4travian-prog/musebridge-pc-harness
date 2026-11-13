from pathlib import Path
import hashlib
import subprocess
import sys

root = Path.cwd()
test_file = root / "tests" / "test_counter.py"
expected_hash = "6ef6eb1179961a026534df298fe3edce61960c3bbaff82a9bf399b68a7b6de2b"
if hashlib.sha256(test_file.read_bytes()).hexdigest() != expected_hash:
    raise SystemExit("immutable test file changed")
result = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=root, capture_output=True, text=True, timeout=45,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
print((result.stdout + result.stderr)[-2000:])
raise SystemExit(result.returncode)
