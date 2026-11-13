"""Bounded, isolated text extraction for local PDF and DOCX snapshots."""
import json
import subprocess
import sys
import tempfile
from threading import BoundedSemaphore

MAX_DOCUMENT_PAGES = 100
MAX_DOCUMENT_TEXT = 256 * 1024
MAX_DOCX_MEMBERS = 2000
MAX_DOCX_UNCOMPRESSED = 32 * 1024 * 1024
EXTRACTION_TIMEOUT = 8
MAX_WORKER_OUTPUT = 6 * MAX_DOCUMENT_TEXT + 4096
EXTRACTOR_VERSION = 1
_PARSER_SLOTS = BoundedSemaphore(2)


class DocumentExtractionError(ValueError):
    """A document could not safely yield readable text."""


def extract_document(data: bytes, kind: str) -> str:
    """Extract PDF/DOCX text in a time-limited subprocess; source bytes stay intact."""
    if kind not in {"pdf", "docx"}:
        raise DocumentExtractionError("Unsupported document format")
    if not _PARSER_SLOTS.acquire(timeout=0.05):
        raise DocumentExtractionError("Document extraction is busy; retry shortly.")
    try:
        try:
            with tempfile.TemporaryFile() as output:
                result = subprocess.run(
                    [sys.executable, "-m", "musebridge.document_worker", kind],
                    input=data, stdout=output, stderr=subprocess.DEVNULL,
                    timeout=EXTRACTION_TIMEOUT, check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                size = output.tell()
                if size > MAX_WORKER_OUTPUT:
                    raise DocumentExtractionError("Document extraction output exceeded the safe limit.")
                output.seek(0)
                response = output.read(MAX_WORKER_OUTPUT + 1)
        except subprocess.TimeoutExpired:
            raise DocumentExtractionError("Document extraction timed out; the original file is preserved.") from None
        try:
            payload = json.loads(response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DocumentExtractionError("Document extraction failed; the original file is preserved.") from None
        if result.returncode or not payload.get("ok"):
            raise DocumentExtractionError(payload.get("error", "Document extraction failed; the original file is preserved."))
        text = payload.get("text")
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_DOCUMENT_TEXT:
            raise DocumentExtractionError("Extracted document text exceeds 256 KiB.")
        return text
    finally:
        _PARSER_SLOTS.release()
