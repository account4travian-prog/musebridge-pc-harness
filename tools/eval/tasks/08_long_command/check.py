from pathlib import Path
import subprocess
import sys

root = Path.cwd()
script = root / "long_check.py"
if "time.sleep(70)" not in script.read_text(encoding="utf-8"):
    raise SystemExit("seeded 70-second duration was changed")
result = subprocess.run([sys.executable, str(script)], cwd=root, capture_output=True, text=True, timeout=90,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
lines = [line for line in result.stdout.splitlines() if line.startswith("SUMMARY:")]
if result.returncode != 0 or lines != ["SUMMARY: harness-eval-70s-complete"]:
    raise SystemExit("70-second check did not produce the expected summary")
if (root / "answer.txt").read_text(encoding="utf-8").strip() != lines[0]:
    raise SystemExit("answer.txt does not match the final summary")
print("70-second command and answer verified")
