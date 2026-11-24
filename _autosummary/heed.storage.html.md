# heed.storage

Storage helpers — dol-backed stores for reports and binary attachments.

Stores are plain `MutableMapping``s, so the rest of heed never depends on a specific
backend. Defaults are in-memory; pass a directory to persist to the filesystem, or
inject any ``dol` store (S3, Mongo, …).

### Module Attributes

| [`CLAIMS_ATTR`](#heed.storage.CLAIMS_ATTR)     | the store its reports are claimed in while `heed.routing.release_held` sends them.                   |
|------------------------------------------------------------------|------------------------------------------------------------------------------------------------------|
| [`CLAIM_MOVE_ATTR`](#heed.storage.CLAIM_MOVE_ATTR) | the atomic move `heed.routing.release_held` claims its reports with (issue #24).                     |
| [`ClaimMove`](#heed.storage.ClaimMove)       | take the report out of holding and put it under `claim_key` in the claims store, as one atomic step. |

### Functions

| [`carried_claim_move`](#heed.storage.carried_claim_move)(holding_store)                | The claim move `holding_store` itself carries (set by `make_holding_store`), or None.                                                                        |
|---------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`make_attachment_store`](#heed.storage.make_attachment_store)([rootdir, ...])            | A bytes store for attachments.                                                                                                                               |
| [`make_holding_store`](#heed.storage.make_holding_store)([rootdir, delete_func])       | A report store for held reports, with a dedicated claims store beside it.                                                                                    |
| [`make_report_store`](#heed.storage.make_report_store)([rootdir, delete_func])        | A text store for report JSON.                                                                                                                                |
| [`pop_claim_move`](#heed.storage.pop_claim_move)(holding_store, report_id, ...)    | The atomic claim move for in-process stores: `dict.pop` is atomic, so of two threads releasing one report only one gets it.                                  |
| [`rename_claim_move`](#heed.storage.rename_claim_move)(holding_store, report_id, ...) | The atomic claim move for file stores (`dol` `Files`/`TextFiles`) on one filesystem: `os.rename`, so of two processes releasing one report only one gets it. |

### heed.storage.CLAIMS_ATTR *= 'heed_claims'*

the store its
reports are claimed in while `heed.routing.release_held` sends them.

* **Type:**
  Attribute a holding store made by `make_holding_store` carries

### heed.storage.CLAIM_MOVE_ATTR *= 'heed_claim_move'*

the atomic move
`heed.routing.release_held` claims its reports with (issue #24).

* **Type:**
  Attribute a holding store made by `make_holding_store` carries

### heed.storage.ClaimMove

take the report out of
holding and put it under `claim_key` in the claims store, as one atomic step. Of
several concurrent moves of one report exactly one succeeds; the others raise
`KeyError` and change nothing. A crash leaves the report in at least one of the
two (`heed.routing.recover_claimed` handles a report in both). A release also puts a
report back with it, claims to holding: `move(claims_store, claim_key,
holding_store, report_id)`.

* **Type:**
  `move(holding_store, report_id, claims_store, claim_key)`

alias of [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[`MutableMapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.MutableMapping), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`MutableMapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.MutableMapping), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)], [`None`](https://docs.python.org/3/builtins/constants.html#None)]

### heed.storage.carried_claim_move(holding_store)

The claim move `holding_store` itself carries (set by `make_holding_store`),
or None. Only the store’s own attribute counts: a `dol` wrapper forwards
attribute lookups to the store it wraps, but its keys are not that store’s keys.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[`MutableMapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.MutableMapping), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`MutableMapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.MutableMapping), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)], [`None`](https://docs.python.org/3/builtins/constants.html#None)] | [`None`](https://docs.python.org/3/builtins/constants.html#None)

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
holding store’s recursive listing). It also carries the atomic move a release claims
with (`heed_claim_move`: `os.rename` on disk, `dict.pop` in memory), so two
releases of one report at the same moment cannot both send it (issue #24).

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

### heed.storage.pop_claim_move(holding_store, report_id, claims, claim_key)

The atomic claim move for in-process stores: `dict.pop` is atomic, so of two
threads releasing one report only one gets it.

```pycon
>>> held, claims = {"r1": "{}"}, {}
>>> pop_claim_move(held, "r1", claims, "r1--token")
>>> held, claims
({}, {'r1--token': '{}'})
>>> pop_claim_move(held, "r1", claims, "r1--other")
Traceback (most recent call last):
  ...
KeyError: 'r1'
```

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### heed.storage.rename_claim_move(holding_store, report_id, claims, claim_key)

The atomic claim move for file stores (`dol` `Files`/`TextFiles`) on one
filesystem: `os.rename`, so of two processes releasing one report only one gets
it. Raises `FileNotFoundError` if the claims directory is missing, and refuses
(`ValueError`) a store whose keys do not resolve to absolute paths under its
`rootdir` (e.g. one wrapped by a key codec) or a held report that is a symlink.
On Windows a rename of a file open elsewhere raises `PermissionError`; the report
stays held.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)
