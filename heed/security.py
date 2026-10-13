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

import ipaddress
import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol, runtime_checkable

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
    """
    if not origin or origin == "null" or not origin_allowed(origin, allowed):
        return {}
    headers = {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
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


Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_networks(proxies: Iterable[str | Network]) -> list[Network]:
    """Parse IPs/CIDRs (strings or network objects) into networks; raises ValueError.

    Call it once at startup so a typo fails loudly there, not on every request.

    >>> parse_networks(["127.0.0.1", "10.0.0.0/8"])
    [IPv4Network('127.0.0.1/32'), IPv4Network('10.0.0.0/8')]
    """
    return [
        p
        if isinstance(p, (ipaddress.IPv4Network, ipaddress.IPv6Network))
        else ipaddress.ip_network(p.strip(), strict=False)
        for p in proxies
    ]


def _in_networks(addr: str, networks: list) -> bool:
    try:
        ip = ipaddress.ip_address(addr.strip())
    except ValueError:
        return False
    return any(ip in net for net in networks)


def client_ip(
    peer: str | None,
    forwarded_for: str | None,
    *,
    trusted_proxies: Iterable[str | Network] = (),
) -> str:
    """The client address to rate-limit on.

    ``X-Forwarded-For`` is attacker-controlled unless the direct peer is a trusted
    proxy, so it is only consulted then — and read right-to-left, skipping trusted
    hops, so a client cannot prepend a fake address.

    >>> client_ip("10.0.0.1", "6.6.6.6, 1.2.3.4", trusted_proxies=["10.0.0.0/8"])
    '1.2.3.4'
    >>> client_ip("5.5.5.5", "1.2.3.4")  # untrusted peer: header ignored
    '5.5.5.5'
    """
    peer = peer or "unknown"
    networks = parse_networks(trusted_proxies)
    if not networks or not forwarded_for or not _in_networks(peer, networks):
        return peer
    for hop in reversed([h.strip() for h in forwarded_for.split(",") if h.strip()]):
        if not _in_networks(hop, networks):
            return hop
    return peer


def rate_limit_key(ip: str, *, ipv6_prefix: int = 64) -> str:
    """The bucket key for ``ip``: IPv4 as-is, IPv6 grouped by its /``ipv6_prefix``.

    One IPv6 host typically owns a whole /64, so per-address buckets are trivially
    dodged by rotating addresses.

    >>> rate_limit_key("2001:db8::1") == rate_limit_key("2001:db8::ffff")
    True
    >>> rate_limit_key("1.2.3.4")
    '1.2.3.4'
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 6 and addr.ipv4_mapped is not None:
        return str(addr.ipv4_mapped)
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{addr}/{ipv6_prefix}", strict=False))
    return ip


# ---------------------------------------------------------------------- rate limits


@runtime_checkable
class RateLimiter(Protocol):
    """Decide whether a request keyed by ``key`` (e.g. client IP) is allowed now."""

    def allow(self, key: str) -> bool: ...


class InMemoryRateLimiter:
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
        max_keys: int = 100_000,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.max_per_window = max_per_window
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._clock = clock
        self._hits: OrderedDict[str, deque] = OrderedDict()
        self._lock = threading.Lock()  # the router calls limiters from a threadpool

    def allow(self, key: str) -> bool:
        with self._lock:
            return self._allow(key)

    def _allow(self, key: str) -> bool:
        now = self._clock()
        hits = self._hits.get(key)
        if hits is None:
            hits = self._hits[key] = deque()
            while len(self._hits) > self.max_keys:
                self._hits.popitem(last=False)
        else:
            self._hits.move_to_end(key)
        cutoff = now - self.window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self.max_per_window:
            return False
        hits.append(now)
        return True


class RedisRateLimiter:
    """Fixed-window limiter in Redis — one count shared by every worker and host.

    ``client`` is any redis-py compatible client (injected; tests use a fake). When
    Redis errors, the request is decided by ``fallback`` (a per-process in-memory
    limiter by default), so an outage degrades protection instead of either dropping
    all feedback or removing the limit.
    """

    def __init__(
        self,
        client: Any,
        *,
        max_per_window: int = 30,
        window_seconds: float = 60.0,
        namespace: str = "heed:rl",
        fallback: RateLimiter | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.client = client
        self.max_per_window = max_per_window
        self.window_seconds = window_seconds
        self.namespace = namespace
        self.fallback = fallback or InMemoryRateLimiter(
            max_per_window=max_per_window, window_seconds=window_seconds
        )
        self._clock = clock

    @classmethod
    def from_url(
        cls, url: str, *, socket_timeout: float = 0.5, **kwargs
    ) -> RedisRateLimiter:
        """Build from a ``redis://`` URL (needs the ``[redis]`` extra).

        ``socket_timeout`` bounds both connect and reads, so a hung Redis costs at most
        that long before the fallback limiter decides.
        """
        try:
            import redis  # lazy: optional dependency
        except ImportError as e:  # pragma: no cover - depends on environment
            raise ImportError(
                "The Redis rate limiter needs the redis client: "
                "pip install 'heed[redis]'"
            ) from e
        client = redis.Redis.from_url(
            url, socket_timeout=socket_timeout, socket_connect_timeout=socket_timeout
        )
        return cls(client, **kwargs)

    def allow(self, key: str) -> bool:
        window = int(self._clock() // self.window_seconds)
        redis_key = f"{self.namespace}:{key}:{window}"
        try:
            pipe = self.client.pipeline()
            pipe.incr(redis_key)
            pipe.expire(redis_key, int(self.window_seconds) + 1)
            count = pipe.execute()[0]
        except Exception:  # noqa: BLE001 - any client/network failure
            logger.warning("Redis rate limiter unavailable; using fallback limiter")
            return self.fallback.allow(key)
        return int(count) <= self.max_per_window


def _memory_limiter(*, max_per_window, window_seconds, namespace, redis_url=None):
    return InMemoryRateLimiter(
        max_per_window=max_per_window, window_seconds=window_seconds
    )


def _redis_limiter(*, max_per_window, window_seconds, namespace, redis_url=None):
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
#: never sniffed as another type, never allowed to run script or load anything, and
#: offered as a file if a browser navigates to it directly.
ATTACHMENT_RESPONSE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cross-Origin-Resource-Policy": "cross-origin",
    "Referrer-Policy": "no-referrer",
}


def attachment_headers(media_type: str, *, filename: str | None = None) -> dict:
    """Headers for serving a stored screenshot (issue #15). Use them on every response
    that returns attachment bytes, ideally from an origin separate from the app's.

    Only the image types heed accepts are served as themselves; anything else is sent as
    an opaque download.

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
    }
    disposition = "inline" if known else "attachment"
    if filename is not None:
        safe = "".join(c for c in filename if c.isalnum() or c in "._-") or "attachment"
        disposition += f'; filename="{safe}"'
    headers["Content-Disposition"] = disposition
    return headers
