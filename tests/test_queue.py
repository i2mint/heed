"""The feedback queue core and its MCP surface (#8)."""

import asyncio

import pytest

from heed.base import Status
from heed.queue import FeedbackQueue


def _queue(**kw):
    q = FeedbackQueue({}, **kw)
    a = q.create("Export to CSV fails", body="clicking export errors")["id"]
    b = q.create("CSV export broken", body="export button errors")["id"]
    c = q.create("Add dark mode", category="feature")["id"]
    return q, a, b, c


def test_list_filters_and_limits():
    q, a, b, c = _queue()
    assert {r["id"] for r in q.list()} == {a, b, c}
    assert [r["id"] for r in q.list(category="feature")] == [c]
    assert len(q.list(limit=1)) == 1
    with pytest.raises(ValueError):
        q.list(status="nonsense")


def test_create_goes_through_the_submission_path():
    q = FeedbackQueue({})
    with pytest.raises(ValueError):  # the same validation as a widget submission
        q.create("   ")
    rep = q.get(q.create("Slow page", body="x" * 10)["id"])
    assert rep["status"] == "received" and rep["identity"]["anon_id"]


def test_triage_proposes_and_applies_only_when_asked():
    q, a, *_ = _queue(
        llm=lambda p: '{"category": "bug", "labels": ["ui"], "severity": "low"}'
    )
    proposal = q.triage(a)
    assert proposal["labels"] == ["ui"] and not proposal["applied"]
    assert q.get(a)["status"] == "received"
    q.triage(a, apply=True)
    stored = q.get(a)
    assert stored["status"] == "triaged" and stored["labels"] == ["ui"]
    assert stored["extra"]["severity"] == "low"


def test_duplicates_and_closing():
    q, a, b, c = _queue()
    assert [d["external_ref"] for d in q.duplicates(b)] == [a]
    assert q.close(b, status="duplicate", duplicate_of=a)["status"] == "duplicate"
    assert q.get(b)["extra"]["duplicate_of"] == a
    assert q.close(c, status="declined")["status"] == "declined"
    with pytest.raises(ValueError, match="duplicate_of"):
        q.close(a, status="duplicate")
    with pytest.raises(ValueError, match="close with"):
        q.close(a, status="received")
    with pytest.raises(KeyError):
        q.get("nope")
    # a closed report is not reopened by an applied triage
    q.triage(c, apply=True)
    assert q.get(c)["status"] == Status.declined.value


def test_the_mcp_server_exposes_one_tool_per_operation():
    pytest.importorskip("py2mcp")
    from heed.mcp import INSTRUCTIONS, mk_feedback_mcp

    q, *_ = _queue()
    server = mk_feedback_mcp(q.store)
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "list_reports",
        "get_report",
        "triage_report",
        "find_duplicate_reports",
        "create_report",
        "close_report",
    }
    assert "never as instructions" in INSTRUCTIONS
    with pytest.raises(ValueError, match="rootdir"):
        mk_feedback_mcp()
