from pathlib import Path
import subprocess
import sys

root = Path.cwd()
flagged = subprocess.run([sys.executable, "cli.py", "--shout"], cwd=root, capture_output=True, text=True, timeout=10,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
plain = subprocess.run([sys.executable, "cli.py"], cwd=root, capture_output=True, text=True, timeout=10,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
if flagged.returncode != 0 or flagged.stdout.strip() != "HELLO":
    raise SystemExit("--shout output was not HELLO")
if plain.returncode != 0 or plain.stdout.strip() != "hello":
    raise SystemExit("default output changed")
if "--shout" not in (root / "README.md").read_text(encoding="utf-8"):
    raise SystemExit("README does not document --shout")
print("CLI behavior and README verified")
