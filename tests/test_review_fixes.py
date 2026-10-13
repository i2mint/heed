"""Fixes from the sprint review: a forged X-Forwarded-For line, a sink that fails, and
discarding a held report together with its screenshot."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from heed import HeedConfig, ReportSubmission, StoreSink, discard_held, make_router
from heed.ingest import process_submission
from heed.routing import CLAIM_PREFIX, hold_all
from heed.security import InMemoryRateLimiter
from heed.sinks.base import SinkResult

ORIGIN = "https://app.example"
PROXY = "127.0.0.1"


def _app(sink, **router_kwargs):
    config = router_kwargs.pop("config", None) or HeedConfig(allowed_origins=[ORIGIN])
    app = FastAPI()
    app.include_router(make_router(sink, config=config, **router_kwargs))
    return app


class _RaisingSink:
    def submit(self, report):
        raise ConnectionError("tracker is down")


class _RefusingSink:
    def submit(self, report):
        return SinkResult(ok=False, detail="refused")


# ------------------------------------------------------------ X-Forwarded-For


def test_a_forged_first_forwarded_line_does_not_choose_the_rate_limit_ip():
    config = HeedConfig(allowed_origins=[ORIGIN], trusted_proxies=[PROXY])
    app = _app(
        StoreSink({}), config=config, rate_limiter=InMemoryRateLimiter(max_per_window=1)
    )
    client = TestClient(app, client=(PROXY, 50000))

    def post(fake_ip):
        # The client sends its own line; the proxy appends the real address after it.
        headers = [
            ("origin", ORIGIN),
            ("x-forwarded-for", fake_ip),
            ("x-forwarded-for", "1.2.3.4"),
        ]
        return client.post("/report", data={"title": "x"}, headers=headers)

    assert post("9.9.9.1").status_code == 200
    assert post("9.9.9.2").status_code == 429  # same real client, new fake IP


# ----------------------------------------------------------------- sink failure


def test_a_failing_sink_is_a_502_the_widget_can_read():
    client = TestClient(_app(_RaisingSink()))
    resp = client.post("/report", data={"title": "x"}, headers={"origin": ORIGIN})
    assert resp.status_code == 502
    assert resp.headers["access-control-allow-origin"] == ORIGIN
    assert "tracker is down" not in resp.text  # no internals


@pytest.mark.parametrize("sink", [_RaisingSink(), _RefusingSink()])
def test_a_report_that_went_nowhere_leaves_no_screenshot(sink):
    attachments = {}
    submission = ReportSubmission(title="x")
    try:
        _, result = process_submission(
            submission, sink, screenshot=b"img", attachment_store=attachments
        )
        assert not result.ok
    except ConnectionError:
        pass
    assert attachments == {}


def test_a_delivered_report_keeps_its_screenshot():
    attachments = {}
    _, result = process_submission(
        ReportSubmission(title="x"),
        StoreSink({}),
        screenshot=b"img",
        attachment_store=attachments,
    )
    assert result.ok and len(attachments) == 1


# ---------------------------------------------------------------- discard_held


def _held(holding, attachments):
    report, _ = process_submission(
        ReportSubmission(title="spam"),
        StoreSink({}),
        screenshot=b"img",
        attachment_store=attachments,
        moderator=hold_all,
        holding_sink=StoreSink(holding),
    )
    return report.id


def test_discard_held_removes_the_report_and_its_screenshot():
    holding, attachments = {}, {}
    report_id = _held(holding, attachments)
    assert report_id in holding and attachments

    discard_held(report_id, holding_store=holding, attachment_store=attachments)

    assert holding == {} and attachments == {}
    with pytest.raises(KeyError):
        discard_held(report_id, holding_store=holding, attachment_store=attachments)


def test_discard_held_refuses_a_claim_key():
    with pytest.raises(ValueError, match="claim"):
        discard_held(CLAIM_PREFIX + "x--" + "0" * 32, holding_store={})


def test_discard_held_deletes_an_unparseable_report():
    holding = {"r1": "not json"}
    discard_held("r1", holding_store=holding, attachment_store={"r1.png": b"x"})
    assert holding == {}
