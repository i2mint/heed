"""Neutralise uploaded screenshots before they are stored (issue #15).

Magic-byte sniffing (:func:`heed.security.sniff_image_type`) proves a file *starts* like
a PNG, JPEG or WebP. It says nothing about the rest: a polyglot can carry a valid image
header followed by HTML, a ZIP, or a script, and EXIF/XMP metadata can carry GPS
coordinates. A sanitizer rewrites the accepted bytes so only the image survives.

Sanitizers are ``(data, media_type) -> bytes`` callables that raise ``ValueError`` on a
malformed image, looked up by name in :data:`image_sanitizers`:

- ``"auto"`` (default): ``"reencode"`` when Pillow is installed, else ``"strip"``.
- ``"reencode"``: decode and re-save with Pillow (``pip install heed[images]``). Drops
  everything that is not pixels: trailing payloads, metadata, extra frames.
- ``"strip"``: pure Python, no dependency. Walks the container format, keeps only the
  chunks/segments needed to render, and truncates at the end marker, so trailing
  payloads and metadata (EXIF, XMP, text chunks, comments) are gone. The compressed
  pixel stream itself is kept as sent.
- ``"none"``: store the bytes as received (explicit opt-out).

Neither rewrite makes a stored file safe to serve on its own (a re-encoded pixel stream
can still contain attacker-chosen bytes), so attachments must also be served with
:func:`heed.security.attachment_headers`.

>>> png = _tiny_png() + b"<html><script>alert(1)</script>"
>>> clean = strip_image(png, "image/png")
>>> clean == _tiny_png(), b"<html>" in clean
(True, False)
"""

from __future__ import annotations

import io
import struct
import zlib
from collections.abc import Callable

ImageSanitizer = Callable[[bytes, str], bytes]

#: Pillow ``save`` options per media type, used by :func:`reencode_image`.
REENCODE_OPTIONS: dict[str, dict] = {
    "image/png": {"format": "PNG", "optimize": False},
    "image/jpeg": {"format": "JPEG", "quality": 90},
    "image/webp": {"format": "WEBP", "quality": 90},
}

#: Pixel modes each format can be saved in without conversion.
_SAVE_MODES = {
    "image/png": {"1", "L", "LA", "I", "I;16", "P", "RGB", "RGBA"},
    "image/jpeg": {"L", "RGB", "CMYK"},
    "image/webp": {"RGB", "RGBA"},
}

DEFAULT_MAX_PIXELS = 40_000_000  # a little above 8K (7680x4320 = 33.2M)


# ----------------------------------------------------------------------------- strip

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Critical chunks plus small, fixed-shape rendering hints. Dropped: text (tEXt, zTXt,
# iTXt), eXIf, iCCP, time, APNG frames (the default image still renders), unknowns.
_PNG_KEEP = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB"}
_PNG_KEEP |= {b"pHYs", b"sBIT", b"bKGD"}


def _strip_png(data: bytes) -> bytes:
    if not data.startswith(_PNG_SIGNATURE):
        raise ValueError("not a PNG")
    out, pos, seen = [_PNG_SIGNATURE], len(_PNG_SIGNATURE), []
    while True:
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
        if ctype in _PNG_KEEP:
            out.append(data[pos:end])
        pos = end
        if ctype == b"IEND":
            break
    if seen[0] != b"IHDR" or b"IDAT" not in seen:
        raise ValueError("PNG lacks IHDR or IDAT")
    return b"".join(out)


_JPEG_EOI, _JPEG_SOS = 0xD9, 0xDA
_JPEG_STANDALONE = {0x01, *range(0xD0, 0xD8)}  # TEM, RST0-7: no length field
# Application segments carry metadata (APP0 JFIF incl. thumbnail, APP1 EXIF/XMP, APP2
# ICC, ...) and COM carries free text: all dropped. APP14 (Adobe) is kept because it
# selects the colour transform, and only in its fixed small size.
_JPEG_APP14, _JPEG_APP14_MAX_LEN = 0xEE, 16
_JPEG_DROP = {*range(0xE0, 0xF0), 0xFE}


def _skip_entropy_coded(data: bytes, pos: int) -> int:
    """Index of the first real marker after a scan's entropy-coded data."""
    while True:
        pos = data.find(b"\xff", pos)
        if pos < 0 or pos + 1 >= len(data):
            raise ValueError("JPEG truncated inside a scan")
        nxt = data[pos + 1]
        if nxt == 0x00 or 0xD0 <= nxt <= 0xD7 or nxt == 0xFF:
            pos += 1 if nxt == 0xFF else 2  # stuffed byte, restart marker, fill byte
            continue
        return pos


def _strip_jpeg(data: bytes) -> bytes:
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("not a JPEG")
    out, pos, scans = [b"\xff\xd8"], 2, 0
    while pos < len(data):
        if data[pos] != 0xFF:
            raise ValueError(f"JPEG: expected a marker at byte {pos}")
        while pos < len(data) and data[pos] == 0xFF:  # fill bytes
            pos += 1
        if pos >= len(data):
            break
        marker = data[pos]
        pos += 1
        if marker == _JPEG_EOI:
            if not scans:
                raise ValueError("JPEG has no image scan")
            out.append(b"\xff\xd9")
            return b"".join(out)
        if marker in _JPEG_STANDALONE:
            out.append(bytes((0xFF, marker)))
            continue
        if pos + 2 > len(data):
            raise ValueError("JPEG truncated in a segment header")
        (length,) = struct.unpack(">H", data[pos : pos + 2])
        if length < 2 or pos + length > len(data):
            raise ValueError("JPEG segment length is invalid")
        drop = marker in _JPEG_DROP and not (
            marker == _JPEG_APP14 and length <= _JPEG_APP14_MAX_LEN
        )
        if not drop:
            out.append(bytes((0xFF, marker)) + data[pos : pos + length])
        pos += length
        if marker == _JPEG_SOS:
            scans += 1
            end = _skip_entropy_coded(data, pos)
            out.append(data[pos:end])
            pos = end
    raise ValueError("JPEG has no end-of-image marker")


# Image data and animation; dropped: ICCP, EXIF, XMP, unknown chunks.
_WEBP_KEEP = {b"VP8 ", b"VP8L", b"VP8X", b"ALPH", b"ANIM", b"ANMF"}
_WEBP_IMAGE = {b"VP8 ", b"VP8L", b"ANMF"}
_VP8X_ICC, _VP8X_EXIF, _VP8X_XMP = 0x20, 0x08, 0x04


def _strip_webp(data: bytes) -> bytes:
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("not a WebP")
    (riff_size,) = struct.unpack("<I", data[4:8])
    riff_end = 8 + riff_size
    if riff_end > len(data) or riff_size < 4:
        raise ValueError("WebP RIFF size is invalid")
    chunks, pos, seen = [], 12, set()
    while pos < riff_end:
        if pos + 8 > riff_end:
            raise ValueError("WebP truncated in a chunk header")
        fourcc = data[pos : pos + 4]
        (size,) = struct.unpack("<I", data[pos + 4 : pos + 8])
        end = pos + 8 + size
        if end > riff_end:
            raise ValueError(f"WebP chunk {fourcc!r} runs past the RIFF")
        payload = data[pos + 8 : end]
        seen.add(fourcc)
        if fourcc == b"VP8X":
            if not payload:
                raise ValueError("WebP VP8X chunk is empty")
            flags = payload[0] & ~(_VP8X_ICC | _VP8X_EXIF | _VP8X_XMP)
            payload = bytes((flags,)) + payload[1:]
        if fourcc in _WEBP_KEEP:
            chunks.append(
                fourcc + struct.pack("<I", size) + payload + b"\0" * (size % 2)
            )
        pos = end + size % 2
    if not seen & _WEBP_IMAGE:
        raise ValueError("WebP has no image data")
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


_STRIPPERS: dict[str, Callable[[bytes], bytes]] = {
    "image/png": _strip_png,
    "image/jpeg": _strip_jpeg,
    "image/webp": _strip_webp,
}


def strip_image(data: bytes, media_type: str) -> bytes:
    """Keep only what renders the image; truncate at its end. Pure Python.

    Raises ``ValueError`` when ``data`` is not a well-formed ``media_type`` image.
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
    """Decode ``data`` and save its first frame afresh, in the same format.

    Images over ``max_pixels`` are refused before any pixel is decoded (a small file can
    declare a huge canvas). Raises ``ValueError`` on a malformed or oversized image.
    """
    from PIL import Image

    try:
        options = dict(REENCODE_OPTIONS[media_type])
    except KeyError:
        raise ValueError(f"cannot sanitize media type {media_type!r}") from None
    fmt = options["format"]
    try:
        with Image.open(io.BytesIO(data), formats=[fmt]) as im:
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
            out = io.BytesIO()
            frame.save(out, **options)
    except ValueError:
        raise
    except Exception as e:  # Pillow raises OSError, SyntaxError, struct.error, ...
        raise ValueError(f"not a well-formed {media_type} image: {e}") from e
    return out.getvalue()


# -------------------------------------------------------------------------- registry


def _no_sanitizer(*, max_pixels: int) -> ImageSanitizer:
    return lambda data, media_type: data


def _strip_sanitizer(*, max_pixels: int) -> ImageSanitizer:
    return strip_image


def _reencode_sanitizer(*, max_pixels: int) -> ImageSanitizer:
    if not pillow_available():
        raise ImportError(
            "screenshot_sanitizer='reencode' needs Pillow: pip install 'heed[images]' "
            "(or use 'strip', which needs nothing)"
        )

    def sanitize(data: bytes, media_type: str) -> bytes:
        return reencode_image(data, media_type, max_pixels=max_pixels)

    return sanitize


def _auto_sanitizer(*, max_pixels: int) -> ImageSanitizer:
    if pillow_available():
        return _reencode_sanitizer(max_pixels=max_pixels)
    return _strip_sanitizer(max_pixels=max_pixels)


image_sanitizers: dict[str, Callable[..., ImageSanitizer]] = {
    "auto": _auto_sanitizer,
    "reencode": _reencode_sanitizer,
    "strip": _strip_sanitizer,
    "none": _no_sanitizer,
}


def make_image_sanitizer(
    name: str = "auto", *, max_pixels: int = DEFAULT_MAX_PIXELS
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
    return factory(max_pixels=max_pixels)


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
