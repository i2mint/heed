"""Two-stage duplicate detection and explicit marking (#8)."""

import pytest

from heed.base import Identity, Report, Status
from heed.dedup import find_duplicates, mark_duplicate, stored_reports, word_similarity

ME = Identity(anon_id="a")


def _r(id, title, body="", **kw):
    return Report(id=id, title=title, body=body, identity=ME, **kw)


EXISTING = [
    _r("r1", "Export to CSV fails", "Clicking export gives an error"),
    _r("r2", "Add dark mode", "Please add a dark theme"),
    _r("r3", "CSV export broken", "export button shows an error"),
    _r("r4", "Typo on pricing page"),
]


def test_retrieval_ranks_by_word_overlap_and_skips_itself_and_duplicates():
    new = _r("n1", "CSV export gives an error")
    dups = find_duplicates(
        new, EXISTING + [new, _r("r5", "CSV export error", status=Status.duplicate)]
    )
    assert [d.external_ref for d in dups] == ["r1", "r3"]
    assert dups[0].score >= dups[1].score >= 0.25


def test_k_and_min_score_bound_the_result():
    new = _r("n1", "CSV export gives an error")
    assert len(find_duplicates(new, EXISTING, k=1)) == 1
    assert find_duplicates(new, EXISTING, min_score=0.99) == []


def test_confirmation_rescores_and_filters():
    new = _r("n1", "CSV export gives an error")
    judge = {"r1": 0.9, "r3": 0.2}
    dups = find_duplicates(new, EXISTING, confirm=lambda a, b: judge[b.id])
    assert [(d.external_ref, d.score) for d in dups] == [("r1", 0.9)]

    def broken(a, b):
        raise RuntimeError("model down")

    assert find_duplicates(new, EXISTING, confirm=broken) == []
    assert find_duplicates(new, EXISTING, confirm=lambda a, b: float("nan")) == []


def test_folding_defeats_case_and_width():
    a, b = _r("a", "ＤＡＲＫ mode"), _r("b", "dark MODE")
    assert word_similarity(a, b) == 1.0


def test_mark_duplicate_records_the_decision():
    store = {r.id: r.model_dump_json() for r in EXISTING}
    marked = mark_duplicate(store, "r3", of="r1")
    assert marked.status is Status.duplicate and marked.extra["duplicate_of"] == "r1"
    reread = {r.id: r for r in stored_reports(store)}
    assert reread["r3"].status is Status.duplicate
    with pytest.raises(KeyError):
        mark_duplicate(store, "r3", of="nope")
    with pytest.raises(ValueError):
        mark_duplicate(store, "r1", of="r1")


def test_stored_reports_skips_garbage():
    store = {"r1": EXISTING[0].model_dump_json(), "bad": "{not json"}
    assert [r.id for r in stored_reports(store)] == ["r1"]
