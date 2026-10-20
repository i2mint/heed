"""Duplicate detection in two stages: cheap retrieval, then confirmation (#8).

1. **Retrieval** ranks the stored reports by word overlap with the incoming one
   (title counted twice, body once; Jaccard over folded words) and keeps the
   ``k`` best above ``min_score``. No model, no index: fine for a feedback queue.
2. **Confirmation** (optional) asks an injected ``confirm(new, candidate) -> float``
   judge, e.g. an LLM or an embedding similarity, and keeps those it scores at
   ``confirm_threshold`` or more.

The result is a list of :class:`~heed.sinks.base.DupCandidate`; nothing is merged.
:func:`mark_duplicate` records a decision on the stored report (``status`` becomes
``duplicate`` and ``extra["duplicate_of"]`` names the original), so a human or an
agent applies it explicitly.

>>> from heed.base import Identity, Report
>>> me = Identity(anon_id="a")
>>> old = Report(id="r1", title="Save button crashes the app", identity=me)
>>> new = Report(id="r2", title="App crashes when I press save", identity=me)
>>> [c.external_ref for c in find_duplicates(new, [old])]
['r1']
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, MutableMapping

from heed.base import Report, Status
from heed.routing import fold_text
from heed.sinks.base import DupCandidate

#: ``(incoming, candidate) -> similarity in [0, 1]``: the confirming judge.
Confirm = Callable[[Report, Report], float]

DEFAULT_K = 5
DEFAULT_MIN_SCORE = 0.25
DEFAULT_CONFIRM_THRESHOLD = 0.5
_STOPWORDS = frozenset(
    "a an and are as at be but by do does for from has have i if in into is it its "
    "me my no not of on or so that the then this to was when with you your".split()
)


def _words(report: Report) -> list[str]:
    def words(text: str) -> list[str]:
        return [w for w in re.findall(r"\w+", fold_text(text)) if w not in _STOPWORDS]

    return words(report.title) * 2 + words(report.body)


def word_similarity(a: Report, b: Report) -> float:
    """Jaccard similarity of the reports' folded words (title words weigh double).

    >>> from heed.base import Identity, Report
    >>> me = Identity(anon_id="a")
    >>> word_similarity(Report(title="Dark mode", identity=me),
    ...                 Report(title="dark  MODE please", identity=me))
    0.6667
    """
    from collections import Counter

    wa, wb = Counter(_words(a)), Counter(_words(b))
    union = sum((wa | wb).values())
    return round(sum((wa & wb).values()) / union, 4) if union else 0.0


def find_duplicates(
    report: Report,
    candidates: Iterable[Report],
    *,
    k: int = DEFAULT_K,
    min_score: float = DEFAULT_MIN_SCORE,
    confirm: Confirm | None = None,
    confirm_threshold: float = DEFAULT_CONFIRM_THRESHOLD,
) -> list[DupCandidate]:
    """Likely duplicates of ``report`` among ``candidates``, best first.

    Reports already marked duplicate, and ``report`` itself, are skipped. With
    ``confirm``, each retrieved candidate is re-scored by it and kept only at
    ``confirm_threshold`` or more; a judge that raises drops that candidate.
    """
    scored = []
    for other in candidates:
        if other.id == report.id or other.status is Status.duplicate:
            continue
        score = word_similarity(report, other)
        if score >= min_score:
            scored.append((score, other))
    scored.sort(key=lambda so: -so[0])
    out = []
    for score, other in scored[:k]:
        if confirm is not None:
            try:
                score = float(confirm(report, other))
            except Exception:  # noqa: BLE001 - a failing judge confirms nothing
                continue
            if not score >= confirm_threshold:  # also drops NaN
                continue
        out.append(
            DupCandidate(
                external_ref=other.id, score=round(score, 4), title=other.title
            )
        )
    return sorted(out, key=lambda c: -c.score)


def stored_reports(store: MutableMapping[str, str]) -> Iterable[Report]:
    """The reports in a StoreSink store (entries that do not parse are skipped)."""
    for raw in store.values():
        try:
            yield Report.model_validate_json(raw)
        except ValueError:
            continue


def mark_duplicate(
    store: MutableMapping[str, str], report_id: str, *, of: str
) -> Report:
    """Record that stored report ``report_id`` duplicates ``of`` (both must exist)."""
    if report_id == of:
        raise ValueError("a report cannot duplicate itself")
    if of not in store:
        raise KeyError(f"no report {of!r} to be a duplicate of")
    report = Report.model_validate_json(store[report_id])
    report = report.model_copy(
        update={
            "status": Status.duplicate,
            "extra": {**report.extra, "duplicate_of": of},
        }
    )
    store[report_id] = report.model_dump_json()
    return report
