# heed — Design

This document is the architectural source of truth for `heed`. It captures the
shape of the system, the key decisions, and the rationale behind them. The
competitive landscape and the evidence behind these choices live in
[`research-report.md`](research-report.md). Near-term work lives in GitHub issues;
unresolved design questions live in GitHub Discussions.

---

## 1. Scope & non-goals

**In scope.** A *public, no-install* way for an ordinary visitor of a deployed web
app to **report a bug or request a feature**, gathering useful context automatically,
and delivering it to a backend the maintainer owns and routes wherever they want
(GitHub Issues first).

**Explicit non-goals.**

- Not a developer-side debugging tool. Chrome DevTools MCP, Jam, browser extensions,
  and `ov` (the agent-driven crawler) already serve *the maintainer*. `heed` serves
  *the maintainer's users*.
- Not an always-on error monitor. Passive SDK telemetry (Sentry/GlitchTip/PostHog) is
  complementary, not what this is. `heed` is **human-triggered**.
- Not a full product-analytics or support-chat suite.

## 2. Where heed sits (the spectrum)

Feedback tooling spans a spectrum; `heed` deliberately spans the **middle two bands**
with a privacy-first default and optional depth:

| Band | Example | Pros | Cons | heed? |
|---|---|---|---|---|
| Lightweight comment box | Feedback Fish | trivial, private | no context, no triage | the zero-config default |
| **GitHub-issue visual widget** | **BugDrop** | screenshot+context → real issues, self-host | DOM-screenshot limits | **core v1** |
| Full technical capture + replay | Sentry User Feedback, Bird Eats Bug | rich repro | heavy, PII/consent risk | **opt-in extension** |
| Feature-request board (vote/roadmap) | Fider, Canny | community signal, roadmap | heavier product surface | **v2 (unified inbox)** |

The unifying move: **one widget, one typed `Report`, many sinks.** A report is a bug
*or* a feature request *or* a question; the sink decides what becomes of it.

## 3. Architecture overview

Two clean halves, loosely coupled by a typed report:

```
WIDGET (frontend, framework-agnostic)            BACKEND (Python, mountable)
 one <script> tag → Shadow-DOM custom        →   FastAPI APIRouter:
 element; <dialog> modal; capture                 validate → harden → (AI triage)
 screenshot/url/env by default;                   → store attachments (dol)
 console/network OPT-IN; POST a Report            → dispatch to a pluggable SINK
                                                  → record external ref
                                       ↘ optional: expose the queue as MCP tools (py2mcp)
                                       ↘ optional: mount into enlace; bind identity via enlace_auth
```

The `Report` (Pydantic v2, SSOT in `base.py`) is the **only** contract between the
halves. Everything else is replaceable.

## 4. Data model (SSOT)

Minimal v1 shape (sketch — final form lands in `heed/base.py`):

```python
class Report(BaseModel):
    id: str                          # opaque server-assigned
    created_at: datetime
    category: Literal["bug", "feature", "question", "other"]
    title: str
    body: str
    page_url: str
    env: Environment                 # browser, os, viewport, locale, dpr
    screenshot_ref: str | None = None   # key into the attachment store (dol)
    console: list[LogEntry] | None = None   # OPT-IN only
    network: list[NetEntry] | None = None   # OPT-IN only
    identity: Identity               # anonymous by default
    origin: str                      # validated request Origin
    status: Status = Status.received

class Identity(BaseModel):
    anon_id: str                     # opaque random; the DEFAULT
    user: str | None = None          # set only when an SSO/enlace_auth token is present
    # NOTE: client IP is used transiently for rate-limiting ONLY; it is PII and is
    # never persisted as an identifier.
```

**Status lifecycle** (one enum, drives a board/roadmap/changelog if those are
surfaced later):

```
received → triaged → open (planned → started → completed)
                   ↘ declined
                   ↘ duplicate(of=<original_id>)   # votes/refs merge into the original
```

## 5. The Sink interface (strategy pattern)

A sink is *where a report goes*. Adapters register; the dispatcher never branches on
type (open/closed principle).

```python
class Sink(Protocol):
    def submit(self, report: Report) -> SinkResult: ...
    def find_duplicates(self, report: Report) -> list[DupCandidate]: ...   # optional
    def acknowledge(self, report: Report) -> None: ...                     # optional
```

- **`GitHubIssuesSink`** — the v1 default (see §9).
- Later: `GitHubDiscussionsSink`, `BoardSink` (dol-backed built-in board), `EmailSink`,
  `SlackSink`, `StoreSink` (just persist).
- The **same core functions** back both the HTTP router and the MCP tools (§10) — one
  implementation, two faces (SSOT).

## 6. Widget architecture

**Decision: a tiny async loader → a versioned bundle that mounts a Web Component with
an _open_ Shadow DOM and a native `<dialog>` modal**; plus an optional thin React
wrapper over the same custom element. Rationale:

- **Web Component + Shadow DOM** gives style/DOM isolation so the widget looks the same
  dropped into React, plain HTML, or a server-rendered page — the framework-agnostic
  requirement. Use an **open** shadow root (closed buys nothing here and complicates
  testing). *Shadow DOM is not a security boundary* — it's for isolation, not trust.
- **Native `<dialog>`** for the modal: focus-trapping and accessibility come for free,
  and — critically — many React modal/focus-trap libs (Radix, focus-trap) **break
  inside Shadow DOM**. `<dialog>` sidesteps that.
- **Tiny async loader** (a stub that buffers a command queue, à la Sentry/Intercom):
  one `<script async>` tag, non-blocking, with the heavy bundle (and opt-in capture
  libs) lazy-loaded only when the user opens the widget.
- **Declarative + imperative**: a `data-*`-configured auto-mount for the no-code case,
  and an `init({...})` for programmatic control. Keyword options, smart defaults.
- **Screenshots** via a lazy-loaded `modern-screenshot`/`html-to-image`-class
  rasterizer — understanding these are DOM re-renders, not true pixels (see §7).
- **React wrapper** is a thin convenience over the custom element, not a second
  implementation.

## 7. Capture & privacy

**Default to the minimum; richness is opt-in and consented.**

- **Default capture**: screenshot (opt-in toggle per deployment), page URL, environment
  (browser/OS/viewport/locale). That alone removes most of the manual copying.
- **Opt-in capture**: console logs, network entries, session replay. These are *off by
  default*. If enabled, **PII masking is mandatory and on by default** — mask all text
  / inputs / media (Sentry Replay's `maskAll*` defaults are the reference; rrweb's
  permissive defaults must be inverted).
- **Consent**: capturing a *stranger's* screen/replay is regulated. Under ePrivacy
  (Art. 5(3)) capturing session/replay data from the user's device generally needs
  **consent** — "legitimate interest" only covers downstream processing, not the
  capture itself. So heavy capture must be gated behind an explicit consent affordance,
  and the default-minimal mode must be usable with no consent prompt.
- **Redact before capture**, not after: never let unmasked PII reach the wire.

## 8. Public-endpoint hardening

The ingest endpoint is **unauthenticated and hostile by nature** — anyone can POST.
This is the dimension dev-side tools never face, and it shapes the backend:

- **Bot/spam gate**: Cloudflare Turnstile (or hCaptcha) token verified server-side.
- **Rate limiting**: per-IP and per-origin. `slowapi` is the obvious choice **but its
  default in-memory store is per-worker and fails under multiple workers** — back it
  with Redis.
- **Payload caps**: hard limits on body size, screenshot bytes, and field lengths.
- **CORS + CSP**: an explicit **origin allowlist** (CORS) *and* documented CSP guidance
  for embedders (`script-src`, `connect-src`, `frame-ancestors`). Both are required.
- **Never trust widget-supplied routing**: the target repo/board is resolved
  **server-side** from the validated origin/site key — the browser never names the repo.
- **Moderation queue**: reports land in a holding state; spam/abuse can be filtered
  before they reach the sink.
- **No frontend secret can authenticate the endpoint** — design assumes the site key is
  public and treats every submission as untrusted UGC.

## 9. GitHub sink specifics

- **No GitHub token in the browser.** The backend brokers issue creation via a **GitHub
  App**, minting short-lived per-installation tokens, with the **target repos
  allowlisted server-side**.
- **There is no GitHub API to attach an image to an issue** (a hard platform limit).
  Workarounds, in order of preference: store the screenshot in the `dol` attachment
  store and embed its public URL; commit it to a dedicated branch and embed the raw
  URL; or upload as a Release asset. **`base64` data-URIs do not render** in GitHub
  markdown — never rely on them.
- Map `category` → labels; embed env + (opt-in) console/network as collapsible
  markdown sections.

## 10. AI triage, dedup, and the MCP feedback queue

- **Triage** = a single structured-JSON LLM call: classify bug-vs-feature, propose
  labels/severity, and summarize free text into a clean issue body. Constrain with a
  whitelist + safe defaults, and mark the user text with **injection-boundary markers**
  (it is untrusted input).
- **Dedup** = two stages: cheap retrieval (title/body similarity or embeddings) →
  LLM/embedding confirmation. Surface `duplicate(of=…)` rather than auto-merging
  silently.
- **MCP** = expose the feedback queue (`list / get / triage / dedup / create / close`)
  as MCP tools via **`py2mcp`**, sharing the *same* core functions as the HTTP router
  (SSOT), so Claude Code can pull and act on the queue.

## 11. enlace integration (optional, never required)

- `heed` ships a plain FastAPI **`APIRouter`**. Standalone, you run it directly. With
  enlace, you **`include_router(...)`** it (not `.mount()` — mounting a sub-app drops
  it from the parent's OpenAPI/route table).
- Auth is injected via `Depends`, so when **`enlace_auth`** is present the report's
  `identity.user` binds to the logged-in user; absent it, reports are anonymous.
- The `enlace` dependency is an **optional extra** (`heed[enlace]`) imported lazily —
  the core has zero enlace import at module load.

## 12. OSS reuse posture (don't reinvent — but stay license-clean)

No existing OSS tool fits off the shelf (the closest backends are the wrong runtime for
a Python/i2mint shop). The posture per project:

- **BugDrop** (MIT) — **STUDY & REBUILD**: closest prior art; vendor its MIT
  masking/screenshot JS, port the architecture to FastAPI/`dol`.
- **FasterFixes `@fasterfixes/core` + `/mcp`** (MIT) — **FORK** the MIT widget/MCP
  packages; copy the `list_feedbacks`/`update_feedback_status` MCP surface. (Its
  dashboard/server is AGPL — avoid.)
- **shogomuranushi/feedback-widget** — **STUDY**: best template for AI conversational
  intake → summarized issue.
- **Fider** (AGPL) — **STUDY** the data model, six statuses, and duplicate-merge
  contract as *facts*, clean-room the implementation.
- **GlitchTip** (MIT) — **COMPOSE-WITH**: Python/Django Sentry-compatible automatic
  error capture; a complement for the passive-monitoring story, not the widget.

**License rule**: steal only from MIT/permissive sources; reconstruct anything copyleft
clean-room from public data models/APIs.

## 13. Phasing

- **Phase 0 (now)** — design phase: this doc, the research report, repo, plan.
- **Phase 1** — the typed `Report` model; the `Sink` protocol + `GitHubIssuesSink`; the
  mountable ingest router; public-endpoint hardening; the minimal widget (screenshot +
  url + env → issue). Default-minimal capture only.
- **Phase 2** — opt-in console/network capture with masking + consent; the unified
  inbox (bug *and* feature request); AI triage + dedup; the `py2mcp` MCP queue.
- **Phase 3** — feature-request board surface (votes/roadmap/changelog); more sinks;
  enlace_auth identity binding polish.

## 14. Open questions (→ Discussions)

- Widget distribution: self-built CDN bundle vs npm package vs both? Versioning policy
  for the embed.
- Identity: how much to lean on `enlace_auth` vs a heed-native anonymous id; abuse vs
  friction trade-off.
- Board surface (Phase 3): build native, or route feature-requests to GitHub
  Discussions and treat that as the board?
- Default sink when no GitHub App is configured (store-only? email?).

---

*See [`research-report.md`](research-report.md) for the evidence and the full tool
landscape behind every decision above.*
