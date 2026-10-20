"""A sink that sends each category of report to its own sink.

One widget and one :class:`~heed.base.Report` cover a bug, a feature request and a
question; the sink decides what each becomes. :class:`CategorySink` is that decision as
data: bugs to the tracker, feature requests somewhere else (a second repo, a Discussions
sink, a store), anything unlisted to ``default``. It is itself a sink, so it nests under
:func:`~heed.routing.sinks_by_origin` and under moderation like any other.

>>> from heed.sinks import StoreSink
>>> bugs, ideas = StoreSink({}), StoreSink({})
>>> sink = CategorySink({"feature": ideas}, default=bugs)
>>> sink.sink_for("feature") is ideas and sink.sink_for("bug") is bugs
True
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import ClassVar

from heed.base import Category, Report
from heed.sinks.base import BaseSink, DupCandidate, PublicItem, Sink, SinkResult


class CategorySink(BaseSink):
    """Route by ``report.category``; unlisted categories go to ``default``."""

    #: Marks a sink that holds other sinks, so code walking a sink tree (the held-report
    #: release guard) does not mistake any sink that happens to have a ``sinks`` method.
    is_composite_sink: ClassVar[bool] = True

    def __init__(self, sinks: Mapping[Category | str, Sink], *, default: Sink):
        self._sinks = {Category(category): sink for category, sink in sinks.items()}
        self._default = default

    def sink_for(self, category: Category | str) -> Sink:
        """The sink that receives reports of ``category``."""
        return self._sinks.get(Category(category), self._default)

    def sinks(self) -> list[Sink]:
        """Every distinct member sink, including the default."""
        return self._distinct()

    def _distinct(self) -> list[Sink]:
        seen: list[Sink] = []
        for sink in (*self._sinks.values(), self._default):
            if not any(sink is other for other in seen):
                seen.append(sink)
        return seen

    def submit(self, report: Report) -> SinkResult:
        return self.sink_for(report.category).submit(report)

    def find_duplicates(self, report: Report) -> list[DupCandidate]:
        sink = self.sink_for(report.category)
        finder = getattr(sink, "find_duplicates", None)
        return finder(report) if callable(finder) else []

    def acknowledge(self, report: Report) -> None:
        sink = self.sink_for(report.category)
        hook = getattr(sink, "acknowledge", None)
        if callable(hook):
            hook(report)

    def public_items(self, *, category: Category | None = None) -> list[PublicItem]:
        """Every distinct member sink's public items (a shared link once)."""
        items: dict[str, PublicItem] = {}
        for sink in self._distinct():
            lister = getattr(sink, "public_items", None)
            if callable(lister):
                for item in _items_of(lister, category):
                    # (no link: the same item is told apart by what it says, not by its sink)
                    key = item.url or f"{item.category}|{item.ref}|{item.title}|{item.created_at}"
                    items.setdefault(key, item)
        return list(items.values())


def _items_of(lister: Callable[..., list[PublicItem]], category: Category | None):
    """``lister``'s items of ``category``, whether or not it takes a ``category`` keyword.

    A third-party sink's ``public_items`` may not (the :class:`~heed.board.BoardSource`
    protocol names the keyword, but a duck-typed sink can differ): it is then called bare
    and filtered here, never failed on.
    """
    if category is None:
        return lister()
    try:
        params = inspect.signature(lister).parameters.values()
    except (TypeError, ValueError):  # a builtin or C callable with no signature
        params = ()
    if any(p.name == "category" or p.kind is p.VAR_KEYWORD for p in params):
        return lister(category=category)
    return [item for item in lister() if item.category == Category(category)]
