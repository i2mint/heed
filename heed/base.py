"""Domain model for heed — the single source of truth shared by widget and backend.

Everything that crosses the widget↔backend boundary, or moves between the ingest
layer and a sink, is one of the Pydantic models defined here. The widget POSTs a
:class:`ReportSubmission` (what an untrusted client may assert); the backend enriches
it into a :class:`Report` (server-assigned id, timestamp, resolved identity, validated
origin, status) before handing it to a sink.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    Field,
    StringConstraints,
)

# --- Field limits (SSOT). The ingest endpoint is public, so every string and list a
# client can send is bounded here; the router adds byte-level caps on top (config.py).
MAX_TITLE_CHARS = 300
MAX_BODY_CHARS = 20_000
MAX_URL_CHARS = 2_000
MAX_SHORT_CHARS = 500  # user agent, browser, os, locale, ...
MAX_EXTRA_ITEMS = 32
MAX_EXTRA_KEY_CHARS = 64
MAX_ANON_ID_CHARS = 128
ANON_ID_PATTERN = r"^[A-Za-z0-9_.:-]+$"
MAX_LOG_ENTRIES = 200
MAX_LOG_MESSAGE_CHARS = 2_000

# C0/C1 control characters except tab, newline and carriage return, plus the Unicode
# bidi embedding/override/isolate controls (used to visually spoof text).
_CONTROL_CHARS = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]"
)
_SAFE_URL_SCHEMES = frozenset({"http", "https"})


def strip_control_chars(text: str) -> str:
    r"""Remove control characters (keeping tab/newline/CR) from untrusted text.

    >>> strip_control_chars("a\x00b\tc\u202ed")
    'ab\tcd'
    """
    return _CONTROL_CHARS.sub("", text)


def normalize_newlines(text):
    r"""Turn CRLF and lone CR into LF, so a line break counts as one character.

    Browsers send every line break of a form field as CRLF; without this, a body at
    the length limit would be refused for its line breaks.

    >>> normalize_newlines("a\r\nb\rc")
    'a\nb\nc'
    """
    if isinstance(text, str):
        return text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def _clean_text(text: str) -> str:
    return strip_control_chars(text).strip()


def _non_blank(text: str) -> str:
    if not text:
        raise ValueError("must not be blank")
    return text


def _safe_url(url: str) -> str:
    """Allow only empty or absolute http(s) URLs (no ``javascript:``/``data:``)."""
    if not url:
        return url
    parts = urlsplit(url)
    if parts.scheme.lower() not in _SAFE_URL_SCHEMES or not parts.netloc:
        raise ValueError("must be an absolute http(s) URL")
    return url


ShortText = Annotated[
    str, AfterValidator(_clean_text), StringConstraints(max_length=MAX_SHORT_CHARS)
]
Title = Annotated[
    str,
    AfterValidator(_clean_text),
    AfterValidator(_non_blank),
    StringConstraints(max_length=MAX_TITLE_CHARS),
]
BodyText = Annotated[
    str,
    BeforeValidator(normalize_newlines),
    AfterValidator(strip_control_chars),
    StringConstraints(max_length=MAX_BODY_CHARS),
]
SafeUrl = Annotated[
    str,
    AfterValidator(_clean_text),
    StringConstraints(max_length=MAX_URL_CHARS),
    AfterValidator(_safe_url),
]
ExtraKey = Annotated[
    str, AfterValidator(_clean_text), StringConstraints(max_length=MAX_EXTRA_KEY_CHARS)
]
LogMessage = Annotated[
    str,
    BeforeValidator(normalize_newlines),
    AfterValidator(strip_control_chars),
    StringConstraints(max_length=MAX_LOG_MESSAGE_CHARS),
]
AnonId = Annotated[
    str,
    StringConstraints(
        min_length=1, max_length=MAX_ANON_ID_CHARS, pattern=ANON_ID_PATTERN
    ),
]


def new_report_id() -> str:
    """Return a fresh opaque report id (uuid4 hex)."""
    return uuid4().hex


def utcnow() -> datetime:
    """Return the current UTC time (an injectable clock seam for tests)."""
    return datetime.now(timezone.utc)


class Category(str, Enum):
    """What kind of feedback a report is."""

    bug = "bug"
    feature = "feature"
    question = "question"
    other = "other"


class Status(str, Enum):
    """Lifecycle of a report (flattened; see misc/docs/design.md for the full map)."""

    received = "received"
    held = "held"  # parked by moderation until a human releases it
    triaged = "triaged"
    planned = "planned"
    started = "started"
    completed = "completed"
    declined = "declined"
    duplicate = "duplicate"


class Environment(BaseModel):
    """Client environment captured by default (no PII beyond the user agent)."""

    user_agent: ShortText | None = None
    browser: ShortText | None = None
    os: ShortText | None = None
    viewport: ShortText | None = Field(default=None, description='e.g. "1280x720"')
    locale: ShortText | None = None
    device_pixel_ratio: float | None = Field(default=None, gt=0, le=100)
    extra: dict[ExtraKey, ShortText] = Field(
        default_factory=dict, max_length=MAX_EXTRA_ITEMS
    )


class LogEntry(BaseModel):
    """One captured console entry (opt-in capture only)."""

    level: ShortText
    message: LogMessage
    at: datetime | None = None
    source: ShortText | None = None


class NetEntry(BaseModel):
    """One captured network entry (opt-in; metadata only, never bodies)."""

    method: ShortText
    url: Annotated[str, StringConstraints(max_length=MAX_URL_CHARS)]
    status: int | None = None
    duration_ms: float | None = None
    ok: bool | None = None


class Attachment(BaseModel):
    """A stored binary artifact (e.g. a screenshot), referenced by store key."""

    kind: str = "screenshot"
    media_type: str = "image/png"
    ref: str
    size: int | None = None


class Identity(BaseModel):
    """Who submitted the report — anonymous by default.

    ``anon_id`` is an opaque random id (the default). ``user`` is set only when an
    authenticated session is present (e.g. via enlace_auth). The client IP is NEVER
    stored here — it is used transiently for rate limiting only.
    """

    anon_id: str
    user: str | None = None


class ReportSubmission(BaseModel):
    """The (untrusted) payload a widget POSTs; server fields are assigned later."""

    category: Category = Category.bug
    title: Title
    body: BodyText = ""
    page_url: SafeUrl = ""
    env: Environment = Field(default_factory=Environment)
    console: list[LogEntry] | None = Field(default=None, max_length=MAX_LOG_ENTRIES)
    network: list[NetEntry] | None = Field(default=None, max_length=MAX_LOG_ENTRIES)
    # Opaque id the widget may carry across submissions; server still owns identity.
    anon_id: AnonId | None = None


class Report(BaseModel):
    """A server-enriched report — the unit a sink receives."""

    id: str = Field(default_factory=new_report_id)
    created_at: datetime = Field(default_factory=utcnow)
    category: Category = Category.bug
    title: str
    body: str = ""
    page_url: str = ""
    env: Environment = Field(default_factory=Environment)
    attachments: list[Attachment] = Field(default_factory=list)
    console: list[LogEntry] | None = None
    network: list[NetEntry] | None = None
    identity: Identity
    origin: str = ""
    status: Status = Status.received
    labels: list[str] = Field(default_factory=list)
    extra: dict[str, str] = Field(default_factory=dict)
