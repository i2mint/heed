"""The ingest layer — build a Report from a submission and route it to a sink.

``process_submission`` is the pure, framework-free core (dependency-injected sink,
stores, clock, id generator) so it is unit-testable without a server. ``make_router``
wraps it in a FastAPI ``APIRouter`` you can run standalone (``make_app``) or
``include_router`` into an enlace app. Identity is anonymous by default; pass a
``user_dependency`` to bind identity when an auth layer (e.g. enlace_auth) is present.

NOTE: this module does not ``from __future__ import annotations`` — FastAPI resolves
route-handler annotations against module globals, so the request/response types are
imported at module level.
"""

import json
from collections.abc import Callable, MutableMapping
from typing import Any, Optional

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)

from heed.base import (
    Attachment,
    Identity,
    Report,
    ReportSubmission,
    Status,
    new_report_id,
    utcnow,
)
from heed.config import HeedConfig
from heed.security import InMemoryRateLimiter, RateLimiter, origin_allowed
from heed.sinks.base import Sink, SinkResult


def process_submission(
    submission: ReportSubmission,
    sink: Sink,
    *,
    origin: str = "",
    user: Optional[str] = None,
    screenshot: Optional[bytes] = None,
    attachment_store: Optional[MutableMapping] = None,
    config: Optional[HeedConfig] = None,
    id_gen: Callable[[], str] = new_report_id,
    clock: Callable[[], Any] = utcnow,
) -> tuple[Report, SinkResult]:
    """Enrich ``submission`` into a Report, store any screenshot, and route to ``sink``.

    Returns the (report, sink-result) pair. The server owns id/created_at/identity/
    origin/status — never the client. Opt-in capture (console/network) is dropped unless
    enabled in ``config``.
    """
    config = config or HeedConfig()
    report_id = id_gen()

    attachments: list[Attachment] = []
    if screenshot is not None and attachment_store is not None:
        key = f"{report_id}.png"
        attachment_store[key] = screenshot
        attachments.append(Attachment(ref=key, size=len(screenshot)))

    report = Report(
        id=report_id,
        created_at=clock(),
        category=submission.category,
        title=submission.title,
        body=submission.body,
        page_url=submission.page_url,
        env=submission.env,
        attachments=attachments,
        console=submission.console if config.accept_console else None,
        network=submission.network if config.accept_network else None,
        identity=Identity(anon_id=submission.anon_id or report_id, user=user),
        origin=origin,
        status=Status.received,
    )
    result = sink.submit(report)
    return report, result


def _build_submission(
    *,
    title: str,
    body: str,
    category: str,
    page_url: str,
    env: str,
    anon_id: Optional[str],
) -> ReportSubmission:
    env_obj = json.loads(env) if env else {}
    return ReportSubmission(
        title=title,
        body=body,
        category=category,
        page_url=page_url,
        env=env_obj,
        anon_id=anon_id,
    )


def make_router(
    sink: Sink,
    *,
    config: Optional[HeedConfig] = None,
    attachment_store: Optional[MutableMapping] = None,
    rate_limiter: Optional[RateLimiter] = None,
    user_dependency: Optional[Callable[..., Any]] = None,
) -> APIRouter:
    """Build a FastAPI APIRouter exposing ``POST /report``.

    Run standalone via :func:`make_app`, or ``include_router`` it into an enlace app.
    Provide ``user_dependency`` (a FastAPI dependency returning a user id or None) to
    bind identity when an auth layer like enlace_auth is present.
    """
    cfg = config or HeedConfig()
    limiter = rate_limiter or InMemoryRateLimiter(
        max_per_window=cfg.rate_limit_per_minute
    )
    router = APIRouter()

    async def _no_user() -> Optional[str]:
        return None

    user_dep = user_dependency or _no_user

    @router.post("/report")
    async def submit_report(
        request: Request,
        title: str = Form(...),
        body: str = Form(""),
        category: str = Form("bug"),
        page_url: str = Form(""),
        env: str = Form("{}"),
        anon_id: Optional[str] = Form(None),
        screenshot: Optional[UploadFile] = File(None),
        user: Optional[str] = Depends(user_dep),
    ):
        origin = request.headers.get("origin", "")
        if not origin_allowed(origin or None, cfg.allowed_origins):
            raise HTTPException(status_code=403, detail="origin not allowed")
        client_key = request.client.host if request.client else "unknown"
        if not limiter.allow(client_key):
            raise HTTPException(status_code=429, detail="rate limit exceeded")

        try:
            submission = _build_submission(
                title=title,
                body=body,
                category=category,
                page_url=page_url,
                env=env,
                anon_id=anon_id,
            )
        except Exception as e:
            raise HTTPException(
                status_code=422, detail=f"invalid submission: {e}"
            ) from e

        shot: Optional[bytes] = None
        if screenshot is not None:
            shot = await screenshot.read()
            if len(shot) > cfg.max_screenshot_bytes:
                raise HTTPException(status_code=413, detail="screenshot too large")

        report, result = process_submission(
            submission,
            sink,
            origin=origin,
            user=user,
            screenshot=shot,
            attachment_store=attachment_store,
            config=cfg,
        )
        return {
            "id": report.id,
            "ok": result.ok,
            "ref": result.external_ref,
            "url": result.url,
        }

    return router


def make_app(
    sink: Sink,
    *,
    config: Optional[HeedConfig] = None,
    attachment_store: Optional[MutableMapping] = None,
    prefix: str = "/heed",
    **router_kwargs: Any,
) -> FastAPI:
    """A standalone FastAPI app serving the heed router under ``prefix``."""
    app = FastAPI(title="heed")
    app.include_router(
        make_router(
            sink, config=config, attachment_store=attachment_store, **router_kwargs
        ),
        prefix=prefix,
    )
    return app
