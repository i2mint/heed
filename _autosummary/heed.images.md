# heed.images

Neutralise uploaded screenshots before they are stored (issue #15).

Magic-byte sniffing ([`heed.security.sniff_image_type()`](heed.security.md#heed.security.sniff_image_type)) proves a file *starts* like
a PNG, JPEG or WebP. It says nothing about the rest: a polyglot can carry a valid image
header followed by HTML, a ZIP, or a script, and EXIF/XMP metadata can carry GPS
coordinates. A sanitizer rewrites the accepted bytes to cut that down.

Sanitizers are `(data, media_type) -> bytes` callables that raise `ValueError` on a
malformed or refused image, looked up by name in `image_sanitizers`:

- `"auto"` (default): `"reencode"` when Pillow is installed, else `"strip"` (with
  a warning at startup).
- `"reencode"`: `strip` first (structure and cost checks), then decode and re-save
  the first frame with Pillow (`pip install heed[images]`). Only freshly encoded
  pixels are stored: no metadata, comments, extra frames or trailing data. At most
  `max_concurrent` re-encodes run at once; beyond that [`SanitizerBusy`](#heed.images.SanitizerBusy) is
  raised (the router answers 503).
- `"strip"`: pure Python, no dependency. Walks the container, keeps an allow-list of
  chunks/segments (each length-checked where the format fixes it), rejects unknown
  JPEG markers, and truncates at the end marker. Trailing payloads, metadata (EXIF, XMP,
  ICC, text chunks, comments) and animation are gone; the \*\*compressed pixel data is
  kept as sent\*\*, so attacker-chosen bytes can survive inside it.
- `"none"`: store the bytes as received (explicit opt-out).

Neither rewrite makes a stored file safe to serve on its own (even re-encoded pixel data
can hold attacker-chosen byte runs), so attachments must also be served with
[`heed.security.attachment_headers()`](heed.security.md#heed.security.attachment_headers).

```pycon
>>> png = _tiny_png() + b"<html><script>alert(1)</script>"
>>> clean = strip_image(png, "image/png")
>>> clean == _tiny_png(), b"<html>" in clean
(True, False)
```

### Module Attributes

| [`REENCODE_OPTIONS`](#heed.images.REENCODE_OPTIONS)   | Pillow `save` options per media type, used by [`reencode_image()`](#heed.images.reencode_image).   |
|---------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------|

### Functions

| [`make_image_sanitizer`](#heed.images.make_image_sanitizer)([name, max_pixels, ...])      | Build a sanitizer from the `image_sanitizers` registry by name.                                           |
|-----------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------|
| [`pillow_available`](#heed.images.pillow_available)()                                 | Whether [`reencode_image()`](#heed.images.reencode_image) can run (Pillow is importable). |
| [`reencode_image`](#heed.images.reencode_image)(data, media_type, \*[, max_pixels]) | Strip `data`, then decode it and save its first frame afresh, same format.                                |
| [`strip_image`](#heed.images.strip_image)(data, media_type)                      | Keep only what renders the image; truncate at its end.                                                    |

### Exceptions

| [`SanitizerBusy`](#heed.images.SanitizerBusy)   | Every re-encode slot stayed busy for the whole wait: try again later.   |
|------------------------------------------------------------------|-------------------------------------------------------------------------|

### heed.images.REENCODE_OPTIONS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)]* *= {'image/jpeg': {'format': 'JPEG', 'quality': 90}, 'image/png': {'format': 'PNG', 'optimize': False}, 'image/webp': {'format': 'WEBP', 'quality': 90}}*

Pillow `save` options per media type, used by [`reencode_image()`](#heed.images.reencode_image).

### *exception* heed.images.SanitizerBusy

Bases: [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError)

Every re-encode slot stayed busy for the whole wait: try again later.

### heed.images.make_image_sanitizer(name='auto', , max_pixels=25000000, max_concurrent=2, busy_wait_seconds=2.0)

Build a sanitizer from the `image_sanitizers` registry by name.

Fails at construction (not on the first upload) when the name is unknown or its
dependency is missing.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[`bytes`](https://docs.python.org/3/builtins/stdtypes.html#bytes), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)], [`bytes`](https://docs.python.org/3/builtins/stdtypes.html#bytes)]

### heed.images.pillow_available()

Whether [`reencode_image()`](#heed.images.reencode_image) can run (Pillow is importable).

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### heed.images.reencode_image(data, media_type, , max_pixels=25000000)

Strip `data`, then decode it and save its first frame afresh, same format.

Images over `max_pixels` are refused before any pixel is decoded (a small file can
declare a huge canvas). Nothing but pixels (and PNG palette transparency) is carried
over: no comment, EXIF, ICC profile or other metadata. Raises `ValueError` on a
malformed, oversized or too costly image.

* **Return type:**
  [`bytes`](https://docs.python.org/3/builtins/stdtypes.html#bytes)

### heed.images.strip_image(data, media_type)

Keep only what renders the image; truncate at its end. Pure Python.

Raises `ValueError` when `data` is not a well-formed `media_type` image, or
one too costly to decode (over `MAX_JPEG_SCANS` scans, `_MAX_PARTS` parts).

* **Return type:**
  [`bytes`](https://docs.python.org/3/builtins/stdtypes.html#bytes)
