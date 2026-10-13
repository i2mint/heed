# heed.sinks.store

A sink that persists reports into a Mapping (the default, dependency-free sink).

### Classes

| [`StoreSink`](#heed.sinks.store.StoreSink)(store)   | Persist each report as JSON into a dol-style store keyed by report id.   |
|---------------------------------------------------------------------|--------------------------------------------------------------------------|

### *class* heed.sinks.store.StoreSink(store)

Bases: [`BaseSink`](heed.sinks.base.md#heed.sinks.base.BaseSink)

Persist each report as JSON into a dol-style store keyed by report id.

The zero-configuration default when no external sink (e.g. GitHub) is configured.
`store` is any `MutableMapping[str, str]` (a plain dict, a `dol` file store,
S3, …) — dependency-injected so the sink stays testable.

#### find_duplicates(report)

Cheap title-equality duplicate scan over stored reports.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`DupCandidate`](heed.sinks.base.md#heed.sinks.base.DupCandidate)]
