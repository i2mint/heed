"""Tests for the heed domain model."""

from heed import Category, Identity, Report, ReportSubmission, Status


def test_report_roundtrip():
    """A Report assigns defaults and round-trips through JSON unchanged."""
    r = Report(title="x", identity=Identity(anon_id="a1"))
    assert r.id and r.status is Status.received and r.category is Category.bug
    r2 = Report.model_validate_json(r.model_dump_json())
    assert r2 == r


def test_submission_defaults():
    """A submission defaults to a bug with no opt-in capture."""
    s = ReportSubmission(title="boom")
    assert s.category is Category.bug
    assert s.body == ""
    assert s.console is None and s.network is None
