"""Tests for sinks (store + github)."""

from heed import Category, GitHubIssuesSink, Identity, Report, StoreSink


def _report(**kw) -> Report:
    kw.setdefault("title", "T")
    kw.setdefault("identity", Identity(anon_id="anon12345"))
    return Report(**kw)


def test_store_sink_persists_and_dedups():
    """StoreSink writes JSON keyed by id and finds same-title duplicates."""
    store: dict = {}
    sink = StoreSink(store)
    r1 = _report(title="same")
    res = sink.submit(r1)
    assert res.ok and store[r1.id]

    r2 = _report(title="same")
    sink.submit(r2)
    dups = sink.find_duplicates(r2)
    assert any(d.external_ref == r1.id for d in dups)


def test_github_sink_uses_injected_create_issue():
    """GitHubIssuesSink delegates to the injected callable and maps category->labels."""
    calls: dict = {}

    def fake_create_issue(*, title, body, labels):
        calls.update(title=title, body=body, labels=labels)
        return {"number": 42, "html_url": "https://github.com/x/y/issues/42"}

    sink = GitHubIssuesSink(fake_create_issue)
    r = _report(title="Login broken", category=Category.bug, body="it broke")
    res = sink.submit(r)

    assert res.ok and res.external_ref == "42" and res.url.endswith("/42")
    assert calls["title"] == "Login broken"
    assert "bug" in calls["labels"]
    assert "it broke" in calls["body"]
