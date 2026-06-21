"""Tests for the ingest core and the FastAPI router."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from heed import (
    HeedConfig,
    ReportSubmission,
    StoreSink,
    make_router,
    process_submission,
)


def test_process_submission_enriches_and_routes():
    """The core enriches a submission, stores the screenshot, and routes to the sink."""
    store: dict = {}
    attachments: dict = {}
    sink = StoreSink(store)
    sub = ReportSubmission(title="bug!", anon_id="widget-abc")

    report, result = process_submission(
        sub,
        sink,
        origin="https://app.example",
        screenshot=b"PNGDATA",
        attachment_store=attachments,
    )

    assert result.ok and report.id in store
    assert report.identity.anon_id == "widget-abc"
    assert report.origin == "https://app.example"
    assert report.attachments and report.attachments[0].ref in attachments


def test_process_submission_drops_optin_capture_by_default():
    """Console/network are dropped unless the config opts in (privacy by default)."""
    from heed import LogEntry

    sub = ReportSubmission(title="x", console=[LogEntry(level="error", message="boom")])
    report, _ = process_submission(sub, StoreSink({}))
    assert report.console is None  # default config does not accept console capture


def test_router_post_report():
    """POST /report validates, routes to the sink, and returns the new id."""
    store: dict = {}
    app = FastAPI()
    app.include_router(make_router(StoreSink(store), config=HeedConfig()))
    client = TestClient(app)

    resp = client.post(
        "/report",
        data={"title": "broken", "category": "bug"},
        headers={"origin": "http://test"},
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["ok"] and out["id"] in store


def test_router_blocks_disallowed_origin():
    """An origin allow-list rejects unknown origins with 403."""
    app = FastAPI()
    app.include_router(
        make_router(
            StoreSink({}), config=HeedConfig(allowed_origins=["https://ok.example"])
        )
    )
    client = TestClient(app)
    resp = client.post(
        "/report", data={"title": "x"}, headers={"origin": "https://evil.example"}
    )
    assert resp.status_code == 403
