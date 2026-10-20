# heed.config

Configuration for the heed backend (smart defaults; keyword-only).

Every knob has a safe default, so `HeedConfig()` works out of the box for a
single-worker deployment. [`HeedConfig.from_env()`](#heed.config.HeedConfig.from_env) reads the same knobs from
`HEED_*` environment variables, which is where secrets (the bot-gate key) and
infrastructure (the Redis URL) belong.

### Classes

| [`HeedConfig`](#heed.config.HeedConfig)(\*[, allowed_origins, ...])   | Knobs for the ingest endpoint.   |
|-------------------------------------------------------------------------------------------|----------------------------------|

### *class* heed.config.HeedConfig(\*, allowed_origins=None, handle_cors=True, cors_allow_credentials=False, cors_max_age_seconds=600, max_body_bytes=6000000, max_screenshot_bytes=5000000, screenshot_sanitizer='auto', max_screenshot_pixels=8500000, max_concurrent_reencodes=2, reencode_busy_wait_seconds=2.0, reencode_retry_after_seconds=5, max_field_bytes=100000, max_form_fields=16, rate_limit_per_minute=30, origin_rate_limit_per_minute=600, rate_limit_window_seconds=60.0, rate_limit_backend='memory', redis_url=None, redis_socket_timeout_seconds=0.5, rate_limit_max_keys=100000, trusted_proxies=list(), ipv6_rate_limit_prefix=64, limiter_threads=16, bot_verify_threads=8, reencode_threads=4, delivery_threads=8, bot_gate=None, bot_secret=None, bot_expected_hostnames=None, bot_token_field='bot_token', honeypot_field='hp_website', attachment_signing_key=None, attachment_url_ttl_seconds=None, attachment_previous_keys=list(), attachment_hosts=None, attachment_cache_control=None, accept_console=False, accept_network=False, mask_page_url=True, light_categories=<factory>)

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
  [`HeedConfig`](#heed.config.HeedConfig)
