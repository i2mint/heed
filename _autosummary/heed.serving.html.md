# heed.serving

Serve stored screenshots: a read-only route meant for its own origin (issue #19).

heed stores sanitized screenshots in the attachment store (`images.py`); a GitHub
issue can only show one through a URL (design.md §9). This module is that URL’s server:

- `AttachmentSigner` mints and checks signed URLs. Report ids are not secrets (the
  submit response, the report JSON, logs and holding stores all carry them), so by
  default a screenshot is served only with a valid HMAC signature, which only the
  backend can make — typically when a sink embeds the screenshot in an issue. Held or
  rejected reports’ screenshots therefore have no URL at all.
- `make_attachment_router` / `make_attachment_app` serve `GET <path>/<ref>` with
  `attachment_headers` on every response. Mount them on a dedicated origin (a
  subdomain such as `files.example.com`), never the app’s: `allowed_hosts` refuses
  any other `Host`.
- `screenshot_url_resolver` plugs the signer into
  `GitHubIssuesSink(screenshot_url=)`.

```pycon
>>> from fastapi.testclient import TestClient
>>> from heed.images import _tiny_png
>>> store = {"0123456789abcdef0123456789abcdef.png": _tiny_png()}
>>> signer = AttachmentSigner("k" * 32)
>>> app = make_attachment_app(store, signer=signer, allowed_hosts=["testserver"])
>>> url = signer.url("/attachments", "0123456789abcdef0123456789abcdef.png")
>>> r = TestClient(app).get(url)
>>> r.status_code, r.headers["content-type"], r.headers["x-content-type-options"]
(200, 'image/png', 'nosniff')
>>> TestClient(app).get("/attachments/0123456789abcdef0123456789abcdef.png").status_code
404
```

### Module Attributes

| [`DEFAULT_REF_PATTERN`](#heed.serving.DEFAULT_REF_PATTERN)   | `{uuid4 hex}.{image extension}` (`process_submission`).   |
|------------------------------------------------------------------------|-----------------------------------------------------------|

### Functions

| [`make_attachment_app`](#heed.serving.make_attachment_app)(attachment_store, ...)    | A standalone app serving only the attachment route (no docs, no OpenAPI): run it on the dedicated attachment origin.                |
|------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------|
| [`make_attachment_router`](#heed.serving.make_attachment_router)(attachment_store, \*)  | A router serving `GET {path}/{ref}` from `attachment_store`, read-only.                                                             |
| [`screenshot_url_resolver`](#heed.serving.screenshot_url_resolver)(base_url, signer, \*) | A `GitHubIssuesSink(screenshot_url=...)` callable: the report's first image attachment as a (signed) URL under `base_url`, or None. |

### Classes

| [`AttachmentSigner`](#heed.serving.AttachmentSigner)(key, \*[, previous_keys, ...])   | Mint and check signed attachment URLs (HMAC-SHA256 over the ref and expiry).   |
|----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------|

### *class* heed.serving.AttachmentSigner(key, \*, previous_keys=(), ttl_seconds=None, clock=<built-in function time>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Mint and check signed attachment URLs (HMAC-SHA256 over the ref and expiry).

`ttl_seconds=None` (the default) signs URLs that never expire: a GitHub issue
keeps showing its screenshot, and access is revoked by deleting the attachment or
rotating the key. A ttl makes URLs expire, which suits links handed to people rather
than embedded in issues. `previous_keys` still verify (never sign), for rotation.

```pycon
>>> s = AttachmentSigner("k" * 32, ttl_seconds=60, clock=lambda: 1000)
>>> q = s.sign("ab.png")
>>> q["exp"], s.verify("ab.png", **q), s.verify("other.png", **q)
('1060', True, False)
>>> later = AttachmentSigner("k" * 32, ttl_seconds=60, clock=lambda: 2000)
>>> later.verify("ab.png", **q)
False
```

#### *classmethod* from_config(config, \*\*kwargs)

The signer `config` describes, or None if it has no signing key.

* **Return type:**
  [`AttachmentSigner`](#heed.serving.AttachmentSigner) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### sign(ref)

The query parameters authorising `ref`: `sig`, and `exp` with a ttl.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

#### url(base_url, ref)

`{base_url}/{ref}?...`: the signed URL of `ref` under `base_url`.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

#### verify(ref, , sig=None, exp=None)

Whether `sig` (and `exp`, if any) authorise `ref` right now.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### heed.serving.DEFAULT_REF_PATTERN *= re.compile('[0-9a-f]{32}\\\\.(?:png|jpg|webp)')*

`{uuid4 hex}.{image extension}` (`process_submission`).
Anything else is refused before the store is touched (no path tricks into a file
store).

* **Type:**
  Refs heed itself writes

### heed.serving.make_attachment_app(attachment_store, \*\*router_kwargs)

A standalone app serving only the attachment route (no docs, no OpenAPI): run it
on the dedicated attachment origin. Keyword arguments go to
`make_attachment_router`.

* **Return type:**
  `FastAPI`

### heed.serving.make_attachment_router(attachment_store, , config=None, signer=None, allow_unsigned=False, allowed_hosts=None, cross_origin=False, path='/attachments', ref_pattern=re.compile('[0-9a-f]{32}\\\\\\\\.(?:png|jpg|webp)'))

A router serving `GET {path}/{ref}` from `attachment_store`, read-only.

Every response that returns bytes carries `attachment_headers`; the bytes are
re-sniffed, and anything that is not the image its extension names is sent as an
opaque download. A missing, malformed, unsigned or expired ref is the same bare 404,
so the route is no oracle for which reports exist; a wrong `Host` is a 421 (a
deployment error worth seeing). Behind a reverse proxy, the `Host` checked is the
one the proxy forwards: keep it the browser’s (Caddy and nginx can).

* **Parameters:**
  * **attachment_store** ([`Mapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`bytes`](https://docs.python.org/3/builtins/stdtypes.html#bytes)]) – Where `process_submission` put the screenshots.
  * **config** ([`HeedConfig`](heed.config.html.md#heed.config.HeedConfig) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Defaults for `signer` (`attachment_signing_key`,
    `attachment_url_ttl_seconds`) and `allowed_hosts`
    (`attachment_hosts`).
  * **signer** ([`AttachmentSigner`](#heed.serving.AttachmentSigner) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Required unless `allow_unsigned`: report ids are not secret.
  * **allow_unsigned** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Serve any ref without a signature (a ref is then the
    capability). Only for stores whose every screenshot may be public.
  * **allowed_hosts** ([`Iterable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Iterable)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)] | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Hostnames this route answers on: the dedicated attachment origin.
    `None` answers on any host (and logs a warning).
  * **cross_origin** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – `Cross-Origin-Resource-Policy: cross-origin`, which an image
    embedded in a GitHub issue needs (GitHub’s camo proxy fetches it).
  * **ref_pattern** ([`Pattern`](https://docs.python.org/3/library/re.html#re.Pattern)) – Refs to serve (full match); the default is what heed writes.
* **Return type:**
  `APIRouter`

### heed.serving.screenshot_url_resolver(base_url, signer, , ref_pattern=re.compile('[0-9a-f]{32}\\\\\\\\.(?:png|jpg|webp)'))

A `GitHubIssuesSink(screenshot_url=...)` callable: the report’s first image
attachment as a (signed) URL under `base_url`, or None.

Pass `signer=None` only if the attachment route was built with `allow_unsigned`.
Refs the route would not serve (`ref_pattern`) get no URL.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[`Report`](heed.base.html.md#heed.base.Report)], [`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)]
