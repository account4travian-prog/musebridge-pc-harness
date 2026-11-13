from pathlib import Path
import shutil
import subprocess

root = Path.cwd()
powershell = shutil.which("pwsh") or shutil.which("powershell")
if not powershell:
    raise SystemExit("PowerShell is unavailable")
result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                         "-File", str(root / "build.ps1")], cwd=root, capture_output=True, text=True, timeout=30,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
if result.returncode != 0:
    print((result.stdout + result.stderr)[-2000:])
    raise SystemExit(result.returncode)
output = root / "output file.txt"
if not output.is_file():
    raise SystemExit("space-containing output file was not created correctly")
data = output.read_bytes()
decoded = None
for encoding in ("utf-8-sig", "utf-16"):
    try:
        decoded = data.decode(encoding).strip()
        break
    except UnicodeDecodeError:
        continue
if decoded != "space-safe output":
    raise SystemExit("space-containing output file has the wrong contents")
print("PowerShell output verified")
