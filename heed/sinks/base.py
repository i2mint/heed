"""The sink abstraction — where a report goes (strategy pattern).

A sink is the only thing that decides what becomes of a report. The ingest layer calls
:meth:`Sink.submit`; richer sinks may also offer duplicate detection and
acknowledgement. New destinations are added by writing a sink, never by editing a
dispatcher.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from heed.base import Report


class SinkResult(BaseModel):
    """Outcome of routing a report to a sink."""

    ok: bool
    external_ref: str | None = None  # e.g. issue number, store key
    url: str | None = None  # e.g. issue URL
    detail: str | None = None


class DupCandidate(BaseModel):
    """A possible duplicate of an incoming report."""

    external_ref: str
    score: float = 0.0
    title: str | None = None


@runtime_checkable
class Sink(Protocol):
    """Anything that can receive a report. ``submit`` is the only required method."""

    def submit(self, report: Report) -> SinkResult: ...


class BaseSink:
    """Convenience base giving no-op defaults for the optional sink methods."""

    def submit(self, report: Report) -> SinkResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def find_duplicates(self, report: Report) -> list[DupCandidate]:
        """Return likely duplicates of ``report`` (default: none)."""
        return []

    def acknowledge(self, report: Report) -> None:
        """Hook called after a successful submit (default: no-op)."""
        return None
