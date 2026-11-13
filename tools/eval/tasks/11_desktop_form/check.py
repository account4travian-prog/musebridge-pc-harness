import json
from pathlib import Path

expected = {"name": "Test fixture", "topic": "harness", "amount": "3"}
saved = json.loads((Path.cwd() / "saved.json").read_text(encoding="utf-8"))
if saved != expected:
    raise SystemExit("fixture form did not save the expected fields")
print("desktop fixture fields verified")
