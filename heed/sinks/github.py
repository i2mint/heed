"""A sink that turns a report into a GitHub issue.

The issue-creating call is dependency-injected (``create_issue``) so the sink is
testable without network access and so the credential-brokering strategy (a GitHub App
minting short-lived installation tokens, or a server-side token) is pluggable. A
convenience factory builds a ``create_issue`` from a token via ``ghapi`` (lazy import,
the ``heed[github]`` extra).

Hard platform limit (see misc/docs/design.md): there is no GitHub API to attach an image
to an issue. Resolve a ``screenshot_url`` (committed-file / attachment-store URL) and
embed it in the body; base64 data-URIs do not render.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from heed.base import Category, Report
from heed.sinks.base import BaseSink, SinkResult

# A callable that creates an issue and returns the GitHub API's issue dict.
CreateIssue = Callable[..., Mapping[str, Any]]

DEFAULT_LABELS: dict[Category, tuple[str, ...]] = {
    Category.bug: ("bug",),
    Category.feature: ("enhancement",),
    Category.question: ("question",),
    Category.other: (),
}


class GitHubIssuesSink(BaseSink):
    """Route a report to a GitHub issue via an injected ``create_issue`` callable."""

    def __init__(
        self,
        create_issue: CreateIssue,
        *,
        label_map: Mapping[Category, tuple[str, ...]] | None = None,
        screenshot_url: Callable[[Report], str | None] | None = None,
    ):
        self._create_issue = create_issue
        self._label_map = dict(label_map or DEFAULT_LABELS)
        self._screenshot_url = screenshot_url

    def submit(self, report: Report) -> SinkResult:
        labels = list(self._label_map.get(report.category, ())) + list(report.labels)
        url = self._screenshot_url(report) if self._screenshot_url else None
        body = render_issue_body(report, screenshot_url=url)
        issue = self._create_issue(title=report.title, body=body, labels=labels)
        number = issue.get("number")
        return SinkResult(
            ok=True,
            external_ref=str(number) if number is not None else None,
            url=issue.get("html_url"),
        )


def render_issue_body(report: Report, *, screenshot_url: str | None = None) -> str:
    """Render a report as a GitHub-flavoured markdown issue body."""
    lines: list[str] = []
    if report.body:
        lines += [report.body, ""]

    reporter = report.identity.user or f"anonymous ({report.identity.anon_id[:8]}…)"
    meta = [f"- **Category**: {report.category.value}"]
    if report.page_url:
        meta.append(f"- **URL**: {report.page_url}")
    if report.env.browser or report.env.os:
        env_line = f"- **Env**: {report.env.browser or '?'} on {report.env.os or '?'}"
        if report.env.viewport:
            env_line += f" ({report.env.viewport})"
        meta.append(env_line)
    meta.append(f"- **Reporter**: {reporter}")
    lines += meta + [""]

    if screenshot_url:
        lines += [f"![screenshot]({screenshot_url})", ""]
    if report.console:
        lines += _collapsible(
            "Console", "\n".join(f"[{e.level}] {e.message}" for e in report.console)
        )
    if report.network:
        lines += _collapsible(
            "Network",
            "\n".join(
                f"{e.method} {e.url} -> {e.status if e.status is not None else '?'}"
                for e in report.network
            ),
        )
    return "\n".join(lines).rstrip() + "\n"


def _collapsible(summary: str, content: str) -> list[str]:
    return [
        "<details>",
        f"<summary>{summary}</summary>",
        "",
        "```",
        content,
        "```",
        "",
        "</details>",
        "",
    ]


def github_sink_from_token(repo: str, token: str, **kwargs) -> GitHubIssuesSink:
    """Build a GitHubIssuesSink that creates issues in ``repo`` ('owner/name').

    Requires the ``heed[github]`` extra. ``token`` is a server-side credential (a GitHub
    App installation token or a PAT) — never shipped to the browser.
    """
    try:
        from ghapi.all import GhApi
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "GitHubIssuesSink via token needs the 'github' extra: "
            "pip install 'heed[github]'"
        ) from e

    owner, _, name = repo.partition("/")
    api = GhApi(owner=owner, repo=name, token=token)

    def create_issue(*, title: str, body: str, labels: list[str]) -> Mapping[str, Any]:
        return api.issues.create(title=title, body=body, labels=labels)

    return GitHubIssuesSink(create_issue, **kwargs)
