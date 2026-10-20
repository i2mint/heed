# heed.security

Public-endpoint hardening (the ingest endpoint is unauthenticated and hostile).

Everything here is a small function or a strategy behind a protocol, so the router
composes them and deployments swap implementations without touching it:

- **Origins / CORS**: [`origin_allowed()`](#heed.security.origin_allowed), [`cors_headers()`](#heed.security.cors_headers),
  [`preflight_headers()`](#heed.security.preflight_headers).
- **Client identity for rate limiting**: [`client_ip()`](#heed.security.client_ip) (honours
  `X-Forwarded-For` only from trusted proxies).
- **Rate limiting**: the [`RateLimiter`](#heed.security.RateLimiter) protocol, an in-memory default
  ([`InMemoryRateLimiter`](#heed.security.InMemoryRateLimiter), per process) and [`RedisRateLimiter`](#heed.security.RedisRateLimiter) (shared
  across workers), looked up by name via [`make_rate_limiter()`](#heed.security.make_rate_limiter).
- **Bot gate**: the [`BotVerifier`](#heed.security.BotVerifier) protocol and [`SiteverifyBotVerifier`](#heed.security.SiteverifyBotVerifier)
  for Cloudflare Turnstile / hCaptcha, built by [`make_bot_verifier()`](#heed.security.make_bot_verifier) only when a
  secret is configured (off otherwise).
- **Attachments**: [`sniff_image_type()`](#heed.security.sniff_image_type) checks screenshot magic bytes (and
  `images.py` rewrites them); [`attachment_headers()`](#heed.security.attachment_headers) is how to serve them.

### Module Attributes

| [`ATTACHMENT_RESPONSE_HEADERS`](#heed.security.ATTACHMENT_RESPONSE_HEADERS)   | Response headers for serving a stored attachment, whatever its bytes contain: never sniffed as another type, never allowed to run script or load anything, never cached by shared caches.   |
|--------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|

### Functions

| [`attachment_headers`](#heed.security.attachment_headers)(media_type, \*[, ...])         | Headers for serving a stored screenshot (issue #15).                            |
|----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------|
| [`client_ip`](#heed.security.client_ip)(peer, forwarded_for, \*[, ...])         | The client address to rate-limit on (`enlace.ratelimit.client_address()`).      |
| [`cors_headers`](#heed.security.cors_headers)(origin, allowed, \*[, ...])          | CORS response headers for `origin`, or `{}` when it is not allowed.             |
| [`make_bot_verifier`](#heed.security.make_bot_verifier)(provider, secret, \*\*kwargs)   | Return a verifier, or `None` (gate off) when no secret is configured.           |
| [`make_rate_limiter`](#heed.security.make_rate_limiter)([backend, window_seconds, ...]) | Build a limiter from the `rate_limiter_backends` registry by name.              |
| [`normalize_origin`](#heed.security.normalize_origin)(origin)                          | Canonical form of an origin for comparison (lower-case, no trailing slash).     |
| [`origin_allowed`](#heed.security.origin_allowed)(origin, allowed)                   | True if origin is permitted; `allowed=None` allows any (the dev default).       |
| [`preflight_headers`](#heed.security.preflight_headers)(origin, allowed, \*[, ...])     | Headers answering a CORS preflight, or `{}` when the origin is not allowed.     |
| [`rate_limit_key`](#heed.security.rate_limit_key)(ip, \*[, ipv6_prefix])             | The bucket key for `ip`: IPv4 as-is, IPv6 grouped by its /`ipv6_prefix`.        |
| [`sniff_image_type`](#heed.security.sniff_image_type)(data)                            | The media type of `data` from its magic bytes, or None if not an allowed image. |

### Classes

| [`BotVerifier`](#heed.security.BotVerifier)(\*args, \*\*kwargs)                | Verify a human-challenge token server-side; True means "looks human".        |
|-------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------|
| [`InMemoryRateLimiter`](#heed.security.InMemoryRateLimiter)(\*[, max_per_window, ...]) | Sliding-window, in-memory limiter.                                           |
| [`RateLimiter`](#heed.security.RateLimiter)(\*args, \*\*kwargs)                | Decide whether a request keyed by `key` (e.g. client IP) is allowed now.     |
| [`RedisRateLimiter`](#heed.security.RedisRateLimiter)(client, \*[, ...])            | Fixed-window limiter in Redis — one count shared by every worker and host.   |
| [`SiteverifyBotVerifier`](#heed.security.SiteverifyBotVerifier)(secret, \*[, ...])       | Turnstile / hCaptcha verification (both speak the same siteverify protocol). |

### heed.security.ATTACHMENT_RESPONSE_HEADERS *= {'Cache-Control': 'private, no-store', 'Content-Security-Policy': "default-src 'none'; sandbox", 'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff'}*

Response headers for serving a stored attachment, whatever its bytes contain:
never sniffed as another type, never allowed to run script or load anything, never
cached by shared caches.

### *class* heed.security.BotVerifier(\*args, \*\*kwargs)

Bases: [`Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)

Verify a human-challenge token server-side; True means “looks human”.

### *class* heed.security.InMemoryRateLimiter(\*, max_per_window=30, window_seconds=60.0, max_keys=100000, clock=<built-in function monotonic>)

Bases: `_WindowKnobs`, `SlidingWindowLimiter`

Sliding-window, in-memory limiter. Per process — NOT shared across workers.

Fine for a single-worker deployment; with several workers each one counts alone,
so the effective limit is multiplied. Use [`RedisRateLimiter`](#heed.security.RedisRateLimiter) there. The
number of tracked keys is bounded (`max_keys`, least-recently-seen evicted) so a
flood of distinct keys cannot exhaust memory.

### *class* heed.security.RateLimiter(\*args, \*\*kwargs)

Bases: [`Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)

Decide whether a request keyed by `key` (e.g. client IP) is allowed now.

### *class* heed.security.RedisRateLimiter(client, \*, max_per_window=30, window_seconds=60.0, namespace='heed:rl', fallback=None, max_keys=100000, clock=<built-in function time>)

Bases: `_WindowKnobs`, `RedisLimiter`

Fixed-window limiter in Redis — one count shared by every worker and host.

`client` is any redis-py compatible client (injected; tests use a fake). When
Redis errors, the request is decided by `fallback` (a per-process in-memory
limiter by default), so an outage degrades protection instead of either dropping
all feedback or removing the limit. `from_url()` builds the client from a
`redis://` URL (needs the `[redis]` extra).

After a Redis error the fallback alone decides for `retry_seconds` (5 s by
default) before Redis is tried again, so a hung Redis costs one socket timeout
per interval, not one per request; one warning per outage is logged, on the
`enlace.ratelimit` logger. An `allow`-only `fallback` is wrapped to answer
`acquire` too, so `.fallback` is then that wrapper (its `.limiter` is
yours). Counters are named `{namespace}:{window}:{key}:{window number}`.

#### install_hint *= "pip install 'heed[redis]'"*

Shown when the optional `redis` client is not installed.

### *class* heed.security.SiteverifyBotVerifier(secret, \*, verify_url='https://challenges.cloudflare.com/turnstile/v0/siteverify', expected_hostnames=None, timeout=5.0, max_token_chars=4096, http_post=<function \_urllib_post_form>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Turnstile / hCaptcha verification (both speak the same siteverify protocol).

Fails closed: a missing token, a network error, or a malformed answer all verify
as False. `expected_hostnames`, when given, also rejects tokens solved on
another site. `http_post` is injectable (tests never touch the network).

### heed.security.attachment_headers(media_type, , filename=None, cross_origin=False)

Headers for serving a stored screenshot (issue #15). Use them on every response
that returns attachment bytes, ideally from an origin separate from the app’s.

Only the image types heed accepts are served as themselves; anything else is sent as
an opaque download. Embedding is limited to the same site
(`Cross-Origin-Resource-Policy: same-site`) unless `cross_origin=True`, which a
screenshot embedded elsewhere (e.g. in a GitHub issue) needs.

```pycon
>>> h = attachment_headers("image/png", filename="r1.png")
>>> h["Content-Type"], h["X-Content-Type-Options"], h["Content-Disposition"]
('image/png', 'nosniff', 'inline; filename="r1.png"')
>>> attachment_headers("text/html")["Content-Type"]
'application/octet-stream'
```

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### heed.security.client_ip(peer, forwarded_for, , trusted_proxies=())

The client address to rate-limit on (`enlace.ratelimit.client_address()`).

`X-Forwarded-For` is attacker-controlled unless the direct peer is a trusted
proxy, so it is only consulted then — and read right-to-left, skipping trusted
hops, so a client cannot prepend a fake address. A hop that is not an address
stops the walk (the peer is used).

```pycon
>>> client_ip("10.0.0.1", "6.6.6.6, 1.2.3.4", trusted_proxies=["10.0.0.0/8"])
'1.2.3.4'
>>> client_ip("5.5.5.5", "1.2.3.4")  # untrusted peer: header ignored
'5.5.5.5'
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### heed.security.cors_headers(origin, allowed, , allow_credentials=False)

CORS response headers for `origin`, or `{}` when it is not allowed.

The request’s own origin is echoed (never `*`) so the response is correct with
or without credentials; `Vary: Origin` keeps shared caches honest.
`Retry-After` is exposed so the widget can tell a visitor when to retry.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### heed.security.make_bot_verifier(provider, secret, \*\*kwargs)

Return a verifier, or `None` (gate off) when no secret is configured.

`provider` defaults to `"turnstile"` when a secret is given.

* **Return type:**
  [`BotVerifier`](#heed.security.BotVerifier) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### heed.security.make_rate_limiter(backend='memory', , max_per_window, window_seconds=60.0, namespace='ip', redis_url=None, max_keys=100000, redis_socket_timeout=0.5)

Build a limiter from the `rate_limiter_backends` registry by name.

* **Return type:**
  [`RateLimiter`](#heed.security.RateLimiter)

### heed.security.normalize_origin(origin)

Canonical form of an origin for comparison (lower-case, no trailing slash).

```pycon
>>> normalize_origin("HTTPS://App.Example/")
'https://app.example'
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### heed.security.origin_allowed(origin, allowed)

True if origin is permitted; `allowed=None` allows any (the dev default).

```pycon
>>> origin_allowed("https://a.example", ["https://a.example"])
True
>>> origin_allowed(None, ["https://a.example"])
False
```

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### heed.security.preflight_headers(origin, allowed, , allow_credentials=False, max_age_seconds=600, methods='POST, OPTIONS', request_headers=None)

Headers answering a CORS preflight, or `{}` when the origin is not allowed.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### heed.security.rate_limit_key(ip, , ipv6_prefix=64)

The bucket key for `ip`: IPv4 as-is, IPv6 grouped by its /`ipv6_prefix`.

One IPv6 host typically owns a whole /64, so per-address buckets are trivially
dodged by rotating addresses (`enlace.ratelimit.bucket_key()`).

```pycon
>>> rate_limit_key("2001:db8::1") == rate_limit_key("2001:db8::ffff")
True
>>> rate_limit_key("1.2.3.4")
'1.2.3.4'
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### heed.security.sniff_image_type(data)

The media type of `data` from its magic bytes, or None if not an allowed image.

Never trust the client’s declared content type: an “image” might be HTML/SVG.

```pycon
>>> sniff_image_type(b"\x89PNG\r\n\x1a\n...")
'image/png'
>>> sniff_image_type(b"<svg onload=alert(1)>") is None
True
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)
