# heed.dedup

Duplicate detection in two stages: cheap retrieval, then confirmation (#8).

1. **Retrieval** ranks the stored reports by word overlap with the incoming one
   (title counted twice, body once; Jaccard over folded words) and keeps the
   `k` best above `min_score`. No model, no index: fine for a feedback queue.
2. **Confirmation** (optional) asks an injected `confirm(new, candidate) -> float`
   judge, e.g. an LLM or an embedding similarity, and keeps those it scores at
   `confirm_threshold` or more.

The result is a list of [`DupCandidate`](heed.sinks.base.html.md#heed.sinks.base.DupCandidate); nothing is merged.
[`mark_duplicate()`](#heed.dedup.mark_duplicate) records a decision on the stored report (`status` becomes
`duplicate` and `extra["duplicate_of"]` names the original), so a human or an
agent applies it explicitly.

```pycon
>>> from heed.base import Identity, Report
>>> me = Identity(anon_id="a")
>>> old = Report(id="r1", title="Save button crashes the app", identity=me)
>>> new = Report(id="r2", title="App crashes when I press save", identity=me)
>>> [c.external_ref for c in find_duplicates(new, [old])]
['r1']
```

### Module Attributes

| [`Confirm`](#heed.dedup.Confirm)   | the confirming judge.   |
|------------------------------------------------------------|-------------------------|

### Functions

| [`find_duplicates`](#heed.dedup.find_duplicates)(report, candidates, \*[, k, ...])   | Likely duplicates of `report` among `candidates`, best first.               |
|------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------|
| [`mark_duplicate`](#heed.dedup.mark_duplicate)(store, report_id, \*, of)            | Record that stored report `report_id` duplicates `of` (both must exist).    |
| [`stored_reports`](#heed.dedup.stored_reports)(store)                               | The reports in a StoreSink store (entries that do not parse are skipped).   |
| [`word_similarity`](#heed.dedup.word_similarity)(a, b)                               | Jaccard similarity of the reports' folded words (title words weigh double). |

### heed.dedup.Confirm

the confirming judge.

* **Type:**
  `(incoming, candidate) -> similarity in [0, 1]`

alias of [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[`Report`](heed.base.html.md#heed.base.Report), [`Report`](heed.base.html.md#heed.base.Report)], [`float`](https://docs.python.org/3/builtins/functions.html#float)]

### heed.dedup.find_duplicates(report, candidates, , k=5, min_score=0.25, confirm=None, confirm_threshold=0.5)

Likely duplicates of `report` among `candidates`, best first.

Reports already marked duplicate, and `report` itself, are skipped. With
`confirm`, each retrieved candidate is re-scored by it and kept only at
`confirm_threshold` or more; a judge that raises drops that candidate.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`DupCandidate`](heed.sinks.base.html.md#heed.sinks.base.DupCandidate)]

### heed.dedup.mark_duplicate(store, report_id, , of)

Record that stored report `report_id` duplicates `of` (both must exist).

* **Return type:**
  [`Report`](heed.base.html.md#heed.base.Report)

### heed.dedup.stored_reports(store)

The reports in a StoreSink store (entries that do not parse are skipped).

* **Return type:**
  [`Iterable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Iterable)[[`Report`](heed.base.html.md#heed.base.Report)]

### heed.dedup.word_similarity(a, b)

Jaccard similarity of the reports’ folded words (title words weigh double).

```pycon
>>> from heed.base import Identity, Report
>>> me = Identity(anon_id="a")
>>> word_similarity(Report(title="Dark mode", identity=me),
...                 Report(title="dark  MODE please", identity=me))
0.6667
```

* **Return type:**
  [`float`](https://docs.python.org/3/builtins/functions.html#float)
