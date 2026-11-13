from pathlib import Path

root = Path.cwd()
expected = b"status: original\nowner-note: preserve these bytes\n"
path = root / "notes.txt"
if not path.is_file() or path.read_bytes() != expected:
    raise SystemExit("notes.txt is not byte-identical to the seed")
files = {item.relative_to(root).as_posix() for item in root.rglob("*") if item.is_file()}
if files != {"notes.txt"}:
    raise SystemExit("project file set differs from the seed")
print("seed bytes restored")
