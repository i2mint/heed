# heed

heed — embeddable, no-install, framework-agnostic end-user feedback.

`heed` lets any visitor of a deployed web app report a bug or request a feature, with
useful context gathered automatically, routed to a pluggable backend (GitHub Issues
first). It works standalone and integrates with `enlace` as an optional add-on,
without depending on it.

Phase 1 ships the backend core and the embeddable widget (`widget/`, served by
[`make_widget_router()`](#heed.make_widget_router)). The competitive
landscape and design rationale live in `misc/docs/`.

Public API (Phase 1):
: Models    Report, ReportSubmission, Identity, Environment, Category, Status,
  : Attachment, LogEntry, NetEntry
  <br/>
  Sinks     Sink, BaseSink, SinkResult, StoreSink, GitHubIssuesSink,
  : github_sink_from_token
  <br/>
  Backend   process_submission, make_router, make_app, HeedConfig
  Hardening RateLimiter, InMemoryRateLimiter, RedisRateLimiter, make_rate_limiter,
  <br/>
  > BotVerifier, SiteverifyBotVerifier, make_bot_verifier,
  > make_image_sanitizer, strip_image, reencode_image, attachment_headers
  > (see misc/docs/embedding-security.md)
  <br/>
  Routing   sinks_by_origin, Verdict, accept_all, hold_all,
  : make_heuristic_moderator, chain_moderators, release_held,
    recover_claimed, discard_held
  <br/>
  Storage   make_report_store, make_holding_store, make_attachment_store
  Serving   make_attachment_router, make_attachment_app, AttachmentSigner,
  <br/>
  > screenshot_url_resolver (a separate origin for screenshots, #19)
  <br/>
  Widget    make_widget_router (serves the built embeddable widget),
  : widget_contract (what the widget sends; SSOT for widget/)

### Example

```pycon
>>> from heed import StoreSink, ReportSubmission, process_submission
>>> store = {}
>>> report, result = process_submission(
...     ReportSubmission(title="Login button does nothing"), StoreSink(store)
... )
>>> result.ok and report.id in store
True
```

### Functions

| [`make_attachment_app`](#heed.make_attachment_app)(attachment_store, ...)         | A standalone app serving only the attachment route (no docs, no OpenAPI): run it on the dedicated attachment origin.                |
|-----------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------|
| [`make_attachment_router`](#heed.make_attachment_router)(attachment_store, \*)       | A router serving `GET {path}/{ref}` from `attachment_store`, read-only.                                                             |
| [`make_widget_router`](#heed.make_widget_router)(\*[, asset_dir])                | A router serving the built widget at `GET /widget/{name}`.                                                                          |
| [`widget_contract`](#heed.widget_contract)([config])                          | What the widget sends and the limits it must respect (from heed's SSOT).                                                            |
| [`screenshot_url_resolver`](#heed.screenshot_url_resolver)(base_url, signer, \*)      | A `GitHubIssuesSink(screenshot_url=...)` callable: the report's first image attachment as a (signed) URL under `base_url`, or None. |
| [`new_report_id`](#heed.new_report_id)()                                    | Return a fresh opaque report id (uuid4 hex).                                                                                        |
| [`github_sink_from_token`](#heed.github_sink_from_token)(repo, token, \*\*kwargs)    | Build a GitHubIssuesSink that creates issues in `repo` ('owner/name').                                                              |
| [`roadmap`](#heed.roadmap)(items)                                     | Items still to come, started first; most-voted first within each stage.                                                             |
| [`changelog`](#heed.changelog)(items)                                   | Shipped items, newest first (undated ones last).                                                                                    |
| [`process_submission`](#heed.process_submission)(submission, sink, \*[, ...])    | Enrich `submission` into a Report, store any screenshot, and route to `sink`.                                                       |
| [`make_router`](#heed.make_router)(sink, \*[, config, ...])               | Build a FastAPI APIRouter exposing `POST /report` (and its CORS preflight).                                                         |
| [`make_app`](#heed.make_app)(sink, \*[, config, ...])                  | A standalone FastAPI app serving the heed router under `prefix`.                                                                    |
| [`make_rate_limiter`](#heed.make_rate_limiter)([backend, window_seconds, ...])  | Build a limiter from the `rate_limiter_backends` registry by name.                                                                  |
| [`make_bot_verifier`](#heed.make_bot_verifier)(provider, secret, \*\*kwargs)    | Return a verifier, or `None` (gate off) when no secret is configured.                                                               |
| [`make_image_sanitizer`](#heed.make_image_sanitizer)([name, max_pixels, ...])      | Build a sanitizer from the `image_sanitizers` registry by name.                                                                     |
| [`strip_image`](#heed.strip_image)(data, media_type)                      | Keep only what renders the image; truncate at its end.                                                                              |
| [`reencode_image`](#heed.reencode_image)(data, media_type, \*[, max_pixels]) | Strip `data`, then decode it and save its first frame afresh, same format.                                                          |
| [`attachment_headers`](#heed.attachment_headers)(media_type, \*[, ...])          | Headers for serving a stored screenshot (issue #15).                                                                                |
| [`sinks_by_origin`](#heed.sinks_by_origin)(sinks, \*[, default])              | A resolver from an `{origin: sink}` mapping (origins compared normalized).                                                          |
| [`accept_all`](#heed.accept_all)(report)                                 | The default moderator: everything goes straight to the sink.                                                                        |
| [`hold_all`](#heed.hold_all)(report)                                   | Full pre-moderation: every report waits for a human.                                                                                |
| [`make_heuristic_moderator`](#heed.make_heuristic_moderator)(\*[, max_links, ...])     | Hold reports with more than `max_links` links or any of `blocked_terms`.                                                            |
| [`chain_moderators`](#heed.chain_moderators)(\*moderators)                     | Combine moderators; the most severe verdict wins (reject > hold > accept).                                                          |
| [`release_held`](#heed.release_held)(report_id, \*, holding_store[, ...])  | Release a held report: route it on and remove it from holding.                                                                      |
| [`recover_claimed`](#heed.recover_claimed)(\*, holding_store[, ...])          | Return every claimed report to holding, and the ids returned.                                                                       |
| [`discard_held`](#heed.discard_held)(report_id, \*, holding_store[, ...])  | Delete a held report for good, with its attachments (e.g. a screenshot).                                                            |
| [`make_report_store`](#heed.make_report_store)([rootdir, delete_func])          | A text store for report JSON.                                                                                                       |
| [`make_attachment_store`](#heed.make_attachment_store)([rootdir, ...])              | A bytes store for attachments.                                                                                                      |
| [`make_holding_store`](#heed.make_holding_store)([rootdir, delete_func])         | A report store for held reports, with a dedicated claims store beside it.                                                           |

### Classes

| [`AttachmentSigner`](#heed.AttachmentSigner)(key, \*[, previous_keys, ...])   | Mint and check signed attachment URLs (HMAC-SHA256 over the ref and expiry).   |
|----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------|
| [`Report`](#heed.Report)(\*[, id, created_at, category, body, ...]) | A server-enriched report — the unit a sink receives.                           |
| [`ReportSubmission`](#heed.ReportSubmission)(\*[, category, body, ...])       | The (untrusted) payload a widget POSTs; server fields are assigned later.      |
| [`Identity`](#heed.Identity)(\*, anon_id[, user])                     | Who submitted the report — anonymous by default.                               |
| [`Environment`](#heed.Environment)(\*[, user_agent, browser, os, ...])   | Client environment captured by default (no PII beyond the user agent).         |
| [`Category`](#heed.Category)(\*values)                                | What kind of feedback a report is.                                             |
| [`Status`](#heed.Status)(\*values)                                  | Lifecycle of a report (flattened; see misc/docs/design.md for the full map).   |
| [`Attachment`](#heed.Attachment)(\*[, kind, media_type, size])          | A stored binary artifact (e.g. a screenshot), referenced by store key.         |
| [`LogEntry`](#heed.LogEntry)(\*, level, message[, at, source])        | One captured console entry (opt-in capture only).                              |
| [`NetEntry`](#heed.NetEntry)(\*, method, url[, status, ...])          | One captured network entry (opt-in; metadata only, never bodies).              |
| [`Sink`](#heed.Sink)(\*args, \*\*kwargs)                          | Anything that can receive a report.                                            |
| [`BaseSink`](#heed.BaseSink)()                                        | Convenience base giving no-op defaults for the optional sink methods.          |
| [`SinkResult`](#heed.SinkResult)(\*, ok[, external_ref, url, detail])   | Outcome of routing a report to a sink.                                         |
| [`StoreSink`](#heed.StoreSink)(store)                                  | Persist each report as JSON into a dol-style store keyed by report id.         |
| [`GitHubIssuesSink`](#heed.GitHubIssuesSink)(create_issue, \*[, ...])         | Route a report to a GitHub issue via an injected `create_issue` callable.      |
| [`CategorySink`](#heed.CategorySink)(sinks, \*, default)                  | Route by `report.category`; unlisted categories go to `default`.               |
| [`CategoryTreatment`](#heed.CategoryTreatment)([labels, title_prefix, detail]) | What one category of report becomes as a GitHub issue.                         |
| [`PublicItem`](#heed.PublicItem)(\*, ref, title[, category, ...])       | One board entry: the public projection of a stored report.                     |
| [`HeedConfig`](#heed.HeedConfig)(\*[, allowed_origins, ...])            | Knobs for the ingest endpoint.                                                 |
| [`RateLimiter`](#heed.RateLimiter)(\*args, \*\*kwargs)                   | Decide whether a request keyed by `key` (e.g. client IP) is allowed now.       |
| [`InMemoryRateLimiter`](#heed.InMemoryRateLimiter)(\*[, max_per_window, ...])    | Sliding-window, in-memory limiter.                                             |
| [`RedisRateLimiter`](#heed.RedisRateLimiter)(client, \*[, ...])               | Fixed-window limiter in Redis — one count shared by every worker and host.     |
| [`BotVerifier`](#heed.BotVerifier)(\*args, \*\*kwargs)                   | Verify a human-challenge token server-side; True means "looks human".          |
| [`SiteverifyBotVerifier`](#heed.SiteverifyBotVerifier)(secret, \*[, ...])          | Turnstile / hCaptcha verification (both speak the same siteverify protocol).   |
| [`Verdict`](#heed.Verdict)(\*values)                                 | A moderator's decision about one report.                                       |

### *class* heed.Attachment(, kind='screenshot', media_type='image/png', ref, size=None)

Bases: `BaseModel`

A stored binary artifact (e.g. a screenshot), referenced by store key.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.AttachmentSigner(key, \*, previous_keys=(), ttl_seconds=None, clock=<built-in function time>)

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
  [`AttachmentSigner`](heed.serving.html.md#heed.serving.AttachmentSigner) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

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

### *class* heed.BaseSink

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Convenience base giving no-op defaults for the optional sink methods.

#### acknowledge(report)

Hook called after a successful submit (default: no-op).

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### find_duplicates(report)

Return likely duplicates of `report` (default: none).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`DupCandidate`](heed.sinks.base.html.md#heed.sinks.base.DupCandidate)]

#### public_items(, category=None)

The items a public board may show (default: none; see [`heed.board`](heed.board.html.md#module-heed.board)).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`PublicItem`](heed.board.html.md#heed.board.PublicItem)]

### *class* heed.BotVerifier(\*args, \*\*kwargs)

Bases: [`Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)

Verify a human-challenge token server-side; True means “looks human”.

### *class* heed.Category(\*values)

Bases: [`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Enum`](https://docs.python.org/3/library/enum.html#enum.Enum)

What kind of feedback a report is.

### *class* heed.CategorySink(sinks, , default)

Bases: [`BaseSink`](heed.sinks.base.html.md#heed.sinks.base.BaseSink)

Route by `report.category`; unlisted categories go to `default`.

#### acknowledge(report)

Hook called after a successful submit (default: no-op).

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### find_duplicates(report)

Return likely duplicates of `report` (default: none).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`DupCandidate`](heed.sinks.base.html.md#heed.sinks.base.DupCandidate)]

#### public_items(, category=None)

Every distinct member sink’s public items (a shared link once).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`PublicItem`](heed.board.html.md#heed.board.PublicItem)]

#### sink_for(category)

The sink that receives reports of `category`.

* **Return type:**
  [`Sink`](heed.sinks.base.html.md#heed.sinks.base.Sink)

#### sinks()

Every distinct member sink, including the default.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Sink`](heed.sinks.base.html.md#heed.sinks.base.Sink)]

### *class* heed.CategoryTreatment(labels=None, title_prefix='', detail=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

What one category of report becomes as a GitHub issue.

`detail="light"` is the feature-request intake: the idea and who asked, without
the browser/console/network dump or a screenshot, which say nothing about a wish.

### *class* heed.Environment(\*, user_agent=None, browser=None, os=None, viewport=None, locale=None, device_pixel_ratio=None, extra=<factory>)

Bases: `BaseModel`

Client environment captured by default (no PII beyond the user agent).

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.GitHubIssuesSink(create_issue, , label_map=None, screenshot_url=None, treatments=None, list_issues=None, public_label='public', status_labels=None)

Bases: [`BaseSink`](heed.sinks.base.html.md#heed.sinks.base.BaseSink)

Route a report to a GitHub issue via an injected `create_issue` callable.

`treatments` says, per category, the labels, a title prefix and how much context
the issue carries (`label_map` is the older labels-only spelling and still wins
for labels). With `list_issues` it can also read its issues back as a board’s
[`PublicItem`](heed.board.html.md#heed.board.PublicItem) entries: only those carrying `public_label`, with
status from `status_labels` (or the issue’s closed state) and votes from the 👍
reactions.

#### public_items(, category=None)

The board’s items: issues labelled `public_label`, as [`PublicItem`](#heed.PublicItem).

Empty without `list_issues`. Only title, category, status, link, votes and
dates leave the sink: never an issue body (which holds the reporter’s
environment and identity).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`PublicItem`](heed.board.html.md#heed.board.PublicItem)]

#### treatment(category)

The treatment applied to reports of `category`.

* **Return type:**
  [`CategoryTreatment`](heed.sinks.github.html.md#heed.sinks.github.CategoryTreatment)

### *class* heed.HeedConfig(\*, allowed_origins=None, handle_cors=True, cors_allow_credentials=False, cors_max_age_seconds=600, max_body_bytes=6000000, max_screenshot_bytes=5000000, screenshot_sanitizer='auto', max_screenshot_pixels=25000000, max_concurrent_reencodes=2, max_field_bytes=100000, max_form_fields=16, rate_limit_per_minute=30, origin_rate_limit_per_minute=600, rate_limit_window_seconds=60.0, rate_limit_backend='memory', redis_url=None, trusted_proxies=list(), ipv6_rate_limit_prefix=64, bot_gate=None, bot_secret=None, bot_expected_hostnames=None, bot_token_field='bot_token', honeypot_field='hp_website', attachment_signing_key=None, attachment_url_ttl_seconds=None, attachment_previous_keys=list(), attachment_hosts=None, attachment_cache_control=None, accept_console=False, accept_network=False, light_categories=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Knobs for the ingest endpoint. All optional with sensible defaults.

#### *property* bot_gate_enabled *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

True when a bot-gate secret is configured.

#### *classmethod* from_env(environ=None, \*\*overrides)

Build a config from `HEED_<FIELD>` variables, then apply `overrides`.

Lists are comma-separated; booleans accept 1/true/yes/on; `none` unsets an
optional knob (e.g. `HEED_ORIGIN_RATE_LIMIT_PER_MINUTE=none`).

```pycon
>>> cfg = HeedConfig.from_env(
...     {"HEED_ALLOWED_ORIGINS": "https://a.example, https://b.example",
...      "HEED_RATE_LIMIT_PER_MINUTE": "10"}
... )
>>> cfg.allowed_origins, cfg.rate_limit_per_minute
(['https://a.example', 'https://b.example'], 10)
```

* **Return type:**
  [`HeedConfig`](heed.config.html.md#heed.config.HeedConfig)

### *class* heed.Identity(, anon_id, user=None)

Bases: `BaseModel`

Who submitted the report — anonymous by default.

`anon_id` is an opaque random id (the default). `user` is set only when an
authenticated session is present (e.g. via enlace_auth). The client IP is NEVER
stored here — it is used transiently for rate limiting only.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.InMemoryRateLimiter(\*, max_per_window=30, window_seconds=60.0, max_keys=100000, clock=<built-in function monotonic>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Sliding-window, in-memory limiter. Per process — NOT shared across workers.

Fine for a single-worker deployment; with several workers each one counts alone,
so the effective limit is multiplied. Use [`RedisRateLimiter`](#heed.RedisRateLimiter) there. The
number of tracked keys is bounded (`max_keys`, least-recently-seen evicted) so a
flood of distinct keys cannot exhaust memory.

### *class* heed.LogEntry(, level, message, at=None, source=None)

Bases: `BaseModel`

One captured console entry (opt-in capture only).

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.NetEntry(, method, url, status=None, duration_ms=None, ok=None)

Bases: `BaseModel`

One captured network entry (opt-in; metadata only, never bodies).

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.PublicItem(, ref, title, category=Category.feature, status=Status.received, url=None, votes=0, created_at=None, closed_at=None)

Bases: `BaseModel`

One board entry: the public projection of a stored report.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.RateLimiter(\*args, \*\*kwargs)

Bases: [`Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)

Decide whether a request keyed by `key` (e.g. client IP) is allowed now.

### *class* heed.RedisRateLimiter(client, \*, max_per_window=30, window_seconds=60.0, namespace='heed:rl', fallback=None, clock=<built-in function time>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Fixed-window limiter in Redis — one count shared by every worker and host.

`client` is any redis-py compatible client (injected; tests use a fake). When
Redis errors, the request is decided by `fallback` (a per-process in-memory
limiter by default), so an outage degrades protection instead of either dropping
all feedback or removing the limit.

#### *classmethod* from_url(url, , socket_timeout=0.5, \*\*kwargs)

Build from a `redis://` URL (needs the `[redis]` extra).

`socket_timeout` bounds both connect and reads, so a hung Redis costs at most
that long before the fallback limiter decides.

* **Return type:**
  [`RedisRateLimiter`](heed.security.html.md#heed.security.RedisRateLimiter)

### *class* heed.Report(\*, id=<factory>, created_at=<factory>, category=Category.bug, title, body='', page_url='', env=<factory>, attachments=<factory>, console=None, network=None, identity, origin='', status=Status.received, labels=<factory>, extra=<factory>)

Bases: `BaseModel`

A server-enriched report — the unit a sink receives.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.ReportSubmission(\*, category=Category.bug, title, body='', page_url='', env=<factory>, console=None, network=None, anon_id=None)

Bases: `BaseModel`

The (untrusted) payload a widget POSTs; server fields are assigned later.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.Sink(\*args, \*\*kwargs)

Bases: [`Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)

Anything that can receive a report. `submit` is the only required method.

### *class* heed.SinkResult(, ok, external_ref=None, url=None, detail=None)

Bases: `BaseModel`

Outcome of routing a report to a sink.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.SiteverifyBotVerifier(secret, \*, verify_url='https://challenges.cloudflare.com/turnstile/v0/siteverify', expected_hostnames=None, timeout=5.0, max_token_chars=4096, http_post=<function \_urllib_post_form>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Turnstile / hCaptcha verification (both speak the same siteverify protocol).

Fails closed: a missing token, a network error, or a malformed answer all verify
as False. `expected_hostnames`, when given, also rejects tokens solved on
another site. `http_post` is injectable (tests never touch the network).

### *class* heed.Status(\*values)

Bases: [`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Enum`](https://docs.python.org/3/library/enum.html#enum.Enum)

Lifecycle of a report (flattened; see misc/docs/design.md for the full map).

### *class* heed.StoreSink(store)

Bases: [`BaseSink`](heed.sinks.base.html.md#heed.sinks.base.BaseSink)

Persist each report as JSON into a dol-style store keyed by report id.

The zero-configuration default when no external sink (e.g. GitHub) is configured.
`store` is any `MutableMapping[str, str]` (a plain dict, a `dol` file store,
S3, …) — dependency-injected so the sink stays testable.

#### find_duplicates(report)

Cheap title-equality duplicate scan over stored reports.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`DupCandidate`](heed.sinks.base.html.md#heed.sinks.base.DupCandidate)]

### *class* heed.Verdict(\*values)

Bases: [`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Enum`](https://docs.python.org/3/library/enum.html#enum.Enum)

A moderator’s decision about one report.

### heed.accept_all(report)

The default moderator: everything goes straight to the sink.

* **Return type:**
  [`Verdict`](heed.routing.html.md#heed.routing.Verdict)

### heed.attachment_headers(media_type, , filename=None, cross_origin=False)

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

### heed.chain_moderators(\*moderators)

Combine moderators; the most severe verdict wins (reject > hold > accept).

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[`Report`](heed.base.html.md#heed.base.Report)], [`Verdict`](heed.routing.html.md#heed.routing.Verdict)]

### heed.changelog(items)

Shipped items, newest first (undated ones last).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`PublicItem`](heed.board.html.md#heed.board.PublicItem)]

### heed.discard_held(report_id, , holding_store, attachment_store=None)

Delete a held report for good, with its attachments (e.g. a screenshot).

The report leaves holding first, so a release that took it meanwhile wins and this
raises `KeyError` before touching its attachments; a crash between the two steps
leaves an orphaned screenshot, never a report pointing at a deleted one. Raises
`KeyError` when `report_id` is not held. A report whose text no longer parses is
still deleted, but its attachments cannot be named and are left. Run it serialised
with [`release_held()`](#heed.release_held) for the same report.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### heed.github_sink_from_token(repo, token, \*\*kwargs)

Build a GitHubIssuesSink that creates issues in `repo` (‘owner/name’).

Requires the `heed[github]` extra. `token` is a server-side credential (a GitHub
App installation token or a PAT) — never shipped to the browser.

* **Return type:**
  [`GitHubIssuesSink`](heed.sinks.github.html.md#heed.sinks.github.GitHubIssuesSink)

### heed.hold_all(report)

Full pre-moderation: every report waits for a human.

* **Return type:**
  [`Verdict`](heed.routing.html.md#heed.routing.Verdict)

### heed.make_app(sink, , config=None, attachment_store=None, prefix='/heed', serve_widget=True, \*\*router_kwargs)

A standalone FastAPI app serving the heed router under `prefix`.

With `serve_widget` (the default) it also serves the built widget at
`{prefix}/widget/{file}` (see [`heed.widget`](heed.widget.html.md#module-heed.widget)), so one host both hands out the
script and receives its reports.

* **Return type:**
  `FastAPI`

### heed.make_attachment_app(attachment_store, \*\*router_kwargs)

A standalone app serving only the attachment route (no docs, no OpenAPI): run it
on the dedicated attachment origin. Keyword arguments go to
`make_attachment_router`.

* **Return type:**
  `FastAPI`

### heed.make_attachment_router(attachment_store, , config=None, signer=None, allow_unsigned=False, allowed_hosts=None, cross_origin=False, cache_control=None, max_bytes=None, path='/attachments', ref_pattern=re.compile('[0-9a-f]{32}\\\\.(?:png|jpg|webp)'))

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
  * **signer** ([`AttachmentSigner`](heed.serving.html.md#heed.serving.AttachmentSigner) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Required unless `allow_unsigned`: report ids are not secret.
  * **allow_unsigned** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Serve any ref without a signature (a ref is then the
    capability). Only for stores whose every screenshot may be public.
  * **allowed_hosts** ([`Iterable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Iterable)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)] | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Hostnames this route answers on: the dedicated attachment origin.
    Required (here or as `config.attachment_hosts`): `ANY_HOST` says any
    host is fine, which is only right when the route has no origin of its own.
  * **cross_origin** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – `Cross-Origin-Resource-Policy: cross-origin`, which an image
    embedded in a GitHub issue needs (GitHub’s camo proxy fetches it).
  * **cache_control** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Replaces the `Cache-Control` of a served image (default
    `private, no-store`), e.g. `public, max-age=300` if GitHub’s camo proxy
    should cache signed, cross-origin images. Defaults to
    `config.attachment_cache_control`.
  * **max_bytes** ([`int`](https://docs.python.org/3/builtins/functions.html#int) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Stored values larger than this are not served (a bare 404 and a
    warning). Defaults to `config.max_screenshot_bytes`. It does not stop a
    store from reading the value into memory; it keeps serving from relying on
    the ingest cap alone.
  * **ref_pattern** ([`Pattern`](https://docs.python.org/3/library/re.html#re.Pattern)) – Refs to serve (full match); the default is what heed writes.
* **Return type:**
  `APIRouter`

### heed.make_attachment_store(rootdir=None, \*, delete_func=<built-in function remove>, follow_symlinks=False)

A bytes store for attachments. In-memory unless `rootdir` is given.

Deletion is permanent by default, for the reason given in `make_report_store`.
Reading a key whose file is a symlink leading outside `rootdir` raises
`KeyError`, so a misconfigured root cannot make the attachment route serve other
files; `follow_symlinks=True` opts out.

```pycon
>>> make_attachment_store()
{}
```

* **Return type:**
  [`MutableMapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.MutableMapping)

### heed.make_bot_verifier(provider, secret, \*\*kwargs)

Return a verifier, or `None` (gate off) when no secret is configured.

`provider` defaults to `"turnstile"` when a secret is given.

* **Return type:**
  [`BotVerifier`](heed.security.html.md#heed.security.BotVerifier) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### heed.make_heuristic_moderator(, max_links=3, blocked_terms=(), on_blocked_term=Verdict.hold)

Hold reports with more than `max_links` links or any of `blocked_terms`.

Cheap, dependency-free spam signals. Holding (not rejecting) is the default so a
false positive costs a human click, not a lost report.

```pycon
>>> mod = make_heuristic_moderator(max_links=1, blocked_terms=["casino"])
>>> from heed.base import Identity
>>> def r(body): return Report(title="t", body=body, identity=Identity(anon_id="a"))
>>> [mod(r(b)).value for b in ["ok", "http://a http://b", "best CASINO"]]
['accept', 'hold', 'hold']
```

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[`Report`](heed.base.html.md#heed.base.Report)], [`Verdict`](heed.routing.html.md#heed.routing.Verdict)]

### heed.make_holding_store(rootdir=None, \*, delete_func=<built-in function remove>)

A report store for held reports, with a dedicated claims store beside it.

`release_held` and `recover_claimed` find the claims store on the holding store
(`heed_claims`), so a release lists only the reports being released right now,
not every held report (issue #21), and nothing listing holding (a moderation view,
`StoreSink.find_duplicates`) ever sees a claim. With `rootdir`, claims live in a
sibling directory, `<rootdir>.claims` (a subdirectory would show up in the
holding store’s recursive listing).

```pycon
>>> held = make_holding_store()
>>> held["r1"] = "{}"
>>> list(held), dict(held.heed_claims)
(['r1'], {})
```

* **Return type:**
  [`MutableMapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.MutableMapping)

### heed.make_image_sanitizer(name='auto', , max_pixels=25000000, max_concurrent=2, busy_wait_seconds=2.0)

Build a sanitizer from the `image_sanitizers` registry by name.

Fails at construction (not on the first upload) when the name is unknown or its
dependency is missing.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[`bytes`](https://docs.python.org/3/builtins/stdtypes.html#bytes), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)], [`bytes`](https://docs.python.org/3/builtins/stdtypes.html#bytes)]

### heed.make_rate_limiter(backend='memory', , max_per_window, window_seconds=60.0, namespace='ip', redis_url=None)

Build a limiter from the `rate_limiter_backends` registry by name.

* **Return type:**
  [`RateLimiter`](heed.security.html.md#heed.security.RateLimiter)

### heed.make_report_store(rootdir=None, \*, delete_func=<built-in function remove>)

A text store for report JSON. In-memory unless `rootdir` is given.

Deleting a key removes the file for good (`os.remove`). dol’s own default moves it
to the OS trash, which would keep a copy of every deleted, claimed or released
report – and reports can carry personal data. Pass `delete_func` to opt back in.

* **Return type:**
  [`MutableMapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.MutableMapping)

### heed.make_router(sink, , config=None, attachment_store=None, rate_limiter=None, origin_rate_limiter=None, bot_verifier=None, user_dependency=None, resolve_sink=None, moderator=None, holding_sink=None, image_sanitizer=None)

Build a FastAPI APIRouter exposing `POST /report` (and its CORS preflight).

Run standalone via [`make_app()`](#heed.make_app), or `include_router` it into an enlace app.
Provide `user_dependency` (a FastAPI dependency returning a user id or None) to
bind identity when an auth layer like enlace_auth is present.

Hardening defaults come from `config`; any piece can be injected instead:
`rate_limiter` (per client IP), `origin_rate_limiter` (per Origin) and
`bot_verifier` (by default built from `config.bot_secret`, i.e. off until a
secret is configured) and `image_sanitizer` (by default from
`config.screenshot_sanitizer`: re-encode with Pillow, else a pure-Python strip).

Routing is decided server-side, never by the browser: `resolve_sink` maps the
validated origin to that site’s sink (`sink` is then the fallback and may be
None; an origin resolving to no sink gets 403). `moderator` may hold reports in
`holding_sink` (required with a moderator) or reject them; see `routing.py`.

* **Return type:**
  `APIRouter`

### heed.make_widget_router(, asset_dir=None)

A router serving the built widget at `GET /widget/{name}`.

Include it beside [`heed.make_router()`](#heed.make_router) (`make_app` does) so the widget is
served from the same host that receives its reports. `asset_dir` defaults to the
files shipped in the package; point it elsewhere to serve another build.

* **Return type:**
  `APIRouter`

### heed.new_report_id()

Return a fresh opaque report id (uuid4 hex).

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### heed.process_submission(submission, sink, \*, origin='', user=None, screenshot=None, screenshot_media_type='image/png', attachment_store=None, config=None, id_gen=<function new_report_id>, clock=<function utcnow>, moderator=<function accept_all>, holding_sink=None)

Enrich `submission` into a Report, store any screenshot, and route to `sink`.

Returns the (report, sink-result) pair. The server owns id/created_at/identity/
origin/status — never the client. Opt-in capture (console/network) is dropped
unless enabled in `config`. `moderator` decides first: `hold` sends the report
(status `held`) to `holding_sink` instead, `reject` drops it (status
`declined`).

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Report`](heed.base.html.md#heed.base.Report), [`SinkResult`](heed.sinks.base.html.md#heed.sinks.base.SinkResult)]

### heed.recover_claimed(\*, holding_store, claimed_store=None, stale_after=None, clock=<built-in function time>)

Return every claimed report to holding, and the ids returned.

A claim outlives its [`release_held()`](#heed.release_held) only if the process died mid-release
(the sink may or may not have accepted the report, so a recovered report may be
sent twice – never zero times). Pass the same `claimed_store` as to
[`release_held()`](#heed.release_held).

An in-flight release’s claim looks the same as a stranded one. With one process,
call this when no release is running (e.g. at startup) and leave `stale_after`
unset: every claim is recovered. With several workers, one worker’s startup can
overlap another’s release, so pass `stale_after` (seconds; longer than any sink
call can take plus the clock skew between workers, e.g. `600`): only claims older
than that are recovered (and claims stamped further ahead than that, by a clock
running fast). Claims made before claim keys carried a time count as stale. A
release whose claim is recovered anyway puts the report back in holding and fails.
Beside a dedicated claims store, `claimed--` claims an older heed left in holding
are recovered too (which lists holding once).

Only a valid report (whose own id matches its claim key) is ever written to
holding, and a valid holding copy is never replaced: a claim whose report is
intact in holding is just dropped (even a torn one), and one whose holding copy
is torn or missing restores it. Otherwise a claim that is not a valid report is
left in place, and not returned.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### heed.reencode_image(data, media_type, , max_pixels=25000000)

Strip `data`, then decode it and save its first frame afresh, same format.

Images over `max_pixels` are refused before any pixel is decoded (a small file can
declare a huge canvas). Nothing but pixels (and PNG palette transparency) is carried
over: no comment, EXIF, ICC profile or other metadata. Raises `ValueError` on a
malformed, oversized or too costly image.

* **Return type:**
  [`bytes`](https://docs.python.org/3/builtins/stdtypes.html#bytes)

### heed.release_held(report_id, \*, holding_store, sink=None, resolve_sink=None, claimed_store=None, clock=<built-in function time>)

Release a held report: route it on and remove it from holding.

`holding_store` is the store behind the holding sink (a `StoreSink`’s
`.store`). The destination is `sink`, or — for a multi-site backend — the sink
`resolve_sink` gives for the report’s own origin (pass exactly one).

The report is claimed before submitting, so a second release while the first is in
flight finds nothing to send. Claiming *moves* it: it is written under a claim key
of its own (`<id>--<time>-<token>`) in the claims store and only then deleted
from holding. The claims store is `claimed_store` if given, else the one a
`make_holding_store` store carries, else (legacy) `holding_store` itself under a
`claimed--` prefix – which makes every release list the whole holding store.
If the sink fails the report is put back in holding; a release only ever
deletes its own claim, and only once the sink has accepted the report or it is
back in holding. So a crash at any point leaves the report in holding, in the
claims, or in both – never lost – and [`recover_claimed()`](#heed.recover_claimed) returns stranded
claims to holding. A report with a claim is not released again until then.

Not safe against two releases of the same report at the same moment: claiming is a
copy and a delete on a plain mapping, not an atomic move, so one interleaving
leaves a claim on a report the other release already sent, and the next
[`recover_claimed()`](#heed.recover_claimed) sends it again. Serialise releases of one report.

* **Return type:**
  [`SinkResult`](heed.sinks.base.html.md#heed.sinks.base.SinkResult)

### heed.roadmap(items)

Items still to come, started first; most-voted first within each stage.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`PublicItem`](heed.board.html.md#heed.board.PublicItem)]

### heed.screenshot_url_resolver(base_url, signer, , ref_pattern=re.compile('[0-9a-f]{32}\\\\.(?:png|jpg|webp)'))

A `GitHubIssuesSink(screenshot_url=...)` callable: the report’s first image
attachment as a (signed) URL under `base_url`, or None.

Pass `signer=None` only if the attachment route was built with `allow_unsigned`.
Refs the route would not serve (`ref_pattern`) get no URL.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[`Report`](heed.base.html.md#heed.base.Report)], [`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)]

### heed.sinks_by_origin(sinks, , default=None)

A resolver from an `{origin: sink}` mapping (origins compared normalized).

Unknown origins resolve to `default`; with no default they resolve to None, and
the router refuses them.

```pycon
>>> from heed.sinks import StoreSink
>>> a = StoreSink({})
>>> resolve = sinks_by_origin({"https://a.example": a})
>>> resolve("HTTPS://A.example/") is a, resolve("https://b.example")
(True, None)
```

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)], [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Sink`](heed.sinks.base.html.md#heed.sinks.base.Sink)]]

### heed.strip_image(data, media_type)

Keep only what renders the image; truncate at its end. Pure Python.

Raises `ValueError` when `data` is not a well-formed `media_type` image, or
one too costly to decode (over `MAX_JPEG_SCANS` scans, `_MAX_PARTS` parts).

* **Return type:**
  [`bytes`](https://docs.python.org/3/builtins/stdtypes.html#bytes)

### heed.widget_contract(config=None)

What the widget sends and the limits it must respect (from heed’s SSOT).

`config` supplies the server-configurable names (honeypot, bot token field) and
the screenshot cap; their defaults are what the widget assumes unless an embedder
overrides them.

```pycon
>>> widget_contract()["categories"]
['bug', 'feature', 'question', 'other']
```

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### Modules

| [`base`](heed.base.html.md#module-heed.base)         | Domain model for heed — the single source of truth shared by widget and backend.   |
|--------------------------------------------------------------------------------|------------------------------------------------------------------------------------|
| [`board`](heed.board.html.md#module-heed.board)       | The read side of a feedback inbox: what a public board may show.                   |
| [`config`](heed.config.html.md#module-heed.config)     | Configuration for the heed backend (smart defaults; keyword-only).                 |
| [`images`](heed.images.html.md#module-heed.images)     | Neutralise uploaded screenshots before they are stored (issue #15).                |
| [`ingest`](heed.ingest.html.md#module-heed.ingest)     | The ingest layer — build a Report from a submission and route it to a sink.        |
| [`routing`](heed.routing.html.md#module-heed.routing)   | Where a report goes: server-side site resolution and moderation (holding state).   |
| [`security`](heed.security.html.md#module-heed.security) | Public-endpoint hardening (the ingest endpoint is unauthenticated and hostile).    |
| [`serving`](heed.serving.html.md#module-heed.serving)   | Serve stored screenshots: a read-only route meant for its own origin (issue #19).  |
| [`sinks`](heed.sinks.html.md#module-heed.sinks)       | Sinks: pluggable report destinations (strategy pattern).                           |
| [`storage`](heed.storage.html.md#module-heed.storage)   | Storage helpers — dol-backed stores for reports and binary attachments.            |
| [`widget`](heed.widget.html.md#module-heed.widget)     | Serve the embeddable widget, and state the contract it is built against.           |
