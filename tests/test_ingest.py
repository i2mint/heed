"""Tests for the ingest core and the FastAPI router."""

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from heed import (
    HeedConfig,
    Report,
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


def _capture_client(**config):
    store: dict = {}
    app = FastAPI()
    app.include_router(make_router(StoreSink(store), config=HeedConfig(**config)))
    return TestClient(app), store


_CONSOLE = json.dumps(
    [{"level": "error", "message": "auth failed for ana@example.com token=s3cr3t"}]
)
_NETWORK = json.dumps(
    [{"method": "GET", "url": "https://u:p@api.ex/me?session=abc#x", "status": 401}]
)


def test_router_ignores_capture_unless_accepted():
    """Without the opt-in, console/network fields are neither parsed nor kept."""
    client, store = _capture_client()
    resp = client.post(
        "/report",
        data={"title": "t", "console": "not json", "network": _NETWORK},
        headers={"origin": "http://test"},
    )
    assert resp.status_code == 200, resp.text
    report = Report.model_validate_json(store[resp.json()["id"]])
    assert report.console is None and report.network is None


def test_router_redacts_accepted_capture():
    """Accepted capture is masked on the server whatever the widget sent."""
    client, store = _capture_client(accept_console=True, accept_network=True)
    resp = client.post(
        "/report",
        data={"title": "t", "console": _CONSOLE, "network": _NETWORK},
        headers={"origin": "http://test"},
    )
    assert resp.status_code == 200, resp.text
    report = Report.model_validate_json(store[resp.json()["id"]])
    assert report.console[0].message == "auth failed for [email] token=[redacted]"
    assert report.network[0].url == "https://api.ex/me?session=[redacted]"
    assert report.network[0].status == 401


def test_router_rejects_malformed_capture():
    """An accepted capture field that is not a JSON array is a 422 naming the field."""
    client, _ = _capture_client(accept_console=True)
    resp = client.post(
        "/report",
        data={"title": "t", "console": '{"level": "x"}'},
        headers={"origin": "http://test"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "invalid console: must be a JSON array"


def test_process_submission_uses_injected_redactor():
    """The redactor is a seam: a stricter one replaces the default."""
    from heed import LogEntry

    sub = ReportSubmission(title="x", console=[LogEntry(level="log", message="hello")])
    report, _ = process_submission(
        sub,
        StoreSink({}),
        config=HeedConfig(accept_console=True),
        redactor=lambda text: "*" * len(text),
    )
    assert report.console[0].message == "*****"


def test_failing_redactor_drops_capture():
    """A redactor that raises fails closed: the report keeps no capture."""
    from heed import LogEntry

    def broken(text):
        raise RuntimeError("boom")

    sub = ReportSubmission(title="x", console=[LogEntry(level="log", message="hi")])
    report, result = process_submission(
        sub, StoreSink({}), config=HeedConfig(accept_console=True), redactor=broken
    )
    assert result.ok and report.console is None


def test_page_url_is_masked_by_default_and_opt_out():
    """Id-like path segments, handles and query values never reach the sink (#60)."""
    url = "https://app.example/reset/9f8e7d6c5b4a3f2e/users/12345/@ana?t=s3cr3t#f"
    kept = "https://app.example/reset/9f8e7d6c5b4a3f2e/users/12345/@ana?t=s3cr3t#f"
    masked, _ = process_submission(ReportSubmission(title="x", page_url=url), StoreSink({}))
    assert masked.page_url == "https://app.example/reset/[id]/users/[id]/[user]?t=[redacted]"
    raw, _ = process_submission(
        ReportSubmission(title="x", page_url=url),
        StoreSink({}),
        config=HeedConfig(mask_page_url=False),
    )
    assert raw.page_url == kept
    empty, _ = process_submission(ReportSubmission(title="x"), StoreSink({}))
    assert empty.page_url == ""
