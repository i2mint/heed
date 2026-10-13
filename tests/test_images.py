"""Screenshot neutralisation (#15): polyglot payloads and metadata are never stored."""

import io
import struct
import zlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from heed import (
    HeedConfig,
    StoreSink,
    attachment_headers,
    make_image_sanitizer,
    make_router,
    reencode_image,
    strip_image,
)
from heed.images import _tiny_png, pillow_available

PAYLOAD = b"<html><script>alert(document.domain)</script></html>"
ORIGIN = "https://app.example"

needs_pillow = pytest.mark.skipif(not pillow_available(), reason="Pillow not installed")


def _png_chunk(ctype: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(ctype + payload)
    return struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", crc)


def _png_with_text_chunk() -> bytes:
    png = _tiny_png()
    iend = png.rindex(b"IEND") - 4
    return png[:iend] + _png_chunk(b"tEXt", b"Comment\x00" + PAYLOAD) + png[iend:]


def _pillow_image(fmt: str, **save) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(out, format=fmt, **save)
    return out.getvalue()


def _jpeg_with_exif() -> bytes:
    from PIL import Image

    exif = Image.Exif()
    exif[0x010E] = PAYLOAD.decode()  # ImageDescription
    jpeg = _pillow_image("JPEG", exif=exif.tobytes())
    com = b"\xff\xfe" + struct.pack(">H", len(PAYLOAD) + 2) + PAYLOAD
    return jpeg[:2] + com + jpeg[2:]


def _webp_with_exif() -> bytes:
    from PIL import Image

    exif = Image.Exif()
    exif[0x010E] = PAYLOAD.decode()
    return _pillow_image("WEBP", exif=exif.tobytes())


# ----------------------------------------------------------------------------- strip


def test_strip_png_drops_trailing_payload_and_text_chunks():
    for dirty in (_tiny_png() + PAYLOAD, _png_with_text_chunk() + PAYLOAD):
        assert strip_image(dirty, "image/png") == _tiny_png()


@pytest.mark.parametrize(
    "bad",
    [
        _tiny_png()[:-6],  # truncated IEND
        _tiny_png()[:19] + b"\x07" + _tiny_png()[20:],  # corrupt IHDR -> bad CRC
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,  # magic bytes only
    ],
)
def test_strip_png_rejects_malformed(bad):
    with pytest.raises(ValueError):
        strip_image(bad, "image/png")


@needs_pillow
def test_strip_jpeg_drops_comments_exif_and_trailer():
    dirty = _jpeg_with_exif() + PAYLOAD
    clean = strip_image(dirty, "image/jpeg")
    assert PAYLOAD not in clean and b"Exif" not in clean
    assert clean.endswith(b"\xff\xd9")
    from PIL import Image

    assert Image.open(io.BytesIO(clean)).size == (8, 8)


@needs_pillow
def test_strip_progressive_jpeg_keeps_every_scan():
    jpeg = _pillow_image("JPEG", progressive=True)
    from PIL import Image

    clean = strip_image(jpeg + PAYLOAD, "image/jpeg")
    im = Image.open(io.BytesIO(clean))
    im.load()
    assert im.size == (8, 8)


@needs_pillow
def test_strip_webp_drops_exif_and_trailer():
    dirty = _webp_with_exif()
    assert PAYLOAD in dirty
    clean = strip_image(dirty + PAYLOAD, "image/webp")
    assert PAYLOAD not in clean
    from PIL import Image

    im = Image.open(io.BytesIO(clean))
    im.load()
    assert im.size == (8, 8) and "exif" not in im.info


def test_strip_rejects_unknown_type_and_junk():
    with pytest.raises(ValueError):
        strip_image(b"GIF89a", "image/gif")
    for media_type, junk in [
        ("image/jpeg", b"\xff\xd8\xff\xe0\x00\x10" + b"\x00" * 14),  # no scan, no EOI
        ("image/webp", b"RIFF\xff\xff\xff\x7fWEBP"),  # RIFF size past the end
    ]:
        with pytest.raises(ValueError):
            strip_image(junk, media_type)


# -------------------------------------------------------------------------- reencode


@needs_pillow
@pytest.mark.parametrize(
    "fmt, media_type",
    [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")],
)
def test_reencode_drops_payload(fmt, media_type):
    from PIL import Image

    clean = reencode_image(_pillow_image(fmt) + PAYLOAD, media_type)
    assert PAYLOAD not in clean
    assert Image.open(io.BytesIO(clean)).format == fmt


@needs_pillow
def test_reencode_refuses_huge_canvas_before_decoding():
    png = _tiny_png()
    huge = png.replace(struct.pack(">II", 1, 1), struct.pack(">II", 2_000, 2_000), 1)
    ihdr_end = 8 + 8 + 13
    fixed = (
        huge[:ihdr_end]
        + struct.pack(">I", zlib.crc32(huge[12:ihdr_end]))
        + huge[ihdr_end + 4 :]
    )
    with pytest.raises(ValueError, match="over"):
        reencode_image(fixed, "image/png", max_pixels=1000)


@needs_pillow
def test_reencode_rejects_header_only_image():
    with pytest.raises(ValueError, match="well-formed"):
        reencode_image(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png")


# -------------------------------------------------------------------------- registry


def test_make_image_sanitizer_names():
    assert make_image_sanitizer("none")(b"x", "image/png") == b"x"
    assert (
        make_image_sanitizer("strip")(_tiny_png() + PAYLOAD, "image/png") == _tiny_png()
    )
    with pytest.raises(ValueError, match="Unknown screenshot_sanitizer"):
        make_image_sanitizer("magic")


def test_reencode_without_pillow_fails_at_construction(monkeypatch):
    import heed.images

    monkeypatch.setattr(heed.images, "pillow_available", lambda: False)
    with pytest.raises(ImportError, match="heed\\[images\\]"):
        make_image_sanitizer("reencode")
    # and "auto" falls back to the pure-Python strip
    assert make_image_sanitizer("auto") is strip_image


# ---------------------------------------------------------------------------- router


def _client(attachments, **cfg):
    app = FastAPI()
    app.include_router(
        make_router(
            StoreSink({}),
            config=HeedConfig(allowed_origins=[ORIGIN], **cfg),
            attachment_store=attachments,
        )
    )
    return TestClient(app)


def _post_shot(client, shot):
    return client.post(
        "/report",
        data={"title": "t"},
        files={"screenshot": ("s.png", shot)},
        headers={"Origin": ORIGIN},
    )


@pytest.mark.parametrize("sanitizer", ["auto", "strip"])
def test_router_stores_only_the_image(sanitizer):
    attachments: dict = {}
    resp = _post_shot(
        _client(attachments, screenshot_sanitizer=sanitizer), _tiny_png() + PAYLOAD
    )
    assert resp.status_code == 200
    (stored,) = attachments.values()
    assert PAYLOAD not in stored and stored.startswith(b"\x89PNG")


def test_router_rejects_magic_bytes_only_polyglot():
    attachments: dict = {}
    resp = _post_shot(_client(attachments), b"\x89PNG\r\n\x1a\n" + PAYLOAD)
    assert resp.status_code == 415 and attachments == {}


def test_router_none_stores_as_received():
    attachments: dict = {}
    resp = _post_shot(
        _client(attachments, screenshot_sanitizer="none"), _tiny_png() + PAYLOAD
    )
    assert resp.status_code == 200
    assert list(attachments.values()) == [_tiny_png() + PAYLOAD]


# --------------------------------------------------------------------------- serving


def test_attachment_headers_never_let_bytes_render_as_a_document():
    h = attachment_headers("image/png", filename='r1.png"; x="<script>')
    assert h["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'none'" in h["Content-Security-Policy"]
    assert "sandbox" in h["Content-Security-Policy"]
    assert h["Content-Disposition"] == 'inline; filename="r1.pngxscript"'
    other = attachment_headers("text/html")
    assert other["Content-Type"] == "application/octet-stream"
    assert other["Content-Disposition"] == "attachment"
