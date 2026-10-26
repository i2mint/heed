"""Access control for review pages: an optional password, expiry and revoke.

A review page is a capability (its 128-bit id), and by default stays one forever. This
module adds what a link sent to a client needs, on top of `caplinks
<https://github.com/i2mint/caplinks>`_ (the optional ``heed[gate]`` extra): a password,
an expiry, and a revoke, kept as one record per page under ``<root>/access/<id>.json``.

A page **with no record is open**, exactly as before; a page with one is gated. So
nothing changes for existing pages, and ``caplinks`` is only imported when a record
exists or one is being written (a gated page whose ``caplinks`` is missing answers 503,
never open).

Python::

    >>> import tempfile
    >>> root = tempfile.mkdtemp()
    >>> page_id = "A" * 22
    >>> rec = set_access(root, page_id, password="open sesame", expires="7d")
    >>> rec["protected"]
    True
    >>> revoke_access(root, page_id)["revoked"] is not None
    True

HTTP: :class:`ReviewGate` is what :func:`heed.review.make_review_app` consults. A gated
page answers 401 until ``POST /pages/{id}/unlock`` with the password sets a cookie
(HMAC keyed by the stored hash, so a new password or a revoke ends every open session);
an expired page answers 410, a revoked one 404. Wrong passwords are limited per client
per page, and a page that takes too many wrong ones from everyone is locked for a while.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator, MutableMapping
from pathlib import Path

from fastapi import HTTPException, Request, Response

from heed.security import RateLimiter, client_ip, make_rate_limiter, rate_limit_key
from heed.threads import read_capped

ACCESS_DIR = "access"
COOKIE_PREFIX = "heed_unlock_"
#: password tries one client gets on one page per window
DFLT_MAX_ATTEMPTS = 10
#: wrong passwords one page takes from everyone per window before it locks
DFLT_MAX_PAGE_FAILURES = 30
DFLT_ATTEMPT_WINDOW_S = 300.0
#: how long an unlock cookie lasts (never past the page's expiry)
DFLT_UNLOCK_MAX_AGE_S = 7 * 24 * 3600
MAX_UNLOCK_BYTES = 2_048
_MISSING_EXTRA = (
    "gated review pages need caplinks: pip install 'heed[gate]' (or caplinks itself)"
)


class _JsonRecords(MutableMapping):
    """``{id: dict}`` kept as ``<dir>/<id>.json`` files (what caplinks stores)."""

    def __init__(self, folder: Path):
        self.folder = folder

    def _path(self, key: str) -> Path:
        if not key.replace("-", "").replace("_", "").isalnum():
            raise KeyError(key)
        return self.folder / f"{key}.json"

    def __getitem__(self, key):
        try:
            return json.loads(self._path(key).read_text())
        except FileNotFoundError:
            raise KeyError(key) from None

    def __setitem__(self, key, value):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value))
        os.chmod(tmp, 0o600)  # holds a password hash
        os.replace(tmp, path)

    def __delitem__(self, key):
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            raise KeyError(key) from None

    def __iter__(self) -> Iterator[str]:
        if self.folder.is_dir():
            yield from (p.stem for p in self.folder.glob("*.json"))

    def __len__(self):
        return sum(1 for _ in self)

    def __contains__(self, key):
        try:
            return self._path(key).is_file()
        except KeyError:
            return False


def _links(rootdir):
    try:
        from caplinks import CapabilityLinks
    except ImportError as e:
        raise ImportError(_MISSING_EXTRA) from e
    return CapabilityLinks(_JsonRecords(Path(rootdir).expanduser() / ACCESS_DIR))


def set_access(rootdir, page_id: str, *, password: str | None = None, expires=None):
    """Gate page ``page_id`` (creating its access record, or updating it).

    ``password`` (at least 8 characters) and ``expires`` (``"7d"``, ``"2026-11-01"``,
    an ISO timestamp, ``"never"``) are changed only when given, so
    ``set_access(root, id, expires="30d")`` keeps the password. Returns the record
    without the hash. A revoked page stays revoked (``KeyError``).
    """
    links = _links(rootdir)
    if links.record(page_id) is None:
        return links.create(
            {},
            password=password,
            expires="never" if expires is None else expires,
            token=page_id,
        )
    out = links.record(page_id)
    if password is not None:
        out = links.set_password(page_id, password)
    if expires is not None:
        out = links.set_expires(page_id, expires)
    return out


def revoke_access(rootdir, page_id: str):
    """End page ``page_id`` for every visitor: it answers 404 from now on.

    A page with no access record is first gated (so it can be revoked), then revoked.
    """
    links = _links(rootdir)
    if links.record(page_id) is None:
        links.create({}, expires="never", token=page_id)
    return links.revoke(page_id)


class ReviewGate:
    """The HTTP side of the gate: refuse, unlock, and limit password tries.

    ``rate_limiter`` counts every try of one client on one page;
    ``page_failure_limiter`` counts the wrong passwords a page takes from everyone and,
    past its limit, locks the page's unlock for ``lock_s`` seconds. Both are anything
    with ``allow(key) -> bool`` (heed's Redis limiter fits). Behind a reverse proxy set
    ``trusted_proxies``.
    """

    def __init__(
        self,
        rootdir,
        *,
        trusted_proxies=(),
        rate_limiter: RateLimiter | None = None,
        page_failure_limiter: RateLimiter | None = None,
        unlock_max_age_s: int = DFLT_UNLOCK_MAX_AGE_S,
        lock_s: float = DFLT_ATTEMPT_WINDOW_S,
        clock=time.monotonic,
    ):
        self.records = _JsonRecords(Path(rootdir).expanduser() / ACCESS_DIR)
        self.trusted_proxies = trusted_proxies
        self.rate_limiter = rate_limiter or make_rate_limiter(
            max_per_window=DFLT_MAX_ATTEMPTS,
            window_seconds=DFLT_ATTEMPT_WINDOW_S,
            namespace="unlock-client",
        )
        self.page_failure_limiter = page_failure_limiter or make_rate_limiter(
            max_per_window=DFLT_MAX_PAGE_FAILURES,
            window_seconds=DFLT_ATTEMPT_WINDOW_S,
            namespace="unlock-page",
        )
        self.unlock_max_age_s = unlock_max_age_s
        self.lock_s = lock_s
        self._clock = clock
        self._locked: dict[str, float] = {}  # page id -> when its lock ends

    def _open_record(self, page_id: str):
        """``(links, record)`` of a gated page that is neither revoked nor expired;
        ``None`` for an ungated one. Raises 404 (revoked), 410 (expired), 503 (no
        caplinks to judge a gated page: closed, never open)."""
        if page_id not in self.records:
            return None
        try:
            links = _links(self.records.folder.parent)
        except ImportError:
            raise HTTPException(
                503, "This page is protected and cannot be opened here"
            ) from None
        state, rec = links.status(page_id)
        if state == "missing":
            raise HTTPException(404, "No such page")
        if state == "expired":
            raise HTTPException(410, "This link has expired")
        return links, rec

    def check(self, request: Request, page_id: str) -> None:
        """Raise ``HTTPException`` (404 revoked, 410 expired, 401 locked) unless the
        page is open to ``request``; an ungated page always is."""
        found = self._open_record(page_id)
        if found is None:
            return
        links, rec = found
        cookie = request.cookies.get(COOKIE_PREFIX + page_id)
        if not links.unlocked(rec, cookie, max_age_s=self.unlock_max_age_s):
            raise HTTPException(401, "Password required")

    async def unlock(self, request: Request, page_id: str) -> Response:
        """Check the posted password; set the unlock cookie when it is right."""
        found = self._open_record(page_id)
        if found is None:
            return Response(status_code=204)
        links, rec = found
        if not rec.get("password"):
            return Response(status_code=204)
        try:
            raw = await read_capped(request, MAX_UNLOCK_BYTES, what="Password")
            data = json.loads(raw or b"{}")
            password = data["password"]
            if not isinstance(password, str):
                raise TypeError
        except (ValueError, KeyError, TypeError, RecursionError):
            raise HTTPException(400, "Expected a JSON object with a password") from None
        if self._locked.get(page_id, 0) > self._clock() or not self.rate_limiter.allow(
            f"{page_id}:{self._client(request)}"
        ):
            raise HTTPException(
                429, "Too many attempts. Wait a few minutes and try again."
            )
        if not links.check_password(rec, password):
            if not self.page_failure_limiter.allow(page_id):
                self._locked[page_id] = self._clock() + self.lock_s
            raise HTTPException(401, "That password is not right.")
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        response = Response(status_code=204, headers={"Cache-Control": "no-store"})
        response.set_cookie(
            COOKIE_PREFIX + page_id,
            links.unlock_value(rec),
            max_age=links.unlock_max_age(rec, max_age_s=self.unlock_max_age_s),
            path=request.scope.get("root_path") or "/",
            httponly=True,
            samesite="strict",
            secure=proto.split(",")[0].strip() == "https",
        )
        return response

    def _client(self, request: Request) -> str:
        peer = request.client.host if request.client else None
        ip = client_ip(
            peer,
            request.headers.get("x-forwarded-for"),
            trusted_proxies=self.trusted_proxies,
        )
        return rate_limit_key(ip)
