from pathlib import Path

import yaml


SKILL = (Path(__file__).resolve().parents[1] / "docs" / "muse-skill" /
         "gadget-musebridge-windows-pc" / "SKILL.md")


def test_muse_windows_skill_metadata_and_boundaries():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, frontmatter, body = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)

    assert metadata["name"] == "gadget-musebridge-windows-pc"
    assert metadata["description"]
    assert len(body.split()) < 1200
    for phrase in (
        "bridge.context", "file.search", "file.read", "file.edit", "shell.run",
        "shell.job", "task.set", "task.verify", "CRLF", "Git Bash", "Stop Work",
        "unknown outcome", "actual image", "Manual-review tasks require the owner",
    ):
        assert phrase in body
