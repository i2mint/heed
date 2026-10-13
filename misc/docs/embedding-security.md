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
11. Screenshot: size cap, then magic-byte sniffing; only PNG, JPEG and WebP are stored, under the sniffed type, whatever the client claimed.

The sink is chosen server-side; the browser never names a repository or board.

## Knobs

Every knob is a keyword of `HeedConfig` and can also come from a `HEED_<NAME>` environment variable via `HeedConfig.from_env()` (lists are comma-separated, booleans accept `1/true/yes/on`).

| Knob | Default | What it does |
|---|---|---|
| `allowed_origins` | `None` (any) | Origins allowed to submit. **Set this in production.** |
| `handle_cors` | `True` | The router answers preflight and sets CORS headers itself. Turn off if the host app already runs `CORSMiddleware`. |
| `cors_allow_credentials` | `False` | Needed only when an auth cookie binds identity (`user_dependency`). Requires an explicit `allowed_origins`. |
| `max_body_bytes` | 6 MB | Whole request. Must be at least `max_screenshot_bytes`. |
| `max_screenshot_bytes` | 5 MB | The screenshot file. |
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
