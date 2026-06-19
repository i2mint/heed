# CLAUDE.md — orientation for agents working in `heed`

`heed` is an **embeddable, no-install, framework-agnostic end-user feedback**
capability: a drop-in widget that lets *any visitor* of a deployed web app report a
bug or request a feature — with context gathered automatically — routed to a
pluggable backend. The authoritative landscape and rationale are in `misc/docs/`
(read `research-report.md` and `design.md` before non-trivial work).

## The one idea to hold onto

**Two clean halves, loosely coupled by a typed report:**

```
WIDGET (frontend, framework-agnostic)          BACKEND (Python, mountable)
 one <script> tag -> Shadow-DOM custom     ->   FastAPI router: validate, store
 element; capture screenshot/url/env;           attachments (dol), dispatch to a
 console/network OPT-IN; POST a Report          pluggable SINK (GitHub first)
```

The `Report` (Pydantic, in `base.py` once built) is the single source of truth
contract between the two halves.

## Design rules (do not break)

- **Standalone first.** `heed` MUST NOT hard-depend on `enlace`. Enlace integration
  lives in the optional `[enlace]` extra and imports enlace lazily. The core is a
  plain FastAPI router that mounts anywhere.
- **Pluggable sinks = strategy pattern.** A `Sink` protocol with registered adapters
  (`github`, later `board`/`email`/`slack`/`store`). Never branch on sink type in a
  dispatcher; register adapters.
- **Privacy by default.** Screenshot + URL + env are the default capture. Console,
  network, and session replay are **opt-in** and must mask PII. Design masking in.
- **The ingest endpoint is unauthenticated and hostile.** Rate limiting, origin
  allow-listing, payload size caps, and bot protection (Turnstile/hCaptcha) are
  first-class, not afterthoughts.
- **No secrets in the browser.** The widget never holds a GitHub token; the backend
  brokers issue creation via a GitHub App (or a server-side token).
- **Progressive disclosure.** One script tag works out of the box; everything is
  parametrizable via keyword-only options with smart defaults.

## Conventions

- Apache-2.0; every module has a top-level docstring (ruff `D100` is enabled).
- Pydantic v2 SSOT in `base.py`; small functional helpers; module-private helpers get
  a `_` prefix; cross-module reusable helpers do not.
- Work is tracked in GitHub **issues**; durable design rationale in **Discussions**;
  reference material + research in `misc/docs/` (keep a one-line index, read on demand).

## Status

Design phase. No backend code yet — the plan lives in the GitHub issues and the
design docs. Start there.
