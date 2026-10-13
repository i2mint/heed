"""Neutralise uploaded screenshots before they are stored (issue #15).

Magic-byte sniffing (:func:`heed.security.sniff_image_type`) proves a file *starts* like
a PNG, JPEG or WebP. It says nothing about the rest: a polyglot can carry a valid image
header followed by HTML, a ZIP, or a script, and EXIF/XMP metadata can carry GPS
coordinates. A sanitizer rewrites the accepted bytes to cut that down.

Sanitizers are ``(data, media_type) -> bytes`` callables that raise ``ValueError`` on a
malformed or refused image, looked up by name in :data:`image_sanitizers`:

- ``"auto"`` (default): ``"reencode"`` when Pillow is installed, else ``"strip"`` (with
  a warning at startup).
- ``"reencode"``: ``strip`` first (structure and cost checks), then decode and re-save
  the first frame with Pillow (``pip install heed[images]``). Only freshly encoded
  pixels are stored: no metadata, comments, extra frames or trailing data. At most
  ``max_concurrent`` re-encodes run at once; beyond that :class:`SanitizerBusy` is
  raised (the router answers 503).
- ``"strip"``: pure Python, no dependency. Walks the container, keeps an allow-list of
  chunks/segments (each length-checked where the format fixes it), rejects unknown
  JPEG markers, and truncates at the end marker. Trailing payloads, metadata (EXIF, XMP,
  ICC, text chunks, comments) and animation are gone; the **compressed pixel data is
  kept as sent**, so attacker-chosen bytes can survive inside it.
- ``"none"``: store the bytes as received (explicit opt-out).

Neither rewrite makes a stored file safe to serve on its own (even re-encoded pixel data
can hold attacker-chosen byte runs), so attachments must also be served with
:func:`heed.security.attachment_headers`.

>>> png = _tiny_png() + b"<html><script>alert(1)</script>"
>>> clean = strip_image(png, "image/png")
>>> clean == _tiny_png(), b"<html>" in clean
(True, False)
"""

from __future__ import annotations

import io
import logging
import re
import struct
import threading
import zlib
from collections.abc import Callable

logger = logging.getLogger("heed.images")

ImageSanitizer = Callable[[bytes, str], bytes]

#: Pillow ``save`` options per media type, used by :func:`reencode_image`.
REENCODE_OPTIONS: dict[str, dict] = {
    "image/png": {"format": "PNG", "optimize": False},
    "image/jpeg": {"format": "JPEG", "quality": 90},
    "image/webp": {"format": "WEBP", "quality": 90},
}

#: Pixel modes each format can be saved in without conversion.
_SAVE_MODES = {
    "image/png": {"1", "L", "LA", "I;16", "P", "RGB", "RGBA"},
    "image/jpeg": {"L", "RGB", "CMYK"},
    "image/webp": {"RGB", "RGBA"},
}

DEFAULT_MAX_PIXELS = 25_000_000  # covers a 6K display (6016x3384 = 20.4M)
DEFAULT_MAX_CONCURRENT = 2  # re-encodes in flight per process (each can use ~100 MB)
DEFAULT_BUSY_WAIT_SECONDS = 2.0  # how long a re-encode waits for a free slot
BUSY_RETRY_AFTER_SECONDS = 5  # Retry-After sent with the 503 when every slot is busy
MAX_JPEG_SCANS = 32  # progressive encoders use ~10; each scan is a full decode pass
_MAX_PARTS = 4096  # chunks / segments per file (a crafted file can have ~10^6)


class SanitizerBusy(RuntimeError):
    """Every re-encode slot stayed busy for the whole wait: try again later."""


# ----------------------------------------------------------------------------- strip

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _between(lo: int, hi: int) -> Callable[[int], bool]:
    return lambda n: lo <= n <= hi


# Critical chunks plus small rendering hints, each with the lengths the PNG spec allows.
# Dropped: text (tEXt, zTXt, iTXt), eXIf, iCCP, tIME, APNG frames, unknown chunks.
_PNG_KEEP: dict[bytes, Callable[[int], bool]] = {
    b"IHDR": _between(13, 13),
    b"PLTE": lambda n: 3 <= n <= 768 and n % 3 == 0,
    b"IDAT": _between(0, 2**31 - 1),
    b"IEND": _between(0, 0),
    b"tRNS": _between(1, 256),
    b"gAMA": _between(4, 4),
    b"cHRM": _between(32, 32),
    b"sRGB": _between(1, 1),
    b"pHYs": _between(9, 9),
    b"sBIT": _between(1, 4),
    b"bKGD": _between(1, 6),
}


def _strip_png(data: bytes) -> bytes:
    if not data.startswith(_PNG_SIGNATURE):
        raise ValueError("not a PNG")
    out, pos, seen = [_PNG_SIGNATURE], len(_PNG_SIGNATURE), []
    while True:
        if len(seen) >= _MAX_PARTS:
            raise ValueError(f"PNG has more than {_MAX_PARTS} chunks")
        if pos + 8 > len(data):
            raise ValueError("PNG truncated before IEND")
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        ctype = data[pos + 4 : pos + 8]
        end = pos + 12 + length
        if end > len(data):
            raise ValueError(f"PNG chunk {ctype!r} runs past the end of the file")
        (crc,) = struct.unpack(">I", data[end - 4 : end])
        if zlib.crc32(data[pos + 4 : end - 4]) != crc:
            raise ValueError(f"PNG chunk {ctype!r} has a bad CRC")
        seen.append(ctype)
        length_ok = _PNG_KEEP.get(ctype)
        if length_ok is not None:
            if not length_ok(length):
                raise ValueError(f"PNG chunk {ctype!r} has an invalid length {length}")
            out.append(data[pos:end])
        pos = end
        if ctype == b"IEND":
            break
    if seen[0] != b"IHDR" or b"IDAT" not in seen:
        raise ValueError("PNG lacks IHDR or IDAT")
    return b"".join(out)


_JPEG_EOI, _JPEG_SOS, _JPEG_APP14 = 0xD9, 0xDA, 0xEE
_JPEG_APP14_MAX_LEN = (
    16  # Adobe segment: selects the colour transform; fixed small size
)
# Frame headers (SOF0-3, 5-7, 9-11, 13-15), tables (DHT, DAC, DQT), restart interval
# (DRI) and scans (SOS) are kept. Application segments (EXIF, XMP, ICC, JFIF and its
# thumbnail, ...) and comments are dropped. Any other marker is refused.
_JPEG_KEEP = {*range(0xC0, 0xD0), 0xDA, 0xDB, 0xDD} - {0xC8}
_JPEG_DROP = {*range(0xE0, 0xF0), 0xFE}
_FF_RUN = re.compile(rb"\xff+")
# Inside a scan: a run of 0xFF followed by a byte that is not a stuffed 0x00 nor a
# restart marker (RST0-7) is the next real marker.
_SCAN_END = re.compile(rb"\xff+[^\x00\xd0-\xd7\xff]")


def _strip_jpeg(data: bytes, *, max_scans: int = MAX_JPEG_SCANS) -> bytes:
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("not a JPEG")
    out, pos, scans, parts = [b"\xff\xd8"], 2, 0, 0
    while pos < len(data):
        parts += 1
        if parts > _MAX_PARTS:
            raise ValueError(f"JPEG has more than {_MAX_PARTS} segments")
        fill = _FF_RUN.match(data, pos)
        if fill is None:
            raise ValueError(f"JPEG: expected a marker at byte {pos}")
        pos = fill.end()
        if pos >= len(data):
            break
        marker = data[pos]
        pos += 1
        if marker == _JPEG_EOI:
            if not scans:
                raise ValueError("JPEG has no image scan")
            out.append(b"\xff\xd9")
            return b"".join(out)
        if marker not in _JPEG_KEEP and marker not in _JPEG_DROP:
            raise ValueError(f"JPEG marker 0x{marker:02X} is not allowed")
        if pos + 2 > len(data):
            raise ValueError("JPEG truncated in a segment header")
        (length,) = struct.unpack(">H", data[pos : pos + 2])
        if length < 2 or pos + length > len(data):
            raise ValueError("JPEG segment length is invalid")
        keep = marker in _JPEG_KEEP or (
            marker == _JPEG_APP14 and length <= _JPEG_APP14_MAX_LEN
        )
        if keep:
            out.append(bytes((0xFF, marker)) + data[pos : pos + length])
        pos += length
        if marker == _JPEG_SOS:
            scans += 1
            if scans > max_scans:
                raise ValueError(f"JPEG has more than {max_scans} scans")
            end = _SCAN_END.search(data, pos)
            if end is None:
                raise ValueError("JPEG truncated inside a scan")
            marker_at = end.end() - 2  # the last 0xFF of the run
            out.append(data[pos:marker_at])
            pos = marker_at
    raise ValueError("JPEG has no end-of-image marker")


# Still-image chunks only, with the sizes the format fixes. Dropped: ICCP, EXIF, XMP,
# animation (ANIM, ANMF: a screenshot is a single frame) and unknown chunks.
_WEBP_KEEP: dict[bytes, Callable[[int], bool]] = {
    b"VP8 ": _between(10, 2**32 - 1),
    b"VP8L": _between(5, 2**32 - 1),
    b"VP8X": _between(10, 10),
    b"ALPH": _between(1, 2**32 - 1),
}
_WEBP_IMAGE = {b"VP8 ", b"VP8L"}
# VP8X flags cleared, since their chunks are dropped: ICC, EXIF, XMP, animation.
_VP8X_DROPPED_FLAGS = 0x20 | 0x08 | 0x04 | 0x02


def _strip_webp(data: bytes) -> bytes:
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("not a WebP")
    (riff_size,) = struct.unpack("<I", data[4:8])
    riff_end = 8 + riff_size
    if riff_end > len(data) or riff_size < 4:
        raise ValueError("WebP RIFF size is invalid")
    chunks, pos, seen = [], 12, []
    while pos < riff_end:
        if len(seen) >= _MAX_PARTS:
            raise ValueError(f"WebP has more than {_MAX_PARTS} chunks")
        if pos + 8 > riff_end:
            raise ValueError("WebP truncated in a chunk header")
        fourcc = data[pos : pos + 4]
        (size,) = struct.unpack("<I", data[pos + 4 : pos + 8])
        end = pos + 8 + size
        if end > riff_end:
            raise ValueError(f"WebP chunk {fourcc!r} runs past the RIFF")
        payload = data[pos + 8 : end]
        seen.append(fourcc)
        size_ok = _WEBP_KEEP.get(fourcc)
        if size_ok is not None:
            if not size_ok(size):
                raise ValueError(f"WebP chunk {fourcc!r} has an invalid size {size}")
            if fourcc == b"VP8X":
                payload = bytes((payload[0] & ~_VP8X_DROPPED_FLAGS,)) + payload[1:]
            chunks.append(
                fourcc + struct.pack("<I", size) + payload + b"\0" * (size % 2)
            )
        pos = end + size % 2
    if sum(fourcc in _WEBP_IMAGE for fourcc in seen) != 1:
        raise ValueError("WebP must hold exactly one still image")
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


_STRIPPERS: dict[str, Callable[[bytes], bytes]] = {
    "image/png": _strip_png,
    "image/jpeg": _strip_jpeg,
    "image/webp": _strip_webp,
}


def strip_image(data: bytes, media_type: str) -> bytes:
    """Keep only what renders the image; truncate at its end. Pure Python.

    Raises ``ValueError`` when ``data`` is not a well-formed ``media_type`` image, or
    one too costly to decode (over ``MAX_JPEG_SCANS`` scans, ``_MAX_PARTS`` parts).
    """
    try:
        stripper = _STRIPPERS[media_type]
    except KeyError:
        raise ValueError(f"cannot sanitize media type {media_type!r}") from None
    return stripper(data)


# -------------------------------------------------------------------------- reencode


def pillow_available() -> bool:
    """Whether :func:`reencode_image` can run (Pillow is importable)."""
    try:
        import PIL.Image  # noqa: F401
    except ImportError:
        return False
    return True


def reencode_image(
    data: bytes, media_type: str, *, max_pixels: int = DEFAULT_MAX_PIXELS
) -> bytes:
    """Strip ``data``, then decode it and save its first frame afresh, same format.

    Images over ``max_pixels`` are refused before any pixel is decoded (a small file can
    declare a huge canvas). Nothing but pixels (and PNG palette transparency) is carried
    over: no comment, EXIF, ICC profile or other metadata. Raises ``ValueError`` on a
    malformed, oversized or too costly image.
    """
    from PIL import Image

    try:
        options = dict(REENCODE_OPTIONS[media_type])
    except KeyError:
        raise ValueError(f"cannot sanitize media type {media_type!r}") from None
    data = strip_image(data, media_type)  # structure + cost checks before decoding
    try:
        with Image.open(io.BytesIO(data), formats=[options["format"]]) as im:
            if im.width * im.height > max_pixels:
                raise ValueError(
                    f"image is {im.width}x{im.height}, over {max_pixels} pixels"
                )
            im.load()
            frame = im
            if im.mode not in _SAVE_MODES[media_type]:
                alpha = "A" in im.mode or "transparency" in im.info
                keep_alpha = alpha and media_type != "image/jpeg"
                frame = im.convert("RGBA" if keep_alpha else "RGB")
            kept = {k: frame.info[k] for k in ("transparency",) if k in frame.info}
            frame.info = kept  # Pillow writes e.g. a JPEG comment back from .info
            out = io.BytesIO()
            frame.save(out, **options)
    except ValueError:
        raise
    except Exception as e:  # Pillow raises OSError, SyntaxError, struct.error, ...
        raise ValueError(f"not a well-formed {media_type} image: {e}") from e
    return out.getvalue()


# -------------------------------------------------------------------------- registry


def _no_sanitizer(**_) -> ImageSanitizer:
    return lambda data, media_type: data


def _strip_sanitizer(**_) -> ImageSanitizer:
    return strip_image


def _reencode_sanitizer(
    *,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    busy_wait_seconds: float = DEFAULT_BUSY_WAIT_SECONDS,
    **_,
) -> ImageSanitizer:
    if not pillow_available():
        raise ImportError(
            "screenshot_sanitizer='reencode' needs Pillow: pip install 'heed[images]' "
            "(or use 'strip', which needs nothing)"
        )
    if max_concurrent < 1:
        raise ValueError(f"max_concurrent must be at least 1, got {max_concurrent}")
    slots = threading.BoundedSemaphore(max_concurrent)

    def sanitize(data: bytes, media_type: str) -> bytes:
        if not slots.acquire(timeout=busy_wait_seconds):
            raise SanitizerBusy("every screenshot re-encode slot is busy")
        try:
            return reencode_image(data, media_type, max_pixels=max_pixels)
        finally:
            slots.release()

    return sanitize


def _auto_sanitizer(**kwargs) -> ImageSanitizer:
    if pillow_available():
        return _reencode_sanitizer(**kwargs)
    logger.warning(
        "heed: Pillow is not installed, so screenshots are only stripped, not "
        "re-encoded (compressed pixel data is kept as sent). "
        "Install heed[images] for re-encoding."
    )
    return _strip_sanitizer(**kwargs)


image_sanitizers: dict[str, Callable[..., ImageSanitizer]] = {
    "auto": _auto_sanitizer,
    "reencode": _reencode_sanitizer,
    "strip": _strip_sanitizer,
    "none": _no_sanitizer,
}


def make_image_sanitizer(
    name: str = "auto",
    *,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    busy_wait_seconds: float = DEFAULT_BUSY_WAIT_SECONDS,
) -> ImageSanitizer:
    """Build a sanitizer from the :data:`image_sanitizers` registry by name.

    Fails at construction (not on the first upload) when the name is unknown or its
    dependency is missing.
    """
    try:
        factory = image_sanitizers[name]
    except KeyError:
        raise ValueError(
            f"Unknown screenshot_sanitizer {name!r}. Known: {sorted(image_sanitizers)}"
        ) from None
    return factory(
        max_pixels=max_pixels,
        max_concurrent=max_concurrent,
        busy_wait_seconds=busy_wait_seconds,
    )


def _tiny_png() -> bytes:
    """A valid 1x1 grey PNG (for doctests and tests)."""

    def chunk(ctype: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(ctype + payload)
        return (
            struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", crc)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    idat = zlib.compress(b"\x00\x80")
    return (
        _PNG_SIGNATURE
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )
