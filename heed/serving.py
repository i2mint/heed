"""Serve stored screenshots: a read-only route meant for its own origin (issue #19).

heed stores sanitized screenshots in the attachment store (``images.py``); a GitHub
issue can only show one through a URL (design.md §9). This module is that URL's server:

- ``AttachmentSigner`` mints and checks signed URLs. Report ids are not secrets (the
  submit response, the report JSON, logs and holding stores all carry them), so by
  default a screenshot is served only with a valid HMAC signature, which only the
  backend can make — typically when a sink embeds the screenshot in an issue. Held or
  rejected reports' screenshots therefore have no URL at all.
- ``make_attachment_router`` / ``make_attachment_app`` serve ``GET <path>/<ref>`` with
  ``attachment_headers`` on every response. Mount them on a dedicated origin (a
  subdomain such as ``files.example.com``), never the app's: ``allowed_hosts`` refuses
  any other ``Host``.
- ``screenshot_url_resolver`` plugs the signer into
  ``GitHubIssuesSink(screenshot_url=)``.

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
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import time
from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from fastapi import APIRouter, FastAPI, Request, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from heed.config import ENV_PREFIX, HeedConfig
from heed.images import pillow_available
from heed.security import IMAGE_EXTENSIONS, attachment_headers, sniff_image_type

if TYPE_CHECKING:
    from heed.base import Report

logger = logging.getLogger(__name__)

#: Refs heed itself writes: ``{uuid4 hex}.{image extension}`` (``process_submission``).
#: Anything else is refused before the store is touched (no path tricks into a file
#: store).
DEFAULT_REF_PATTERN = re.compile(
    r"[0-9a-f]{32}\.(?:%s)" % "|".join(map(re.escape, IMAGE_EXTENSIONS.values()))
)
#: ``allowed_hosts=ANY_HOST`` (or ``["*"]``): the route answers on every host.
ANY_HOST = "*"
MIN_SIGNING_KEY_BYTES = 32
_SIGNATURE_CONTEXT = b"heed-attachment-v1"
_SIG = re.compile(r"[A-Za-z0-9_-]{43}")  # urlsafe base64 of a SHA-256, unpadded
_EXP = re.compile(r"[0-9]{1,12}")  # a Unix time, in seconds
_BARE_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
_MEDIA_TYPE_OF_EXTENSION = {ext: media for media, ext in IMAGE_EXTENSIONS.items()}


def _key_bytes(key: str | bytes) -> bytes:
    key = key.encode() if isinstance(key, str) else bytes(key)
    if len(key) < MIN_SIGNING_KEY_BYTES:
        raise ValueError(
            f"an attachment signing key needs at least {MIN_SIGNING_KEY_BYTES} bytes; "
            "make one with: "
            "python -c 'import secrets; print(secrets.token_urlsafe(32))'"
        )
    return key


class AttachmentSigner:
    """Mint and check signed attachment URLs (HMAC-SHA256 over the ref and expiry).

    ``ttl_seconds=None`` (the default) signs URLs that never expire: a GitHub issue
    keeps showing its screenshot, and access is revoked by deleting the attachment or
    rotating the key. A ttl makes URLs expire, which suits links handed to people rather
    than embedded in issues. ``previous_keys`` still verify (never sign), for rotation.

    >>> s = AttachmentSigner("k" * 32, ttl_seconds=60, clock=lambda: 1000)
    >>> q = s.sign("ab.png")
    >>> q["exp"], s.verify("ab.png", **q), s.verify("other.png", **q)
    ('1060', True, False)
    >>> later = AttachmentSigner("k" * 32, ttl_seconds=60, clock=lambda: 2000)
    >>> later.verify("ab.png", **q)
    False
    """

    def __init__(
        self,
        key: str | bytes,
        *,
        previous_keys: Iterable[str | bytes] = (),
        ttl_seconds: float | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self._key = _key_bytes(key)
        self._verify_keys = (self._key, *map(_key_bytes, previous_keys))
        if ttl_seconds is not None and ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be positive or None, got {ttl_seconds}")
        self.ttl_seconds = ttl_seconds
        self._clock = clock

    def __repr__(self) -> str:  # never leak a key into logs
        return f"AttachmentSigner(ttl_seconds={self.ttl_seconds!r})"

    @classmethod
    def from_config(cls, config: HeedConfig, **kwargs) -> AttachmentSigner | None:
        """The signer ``config`` describes, or None if it has no signing key."""
        if not config.attachment_signing_key:
            return None
        kwargs.setdefault("previous_keys", config.attachment_previous_keys)
        return cls(
            config.attachment_signing_key,
            ttl_seconds=config.attachment_url_ttl_seconds,
            **kwargs,
        )

    @staticmethod
    def _mac(key: bytes, ref: str, exp: str) -> str:
        msg = b"\n".join([_SIGNATURE_CONTEXT, ref.encode(), exp.encode()])
        digest = hmac.new(key, msg, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def sign(self, ref: str) -> dict[str, str]:
        """The query parameters authorising ``ref``: ``sig``, and ``exp`` with a ttl."""
        exp = (
            ""
            if self.ttl_seconds is None
            else str(int(self._clock() + self.ttl_seconds))
        )
        params = {"exp": exp} if exp else {}
        return {**params, "sig": self._mac(self._key, ref, exp)}

    def verify(
        self, ref: str, *, sig: str | None = None, exp: str | None = None
    ) -> bool:
        """Whether ``sig`` (and ``exp``, if any) authorise ``ref`` right now."""
        if not sig or not _SIG.fullmatch(sig):
            return False
        exp = exp or ""
        if exp:
            if not _EXP.fullmatch(exp) or int(exp) < self._clock():
                return False
        elif self.ttl_seconds is not None:
            return False  # this signer only issues expiring URLs
        return any(
            hmac.compare_digest(self._mac(key, ref, exp), sig)
            for key in self._verify_keys
        )

    def url(self, base_url: str, ref: str) -> str:
        """``{base_url}/{ref}?...``: the signed URL of ``ref`` under ``base_url``."""
        return f"{base_url.rstrip('/')}/{ref}?{urlencode(self.sign(ref))}"


def screenshot_url_resolver(
    base_url: str,
    signer: AttachmentSigner | None,
    *,
    ref_pattern: re.Pattern = DEFAULT_REF_PATTERN,
) -> Callable[[Report], str | None]:
    """A ``GitHubIssuesSink(screenshot_url=...)`` callable: the report's first image
    attachment as a (signed) URL under ``base_url``, or None.

    Pass ``signer=None`` only if the attachment route was built with ``allow_unsigned``.
    Refs the route would not serve (``ref_pattern``) get no URL.
    """

    def screenshot_url(report: Report) -> str | None:
        for attachment in report.attachments:
            if attachment.media_type in IMAGE_EXTENSIONS and ref_pattern.fullmatch(
                attachment.ref
            ):
                if signer is None:
                    return f"{base_url.rstrip('/')}/{attachment.ref}"
                return signer.url(base_url, attachment.ref)
        return None

    return screenshot_url


def _host_of(request: Request) -> str:
    return (request.url.hostname or "").lower()


def make_attachment_router(
    attachment_store: Mapping[str, bytes],
    *,
    config: HeedConfig | None = None,
    signer: AttachmentSigner | None = None,
    allow_unsigned: bool = False,
    allowed_hosts: Iterable[str] | None = None,
    cross_origin: bool = False,
    cache_control: str | None = None,
    max_bytes: int | None = None,
    path: str = "/attachments",
    ref_pattern: re.Pattern = DEFAULT_REF_PATTERN,
) -> APIRouter:
    """A router serving ``GET {path}/{ref}`` from ``attachment_store``, read-only.

    Every response that returns bytes carries ``attachment_headers``; the bytes are
    re-sniffed, and anything that is not the image its extension names is sent as an
    opaque download. A missing, malformed, unsigned or expired ref is the same bare 404,
    so the route is no oracle for which reports exist; a wrong ``Host`` is a 421 (a
    deployment error worth seeing). Behind a reverse proxy, the ``Host`` checked is the
    one the proxy forwards: keep it the browser's (Caddy and nginx can).

    Args:
        attachment_store: Where ``process_submission`` put the screenshots.
        config: Defaults for ``signer`` (``attachment_signing_key``,
            ``attachment_url_ttl_seconds``) and ``allowed_hosts``
            (``attachment_hosts``).
        signer: Required unless ``allow_unsigned``: report ids are not secret.
        allow_unsigned: Serve any ref without a signature (a ref is then the
            capability). Only for stores whose every screenshot may be public.
        allowed_hosts: Hostnames this route answers on: the dedicated attachment origin.
            Required (here or as ``config.attachment_hosts``): ``ANY_HOST`` says any
            host is fine, which is only right when the route has no origin of its own.
        cross_origin: ``Cross-Origin-Resource-Policy: cross-origin``, which an image
            embedded in a GitHub issue needs (GitHub's camo proxy fetches it).
        cache_control: Replaces the ``Cache-Control`` of a served image (default
            ``private, no-store``), e.g. ``public, max-age=300`` if GitHub's camo proxy
            should cache signed, cross-origin images. Defaults to
            ``config.attachment_cache_control``.
        max_bytes: Stored values larger than this are not served (a bare 404 and a
            warning). Defaults to ``config.max_screenshot_bytes``. It does not stop a
            store from reading the value into memory; it keeps serving from relying on
            the ingest cap alone.
        ref_pattern: Refs to serve (full match); the default is what heed writes.
    """
    cfg = config or HeedConfig()
    signer = signer or AttachmentSigner.from_config(cfg)
    if signer is None and not allow_unsigned:
        raise ValueError(
            "make_attachment_router needs a signer: report ids appear in submit "
            "responses, report JSON and logs, so an unsigned ref would serve any "
            f"screenshot to whoever saw its id. Set {ENV_PREFIX}ATTACHMENT_SIGNING_KEY "
            "(HeedConfig.attachment_signing_key), pass signer=AttachmentSigner(key), "
            "or pass allow_unsigned=True if every stored screenshot may be public."
        )
    if allowed_hosts is None:
        allowed_hosts = cfg.attachment_hosts
    if allowed_hosts is None:
        raise ValueError(
            "make_attachment_router needs allowed_hosts: the hostnames of the "
            "dedicated attachment origin (HeedConfig.attachment_hosts / "
            f"{ENV_PREFIX}ATTACHMENT_HOSTS). Pass allowed_hosts=ANY_HOST to answer on "
            "any host."
        )
    if isinstance(allowed_hosts, str) and allowed_hosts != ANY_HOST:
        raise TypeError(
            "allowed_hosts must be a list of hostnames, not a string: "
            f"{allowed_hosts!r}"
        )
    hosts = {allowed_hosts} if isinstance(allowed_hosts, str) else set(allowed_hosts)
    hosts = {h.lower() for h in hosts}
    if ANY_HOST in hosts:
        hosts = None
    if cache_control is None:
        cache_control = cfg.attachment_cache_control
    if cache_control is not None and not cache_control.isprintable():
        raise ValueError(f"cache_control must be a single header line: {cache_control!r}")
    if max_bytes is None:
        max_bytes = cfg.max_screenshot_bytes
    if not pillow_available():
        logger.warning(
            "heed: Pillow is not installed, so screenshots ingested here with the "
            "default sanitizer were stripped, not re-encoded: compressed pixel data is "
            "stored as sent. Install heed[images] where attachments are served."
        )
    router = APIRouter(redirect_slashes=False)  # no redirect to whatever Host says

    def not_found() -> Response:
        return Response(status_code=404, headers=_BARE_HEADERS)

    @router.get(path + "/{ref}", include_in_schema=False)
    def get_attachment(
        ref: str, request: Request, sig: str | None = None, exp: str | None = None
    ) -> Response:
        if hosts is not None and _host_of(request) not in hosts:
            return Response(status_code=421, headers=_BARE_HEADERS)
        if not ref_pattern.fullmatch(ref):
            return not_found()
        if signer is not None and not signer.verify(ref, sig=sig, exp=exp):
            return not_found()
        try:
            data = attachment_store[ref]
        except KeyError:
            return not_found()
        if len(data) > max_bytes:
            logger.warning(
                "heed: %s is %d bytes, over max_bytes=%d: not served", ref, len(data), max_bytes
            )
            return not_found()
        media_type = sniff_image_type(data)
        if media_type != _MEDIA_TYPE_OF_EXTENSION.get(ref.rsplit(".", 1)[-1]):
            media_type = "application/octet-stream"
        headers = attachment_headers(
            media_type, filename=ref, cross_origin=cross_origin
        )
        if cache_control is not None:
            headers["Cache-Control"] = cache_control
        return Response(content=data, headers=headers)

    return router


def make_attachment_app(
    attachment_store: Mapping[str, bytes], **router_kwargs
) -> FastAPI:
    """A standalone app serving only the attachment route (no docs, no OpenAPI): run it
    on the dedicated attachment origin. Keyword arguments go to
    ``make_attachment_router``."""
    app = FastAPI(
        title="heed attachments",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
    app.include_router(make_attachment_router(attachment_store, **router_kwargs))

    @app.exception_handler(StarletteHTTPException)
    async def _bare(request: Request, exc: StarletteHTTPException) -> Response:
        # Other paths and methods: the status alone, no framework JSON, never cached.
        return Response(status_code=exc.status_code, headers=_BARE_HEADERS)

    return app
