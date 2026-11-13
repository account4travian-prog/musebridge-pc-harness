"""Bounded local image admission for the Muse image-send route.

Images are validated as-is and never resized or re-encoded implicitly.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import os
from pathlib import Path
import stat
import warnings

from PIL import Image

from musegadget.link_client import IMAGE_MIME_TYPES, MAX_IMAGE_BYTES


# These are application decode limits, not claims that every image under them
# is harmless. 20k bounds decoder dimensions; 40M cumulative pixels bounds the
# raw pixel work (about 160 MiB for one RGBA frame) across animated formats.
MAX_IMAGE_DIMENSION = 20_000
MAX_DECODE_PIXELS = 40_000_000
_FORMAT_BY_MIME = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/gif": "GIF",
    "image/webp": "WEBP",
}
_EXTENSION_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
_REPARSE_POINT = 0x0400


class _InvalidImage(ValueError):
    """Internal validation failure with a safe public message."""


@dataclass(frozen=True, slots=True)
class ImageAttachment:
    """Validated immutable image bytes ready for an explicit send operation."""

    data: bytes
    mime_type: str
    filename: str

    def __post_init__(self):
        if not isinstance(self.data, bytes):
            raise ValueError("image data must be immutable bytes")
        _validate_image(self.data, self.mime_type)
        object.__setattr__(self, "filename", _safe_filename(self.filename))


def from_bytes(data, mime_type, filename=None) -> ImageAttachment:
    """Validate image bytes and MIME type without modifying or resizing them."""
    if not isinstance(mime_type, str) or mime_type not in IMAGE_MIME_TYPES:
        raise ValueError("images must be JPEG, PNG, GIF or WebP")
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError("image data must be bytes")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("image exceeds the 2 MiB limit")
    immutable = data if isinstance(data, bytes) else bytes(data)
    if filename is None:
        filename = "image" + _EXTENSION_BY_MIME.get(mime_type, ".img")
    return ImageAttachment(immutable, mime_type, filename)


def load_image(path) -> ImageAttachment:
    """Read one regular local file, bounded before reading, then validate it.

    Error messages intentionally omit the input path and filename so accidental
    UI/log reporting cannot disclose directory names.
    """
    try:
        image_path = Path(path).expanduser()
        before_path = image_path.lstat()
    except (OSError, TypeError, ValueError):
        raise ValueError("image file is unavailable") from None
    if not _is_regular(before_path):
        raise ValueError("image source must be a regular file")
    if before_path.st_size <= 0:
        raise ValueError("image file is empty")
    if before_path.st_size > MAX_IMAGE_BYTES:
        raise ValueError("image exceeds the 2 MiB limit")

    try:
        with image_path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not _is_regular(before):
                raise ValueError("image source must be a regular file")
            if before.st_size <= 0:
                raise ValueError("image file is empty")
            if before.st_size > MAX_IMAGE_BYTES:
                raise ValueError("image exceeds the 2 MiB limit")
            if not _same_file(before_path, before):
                raise ValueError("image file changed before it was read")
            data = stream.read(MAX_IMAGE_BYTES + 1)
            after = os.fstat(stream.fileno())
    except ValueError:
        raise
    except (OSError, TypeError, ValueError):
        raise ValueError("image file could not be read") from None

    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("image exceeds the 2 MiB limit")
    if not _same_file(before, after) or len(data) != before.st_size:
        raise ValueError("image file changed while it was being read")

    # Use only the basename as a sent filename; never attach a local path.
    return from_bytes(data, _mime_from_extension(image_path.suffix), image_path.name)


def _validate_image(data: bytes, mime_type: str) -> None:
    if not isinstance(mime_type, str) or mime_type not in IMAGE_MIME_TYPES:
        raise ValueError("images must be JPEG, PNG, GIF or WebP")
    if not data:
        raise ValueError("the image is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("image exceeds the 2 MiB limit")

    expected = _FORMAT_BY_MIME[mime_type]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format != expected:
                    raise _InvalidImage("image content does not match its MIME type")
                _validate_dimensions(image.size)
                frames = getattr(image, "n_frames", 1)
                if not isinstance(frames, int) or frames < 1:
                    raise _InvalidImage("image frame structure is invalid")
                width, height = image.size
                if width * height * frames > MAX_DECODE_PIXELS:
                    raise _InvalidImage("image exceeds the 40-million-pixel decode limit")
                image.verify()

            # verify() checks container integrity without decoding pixel data.
            # Load every frame as well so truncated/corrupt image payloads fail
            # before they are admitted to a remote send.
            with Image.open(BytesIO(data)) as image:
                frames = getattr(image, "n_frames", 1)
                total_pixels = 0
                for index in range(frames):
                    image.seek(index)
                    size = image.size
                    _validate_dimensions(size)
                    total_pixels += size[0] * size[1]
                    if total_pixels > MAX_DECODE_PIXELS:
                        raise _InvalidImage("image exceeds the 40-million-pixel decode limit")
                    image.load()
    except _InvalidImage:
        raise
    except Exception as exc:
        # Pillow plugin exceptions vary by format/version. Normalize them to a
        # path-free validation error. BaseException signals still propagate.
        raise ValueError("image is malformed, truncated, or exceeds decode limits") from None


def _validate_dimensions(size) -> None:
    width, height = size
    if (not isinstance(width, int) or not isinstance(height, int)
            or width <= 0 or height <= 0
            or width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION):
        raise _InvalidImage("image dimensions exceed the 20,000-pixel limit")
    if width * height > MAX_DECODE_PIXELS:
        raise _InvalidImage("image exceeds the 40-million-pixel decode limit")


def _safe_filename(filename) -> str:
    if not isinstance(filename, str) or not filename or "\x00" in filename:
        raise ValueError("image filename is invalid")
    # Treat both slash styles as separators even when called on another OS.
    leaf = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if leaf in ("", ".", "..") or len(leaf) > 255:
        raise ValueError("image filename is invalid")
    return leaf


def _mime_from_extension(extension: str) -> str:
    suffix = extension.casefold()
    mapping = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".gif": "image/gif", ".webp": "image/webp"}
    mime = mapping.get(suffix)
    if not mime:
        raise ValueError("image file extension must be JPEG, PNG, GIF or WebP")
    return mime


def _is_regular(info) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISREG(info.st_mode) and not (attributes & _REPARSE_POINT)


def _same_file(left, right) -> bool:
    # Windows lstat/fstat can expose differing creation/change timestamps even
    # for one unchanged inode. Content identity uses inode, size and write time.
    return (left.st_dev, left.st_ino, left.st_size, left.st_mtime_ns) == (
        right.st_dev, right.st_ino, right.st_size, right.st_mtime_ns)
