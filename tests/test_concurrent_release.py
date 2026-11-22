"""Two releases of one report at the same moment send it once, when the claim is an
atomic move (issue #24)."""

import threading

import pytest

from heed import (
    ReportSubmission,
    StoreSink,
    hold_all,
    make_holding_store,
    pop_claim_move,
    process_submission,
    recover_claimed,
    release_held,
)
from heed.storage import CLAIM_MOVE_ATTR, CLAIMS_ATTR


def _hold(held):
    report, _ = process_submission(
        ReportSubmission(title="x"),
        StoreSink({}),
        moderator=hold_all,
        holding_sink=StoreSink(held),
    )
    return report.id


class _RecordingSink(StoreSink):
    """A sink that records every report id it accepts, in order."""

    def __init__(self):
        super().__init__({})
        self.sent = []

    def submit(self, report):
        self.sent.append(report.id)
        return super().submit(report)


class _InterleavedHolding(dict):
    """A holding store that runs ``meanwhile`` once, the first time a release reads it:
    the other release runs to completion between this one's read and its claim."""

    meanwhile = None

    def get(self, k, default=None):
        value = super().get(k, default)
        run, self.meanwhile = self.meanwhile, None
        if run is not None:
            run()
        return value


def test_a_release_that_loses_the_race_sends_nothing():
    """The interleaving of issue #24: B releases X between A's read and A's claim."""
    held = _InterleavedHolding()
    setattr(held, CLAIMS_ATTR, {})
    setattr(held, CLAIM_MOVE_ATTR, pop_claim_move)
    rid = _hold(held)
    sink = _RecordingSink()
    held.meanwhile = lambda: release_held(rid, holding_store=held, sink=sink)

    with pytest.raises(KeyError, match="No held report"):
        release_held(rid, holding_store=held, sink=sink)

    assert sink.sent == [rid]
    assert recover_claimed(holding_store=held) == []
    assert rid not in held and not getattr(held, CLAIMS_ATTR)


@pytest.mark.parametrize("on_disk", [False, True], ids=["memory", "disk"])
def test_concurrent_releases_send_a_report_exactly_once(tmp_path, on_disk):
    held = make_holding_store(str(tmp_path / "held") if on_disk else None)
    sink = _RecordingSink()
    n = 8
    for _ in range(20):
        rid = _hold(held)
        start, outcomes = threading.Barrier(n), []

        def release():
            start.wait()
            try:
                outcomes.append(release_held(rid, holding_store=held, sink=sink).ok)
            except KeyError:
                outcomes.append("lost")

        threads = [threading.Thread(target=release) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert outcomes.count(True) == 1, outcomes
        assert recover_claimed(holding_store=held) == []  # no claim left to resend
        assert rid not in held
    assert len(sink.sent) == len(set(sink.sent)) == 20


def test_an_explicit_claim_move_is_used(tmp_path):
    held, claims, moves = {}, {}, []

    def move(holding, report_id, claims_, claim_key):
        moves.append(report_id)
        pop_claim_move(holding, report_id, claims_, claim_key)

    rid = _hold(held)
    sink = _RecordingSink()
    assert release_held(
        rid, holding_store=held, sink=sink, claimed_store=claims, claim_move=move
    ).ok
    assert moves == [rid] and sink.sent == [rid] and not held and not claims


def test_the_carried_move_is_not_used_with_another_claims_store(tmp_path):
    """``rename_claim_move`` needs file stores on both sides: a claims store passed
    explicitly is not the holding store's own, so the release claims by copy."""
    held = make_holding_store(str(tmp_path / "held"))
    rid = _hold(held)
    other_claims = {}
    sink = _RecordingSink()
    assert release_held(
        rid, holding_store=held, sink=sink, claimed_store=other_claims
    ).ok
    assert sink.sent == [rid] and rid not in held and not other_claims


def test_a_failed_sink_puts_an_atomically_claimed_report_back(tmp_path):
    held = make_holding_store(str(tmp_path / "held"))
    rid = _hold(held)

    class Failing(StoreSink):
        def submit(self, report):
            raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        release_held(rid, holding_store=held, sink=Failing({}))
    assert rid in held and not list(getattr(held, CLAIMS_ATTR))
