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
import unicodedata
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


_URL_PATTERN = re.compile(r"(?:https?://|www\.)")


def fold_text(text: str) -> str:
    """Normalise text for matching: NFKC, drop format chars (zero-width), casefold.

    Defeats fullwidth letters and zero-width splitting; it does not map cross-script
    look-alikes (e.g. Cyrillic "а"), which needs a confusables table.

    >>> fold_text("ＣＡＳ\u200bINO")
    'casino'
    """
    text = unicodedata.normalize("NFKC", text)
    return "".join(c for c in text if unicodedata.category(c) != "Cf").casefold()


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
    terms = [fold_text(t) for t in blocked_terms if t]

    def moderate(report: Report) -> Verdict:
        text = fold_text(f"{report.title}\n{report.body}")
        if terms and any(t in text for t in terms):
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
    sink: Sink | None = None,
    resolve_sink: SinkResolver | None = None,
) -> SinkResult:
    """Release a held report: route it on and remove it from holding.

    ``holding_store`` is the store behind the holding sink (a ``StoreSink``'s
    ``.store``). The destination is ``sink``, or — for a multi-site backend — the sink
    ``resolve_sink`` gives for the report's own origin (pass exactly one). The report
    is claimed (popped) before submitting, so two concurrent releases cannot both
    send it, and it is put back if the sink fails, so a failed release can be retried.
    """
    if (sink is None) == (resolve_sink is None):
        raise ValueError("pass exactly one of sink= or resolve_sink=")
    raw = holding_store.pop(report_id, None)
    if raw is None:
        raise KeyError(f"No held report with id {report_id!r}")
    try:
        report = Report.model_validate_json(raw)
        destination = sink if sink is not None else resolve_sink(report.origin)
        if destination is None:
            raise LookupError(f"No sink for the report's origin {report.origin!r}")
        if getattr(destination, "store", None) is holding_store:
            raise ValueError("the destination sink writes to the holding store itself")
        result = destination.submit(
            report.model_copy(update={"status": Status.received})
        )
    except BaseException:  # incl. KeyboardInterrupt/cancellation: never lose it
        holding_store[report_id] = raw
        raise
    if not result.ok:
        holding_store[report_id] = raw
    return result
