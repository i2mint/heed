# heed.storage

Storage helpers — dol-backed stores for reports and binary attachments.

Stores are plain `MutableMapping``s, so the rest of heed never depends on a specific
backend. Defaults are in-memory; pass a directory to persist to the filesystem, or
inject any ``dol` store (S3, Mongo, …).

### Module Attributes

| [`CLAIMS_ATTR`](#heed.storage.CLAIMS_ATTR)   | the store its reports are claimed in while `heed.routing.release_held` sends them.   |
|----------------------------------------------------------------|--------------------------------------------------------------------------------------|

### Functions

| [`make_attachment_store`](#heed.storage.make_attachment_store)([rootdir, ...])      | A bytes store for attachments.                                            |
|---------------------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| [`make_holding_store`](#heed.storage.make_holding_store)([rootdir, delete_func]) | A report store for held reports, with a dedicated claims store beside it. |
| [`make_report_store`](#heed.storage.make_report_store)([rootdir, delete_func])  | A text store for report JSON.                                             |

### heed.storage.CLAIMS_ATTR *= 'heed_claims'*

the store its
reports are claimed in while `heed.routing.release_held` sends them.

* **Type:**
  Attribute a holding store made by `make_holding_store` carries

### heed.storage.make_attachment_store(rootdir=None, \*, delete_func=<built-in function remove>, follow_symlinks=False)

A bytes store for attachments. In-memory unless `rootdir` is given.

Deletion is permanent by default, for the reason given in `make_report_store`.
Reading a key whose file is a symlink leading outside `rootdir` raises
`KeyError`, so a misconfigured root cannot make the attachment route serve other
files; `follow_symlinks=True` opts out.

```pycon
>>> make_attachment_store()
{}
```

* **Return type:**
  [`MutableMapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.MutableMapping)

### heed.storage.make_holding_store(rootdir=None, \*, delete_func=<built-in function remove>)

A report store for held reports, with a dedicated claims store beside it.

`release_held` and `recover_claimed` find the claims store on the holding store
(`heed_claims`), so a release lists only the reports being released right now,
not every held report (issue #21), and nothing listing holding (a moderation view,
`StoreSink.find_duplicates`) ever sees a claim. With `rootdir`, claims live in a
sibling directory, `<rootdir>.claims` (a subdirectory would show up in the
holding store’s recursive listing).

```pycon
>>> held = make_holding_store()
>>> held["r1"] = "{}"
>>> list(held), dict(held.heed_claims)
(['r1'], {})
```

* **Return type:**
  [`MutableMapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.MutableMapping)

### heed.storage.make_report_store(rootdir=None, \*, delete_func=<built-in function remove>)

A text store for report JSON. In-memory unless `rootdir` is given.

Deleting a key removes the file for good (`os.remove`). dol’s own default moves it
to the OS trash, which would keep a copy of every deleted, claimed or released
report – and reports can carry personal data. Pass `delete_func` to opt back in.

* **Return type:**
  [`MutableMapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.MutableMapping)
