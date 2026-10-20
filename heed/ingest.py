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
→ model validation → screenshot sniffing → screenshot sanitizing (``images.py``: only
the image survives, so polyglot payloads and EXIF/GPS metadata are never stored).

NOTE: this module does not ``from __future__ import annotations`` — FastAPI resolves
route-handler annotations against module globals, so the request/response types are
imported at module level.
"""

import json
import logging
from collections.abc import Callable, MutableMapping
from typing import Any, Optional
from urllib.parse import urlsplit

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
    Environment,
    Identity,
    Report,
    ReportSubmission,
    Status,
    new_report_id,
    utcnow,
)
from heed.config import HeedConfig
from heed.images import (
    BUSY_RETRY_AFTER_SECONDS,
    ImageSanitizer,
    SanitizerBusy,
    make_image_sanitizer,
)
from heed.redact import (
    Redactor,
    redact_console,
    redact_network,
    redact_text,
    redact_url,
)
from heed.routing import Moderator, SinkResolver, Verdict, accept_all
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
from heed.widget import make_widget_router

_FORM_MEDIA_TYPES = ("multipart/form-data", "application/x-www-form-urlencoded")
_TEXT_FIELDS = (
    "title",
    "body",
    "category",
    "page_url",
    "env",
    "anon_id",
    "console",
    "network",
)
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
    moderator: Moderator = accept_all,
    holding_sink: Optional[Sink] = None,
    redactor: Redactor = redact_text,
) -> tuple[Report, SinkResult]:
    """Enrich ``submission`` into a Report, store any screenshot, and route to ``sink``.

    Returns the (report, sink-result) pair. The server owns id/created_at/identity/
    origin/status — never the client. Opt-in capture (console/network) is dropped
    unless enabled in ``config``, and what is kept is passed through ``redactor``
    (personal data and secrets masked, see ``redact.py``) whatever the widget did.
    ``moderator`` decides first: ``hold`` sends the report
    (status ``held``) to ``holding_sink`` instead, ``reject`` drops it (status
    ``declined``).
    """
    config = config or HeedConfig()
    report_id = id_gen()

    light = submission.category.value in config.light_categories
    if light:
        submission = submission.model_copy(
            update={
                "env": Environment(extra=submission.env.extra),
                "console": None,
                "network": None,
            }
        )
        screenshot = None  # nothing would use it, so it is not kept
    attachments: list[Attachment] = []
    if screenshot is not None and attachment_store is not None:
        ext = IMAGE_EXTENSIONS.get(screenshot_media_type, "bin")
        key = f"{report_id}.{ext}"
        attachment_store[key] = screenshot
        attachments.append(
            Attachment(ref=key, size=len(screenshot), media_type=screenshot_media_type)
        )

    try:
        report = _new_report(
            submission,
            report_id,
            attachments,
            origin=origin,
            user=user,
            config=config,
            clock=clock,
            redactor=redactor,
        )
    except Exception:  # nothing will reference the screenshot: don't keep it
        _discard_attachments(attachments, attachment_store)
        raise
    verdict = _moderate(moderator, report)
    if verdict is Verdict.reject:
        _discard_attachments(
            attachments, attachment_store
        )  # a rejected report keeps nothing
        report = report.model_copy(
            update={"status": Status.declined, "attachments": []}
        )
        return report, SinkResult(ok=True, detail="rejected by moderation")
    if verdict is Verdict.hold:
        if holding_sink is None:
            raise ValueError("moderator returned 'hold' but no holding_sink was given")
        report = report.model_copy(update={"status": Status.held})
        sink = holding_sink
    # A sink that raises may already have acted (an issue created, then a timeout on the
    # reply), and the report it made links the screenshot: keep it. One that returns
    # ok=False took nothing, so the screenshot has nothing to belong to.
    result = sink.submit(report)
    if not result.ok:
        _discard_attachments(attachments, attachment_store)
    return report, result


def _new_report(
    submission, report_id, attachments, *, origin, user, config, clock, redactor
) -> Report:
    """The Report for ``submission``. The server owns id, time, identity and origin."""
    console = submission.console if config.accept_console else None
    network = submission.network if config.accept_network else None
    try:
        console = redact_console(console, redactor=redactor) if console else None
        network = redact_network(network, redactor=redactor) if network else None
    except Exception:  # noqa: BLE001 - fail closed: no capture rather than unmasked
        logger.exception("heed: the redactor failed; dropping console/network capture")
        console = network = None
    return Report(
        id=report_id,
        created_at=clock(),
        category=submission.category,
        title=submission.title,
        body=submission.body,
        page_url=_page_url(submission.page_url, config, redactor),
        env=submission.env,
        attachments=attachments,
        console=console,
        network=network,
        identity=Identity(anon_id=submission.anon_id or report_id, user=user),
        origin=origin,
        status=Status.received,
    )


    """``page_url`` with tokens/ids masked (``config.mask_page_url``); fail closed."""
    """``page_url`` with tokens/ids masked (see ``config.mask_page_url``); fail closed."""
    if not page_url or not config.mask_page_url:
        return page_url
    try:
        urlsplit(page_url)  # redact_url would hand an unparseable URL to the redactor
        return redact_url(page_url, redactor=redactor)
    except Exception:  # noqa: BLE001 - an unmaskable URL is dropped, not sent raw
        logger.exception("heed: the redactor failed; dropping page_url")
        return ""


def _discard_attachments(attachments, attachment_store) -> None:
    for attachment in attachments:
        attachment_store.pop(attachment.ref, None)


def _moderate(moderator: Moderator, report: Report) -> Verdict:
    """Run ``moderator``; a crash or an invalid answer holds the report (fail safe)."""
    try:
        return Verdict(moderator(report))
    except Exception:  # noqa: BLE001 - operator code must not 500 the endpoint
        logger.exception("heed: moderator failed; holding report %s", report.id)
        return Verdict.hold


def _build_submission(
    *,
    title: str,
    body: str,
    category: str,
    page_url: str,
    env: str,
    anon_id: Optional[str],
    console: Optional[str] = None,
    network: Optional[str] = None,
) -> ReportSubmission:
    """The submission for the form's text fields. A malformed JSON field raises
    ``_BadJsonField`` naming it; a well-formed one that breaks the model raises
    ``ValidationError``."""
    env_obj = _json_field("env", env, dict) if env else {}
    return ReportSubmission(
        title=title,
        body=body,
        category=category,
        page_url=page_url,
        env=env_obj,
        console=_json_field("console", console, list) if console else None,
        network=_json_field("network", network, list) if network else None,
        anon_id=anon_id or None,
    )


class _BadJsonField(ValueError):
    """A form field that should hold JSON of a given shape does not."""


def _json_field(name: str, raw: str, kind: type) -> Any:
    shape = "a JSON object" if kind is dict else "a JSON array"
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError) as e:
        raise _BadJsonField(f"invalid {name}: must be {shape}") from e
    if not isinstance(value, kind):
        raise _BadJsonField(f"invalid {name}: must be {shape}")
    return value


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
                        "console": {
                            "type": "string",
                            "description": "JSON array of LogEntry (opt-in capture)",
                        },
                        "network": {
                            "type": "string",
                            "description": "JSON array of NetEntry (opt-in capture)",
                        },
                        "bot_token": {"type": "string"},
                        "screenshot": {"type": "string", "format": "binary"},
                    },
                }
            }
        },
    }
}


def make_router(
    sink: Optional[Sink],
    *,
    config: Optional[HeedConfig] = None,
    attachment_store: Optional[MutableMapping] = None,
    rate_limiter: Optional[RateLimiter] = None,
    origin_rate_limiter: Optional[RateLimiter] = None,
    bot_verifier: Optional[BotVerifier] = None,
    user_dependency: Optional[Callable[..., Any]] = None,
    resolve_sink: Optional[SinkResolver] = None,
    moderator: Optional[Moderator] = None,
    holding_sink: Optional[Sink] = None,
    image_sanitizer: Optional[ImageSanitizer] = None,
    redactor: Redactor = redact_text,
) -> APIRouter:
    """Build a FastAPI APIRouter exposing ``POST /report`` (and its CORS preflight).

    Run standalone via :func:`make_app`, or ``include_router`` it into an enlace app.
    Provide ``user_dependency`` (a FastAPI dependency returning a user id or None) to
    bind identity when an auth layer like enlace_auth is present.

    Hardening defaults come from ``config``; any piece can be injected instead:
    ``rate_limiter`` (per client IP), ``origin_rate_limiter`` (per Origin) and
    ``bot_verifier`` (by default built from ``config.bot_secret``, i.e. off until a
    secret is configured) and ``image_sanitizer`` (by default from
    ``config.screenshot_sanitizer``: re-encode with Pillow, else a pure-Python strip).
    Opt-in console/network capture (``config.accept_console``/``accept_network``) is
    masked by ``redactor`` before any sink sees it; a field the config does not accept
    is never parsed.

    Routing is decided server-side, never by the browser: ``resolve_sink`` maps the
    validated origin to that site's sink (``sink`` is then the fallback and may be
    None; an origin resolving to no sink gets 403). ``moderator`` may hold reports in
    ``holding_sink`` (required with a moderator) or reject them; see ``routing.py``.
    """
    cfg = config or HeedConfig()
    if sink is None and resolve_sink is None:
        raise ValueError("make_router needs a sink, a resolve_sink, or both")
    if moderator is not None and holding_sink is None:
        raise ValueError(
            "a moderator needs a holding_sink to park held reports in (a failing "
            "moderator also holds), e.g. holding_sink=StoreSink(make_holding_store("
            "'held/'))"
        )
    if holding_sink is not None and moderator is None:
        raise ValueError(
            "holding_sink was given without a moderator, so nothing would ever be "
            "held; pass moderator=hold_all (or another moderator)"
        )
    if resolve_sink is not None and cfg.allowed_origins:
        unresolved = [o for o in cfg.allowed_origins if resolve_sink(o) is None]
        if unresolved and sink is None:
            logger.warning(
                "heed: allowed origins with no sink (they will get 403): %s",
                unresolved,
            )

    def _sink_for(origin: str) -> Optional[Sink]:
        if resolve_sink is None:
            return sink
        resolved = resolve_sink(origin)
        return resolved if resolved is not None else sink

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
    sanitize_image = image_sanitizer or make_image_sanitizer(
        cfg.screenshot_sanitizer,
        max_pixels=cfg.max_screenshot_pixels,
        max_concurrent=cfg.max_concurrent_reencodes,
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
            # Every X-Forwarded-For line, in order: a proxy appends its own line, so
            # reading only the first would let the client's forged line choose the IP.
            ", ".join(request.headers.getlist("x-forwarded-for")) or None,
            trusted_proxies=trusted_networks,
        )
        # Limiters may do network I/O (Redis): keep it off the event loop.
        bucket = rate_limit_key(ip, ipv6_prefix=cfg.ipv6_rate_limit_prefix)
        if not await run_in_threadpool(ip_limiter.allow, bucket):
            raise _fail(
                429, "rate limit exceeded", origin, **{"Retry-After": retry_after}
            )
        site_sink = _sink_for(origin)
        if site_sink is None:
            # Same answer as a disallowed origin (no CORS echo), so a page cannot
            # probe which sites this backend serves.
            raise HTTPException(
                status_code=403, detail="origin not allowed", headers=_RESPONSE_HEADERS
            )
        request.state.heed_origin = origin
        request.state.heed_ip = ip
        request.state.heed_sink = site_sink

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
                {
                    "id": new_report_id(),
                    "ok": True,
                    "ref": None,
                    "url": None,
                    "status": Status.received.value,
                },
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
                console=text["console"] if cfg.accept_console else None,
                network=text["network"] if cfg.accept_network else None,
            )
        except ValidationError as e:
            detail = _safe_errors(e)
            raise _fail(422, detail, origin) from e
        except _BadJsonField as e:
            raise _fail(422, str(e), origin) from e
        except RecursionError as e:  # nesting too deep for the validator
            raise _fail(422, "invalid form data: nested too deeply", origin) from e

        shot = fields.get("screenshot") or None
        shot_type = "image/png"
        if shot is not None:
            if len(shot) > cfg.max_screenshot_bytes:
                raise _fail(413, "screenshot too large", origin)
            shot_type = sniff_image_type(shot)
            if shot_type is None:
                raise _fail(415, "screenshot must be a PNG, JPEG or WebP image", origin)
            try:
                shot = await run_in_threadpool(sanitize_image, shot, shot_type)
            except SanitizerBusy as e:
                raise _fail(
                    503,
                    "busy, retry shortly",
                    origin,
                    **{"Retry-After": str(BUSY_RETRY_AFTER_SECONDS)},
                ) from e
            except ValueError as e:
                raise _fail(
                    415,
                    "screenshot is not a well-formed PNG, JPEG or WebP image",
                    origin,
                ) from e
            if len(shot) > cfg.max_screenshot_bytes:  # a re-encode can grow the file
                raise _fail(413, "screenshot too large", origin)

        try:
            report, result = await run_in_threadpool(
                process_submission,
                submission,
                request.state.heed_sink,
                origin=origin,
                user=user,
                screenshot=shot,
                screenshot_media_type=shot_type,
                attachment_store=attachment_store,
                config=cfg,
                moderator=moderator or accept_all,
                holding_sink=holding_sink,
                redactor=redactor,
            )
        except Exception as e:  # the sink failed: say so with CORS, not a bare 500
            logger.exception("heed: a report could not be processed or delivered")
            raise _fail(502, "the report could not be delivered", origin) from e
        # A rejected report looks received, like a honeypot hit; a held one says so,
        # so the widget can tell a real visitor their report awaits review.
        held = report.status is Status.held
        return JSONResponse(
            {
                "id": report.id,
                "ok": result.ok,
                "ref": None if held else result.external_ref,
                "url": None if held else result.url,
                "status": Status.held.value if held else Status.received.value,
            },
            headers=_cors(origin),
        )

    return router


def make_app(
    sink: Optional[Sink],
    *,
    config: Optional[HeedConfig] = None,
    attachment_store: Optional[MutableMapping] = None,
    prefix: str = "/heed",
    serve_widget: bool = True,
    **router_kwargs: Any,
) -> FastAPI:
    """A standalone FastAPI app serving the heed router under ``prefix``.

    With ``serve_widget`` (the default) it also serves the built widget at
    ``{prefix}/widget/{file}`` (see :mod:`heed.widget`), so one host both hands out the
    script and receives its reports.
    """
    app = FastAPI(title="heed")
    app.include_router(
        make_router(
            sink, config=config, attachment_store=attachment_store, **router_kwargs
        ),
        prefix=prefix,
    )
    if serve_widget:
        app.include_router(make_widget_router(), prefix=prefix)
    return app
