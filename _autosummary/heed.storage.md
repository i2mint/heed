# heed.storage

Storage helpers — dol-backed stores for reports and binary attachments.

Stores are plain `MutableMapping``s, so the rest of heed never depends on a specific
backend. Defaults are in-memory; pass a directory to persist to the filesystem, or
inject any ``dol` store (S3, Mongo, …).

### Functions

| [`make_attachment_store`](#heed.storage.make_attachment_store)([rootdir])   | A bytes store for attachments.   |
|-------------------------------------------------------------------------------------|----------------------------------|
| [`make_report_store`](#heed.storage.make_report_store)([rootdir])       | A text store for report JSON.    |

### heed.storage.make_attachment_store(rootdir=None)

A bytes store for attachments. In-memory unless `rootdir` is given.

* **Return type:**
  [`MutableMapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.MutableMapping)

### heed.storage.make_report_store(rootdir=None)

A text store for report JSON. In-memory unless `rootdir` is given.

* **Return type:**
  [`MutableMapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.MutableMapping)
