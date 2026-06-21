"""Domain model for heed — the single source of truth shared by widget and backend.

Everything that crosses the widget↔backend boundary, or moves between the ingest
layer and a sink, is one of the Pydantic models defined here. The widget POSTs a
:class:`ReportSubmission` (what an untrusted client may assert); the backend enriches
it into a :class:`Report` (server-assigned id, timestamp, resolved identity, validated
origin, status) before handing it to a sink.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


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
    triaged = "triaged"
    planned = "planned"
    started = "started"
    completed = "completed"
    declined = "declined"
    duplicate = "duplicate"


class Environment(BaseModel):
    """Client environment captured by default (no PII beyond the user agent)."""

    user_agent: str | None = None
    browser: str | None = None
    os: str | None = None
    viewport: str | None = Field(default=None, description='e.g. "1280x720"')
    locale: str | None = None
    device_pixel_ratio: float | None = None
    extra: dict[str, str] = Field(default_factory=dict)


class LogEntry(BaseModel):
    """One captured console entry (opt-in capture only)."""

    level: str
    message: str
    at: datetime | None = None
    source: str | None = None


class NetEntry(BaseModel):
    """One captured network entry (opt-in; metadata only, never bodies)."""

    method: str
    url: str
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
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(default="", max_length=20_000)
    page_url: str = Field(default="", max_length=2_000)
    env: Environment = Field(default_factory=Environment)
    console: list[LogEntry] | None = None
    network: list[NetEntry] | None = None
    # Opaque id the widget may carry across submissions; server still owns identity.
    anon_id: str | None = None


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
