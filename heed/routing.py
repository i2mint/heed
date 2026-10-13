"""Where a report goes: server-side site resolution and moderation (holding state).

Two seams, both decided on the server, never by the browser:

- **Site resolution**: a :data:`SinkResolver` maps the *validated* request origin to
  the sink for that site (e.g. which GitHub repo). The widget never names a target, so
  a visitor cannot redirect reports into someone else's tracker.
  :func:`sinks_by_origin` builds one from a plain mapping.
- **Moderation**: a :data:`Moderator` looks at a server-enriched report and returns a
  :class:`Verdict`. ``hold`` parks the report in a holding sink (status ``held``)
  until a human releases it with :func:`release_held`; ``reject`` drops it.
  :func:`accept_all` is the default, :func:`hold_all` gives full pre-moderation, and
  :func:`make_heuristic_moderator` holds link floods and blocked terms.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, MutableMapping
from enum import Enum
from typing import Optional

from heed.base import Report, Status
from heed.security import normalize_origin
from heed.sinks.base import Sink, SinkResult

# ---------------------------------------------------------------- site resolution

SinkResolver = Callable[[str], Optional[Sink]]


def sinks_by_origin(
    sinks: Mapping[str, Sink], *, default: Sink | None = None
) -> SinkResolver:
    """A resolver from an ``{origin: sink}`` mapping (origins compared normalized).

    Unknown origins resolve to ``default``; with no default they resolve to None, and
    the router refuses them.

    >>> from heed.sinks import StoreSink
    >>> a = StoreSink({})
    >>> resolve = sinks_by_origin({"https://a.example": a})
    >>> resolve("HTTPS://A.example/") is a, resolve("https://b.example")
    (True, None)
    """
    table = {normalize_origin(origin): sink for origin, sink in sinks.items()}

    def resolve(origin: str) -> Sink | None:
        return table.get(normalize_origin(origin), default) if origin else default

    return resolve


# --------------------------------------------------------------------- moderation


class Verdict(str, Enum):
    """A moderator's decision about one report."""

    accept = "accept"  # route to the site's sink now
    hold = "hold"  # park in the holding sink until a human releases it
    reject = "reject"  # drop (the submitter still sees a normal success)


Moderator = Callable[[Report], Verdict]

_SEVERITY = {Verdict.accept: 0, Verdict.hold: 1, Verdict.reject: 2}


def accept_all(report: Report) -> Verdict:
    """The default moderator: everything goes straight to the sink."""
    return Verdict.accept


def hold_all(report: Report) -> Verdict:
    """Full pre-moderation: every report waits for a human."""
    return Verdict.hold


_URL_PATTERN = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)


def make_heuristic_moderator(
    *,
    max_links: int = 3,
    blocked_terms: Iterable[str] = (),
    on_blocked_term: Verdict = Verdict.hold,
) -> Moderator:
    """Hold reports with more than ``max_links`` links or any of ``blocked_terms``.

    Cheap, dependency-free spam signals. Holding (not rejecting) is the default so a
    false positive costs a human click, not a lost report.

    >>> mod = make_heuristic_moderator(max_links=1, blocked_terms=["casino"])
    >>> from heed.base import Identity
    >>> def r(body): return Report(title="t", body=body, identity=Identity(anon_id="a"))
    >>> [mod(r(b)).value for b in ["ok", "http://a http://b", "best CASINO"]]
    ['accept', 'hold', 'hold']
    """
    terms = [t.lower() for t in blocked_terms if t]

    def moderate(report: Report) -> Verdict:
        text = f"{report.title}\n{report.body}"
        if terms and any(t in text.lower() for t in terms):
            return on_blocked_term
        if len(_URL_PATTERN.findall(text)) > max_links:
            return Verdict.hold
        return Verdict.accept

    return moderate


def chain_moderators(*moderators: Moderator) -> Moderator:
    """Combine moderators; the most severe verdict wins (reject > hold > accept)."""

    def moderate(report: Report) -> Verdict:
        verdicts = [Verdict(m(report)) for m in moderators] or [Verdict.accept]
        return max(verdicts, key=_SEVERITY.__getitem__)

    return moderate


def release_held(
    report_id: str,
    *,
    holding_store: MutableMapping[str, str],
    sink: Sink,
) -> SinkResult:
    """Release a held report: route it to ``sink`` and remove it from holding.

    ``holding_store`` is the store behind the holding sink (a ``StoreSink``'s
    ``.store``). The report is removed only after the sink accepts it, so a failed
    release can be retried.
    """
    try:
        raw = holding_store[report_id]
    except KeyError:
        raise KeyError(f"No held report with id {report_id!r}") from None
    report = Report.model_validate_json(raw).model_copy(
        update={"status": Status.received}
    )
    result = sink.submit(report)
    if result.ok:
        del holding_store[report_id]
    return result
