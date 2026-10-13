# heed.ingest

The ingest layer — build a Report from a submission and route it to a sink.

`process_submission` is the pure, framework-free core (dependency-injected sink,
stores, clock, id generator) so it is unit-testable without a server. `make_router`
wraps it in a FastAPI `APIRouter` you can run standalone (`make_app`) or
`include_router` into an enlace app. Identity is anonymous by default; pass a
`user_dependency` to bind identity when an auth layer (e.g. enlace_auth) is present.

The router treats every request as hostile (see `security.py` and issue #5). Checks
run cheapest-first so abuse is rejected before it costs anything: CORS preflight →
origin allow-list → per-IP rate limit (both before `user_dependency`) → body-size cap
(streamed, before parsing) → form limits → honeypot → bot gate → per-origin rate limit
→ model validation → screenshot sniffing.

#### NOTE
this module does not `from __future__ import annotations` — FastAPI resolves
route-handler annotations against module globals, so the request/response types are
imported at module level.

### Functions

| [`make_app`](#heed.ingest.make_app)(sink, \*[, config, ...])               | A standalone FastAPI app serving the heed router under `prefix`.              |
|--------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------|
| [`make_router`](#heed.ingest.make_router)(sink, \*[, config, ...])            | Build a FastAPI APIRouter exposing `POST /report` (and its CORS preflight).   |
| [`process_submission`](#heed.ingest.process_submission)(submission, sink, \*[, ...]) | Enrich `submission` into a Report, store any screenshot, and route to `sink`. |
| [`read_capped_body`](#heed.ingest.read_capped_body)(request, \*, max_bytes)        | Read the request body, or return None as soon as it exceeds `max_bytes`.      |

### heed.ingest.make_app(sink, , config=None, attachment_store=None, prefix='/heed', \*\*router_kwargs)

A standalone FastAPI app serving the heed router under `prefix`.

* **Return type:**
  `FastAPI`

### heed.ingest.make_router(sink, , config=None, attachment_store=None, rate_limiter=None, origin_rate_limiter=None, bot_verifier=None, user_dependency=None)

Build a FastAPI APIRouter exposing `POST /report` (and its CORS preflight).

Run standalone via [`make_app()`](#heed.ingest.make_app), or `include_router` it into an enlace app.
Provide `user_dependency` (a FastAPI dependency returning a user id or None) to
bind identity when an auth layer like enlace_auth is present.

Hardening defaults come from `config`; any piece can be injected instead:
`rate_limiter` (per client IP), `origin_rate_limiter` (per Origin) and
`bot_verifier` (by default built from `config.bot_secret`, i.e. off until a
secret is configured).

* **Return type:**
  `APIRouter`

### heed.ingest.process_submission(submission, sink, \*, origin='', user=None, screenshot=None, screenshot_media_type='image/png', attachment_store=None, config=None, id_gen=<function new_report_id>, clock=<function utcnow>)

Enrich `submission` into a Report, store any screenshot, and route to `sink`.

Returns the (report, sink-result) pair. The server owns id/created_at/identity/
origin/status — never the client. Opt-in capture (console/network) is dropped unless
enabled in `config`.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Report`](heed.base.md#heed.base.Report), [`SinkResult`](heed.sinks.base.md#heed.sinks.base.SinkResult)]

### *async* heed.ingest.read_capped_body(request, , max_bytes)

Read the request body, or return None as soon as it exceeds `max_bytes`.

Checks `Content-Length` first, then counts streamed bytes (a chunked request
has no length header), so an oversized body is never buffered whole.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`bytes`](https://docs.python.org/3/builtins/stdtypes.html#bytes)]
