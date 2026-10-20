# heed.board

The read side of a feedback inbox: what a public board may show.

heed’s write path ends at a [`Sink`](heed.sinks.base.html.md#heed.sinks.base.Sink). A *board* (votes, a roadmap,
a changelog) is the read path over what the sinks accumulated, and it is a public
surface over untrusted, user-written text. This module fixes its shape before any UI
exists, so that the surface can be built on GitHub labels today and on a native store
later without touching the widget or the ingest layer:

- [`PublicItem`](#heed.board.PublicItem) – the **only** thing a board may expose: title, category, status,
  link, vote count, date. Never the body, identity, origin, environment or attachments.
- [`BoardSource`](#heed.board.BoardSource) – the protocol a sink implements to be listable
  (`BoardSource.public_items()`). [`BaseSink`](heed.sinks.base.html.md#heed.sinks.base.BaseSink) answers
  `[]`, so a sink that cannot list simply has an empty board.
- [`roadmap()`](#heed.board.roadmap) and [`changelog()`](#heed.board.changelog) – the two views the design names, as pure
  functions over items. Votes rank; status partitions.

Items reach a board only when an operator opts them in (for the GitHub sink, by a
`public` label), because an unmoderated visitor title on a public page is an abuse
vector (`misc/docs/embedding-security.md`).

```pycon
>>> from heed.base import Category, Status
>>> items = [
...     PublicItem(ref="1", title="Dark mode", category=Category.feature,
...                status=Status.planned, votes=5),
...     PublicItem(ref="2", title="Export CSV", category=Category.feature,
...                status=Status.completed, votes=9),
...     PublicItem(ref="3", title="Offline", category=Category.feature,
...                status=Status.triaged, votes=1),
... ]
>>> [i.title for i in roadmap(items)]
['Dark mode', 'Offline']
>>> [i.title for i in changelog(items)]
['Export CSV']
```

### Module Attributes

| [`ROADMAP_STATUSES`](#heed.board.ROADMAP_STATUSES)   | Statuses that mean "still to come" (shown on a roadmap), in display order.   |
|---------------------------------------------------------------------|------------------------------------------------------------------------------|
| [`CHANGELOG_STATUSES`](#heed.board.CHANGELOG_STATUSES) | Statuses that mean "shipped" (shown in a changelog).                         |

### Functions

| [`changelog`](#heed.board.changelog)(items)   | Shipped items, newest first (undated ones last).                        |
|---------------------------------------------------------------------|-------------------------------------------------------------------------|
| [`roadmap`](#heed.board.roadmap)(items)     | Items still to come, started first; most-voted first within each stage. |

### Classes

| [`BoardSource`](#heed.board.BoardSource)(\*args, \*\*kwargs)             | Anything that can list its public items (an optional sink capability).   |
|----------------------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| [`PublicItem`](#heed.board.PublicItem)(\*, ref, title[, category, ...]) | One board entry: the public projection of a stored report.               |

### *class* heed.board.BoardSource(\*args, \*\*kwargs)

Bases: [`Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)

Anything that can list its public items (an optional sink capability).

### heed.board.CHANGELOG_STATUSES *: [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[Status](heed.base.html.md#heed.base.Status), ...]* *= (Status.completed,)*

Statuses that mean “shipped” (shown in a changelog).

### *class* heed.board.PublicItem(, ref, title, category=Category.feature, status=Status.received, url=None, votes=0, created_at=None, closed_at=None)

Bases: `BaseModel`

One board entry: the public projection of a stored report.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### heed.board.ROADMAP_STATUSES *: [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[Status](heed.base.html.md#heed.base.Status), ...]* *= (Status.started, Status.planned, Status.triaged, Status.received)*

Statuses that mean “still to come” (shown on a roadmap), in display order.

### heed.board.changelog(items)

Shipped items, newest first (undated ones last).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`PublicItem`](#heed.board.PublicItem)]

### heed.board.roadmap(items)

Items still to come, started first; most-voted first within each stage.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`PublicItem`](#heed.board.PublicItem)]
