import json
import os
from pathlib import Path

root = Path.cwd()
expected = json.loads(os.environ.get("MUSEBRIDGE_EVAL_EXPECTED", "{}"))
code = expected.get("confirmation_code")
if not isinstance(code, str) or not code:
    raise SystemExit("fixture confirmation code was not supplied to the independent checker")
state = expected.get("fixture_state")
if not isinstance(state, dict):
    raise SystemExit("private fixture state was not supplied to the independent checker")
if state.get("saved") is not True or state.get("mode") != "advanced" or state.get("confirmation_code") != code:
    raise SystemExit("fixture did not record the requested setting")
if (root / "answer.txt").read_text(encoding="utf-8").strip() != code:
    raise SystemExit("answer.txt does not match the recorded confirmation code")
print("browser fixture state and answer verified")
