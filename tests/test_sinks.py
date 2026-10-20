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


def test_github_sink_neutralises_mentions_and_refs():
    """@user and #123 in untrusted text get a zero-width joiner; stored report is intact."""
    calls: dict = {}

    def fake_create_issue(*, title, body, labels):
        calls.update(title=title, body=body)
        return {"number": 1, "html_url": "u"}

    r = _report(title="hi @octocat #5", body="ping @someone, see #123")
    GitHubIssuesSink(fake_create_issue).submit(r)
    for text in (calls["title"], calls["body"]):
        assert "@octocat" not in text and "@someone" not in text
        assert "#5" not in text and "#123" not in text
    assert "@‍someone" in calls["body"]
    assert r.body == "ping @someone, see #123"


def test_github_sink_fence_cannot_be_closed_by_console_text():
    """Console text containing ``` does not break out of the code fence."""
    from heed.sinks.github import render_issue_body

    r = _report(console=[{"level": "log", "message": "```\n<img src=x>"}])
    body = render_issue_body(r)
    assert "````\n[log] ```" in body
