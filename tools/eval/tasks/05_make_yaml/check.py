from pathlib import Path
import yaml

root = Path.cwd()
makefile = (root / "Makefile").read_bytes()
if b"check:\n\tpython -m pytest tests -q\n" not in makefile and b"check:\r\n\tpython -m pytest tests -q\r\n" not in makefile:
    raise SystemExit("check recipe is missing or not tab-indented")
data = yaml.safe_load((root / "settings.yaml").read_text(encoding="utf-8"))
if data["application"]["deploy"]["retry"]["workers"] != 3:
    raise SystemExit("nested worker count is not 3")
print("tab recipe and nested YAML value verified")
