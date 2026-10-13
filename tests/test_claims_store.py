"""A dedicated claims store and a staleness rule for recovery (issue #21)."""

import math
import time

import pytest

from heed import (
    ReportSubmission,
    StoreSink,
    hold_all,
    make_holding_store,
    process_submission,
    recover_claimed,
    release_held,
)

TOKEN = "0" * 32


def _hold(held):
    report, _ = process_submission(
        ReportSubmission(title="x"),
        StoreSink({}),
        moderator=hold_all,
        holding_sink=StoreSink(held),
    )
    return report.id


class _CountingHolding(dict):
    """A holding store that counts how often it is listed."""

    listings = 0

    def __iter__(self):
        type(self).listings += 1
        return super().__iter__()


def test_a_release_never_lists_the_holding_store():
    held = _CountingHolding()
    held.heed_claims = {}
    ids = [_hold(held) for _ in range(50)]
    _CountingHolding.listings = 0
    live = {}
    for rid in ids:
        assert release_held(rid, holding_store=held, sink=StoreSink(live)).ok
    assert _CountingHolding.listings == 0
    assert sorted(live) == sorted(ids) and held == {} and held.heed_claims == {}


@pytest.mark.parametrize("on_disk", [False, True], ids=["memory", "files"])
def test_claims_never_show_in_holding(tmp_path, on_disk):
    held = make_holding_store(str(tmp_path / "held") if on_disk else None)
    rid = _hold(held)
    seen = {}

    class Sink:
        def submit(self, r):
            seen["holding"], seen["claims"] = list(held), list(held.heed_claims)
            return StoreSink({}).submit(r)

    assert release_held(rid, holding_store=held, sink=Sink()).ok
    assert seen["holding"] == [] and [k.split("--")[0] for k in seen["claims"]] == [rid]
    assert list(held) == [] and list(held.heed_claims) == []
    if on_disk:
        assert (tmp_path / "held.claims").is_dir()


def test_recover_finds_the_claims_store_on_the_holding_store():
    held = make_holding_store()
    rid = _hold(held)
    held.heed_claims[f"{rid}--1000-{TOKEN}"] = held.pop(rid)  # a crash after unholding
    assert recover_claimed(holding_store=held) == [rid]
    assert rid in held and held.heed_claims == {}


def test_a_release_stamps_its_claim_with_the_clock():
    held = make_holding_store()
    rid = _hold(held)
    keys = []

    class Sink:
        def submit(self, r):
            keys.extend(held.heed_claims)
            return StoreSink({}).submit(r)

    release_held(rid, holding_store=held, sink=Sink(), clock=lambda: 1234.5)
    assert keys[0].startswith(f"{rid}--1234500-")


def test_stale_after_recovers_only_old_claims():
    held = make_holding_store()
    fresh, old, legacy = (_hold(held) for _ in range(3))
    claims = held.heed_claims
    claims[f"{fresh}--1000000-{TOKEN}"] = held.pop(fresh)  # claimed at t=1000 s
    claims[f"{old}--100000-{TOKEN}"] = held.pop(old)  # claimed at t=100 s
    claims[f"{legacy}--{TOKEN}"] = held.pop(legacy)  # before claims carried a time
    got = recover_claimed(holding_store=held, stale_after=600, clock=lambda: 1100)
    assert sorted(got) == sorted([old, legacy]) and fresh not in held
    assert recover_claimed(holding_store=held, stale_after=600, clock=lambda: 1700) == [
        fresh
    ]
    assert claims == {}


def test_without_stale_after_every_claim_is_recovered():
    held = make_holding_store()
    rid = _hold(held)
    held.heed_claims[f"{rid}--999999999999-{TOKEN}"] = held.pop(rid)  # far future
    assert recover_claimed(holding_store=held, clock=lambda: 0) == [rid]


def test_bad_arguments_are_refused():
    held = make_holding_store()
    with pytest.raises(ValueError, match="stale_after"):
        recover_claimed(holding_store=held, stale_after=-1)
    plain = {}
    with pytest.raises(ValueError, match="must not be the holding store"):
        recover_claimed(holding_store=plain, claimed_store=plain)


# ---------------------------------------------------------------------------
# Adversarial-review regressions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "clock", [time.time_ns, lambda: time.time() * 1000, lambda: -1.0, lambda: math.nan]
)
def test_a_clock_not_in_seconds_is_refused_before_claiming(clock):
    held = make_holding_store()
    rid = _hold(held)
    with pytest.raises(ValueError, match="clock"):
        release_held(rid, holding_store=held, sink=StoreSink({}), clock=clock)
    assert rid in held and held.heed_claims == {}


def test_a_release_whose_claim_is_recovered_meanwhile_puts_the_report_back():
    held = make_holding_store()
    rid = _hold(held)

    class RecoveringClaims(dict):
        def __setitem__(self, k, v):
            super().__setitem__(k, v)
            # another worker, its clock far ahead, recovers right after the claim
            recover_claimed(holding_store=held, stale_after=600, clock=lambda: 10**9)

    held.heed_claims = RecoveringClaims()
    live = {}
    with pytest.raises(KeyError, match="recovered by another worker"):
        release_held(rid, holding_store=held, sink=StoreSink(live))
    assert rid in held and live == {}


def test_legacy_claims_left_in_holding_are_recovered_and_never_released():
    held = make_holding_store()
    rid = _hold(held)
    legacy = f"claimed--{rid}--{TOKEN}"
    held[legacy] = held.pop(rid)  # a crash under an older heed
    with pytest.raises(ValueError, match="recover_claimed"):
        release_held(legacy, holding_store=held, sink=StoreSink({}))
    assert recover_claimed(holding_store=held) == [rid]
    assert list(held) == [rid]


def test_a_claim_stamped_far_in_the_future_is_stale():
    held = make_holding_store()
    rid = _hold(held)
    held.heed_claims[f"{rid}--999999999000-{TOKEN}"] = held.pop(rid)
    assert recover_claimed(holding_store=held, stale_after=600, clock=lambda: 1000) == [
        rid
    ]


def test_nan_stale_after_is_refused():
    with pytest.raises(ValueError, match="stale_after"):
        recover_claimed(holding_store=make_holding_store(), stale_after=math.nan)
