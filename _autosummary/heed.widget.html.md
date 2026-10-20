# heed.widget

Serve the embeddable widget, and state the contract it is built against.

The widget (TypeScript sources under `widget/` at the repository root) is built into
`heed/static/widget/` and shipped inside the Python package, so the backend that
receives reports can also serve the script that sends them: one host, no CDN, no npm
install for the embedding site.

Two things live here:

* [`make_widget_router()`](#heed.widget.make_widget_router) serves the built files. Only the names listed in the
  build’s `manifest.json` are served (no path ever reaches the filesystem), with
  headers that let any site load them (`Access-Control-Allow-Origin: *` and
  `Cross-Origin-Resource-Policy: cross-origin`, needed for Subresource Integrity and
  for pages with COEP) and long-lived caching for content-hashed names.
* [`widget_contract()`](#heed.widget.widget_contract) is the single source of truth for what the widget sends:
  the form field names the ingest route reads and the limits `base.py` enforces.
  `widget/src/contract.gen.ts` is generated from it (`python -m heed.widget
  contract`) and a test fails when the two drift.

```pycon
>>> c = widget_contract()
>>> c["fields"]["title"], c["limits"]["title_chars"]
('title', 300)
```

### Functions

| [`load_widget_manifest`](#heed.widget.load_widget_manifest)([asset_dir])   | The build's manifest: `{"version", "entries", "files": {name: {...}}}`.     |
|--------------------------------------------------------------------------------------|-----------------------------------------------------------------------------|
| [`main`](#heed.widget.main)([argv])                        | `python -m heed.widget contract`: print the generated TypeScript contract.  |
| [`make_widget_router`](#heed.widget.make_widget_router)(\*[, asset_dir]) | A router serving the built widget at `GET /widget/{name}`.                  |
| [`render_contract_ts`](#heed.widget.render_contract_ts)([contract])      | The TypeScript module `widget/src/contract.gen.ts` (generated, not edited). |
| [`widget_contract`](#heed.widget.widget_contract)([config])           | What the widget sends and the limits it must respect (from heed's SSOT).    |

### heed.widget.load_widget_manifest(asset_dir=None)

The build’s manifest: `{"version", "entries", "files": {name: {...}}}`.

Returns an empty file list when the widget has not been built (a source checkout
that never ran `npm run build`), so the backend still starts.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### heed.widget.main(argv=None)

`python -m heed.widget contract`: print the generated TypeScript contract.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### heed.widget.make_widget_router(, asset_dir=None)

A router serving the built widget at `GET /widget/{name}`.

Include it beside [`heed.make_router()`](heed.html.md#heed.make_router) (`make_app` does) so the widget is
served from the same host that receives its reports. `asset_dir` defaults to the
files shipped in the package; point it elsewhere to serve another build.

* **Return type:**
  `APIRouter`

### heed.widget.render_contract_ts(contract=None)

The TypeScript module `widget/src/contract.gen.ts` (generated, not edited).

```pycon
>>> render_contract_ts().splitlines()[0].startswith("// Generated")
True
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### heed.widget.widget_contract(config=None)

What the widget sends and the limits it must respect (from heed’s SSOT).

`config` supplies the server-configurable names (honeypot, bot token field) and
the screenshot cap; their defaults are what the widget assumes unless an embedder
overrides them.

```pycon
>>> widget_contract()["categories"]
['bug', 'feature', 'question', 'other']
```

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]
