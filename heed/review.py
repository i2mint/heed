"""Review pages: media (a video, audio, images) with a comment thread under it.

Publish one or more media files under a title and get a link; whoever has the link
watches the media and reads and adds comments (a name per comment, optionally pinned
to a moment in the video), on every visit, without an account. The owner deletes
comments, over HTTP when ``is_moderator`` says so or from the CLI. On an origin shared
with other apps, use ``token_moderator`` (a typed-in secret) rather than a login alone:
a login is ambient, so any script on that origin acts as the owner (#74).

The link is a **capability**: the page id is 128 random bits and travels in the URL
*fragment* (``https://host/review/#<id>``), so the link itself is never sent in a
request or a ``Referer`` (the API calls the page makes do carry the id in their
paths, so the host's access logs see it). Without the id a page, its media and its
thread are all 404s. On top of that the thread endpoint is rate-limited per IP and per
thread, capped per thread, size-capped, and has a honeypot (see :mod:`heed.threads`).

Layout of the data root (one folder; ADR-0024's S3 layout mirrors it key for key)::

    pages/<id>.json            the page: title, note, media list, created_at
    media/<id>/<name>          the media files, served with byte ranges (seekable)
    comments/<id>/<cid>.json   the thread (heed.threads)
    access/<id>.json           optional password, expiry, revoke (heed.review_access);
                               no file means open: back this folder up too

Python::

    >>> import tempfile
    >>> root = tempfile.mkdtemp()
    >>> clip = os.path.join(root, "cut1.mp4"); _ = open(clip, "wb").write(b"\\0" * 10)
    >>> page = publish_review([clip], title="Plot car, cut 1", rootdir=root)
    >>> [m.name for m in page.media], len(page.id)
    (['cut1.mp4'], 22)
    >>> url = review_url(page.id, "https://apps.example/review/")
    >>> url == "https://apps.example/review/#" + page.id
    True

CLI: ``heed review publish cut1.mp4 --title "Plot car"`` prints the link;
``heed review comments <id>`` reads the thread back (``--json`` for agents).
"""

from __future__ import annotations

import mimetypes
import os
import re
import secrets
import shutil
import sys
from collections.abc import Callable, Iterable, MutableMapping
from datetime import datetime, timezone
from importlib.resources import files as _package_files
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from heed.review_access import ReviewGate, revoke_access, set_access
from heed.storage import make_report_store
from heed.threads import (
    ID_PATTERN,
    MEDIA_NAME_PATTERN,
    Comment,
    RateLimiter,
    folder_threads,
    list_comments,
    make_thread_router,
    no_moderator,
    valid_id,
)

ENV_DIR = "HEED_REVIEW_DIR"
ENV_BASE_URL = "HEED_REVIEW_BASE_URL"
#: Where review pages live when neither ``rootdir`` nor ``HEED_REVIEW_DIR`` is given.
DEFAULT_DIR = "~/.local/share/heed/review"
#: The optional config file that may set ``[review] base_url`` (and ``dir``).
CONFIG_FILE = "~/.config/heed/config.toml"
MEDIA_KINDS = ("video", "audio", "image")
MAX_TITLE_CHARS = 200
MAX_NOTE_CHARS = 4_000
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._ -]+")

#: Headers on every review response: never indexed, never leak the URL onward.
PRIVATE_PAGE_HEADERS = {
    "X-Robots-Tag": "noindex, nofollow",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}
#: The page shell's policy: only its own script, style, API and media.
_SHELL_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "media-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'"
)
_JSON_HEADERS = {**PRIVATE_PAGE_HEADERS, "Cache-Control": "no-store"}


class MediaItem(BaseModel):
    """One media file of a page."""

    name: str = Field(pattern=MEDIA_NAME_PATTERN, max_length=200)
    kind: str  # one of MEDIA_KINDS
    content_type: str
    bytes: int = Field(ge=0)


class ReviewPage(BaseModel):
    """A published review page (its comments live beside it, in the thread store)."""

    id: str = Field(pattern=ID_PATTERN)
    title: str = Field(max_length=MAX_TITLE_CHARS)
    note: str | None = Field(default=None, max_length=MAX_NOTE_CHARS)
    media: list[MediaItem] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime | None = None


# ------------------------------------------------------------------- configuration


def _config() -> dict:
    path = Path(CONFIG_FILE).expanduser()
    if not path.is_file():
        return {}
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10: no config file support
        return {}
    with path.open("rb") as f:
        return tomllib.load(f).get("review", {})


def default_rootdir() -> Path:
    """``HEED_REVIEW_DIR``, else ``[review] dir`` in the config file, else default."""
    raw = os.environ.get(ENV_DIR) or _config().get("dir") or DEFAULT_DIR
    return Path(raw).expanduser()


def default_base_url() -> str | None:
    """``HEED_REVIEW_BASE_URL``, else ``[review] base_url`` in the config file."""
    return os.environ.get(ENV_BASE_URL) or _config().get("base_url") or None


def review_url(page_id: str, base_url: str | None = None) -> str:
    """The shareable link of a page: the base URL with the id as its fragment.

    >>> review_url("abc", "https://apps.example/review")
    'https://apps.example/review/#abc'
    >>> review_url("abc", "")  # no base URL: the fragment alone
    '#abc'
    """
    base = base_url if base_url is not None else default_base_url()
    if not base:
        return f"#{page_id}"
    return f"{base.split('#')[0].rstrip('/')}/#{page_id}"


# ------------------------------------------------------------------------- storage


class ReviewStores:
    """The three stores of a data root: pages, media files, comments.

    Pages are a text ``MutableMapping`` (swap in any ``dol`` store); ``threads`` maps
    a page id to its comment store (:func:`heed.threads.folder_threads`); media are
    files, because video must be served with byte ranges.
    """

    def __init__(self, rootdir: str | os.PathLike | None = None):
        self.root = Path(rootdir).expanduser() if rootdir else default_rootdir()
        for sub in ("pages", "media", "comments"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        self.pages: MutableMapping = make_report_store(
            str(self.root / "pages") + os.sep
        )
        self.threads = folder_threads(self.root / "comments")

    def page(self, page_id: str) -> ReviewPage | None:
        """The page ``page_id``, or ``None`` if there is none (or the id is invalid)."""
        if not valid_id(page_id):
            return None
        try:
            return ReviewPage.model_validate_json(self.pages[f"{page_id}.json"])
        except KeyError:
            return None

    def save(self, page: ReviewPage) -> None:
        """Write ``page``."""
        self.pages[f"{page.id}.json"] = page.model_dump_json(indent=2)

    def media_path(self, page_id: str, name: str) -> Path | None:
        """The file of media ``name`` of ``page_id``, if the page lists it."""
        page = self.page(page_id)
        if page is None or name not in {m.name for m in page.media}:
            return None
        path = self.root / "media" / page_id / name
        return path if path.is_file() else None

    def all_pages(self) -> list[ReviewPage]:
        """Every page, newest first."""
        ids = (k[: -len(".json")] for k in self.pages if k.endswith(".json"))
        pages = [p for p in map(self.page, ids) if p is not None]
        return sorted(pages, key=lambda p: p.created_at, reverse=True)

    def comments_of(self, page_id: str) -> list[Comment]:
        """The thread of ``page_id``, oldest first."""
        return list_comments(self.threads(page_id))


def safe_media_name(filename: str) -> str:
    """A file name made safe to store and serve (no path, no odd characters).

    >>> safe_media_name("../../etc/My Cut (v2).mp4")
    'My Cut _v2_.mp4'
    """
    base = os.path.basename(filename.replace("\\", "/"))
    name = _UNSAFE_NAME_CHARS.sub("_", base).strip(" .") or "media"
    return name[:200] if name[0].isalnum() else f"m{name}"[:200]


def media_kind(filename: str) -> tuple[str, str]:
    """``(kind, content_type)`` of a media file, refusing anything not media.

    >>> media_kind("a.mp4")
    ('video', 'video/mp4')
    >>> media_kind("a.html")
    Traceback (most recent call last):
    ...
    ValueError: Not a video, audio or image file: 'a.html' (text/html)
    """
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    kind = content_type.split("/")[0]
    if kind not in MEDIA_KINDS or content_type == "image/svg+xml":
        raise ValueError(
            f"Not a video, audio or image file: {filename!r} ({content_type})"
        )
    return kind, content_type


def publish_review(
    media: Iterable[str | os.PathLike],
    *,
    title: str | None = None,
    note: str | None = None,
    rootdir: str | os.PathLike | None = None,
    to: str | None = None,
    password: str | None = None,
    expires=None,
) -> ReviewPage:
    """Publish ``media`` as a new review page (or add it to page ``to``).

    ``password`` and ``expires`` (``"7d"``, ``"2026-11-01"``, ``"never"``) gate the
    page (:mod:`heed.review_access`, needs ``heed[gate]``); on an existing page they
    change only what is given. Neither: the page is open to whoever has the link.

    The files are copied into the data root. Adding to an existing page keeps its
    link and its thread, so a new cut can go where the old one's comments are; a file
    with the name of one already on the page replaces it.
    """
    stores = ReviewStores(rootdir)
    sources = [Path(p).expanduser() for p in media]
    for src in sources:
        if not src.is_file():
            raise FileNotFoundError(f"No such media file: {src}")
        media_kind(src.name)
    now = datetime.now(timezone.utc)
    if to is not None:
        page = stores.page(to)
        if page is None:
            raise KeyError(f"No review page {to!r}")
        page.updated_at = now
    else:
        if not title:
            raise ValueError("A new review page needs a title")
        page = ReviewPage(id=secrets.token_urlsafe(16), title=title, created_at=now)
    if title and to is not None:
        page.title = title
    if note is not None:
        page.note = note
    if password is not None or expires is not None:
        # first: a bad password or expiry must not leave an open page behind
        set_access(stores.root, page.id, password=password, expires=expires)
    folder = stores.root / "media" / page.id
    folder.mkdir(parents=True, exist_ok=True)
    for src in sources:
        name = safe_media_name(src.name)
        kind, content_type = media_kind(name)
        shutil.copyfile(src, folder / name)
        item = MediaItem(
            name=name, kind=kind, content_type=content_type, bytes=src.stat().st_size
        )
        page.media = [m for m in page.media if m.name != name] + [item]
    stores.save(page)
    return page


# ----------------------------------------------------------------------------- app


def page_shell(*, api: str = "", title: str = "Review") -> str:
    """The page's HTML shell: it loads the script and style from ``{api}/ui/``.

    A host that serves static files (enlace's ``frontend_dir``) writes this once:
    ``heed review page-shell --api /api/review > frontend/index.html``.
    """
    api = api.rstrip("/")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="{_SHELL_CSP}">
<meta name="robots" content="noindex, nofollow">
<title>{title}</title>
<link rel="stylesheet" href="{api}/ui/review.css">
</head>
<body>
<main id="review" data-api="{api}"><p class="muted">Loading…</p></main>
<script src="{api}/ui/review.js"></script>
</body>
</html>
"""


def _ui_asset(name: str) -> tuple[bytes, str]:
    types = {"review.js": "text/javascript", "review.css": "text/css"}
    if name not in types:
        raise HTTPException(404)
    data = _package_files("heed").joinpath("static", "review", name).read_bytes()
    return data, types[name]


def make_review_app(
    rootdir: str | os.PathLike | None = None,
    *,
    is_moderator: Callable[[Request], bool] = no_moderator,
    trusted_proxies=(),
    rate_limiter: RateLimiter | None = None,
    rate_limit_per_minute: int = 10,
    max_comments_per_thread: int = 500,
    serve_shell: bool = True,
    gate: ReviewGate | None = None,
) -> FastAPI:
    """The review API (and, with ``serve_shell``, the page at ``/``).

    Routes: ``GET /pages/{id}``, ``GET /pages/{id}/media/{name}`` (byte ranges;
    ``?download=1`` serves it as an attachment, so a phone saves the file),
    ``GET|POST /threads/{id}``, ``DELETE /threads/{id}/{comment}`` (moderators),
    ``GET /ui/review.{js,css}``. There is deliberately no HTTP listing of pages:
    it would hand every capability to any script running as the owner (the CLI's
    ``ls`` lists them).

    A page given a password, an expiry or a revoke (:func:`publish_review`,
    ``heed review revoke``) is gated by ``gate`` (a :class:`ReviewGate`, built over
    ``rootdir`` with ``trusted_proxies`` by default): 401 until ``POST
    /pages/{id}/unlock`` ``{"password": ...}`` sets a cookie, 410 once expired, 404
    once revoked. Pages with no such record are open, as ever.
    """
    stores = ReviewStores(rootdir)
    gate = gate or ReviewGate(stores.root, trusted_proxies=trusted_proxies)
    app = FastAPI(title="heed review", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def _private_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in PRIVATE_PAGE_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    def _page_or_404(page_id: str) -> ReviewPage:
        page = stores.page(page_id)
        if page is None:
            raise HTTPException(404, "No such page")
        return page

    def _open_page_or_404(page_id: str, request: Request) -> ReviewPage:
        page = _page_or_404(page_id)
        gate.check(request, page_id)
        return page

    @app.get("/pages/{page_id}")
    def get_page(page_id: str, request: Request, response: Response):
        response.headers.update(_JSON_HEADERS)
        return _open_page_or_404(page_id, request).model_dump(mode="json")

    @app.post("/pages/{page_id}/unlock")
    async def unlock_page(page_id: str, request: Request):
        _page_or_404(page_id)
        response = await gate.unlock(request, page_id)
        response.headers.update(_JSON_HEADERS)
        return response

    @app.get("/pages/{page_id}/media/{name}")
    def get_media(page_id: str, name: str, request: Request, download: bool = False):
        # The link already lets its holder fetch the file: a download adds nothing.
        _open_page_or_404(page_id, request)
        path = stores.media_path(page_id, name)
        if path is None:
            raise HTTPException(404, "No such media")
        _, content_type = media_kind(name)
        return FileResponse(
            path,
            media_type=content_type,
            filename=name,
            content_disposition_type="attachment" if download else "inline",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    app.include_router(
        make_thread_router(
            stores.threads,
            thread_exists=lambda t: stores.page(t) is not None,
            is_moderator=is_moderator,
            trusted_proxies=trusted_proxies,
            rate_limiter=rate_limiter,
            rate_limit_per_minute=rate_limit_per_minute,
            max_comments_per_thread=max_comments_per_thread,
            thread_gate=lambda request, page_id: gate.check(request, page_id),
        )
    )

    @app.get("/ui/{name}")
    def ui_asset(name: str):
        data, content_type = _ui_asset(name)
        return Response(
            data, media_type=content_type, headers={"Cache-Control": "no-cache"}
        )

    if serve_shell:

        @app.get("/", response_class=HTMLResponse)
        def shell():
            return HTMLResponse(page_shell(), headers={"Cache-Control": "no-cache"})

    return app


# ----------------------------------------------------------------------------- CLI


def _format_ms(ms: int | None) -> str:
    """``m:ss`` of a media position.

    >>> _format_ms(83_400)
    '1:23'
    """
    if ms is None:
        return ""
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


def _print_comments(stores: ReviewStores, page_id: str, *, as_json: bool) -> int:
    import json

    page = stores.page(page_id)
    if page is None:
        print(f"No review page {page_id!r} in {stores.root}")
        return 1
    comments = stores.comments_of(page_id)
    if as_json:
        print(
            json.dumps(
                {
                    "page": page.model_dump(mode="json"),
                    "comments": [c.model_dump(mode="json") for c in comments],
                },
                indent=2,
            )
        )
        return 0
    print(f"# {page.title}  ({len(comments)} comments)")
    for c in comments:
        at = f" @{_format_ms(c.at_ms)}" if c.at_ms is not None else ""
        on = f" [{c.media}]" if c.media else ""
        print(f"\n{c.created_at:%Y-%m-%d %H:%M} {c.author}{at}{on}  ({c.id})")
        print(c.body)
    return 0


def main(argv: list[str] | None = None) -> int:
    """``heed review ...``: publish pages, read their comments, moderate, serve."""
    import argparse

    p = argparse.ArgumentParser(prog="heed review", description=main.__doc__)
    p.add_argument("--dir", default=None, help=f"data root (default {DEFAULT_DIR})")
    sub = p.add_subparsers(dest="cmd", required=True)

    pub = sub.add_parser(
        "publish", help="publish media as a review page; prints the link"
    )
    pub.add_argument("media", nargs="+")
    pub.add_argument("--title")
    pub.add_argument("--note", help="text shown under the title")
    pub.add_argument("--note-file", help="read the note from this file (e.g. a .md)")
    pub.add_argument("--to", help="add the media to this existing page (same link)")
    pub.add_argument("--base-url", help=f"the page's URL (default ${ENV_BASE_URL})")
    pub.add_argument(
        "--password-file",
        help="gate the page with the password in this file ('-' reads stdin); "
        "not an argument, which other users' `ps` would show",
    )
    pub.add_argument(
        "--expires",
        help="the link stops working then: 7d, 2026-11-01, an ISO time, never",
    )

    rev = sub.add_parser(
        "revoke", help="end a page's link for everyone (it answers 404)"
    )
    rev.add_argument("page")

    com = sub.add_parser("comments", help="print a page's comments")
    com.add_argument("page")
    com.add_argument("--json", action="store_true")

    sub.add_parser("ls", help="list pages, newest first")

    dele = sub.add_parser("delete-comment", help="delete one comment (moderation)")
    dele.add_argument("page")
    dele.add_argument("comment")

    shell = sub.add_parser("page-shell", help="print the HTML shell for a static host")
    shell.add_argument("--api", default="", help="where the review API is mounted")

    srv = sub.add_parser("serve", help="run the review app (needs uvicorn)")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8077)
    srv.add_argument(
        "--moderator-token-env",
        metavar="VAR",
        help="name of an environment variable holding the moderator token "
        "(deleting comments over HTTP then needs it; see heed.token_moderator)",
    )

    a = p.parse_args(argv)
    if a.cmd == "page-shell":
        print(page_shell(api=a.api), end="")
        return 0
    if a.cmd == "publish":
        note = Path(a.note_file).read_text() if a.note_file else a.note
        password = None
        if a.password_file:
            raw = (
                sys.stdin.read()
                if a.password_file == "-"
                else Path(a.password_file).read_text()
            )
            password = raw.rstrip("\r\n")
        page = publish_review(
            a.media,
            title=a.title,
            note=note,
            rootdir=a.dir,
            to=a.to,
            password=password,
            expires=a.expires,
        )
        print(review_url(page.id, a.base_url))
        return 0
    stores = ReviewStores(a.dir)
    if a.cmd == "revoke":
        if stores.page(a.page) is None:
            print(f"No review page {a.page!r} in {stores.root}")
            return 1
        revoke_access(stores.root, a.page)
        print("revoked")
        return 0
    if a.cmd == "comments":
        return _print_comments(stores, a.page, as_json=a.json)
    if a.cmd == "ls":
        for page in stores.all_pages():
            n = len(stores.comments_of(page.id))
            print(
                f"{page.created_at:%Y-%m-%d}  {page.id}  {n:>3} comments  {page.title}"
            )
        return 0
    if a.cmd == "delete-comment":
        from heed.threads import delete_comment

        ok = delete_comment(stores.threads(a.page), a.comment)
        print("deleted" if ok else "no such comment")
        return 0 if ok else 1
    if a.cmd == "serve":
        import uvicorn

        is_moderator = no_moderator
        if a.moderator_token_env:
            from heed.threads import token_moderator

            try:
                token = os.environ.get(a.moderator_token_env, "")
                is_moderator = token_moderator(token)
            except ValueError as e:
                p.error(f"${a.moderator_token_env}: {e}")
        app = make_review_app(a.dir, is_moderator=is_moderator)
        uvicorn.run(app, host=a.host, port=a.port)
        return 0
    return 2
