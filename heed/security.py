"""Public-endpoint hardening (the ingest endpoint is unauthenticated and hostile).

Everything here is a small function or a strategy behind a protocol, so the router
composes them and deployments swap implementations without touching it:

- **Origins / CORS**: :func:`origin_allowed`, :func:`cors_headers`,
  :func:`preflight_headers`.
- **Client identity for rate limiting**: :func:`client_ip` (honours
  ``X-Forwarded-For`` only from trusted proxies).
- **Rate limiting**: the :class:`RateLimiter` protocol, an in-memory default
  (:class:`InMemoryRateLimiter`, per process) and :class:`RedisRateLimiter` (shared
  across workers), looked up by name via :func:`make_rate_limiter`.
- **Bot gate**: the :class:`BotVerifier` protocol and :class:`SiteverifyBotVerifier`
  for Cloudflare Turnstile / hCaptcha, built by :func:`make_bot_verifier` only when a
  secret is configured (off otherwise).
- **Attachments**: :func:`sniff_image_type` checks screenshot magic bytes (and
  ``images.py`` rewrites them); :func:`attachment_headers` is how to serve them.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol, runtime_checkable

from enlace.ratelimit import (
    DEFAULT_IPV6_PREFIX,
    DEFAULT_MAX_KEYS,
    DEFAULT_REDIS_SOCKET_TIMEOUT,
    Network,
    Rate,
    RedisLimiter,
    SlidingWindowLimiter,
    bucket_key,
    client_address,
    parse_networks,
)

logger = logging.getLogger("heed.security")

# --------------------------------------------------------------------------- origins


def normalize_origin(origin: str) -> str:
    """Canonical form of an origin for comparison (lower-case, no trailing slash).

    >>> normalize_origin("HTTPS://App.Example/")
    'https://app.example'
    """
    return origin.strip().rstrip("/").lower()


def origin_allowed(origin: str | None, allowed: Iterable[str] | None) -> bool:
    """True if origin is permitted; ``allowed=None`` allows any (the dev default).

    >>> origin_allowed("https://a.example", ["https://a.example"])
    True
    >>> origin_allowed(None, ["https://a.example"])
    False
    """
    if allowed is None:
        return True
    if not origin or origin == "null":
        return False
    return normalize_origin(origin) in {normalize_origin(o) for o in allowed}


def cors_headers(
    origin: str | None,
    allowed: Iterable[str] | None,
    *,
    allow_credentials: bool = False,
) -> dict[str, str]:
    """CORS response headers for ``origin``, or ``{}`` when it is not allowed.

    The request's own origin is echoed (never ``*``) so the response is correct with
    or without credentials; ``Vary: Origin`` keeps shared caches honest.
    ``Retry-After`` is exposed so the widget can tell a visitor when to retry.
    """
    if not origin or origin == "null" or not origin_allowed(origin, allowed):
        return {}
    headers = {
        "Access-Control-Allow-Origin": origin,
        "Vary": "Origin",
        # Not CORS-safelisted, so a cross-origin widget could not read it otherwise.
        "Access-Control-Expose-Headers": "Retry-After",
    }
    if allow_credentials:
        headers["Access-Control-Allow-Credentials"] = "true"
    return headers


def preflight_headers(
    origin: str | None,
    allowed: Iterable[str] | None,
    *,
    allow_credentials: bool = False,
    max_age_seconds: int = 600,
    methods: str = "POST, OPTIONS",
    request_headers: str | None = None,
) -> dict[str, str]:
    """Headers answering a CORS preflight, or ``{}`` when the origin is not allowed."""
    headers = cors_headers(origin, allowed, allow_credentials=allow_credentials)
    if headers:
        headers["Access-Control-Allow-Methods"] = methods
        headers["Access-Control-Allow-Headers"] = request_headers or "Content-Type"
        headers["Access-Control-Max-Age"] = str(max_age_seconds)
    return headers


# ------------------------------------------------------------------------- client ip
#
# The trusted-proxy client address, the bucket key and the limiter algorithms are
# one shared implementation in ``enlace.ratelimit`` (stdlib only), tested there;
# the names below keep heed's public API and its keyword-style constructors.


def client_ip(
    peer: str | None,
    forwarded_for: str | None,
    *,
    trusted_proxies: Iterable[str | Network] = (),
) -> str:
    """The client address to rate-limit on (:func:`enlace.ratelimit.client_address`).

    ``X-Forwarded-For`` is attacker-controlled unless the direct peer is a trusted
    proxy, so it is only consulted then — and read right-to-left, skipping trusted
    hops, so a client cannot prepend a fake address. A hop that is not an address
    stops the walk (the peer is used).

    >>> client_ip("10.0.0.1", "6.6.6.6, 1.2.3.4", trusted_proxies=["10.0.0.0/8"])
    '1.2.3.4'
    >>> client_ip("5.5.5.5", "1.2.3.4")  # untrusted peer: header ignored
    '5.5.5.5'
    """
    return client_address(peer, forwarded_for, trusted=parse_networks(trusted_proxies))


def rate_limit_key(ip: str, *, ipv6_prefix: int = DEFAULT_IPV6_PREFIX) -> str:
    """The bucket key for ``ip``: IPv4 as-is, IPv6 grouped by its /``ipv6_prefix``.

    One IPv6 host typically owns a whole /64, so per-address buckets are trivially
    dodged by rotating addresses (:func:`enlace.ratelimit.bucket_key`).

    >>> rate_limit_key("2001:db8::1") == rate_limit_key("2001:db8::ffff")
    True
    >>> rate_limit_key("1.2.3.4")
    '1.2.3.4'
    """
    return bucket_key(ip, ipv6_prefix=ipv6_prefix)


# ---------------------------------------------------------------------- rate limits


@runtime_checkable
class RateLimiter(Protocol):
    """Decide whether a request keyed by ``key`` (e.g. client IP) is allowed now."""

    def allow(self, key: str) -> bool: ...


class _WindowKnobs:
    """heed's names for the shared limiters' :class:`~enlace.ratelimit.Rate`."""

    rate: Rate

    @property
    def max_per_window(self) -> int:
        return self.rate.limit

    @property
    def window_seconds(self) -> float:
        return self.rate.window


class InMemoryRateLimiter(_WindowKnobs, SlidingWindowLimiter):
    """Sliding-window, in-memory limiter. Per process — NOT shared across workers.

    Fine for a single-worker deployment; with several workers each one counts alone,
    so the effective limit is multiplied. Use :class:`RedisRateLimiter` there. The
    number of tracked keys is bounded (``max_keys``, least-recently-seen evicted) so a
    flood of distinct keys cannot exhaust memory.
    """

    def __init__(
        self,
        *,
        max_per_window: int = 30,
        window_seconds: float = 60.0,
        max_keys: int = DEFAULT_MAX_KEYS,
        clock: Callable[[], float] = time.monotonic,
    ):
        super().__init__(
            Rate(max_per_window, window_seconds), max_keys=max_keys, clock=clock
        )


class _AllowAsAcquire:
    """Adapt an ``allow(key) -> bool`` limiter to ``acquire(key) -> seconds``."""

    def __init__(self, limiter: RateLimiter, *, wait: float):
        self.limiter, self.wait = limiter, wait

    def acquire(self, key: str) -> float:
        return 0.0 if self.limiter.allow(key) else self.wait


class RedisRateLimiter(_WindowKnobs, RedisLimiter):
    """Fixed-window limiter in Redis — one count shared by every worker and host.

    ``client`` is any redis-py compatible client (injected; tests use a fake). When
    Redis errors, the request is decided by ``fallback`` (a per-process in-memory
    limiter by default), so an outage degrades protection instead of either dropping
    all feedback or removing the limit. :meth:`from_url` builds the client from a
    ``redis://`` URL (needs the ``[redis]`` extra).
    """

    install_hint = "pip install 'heed[redis]'"

    def __init__(
        self,
        client: Any,
        *,
        max_per_window: int = 30,
        window_seconds: float = 60.0,
        namespace: str = "heed:rl",
        fallback: RateLimiter | None = None,
        max_keys: int = DEFAULT_MAX_KEYS,
        clock: Callable[[], float] = time.time,
    ):
        if fallback is None:
            fallback = InMemoryRateLimiter(
                max_per_window=max_per_window,
                window_seconds=window_seconds,
                max_keys=max_keys,
            )
        elif not hasattr(fallback, "acquire"):  # any ``allow``-only RateLimiter
            fallback = _AllowAsAcquire(fallback, wait=window_seconds)
        super().__init__(
            client,
            Rate(max_per_window, window_seconds),
            namespace=namespace,
            fallback=fallback,
            clock=clock,
        )


def _memory_limiter(
    *, max_per_window, window_seconds, namespace, max_keys=DEFAULT_MAX_KEYS, **_
):
    return InMemoryRateLimiter(
        max_per_window=max_per_window, window_seconds=window_seconds, max_keys=max_keys
    )


def _redis_limiter(
    *,
    max_per_window,
    window_seconds,
    namespace,
    redis_url=None,
    max_keys=DEFAULT_MAX_KEYS,
    socket_timeout=DEFAULT_REDIS_SOCKET_TIMEOUT,
    **_,
):
    if not redis_url:
        raise ValueError(
            "rate_limit_backend='redis' needs redis_url (HEED_REDIS_URL), "
            "e.g. redis://localhost:6379/0"
        )
    return RedisRateLimiter.from_url(
        redis_url,
        max_per_window=max_per_window,
        window_seconds=window_seconds,
        namespace=f"heed:rl:{namespace}",
        max_keys=max_keys,
        socket_timeout=socket_timeout,
    )


rate_limiter_backends: dict[str, Callable[..., RateLimiter]] = {
    "memory": _memory_limiter,
    "redis": _redis_limiter,
}


def make_rate_limiter(
    backend: str = "memory",
    *,
    max_per_window: int,
    window_seconds: float = 60.0,
    namespace: str = "ip",
    redis_url: str | None = None,
    max_keys: int = DEFAULT_MAX_KEYS,
    redis_socket_timeout: float = DEFAULT_REDIS_SOCKET_TIMEOUT,
) -> RateLimiter:
    """Build a limiter from the :data:`rate_limiter_backends` registry by name."""
    try:
        factory = rate_limiter_backends[backend]
    except KeyError:
        raise ValueError(
            f"Unknown rate_limit_backend {backend!r}. "
            f"Known: {sorted(rate_limiter_backends)}"
        ) from None
    return factory(
        max_per_window=max_per_window,
        window_seconds=window_seconds,
        namespace=namespace,
        redis_url=redis_url,
        max_keys=max_keys,
        socket_timeout=redis_socket_timeout,
    )


# ------------------------------------------------------------------------- bot gate


@runtime_checkable
class BotVerifier(Protocol):
    """Verify a human-challenge token server-side; True means "looks human"."""

    def verify(self, token: str | None, *, remote_ip: str | None = None) -> bool: ...


HttpPost = Callable[[str, Mapping[str, str], float], Mapping[str, Any]]


def _urllib_post_form(
    url: str, data: Mapping[str, str], timeout: float
) -> Mapping[str, Any]:
    body = urllib.parse.urlencode(dict(data)).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode())


SITEVERIFY_URLS = {
    "turnstile": "https://challenges.cloudflare.com/turnstile/v0/siteverify",
    "hcaptcha": "https://api.hcaptcha.com/siteverify",
}


class SiteverifyBotVerifier:
    """Turnstile / hCaptcha verification (both speak the same siteverify protocol).

    Fails closed: a missing token, a network error, or a malformed answer all verify
    as False. ``expected_hostnames``, when given, also rejects tokens solved on
    another site. ``http_post`` is injectable (tests never touch the network).
    """

    def __init__(
        self,
        secret: str,
        *,
        verify_url: str = SITEVERIFY_URLS["turnstile"],
        expected_hostnames: Iterable[str] | None = None,
        timeout: float = 5.0,
        max_token_chars: int = 4096,
        http_post: HttpPost = _urllib_post_form,
    ):
        if not secret:
            raise ValueError("SiteverifyBotVerifier needs a non-empty secret")
        self._secret = secret
        self.verify_url = verify_url
        self.expected_hostnames = (
            None
            if expected_hostnames is None
            else {h.lower() for h in expected_hostnames}
        )
        self.timeout = timeout
        self.max_token_chars = max_token_chars
        self._http_post = http_post

    def __repr__(self) -> str:  # never leak the secret into logs
        return f"{type(self).__name__}(verify_url={self.verify_url!r})"

    def verify(self, token: str | None, *, remote_ip: str | None = None) -> bool:
        if not token or len(token) > self.max_token_chars:
            return False
        data = {"secret": self._secret, "response": token}
        if remote_ip and remote_ip != "unknown":
            data["remoteip"] = remote_ip
        try:
            answer = self._http_post(self.verify_url, data, self.timeout)
        except Exception:  # noqa: BLE001 - fail closed on any transport error
            logger.warning("bot verification request failed; rejecting submission")
            return False
        if not isinstance(answer, Mapping) or answer.get("success") is not True:
            return False
        if self.expected_hostnames is not None:
            hostname = str(answer.get("hostname") or "").lower()
            return hostname in self.expected_hostnames
        return True


bot_verifier_providers: dict[str, Callable[..., BotVerifier]] = {
    name: (
        lambda secret, _url=url, **kw: SiteverifyBotVerifier(
            secret, verify_url=_url, **kw
        )
    )
    for name, url in SITEVERIFY_URLS.items()
}


def make_bot_verifier(
    provider: str | None,
    secret: str | None,
    **kwargs,
) -> BotVerifier | None:
    """Return a verifier, or ``None`` (gate off) when no secret is configured.

    ``provider`` defaults to ``"turnstile"`` when a secret is given.
    """
    if not secret:
        return None
    name = provider or "turnstile"
    try:
        factory = bot_verifier_providers[name]
    except KeyError:
        raise ValueError(
            f"Unknown bot_gate {name!r}. Known: {sorted(bot_verifier_providers)}"
        ) from None
    return factory(secret, **kwargs)


# ---------------------------------------------------------------------- attachments

_IMAGE_SIGNATURES: tuple[tuple[str, Callable[[bytes], bool]], ...] = (
    ("image/png", lambda b: b.startswith(b"\x89PNG\r\n\x1a\n")),
    ("image/jpeg", lambda b: b.startswith(b"\xff\xd8\xff")),
    ("image/webp", lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP"),
)

IMAGE_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def sniff_image_type(data: bytes) -> str | None:
    """The media type of ``data`` from its magic bytes, or None if not an allowed image.

    Never trust the client's declared content type: an "image" might be HTML/SVG.

    >>> sniff_image_type(b"\\x89PNG\\r\\n\\x1a\\n...")
    'image/png'
    >>> sniff_image_type(b"<svg onload=alert(1)>") is None
    True
    """
    for media_type, matches in _IMAGE_SIGNATURES:
        if matches(data):
            return media_type
    return None


#: Response headers for serving a stored attachment, whatever its bytes contain:
#: never sniffed as another type, never allowed to run script or load anything, never
#: cached by shared caches.
ATTACHMENT_RESPONSE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "private, no-store",
}


def attachment_headers(
    media_type: str, *, filename: str | None = None, cross_origin: bool = False
) -> dict:
    """Headers for serving a stored screenshot (issue #15). Use them on every response
    that returns attachment bytes, ideally from an origin separate from the app's.

    Only the image types heed accepts are served as themselves; anything else is sent as
    an opaque download. Embedding is limited to the same site
    (``Cross-Origin-Resource-Policy: same-site``) unless ``cross_origin=True``, which a
    screenshot embedded elsewhere (e.g. in a GitHub issue) needs.

    >>> h = attachment_headers("image/png", filename="r1.png")
    >>> h["Content-Type"], h["X-Content-Type-Options"], h["Content-Disposition"]
    ('image/png', 'nosniff', 'inline; filename="r1.png"')
    >>> attachment_headers("text/html")["Content-Type"]
    'application/octet-stream'
    """
    known = media_type in IMAGE_EXTENSIONS
    headers = {
        "Content-Type": media_type if known else "application/octet-stream",
        **ATTACHMENT_RESPONSE_HEADERS,
        "Cross-Origin-Resource-Policy": "cross-origin" if cross_origin else "same-site",
    }
    disposition = "inline" if known else "attachment"
    if filename is not None:
        safe = "".join(
            c for c in filename if (c.isascii() and c.isalnum()) or c in "._-"
        )
        disposition += f'; filename="{safe or "attachment"}"'
    headers["Content-Disposition"] = disposition
    return headers
