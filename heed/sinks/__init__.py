"""Sinks: pluggable report destinations (strategy pattern).

Import the sink you need, or look one up by short name via :func:`get_sink_class`.
"""

from heed.sinks.base import BaseSink, DupCandidate, Sink, SinkResult
from heed.sinks.github import (
    GitHubIssuesSink,
    github_sink_from_token,
    render_issue_body,
)
from heed.sinks.store import StoreSink

_REGISTRY: dict[str, type] = {
    "store": StoreSink,
    "github": GitHubIssuesSink,
}


def get_sink_class(name: str) -> type:
    """Return a registered sink class by short name (e.g. 'github', 'store')."""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"Unknown sink {name!r}. Known: {sorted(_REGISTRY)}") from None


__all__ = [
    "Sink",
    "BaseSink",
    "SinkResult",
    "DupCandidate",
    "StoreSink",
    "GitHubIssuesSink",
    "github_sink_from_token",
    "render_issue_body",
    "get_sink_class",
]
