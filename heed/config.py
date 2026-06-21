"""Configuration for the heed backend (smart defaults; keyword-only)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(kw_only=True)
class HeedConfig:
    """Knobs for the ingest endpoint. All optional with sensible defaults."""

    allowed_origins: list[str] | None = None  # None = allow any (dev)
    max_body_bytes: int = 2_000_000  # 2 MB total
    max_screenshot_bytes: int = 5_000_000  # 5 MB
    rate_limit_per_minute: int = 30
    accept_console: bool = False  # opt-in heavy capture (privacy by default)
    accept_network: bool = False
