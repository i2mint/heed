"""The ingest layer — build a Report from a submission and route it to a sink.

``process_submission`` is the pure, framework-free core (dependency-injected sink,
stores, clock, id generator) so it is unit-testable without a server. ``make_router``
wraps it in a FastAPI ``APIRouter`` you can run standalone (``make_app``) or
``include_router`` into an enlace app. Identity is anonymous by default; pass a
``user_dependency`` to bind identity when an auth layer (e.g. enlace_auth) is present.

The router treats every request as hostile (see ``security.py`` and issue #5). Checks
run cheapest-first so abuse is rejected before it costs anything: CORS preflight →
origin allow-list → per-IP rate limit (both before ``user_dependency``) → body-size cap
(streamed, before parsing) → form limits → honeypot → bot gate → per-origin rate limit
→ model validation → screenshot sniffing.

NOTE: this module does not ``from __future__ import annotations`` — FastAPI resolves
route-handler annotations against module globals, so the request/response types are
imported at module level.
"""

import json
import logging
from collections.abc import Callable, MutableMapping
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from python_multipart.exceptions import MultipartParseError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

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
from heed.security import (
    IMAGE_EXTENSIONS,
    BotVerifier,
    RateLimiter,
    client_ip,
    cors_headers,
    make_bot_verifier,
    make_rate_limiter,
    normalize_origin,
    origin_allowed,
    parse_networks,
    preflight_headers,
    rate_limit_key,
    sniff_image_type,
)
from heed.sinks.base import Sink, SinkResult

_FORM_MEDIA_TYPES = ("multipart/form-data", "application/x-www-form-urlencoded")
_TEXT_FIELDS = ("title", "body", "category", "page_url", "env", "anon_id")
_MAX_LOC_CHARS = 64
logger = logging.getLogger("heed.ingest")
# Sent on every /report response, success or error.
_RESPONSE_HEADERS = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}


def process_submission(
    submission: ReportSubmission,
    sink: Sink,
    *,
    origin: str = "",
    user: Optional[str] = None,
    screenshot: Optional[bytes] = None,
    screenshot_media_type: str = "image/png",
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
        ext = IMAGE_EXTENSIONS.get(screenshot_media_type, "bin")
        key = f"{report_id}.{ext}"
        attachment_store[key] = screenshot
        attachments.append(
            Attachment(ref=key, size=len(screenshot), media_type=screenshot_media_type)
        )

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
    if not isinstance(env_obj, dict):
        raise ValueError("env must be a JSON object")
    return ReportSubmission(
        title=title,
        body=body,
        category=category,
        page_url=page_url,
        env=env_obj,
        anon_id=anon_id or None,
    )


async def read_capped_body(request: Request, *, max_bytes: int) -> Optional[bytes]:
    """Read the request body, or return None as soon as it exceeds ``max_bytes``.

    Checks ``Content-Length`` first, then counts streamed bytes (a chunked request
    has no length header), so an oversized body is never buffered whole.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > max_bytes:
                return None
        except ValueError:
            return None
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > max_bytes:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _safe_errors(error: ValidationError) -> list[dict[str, Any]]:
    """Validation errors without the input, and with location parts truncated
    (``env.extra`` keys are client-chosen and could be long or hostile)."""
    return [
        {
            "loc": [str(part)[:_MAX_LOC_CHARS] for part in err["loc"]],
            "msg": err["msg"],
            "type": err["type"],
        }
        for err in error.errors(include_url=False, include_input=False)
    ]


def _replay_request(request: Request, body: bytes) -> Request:
    """A Request over the same scope whose body is the already-read ``body``."""
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(request.scope, receive=receive)


_OPENAPI_REQUEST_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["title"],
                    "properties": {
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                        "category": {"type": "string"},
                        "page_url": {"type": "string"},
                        "env": {"type": "string", "description": "JSON object"},
                        "anon_id": {"type": "string"},
                        "bot_token": {"type": "string"},
                        "screenshot": {"type": "string", "format": "binary"},
                    },
                }
            }
        },
    }
}


def make_router(
    sink: Sink,
    *,
    config: Optional[HeedConfig] = None,
    attachment_store: Optional[MutableMapping] = None,
    rate_limiter: Optional[RateLimiter] = None,
    origin_rate_limiter: Optional[RateLimiter] = None,
    bot_verifier: Optional[BotVerifier] = None,
    user_dependency: Optional[Callable[..., Any]] = None,
) -> APIRouter:
    """Build a FastAPI APIRouter exposing ``POST /report`` (and its CORS preflight).

    Run standalone via :func:`make_app`, or ``include_router`` it into an enlace app.
    Provide ``user_dependency`` (a FastAPI dependency returning a user id or None) to
    bind identity when an auth layer like enlace_auth is present.

    Hardening defaults come from ``config``; any piece can be injected instead:
    ``rate_limiter`` (per client IP), ``origin_rate_limiter`` (per Origin) and
    ``bot_verifier`` (by default built from ``config.bot_secret``, i.e. off until a
    secret is configured).
    """
    cfg = config or HeedConfig()

    def _limiter(per_window: int, namespace: str) -> RateLimiter:
        return make_rate_limiter(
            cfg.rate_limit_backend,
            max_per_window=per_window,
            window_seconds=cfg.rate_limit_window_seconds,
            namespace=namespace,
            redis_url=cfg.redis_url,
        )

    ip_limiter = rate_limiter or _limiter(cfg.rate_limit_per_minute, "ip")
    if origin_rate_limiter is None and cfg.origin_rate_limit_per_minute is not None:
        origin_rate_limiter = _limiter(cfg.origin_rate_limit_per_minute, "origin")
    verifier = bot_verifier or make_bot_verifier(
        cfg.bot_gate, cfg.bot_secret, expected_hostnames=cfg.bot_expected_hostnames
    )
    trusted_networks = parse_networks(cfg.trusted_proxies)
    if cfg.allowed_origins is None:
        logger.warning(
            "heed: allowed_origins is not set, so any origin may submit reports. "
            "Set HeedConfig(allowed_origins=[...]) in production."
        )
    retry_after = str(int(cfg.rate_limit_window_seconds))
    router = APIRouter()

    async def _no_user() -> Optional[str]:
        return None

    user_dep = user_dependency or _no_user

    def _cors(origin: str) -> dict[str, str]:
        if not cfg.handle_cors:
            return dict(_RESPONSE_HEADERS)
        return {
            **_RESPONSE_HEADERS,
            **cors_headers(
                origin or None,
                cfg.allowed_origins,
                allow_credentials=cfg.cors_allow_credentials,
            ),
        }

    def _fail(status: int, detail: Any, origin: str, **extra_headers) -> HTTPException:
        return HTTPException(
            status_code=status,
            detail=detail,
            headers={**_cors(origin), **extra_headers},
        )

    if cfg.handle_cors:

        @router.options("/report", include_in_schema=False)
        async def report_preflight(request: Request):
            headers = preflight_headers(
                request.headers.get("origin"),
                cfg.allowed_origins,
                allow_credentials=cfg.cors_allow_credentials,
                max_age_seconds=cfg.cors_max_age_seconds,
            )
            if not headers:
                return Response(status_code=403)
            return Response(status_code=204, headers=headers)

    async def _parse_form(request: Request, origin: str) -> dict[str, Any]:
        media_type = request.headers.get("content-type", "").split(";")[0].strip()
        if media_type.lower() not in _FORM_MEDIA_TYPES:
            raise _fail(415, "expected multipart/form-data", origin)
        body = await read_capped_body(request, max_bytes=cfg.max_body_bytes)
        if body is None:
            raise _fail(413, "request body too large", origin)
        replay = _replay_request(request, body)
        try:
            form = await replay.form(
                max_files=1,
                max_fields=cfg.max_form_fields,
                max_part_size=cfg.max_field_bytes,
            )
        except (
            MultiPartException,
            MultipartParseError,
            StarletteHTTPException,
        ) as e:
            # malformed multipart, too many parts/files, a part over max_part_size
            raise _fail(400, "malformed form data", origin) from e
        try:
            if len(form) > cfg.max_form_fields + 1:
                raise _fail(400, "too many form fields", origin)
            fields: dict[str, Any] = {}
            for key, value in form.multi_items():
                if isinstance(value, UploadFile):
                    if key != "screenshot":
                        raise _fail(400, "unexpected file field", origin)
                    fields[key] = await value.read()
                elif key == "screenshot":
                    raise _fail(422, "screenshot must be a file upload", origin)
                else:
                    if len(value.encode()) > cfg.max_field_bytes:
                        raise _fail(413, "form field too large", origin)
                    fields[key] = value
            return fields
        finally:
            await form.close()

    async def _admit(request: Request) -> None:
        """Origin allow-list + per-IP limit. A route-level dependency, so it runs
        before ``user_dependency`` (which may hit an auth store) and before any body
        is read."""
        origin = request.headers.get("origin", "")
        if not origin_allowed(origin or None, cfg.allowed_origins):
            raise HTTPException(
                status_code=403, detail="origin not allowed", headers=_RESPONSE_HEADERS
            )
        ip = client_ip(
            request.client.host if request.client else None,
            request.headers.get("x-forwarded-for"),
            trusted_proxies=trusted_networks,
        )
        # Limiters may do network I/O (Redis): keep it off the event loop.
        bucket = rate_limit_key(ip, ipv6_prefix=cfg.ipv6_rate_limit_prefix)
        if not await run_in_threadpool(ip_limiter.allow, bucket):
            raise _fail(
                429, "rate limit exceeded", origin, **{"Retry-After": retry_after}
            )
        request.state.heed_origin = origin
        request.state.heed_ip = ip

    @router.post(
        "/report",
        dependencies=[Depends(_admit)],
        openapi_extra=_OPENAPI_REQUEST_BODY,
    )
    async def submit_report(
        request: Request,
        user: Optional[str] = Depends(user_dep),
    ):
        origin, ip = request.state.heed_origin, request.state.heed_ip
        fields = await _parse_form(request, origin)

        if cfg.honeypot_field and fields.get(cfg.honeypot_field):
            # Look like success so the bot learns nothing; route nowhere.
            return JSONResponse(
                {"id": new_report_id(), "ok": True, "ref": None, "url": None},
                headers=_cors(origin),
            )

        if verifier is not None:
            token = fields.get(cfg.bot_token_field)
            token = token if isinstance(token, str) else None
            human = await run_in_threadpool(verifier.verify, token, remote_ip=ip)
            if not human:
                raise _fail(403, "bot verification failed", origin)

        # Per-origin limit only counts submissions that passed the honeypot and the
        # bot gate, so spoofed-Origin junk cannot exhaust a real site's budget as
        # cheaply. (Origin is a client-set header; see embedding-security.md.)
        if origin_rate_limiter is not None:
            origin_key = normalize_origin(origin) if origin else "-"
            if not await run_in_threadpool(origin_rate_limiter.allow, origin_key):
                raise _fail(
                    429, "rate limit exceeded", origin, **{"Retry-After": retry_after}
                )

        if not isinstance(fields.get("title"), str):
            raise _fail(422, "title is required", origin)
        text = {k: fields.get(k) for k in _TEXT_FIELDS}
        if any(v is not None and not isinstance(v, str) for v in text.values()):
            raise _fail(422, "unexpected file in a text field", origin)
        try:
            submission = _build_submission(
                title=text["title"],
                body=text["body"] or "",
                category=text["category"] or "bug",
                page_url=text["page_url"] or "",
                env=text["env"] or "{}",
                anon_id=text["anon_id"],
            )
        except ValidationError as e:
            detail = _safe_errors(e)
            raise _fail(422, detail, origin) from e
        except (ValueError, RecursionError) as e:
            raise _fail(422, "invalid env: must be a JSON object", origin) from e

        shot = fields.get("screenshot") or None
        shot_type = "image/png"
        if shot is not None:
            if len(shot) > cfg.max_screenshot_bytes:
                raise _fail(413, "screenshot too large", origin)
            shot_type = sniff_image_type(shot)
            if shot_type is None:
                raise _fail(415, "screenshot must be a PNG, JPEG or WebP image", origin)

        report, result = await run_in_threadpool(
            process_submission,
            submission,
            sink,
            origin=origin,
            user=user,
            screenshot=shot,
            screenshot_media_type=shot_type,
            attachment_store=attachment_store,
            config=cfg,
        )
        return JSONResponse(
            {
                "id": report.id,
                "ok": result.ok,
                "ref": result.external_ref,
                "url": result.url,
            },
            headers=_cors(origin),
        )

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
