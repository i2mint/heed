"""heed — embeddable, no-install, framework-agnostic end-user feedback.

``heed`` lets any visitor of a deployed web app report a bug or request a feature, with
useful context gathered automatically, routed to a pluggable backend (GitHub Issues
first). It works standalone and integrates with ``enlace`` as an optional add-on,
without depending on it.

Phase 1 ships the backend core (the widget is tracked in issue #6). The competitive
landscape and design rationale live in ``misc/docs/``.

Public API (Phase 1):
    Models    Report, ReportSubmission, Identity, Environment, Category, Status,
              Attachment, LogEntry, NetEntry
    Sinks     Sink, BaseSink, SinkResult, StoreSink, GitHubIssuesSink,
              github_sink_from_token
    Backend   process_submission, make_router, make_app, HeedConfig
    Storage   make_report_store, make_attachment_store

Example:
    >>> from heed import StoreSink, ReportSubmission, process_submission
    >>> store = {}
    >>> report, result = process_submission(
    ...     ReportSubmission(title="Login button does nothing"), StoreSink(store)
    ... )
    >>> result.ok and report.id in store
    True
"""

from heed.base import (
    Attachment,
    Category,
    Environment,
    Identity,
    LogEntry,
    NetEntry,
    Report,
    ReportSubmission,
    Status,
    new_report_id,
)
from heed.config import HeedConfig
from heed.ingest import make_app, make_router, process_submission
from heed.sinks import (
    BaseSink,
    GitHubIssuesSink,
    Sink,
    SinkResult,
    StoreSink,
    github_sink_from_token,
)
from heed.storage import make_attachment_store, make_report_store

__version__ = "0.0.1"

__all__ = [
    "__version__",
    # models
    "Report",
    "ReportSubmission",
    "Identity",
    "Environment",
    "Category",
    "Status",
    "Attachment",
    "LogEntry",
    "NetEntry",
    "new_report_id",
    # sinks
    "Sink",
    "BaseSink",
    "SinkResult",
    "StoreSink",
    "GitHubIssuesSink",
    "github_sink_from_token",
    # backend
    "HeedConfig",
    "process_submission",
    "make_router",
    "make_app",
    # storage
    "make_report_store",
    "make_attachment_store",
]
