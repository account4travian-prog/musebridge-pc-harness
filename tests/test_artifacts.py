import hashlib

import pytest

from musebridge.artifacts import MAX_ARTIFACT_BYTES, MAX_TEXT_BYTES, Artifacts
from musebridge.catalog import Catalog
from musebridge.tooling import ToolRouter


def test_import_is_an_immutable_snapshot_owned_by_one_conversation(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("before", encoding="utf-8")
    catalog = Catalog(tmp_path / "state")
    first = catalog.create_conversation("First")['id']
    second = catalog.create_conversation("Second")['id']
    artifacts = Artifacts(catalog)
    router = ToolRouter(catalog, tmp_path / "state")
    try:
        item = artifacts.import_file(first, source)
        source.write_text("after!", encoding="utf-8")
        assert artifacts.data(first, item['id']) == b"before"
        assert item['sha256'] == hashlib.sha256(b"before").hexdigest()
        with pytest.raises(ValueError, match="does not belong"):
            artifacts.get(second, item['id'])
        with pytest.raises(ValueError, match="does not belong"):
            artifacts.read(second, item['id'])
        denied = router.call("artifact.read", {"conversation_id": second, "artifact_id": item['id']})
        assert denied['ok'] is False
        assert "does not belong" in denied['error']['message']
    finally:
        router.close()
        catalog.close()


def test_utf8_text_pages_and_unknown_binary_have_honest_limits(tmp_path):
    text = tmp_path / "unicode.txt"
    text.write_text("a猫🙂z", encoding="utf-8")
    binary = tmp_path / "sample.pdf"
    binary.write_bytes(b"%PDF-1.7\x00not extracted")
    catalog = Catalog(tmp_path / "state")
    cid = catalog.create_conversation("Docs")['id']
    artifacts = Artifacts(catalog)
    try:
        text_item = artifacts.import_file(cid, text)
        page = artifacts.read(cid, text_item['id'], offset=1, length=2)
        assert page['text'] == "猫🙂"
        assert page['next_offset'] == 3
        assert "Untrusted" in page['handling']

        binary_item = artifacts.import_file(cid, binary)
        result = artifacts.read(cid, binary_item['id'])
        assert result['text'] is None
        assert "Malformed" in result['limitation']
        assert result['kind'] == "document"
    finally:
        catalog.close()


def test_source_type_and_size_bounds_are_enforced(tmp_path):
    catalog = Catalog(tmp_path / "state")
    cid = catalog.create_conversation("Bounds")['id']
    artifacts = Artifacts(catalog)
    too_large_binary = tmp_path / "large.bin"
    too_large_binary.write_bytes(b"x" * (MAX_ARTIFACT_BYTES + 1))
    too_large_text = tmp_path / "large.txt"
    too_large_text.write_bytes(b"x" * (MAX_TEXT_BYTES + 1))
    folder = tmp_path / "folder"
    folder.mkdir()
    try:
        with pytest.raises(ValueError, match="regular file"):
            artifacts.import_file(cid, folder)
        with pytest.raises(ValueError, match="8 MiB"):
            artifacts.import_file(cid, too_large_binary)
        with pytest.raises(ValueError, match="256 KiB"):
            artifacts.import_file(cid, too_large_text)
    finally:
        catalog.close()


def test_router_rejects_private_project_paths_for_artifact_import(tmp_path):
    root = tmp_path / "project"
    (root / ".local").mkdir(parents=True)
    (root / ".local" / "secret.txt").write_text("private", encoding="utf-8")
    catalog = Catalog(tmp_path / "state")
    project = catalog.create_project("Private", root)
    cid = catalog.create_conversation("Private", project['id'])['id']
    router = ToolRouter(catalog, tmp_path / "state")
    try:
        result = router.call("artifact.add", {"conversation_id": cid, "path": ".local/secret.txt"})
        assert result['ok'] is False
        assert "private" in result['error']['message'].lower()
        assert router.artifacts.list(cid) == []
    finally:
        router.close(); catalog.close()


def test_clearing_acknowledged_drafts_by_id_preserves_newer_draft(tmp_path):
    catalog = Catalog(tmp_path / "state")
    cid = catalog.create_conversation("Drafts")['id']
    artifacts = Artifacts(catalog)
    older_path = tmp_path / "older.md"
    newer_path = tmp_path / "newer.md"
    older_path.write_text("older", encoding="utf-8")
    newer_path.write_text("newer", encoding="utf-8")
    try:
        older = artifacts.import_file(cid, older_path)
        newer = artifacts.import_file(cid, newer_path)
        artifacts.attach(cid, older['id'])
        artifacts.attach(cid, newer['id'])
        artifacts.clear_drafts(cid, [older['id']])
        assert [row['id'] for row in artifacts.drafts(cid)] == [newer['id']]
    finally:
        catalog.close()
