import json
import os
from pathlib import Path

expected = json.loads(os.environ.get("MUSEBRIDGE_EVAL_EXPECTED", "{}"))
code = expected.get("code")
if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
    raise SystemExit("fixture code was not supplied to the independent checker")
answer = (Path.cwd() / "answer.txt").read_text(encoding="utf-8").strip()
if answer != code:
    raise SystemExit("answer.txt does not match the pixel-only code")
print("vision answer verified")
