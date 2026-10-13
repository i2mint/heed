"""Tests for server-side site resolution and moderation/holding (issue #5)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from heed import (
    HeedConfig,
    ReportSubmission,
    Status,
    StoreSink,
    Verdict,
    chain_moderators,
    hold_all,
    make_heuristic_moderator,
    make_router,
    process_submission,
    release_held,
    sinks_by_origin,
)

A, B = "https://a.example", "https://b.example"


def _client(sink=None, **kw):
    app = FastAPI()
    app.include_router(make_router(sink, config=HeedConfig(), **kw))
    return TestClient(app)


def _post(client, origin, data=None):
    return client.post(
        "/report", data=data or {"title": "x"}, headers={"origin": origin}
    )


def test_origin_resolves_to_its_own_sink():
    store_a, store_b = {}, {}
    resolve = sinks_by_origin({A: StoreSink(store_a), B: StoreSink(store_b)})
    client = _client(resolve_sink=resolve)
    assert _post(client, A).status_code == 200
    assert _post(client, B).status_code == 200
    assert len(store_a) == len(store_b) == 1


def test_unknown_site_refused_before_body():
    client = _client(resolve_sink=sinks_by_origin({A: StoreSink({})}))
    resp = _post(client, "https://other.example")
    assert resp.status_code == 403 and resp.json()["detail"] == "unknown site"


def test_browser_cannot_name_the_target():
    """Extra form fields (repo, sink, target) are ignored; the origin decides."""
    store_a, store_b = {}, {}
    resolve = sinks_by_origin({A: StoreSink(store_a), B: StoreSink(store_b)})
    client = _client(resolve_sink=resolve)
    _post(client, A, data={"title": "x", "repo": "b", "sink": B, "target": B})
    assert len(store_a) == 1 and store_b == {}


def test_fallback_sink_used_for_unmapped_origin():
    fallback = {}
    client = _client(StoreSink(fallback), resolve_sink=sinks_by_origin({}))
    assert _post(client, A).status_code == 200 and len(fallback) == 1


def test_router_config_errors():
    with pytest.raises(ValueError, match="sink"):
        make_router(None)
    with pytest.raises(ValueError, match="holding_sink"):
        make_router(StoreSink({}), moderator=hold_all)


def test_held_reports_park_and_release():
    live, held = {}, {}
    client = _client(StoreSink(live), moderator=hold_all, holding_sink=StoreSink(held))
    resp = _post(client, A)
    out = resp.json()
    assert out["status"] == "held" and out["ref"] is None
    assert live == {} and out["id"] in held

    result = release_held(out["id"], holding_store=held, sink=StoreSink(live))
    assert result.ok and held == {} and out["id"] in live
    with pytest.raises(KeyError):
        release_held(out["id"], holding_store=held, sink=StoreSink(live))


def test_failed_release_keeps_report_held():
    from heed import SinkResult

    class Down:
        def submit(self, report):
            return SinkResult(ok=False, detail="down")

    held = {}
    report, _ = process_submission(
        ReportSubmission(title="x"),
        StoreSink({}),
        moderator=hold_all,
        holding_sink=StoreSink(held),
    )
    assert report.status is Status.held
    assert not release_held(report.id, holding_store=held, sink=Down()).ok
    assert report.id in held


def test_rejected_reports_look_received_but_go_nowhere():
    live, held = {}, {}
    client = _client(
        StoreSink(live),
        moderator=lambda r: Verdict.reject,
        holding_sink=StoreSink(held),
    )
    out = _post(client, A).json()
    assert out["ok"] and out["status"] == "received"
    assert live == {} and held == {}


def test_heuristic_and_chain():
    mod = chain_moderators(
        make_heuristic_moderator(max_links=0),
        make_heuristic_moderator(blocked_terms=["viagra"], on_blocked_term="reject"),
    )
    live, held = {}, {}
    client = _client(StoreSink(live), moderator=mod, holding_sink=StoreSink(held))
    assert _post(client, A, {"title": "plain"}).json()["status"] == "received"
    assert _post(client, A, {"title": "see www.x.biz"}).json()["status"] == "held"
    _post(client, A, {"title": "cheap VIAGRA www.x.biz"})
    assert len(live) == 1 and len(held) == 1
