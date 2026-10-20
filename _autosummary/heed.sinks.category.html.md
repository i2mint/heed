# heed.sinks.category

A sink that sends each category of report to its own sink.

One widget and one [`Report`](heed.base.html.md#heed.base.Report) cover a bug, a feature request and a
question; the sink decides what each becomes. [`CategorySink`](#heed.sinks.category.CategorySink) is that decision as
data: bugs to the tracker, feature requests somewhere else (a second repo, a Discussions
sink, a store), anything unlisted to `default`. It is itself a sink, so it nests under
[`sinks_by_origin()`](heed.routing.html.md#heed.routing.sinks_by_origin) and under moderation like any other.

```pycon
>>> from heed.sinks import StoreSink
>>> bugs, ideas = StoreSink({}), StoreSink({})
>>> sink = CategorySink({"feature": ideas}, default=bugs)
>>> sink.sink_for("feature") is ideas and sink.sink_for("bug") is bugs
True
```

### Classes

| [`CategorySink`](#heed.sinks.category.CategorySink)(sinks, \*, default)   | Route by `report.category`; unlisted categories go to `default`.   |
|-------------------------------------------------------------------------------------|--------------------------------------------------------------------|

### *class* heed.sinks.category.CategorySink(sinks, , default)

Bases: [`BaseSink`](heed.sinks.base.html.md#heed.sinks.base.BaseSink)

Route by `report.category`; unlisted categories go to `default`.

#### acknowledge(report)

Hook called after a successful submit (default: no-op).

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### find_duplicates(report)

Return likely duplicates of `report` (default: none).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`DupCandidate`](heed.sinks.base.html.md#heed.sinks.base.DupCandidate)]

#### is_composite_sink *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[[bool](https://docs.python.org/3/builtins/functions.html#bool)]* *= True*

Marks a sink that holds other sinks, so code walking a sink tree (the
held-report release guard) does not mistake any sink with a `sinks` method.

#### public_items(, category=None)

Every distinct member sink’s public items (a shared link once).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`PublicItem`](heed.board.html.md#heed.board.PublicItem)]

#### sink_for(category)

The sink that receives reports of `category`.

* **Return type:**
  [`Sink`](heed.sinks.base.html.md#heed.sinks.base.Sink)

#### sinks()

Every distinct member sink, including the default.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Sink`](heed.sinks.base.html.md#heed.sinks.base.Sink)]
