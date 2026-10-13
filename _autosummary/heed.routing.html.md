# heed.routing

Where a report goes: server-side site resolution and moderation (holding state).

Two seams, both decided on the server, never by the browser:

- **Site resolution**: a `SinkResolver` maps the *validated* request origin to
  the sink for that site (e.g. which GitHub repo). The widget never names a target, so
  a visitor cannot redirect reports into someone else’s tracker.
  [`sinks_by_origin()`](#heed.routing.sinks_by_origin) builds one from a plain mapping.
- **Moderation**: a `Moderator` looks at a server-enriched report and returns a
  [`Verdict`](#heed.routing.Verdict). `hold` parks the report in a holding sink (status `held`)
  > until a human releases it with [`release_held()`](#heed.routing.release_held); `reject` drops it.

  [`accept_all()`](#heed.routing.accept_all) is the default, [`hold_all()`](#heed.routing.hold_all) gives full pre-moderation, and
  [`make_heuristic_moderator()`](#heed.routing.make_heuristic_moderator) holds link floods and blocked terms.

### Module Attributes

| [`CLAIM_PREFIX`](#heed.routing.CLAIM_PREFIX)   | Prefix of the keys reports are claimed under, in the holding store itself, while they are being released (when no separate `claimed_store` is given).   |
|-----------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------|

### Functions

| [`accept_all`](#heed.routing.accept_all)(report)                                | The default moderator: everything goes straight to the sink.                 |
|----------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------|
| [`chain_moderators`](#heed.routing.chain_moderators)(\*moderators)                    | Combine moderators; the most severe verdict wins (reject > hold > accept).   |
| [`fold_text`](#heed.routing.fold_text)(text)                                   | Normalise text for matching: NFKC, drop format chars (zero-width), casefold. |
| [`hold_all`](#heed.routing.hold_all)(report)                                  | Full pre-moderation: every report waits for a human.                         |
| [`make_heuristic_moderator`](#heed.routing.make_heuristic_moderator)(\*[, max_links, ...])    | Hold reports with more than `max_links` links or any of `blocked_terms`.     |
| [`recover_claimed`](#heed.routing.recover_claimed)(\*, holding_store[, ...])         | Return every claimed report to holding, and the ids returned.                |
| [`release_held`](#heed.routing.release_held)(report_id, \*, holding_store[, ...]) | Release a held report: route it on and remove it from holding.               |
| [`sinks_by_origin`](#heed.routing.sinks_by_origin)(sinks, \*[, default])             | A resolver from an `{origin: sink}` mapping (origins compared normalized).   |

### Classes

| [`Verdict`](#heed.routing.Verdict)(\*values)   | A moderator's decision about one report.   |
|----------------------------------------------------------------------|--------------------------------------------|

### heed.routing.CLAIM_PREFIX *= 'claimed--'*

Prefix of the keys reports are claimed under, in the holding store itself, while
they are being released (when no separate `claimed_store` is given).

### *class* heed.routing.Verdict(\*values)

Bases: [`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Enum`](https://docs.python.org/3/library/enum.html#enum.Enum)

A moderator’s decision about one report.

### heed.routing.accept_all(report)

The default moderator: everything goes straight to the sink.

* **Return type:**
  [`Verdict`](#heed.routing.Verdict)

### heed.routing.chain_moderators(\*moderators)

Combine moderators; the most severe verdict wins (reject > hold > accept).

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[`Report`](heed.base.html.md#heed.base.Report)], [`Verdict`](#heed.routing.Verdict)]

### heed.routing.fold_text(text)

Normalise text for matching: NFKC, drop format chars (zero-width), casefold.

Defeats fullwidth letters and zero-width splitting; it does not map cross-script
look-alikes (e.g. Cyrillic “а”), which needs a confusables table.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> fold_text("ＣＡＳ​INO")
'casino'
```

### heed.routing.hold_all(report)

Full pre-moderation: every report waits for a human.

* **Return type:**
  [`Verdict`](#heed.routing.Verdict)

### heed.routing.make_heuristic_moderator(, max_links=3, blocked_terms=(), on_blocked_term=Verdict.hold)

Hold reports with more than `max_links` links or any of `blocked_terms`.

Cheap, dependency-free spam signals. Holding (not rejecting) is the default so a
false positive costs a human click, not a lost report.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[`Report`](heed.base.html.md#heed.base.Report)], [`Verdict`](#heed.routing.Verdict)]

```pycon
>>> mod = make_heuristic_moderator(max_links=1, blocked_terms=["casino"])
>>> from heed.base import Identity
>>> def r(body): return Report(title="t", body=body, identity=Identity(anon_id="a"))
>>> [mod(r(b)).value for b in ["ok", "http://a http://b", "best CASINO"]]
['accept', 'hold', 'hold']
```

### heed.routing.recover_claimed(, holding_store, claimed_store=None)

Return every claimed report to holding, and the ids returned.

A claim outlives its [`release_held()`](#heed.routing.release_held) only if the process died mid-release
(the sink may or may not have accepted the report, so a recovered report may be
sent twice – never zero times). Call this when no release is running – e.g. at
startup – since an in-flight release’s claim looks the same. Pass the same
`claimed_store` as to [`release_held()`](#heed.routing.release_held).

Only a valid report (whose own id matches its claim key) is ever written to
holding, and a valid holding copy is never replaced: a claim whose report is
intact in holding is just dropped (even a torn one), and one whose holding copy
is torn or missing restores it. Otherwise a claim that is not a valid report is
left in place, and not returned.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### heed.routing.release_held(report_id, , holding_store, sink=None, resolve_sink=None, claimed_store=None)

Release a held report: route it on and remove it from holding.

`holding_store` is the store behind the holding sink (a `StoreSink`’s
`.store`). The destination is `sink`, or — for a multi-site backend — the sink
`resolve_sink` gives for the report’s own origin (pass exactly one).

The report is claimed before submitting, so a second release while the first is in
flight finds nothing to send. Claiming *moves* it: it is written under a claim key
of its own (`claimed--<id>--<token>` in `holding_store`, or `<id>--<token>`
in `claimed_store`, a store used for nothing else) and only then deleted from
holding. If the sink fails the report is put back in holding; a release only ever
deletes its own claim, and only once the sink has accepted the report or it is
back in holding. So a crash at any point leaves the report in holding, in the
claims, or in both – never lost – and [`recover_claimed()`](#heed.routing.recover_claimed) returns stranded
claims to holding. A report with a claim is not released again until then.

* **Return type:**
  [`SinkResult`](heed.sinks.base.html.md#heed.sinks.base.SinkResult)

### heed.routing.sinks_by_origin(sinks, , default=None)

A resolver from an `{origin: sink}` mapping (origins compared normalized).

Unknown origins resolve to `default`; with no default they resolve to None, and
the router refuses them.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)], [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Sink`](heed.sinks.base.html.md#heed.sinks.base.Sink)]]

```pycon
>>> from heed.sinks import StoreSink
>>> a = StoreSink({})
>>> resolve = sinks_by_origin({"https://a.example": a})
>>> resolve("HTTPS://A.example/") is a, resolve("https://b.example")
(True, None)
```
