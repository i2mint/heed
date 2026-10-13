# heed

**Embeddable, no-install, framework-agnostic end-user feedback** — a drop-in widget
that lets *any visitor* of your deployed web app report a bug or request a feature,
with useful context gathered automatically, routed to a pluggable backend (GitHub
Issues first). Works standalone; integrates cleanly with
[`enlace`](https://github.com/i2mint/enlace) as an optional add-on, but does **not**
depend on it.

> **Status: design phase.** The competitive landscape, the design rationale, and the
> roadmap live in [`misc/docs/`](misc/docs/). Work is tracked in GitHub
> [issues](https://github.com/i2mint/heed/issues); design decisions in GitHub
> [discussions](https://github.com/i2mint/heed/discussions).

## Why

Developer-side tools (browser extensions, Chrome DevTools MCP, Jam, …) only help
*you*. They can never serve an ordinary end user reporting a problem on your live
site, or asking for a feature. `heed` is the missing piece: a **public, zero-install**
capture-and-report widget plus a small self-hostable backend that *you* own and that
routes wherever you want.

## The shape (planned)

- **Widget** — one `<script>` tag injects a Shadow-DOM-isolated custom element; drops
  into React, plain HTML, or server-rendered pages alike. Captures a screenshot, page
  URL, and environment by default; console/network capture is *opt-in* (privacy).
- **Backend** — a mountable FastAPI router (run on its own, or mounted into `enlace`)
  that validates, stores attachments (via `dol`), and dispatches to a **pluggable
  sink**.
- **Sinks** (strategy pattern) — GitHub Issues / Discussions first; email, Slack, a
  built-in board, or a database later.
- **Agent hand-off** — an optional MCP server (`py2mcp`) so Claude Code can pull the
  feedback queue and act on reports.

## Design principles

- **Standalone first.** No hard `enlace` dependency; enlace integration is an optional
  `[enlace]` extra with lazy imports.
- **Privacy by default.** Heavy capture (console, network, session replay) is opt-in;
  PII masking is designed in, not bolted on.
- **Public endpoint = hostile input.** Rate limiting, origin checks, payload caps, and
  bot protection are first-class — the ingest endpoint is unauthenticated by nature.
- **Progressive disclosure.** One script tag works out of the box; everything is
  parametrizable via keyword-only options with smart defaults.

## Install

```bash
pip install heed                 # core (backend + models)
pip install "heed[github]"       # + GitHub sink
pip install "heed[enlace,mcp]"   # + enlace mount + MCP feedback queue
pip install "heed[redis]"        # + rate limits shared across workers
```

## Running the ingest endpoint safely

The `POST /report` endpoint is public, so it ships hardened: origin allow-list with CORS, streamed body/field/screenshot caps, strict input validation, per-IP and per-origin rate limits, a honeypot field, and an optional Turnstile/hCaptcha gate. Configure it in code or from `HEED_*` environment variables:

```python
from heed import HeedConfig, StoreSink, make_app

config = HeedConfig.from_env()   # e.g. HEED_ALLOWED_ORIGINS=https://myapp.example
app = make_app(StoreSink({}), config=config)
```

For production, set `allowed_origins`, `trusted_proxies` (behind a reverse proxy), `rate_limit_backend="redis"` with `redis_url` (several workers), and `bot_secret` to turn the bot gate on. Embedders also need a Content-Security-Policy that allows the widget: see [`misc/docs/embedding-security.md`](misc/docs/embedding-security.md).

## Embedding the widget

The backend serves the widget too, so a site adds two tags (the bundle's hashed name and its `integrity` value are in `heed/static/widget/manifest.json`):

```html
<script src="https://feedback.example/heed/widget/heed-widget-<hash>.js" integrity="sha384-…" crossorigin="anonymous" defer></script>
<heed-feedback endpoint="https://feedback.example/heed/report"></heed-feedback>
```

Options, events, theming and CSP notes: [`widget/README.md`](widget/README.md).

## Documents

- [`misc/docs/research-report.md`](misc/docs/research-report.md) — the competitive
  landscape: what to reuse / steal / avoid, design patterns, and gotchas (deep
  research with adversarial verification).
- [`misc/docs/design.md`](misc/docs/design.md) — architecture, the data model, the
  sink interface, and the spectrum of approaches with pros/cons.
- [`misc/docs/embedding-security.md`](misc/docs/embedding-security.md) — hardening knobs for operators and CSP guidance for sites that embed the widget.

---

*Author: Thor Whalen. License: Apache-2.0.*
