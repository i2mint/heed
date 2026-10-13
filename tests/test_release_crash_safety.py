"""``release_held`` claims by move, so a crash mid-release never loses a held report,
and ``recover_claimed`` returns stranded claims to holding (issue #17)."""

import pytest

from heed import (
    ReportSubmission,
    StoreSink,
    hold_all,
    make_report_store,
    process_submission,
    recover_claimed,
    release_held,
)
from heed.routing import CLAIM_PREFIX


class Crash(BaseException):
    """Stands in for the process dying (SIGKILL): nothing after it runs."""


class CrashOn(dict):
    """A dict that 'dies' on the first write or delete matching ``when``."""

    def __init__(self, when):
        super().__init__()
        self.when, self.armed = when, False

    def _maybe_crash(self, op, k):
        if self.armed and self.when(op, k):
            self.armed = False
            raise Crash(op, k)

    def __setitem__(self, k, v):
        self._maybe_crash("set", k)
        super().__setitem__(k, v)

    def __delitem__(self, k):
        self._maybe_crash("del", k)
        super().__delitem__(k)


def _hold(held):
    report, _ = process_submission(
        ReportSubmission(title="x"),
        StoreSink({}),
        moderator=hold_all,
        holding_sink=StoreSink(held),
    )
    return report.id


def _restart(held, rid, claimed_store=None):
    """After the 'crash', state is whatever the store holds: recover, then the report
    must be held again (exactly once) and releasable."""
    held.armed = False
    recover_claimed(holding_store=held, claimed_store=claimed_store)
    assert rid in held
    assert not [k for k in held if k.startswith(CLAIM_PREFIX)]
    assert not claimed_store
    live = {}
    kw = {} if claimed_store is None else {"claimed_store": claimed_store}
    assert release_held(rid, holding_store=held, sink=StoreSink(live), **kw).ok
    assert rid in live and rid not in held


@pytest.mark.parametrize(
    "when",
    [
        lambda op, k: op == "set" and k.startswith(CLAIM_PREFIX),  # writing the claim
        lambda op, k: op == "del" and not k.startswith(CLAIM_PREFIX),  # unholding
        lambda op, k: op == "del" and k.startswith(CLAIM_PREFIX),  # dropping the claim
    ],
    ids=["claim", "unhold", "drop-claim"],
)
def test_crash_at_any_step_of_a_release_loses_nothing(when):
    held = CrashOn(when)
    rid = _hold(held)
    held.armed = True
    with pytest.raises(Crash):
        release_held(rid, holding_store=held, sink=StoreSink({}))
    _restart(held, rid)


@pytest.mark.parametrize("ok", [False, True], ids=["sink-fails", "sink-dies"])
def test_crash_while_putting_a_failed_release_back_loses_nothing(ok):
    class Sink:
        def submit(self, r):
            held.armed = True  # die while putting it back / dropping the claim
            if ok:
                raise RuntimeError("down")
            return StoreSink({}).submit(r).model_copy(update={"ok": False})

    held = CrashOn(lambda op, k: True)
    rid = _hold(held)
    with pytest.raises(Crash):
        release_held(rid, holding_store=held, sink=Sink())
    _restart(held, rid)


def test_separate_claimed_store():
    held, claimed, live = {}, {}, {}
    rid = _hold(held)

    class Sink:
        def submit(self, r):
            assert claimed == {rid: claimed[rid]} and rid not in held
            return StoreSink(live).submit(r)

    assert release_held(rid, holding_store=held, sink=Sink(), claimed_store=claimed)
    assert rid in live and held == {} and claimed == {}


def test_recover_claimed_with_separate_store():
    held, claimed = {}, {}
    rid = _hold(held)
    claimed[rid] = held.pop(rid)  # what a crash right after unholding leaves
    assert recover_claimed(holding_store=held, claimed_store=claimed) == [rid]
    assert rid in held and claimed == {}


def test_a_claim_key_is_not_releasable():
    held = {}
    rid = _hold(held)
    held[CLAIM_PREFIX + rid] = held.pop(rid)
    with pytest.raises(ValueError, match="recover_claimed"):
        release_held(CLAIM_PREFIX + rid, holding_store=held, sink=StoreSink({}))


def test_file_backed_holding_store(tmp_path):
    held, live = make_report_store(str(tmp_path)), {}
    rid = _hold(held)
    assert release_held(rid, holding_store=held, sink=StoreSink(live)).ok
    assert rid in live and list(held) == []
