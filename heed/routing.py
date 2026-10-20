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

import logging
import math
import re
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping, MutableMapping
from enum import Enum
from typing import Optional
from uuid import uuid4

from heed.base import Report, Status
from heed.security import normalize_origin
from heed.sinks.base import Sink, SinkResult
from heed.storage import CLAIMS_ATTR

logger = logging.getLogger(__name__)

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


#: Prefix of the keys reports are claimed under, in the holding store itself, while
#: they are being released (when no separate ``claimed_store`` is given).
CLAIM_PREFIX = "claimed--"
#: Separates a report id from the per-release token in a claim key. The token is
#: ``<claim time, ms since the epoch>-<uuid4 hex>`` (claims made before #21 have no
#: time, and count as stale).
_CLAIM_SEP = "--"
_CLAIM_TOKEN = re.compile(r"(?:(?P<ms>[0-9]{1,15})-)?[0-9a-f]{32}")


def release_held(
    report_id: str,
    *,
    holding_store: MutableMapping[str, str],
    sink: Sink | None = None,
    resolve_sink: SinkResolver | None = None,
    claimed_store: MutableMapping[str, str] | None = None,
    clock: Callable[[], float] = time.time,
) -> SinkResult:
    """Release a held report: route it on and remove it from holding.

    ``holding_store`` is the store behind the holding sink (a ``StoreSink``'s
    ``.store``). The destination is ``sink``, or — for a multi-site backend — the sink
    ``resolve_sink`` gives for the report's own origin (pass exactly one).

    The report is claimed before submitting, so a second release while the first is in
    flight finds nothing to send. Claiming *moves* it: it is written under a claim key
    of its own (``<id>--<time>-<token>``) in the claims store and only then deleted
    from holding. The claims store is ``claimed_store`` if given, else the one a
    `make_holding_store` store carries, else (legacy) ``holding_store`` itself under a
    ``claimed--`` prefix -- which makes every release list the whole holding store.
    If the sink fails the report is put back in holding; a release only ever
    deletes its own claim, and only once the sink has accepted the report or it is
    back in holding. So a crash at any point leaves the report in holding, in the
    claims, or in both -- never lost -- and :func:`recover_claimed` returns stranded
    claims to holding. A report with a claim is not released again until then.

    Not safe against two releases of the same report at the same moment: claiming is a
    copy and a delete on a plain mapping, not an atomic move, so one interleaving
    leaves a claim on a report the other release already sent, and the next
    :func:`recover_claimed` sends it again. Serialise releases of one report.
    """
    if (sink is None) == (resolve_sink is None):
        raise ValueError("pass exactly one of sink= or resolve_sink=")
    claims, own_prefix = _claims_of(holding_store, claimed_store)
    if _is_claim_key(report_id, True):  # incl. one left in holding by an older heed
        raise ValueError(
            f"{report_id!r} is a claim, not a held report: use recover_claimed() to "
            "return stranded claims to holding"
        )
    raw = holding_store.get(report_id)
    if raw is None:
        raise KeyError(f"No held report with id {report_id!r}")
    if _claim_keys_of(report_id, claims, own_prefix=own_prefix):
        raise KeyError(
            f"Held report {report_id!r} is being released, or a crashed release left a "
            "claim on it: call recover_claimed() when no release is running"
        )
    claim_key = _new_claim_key(report_id, own_prefix=own_prefix, now=clock())
    if not (
        _is_claim_key(claim_key, own_prefix)
        and _report_id_of_claim_key(claim_key, own_prefix) == report_id
    ):  # never write a claim recover_claimed() could not find
        raise ValueError(f"{report_id!r} cannot be claimed: its claim key is ambiguous")
    claims[claim_key] = raw
    if report_id not in holding_store:  # a concurrent release took it meanwhile
        _drop_claim(claims, claim_key)
        raise KeyError(f"No held report with id {report_id!r}")
    try:
        del holding_store[report_id]
    except KeyError:
        # Usually a concurrent release took it first, and its claim is the one that
        # counts. But if no other claim exists the delete may have happened anyway
        # (stores that report errors after acting): keep ours, so it is not lost.
        others = set(_claim_keys_of(report_id, claims, own_prefix=own_prefix))
        if report_id in holding_store or others - {claim_key}:
            _drop_claim(claims, claim_key)
            raise KeyError(f"No held report with id {report_id!r}") from None
        raise KeyError(
            f"Could not take held report {report_id!r} out of holding; its claim is "
            "kept: call recover_claimed() when no release is running"
        ) from None
    if claim_key not in claims:
        # A recover_claimed() elsewhere took our claim for a stranded one while holding
        # was still intact (a pause or clock skew longer than its stale_after). We just
        # emptied holding, so put the report back rather than leave it nowhere.
        holding_store[report_id] = raw
        raise KeyError(
            f"The claim on held report {report_id!r} was recovered by another worker "
            "during this release; the report is back in holding. Raise stale_after."
        )
    try:
        report = Report.model_validate_json(raw)
        destination = sink if sink is not None else resolve_sink(report.origin)
        if destination is None:
            raise LookupError(f"No sink for the report's origin {report.origin!r}")
        if any(
            getattr(sink_, "store", None) is store
            for sink_ in _leaf_sinks(destination)
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


def _leaf_sinks(sink):
    """``sink``, or the sinks inside a composite one (e.g. a ``CategorySink``)."""
    members = getattr(sink, "sinks", None)
    if getattr(sink, "is_composite_sink", False) and callable(members):
        for inner in members():
            yield from _leaf_sinks(inner)
    else:
        yield sink


def discard_held(
    report_id: str,
    *,
    holding_store: MutableMapping[str, str],
    attachment_store: MutableMapping | None = None,
) -> None:
    """Delete a held report for good, with its attachments (e.g. a screenshot).

    The report leaves holding first, so a release that took it meanwhile wins and this
    raises ``KeyError`` before touching its attachments; a crash between the two steps
    leaves an orphaned screenshot, never a report pointing at a deleted one. Raises
    ``KeyError`` when ``report_id`` is not held. A report whose text no longer parses is
    still deleted, but its attachments cannot be named and are left. Run it serialised
    with :func:`release_held` for the same report.
    """
    if report_id.startswith(CLAIM_PREFIX):
        raise ValueError(f"{report_id!r} is a claim, not a held report")
    raw = holding_store[report_id]
    try:
        refs = [a.ref for a in Report.model_validate_json(raw).attachments]
    except ValueError:
        logger.warning(
            "heed: held report %s does not parse; its attachments are left", report_id
        )
        refs = []
    del holding_store[report_id]
    if attachment_store is not None:
        for ref in refs:
            attachment_store.pop(ref, None)


def recover_claimed(
    *,
    holding_store: MutableMapping[str, str],
    claimed_store: MutableMapping[str, str] | None = None,
    stale_after: float | None = None,
    clock: Callable[[], float] = time.time,
) -> list[str]:
    """Return every claimed report to holding, and the ids returned.

    A claim outlives its :func:`release_held` only if the process died mid-release
    (the sink may or may not have accepted the report, so a recovered report may be
    sent twice -- never zero times). Pass the same ``claimed_store`` as to
    :func:`release_held`.

    An in-flight release's claim looks the same as a stranded one. With one process,
    call this when no release is running (e.g. at startup) and leave ``stale_after``
    unset: every claim is recovered. With several workers, one worker's startup can
    overlap another's release, so pass ``stale_after`` (seconds; longer than any sink
    call can take plus the clock skew between workers, e.g. ``600``): only claims older
    than that are recovered (and claims stamped further ahead than that, by a clock
    running fast). Claims made before claim keys carried a time count as stale. A
    release whose claim is recovered anyway puts the report back in holding and fails.
    Beside a dedicated claims store, ``claimed--`` claims an older heed left in holding
    are recovered too (which lists holding once).

    Only a valid report (whose own id matches its claim key) is ever written to
    holding, and a valid holding copy is never replaced: a claim whose report is
    intact in holding is just dropped (even a torn one), and one whose holding copy
    is torn or missing restores it. Otherwise a claim that is not a valid report is
    left in place, and not returned.
    """
    if stale_after is not None and not (
        math.isfinite(stale_after) and stale_after >= 0
    ):
        raise ValueError(f"stale_after must be >= 0 seconds, got {stale_after}")
    claims, own_prefix = _claims_of(holding_store, claimed_store)
    now = _checked_time(clock())
    recovered = []
    for store, prefixed in _claim_locations(holding_store, claims, own_prefix):
        for claim_key in [k for k in list(store) if _is_claim_key(k, prefixed)]:
            if _is_young(_claim_time(claim_key, prefixed), now, stale_after):
                continue  # possibly a release still in flight
            report_id = _report_id_of_claim_key(claim_key, prefixed)
            if _recover_one(report_id, claim_key, holding_store, store):
                recovered.append(report_id)
    return recovered


def _claim_locations(holding_store, claims, own_prefix):
    """Where claims may be: the claims store, plus, beside a dedicated one,
    ``claimed--`` keys an older heed left in holding (listed once per recovery)."""
    yield claims, own_prefix
    if not own_prefix:
        yield holding_store, True


def _is_young(claimed_at, now, stale_after):
    """Whether a claim made at ``claimed_at`` may belong to a release still running.
    Claims without a time, and claims stamped further in the future than
    ``stale_after`` (a worker whose clock runs ahead), are not."""
    if stale_after is None or claimed_at is None:
        return False
    return abs(now - claimed_at) < stale_after


def _recover_one(report_id, claim_key, holding_store, claims) -> bool:
    """Return one claim's report to holding; whether it was returned."""
    if _is_report(holding_store.get(report_id), report_id):
        _drop_claim(claims, claim_key)  # holding is intact: the claim is redundant
        return False
    raw = claims.get(claim_key)
    if not _is_report(raw, report_id):
        return False  # torn or foreign: never let it replace anything
    _unclaim(report_id, raw, holding_store, claims, claim_key)
    return True


def _is_report(raw, report_id):
    """Whether ``raw`` is a valid serialized report with id ``report_id``."""
    if raw is None:
        return False
    try:
        return Report.model_validate_json(raw).id == report_id
    except ValueError:
        return False


def _claims_of(holding_store, claimed_store):
    """``(claims store, whether claims carry the CLAIM_PREFIX in holding itself)``."""
    if claimed_store is None:
        claimed_store = getattr(holding_store, CLAIMS_ATTR, None)
    if claimed_store is None:
        return holding_store, True
    if claimed_store is holding_store:
        raise ValueError("the claims store must not be the holding store itself")
    return claimed_store, False


#: Latest claim time a claim key can carry: ``_CLAIM_TOKEN`` allows 15 digits of ms.
_MAX_CLAIM_SECONDS = (10**15 - 1) / 1000


def _checked_time(now) -> float:
    """``now``, if it is a plausible time in seconds since the epoch."""
    if not (isinstance(now, (int, float)) and math.isfinite(now)):
        raise ValueError(f"clock() must return seconds since the epoch, got {now!r}")
    if not 0 <= now <= _MAX_CLAIM_SECONDS:
        raise ValueError(
            f"clock() returned {now!r}: expected seconds since the epoch (not ms or ns)"
        )
    return now


def _new_claim_key(report_id, *, own_prefix, now):
    """A claim key no other release of ``report_id`` can make, stamped with ``now``."""
    key = f"{report_id}{_CLAIM_SEP}{int(_checked_time(now) * 1000)}-{uuid4().hex}"
    return CLAIM_PREFIX + key if own_prefix else key


def _claim_token(key, own_prefix):
    """The claim key's token match, or None if ``key`` is not a claim key."""
    if own_prefix:
        if not key.startswith(CLAIM_PREFIX):
            return None
        key = key[len(CLAIM_PREFIX) :]
    report_id, sep, token = key.rpartition(_CLAIM_SEP)
    if not (sep and report_id):
        return None
    return _CLAIM_TOKEN.fullmatch(token)


def _is_claim_key(key, own_prefix):
    return _claim_token(key, own_prefix) is not None


def _claim_time(claim_key, own_prefix):
    """When the claim was made (seconds since the epoch), or None if it predates #21."""
    ms = _claim_token(claim_key, own_prefix)["ms"]
    return None if ms is None else int(ms) / 1000


def _report_id_of_claim_key(claim_key, own_prefix):
    key = claim_key[len(CLAIM_PREFIX) :] if own_prefix else claim_key
    return key.rpartition(_CLAIM_SEP)[0]


def _claim_keys_of(report_id, claims, *, own_prefix):
    """The claim keys of ``report_id`` currently in ``claims``."""
    return [
        k
        for k in list(claims)
        if _is_claim_key(k, own_prefix)
        and _report_id_of_claim_key(k, own_prefix) == report_id
    ]


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
