# Operating heed's public endpoint, and embedding the widget safely

The ingest endpoint (`POST /report`) is unauthenticated by nature: anyone can POST to it. heed treats every submission as untrusted user-generated content. This page lists the hardening knobs for whoever runs the backend, and the Content-Security-Policy guidance for whoever embeds the widget. Design rationale: [`design.md`](design.md) §8; tracking issue: [i2mint/heed#5](https://github.com/i2mint/heed/issues/5).

## Check order

The router rejects abuse cheapest-first, so a flood costs as little as possible:

1. CORS preflight (`OPTIONS /report`) answered from the origin allow-list.
2. Origin allow-list: a missing or unlisted `Origin` gets `403`.
3. Per-IP rate limit (IPv6 grouped by /64); over the limit gets `429` with `Retry-After`. Steps 2–3 run as a route dependency, before any `user_dependency` and before the body is read.
4. Content type must be `multipart/form-data` or `application/x-www-form-urlencoded` (`415` otherwise).
5. Body-size cap, enforced on `Content-Length` and again while streaming (`413`); the body is never buffered past the cap.
6. Form limits: at most one file (`screenshot`), a bounded number of fields, and a per-field byte cap.
7. Honeypot: a hidden field humans leave empty. A filled one gets a fake success and is routed nowhere.
8. Bot gate (only when a secret is configured): Turnstile or hCaptcha token verified server-side; fails closed.
9. Per-origin rate limit. It runs after the honeypot and the bot gate on purpose: `Origin` is a header any non-browser client can set, so counting earlier would let spoofed junk exhaust a real site's budget. With the bot gate off, a determined attacker spread over many IPs can still do that; turn the gate on if it happens.
10. Model validation (`heed/base.py`): length limits on every string and list, `page_url` must be an absolute `http(s)` URL, control and bidi-override characters are stripped, `anon_id` is a short opaque token, `env` must be a JSON object. Errors never echo the submitted input back.
11. Screenshot: size cap, then magic-byte sniffing; only PNG, JPEG and WebP are accepted, under the sniffed type, whatever the client claimed.
12. Screenshot sanitizing (`heed/images.py`): the accepted bytes are rewritten before storage. Trailing data after the image (the classic polyglot), metadata (EXIF/XMP with GPS, ICC, text chunks, comments) and animation are dropped; with Pillow (the default when installed) the pixels are also decoded and freshly re-encoded. Malformed or costly files (a header only, unknown JPEG markers, more than 32 progressive scans, a canvas over `max_screenshot_pixels`) get `415`; the sanitized file is checked against `max_screenshot_bytes` again (`413`); when every re-encode slot is busy the request gets `503` with `Retry-After`. See "Stored screenshots" below.

The sink is chosen server-side; the browser never names a repository or board (see *Routing and moderation* below).

## Routing and moderation

**Site resolution.** One backend can serve several sites. Pass `resolve_sink` to `make_router`: it maps the validated `Origin` to that site's sink, and an origin that resolves to no sink is refused with the same `403` as a disallowed origin (so pages cannot probe which sites are served) before the body is read. `sinks_by_origin({"https://a.example": sink_a, ...}, default=None)` builds one from a mapping. Form fields such as `repo` or `target` are ignored: the browser cannot steer a report into another tracker.

**Moderation / holding.** Pass `moderator` (a function `Report -> Verdict`) together with `holding_sink`. `accept` routes to the site's sink, `hold` parks the report in the holding sink with status `held` until someone calls `release_held(report_id, holding_store=..., sink=...)`, and `reject` drops it while the submitter sees an ordinary success. Built-ins: `accept_all` (the default), `hold_all` (full pre-moderation), `make_heuristic_moderator(max_links=3, blocked_terms=[...])`, and `chain_moderators(...)` (the most severe verdict wins). The response carries `"status": "held"` for a held report so the widget can say it awaits review. A moderator that raises or returns an invalid value holds the report rather than failing the request. Release with `sink=` (single site) or `resolve_sink=` (routes by the report's own origin); the report is claimed before submitting and put back if the sink fails. Claiming moves it (to a per-release `claimed--<id>--<token>` key of the holding store, or to `claimed_store=` if given, a store used for nothing else) before deleting it from holding, and a release only ever deletes its own claim, so a crash mid-release leaves it held, claimed or both, never lost. A report with a claim is not released again until `recover_claimed(holding_store=...)` runs; call it at startup (never while a release runs) to return stranded claims to holding. A crash after the sink accepted can make the report be sent twice, never zero times. Two releases of the same report running at the same moment can also send it twice: claiming is a copy and a delete on a plain mapping, not an atomic move ([#24](https://github.com/i2mint/heed/issues/24)). To delete a held report for good, use `discard_held(report_id, holding_store=..., attachment_store=...)`, which removes its screenshot too; `del holding_store[id]` leaves the screenshot behind. Note that holding and rejecting are a filter, not a secret: a determined spammer can tell a held or rejected reply from an accepted one (`status`, a missing `ref`), and the heuristic folds fullwidth and zero-width tricks but not cross-script look-alikes.

## Knobs

Every knob is a keyword of `HeedConfig` and can also come from a `HEED_<NAME>` environment variable via `HeedConfig.from_env()` (lists are comma-separated, booleans accept `1/true/yes/on`).

| Knob | Default | What it does |
|---|---|---|
| `allowed_origins` | `None` (any) | Origins allowed to submit. **Set this in production.** |
| `handle_cors` | `True` | The router answers preflight and sets CORS headers itself. Turn off if the host app already runs `CORSMiddleware`. |
| `cors_allow_credentials` | `False` | Needed only when an auth cookie binds identity (`user_dependency`). Requires an explicit `allowed_origins`. |
| `max_body_bytes` | 6 MB | Whole request. Must be at least `max_screenshot_bytes`. |
| `max_screenshot_bytes` | 5 MB | The screenshot file. |
| `screenshot_sanitizer` | `"auto"` | `"reencode"` (strip, then decode and re-save with Pillow, `pip install heed[images]`), `"strip"` (pure Python: allow-listed, length-checked chunks only, truncated at the end marker; the compressed pixel data and the JPEG quantisation and Huffman tables are kept as sent, so arbitrary bytes can survive in them: use `"reencode"` where a stored screenshot might be served to a browser), `"auto"` (re-encode if Pillow is installed, else strip with a startup warning), `"none"` (store as received). |
| `max_screenshot_pixels` | 25 M | Re-encode refuses larger canvases before decoding (a 1.5 KB WebP can declare 40 M pixels and need 700 MB to decode). |
| `max_concurrent_reencodes` | 2 | Re-encodes in flight per process; a request that waits more than 2 s for a slot gets `503`. |
| `max_field_bytes` | 100 KB | Any single text field. |
| `max_form_fields` | 16 | Number of form parts. |
| `rate_limit_per_minute` | 30 | Per client IP. |
| `origin_rate_limit_per_minute` | 600 | Per origin (all visitors of one site); `None` disables. |
| `rate_limit_backend` | `"memory"` | `"memory"` (per process) or `"redis"` (shared). |
| `redis_url` | `None` | Required for the Redis backend, e.g. `redis://localhost:6379/0`. |
| `ipv6_rate_limit_prefix` | `64` | IPv6 clients share one per-IP bucket per prefix. |
| `trusted_proxies` | `[]` | IPs/CIDRs of your reverse proxy. Only then is `X-Forwarded-For` used for the client IP. |
| `bot_gate` | `None` | `"turnstile"` or `"hcaptcha"`; with a secret and no provider, Turnstile is assumed. |
| `bot_secret` | `None` | The provider's **secret** key. The gate is off until it is set. Keep it in the environment, never in code. |
| `bot_expected_hostnames` | `None` | Hostnames a challenge must have been solved on; rejects tokens farmed on another site. |
| `bot_token_field` | `"bot_token"` | Form field carrying the widget's challenge token. |
| `honeypot_field` | `"hp_website"` | Hidden trap field; `None` disables. |

Every piece is also injectable on `make_router(...)`: `rate_limiter`, `origin_rate_limiter` (anything with `allow(key) -> bool`) and `bot_verifier` (anything with `verify(token, *, remote_ip=None) -> bool`). New rate-limit backends register in `heed.security.rate_limiter_backends`, new bot providers in `heed.security.bot_verifier_providers`.

## Production checklist

- Set `allowed_origins` to the exact origins of the sites that embed the widget.
- Behind Caddy or nginx, set `trusted_proxies` to the proxy address (often `127.0.0.1`). Without it every visitor shares the proxy's IP, and one rate-limit bucket.
- With more than one worker process or host, use `rate_limit_backend="redis"`. The in-memory limiter counts per process, so the effective limit is multiplied by the worker count. If Redis becomes unreachable (0.5 s socket timeout), the Redis limiter falls back to a per-process in-memory limit rather than dropping all feedback or removing the limit.
- Turn the bot gate on (`bot_secret`) once spam appears, or from the start on a high-traffic site. Set `bot_expected_hostnames` to reject tokens solved on another site.
- Keep a reverse-proxy body limit too (e.g. Caddy `request_body { max_size 6MB }`) as a second line.

## Stored screenshots

Sanitizing reduces what a stored screenshot can carry, but neither rewrite makes the bytes safe to serve on their own: even a re-encoded pixel stream can contain attacker-chosen byte runs. `heed.make_attachment_app` / `make_attachment_router` serve them (see *Serving screenshots* below). Anything else that serves them must send every response with `heed.attachment_headers(media_type, filename=...)`:

- `Content-Type` is the stored, sniffed image type; anything else is sent as `application/octet-stream` with `Content-Disposition: attachment`.
- `X-Content-Type-Options: nosniff`, so a browser never reinterprets the bytes.
- `Content-Security-Policy: default-src 'none'; sandbox`, so even a document rendering runs no script and loads nothing.
- `Referrer-Policy: no-referrer`, `Cache-Control: private, no-store`.
- `Cross-Origin-Resource-Policy: same-site`, or `cross-origin` with `cross_origin=True`, which embedding as an `<img>` on another site (a GitHub issue) needs. Only opt in for attachments that are meant to be public.

Serve attachments from an origin separate from the app's (a dedicated subdomain), so a rendering mistake cannot reach the app's cookies or storage.

### Serving screenshots

```python
from heed import AttachmentSigner, GitHubIssuesSink, make_attachment_app, screenshot_url_resolver

signer = AttachmentSigner(key)  # or set HEED_ATTACHMENT_SIGNING_KEY and pass config=
files = make_attachment_app(attachment_store, signer=signer,
                            allowed_hosts=["files.example.com"], cross_origin=True)
sink = GitHubIssuesSink(..., screenshot_url=screenshot_url_resolver(
    "https://files.example.com/attachments", signer))
```

- **Signed URLs by default.** Report ids are not secret: the submit response, the report JSON, logs and holding stores carry them. The route serves a ref only with an HMAC-SHA256 signature the backend made (`HEED_ATTACHMENT_SIGNING_KEY`, at least 32 bytes), so only screenshots a sink chose to link are reachable, and held or rejected reports' screenshots have no URL. `allow_unsigned=True` turns this off, for stores whose every screenshot may be public.
- **Expiry is optional** (`HEED_ATTACHMENT_URL_TTL_SECONDS`). Without it a GitHub issue keeps its image; revoke by deleting the attachment or rotating the key (`previous_keys=` keeps old URLs valid during a rotation).
- **A dedicated origin.** Run `make_attachment_app` on its own subdomain and set `allowed_hosts` (`HEED_ATTACHMENT_HOSTS`); any other `Host` gets 421. The app has no docs or OpenAPI routes.
- **Nothing but heed's refs.** Only `{32 hex}.{png,jpg,webp}` reaches the store; the bytes are re-sniffed and anything that is not the named image is sent as an opaque download. A missing, malformed, unsigned or expired ref is the same bare 404; other paths and methods get a bare status, never framework JSON or a slash redirect.
- **Re-encode where you serve.** With `screenshot_sanitizer="strip"` (no Pillow) compressed pixel data is stored as sent; the route logs a warning when Pillow is missing. The headers above keep such bytes inert in a browser either way.

## CSP guidance for embedders

A site that embeds the widget under a Content-Security-Policy must allow three things: loading the widget script, the widget's `POST` to your heed backend, and (only with the bot gate on) the challenge provider. With the widget served from `https://feedback.example` and the backend at `https://feedback.example/heed`:

```
Content-Security-Policy:
  script-src 'self' https://feedback.example;
  connect-src 'self' https://feedback.example;
  img-src 'self' data: blob:;
```

- `script-src` must list the origin serving the widget bundle. Prefer pinning a versioned URL with Subresource Integrity (`<script src="…/heed-1.2.3.js" integrity="sha384-…" crossorigin="anonymous">`) over allowing a whole CDN.
- `connect-src` must list the heed backend origin; that is where the report is POSTed.
- `img-src data: blob:` lets the widget preview the screenshot it captured.
- The widget ([i2mint/heed#6](https://github.com/i2mint/heed/issues/6)) is required to render in a Shadow DOM with constructed stylesheets, so that it needs no `'unsafe-inline'` in `style-src` and no `'unsafe-eval'`; treat any widget build that needs either as a bug.

With the bot gate on, add the provider:

| Provider | `script-src` | `frame-src` | `connect-src` |
|---|---|---|---|
| Cloudflare Turnstile | `https://challenges.cloudflare.com` | `https://challenges.cloudflare.com` | — |
| hCaptcha | `https://hcaptcha.com https://*.hcaptcha.com` | `https://hcaptcha.com https://*.hcaptcha.com` | `https://hcaptcha.com https://*.hcaptcha.com` |

The heed backend's own responses are JSON only and already carry `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`; adding `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` on the `/heed` routes at the proxy costs nothing. Screenshots served back (for example to GitHub issues) should be sent with their stored media type and `X-Content-Type-Options: nosniff`.
