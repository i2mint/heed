"""Configuration for the heed backend (smart defaults; keyword-only).

Every knob has a safe default, so ``HeedConfig()`` works out of the box for a
single-worker deployment. :meth:`HeedConfig.from_env` reads the same knobs from
``HEED_*`` environment variables, which is where secrets (the bot-gate key) and
infrastructure (the Redis URL) belong.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field, fields

from heed.security import parse_networks

ENV_PREFIX = "HEED_"


@dataclass(kw_only=True)
class HeedConfig:
    """Knobs for the ingest endpoint. All optional with sensible defaults."""

    # --- Origins / CORS -------------------------------------------------------------
    allowed_origins: list[str] | None = None  # None = allow any (dev only)
    handle_cors: bool = True  # router answers preflight + sets CORS headers itself
    cors_allow_credentials: bool = False  # needs an explicit allowed_origins list
    cors_max_age_seconds: int = 600

    # --- Payload caps ---------------------------------------------------------------
    max_body_bytes: int = 6_000_000  # whole request; must leave room for a screenshot
    max_screenshot_bytes: int = 5_000_000
    # Rewrite accepted screenshots so only the image survives (polyglots, EXIF/GPS):
    # "auto" | "reencode" (Pillow) | "strip" (pure Python) | "none". See images.py.
    screenshot_sanitizer: str = "auto"
    max_screenshot_pixels: int = 25_000_000  # refused before decoding (re-encode only)
    max_concurrent_reencodes: int = 2  # per process; beyond it a submission gets 503
    max_field_bytes: int = 100_000  # any single non-file form field
    max_form_fields: int = 16

    # --- Rate limiting --------------------------------------------------------------
    rate_limit_per_minute: int = 30  # per client IP
    origin_rate_limit_per_minute: int | None = 600  # per Origin; None disables
    rate_limit_window_seconds: float = 60.0
    rate_limit_backend: str = "memory"  # "memory" | "redis" (see security.py)
    redis_url: str | None = field(default=None, repr=False)
    # Proxies whose X-Forwarded-For is trusted for the client IP (IPs or CIDRs).
    # Behind a reverse proxy (Caddy, nginx) set this, or every visitor shares the
    # proxy's address and one rate-limit bucket.
    trusted_proxies: list[str] = field(default_factory=list)
    ipv6_rate_limit_prefix: int = 64  # IPv6 clients share a bucket per /64

    # --- Bot gate (off until a secret is configured) --------------------------------
    bot_gate: str | None = None  # "turnstile" | "hcaptcha"; None infers turnstile
    bot_secret: str | None = field(default=None, repr=False)
    # Hostnames a challenge must have been solved on (rejects tokens farmed elsewhere).
    bot_expected_hostnames: list[str] | None = None
    bot_token_field: str = "bot_token"
    # A hidden form field humans leave empty; a filled one is silently dropped.
    honeypot_field: str | None = "hp_website"

    # --- Capture policy -------------------------------------------------------------
    accept_console: bool = False  # opt-in heavy capture (privacy by default)
    accept_network: bool = False

    def __post_init__(self):
        if self.max_body_bytes < self.max_screenshot_bytes:
            raise ValueError(
                f"max_body_bytes ({self.max_body_bytes}) must be >= "
                f"max_screenshot_bytes ({self.max_screenshot_bytes}): the screenshot "
                "travels inside the request body"
            )
        if self.cors_allow_credentials and self.allowed_origins is None:
            raise ValueError(
                "cors_allow_credentials=True requires an explicit allowed_origins "
                "list; reflecting any origin with credentials lets every site on the "
                "web submit as your logged-in users"
            )
        if not 0 <= self.ipv6_rate_limit_prefix <= 128:
            raise ValueError(
                f"ipv6_rate_limit_prefix must be in 0..128, "
                f"got {self.ipv6_rate_limit_prefix}"
            )
        try:
            parse_networks(self.trusted_proxies)
        except ValueError as e:
            raise ValueError(f"invalid trusted_proxies entry: {e}") from None
        if self.bot_gate is not None and not self.bot_secret:
            raise ValueError(
                f"bot_gate={self.bot_gate!r} is set but bot_secret is empty; set "
                f"{ENV_PREFIX}BOT_SECRET (or leave bot_gate unset to keep it off)"
            )

    @property
    def bot_gate_enabled(self) -> bool:
        """True when a bot-gate secret is configured."""
        return bool(self.bot_secret)

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None, **overrides
    ) -> HeedConfig:
        """Build a config from ``HEED_<FIELD>`` variables, then apply ``overrides``.

        Lists are comma-separated; booleans accept 1/true/yes/on; ``none`` unsets an
        optional knob (e.g. ``HEED_ORIGIN_RATE_LIMIT_PER_MINUTE=none``).

        >>> cfg = HeedConfig.from_env(
        ...     {"HEED_ALLOWED_ORIGINS": "https://a.example, https://b.example",
        ...      "HEED_RATE_LIMIT_PER_MINUTE": "10"}
        ... )
        >>> cfg.allowed_origins, cfg.rate_limit_per_minute
        (['https://a.example', 'https://b.example'], 10)
        """
        environ = os.environ if environ is None else environ
        kwargs = {}
        for f in fields(cls):
            raw = environ.get(ENV_PREFIX + f.name.upper())
            if raw is not None and raw != "":
                kwargs[f.name] = _parse_env_value(raw, f.type)
        kwargs.update(overrides)
        return cls(**kwargs)


def _parse_env_value(raw: str, type_hint: str):
    hint = str(type_hint)
    if raw.strip().lower() == "none" and "None" in hint:
        return None
    if hint.startswith("list"):
        return [item.strip() for item in raw.split(",") if item.strip()]
    if hint.startswith("bool"):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if hint.startswith("int"):
        return int(raw)
    if hint.startswith("float"):
        return float(raw)
    return raw
