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


def test_a_wrapped_holding_store_claims_by_copy(tmp_path, monkeypatch):
    """A dol wrapper forwards the carried move's attribute, but its keys are not file
    names: the release must not rename relative to the working directory."""
    from dol import wrap_kvs

    held = make_holding_store(str(tmp_path / "held"))
    wrapped = wrap_kvs(held, key_decoder=lambda k: k, key_encoder=lambda k: k)
    rid = _hold(held)
    monkeypatch.chdir(tmp_path)
    (tmp_path / rid).write_text("DECOY")
    sink = _RecordingSink()
    assert release_held(rid, holding_store=wrapped, sink=sink).ok
    assert sink.sent == [rid] and rid not in held
    assert (tmp_path / rid).read_text() == "DECOY"


def test_rename_refuses_a_store_without_file_paths():
    from heed import rename_claim_move

    with pytest.raises(ValueError, match="rootdir"):
        rename_claim_move({"r1": "{}"}, "r1", {}, "r1--t")


def test_a_relative_rootdir_can_be_released(tmp_path, monkeypatch):
    """``make_holding_store('held/')`` (the form heed's own error hint suggests) must
    release: the carried rename needs paths it can resolve."""
    monkeypatch.chdir(tmp_path)
    held = make_holding_store("held/")
    rid = _hold(held)
    sink = _RecordingSink()
    assert release_held(rid, holding_store=held, sink=sink).ok
    assert sink.sent == [rid] and rid not in held
    assert not list(getattr(held, CLAIMS_ATTR))


def test_rename_accepts_a_relative_file_store(tmp_path, monkeypatch):
    from heed import make_report_store, rename_claim_move

    monkeypatch.chdir(tmp_path)
    (tmp_path / "h").mkdir()
    (tmp_path / "c").mkdir()
    held, claims = make_report_store("h/"), make_report_store("c/")
    held["r1"] = "{}"
    rename_claim_move(held, "r1", claims, "r1--t")
    assert "r1" not in held and claims["r1--t"] == "{}"


def test_a_put_back_is_atomic(tmp_path, monkeypatch):
    """A failed release puts the report back by a move, never by rewriting the file:
    a concurrent release must never read a half-written (e.g. empty) report."""
    import heed.routing as routing

    held = make_holding_store(str(tmp_path / "held"))
    rid = _hold(held)
    original = (tmp_path / "held" / rid).read_text()
    writes = []

    def no_rewrite(*args, **kwargs):
        writes.append(args)
        raise AssertionError("put back by rewriting the file")

    monkeypatch.setattr(type(held), "__setitem__", no_rewrite)

    class Failing(StoreSink):
        def submit(self, report):
            return routing.SinkResult(ok=False, detail="down")

    assert not release_held(rid, holding_store=held, sink=Failing({})).ok
    assert not writes
    assert (tmp_path / "held" / rid).read_text() == original
    assert not list(getattr(held, CLAIMS_ATTR))


@pytest.mark.parametrize("on_disk", [False, True], ids=["memory", "disk"])
def test_concurrent_releases_with_a_flaky_sink_never_tear_a_report(tmp_path, on_disk):
    """When the sink sometimes fails, a put back races other releases of the report:
    none of them may read or leave behind a torn report."""
    from heed.sinks.base import SinkResult

    held = make_holding_store(str(tmp_path / "held") if on_disk else None)
    lock = threading.Lock()

    class Flaky(_RecordingSink):
        calls = 0

        def submit(self, report):
            with lock:
                Flaky.calls += 1
                fail = Flaky.calls % 2 == 0
            if fail:
                return SinkResult(ok=False, detail="down")
            return super().submit(report)

    sink, n, errors = Flaky(), 8, []
    rids = [_hold(held) for _ in range(30)]
    start = threading.Barrier(n)

    def release():
        start.wait()
        for _ in range(3):
            for rid in rids:
                try:
                    release_held(rid, holding_store=held, sink=sink)
                except KeyError:
                    pass
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

    threads = [threading.Thread(target=release) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[:3]
    assert len(sink.sent) == len(set(sink.sent))
    for rid in held:
        assert held[rid], f"held report {rid} is empty"
    assert set(sink.sent) | set(held) == set(rids)


def test_a_one_way_claim_move_still_puts_a_failed_release_back():
    """A user's move may only work holding -> claims (e.g. fixed key prefixes): when
    it cannot run backwards, a failed release still puts the report back."""
    held, claims = {}, {}

    def one_way(src, report_id, dst, claim_key):
        if src is not held:
            raise KeyError(report_id)
        pop_claim_move(src, report_id, dst, claim_key)

    rid = _hold(held)

    class Failing(StoreSink):
        def submit(self, report):
            raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        release_held(
            rid,
            holding_store=held,
            sink=Failing({}),
            claimed_store=claims,
            claim_move=one_way,
        )
    assert rid in held and not claims


def test_a_home_relative_rootdir_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    held = make_holding_store("~/held")
    assert held.rootdir.startswith(str(tmp_path / "home"))
    rid = _hold(held)
    assert (tmp_path / "home" / "held" / rid).exists()
    assert not (tmp_path / "~").exists()
