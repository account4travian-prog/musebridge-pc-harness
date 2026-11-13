import builtins
import struct
import zlib

import pytest
from PIL import Image

from musebridge.media import ImageAttachment, from_bytes, load_image
from musegadget.link_client import MAX_IMAGE_BYTES


def png_bytes(size=(4, 3), color=(20, 40, 60)):
    import io
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def test_from_bytes_returns_immutable_attachment_and_safe_filename():
    source = bytearray(png_bytes())
    attachment = from_bytes(source, "image/png", r"C:\private\folder\sample.png")
    source[0] = 0
    assert isinstance(attachment, ImageAttachment)
    assert isinstance(attachment.data, bytes)
    assert attachment.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert attachment.filename == "sample.png"
    with pytest.raises(AttributeError):
        attachment.filename = "changed.png"


@pytest.mark.parametrize("mime_type", ["image/jpeg", "image/gif", "image/webp"])
def test_rejects_content_that_does_not_match_declared_mime(mime_type):
    with pytest.raises(ValueError, match="does not match"):
        from_bytes(png_bytes(), mime_type, "not-actually-that-format")


@pytest.mark.parametrize("data", [b"", b"not an image", png_bytes()[:-8]])
def test_rejects_empty_malformed_and_truncated_images(data):
    with pytest.raises(ValueError):
        from_bytes(data, "image/png", "fixture.png")


def test_rejects_unsupported_mime_and_oversize_before_decode():
    with pytest.raises(ValueError, match="JPEG, PNG, GIF or WebP"):
        from_bytes(png_bytes(), "image/svg+xml", "fixture.svg")
    with pytest.raises(ValueError, match="2 MiB"):
        from_bytes(b"x" * (MAX_IMAGE_BYTES + 1), "image/png", "large.png")


def test_rejects_dimension_bomb_from_header_before_pixel_decode():
    forged = bytearray(png_bytes())
    forged[16:24] = struct.pack(">II", 30_000, 2)
    forged[29:33] = struct.pack(">I", zlib.crc32(forged[12:29]) & 0xFFFFFFFF)
    with pytest.raises(ValueError, match="dimensions"):
        from_bytes(forged, "image/png", "wide.png")


def test_load_image_reads_valid_file_and_never_returns_path(tmp_path):
    source = tmp_path / "local-secret-name.png"
    original = png_bytes()
    source.write_bytes(original)
    attachment = load_image(source)
    assert attachment.data == original
    assert attachment.mime_type == "image/png"
    assert attachment.filename == source.name
    assert str(tmp_path) not in attachment.filename


def test_load_image_rejects_nonregular_and_oversize_sources_before_read(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="regular file"):
        load_image(tmp_path)
    oversized = tmp_path / "large.png"
    with oversized.open("wb") as stream:
        stream.truncate(MAX_IMAGE_BYTES + 1)

    def forbidden_read(*args, **kwargs):
        raise AssertionError("oversized source should be rejected before opening")

    monkeypatch.setattr(type(oversized), "open", forbidden_read)
    with pytest.raises(ValueError, match="2 MiB"):
        load_image(oversized)


def test_load_image_detects_file_changed_during_read(tmp_path, monkeypatch):
    source = tmp_path / "changing.png"
    old = png_bytes((2, 2))
    changed = png_bytes((100, 100), color=(250, 1, 3))
    source.write_bytes(old)
    original_open = type(source).open

    class ChangingReader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def fileno(self):
            return self.stream.fileno()

        def read(self, size=-1):
            data = self.stream.read(size)
            with builtins.open(source, "wb") as replacement:
                replacement.write(changed)
            return data

    def changing_open(path, *args, **kwargs):
        return ChangingReader(original_open(path, *args, **kwargs))

    monkeypatch.setattr(type(source), "open", changing_open)
    with pytest.raises(ValueError, match="changed while"):
        load_image(source)


def test_creation_change_timestamp_difference_does_not_reject_same_file(tmp_path,monkeypatch):
    from types import SimpleNamespace
    source=tmp_path/'stable.png';source.write_bytes(png_bytes())
    original_lstat=type(source).lstat
    def differing_creation_time(path,*args,**kwargs):
        result=original_lstat(path,*args,**kwargs)
        return SimpleNamespace(**{key:getattr(result,key) for key in ('st_dev','st_ino','st_size','st_mtime_ns','st_mode','st_file_attributes')},st_ctime_ns=result.st_ctime_ns+1)
    monkeypatch.setattr(type(source),'lstat',differing_creation_time)
    assert load_image(source).data==png_bytes()
