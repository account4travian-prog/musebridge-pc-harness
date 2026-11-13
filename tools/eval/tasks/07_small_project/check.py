from pathlib import Path
import re
import subprocess
import sys

root = Path.cwd()
if not (root / "tinycalc" / "__init__.py").is_file():
    raise SystemExit("tinycalc package is missing")
result = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=root, capture_output=True, text=True, timeout=45,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
output = result.stdout + result.stderr
print(output[-2000:])
match = re.search(r"(\d+) passed", output)
if result.returncode != 0 or not match or int(match.group(1)) < 3:
    raise SystemExit("pytest did not pass at least three tests")
