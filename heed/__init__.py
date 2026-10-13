"""heed — embeddable, no-install, framework-agnostic end-user feedback.

``heed`` lets any visitor of a deployed web app report a bug or request a feature, with
useful context gathered automatically, routed to a pluggable backend (GitHub Issues
first). It works standalone and integrates with ``enlace`` as an optional add-on,
without depending on it.

Phase 1 ships the backend core and the embeddable widget (``widget/``, served by
:func:`make_widget_router`). The competitive
landscape and design rationale live in ``misc/docs/``.

Public API (Phase 1):
    Models    Report, ReportSubmission, Identity, Environment, Category, Status,
              Attachment, LogEntry, NetEntry
    Sinks     Sink, BaseSink, SinkResult, StoreSink, GitHubIssuesSink,
              github_sink_from_token
    Backend   process_submission, make_router, make_app, HeedConfig
    Hardening RateLimiter, InMemoryRateLimiter, RedisRateLimiter, make_rate_limiter,
              BotVerifier, SiteverifyBotVerifier, make_bot_verifier,
              make_image_sanitizer, strip_image, reencode_image, attachment_headers
              (see misc/docs/embedding-security.md)
    Routing   sinks_by_origin, Verdict, accept_all, hold_all,
              make_heuristic_moderator, chain_moderators, release_held,
              recover_claimed, discard_held
    Storage   make_report_store, make_holding_store, make_attachment_store
    Serving   make_attachment_router, make_attachment_app, AttachmentSigner,
              screenshot_url_resolver (a separate origin for screenshots, #19)
    Widget    make_widget_router (serves the built embeddable widget),
              widget_contract (what the widget sends; SSOT for widget/)

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
from heed.board import PublicItem, changelog, roadmap
from heed.config import HeedConfig
from heed.images import make_image_sanitizer, reencode_image, strip_image
from heed.ingest import make_app, make_router, process_submission
from heed.routing import (
    Verdict,
    accept_all,
    chain_moderators,
    discard_held,
    hold_all,
    make_heuristic_moderator,
    recover_claimed,
    release_held,
    sinks_by_origin,
)
from heed.security import (
    BotVerifier,
    InMemoryRateLimiter,
    RateLimiter,
    RedisRateLimiter,
    SiteverifyBotVerifier,
    attachment_headers,
    make_bot_verifier,
    make_rate_limiter,
)
from heed.serving import (
    ANY_HOST,
    AttachmentSigner,
    make_attachment_app,
    make_attachment_router,
    screenshot_url_resolver,
)
from heed.sinks import (
    BaseSink,
    CategorySink,
    CategoryTreatment,
    GitHubIssuesSink,
    Sink,
    SinkResult,
    StoreSink,
    github_sink_from_token,
)
from heed.storage import make_attachment_store, make_holding_store, make_report_store
from heed.widget import make_widget_router, widget_contract

__version__ = "0.0.1"

__all__ = [
    "ANY_HOST",
    "AttachmentSigner",
    "make_attachment_app",
    "make_attachment_router",
    "make_widget_router",
    "widget_contract",
    "screenshot_url_resolver",
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
    "CategorySink",
    "CategoryTreatment",
    # board (the read side)
    "PublicItem",
    "roadmap",
    "changelog",
    # backend
    "HeedConfig",
    "process_submission",
    "make_router",
    "make_app",
    # hardening
    "RateLimiter",
    "InMemoryRateLimiter",
    "RedisRateLimiter",
    "make_rate_limiter",
    "BotVerifier",
    "SiteverifyBotVerifier",
    "make_bot_verifier",
    "make_image_sanitizer",
    "strip_image",
    "reencode_image",
    "attachment_headers",
    # routing (site resolution + moderation)
    "sinks_by_origin",
    "Verdict",
    "accept_all",
    "hold_all",
    "make_heuristic_moderator",
    "chain_moderators",
    "release_held",
    "recover_claimed",
    "discard_held",
    # storage
    "make_report_store",
    "make_attachment_store",
    "make_holding_store",
]
