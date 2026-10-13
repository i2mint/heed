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


#: Prefix of the key a report is claimed under, in the holding store itself, while it is
#: being released (when no separate ``claimed_store`` is given).
CLAIM_PREFIX = "claimed--"


def release_held(
    report_id: str,
    *,
    holding_store: MutableMapping[str, str],
    sink: Sink | None = None,
    resolve_sink: SinkResolver | None = None,
    claimed_store: MutableMapping[str, str] | None = None,
) -> SinkResult:
    """Release a held report: route it on and remove it from holding.

    ``holding_store`` is the store behind the holding sink (a ``StoreSink``'s
    ``.store``). The destination is ``sink``, or — for a multi-site backend — the sink
    ``resolve_sink`` gives for the report's own origin (pass exactly one).

    The report is claimed before submitting, so a second release while the first is in
    flight finds nothing to send. Claiming *moves* it: it is written to a claim key
    (``CLAIM_PREFIX + report_id`` in ``holding_store``, or ``report_id`` in
    ``claimed_store`` if given) and only then deleted from holding. If the sink fails
    the report is put back in holding; the claim is deleted only once the sink has
    accepted it or it is back in holding. So a crash at any point leaves the report
    in holding, in the claims, or in both -- never lost -- and :func:`recover_claimed`
    returns stranded claims to holding.
    """
    if (sink is None) == (resolve_sink is None):
        raise ValueError("pass exactly one of sink= or resolve_sink=")
    claims, claim_key = _claim_location(report_id, holding_store, claimed_store)
    if claimed_store is None and report_id.startswith(CLAIM_PREFIX):
        raise ValueError(
            f"{report_id!r} is a claim, not a held report: use recover_claimed() to "
            "return stranded claims to holding"
        )
    raw = holding_store.get(report_id)
    if raw is None or claim_key in claims:
        raise KeyError(f"No held report with id {report_id!r}")
    claims[claim_key] = raw
    try:
        del holding_store[report_id]
    except KeyError:  # a concurrent release claimed it first: it owns the claim now
        raise KeyError(f"No held report with id {report_id!r}") from None
    try:
        report = Report.model_validate_json(raw)
        destination = sink if sink is not None else resolve_sink(report.origin)
        if destination is None:
            raise LookupError(f"No sink for the report's origin {report.origin!r}")
        if any(
            getattr(destination, "store", None) is store
            for store in (holding_store, claims)
        ):
            raise ValueError("the destination sink writes to the holding store itself")
        result = destination.submit(
            report.model_copy(update={"status": Status.received})
        )
    except BaseException:  # incl. KeyboardInterrupt/cancellation: never lose it
        _unclaim(report_id, raw, holding_store, claims, claim_key)
        raise
    if result.ok:
        _drop_claim(claims, claim_key)
    else:
        _unclaim(report_id, raw, holding_store, claims, claim_key)
    return result


def recover_claimed(
    *,
    holding_store: MutableMapping[str, str],
    claimed_store: MutableMapping[str, str] | None = None,
) -> list[str]:
    """Return every claimed report to holding, and the ids returned.

    A claim outlives its :func:`release_held` only if the process died mid-release
    (the sink may or may not have accepted the report). Call this when no release is
    running -- e.g. at startup -- since an in-flight release's claim looks the same.
    Pass the same ``claimed_store`` as to :func:`release_held`.
    """
    if claimed_store is None:
        claim_keys = [k for k in list(holding_store) if k.startswith(CLAIM_PREFIX)]
        id_of_claim_key = lambda k: k[len(CLAIM_PREFIX) :]  # noqa: E731
        claims = holding_store
    else:
        claim_keys, id_of_claim_key, claims = list(claimed_store), str, claimed_store
    recovered = []
    for claim_key in claim_keys:
        report_id = id_of_claim_key(claim_key)
        _unclaim(report_id, claims[claim_key], holding_store, claims, claim_key)
        recovered.append(report_id)
    return recovered


def _claim_location(report_id, holding_store, claimed_store):
    """The ``(store, key)`` a report is claimed under while it is being released."""
    if claimed_store is None:
        return holding_store, CLAIM_PREFIX + report_id
    return claimed_store, report_id


def _unclaim(report_id, raw, holding_store, claims, claim_key):
    """Put a claimed report back in holding, then drop its claim (in that order, so a
    crash in between leaves it in both, never in neither)."""
    holding_store[report_id] = raw
    _drop_claim(claims, claim_key)


def _drop_claim(claims, claim_key):
    try:
        del claims[claim_key]
    except KeyError:
        pass
