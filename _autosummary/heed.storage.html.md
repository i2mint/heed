# heed.storage

Storage helpers — dol-backed stores for reports and binary attachments.

Stores are plain `MutableMapping``s, so the rest of heed never depends on a specific
backend. Defaults are in-memory; pass a directory to persist to the filesystem, or
inject any ``dol` store (S3, Mongo, …).

### Functions

| [`make_attachment_store`](#heed.storage.make_attachment_store)([rootdir, delete_func])   | A bytes store for attachments.   |
|--------------------------------------------------------------------------------------------------|----------------------------------|
| [`make_report_store`](#heed.storage.make_report_store)([rootdir, delete_func])       | A text store for report JSON.    |

### heed.storage.make_attachment_store(rootdir=None, \*, delete_func=<built-in function remove>)

A bytes store for attachments. In-memory unless `rootdir` is given.

Deletion is permanent by default, for the reason given in `make_report_store`.

* **Return type:**
  [`MutableMapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.MutableMapping)

### heed.storage.make_report_store(rootdir=None, \*, delete_func=<built-in function remove>)

A text store for report JSON. In-memory unless `rootdir` is given.

Deleting a key removes the file for good (`os.remove`). dol’s own default moves it
to the OS trash, which would keep a copy of every deleted, claimed or released
report – and reports can carry personal data. Pass `delete_func` to opt back in.

* **Return type:**
  [`MutableMapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.MutableMapping)
