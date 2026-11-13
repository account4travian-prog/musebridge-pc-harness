from pathlib import Path

root = Path.cwd()
logs = list(root.glob("logs/*.log"))
if len(logs) != 300:
    raise SystemExit(f"expected 300 log files, found {len(logs)}")
matches = []
for path in logs:
    for line in path.read_text(encoding="ascii").splitlines():
        if "E-9281" in line:
            matches.append(line.split(" ", 1)[0])
if matches != ["2026-11-12T10:34:56Z"]:
    raise SystemExit("planted matching timestamp was not uniquely found")
if (root / "answer.txt").read_text(encoding="utf-8").strip() != matches[0]:
    raise SystemExit("answer.txt does not contain the exact timestamp")
print("planted timestamp verified")
