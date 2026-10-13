# heed

heed — embeddable, no-install, framework-agnostic end-user feedback.

`heed` lets any visitor of a deployed web app report a bug or request a feature, with
useful context gathered automatically, routed to a pluggable backend (GitHub Issues
first). It works standalone and integrates with `enlace` as an optional add-on,
without depending on it.

Phase 1 ships the backend core (the widget is tracked in issue #6). The competitive
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
  > BotVerifier, SiteverifyBotVerifier, make_bot_verifier
  > (see misc/docs/embedding-security.md)
  <br/>
  Storage   make_report_store, make_attachment_store

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

| [`new_report_id`](#heed.new_report_id)()                                   | Return a fresh opaque report id (uuid4 hex).                                  |
|----------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------|
| [`github_sink_from_token`](#heed.github_sink_from_token)(repo, token, \*\*kwargs)   | Build a GitHubIssuesSink that creates issues in `repo` ('owner/name').        |
| [`process_submission`](#heed.process_submission)(submission, sink, \*[, ...])   | Enrich `submission` into a Report, store any screenshot, and route to `sink`. |
| [`make_router`](#heed.make_router)(sink, \*[, config, ...])              | Build a FastAPI APIRouter exposing `POST /report` (and its CORS preflight).   |
| [`make_app`](#heed.make_app)(sink, \*[, config, ...])                 | A standalone FastAPI app serving the heed router under `prefix`.              |
| [`make_rate_limiter`](#heed.make_rate_limiter)([backend, window_seconds, ...]) | Build a limiter from the `rate_limiter_backends` registry by name.            |
| [`make_bot_verifier`](#heed.make_bot_verifier)(provider, secret, \*\*kwargs)   | Return a verifier, or `None` (gate off) when no secret is configured.         |
| [`make_report_store`](#heed.make_report_store)([rootdir])                      | A text store for report JSON.                                                 |
| [`make_attachment_store`](#heed.make_attachment_store)([rootdir])                  | A bytes store for attachments.                                                |

### Classes

| [`Report`](#heed.Report)(\*\*data)                               | A server-enriched report — the unit a sink receives.                         |
|-------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------|
| [`ReportSubmission`](#heed.ReportSubmission)(\*\*data)                     | The (untrusted) payload a widget POSTs; server fields are assigned later.    |
| [`Identity`](#heed.Identity)(\*\*data)                             | Who submitted the report — anonymous by default.                             |
| [`Environment`](#heed.Environment)(\*\*data)                          | Client environment captured by default (no PII beyond the user agent).       |
| [`Category`](#heed.Category)(\*values)                             | What kind of feedback a report is.                                           |
| [`Status`](#heed.Status)(\*values)                               | Lifecycle of a report (flattened; see misc/docs/design.md for the full map). |
| [`Attachment`](#heed.Attachment)(\*\*data)                           | A stored binary artifact (e.g. a screenshot), referenced by store key.       |
| [`LogEntry`](#heed.LogEntry)(\*\*data)                             | One captured console entry (opt-in capture only).                            |
| [`NetEntry`](#heed.NetEntry)(\*\*data)                             | One captured network entry (opt-in; metadata only, never bodies).            |
| [`Sink`](#heed.Sink)(\*args, \*\*kwargs)                       | Anything that can receive a report.                                          |
| [`BaseSink`](#heed.BaseSink)()                                     | Convenience base giving no-op defaults for the optional sink methods.        |
| [`SinkResult`](#heed.SinkResult)(\*\*data)                           | Outcome of routing a report to a sink.                                       |
| [`StoreSink`](#heed.StoreSink)(store)                               | Persist each report as JSON into a dol-style store keyed by report id.       |
| [`GitHubIssuesSink`](#heed.GitHubIssuesSink)(create_issue, \*[, ...])      | Route a report to a GitHub issue via an injected `create_issue` callable.    |
| [`HeedConfig`](#heed.HeedConfig)(\*[, allowed_origins, ...])         | Knobs for the ingest endpoint.                                               |
| [`RateLimiter`](#heed.RateLimiter)(\*args, \*\*kwargs)                | Decide whether a request keyed by `key` (e.g. client IP) is allowed now.     |
| [`InMemoryRateLimiter`](#heed.InMemoryRateLimiter)(\*[, max_per_window, ...]) | Sliding-window, in-memory limiter.                                           |
| [`RedisRateLimiter`](#heed.RedisRateLimiter)(client, \*[, ...])            | Fixed-window limiter in Redis — one count shared by every worker and host.   |
| [`BotVerifier`](#heed.BotVerifier)(\*args, \*\*kwargs)                | Verify a human-challenge token server-side; True means "looks human".        |
| [`SiteverifyBotVerifier`](#heed.SiteverifyBotVerifier)(secret, \*[, ...])       | Turnstile / hCaptcha verification (both speak the same siteverify protocol). |

### *class* heed.Attachment(\*\*data)

Bases: `BaseModel`

A stored binary artifact (e.g. a screenshot), referenced by store key.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

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
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`DupCandidate`](heed.sinks.base.md#heed.sinks.base.DupCandidate)]

### *class* heed.BotVerifier(\*args, \*\*kwargs)

Bases: [`Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)

Verify a human-challenge token server-side; True means “looks human”.

### *class* heed.Category(\*values)

Bases: [`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Enum`](https://docs.python.org/3/library/enum.html#enum.Enum)

What kind of feedback a report is.

### *class* heed.Environment(\*\*data)

Bases: `BaseModel`

Client environment captured by default (no PII beyond the user agent).

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.GitHubIssuesSink(create_issue, , label_map=None, screenshot_url=None)

Bases: [`BaseSink`](heed.sinks.base.md#heed.sinks.base.BaseSink)

Route a report to a GitHub issue via an injected `create_issue` callable.

### *class* heed.HeedConfig(\*, allowed_origins=None, handle_cors=True, cors_allow_credentials=False, cors_max_age_seconds=600, max_body_bytes=6000000, max_screenshot_bytes=5000000, max_field_bytes=100000, max_form_fields=16, rate_limit_per_minute=30, origin_rate_limit_per_minute=600, rate_limit_window_seconds=60.0, rate_limit_backend='memory', redis_url=None, trusted_proxies=<factory>, ipv6_rate_limit_prefix=64, bot_gate=None, bot_secret=None, bot_expected_hostnames=None, bot_token_field='bot_token', honeypot_field='hp_website', accept_console=False, accept_network=False)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Knobs for the ingest endpoint. All optional with sensible defaults.

#### *property* bot_gate_enabled *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

True when a bot-gate secret is configured.

#### *classmethod* from_env(environ=None, \*\*overrides)

Build a config from `HEED_<FIELD>` variables, then apply `overrides`.

Lists are comma-separated; booleans accept 1/true/yes/on; `none` unsets an
optional knob (e.g. `HEED_ORIGIN_RATE_LIMIT_PER_MINUTE=none`).

* **Return type:**
  [`HeedConfig`](heed.config.md#heed.config.HeedConfig)

```pycon
>>> cfg = HeedConfig.from_env(
...     {"HEED_ALLOWED_ORIGINS": "https://a.example, https://b.example",
...      "HEED_RATE_LIMIT_PER_MINUTE": "10"}
... )
>>> cfg.allowed_origins, cfg.rate_limit_per_minute
(['https://a.example', 'https://b.example'], 10)
```

### *class* heed.Identity(\*\*data)

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

### *class* heed.LogEntry(\*\*data)

Bases: `BaseModel`

One captured console entry (opt-in capture only).

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.NetEntry(\*\*data)

Bases: `BaseModel`

One captured network entry (opt-in; metadata only, never bodies).

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
  [`RedisRateLimiter`](heed.security.md#heed.security.RedisRateLimiter)

### *class* heed.Report(\*\*data)

Bases: `BaseModel`

A server-enriched report — the unit a sink receives.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.ReportSubmission(\*\*data)

Bases: `BaseModel`

The (untrusted) payload a widget POSTs; server fields are assigned later.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.Sink(\*args, \*\*kwargs)

Bases: [`Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)

Anything that can receive a report. `submit` is the only required method.

### *class* heed.SinkResult(\*\*data)

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

Bases: [`BaseSink`](heed.sinks.base.md#heed.sinks.base.BaseSink)

Persist each report as JSON into a dol-style store keyed by report id.

The zero-configuration default when no external sink (e.g. GitHub) is configured.
`store` is any `MutableMapping[str, str]` (a plain dict, a `dol` file store,
S3, …) — dependency-injected so the sink stays testable.

#### find_duplicates(report)

Cheap title-equality duplicate scan over stored reports.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`DupCandidate`](heed.sinks.base.md#heed.sinks.base.DupCandidate)]

### heed.github_sink_from_token(repo, token, \*\*kwargs)

Build a GitHubIssuesSink that creates issues in `repo` (‘owner/name’).

Requires the `heed[github]` extra. `token` is a server-side credential (a GitHub
App installation token or a PAT) — never shipped to the browser.

* **Return type:**
  [`GitHubIssuesSink`](heed.sinks.github.md#heed.sinks.github.GitHubIssuesSink)

### heed.make_app(sink, , config=None, attachment_store=None, prefix='/heed', \*\*router_kwargs)

A standalone FastAPI app serving the heed router under `prefix`.

* **Return type:**
  `FastAPI`

### heed.make_attachment_store(rootdir=None)

A bytes store for attachments. In-memory unless `rootdir` is given.

* **Return type:**
  [`MutableMapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.MutableMapping)

### heed.make_bot_verifier(provider, secret, \*\*kwargs)

Return a verifier, or `None` (gate off) when no secret is configured.

`provider` defaults to `"turnstile"` when a secret is given.

* **Return type:**
  [`BotVerifier`](heed.security.md#heed.security.BotVerifier) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### heed.make_rate_limiter(backend='memory', , max_per_window, window_seconds=60.0, namespace='ip', redis_url=None)

Build a limiter from the `rate_limiter_backends` registry by name.

* **Return type:**
  [`RateLimiter`](heed.security.md#heed.security.RateLimiter)

### heed.make_report_store(rootdir=None)

A text store for report JSON. In-memory unless `rootdir` is given.

* **Return type:**
  [`MutableMapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.MutableMapping)

### heed.make_router(sink, , config=None, attachment_store=None, rate_limiter=None, origin_rate_limiter=None, bot_verifier=None, user_dependency=None)

Build a FastAPI APIRouter exposing `POST /report` (and its CORS preflight).

Run standalone via [`make_app()`](#heed.make_app), or `include_router` it into an enlace app.
Provide `user_dependency` (a FastAPI dependency returning a user id or None) to
bind identity when an auth layer like enlace_auth is present.

Hardening defaults come from `config`; any piece can be injected instead:
`rate_limiter` (per client IP), `origin_rate_limiter` (per Origin) and
`bot_verifier` (by default built from `config.bot_secret`, i.e. off until a
secret is configured).

* **Return type:**
  `APIRouter`

### heed.new_report_id()

Return a fresh opaque report id (uuid4 hex).

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### heed.process_submission(submission, sink, \*, origin='', user=None, screenshot=None, screenshot_media_type='image/png', attachment_store=None, config=None, id_gen=<function new_report_id>, clock=<function utcnow>)

Enrich `submission` into a Report, store any screenshot, and route to `sink`.

Returns the (report, sink-result) pair. The server owns id/created_at/identity/
origin/status — never the client. Opt-in capture (console/network) is dropped unless
enabled in `config`.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Report`](heed.base.md#heed.base.Report), [`SinkResult`](heed.sinks.base.md#heed.sinks.base.SinkResult)]

### Modules

| [`base`](heed.base.md#module-heed.base)         | Domain model for heed — the single source of truth shared by widget and backend.   |
|--------------------------------------------------------------------------------|------------------------------------------------------------------------------------|
| [`config`](heed.config.md#module-heed.config)     | Configuration for the heed backend (smart defaults; keyword-only).                 |
| [`ingest`](heed.ingest.md#module-heed.ingest)     | The ingest layer — build a Report from a submission and route it to a sink.        |
| [`security`](heed.security.md#module-heed.security) | Public-endpoint hardening (the ingest endpoint is unauthenticated and hostile).    |
| [`sinks`](heed.sinks.md#module-heed.sinks)       | Sinks: pluggable report destinations (strategy pattern).                           |
| [`storage`](heed.storage.md#module-heed.storage)   | Storage helpers — dol-backed stores for reports and binary attachments.            |
