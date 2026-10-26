# heed.review_access

Access control for review pages: an optional password, expiry and revoke.

A review page is a capability (its 128-bit id), and by default stays one forever. This
module adds what a link sent to a client needs, on top of [caplinks](https://github.com/i2mint/caplinks) (the optional `heed[gate]` extra): a password,
an expiry, and a revoke, kept as one record per page under `<root>/access/<id>.json`.

A page **with no record is open**, exactly as before; a page with one is gated. So
nothing changes for existing pages, and `caplinks` is only imported when a record
exists or one is being written (a gated page whose `caplinks` is missing answers 503,
never open).

Python:

```default
>>> import tempfile
>>> root = tempfile.mkdtemp()
>>> page_id = "A" * 22
>>> rec = set_access(root, page_id, password="open sesame", expires="7d")
>>> rec["protected"]
True
>>> revoke_access(root, page_id)["revoked"] is not None
True
```

HTTP: [`ReviewGate`](#heed.review_access.ReviewGate) is what [`heed.review.make_review_app()`](heed.review.html.md#heed.review.make_review_app) consults. A gated
page answers 401 until `POST /pages/{id}/unlock` with the password sets a cookie
(HMAC keyed by the stored hash, so a new password or a revoke ends every open session);
an expired page answers 410, a revoked one 404. Wrong passwords are limited per client
per page, and a page that takes too many wrong ones from everyone is locked for a while.

### Module Attributes

| [`COOKIE_ID_HEX`](#heed.review_access.COOKIE_ID_HEX)          | hex chars of the page id's hash in the cookie name (the id itself is the capability and must not travel to other apps on the origin)   |
|-------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------|
| [`DFLT_MAX_ATTEMPTS`](#heed.review_access.DFLT_MAX_ATTEMPTS)      | password tries one client gets on one page per window                                                                                  |
| [`DFLT_MAX_PAGE_FAILURES`](#heed.review_access.DFLT_MAX_PAGE_FAILURES) | wrong passwords one page takes from everyone per window before it locks                                                                |
| [`DFLT_UNLOCK_MAX_AGE_S`](#heed.review_access.DFLT_UNLOCK_MAX_AGE_S)  | how long an unlock cookie lasts (never past the page's expiry)                                                                         |

### Functions

| [`revoke_access`](#heed.review_access.revoke_access)(rootdir, page_id)                   | End page `page_id` for every visitor: it answers 404 from now on.   |
|----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------|
| [`set_access`](#heed.review_access.set_access)(rootdir, page_id, \*[, password, ...]) | Gate page `page_id` (creating its access record, or updating it).   |

### Classes

| [`ReviewGate`](#heed.review_access.ReviewGate)(rootdir, \*[, trusted_proxies, ...])   | The HTTP side of the gate: refuse, unlock, and limit password tries.   |
|----------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|

### heed.review_access.COOKIE_ID_HEX *= 16*

hex chars of the page id’s hash in the cookie name (the id itself is the capability
and must not travel to other apps on the origin)

### heed.review_access.DFLT_MAX_ATTEMPTS *= 10*

password tries one client gets on one page per window

### heed.review_access.DFLT_MAX_PAGE_FAILURES *= 30*

wrong passwords one page takes from everyone per window before it locks

### heed.review_access.DFLT_UNLOCK_MAX_AGE_S *= 604800*

how long an unlock cookie lasts (never past the page’s expiry)

### *class* heed.review_access.ReviewGate(rootdir, \*, trusted_proxies=(), rate_limiter=None, page_failure_limiter=None, unlock_max_age_s=604800, lock_s=300.0, clock=<built-in function monotonic>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The HTTP side of the gate: refuse, unlock, and limit password tries.

`rate_limiter` counts every try of one client on one page;
`page_failure_limiter` counts the wrong passwords a page takes from everyone and,
past its limit, locks the page’s unlock for `lock_s` seconds. Both are anything
with `allow(key) -> bool` (heed’s Redis limiter fits). Behind a reverse proxy set
`trusted_proxies`.

#### check(request, page_id)

Raise `HTTPException` (404 revoked, 410 expired, 401 locked) unless the
page is open to `request`; an ungated page always is.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### *async* unlock(request, page_id)

Check the posted password; set the unlock cookie when it is right.

* **Return type:**
  `Response`

### heed.review_access.revoke_access(rootdir, page_id)

End page `page_id` for every visitor: it answers 404 from now on.

A page with no access record is first gated (so it can be revoked), then revoked.

### heed.review_access.set_access(rootdir, page_id, , password=None, expires=None)

Gate page `page_id` (creating its access record, or updating it).

`password` (at least 8 characters) and `expires` (`"7d"`, `"2026-11-01"`,
an ISO timestamp, `"never"`) are changed only when given, so
`set_access(root, id, expires="30d")` keeps the password. Returns the record
without the hash. A revoked page stays revoked (`KeyError`).
