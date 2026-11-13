"""Isolated parser worker; this is not an OS security sandbox."""
import ctypes
import os
import json
import sys
import zipfile
from io import BytesIO
from xml.etree import ElementTree

MAX_INPUT = 8 * 1024 * 1024
MAX_TEXT = 256 * 1024
MAX_PAGES = 100
MAX_MEMBERS = 2000
MAX_UNCOMPRESSED = 32 * 1024 * 1024
MAX_PROCESS_MEMORY = 256 * 1024 * 1024


def limit_windows_process_memory():
    """Apply a per-process memory cap on Windows before reading/parsing input."""
    if os.name != "nt":
        return
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create_job = kernel.CreateJobObjectW
    create_job.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, wintypes.INT, ctypes.c_void_p, wintypes.DWORD)
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    job = create_job(None, None)
    if not job:
        raise RuntimeError
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = 0x100 | 0x2000  # PROCESS_MEMORY | KILL_ON_JOB_CLOSE
    limits.ProcessMemoryLimit = MAX_PROCESS_MEMORY
    ok = kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
    if ok:
        ok = kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess())
    if not ok:
        kernel.CloseHandle(job)
        raise RuntimeError
    # Keep this handle alive for the worker lifetime; closing it terminates the job.
    global _JOB_HANDLE
    _JOB_HANDLE = job


def pdf(data):
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(data), strict=True)
    if reader.is_encrypted:
        raise ValueError("Password-protected PDF cannot be read; the original file is preserved.")
    if len(reader.pages) > MAX_PAGES:
        raise ValueError("PDF exceeds the 100-page extraction limit; the original file is preserved.")
    parts = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
        if sum(map(len, parts)) > MAX_TEXT:
            raise ValueError("Extracted document text exceeds 256 KiB.")
    text = "\n\n".join(parts).strip()
    if not text:
        raise ValueError("PDF has no extractable text (it may be scanned); no OCR was performed.")
    return text


def docx(data):
    with zipfile.ZipFile(BytesIO(data)) as archive:
        items = archive.infolist()
        if len(items) > MAX_MEMBERS or sum(i.file_size for i in items) > MAX_UNCOMPRESSED:
            raise ValueError("DOCX archive exceeds safe extraction limits.")
        names = {i.filename for i in items}
        if "word/document.xml" not in names:
            raise ValueError("Malformed DOCX: main document is missing.")
        with archive.open("word/document.xml") as member:
            document_xml = member.read(MAX_UNCOMPRESSED + 1)
        if len(document_xml) > MAX_UNCOMPRESSED:
            raise ValueError("DOCX archive exceeds safe extraction limits.")
        if has_doctype_or_entity_declaration(document_xml):
            raise ValueError("DOCX XML declarations with entities are not supported.")
        root = ElementTree.fromstring(document_xml)
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    out = []
    # Paragraph/table traversal preserves row and paragraph boundaries.
    for node in root.iter():
        if node.tag == ns + "p":
            value = "".join(x.text or "" for x in node.iter(ns + "t"))
            if value:
                out.append(value)
        if sum(map(len, out)) > MAX_TEXT:
            raise ValueError("Extracted document text exceeds 256 KiB.")
    text = "\n".join(out).strip()
    if not text:
        raise ValueError("DOCX contains no readable text.")
    return text


def has_doctype_or_entity_declaration(xml):
    # OOXML files are UTF-8 or UTF-16; inspect markup in either byte encoding
    # before handing it to ElementTree, which does not need DTDs for DOCX.
    upper = xml.upper()
    return any(marker in upper for marker in (
        b"<!DOCTYPE", b"<!ENTITY", b"<\x00!\x00D\x00O\x00C\x00T\x00Y\x00P\x00E\x00",
        b"<\x00!\x00E\x00N\x00T\x00I\x00T\x00Y\x00", b"\x00<\x00!D\x00O\x00C\x00T\x00Y\x00P\x00E",
        b"\x00<\x00!E\x00N\x00T\x00I\x00T\x00Y"))


def main():
    try:
        limit_windows_process_memory()
    except Exception:
        print(json.dumps({"ok": False, "error": "Windows parser memory limit could not be applied; extraction was refused."}))
        raise SystemExit(1)
    kind = sys.argv[1] if len(sys.argv) == 2 else ""
    data = sys.stdin.buffer.read(MAX_INPUT + 1)
    try:
        if len(data) > MAX_INPUT:
            raise ValueError("Document exceeds the 8 MiB limit.")
        text = pdf(data) if kind == "pdf" else docx(data) if kind == "docx" else None
        if text is None:
            raise ValueError("Unsupported document format.")
        print(json.dumps({"ok": True, "text": text}, ensure_ascii=True))
    except Exception as exc:
        # Avoid parser internals and paths in UI messages.
        message = str(exc)
        if kind == "pdf" and "password" in message.lower():
            message = "Password-protected PDF cannot be read; the original file is preserved."
        elif kind == "pdf" and not any(s in message.lower() for s in ("encrypted", "password", "100-page", "256 kib", "no extractable")):
            message = "Malformed or unsupported PDF; the original file is preserved."
        elif kind == "docx" and not any(s in message.lower() for s in ("docx", "256 kib", "safe extraction")):
            message = "Malformed DOCX; the original file is preserved."
        print(json.dumps({"ok": False, "error": message[:300]}, ensure_ascii=True))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
