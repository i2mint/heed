"""Comment threads: a persistent, public, append-mostly conversation per key.

A *thread* is the list of comments left under one thing (a review page, a video, a
document). Anyone who can reach the thread can read it and add to it; only a
moderator can delete. This module is the reusable core of that mechanism, independent
of what the thread is attached to (``heed.review`` attaches it to a media page):

- :class:`Comment` / :class:`CommentSubmission` -- the stored comment and what an
  untrusted client may send (a name, a body, optionally a position in a media item:
  ``at_ms``, integer milliseconds, and ``media``, the item's name).
- :func:`add_comment`, :func:`list_comments`, :func:`delete_comment` -- pure
  functions over ONE thread's store, a ``MutableMapping`` of ``{comment_id}.json``
  keys, so a ``dict``, a folder or an S3 ``dol`` store all work.
- *Threads* are a callable ``thread -> store``: :func:`memory_threads` (dicts) or
  :func:`folder_threads` (one folder per thread), so reading a thread never lists
  any other thread.
- :func:`make_thread_router` -- ``GET``/``POST`` ``/threads/{thread}`` and
  ``DELETE /threads/{thread}/{comment_id}``, hardened like heed's ingest endpoint
  (per-IP and per-thread rate limits, per-thread cap, honeypot, body cap, strict
  validation).

Who may reach a thread is the host's decision, passed in as ``thread_exists``: an
unknown thread is a 404, so a thread keyed by an unguessable id is a capability.

>>> store = {}
>>> sub = CommentSubmission(author="Noel", body="Nice cut", at_ms=1500)
>>> c = add_comment(store, "t1", sub)
>>> [(x.author, x.body, x.at_ms) for x in list_comments(store)]
[('Noel', 'Nice cut', 1500)]
>>> delete_comment(store, c.id)
True
>>> list_comments(store)
[]
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import time
from collections.abc import Callable, MutableMapping
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import AfterValidator, BaseModel, BeforeValidator, Field, ValidationError

from heed.base import _clean_text, _non_blank, normalize_newlines
from heed.security import RateLimiter, client_ip, make_rate_limiter, rate_limit_key

logger = logging.getLogger("heed.threads")

# --- Limits (SSOT). The endpoint is public, so everything a client sends is bounded.
MAX_AUTHOR_CHARS = 60
MAX_COMMENT_CHARS = 4_000
MAX_AT_MS = 24 * 3600 * 1000  # a position in the media: at most a day in
MAX_COMMENT_BYTES = 20_000  # a whole POST body
MAX_MEDIA_NAME_CHARS = 200
#: Which media item a timed comment refers to: a plain file name, no path.
MEDIA_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._ -]*$"
#: Thread and comment ids are URL- and path-safe: no separators, no dots.
ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
_ID_RE = re.compile(ID_PATTERN)
#: The header a moderator's DELETE must carry: a cross-site form cannot set it.
MODERATE_HEADER = "X-Heed-Moderate"
#: Shortest accepted moderator token: it guards deletion and the owner badge.
MIN_TOKEN_LENGTH = 16
#: Headers on every thread response: never cached, never indexed.
THREAD_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow",
}

_Text = Annotated[str, BeforeValidator(normalize_newlines), AfterValidator(_clean_text)]
Author = Annotated[
    _Text, Field(max_length=MAX_AUTHOR_CHARS), AfterValidator(_non_blank)
]
Body = Annotated[_Text, Field(max_length=MAX_COMMENT_CHARS), AfterValidator(_non_blank)]


class CommentSubmission(BaseModel):
    """What a visitor may send: a name, a comment, and optionally a media position."""

    author: Author
    body: Body
    at_ms: int | None = Field(default=None, ge=0, le=MAX_AT_MS)
    media: str | None = Field(
        default=None, max_length=MAX_MEDIA_NAME_CHARS, pattern=MEDIA_NAME_PATTERN
    )


class Comment(CommentSubmission):
    """A stored comment: the submission plus a server-assigned id, time and role."""

    id: str = Field(pattern=ID_PATTERN)
    thread: str = Field(pattern=ID_PATTERN)
    created_at: datetime
    #: Posted by a moderator (the owner): the page marks it, so a name cannot pass
    #: for the owner's.
    moderator: bool = False


def new_comment_id(*, clock: Callable[[], float] = time.time) -> str:
    """A comment id that sorts by creation time (ms, zero-padded) then at random.

    >>> a, b = new_comment_id(clock=lambda: 1.0), new_comment_id(clock=lambda: 2.0)
    >>> a < b and len(a) == len(b)
    True
    """
    return f"{int(clock() * 1000):014d}-{secrets.token_hex(4)}"


def valid_id(value: str) -> bool:
    """Whether ``value`` is a safe thread or comment id.

    >>> valid_id("abc_DEF-1"), valid_id("../x"), valid_id("a.json")
    (True, False, False)
    """
    return bool(_ID_RE.match(value or ""))


def _key(comment_id: str) -> str:
    if not valid_id(comment_id):
        raise KeyError(f"invalid comment id: {comment_id!r}")
    return f"{comment_id}.json"


# ------------------------------------------------------------------- the threads


def memory_threads() -> Callable[[str], MutableMapping]:
    """Threads held in dicts, one per thread (tests, single-process demos)."""
    threads: dict[str, dict] = {}

    def thread_store(thread: str) -> MutableMapping:
        if not valid_id(thread):
            raise KeyError(thread)
        return threads.setdefault(thread, {})

    return thread_store


def folder_threads(rootdir: str | os.PathLike) -> Callable[[str], MutableMapping]:
    """Threads as folders: ``<rootdir>/<thread>/<comment_id>.json``.

    Deletion is permanent (comments carry names), as in
    :func:`heed.storage.make_report_store`.
    """
    from heed.storage import make_report_store

    root = os.fspath(rootdir)

    def thread_store(thread: str) -> MutableMapping:
        if not valid_id(thread):
            raise KeyError(thread)
        folder = os.path.join(root, thread)
        os.makedirs(folder, exist_ok=True)
        return make_report_store(folder + os.sep)

    return thread_store


def add_comment(
    store: MutableMapping,
    thread: str,
    submission: CommentSubmission,
    *,
    moderator: bool = False,
) -> Comment:
    """Store ``submission`` in ``thread``'s ``store``; return the stored comment."""
    comment = Comment(
        **submission.model_dump(),
        id=new_comment_id(),
        thread=thread,
        created_at=datetime.now(timezone.utc),
        moderator=moderator,
    )
    store[_key(comment.id)] = comment.model_dump_json()
    return comment


def _comment_keys(store: MutableMapping) -> list[str]:
    return sorted(k for k in store if k.endswith(".json") and valid_id(k[:-5]))


def list_comments(store: MutableMapping) -> list[Comment]:
    """A thread's comments, oldest first. Unreadable entries are skipped."""
    out = []
    for k in _comment_keys(store):
        try:
            out.append(Comment.model_validate_json(store[k]))
        except (KeyError, ValidationError, ValueError):
            logger.warning("skipping unreadable comment %r", k)
    return out


def count_comments(store: MutableMapping) -> int:
    """How many comments a thread holds (without parsing them)."""
    return len(_comment_keys(store))


def delete_comment(store: MutableMapping, comment_id: str) -> bool:
    """Delete one comment; ``False`` if there was none (or the id is invalid)."""
    try:
        del store[_key(comment_id)]
    except KeyError:
        return False
    return True


# ------------------------------------------------------------------------- routes


def no_moderator(request: Request) -> bool:
    """The default moderator check: nobody may delete over HTTP (use the CLI)."""
    return False


def email_moderator(emails) -> Callable[[Request], bool]:
    """A moderator check passing requests whose authenticated email is in ``emails``.

    It reads ``request.state.user_email``, which an auth middleware sets (enlace_auth
    does, on public apps too, when the visitor has a session).
    """
    allowed = {e.strip().lower() for e in emails if e and e.strip()}

    def is_moderator(request: Request) -> bool:
        email = getattr(request.state, "user_email", None) or ""
        return email.strip().lower() in allowed

    return is_moderator


#: The header a moderator's browser sends with the secret (see :func:`token_moderator`).
MODERATOR_TOKEN_HEADER = "X-Heed-Moderator-Token"


def token_moderator(
    token: str, *, also: Callable[[Request], bool] | None = None
) -> Callable[[Request], bool]:
    """A moderator check that needs a secret the owner types in, not a login.

    On an origin shared with other apps, a session cookie is ambient authority: any
    script running there while the owner is logged in can call the API *as* the owner.
    A secret in a request header is not ambient (the review page keeps it in memory,
    never in storage), so such a script can neither delete comments nor post badged as
    the owner. ``also`` (e.g. :func:`email_moderator`) is required too, when given.

    Use a random token (``secrets.token_urlsafe(24)``). This raises the bar, it is not a
    wall: a hostile script on the same origin can still open the review page in a frame
    and hook it before the owner types the token, or phish with its own prompt. A
    dedicated origin for the review app is the real fix.

    >>> from types import SimpleNamespace as NS
    >>> check = token_moderator("s3cret-token-value")
    >>> check(NS(headers={"X-Heed-Moderator-Token": "s3cret-token-value"}))
    True
    >>> check(NS(headers={}))
    False
    """
    if (
        not isinstance(token, str)
        or len(token) < MIN_TOKEN_LENGTH
        or not token.isascii()
        or not token.isprintable()
        or token != token.strip()
    ):
        raise ValueError(
            f"the moderator token must be printable ASCII without surrounding spaces, "
            f"at least {MIN_TOKEN_LENGTH} characters (e.g. secrets.token_urlsafe(24))"
        )
    expected = token.encode()

    def is_moderator(request: Request) -> bool:
        given = (request.headers.get(MODERATOR_TOKEN_HEADER) or "").encode()
        if not hmac.compare_digest(given, expected):
            return False
        return bool(also(request)) if also is not None else True

    return is_moderator


def _is_loopback(host: str | None) -> bool:
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def make_thread_router(
    threads: Callable[[str], MutableMapping] | None = None,
    *,
    thread_exists: Callable[[str], bool] = valid_id,
    is_moderator: Callable[[Request], bool] = no_moderator,
    rate_limiter: RateLimiter | None = None,
    rate_limit_per_minute: int = 10,
    thread_rate_limiter: RateLimiter | None = None,
    thread_rate_limit_per_hour: int = 120,
    max_comments_per_thread: int = 500,
    trusted_proxies=(),
    honeypot_field: str = "website",
    on_comment: Callable[[Comment], None] | None = None,
    thread_gate: Callable[[Request, str], None] | None = None,
) -> APIRouter:
    """The HTTP surface of comment threads (``threads``: thread id -> its store).

    ``GET /threads/{thread}`` lists, ``POST /threads/{thread}`` adds (a JSON body:
    ``author``, ``body``, optional ``at_ms`` and ``media``),
    ``DELETE /threads/{thread}/{id}`` deletes when ``is_moderator(request)`` and the
    request carries the ``X-Heed-Moderate`` header. A thread for which
    ``thread_exists`` is false answers 404 to everything, which is how a host makes
    threads capabilities; ``thread_gate(request, thread)`` is called on every request
    to an existing thread and raises ``HTTPException`` to refuse it (a password, say).
    A filled ``honeypot_field`` is accepted silently and
    stored nowhere. Behind a reverse proxy, set ``trusted_proxies`` or every visitor
    shares the proxy's rate-limit bucket.

    >>> from fastapi import FastAPI
    >>> from fastapi.testclient import TestClient
    >>> app = FastAPI(); app.include_router(make_thread_router())
    >>> client = TestClient(app)
    >>> client.post("/threads/t1", json={"author": "Thor", "body": "hi"}).status_code
    201
    >>> [c["body"] for c in client.get("/threads/t1").json()["comments"]]
    ['hi']
    """
    threads = threads or memory_threads()
    limiter = rate_limiter or make_rate_limiter(
        max_per_window=rate_limit_per_minute, namespace="thread-ip"
    )
    thread_limiter = thread_rate_limiter or make_rate_limiter(
        max_per_window=thread_rate_limit_per_hour,
        window_seconds=3600.0,
        namespace="thread",
    )
    warned = []
    router = APIRouter()

    def _require_thread(thread: str, request: Request) -> MutableMapping:
        if not (valid_id(thread) and thread_exists(thread)):
            raise HTTPException(404, "No such thread")
        if thread_gate is not None:
            thread_gate(request, thread)
        return threads(thread)

    def _client_key(request: Request) -> str:
        peer = request.client.host if request.client else None
        if not trusted_proxies and _is_loopback(peer) and not warned:
            warned.append(True)
            logger.warning(
                "heed threads: requests come from a loopback peer and no "
                "trusted_proxies are set, so every visitor shares one rate-limit "
                "bucket; pass trusted_proxies=['127.0.0.1', '::1'] behind a proxy"
            )
        ip = client_ip(
            peer,
            request.headers.get("x-forwarded-for"),
            trusted_proxies=trusted_proxies,
        )
        return rate_limit_key(ip)

    @router.get("/threads/{thread}")
    def get_thread(thread: str, request: Request, response: Response):
        store = _require_thread(thread, request)
        response.headers.update(THREAD_RESPONSE_HEADERS)
        return {
            "thread": thread,
            "comments": [c.model_dump(mode="json") for c in list_comments(store)],
            "can_moderate": bool(is_moderator(request)),
        }

    @router.post("/threads/{thread}", status_code=201)
    async def post_comment(thread: str, request: Request, response: Response):
        store = _require_thread(thread, request)
        response.headers.update(THREAD_RESPONSE_HEADERS)
        if not limiter.allow(_client_key(request)):
            raise HTTPException(429, "Too many comments; try again in a minute")
        raw = await read_capped(request, MAX_COMMENT_BYTES)
        try:
            data = json.loads(raw or b"{}")
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
        except (ValueError, RecursionError):
            raise HTTPException(400, "Expected a JSON object") from None
        if honeypot_field and data.pop(honeypot_field, None):
            return {"ok": True}
        try:
            submission = CommentSubmission.model_validate(data)
        except ValidationError as e:
            fields = sorted({str(err["loc"][0]) for err in e.errors() if err["loc"]})
            raise HTTPException(422, f"Invalid fields: {', '.join(fields)}") from None
        moderator = bool(is_moderator(request))
        if not moderator:
            if count_comments(store) >= max_comments_per_thread:
                raise HTTPException(409, "This thread is full")
            if not thread_limiter.allow(thread):
                raise HTTPException(429, "This thread is busy; try again later")
        comment = add_comment(store, thread, submission, moderator=moderator)
        if on_comment is not None:
            try:
                on_comment(comment)
            except Exception:  # a notifier must never lose a stored comment
                logger.exception("on_comment hook failed")
        return {"ok": True, "comment": comment.model_dump(mode="json")}

    @router.delete("/threads/{thread}/{comment_id}")
    def remove_comment(thread: str, comment_id: str, request: Request):
        store = _require_thread(thread, request)
        if request.headers.get(MODERATE_HEADER) != "1" or not is_moderator(request):
            raise HTTPException(403, "Only a moderator can delete comments")
        if not delete_comment(store, comment_id):
            raise HTTPException(404, "No such comment")
        return {"ok": True}

    return router


async def read_capped(request: Request, cap: int, *, what: str = "Comment") -> bytes:
    """The request body, refusing (413) anything over ``cap`` bytes while streaming."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise HTTPException(413, f"{what} too large")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > cap:
            raise HTTPException(413, f"{what} too large")
        chunks.append(chunk)
    return b"".join(chunks)
