import hashlib
from io import BytesIO
from types import SimpleNamespace
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from pypdf import PdfWriter

from musebridge.artifacts import Artifacts
from musebridge.catalog import Catalog
from musebridge import documents
from musebridge.documents import DocumentExtractionError


def pdf_bytes(text="Readable PDF text"):
    # A small, genuine one-page PDF with a content stream.
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n",
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        b"5 0 obj << /Length " + str(len(stream)).encode() + b" >> stream\n" + stream + b"\nendstream endobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out)); out.extend(obj)
    xref = len(out)
    out.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]: out.extend(f"{offset:010d} 00000 n \n".encode())
    out.extend(b"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n")
    return bytes(out)


def docx_bytes():
    body = '''<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:r><w:t>Unicode 猫 🙂</w:t></w:r></w:p>
<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table cell</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Second cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
</w:body></w:document>'''
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr("word/document.xml", body)
    return buffer.getvalue()


def test_pdf_and_docx_text_pages_keep_original_bytes_and_hash(tmp_path):
    catalog = Catalog(tmp_path / "state"); cid = catalog.create_conversation("Docs")["id"]
    store = Artifacts(catalog)
    try:
        for name, data, expected in (("file.pdf", pdf_bytes(), "Readable PDF text"),
                                     ("file.docx", docx_bytes(), "Unicode 猫 🙂")):
            path = tmp_path / name; path.write_bytes(data)
            item = store.import_file(cid, path)
            assert item["kind"] == "document"
            assert store.data(cid, item["id"]) == data
            assert item["sha256"] == hashlib.sha256(data).hexdigest()
            result = store.read(cid, item["id"], length=100)
            assert expected in result["text"]
            if name.endswith("docx"):
                assert "Table cell" in result["text"] and "Second cell" in result["text"]
    finally: catalog.close()


def test_encrypted_pdf_malformed_pdf_and_bad_zip_fail_clearly(tmp_path):
    writer = PdfWriter(); writer.add_blank_page(width=100, height=100); writer.encrypt("secret")
    encrypted = BytesIO(); writer.write(encrypted)
    catalog = Catalog(tmp_path / "state"); cid = catalog.create_conversation("Docs")["id"]; store = Artifacts(catalog)
    try:
        for name, payload, phrase in (("locked.pdf", encrypted.getvalue(), "Password-protected"),
                                      ("broken.pdf", b"not a pdf", "Malformed"),
                                      ("broken.docx", b"PK\x03\x04bad zip", "Malformed DOCX")):
            path = tmp_path / name; path.write_bytes(payload); item = store.import_file(cid, path)
            result = store.read(cid, item["id"])
            assert result["text"] is None and phrase in result["limitation"]
    finally: catalog.close()


def test_scanned_pdf_says_no_ocr_was_performed(tmp_path):
    writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
    source = BytesIO(); writer.write(source)
    path = tmp_path / "scan.pdf"; path.write_bytes(source.getvalue())
    catalog = Catalog(tmp_path / "state"); cid = catalog.create_conversation("Docs")["id"]; store = Artifacts(catalog)
    try:
        item = store.import_file(cid, path); result = store.read(cid, item["id"])
        assert "no OCR was performed" in result["limitation"]
    finally: catalog.close()


def test_extractor_timeout_and_output_bounds(monkeypatch, tmp_path):
    def timeout(*args, **kwargs):
        raise __import__("subprocess").TimeoutExpired(args[0], 8)
    monkeypatch.setattr(documents.subprocess, "run", timeout)
    with pytest.raises(DocumentExtractionError, match="timed out"):
        documents.extract_document(b"file", "pdf")

    def oversized(args, **kwargs):
        kwargs["stdout"].write(b"x" * (documents.MAX_WORKER_OUTPUT + 1))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(documents.subprocess, "run", oversized)
    with pytest.raises(DocumentExtractionError, match="output exceeded"):
        documents.extract_document(b"file", "pdf")


def test_docx_with_entity_declaration_is_rejected(tmp_path):
    buffer = BytesIO()
    xml = '''<!DOCTYPE x [<!ENTITY x "expanded">]><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body></w:document>'''
    with ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    path = tmp_path / "entities.docx"; path.write_bytes(buffer.getvalue())
    catalog = Catalog(tmp_path / "state"); cid = catalog.create_conversation("Docs")["id"]; store = Artifacts(catalog)
    try:
        item = store.import_file(cid, path)
        result = store.read(cid, item["id"])
        assert result["text"] is None
        assert "entities are not supported" in result["limitation"]
    finally: catalog.close()


def test_document_pages_reuse_successful_extraction_cache(tmp_path, monkeypatch):
    import musebridge.artifacts as artifacts_module

    catalog = Catalog(tmp_path / "state"); cid = catalog.create_conversation("Docs")["id"]
    store = Artifacts(catalog); path = tmp_path / "cached.pdf"; original = pdf_bytes(); path.write_bytes(original)
    calls = 0; actual = artifacts_module.extract_document
    def count_calls(data, kind):
        nonlocal calls
        calls += 1
        return actual(data, kind)
    monkeypatch.setattr(artifacts_module, "extract_document", count_calls)
    try:
        item = store.import_file(cid, path)
        first = store.read(cid, item["id"], offset=0, length=6)
        second = store.read(cid, item["id"], offset=6, length=7)
        assert first["text"] + second["text"] == "Readable PDF "
        assert calls == 1
        assert catalog.rows("SELECT extractor_version FROM artifact_text_cache_v1 WHERE artifact_id=?", (item["id"],))
        assert store.data(cid, item["id"]) == original
        assert item["sha256"] == hashlib.sha256(original).hexdigest()
    finally: catalog.close()


def test_transient_extraction_failure_is_not_cached_and_failed_background_attach_keeps_draft(tmp_path, monkeypatch):
    import musebridge.artifacts as artifacts_module
    from musebridge.artifacts_ui import _finish_document_attach

    catalog = Catalog(tmp_path / "state"); cid = catalog.create_conversation("Docs")["id"]
    store = Artifacts(catalog); path = tmp_path / "retry.docx"; path.write_bytes(docx_bytes())
    calls = 0; actual = artifacts_module.extract_document
    def fail_once(data, kind):
        nonlocal calls
        calls += 1
        if calls == 1: raise DocumentExtractionError("Document extraction is busy; retry shortly.")
        return actual(data, kind)
    monkeypatch.setattr(artifacts_module, "extract_document", fail_once)
    try:
        item = store.import_file(cid, path)
        failed = store.read(cid, item["id"])
        assert failed["text"] is None and "busy" in failed["limitation"]
        assert catalog.rows("SELECT * FROM artifact_text_cache_v1 WHERE artifact_id=?", (item["id"],)) == []
        assert store.read(cid, item["id"])["text"]
        assert calls == 2

        class Notice:
            value = ""
            def setText(self, value): self.value = value
        ui = SimpleNamespace(current_id=cid, artifact_notice=Notice())
        _finish_document_attach(ui, store, cid, item["id"], item["filename"],
            {"text": None, "limitation": "Malformed DOCX; original preserved."})
        assert store.drafts(cid) == []
        assert "not attached" in ui.artifact_notice.value and "Malformed DOCX" in ui.artifact_notice.value
    finally: catalog.close()
