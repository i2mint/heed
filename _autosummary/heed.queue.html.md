# heed.queue

The feedback queue as plain functions: list, get, triage, dedup, create, close (#8).

One set of core functions over a report store (the `MutableMapping` a
[`StoreSink`](heed.sinks.store.html.md#heed.sinks.store.StoreSink) writes, e.g.
[`heed.storage.make_report_store()`](heed.storage.html.md#heed.storage.make_report_store)),
shared by every surface: [`heed.mcp`](heed.mcp.html.md#module-heed.mcp) exposes them as MCP tools, and a CLI or an
admin page would call the same ones. Arguments are flat and results are JSON-ready
dicts, so a tool layer adds nothing but transport.

`create` goes through [`heed.ingest.process_submission()`](heed.ingest.html.md#heed.ingest.process_submission), the same path as a
widget submission (validation, enrichment), so an agent cannot write a report the
HTTP router would not.

```pycon
>>> q = FeedbackQueue({})
>>> rid = q.create("Export to CSV fails", body="the button errors")["id"]
>>> [r["title"] for r in q.list()]
['Export to CSV fails']
>>> q.triage(rid)["severity"]
'high'
>>> q.close(rid, status="completed")["status"]
'completed'
```

### Module Attributes

| [`CLOSING_STATUSES`](#heed.queue.CLOSING_STATUSES)   | Statuses a report can be closed with (`duplicate` needs `duplicate_of`).   |
|---------------------------------------------------------------------|----------------------------------------------------------------------------|

### Classes

| [`FeedbackQueue`](#heed.queue.FeedbackQueue)(store, \*[, llm, confirm])   | The queue's core operations over a report store.   |
|---------------------------------------------------------------------------------------------|----------------------------------------------------|

### heed.queue.CLOSING_STATUSES *= (Status.completed, Status.declined, Status.duplicate)*

Statuses a report can be closed with (`duplicate` needs `duplicate_of`).

### *class* heed.queue.FeedbackQueue(store, , llm=None, confirm=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The queue’s core operations over a report store.

* **Parameters:**
  * **store** ([`MutableMapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.MutableMapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – `report id -> report JSON` (a StoreSink’s store).
  * **llm** ([`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)], [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)] | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – for [`triage()`](#heed.queue.FeedbackQueue.triage) (`prompt -> text`); keyword rules without it.
  * **confirm** ([`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[`Report`](heed.base.html.md#heed.base.Report), [`Report`](heed.base.html.md#heed.base.Report)], [`float`](https://docs.python.org/3/builtins/functions.html#float)] | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – the dedup judge for [`duplicates()`](#heed.queue.FeedbackQueue.duplicates) (optional).

#### close(report_id, , status='completed', duplicate_of=None)

Close a report: `completed`, `declined` or `duplicate` (of another).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

#### create(title, , body='', category='bug')

File a report through the widget’s own submission path.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

#### duplicates(report_id, , k=5)

Likely duplicates of a stored report (nothing is marked).

* **Return type:**
  [`list`](#heed.queue.FeedbackQueue.list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]]

#### get(report_id)

One report in full (JSON-ready).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

#### list(, status=None, category=None, limit=50)

Reports, newest first, optionally of one `status` and `category`.

* **Return type:**
  [`list`](#heed.queue.FeedbackQueue.list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]]

#### tools()

The operations as named functions (the MCP tools, one per operation).

* **Return type:**
  [`list`](#heed.queue.FeedbackQueue.list)[[`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]]

#### triage(report_id, , apply=False)

A triage proposal; with `apply`, its category and labels are recorded and
the status becomes `triaged` (a report already closed is left as it is).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]
