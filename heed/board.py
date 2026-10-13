"""The read side of a feedback inbox: what a public board may show.

heed's write path ends at a :class:`~heed.sinks.base.Sink`. A *board* (votes, a roadmap,
a changelog) is the read path over what the sinks accumulated, and it is a public
surface over untrusted, user-written text. This module fixes its shape before any UI
exists, so that the surface can be built on GitHub labels today and on a native store
later without touching the widget or the ingest layer:

- :class:`PublicItem` -- the **only** thing a board may expose: title, category, status,
  link, vote count, date. Never the body, identity, origin, environment or attachments.
- :class:`BoardSource` -- the protocol a sink implements to be listable
  (:meth:`BoardSource.public_items`). :class:`~heed.sinks.base.BaseSink` answers
  ``[]``, so a sink that cannot list simply has an empty board.
- :func:`roadmap` and :func:`changelog` -- the two views the design names, as pure
  functions over items. Votes rank; status partitions.

Items reach a board only when an operator opts them in (for the GitHub sink, by a
``public`` label), because an unmoderated visitor title on a public page is an abuse
vector (``misc/docs/embedding-security.md``).

>>> from heed.base import Category, Status
>>> items = [
...     PublicItem(ref="1", title="Dark mode", category=Category.feature,
...                status=Status.planned, votes=5),
...     PublicItem(ref="2", title="Export CSV", category=Category.feature,
...                status=Status.completed, votes=9),
...     PublicItem(ref="3", title="Offline", category=Category.feature,
...                status=Status.triaged, votes=1),
... ]
>>> [i.title for i in roadmap(items)]
['Dark mode', 'Offline']
>>> [i.title for i in changelog(items)]
['Export CSV']
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from heed.base import Category, Status

#: Statuses that mean "still to come" (shown on a roadmap), in display order.
ROADMAP_STATUSES: tuple[Status, ...] = (
    Status.started,
    Status.planned,
    Status.triaged,
    Status.received,
)

#: Statuses that mean "shipped" (shown in a changelog).
CHANGELOG_STATUSES: tuple[Status, ...] = (Status.completed,)


class PublicItem(BaseModel):
    """One board entry: the public projection of a stored report."""

    ref: str = Field(description="The sink's reference (e.g. a GitHub issue number).")
    title: str
    category: Category = Category.feature
    status: Status = Status.received
    url: str | None = None
    votes: int = Field(default=0, ge=0)
    created_at: datetime | None = None
    closed_at: datetime | None = None


@runtime_checkable
class BoardSource(Protocol):
    """Anything that can list its public items (an optional sink capability)."""

    def public_items(self, *, category: Category | None = None) -> list[PublicItem]: ...


def roadmap(items: Iterable[PublicItem]) -> list[PublicItem]:
    """Items still to come, started first; most-voted first within each stage."""
    order = {status: rank for rank, status in enumerate(ROADMAP_STATUSES)}
    open_items = [item for item in items if item.status in order]
    return sorted(open_items, key=lambda i: (order[i.status], -i.votes, i.title))


def changelog(items: Iterable[PublicItem]) -> list[PublicItem]:
    """Shipped items, newest first (undated ones last)."""
    shipped = [item for item in items if item.status in CHANGELOG_STATUSES]
    return sorted(
        shipped,
        key=lambda i: (
            i.closed_at is None,
            -(i.closed_at.timestamp() if i.closed_at else 0),
            i.title,
        ),
    )
