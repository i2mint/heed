# heed.threads

Comment threads: a persistent, public, append-mostly conversation per key.

A *thread* is the list of comments left under one thing (a review page, a video, a
document). Anyone who can reach the thread can read it and add to it; only a
moderator can delete. This module is the reusable core of that mechanism, independent
of what the thread is attached to (`heed.review` attaches it to a media page):

- [`Comment`](#heed.threads.Comment) / [`CommentSubmission`](#heed.threads.CommentSubmission) – the stored comment and what an
  untrusted client may send (a name, a body, optionally a position in a media item:
  `at_ms`, integer milliseconds, and `media`, the item’s name).
- [`add_comment()`](#heed.threads.add_comment), [`list_comments()`](#heed.threads.list_comments), [`delete_comment()`](#heed.threads.delete_comment) – pure
  functions over ONE thread’s store, a `MutableMapping` of `{comment_id}.json`
  keys, so a `dict`, a folder or an S3 `dol` store all work.
- *Threads* are a callable `thread -> store`: [`memory_threads()`](#heed.threads.memory_threads) (dicts) or
  [`folder_threads()`](#heed.threads.folder_threads) (one folder per thread), so reading a thread never lists
  > any other thread.
- [`make_thread_router()`](#heed.threads.make_thread_router) – `GET`/`POST` `/threads/{thread}` and
  `DELETE /threads/{thread}/{comment_id}`, hardened like heed’s ingest endpoint
  (per-IP and per-thread rate limits, per-thread cap, honeypot, body cap, strict
  validation).

Who may reach a thread is the host’s decision, passed in as `thread_exists`: an
unknown thread is a 404, so a thread keyed by an unguessable id is a capability.

```pycon
>>> store = {}
>>> sub = CommentSubmission(author="Noel", body="Nice cut", at_ms=1500)
>>> c = add_comment(store, "t1", sub)
>>> [(x.author, x.body, x.at_ms) for x in list_comments(store)]
[('Noel', 'Nice cut', 1500)]
>>> delete_comment(store, c.id)
True
>>> list_comments(store)
[]
```

### Module Attributes

| [`MEDIA_NAME_PATTERN`](#heed.threads.MEDIA_NAME_PATTERN)      | a plain file name, no path.      |
|--------------------------------------------------------------------------|----------------------------------|
| [`ID_PATTERN`](#heed.threads.ID_PATTERN)              | no separators, no dots.          |
| [`MODERATE_HEADER`](#heed.threads.MODERATE_HEADER)         | a cross-site form cannot set it. |
| [`THREAD_RESPONSE_HEADERS`](#heed.threads.THREAD_RESPONSE_HEADERS) | never cached, never indexed.     |

### Functions

| [`add_comment`](#heed.threads.add_comment)(store, thread, submission, \*[, ...])   | Store `submission` in `thread`'s `store`; return the stored comment.         |
|------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------|
| [`count_comments`](#heed.threads.count_comments)(store)                               | How many comments a thread holds (without parsing them).                     |
| [`delete_comment`](#heed.threads.delete_comment)(store, comment_id)                   | Delete one comment; `False` if there was none (or the id is invalid).        |
| [`email_moderator`](#heed.threads.email_moderator)(emails)                             | A moderator check passing requests whose authenticated email is in `emails`. |
| [`folder_threads`](#heed.threads.folder_threads)(rootdir)                             | Threads as folders: `<rootdir>/<thread>/<comment_id>.json`.                  |
| [`list_comments`](#heed.threads.list_comments)(store)                                | A thread's comments, oldest first.                                           |
| [`make_thread_router`](#heed.threads.make_thread_router)([threads, thread_exists, ...])   | The HTTP surface of comment threads (`threads`: thread id -> its store).     |
| [`memory_threads`](#heed.threads.memory_threads)()                                    | Threads held in dicts, one per thread (tests, single-process demos).         |
| [`new_comment_id`](#heed.threads.new_comment_id)(\*[, clock])                         | A comment id that sorts by creation time (ms, zero-padded) then at random.   |
| [`no_moderator`](#heed.threads.no_moderator)(request)                               | The default moderator check: nobody may delete over HTTP (use the CLI).      |
| [`valid_id`](#heed.threads.valid_id)(value)                                     | Whether `value` is a safe thread or comment id.                              |

### Classes

| [`Comment`](#heed.threads.Comment)(\*, author, body[, at_ms, media, ...])    | A stored comment: the submission plus a server-assigned id, time and role.   |
|----------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------|
| [`CommentSubmission`](#heed.threads.CommentSubmission)(\*, author, body[, at_ms, ...]) | What a visitor may send: a name, a comment, and optionally a media position. |

### *class* heed.threads.Comment(, author, body, at_ms=None, media=None, id, thread, created_at, moderator=False)

Bases: [`CommentSubmission`](#heed.threads.CommentSubmission)

A stored comment: the submission plus a server-assigned id, time and role.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

#### moderator *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

the page marks it, so a name cannot pass
for the owner’s.

* **Type:**
  Posted by a moderator (the owner)

### *class* heed.threads.CommentSubmission(, author, body, at_ms=None, media=None)

Bases: `BaseModel`

What a visitor may send: a name, a comment, and optionally a media position.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### heed.threads.ID_PATTERN *= '^[A-Za-z0-9_-]{1,64}$'*

no separators, no dots.

* **Type:**
  Thread and comment ids are URL- and path-safe

### heed.threads.MEDIA_NAME_PATTERN *= '^[A-Za-z0-9][A-Za-z0-9._ -]\*$'*

a plain file name, no path.

* **Type:**
  Which media item a timed comment refers to

### heed.threads.MODERATE_HEADER *= 'X-Heed-Moderate'*

a cross-site form cannot set it.

* **Type:**
  The header a moderator’s DELETE must carry

### heed.threads.THREAD_RESPONSE_HEADERS *= {'Cache-Control': 'no-store', 'X-Robots-Tag': 'noindex, nofollow'}*

never cached, never indexed.

* **Type:**
  Headers on every thread response

### heed.threads.add_comment(store, thread, submission, , moderator=False)

Store `submission` in `thread`’s `store`; return the stored comment.

* **Return type:**
  [`Comment`](#heed.threads.Comment)

### heed.threads.count_comments(store)

How many comments a thread holds (without parsing them).

* **Return type:**
  [`int`](https://docs.python.org/3/builtins/functions.html#int)

### heed.threads.delete_comment(store, comment_id)

Delete one comment; `False` if there was none (or the id is invalid).

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### heed.threads.email_moderator(emails)

A moderator check passing requests whose authenticated email is in `emails`.

It reads `request.state.user_email`, which an auth middleware sets (enlace_auth
does, on public apps too, when the visitor has a session).

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[`Request`], [`bool`](https://docs.python.org/3/builtins/functions.html#bool)]

### heed.threads.folder_threads(rootdir)

Threads as folders: `<rootdir>/<thread>/<comment_id>.json`.

Deletion is permanent (comments carry names), as in
[`heed.storage.make_report_store()`](heed.storage.html.md#heed.storage.make_report_store).

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)], [`MutableMapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.MutableMapping)]

### heed.threads.list_comments(store)

A thread’s comments, oldest first. Unreadable entries are skipped.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Comment`](#heed.threads.Comment)]

### heed.threads.make_thread_router(threads=None, \*, thread_exists=<function valid_id>, is_moderator=<function no_moderator>, rate_limiter=None, rate_limit_per_minute=10, thread_rate_limiter=None, thread_rate_limit_per_hour=120, max_comments_per_thread=500, trusted_proxies=(), honeypot_field='website', on_comment=None)

The HTTP surface of comment threads (`threads`: thread id -> its store).

`GET /threads/{thread}` lists, `POST /threads/{thread}` adds (a JSON body:
`author`, `body`, optional `at_ms` and `media`),
`DELETE /threads/{thread}/{id}` deletes when `is_moderator(request)` and the
request carries the `X-Heed-Moderate` header. A thread for which
`thread_exists` is false answers 404 to everything, which is how a host makes
threads capabilities. A filled `honeypot_field` is accepted silently and
stored nowhere. Behind a reverse proxy, set `trusted_proxies` or every visitor
shares the proxy’s rate-limit bucket.

```pycon
>>> from fastapi import FastAPI
>>> from fastapi.testclient import TestClient
>>> app = FastAPI(); app.include_router(make_thread_router())
>>> client = TestClient(app)
>>> client.post("/threads/t1", json={"author": "Thor", "body": "hi"}).status_code
201
>>> [c["body"] for c in client.get("/threads/t1").json()["comments"]]
['hi']
```

* **Return type:**
  `APIRouter`

### heed.threads.memory_threads()

Threads held in dicts, one per thread (tests, single-process demos).

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)], [`MutableMapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.MutableMapping)]

### heed.threads.new_comment_id(\*, clock=<built-in function time>)

A comment id that sorts by creation time (ms, zero-padded) then at random.

```pycon
>>> a, b = new_comment_id(clock=lambda: 1.0), new_comment_id(clock=lambda: 2.0)
>>> a < b and len(a) == len(b)
True
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### heed.threads.no_moderator(request)

The default moderator check: nobody may delete over HTTP (use the CLI).

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### heed.threads.valid_id(value)

Whether `value` is a safe thread or comment id.

```pycon
>>> valid_id("abc_DEF-1"), valid_id("../x"), valid_id("a.json")
(True, False, False)
```

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)
