# heed.review

Review pages: media (a video, audio, images) with a comment thread under it.

Publish one or more media files under a title and get a link; whoever has the link
watches the media and reads and adds comments (a name per comment, optionally pinned
to a moment in the video), on every visit, without an account. The owner deletes
comments, over HTTP when `is_moderator` says so or from the CLI. On an origin shared
with other apps, use `token_moderator` (a typed-in secret) rather than a login alone:
a login is ambient, so any script on that origin acts as the owner (#74).

The link is a **capability**: the page id is 128 random bits and travels in the URL
*fragment* (`https://host/review/#<id>`), so the link itself is never sent in a
request or a `Referer` (the API calls the page makes do carry the id in their
paths, so the host’s access logs see it). Without the id a page, its media and its
thread are all 404s. On top of that the thread endpoint is rate-limited per IP and per
thread, capped per thread, size-capped, and has a honeypot (see [`heed.threads`](heed.threads.html.md#module-heed.threads)).

Layout of the data root (one folder; ADR-0024’s S3 layout mirrors it key for key):

```default
pages/<id>.json            the page: title, note, media list, created_at
media/<id>/<name>          the media files, served with byte ranges (seekable)
comments/<id>/<cid>.json   the thread (heed.threads)
access/<id>.json           optional password, expiry, revoke (heed.review_access);
                           no file means open: back this folder up too
```

Python:

```default
>>> import tempfile
>>> root = tempfile.mkdtemp()
>>> clip = os.path.join(root, "cut1.mp4"); _ = open(clip, "wb").write(b"\0" * 10)
>>> page = publish_review([clip], title="Plot car, cut 1", rootdir=root)
>>> [m.name for m in page.media], len(page.id)
(['cut1.mp4'], 22)
>>> url = review_url(page.id, "https://apps.example/review/")
>>> url == "https://apps.example/review/#" + page.id
True
```

CLI: `heed review publish cut1.mp4 --title "Plot car"` prints the link;
`heed review comments <id>` reads the thread back (`--json` for agents).

### Module Attributes

| [`DEFAULT_DIR`](#heed.review.DEFAULT_DIR)          | Where review pages live when neither `rootdir` nor `HEED_REVIEW_DIR` is given.   |
|-----------------------------------------------------------------------|----------------------------------------------------------------------------------|
| [`CONFIG_FILE`](#heed.review.CONFIG_FILE)          | The optional config file that may set `[review] base_url` (and `dir`).           |
| [`PRIVATE_PAGE_HEADERS`](#heed.review.PRIVATE_PAGE_HEADERS) | never indexed, never leak the URL onward.                                        |

### Functions

| [`default_base_url`](#heed.review.default_base_url)()                            | `HEED_REVIEW_BASE_URL`, else `[review] base_url` in the config file.     |
|------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| [`default_rootdir`](#heed.review.default_rootdir)()                             | `HEED_REVIEW_DIR`, else `[review] dir` in the config file, else default. |
| [`main`](#heed.review.main)([argv])                                  | `heed review ...`: publish pages, read their comments, moderate, serve.  |
| [`make_review_app`](#heed.review.make_review_app)([rootdir, is_moderator, ...]) | The review API (and, with `serve_shell`, the page at `/`).               |
| [`media_kind`](#heed.review.media_kind)(filename)                          | `(kind, content_type)` of a media file, refusing anything not media.     |
| [`page_shell`](#heed.review.page_shell)(\*[, api, title])                  | The page's HTML shell: it loads the script and style from `{api}/ui/`.   |
| [`publish_review`](#heed.review.publish_review)(media, \*[, title, note, ...]) | Publish `media` as a new review page (or add it to page `to`).           |
| [`review_url`](#heed.review.review_url)(page_id[, base_url])               | The shareable link of a page: the base URL with the id as its fragment.  |
| [`safe_media_name`](#heed.review.safe_media_name)(filename)                     | A file name made safe to store and serve (no path, no odd characters).   |

### Classes

| [`MediaItem`](#heed.review.MediaItem)(\*, name, kind, content_type, bytes)   | One media file of a page.                                                   |
|---------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------|
| [`ReviewPage`](#heed.review.ReviewPage)(\*, id, title[, note, media, ...])    | A published review page (its comments live beside it, in the thread store). |
| [`ReviewStores`](#heed.review.ReviewStores)([rootdir])                          | The three stores of a data root: pages, media files, comments.              |

### heed.review.CONFIG_FILE *= '~/.config/heed/config.toml'*

The optional config file that may set `[review] base_url` (and `dir`).

### heed.review.DEFAULT_DIR *= '~/.local/share/heed/review'*

Where review pages live when neither `rootdir` nor `HEED_REVIEW_DIR` is given.

### *class* heed.review.MediaItem(, name, kind, content_type, bytes)

Bases: `BaseModel`

One media file of a page.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### heed.review.PRIVATE_PAGE_HEADERS *= {'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff', 'X-Robots-Tag': 'noindex, nofollow'}*

never indexed, never leak the URL onward.

* **Type:**
  Headers on every review response

### *class* heed.review.ReviewPage(\*, id, title, note=None, media=<factory>, created_at, updated_at=None)

Bases: `BaseModel`

A published review page (its comments live beside it, in the thread store).

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.review.ReviewStores(rootdir=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The three stores of a data root: pages, media files, comments.

Pages are a text `MutableMapping` (swap in any `dol` store); `threads` maps
a page id to its comment store ([`heed.threads.folder_threads()`](heed.threads.html.md#heed.threads.folder_threads)); media are
files, because video must be served with byte ranges.

#### all_pages()

Every page, newest first.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`ReviewPage`](#heed.review.ReviewPage)]

#### comments_of(page_id)

The thread of `page_id`, oldest first.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Comment`](heed.threads.html.md#heed.threads.Comment)]

#### media_path(page_id, name)

The file of media `name` of `page_id`, if the page lists it.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### page(page_id)

The page `page_id`, or `None` if there is none (or the id is invalid).

* **Return type:**
  [`ReviewPage`](#heed.review.ReviewPage) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### save(page)

Write `page`.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### heed.review.default_base_url()

`HEED_REVIEW_BASE_URL`, else `[review] base_url` in the config file.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### heed.review.default_rootdir()

`HEED_REVIEW_DIR`, else `[review] dir` in the config file, else default.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### heed.review.main(argv=None)

`heed review ...`: publish pages, read their comments, moderate, serve.

* **Return type:**
  [`int`](https://docs.python.org/3/builtins/functions.html#int)

### heed.review.make_review_app(rootdir=None, \*, is_moderator=<function no_moderator>, trusted_proxies=(), rate_limiter=None, rate_limit_per_minute=10, max_comments_per_thread=500, serve_shell=True, gate=None)

The review API (and, with `serve_shell`, the page at `/`).

Routes: `GET /pages/{id}`, `GET /pages/{id}/media/{name}` (byte ranges;
`?download=1` serves it as an attachment, so a phone saves the file),
`GET|POST /threads/{id}`, `DELETE /threads/{id}/{comment}` (moderators),
`GET /ui/review.{js,css}`. There is deliberately no HTTP listing of pages:
it would hand every capability to any script running as the owner (the CLI’s
`ls` lists them).

A page given a password, an expiry or a revoke ([`publish_review()`](#heed.review.publish_review),
`heed review revoke`) is gated by `gate` (a `ReviewGate`, built over
`rootdir` with `trusted_proxies` by default): 401 until `POST
/pages/{id}/unlock` `{"password": ...}` sets a cookie, 410 once expired, 404
once revoked. Pages with no such record are open, as ever.

* **Return type:**
  `FastAPI`

### heed.review.media_kind(filename)

`(kind, content_type)` of a media file, refusing anything not media.

```pycon
>>> media_kind("a.mp4")
('video', 'video/mp4')
>>> media_kind("a.html")
Traceback (most recent call last):
...
ValueError: Not a video, audio or image file: 'a.html' (text/html)
```

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### heed.review.page_shell(, api='', title='Review')

The page’s HTML shell: it loads the script and style from `{api}/ui/`.

A host that serves static files (enlace’s `frontend_dir`) writes this once:
`heed review page-shell --api /api/review > frontend/index.html`.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### heed.review.publish_review(media, , title=None, note=None, rootdir=None, to=None, password=None, expires=None)

Publish `media` as a new review page (or add it to page `to`).

`password` and `expires` (`"7d"`, `"2026-11-01"`, `"never"`) gate the
page ([`heed.review_access`](heed.review_access.html.md#module-heed.review_access), needs `heed[gate]`); on an existing page they
change only what is given. Neither: the page is open to whoever has the link.

The files are copied into the data root. Adding to an existing page keeps its
link and its thread, so a new cut can go where the old one’s comments are; a file
with the name of one already on the page replaces it.

* **Return type:**
  [`ReviewPage`](#heed.review.ReviewPage)

### heed.review.review_url(page_id, base_url=None)

The shareable link of a page: the base URL with the id as its fragment.

```pycon
>>> review_url("abc", "https://apps.example/review")
'https://apps.example/review/#abc'
>>> review_url("abc", "")  # no base URL: the fragment alone
'#abc'
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### heed.review.safe_media_name(filename)

A file name made safe to store and serve (no path, no odd characters).

```pycon
>>> safe_media_name("../../etc/My Cut (v2).mp4")
'My Cut _v2_.mp4'
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)
