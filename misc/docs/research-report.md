# `heed` Research Report: Embeddable End-User Feedback Widgets, Boards, and GitHub-Routed Backends

> **Scope.** `heed` aims to be a public, no-install, framework-agnostic **end-user feedback / bug-report / feature-request** widget that ships its **own pluggable Python backend**, routes by default to **GitHub Issues**, runs **standalone**, and mounts as an **optional add-on to the `enlace` FastAPI platform**. The reporters are ordinary strangers, not developers; the ingest endpoint is public and unauthenticated. This report surveys the existing landscape across six research lanes — end-user widgets, feedback boards, widget architecture, privacy/abuse, backend/routing/AI, and OSS prior art — and distills what to **steal**, what to **avoid**, the design patterns to adopt, the hard limits to design around, and a ranked "don't reinvent the wheel" shortlist. Where the research and its adversarial verification disagree, this document follows the **verification**.

---

## Executive summary

1. **BugDrop is the single closest architectural template** to the need: MIT-licensed, self-hostable, one script tag in Shadow DOM, anonymous zero-install submission, client-side screenshots, and a **server-side GitHub App broker** that creates issues — with screenshots committed to a dedicated `bugdrop-screenshots` git branch to sidestep GitHub's missing attachment API [1]. Port the architecture (Cloudflare Worker → FastAPI/`dol`), not the TypeScript code.

2. **The right embed shape is a tiny async loader script + a Web Component (custom element) using OPEN Shadow DOM**, with a thin optional React wrapper over the same core. Custom elements are a browser primitive, so one embed drops into React, plain HTML, and SSR identically [11][12][14]. An iframe gives stronger isolation but cannot screenshot the host page and adds postMessage/sizing friction.

3. **Never put a GitHub write token (PAT or App key) in the browser.** Broker every write through a backend holding a GitHub App private key, minting short-lived installation tokens per request, and **enforce a server-side repo allowlist** so the public endpoint can't be weaponized to file issues into arbitrary repos [20][21].

4. **There is no public GitHub API to upload issue/comment attachments** — a hard limit, confirmed (the web UI uses an undocumented signed-S3 flow) [22]. Durable workarounds: commit the image to a dedicated branch (BugDrop), use a Release asset, or host on your own `dol`/blob store and embed the URL in the issue markdown. Do **not** depend on CLI tools (gh-image/gitshot) that replicate the browser flow.

5. **Client-side "screenshots" are DOM rasterization** (`html2canvas`/`modern-screenshot`/`html-to-image`), not true pixel captures. They silently break on cross-origin images (canvas tainting), `<canvas>`/WebGL, iframes, and some modern CSS — and they bake in whatever PII is on screen. Treat them as approximate, lazy-load on opt-in, and **redact sensitive nodes before serialization** [15][30].

6. **A public, unauthenticated ingest endpoint is an abuse surface dev-side error SDKs never face.** Layer defenses: Cloudflare Turnstile (server-validated token), per-IP rate limiting via `slowapi` (Redis-backed when multi-worker), hard payload/content-length caps, strict CORS allowlist + server-side Origin check, and a moderation queue [26][27][33]. **No frontend secret can authenticate the widget** — browser code is public.

7. **Default to minimal capture; gate richer capture behind consent.** Tier 1 = text + non-PII metadata; Tier 2 = redacted screenshot; Tier 3 = masked DOM replay (explicit opt-in). Sentry's Replay SDK is the gold-standard masking reference (`maskAllText`, `maskAllInputs`, `blockAllMedia` all default **true**); raw `rrweb` is permissive by default and must have its defaults inverted if used [25][28][29].

8. **The pluggable "sink" (strategy pattern) is the correct backend shape.** One `Sink` protocol with adapters — GitHub Issues (default), GitHub Discussions/Projects, email, Slack/Discord webhook, built-in DB board — dependency-injected from config. `wafir`'s YAML `targets` and `bromb`'s forkable endpoint are real-world expressions of this [23][35].

9. **Ship the backend as a FastAPI `APIRouter` mounted via `include_router`, not Starlette `.mount()`** — the latter drops routes from FastAPI routing and OpenAPI. The same call mounts it standalone and into `enlace`; inject auth/config via `Depends` so `enlace_auth` layers on with **no hard dependency** [24].

10. **AI triage is a single structured-JSON LLM call** (category / severity / summary / suggested labels) validated against a whitelist with safe-default fallback, plus **two-stage dedup** (cheap keyword/kNN retrieval → LLM/embedding confirm on a shortlist). Wrap untrusted free text in boundary markers against prompt injection. Expose the queue as **MCP tools via `py2mcp`** so Claude Code can pull, triage, and resolve feedback [36][37].

11. **No OSS tool fits off the shelf.** Every closest-fit OSS backend is the wrong runtime for a Python owner (Cloudflare Worker, Next.js/Node, Go, Ruby, Elixir). The play: **STUDY** BugDrop's broker flow and FasterFixes' MCP loop, then **REBUILD** a thin FastAPI + `dol` + `py2mcp` backend with a vanilla-JS Shadow-DOM widget [1][41].

12. **Copyleft is a real constraint.** The good OSS boards (Fider, Astuto, LogChimp, Formbricks) are AGPL/GPL; `wafir` and the FasterFixes server are AGPL. MIT-safe steal targets are BugDrop, shogomuranushi/feedback-widget, and FasterFixes' `@fasterfixes/core`+`/mcp` packages. Facts and API contracts aren't copyrightable — **write clean-room code informed by their designs rather than forking**.

---

## The spectrum of approaches

Feedback tooling forms a spectrum from "tiny comment box" to "full session replay" to "feature-request board." Placing the options helps locate where `heed` should sit (it spans bands B + E, with C/D as opt-in tiers).

| Band | What it is | Representative tools | Captures | Pros for this need | Cons for this need |
|---|---|---|---|---|---|
| **A — Lightweight comment box** | Wrap a button → small form (category + message + optional email) | Feedback Fish [8], Doorbell [7] | Text, category, minimal metadata | Dead-simple DX; lowest privacy/abuse risk; "simple things simple" default tier | No technical context; Feedback Fish backend is proprietary (not self-host) |
| **B — GitHub-issue visual widget** ⭐ | Click → auto screenshot → annotate → routes to a GitHub Issue via a backend broker | **BugDrop** [1], wafir [23], shogomuranushi/feedback-widget [40] | Screenshot, annotations, URL, browser/OS/viewport, optional console | **The sweet spot.** MIT, self-hostable, anonymous, GitHub-native, one script tag | Young/low-star projects; wrong runtime (TS/CF Worker/Node) — port architecture |
| **C — Full commercial visual feedback** | Rich auto-capture + annotation + two-way PM sync | Marker.io [3], Usersnap [4], Userback [5], Gleap [32] | + console/network logs, advanced metadata | Best UX & auto-capture **checklist** to steal | Cloud-only, closed, no self-host |
| **D — Technical capture + session replay** | Dashcam-style recorder; replay clicks/console/network | Bird Eats Bug [6], Sentry User Feedback [2], rrweb [29] | + DOM replay, network bodies | Best repro fidelity; **buffer-on-report** pattern | Heavy PII/GDPR liability for strangers; make strictly opt-in |
| **E — Feature-request board** | Public board: submit, upvote, comment, roadmap, changelog | Fider [9], Astuto [10], LogChimp [16], Canny [17], Featurebase [18], Frill [19] | Title/description, votes, comments, status | The "request a feature" half; clean status/dedup data model | OSS ones are full-site apps with **no embeddable widget**; AGPL/GPL |

**Recommended placement for `heed`:** default to **Band B** (GitHub-issue visual widget, minimal-capture) with **Band A** as the simplest tier and **Band C/D** capture (console/network/replay) as **opt-in** tiers gated by consent. Offer **Band E** semantics (status lifecycle, voting, dedup-merge) as a logical layer over GitHub Issues/Discussions rather than a separate DB-backed board, unless a public roadmap becomes a goal.

---

## Lane-by-lane findings

### Lane 1 — End-user widgets

The lane spans the full A→E spectrum. **BugDrop** is the prime template [1]: thin Shadow-DOM script tag + stateless serverless proxy holding the GitHub App credential, screenshots committed to a git branch, anonymous submission, declarative PII masking (`data-bugdrop-mask` + auto-mask of password/credit-card inputs), per-IP (10/15 min) and per-repo (50/hr) rate limits, and category→label mapping. **Steal** the whole pattern; **avoid** its synchronous (non-`async`/`defer`) script tag and treat all input as untrusted UGC.

| Tool | Steal | Avoid | License | Self-host | Captures | Routes to |
|---|---|---|---|---|---|---|
| BugDrop [1] | Broker arch, branch-screenshot trick, masking, rate limits | Sync script tag, CF Worker runtime | MIT ✓ | Yes | Screenshot, annotations, URL, sys info, opt. console | GitHub Issues |
| Sentry User Feedback [2] | Consent/replay-buffer model, Shadow DOM + `<dialog>`, standalone mode | Heavy SDK coupling; routes only to Sentry | **MIT** (SDK) — *not FSL, see corrections* | Self-host = heavy | Screenshot, replay (30s buffer), name/email | Sentry inbox |
| Marker.io [3] | "Anyone submits without an account"; one-snippet install | Cloud-only, $39+/mo | Proprietary | No | Auto screenshot, annotations, tech metadata | GitHub/GitLab/Jira/Linear (2-way) |
| Usersnap [4] | The **auto-capture checklist** (screen size/browser/OS/UA/URL always; console XHR/Fetch + JS errors) | Cloud-only | Proprietary | No | + console/network logs | Jira/GitHub/Slack/… |
| Userback [5] | "Full capture" opt-in tier (screenshot+video+replay+console) | Cloud-only, heavy | Proprietary | Unknown | + video, replay | Jira/Trello/… |
| Bird Eats Bug [6] | **Dashcam** buffer-then-persist pattern | Replay of strangers = PII liability | Proprietary | Unknown | Replay, clicks, console, network | Jira/Linear/GitHub/… |
| Doorbell [7] | **Webhook-first** routing seam | Dated UX | Proprietary | Unknown | Text, basic metadata | GitHub/Slack + 25 webhooks |
| Feedback Fish [8] | Dead-simple `projectId`+optional `userId` DX | Backend proprietary (not self-host) | MIT (wrappers) | No | Text, category, email | FF dashboard |
| Frill [19] | Four render modes (popover/modal/sidebar/inline); identity passthrough | Cloud-only | Proprietary | No | Text, upvotes | Frill board |
| Hellonext/FeatureOS [*] | (see corrections — **supports anonymous**) | Cloud-only | Proprietary | No | Text, votes | Own board |
| Userflow [*] | Event/attribute-based survey triggering | No screenshot/tech capture | Proprietary | No | Survey responses | Userflow |

### Lane 2 — Feedback boards

The OSS self-hostable board spectrum is narrow and converges on a near-identical data model. **Fider** [9] is the strongest reference: Go + React + Postgres, single binary, healthy, with a clean REST API (Posts/Users/Tags/Votes/Comments), six statuses (`open/planned/started/completed/declined/duplicate`), and a first-class **duplicate-merge contract** (set `status=duplicate` + `originalNumber` → votes merge into the original). Its weaknesses are exactly the v1 opportunity: **no embeddable widget** (issue #442 is wontfix), no native changelog/roadmap pages, and **AGPL-3.0**. **Astuto** [10] has the cleanest minimal model plus a **moderation queue** and **anonymous feedback** Fider lacks — but is **archived/unmaintained** (Feb 2026), so study only. **LogChimp** [16] demonstrates an **open-core split** (AGPL core + proprietary EE dirs) worth copying as a packaging idea but a licensing trap if forked.

Commercial leaders are idea mines, not reuse targets. **Canny** [17] is *the* embedding pattern to copy: an async script + a `<div data-canny>` placeholder + `Canny('render', {boardToken, basePath, theme, ssoToken})`, with AI dedup and the Groups→Ideas→Insights hierarchy. **Featurebase** [18] unifies board+roadmap+changelog+help behind one floating button and lets an AI agent file requests on a user's behalf.

**Key takeaway:** no OSS board ships a drop-in widget — that gap **is** `heed`'s differentiator. Reuse Fider's data model and status/dedup semantics verbatim (facts, not code), steal Canny's script-tag+SSO-token embed, and build the widget OSS lacks.

### Lane 3 — Widget architecture (how to BUILD it)

The industry has converged on a **tiny async loader script + Web Component (open Shadow DOM)** core. The loader (Intercom/Sentry pattern) synchronously registers a global stub with a **buffered command queue** (so calls before load aren't lost), then async-injects the real, versioned bundle via `insertBefore` so it never blocks first paint [13]. The bundle defines a custom element and attaches an **open** shadow root (closed mode only deters, it is not a security boundary). Use the native **`<dialog>` element** (`showModal()`) for the modal to get browser-managed focus trapping and top-layer — sidestepping the well-documented breakage of JS focus-trap libs and Radix/portal dialogs inside Shadow DOM [12]. Theme via inherited **CSS custom properties** that pierce the shadow boundary. Publish a **thin React wrapper** over the same custom element (Feedback Fish's two-tier model) so React/shadcn users get JSX without a second implementation.

| Tool | Steal | Avoid | Notes |
|---|---|---|---|
| Sentry feedback widget [2] | Open Shadow DOM + `<dialog>`; actor-button→dialog; fully replaceable UI | Full-SDK coupling | Requires Shadow DOM + Dialog support |
| Intercom snippet [13] | **Async loader stub + command queue**; delayable load | iframe-based final UI (heavy isolation reference) | Canonical loader |
| Canny SDK [17] | Declarative `data-` mount target **+** imperative `render()` API; SSO token | Full-page board is heavier than needed | Dual contract |
| Feedback Fish [8] | **Vanilla core + thin React wrapper** two-tier distribution | Closed backend | Closest to `heed`'s embed goal |
| Open-Feedback [38] | The **idea**: backend = webhook + blob store (maps to GitHub + `dol`) | The code (unmaintained since 2021, LGPL-2.1) | Reference only |
| html2canvas / modern-screenshot [15][30] | Client-side screenshot for context; capture stays client-side until submit | Cross-origin/canvas/CSS gaps; cannot run from inside a sandboxed iframe | A strong reason to choose Web Component over iframe |

### Lane 4 — Privacy & abuse

This is where an end-user widget diverges sharply from dev-side error tooling: the reporters are random strangers and **anything captured automatically is presumptively personal data** under GDPR/CCPA. The governing design rule: **default-mask-everything, capture client-side-redacted data only**, and let consent burden scale with capture richness. **Sentry Replay** is the masking gold standard (`maskAllText`/`maskAllInputs`/`blockAllMedia` default true, `maskFn = s => '*'.repeat(s.length)`) [25]; **rrweb** is permissive by default (`maskAllInputs=false`, only password inputs masked) and **must have its defaults inverted** if used [29]. On the backend, the public ingest endpoint needs: **Cloudflare Turnstile** [26] (no cookies, privacy-friendly, server-side `siteverify` mandatory) over hCaptcha [31]; **`slowapi`** IP rate limiting [27] (Redis-backed when multi-worker — in-memory silently fails across processes); hard payload caps; strict CORS allowlist + server-side Origin check; and **no frontend secret**. Identity should be **anonymous-by-default** with optional binding to an authenticated user; treat IP as PII (use transiently for rate-limiting, store an opaque random id as identity) [34].

### Lane 5 — Backend, routing, triage, AI (the sink side)

Five sub-decisions, each with a validated answer:

- **Destinations:** a **pluggable `Sink`** (strategy pattern) with GitHub Issues as the default adapter and Discussions/Projects/email/Slack/Discord/DB-board as drop-in alternatives [23][35].
- **GitHub brokering:** a backend holds a **GitHub App private key**, mints a short-lived installation token per request, creates the issue server-side, and **enforces a server-side repo allowlist** [20][21]. (Note the 2026 migration to stateless-JWT installation tokens and the temporary `X-GitHub-Stateless-S2S-Token` per-request override header — treat tokens as opaque [39].)
- **Image attachments:** no API exists → commit-to-branch, Release asset, or `dol` blob store + URL in markdown [22].
- **Dedup:** **two-stage** — cheap keyword/kNN retrieval → LLM/embedding confirm on the shortlist [37].
- **AI triage:** **single structured-JSON LLM call** + whitelist validation + safe defaults + prompt-injection boundary markers; expose the queue over **MCP via `py2mcp`** [36][37].

Steal targets: **BugDrop** [1] (broker + branch-screenshot), **wafir** [23] (declarative `targets` multi-routing, YAML-mirrors-Issue-Forms, draft-first Projects, allowlist enforcement), the **Issue AI Agent** recipe [37], and `py2mcp` [36]. All are TS or external — port architecture, not code.

### Lane 6 — OSS prior art (synthesized below in ADOPT / FORK / STUDY)

Three bands: **(1)** direct prior art to steal from — BugDrop [1], shogomuranushi/feedback-widget [40], FasterFixes [41]; **(2)** feedback portals (Fider/Astuto/LogChimp/Formbricks) — wrong category (boards, not in-page capture), copyleft, route to their own DB; **(3)** adjacent infra to compose with — **GlitchTip** [44] (Python/Django, Sentry-compatible automatic error capture — the complement to a user-initiated widget), Chatwoot [43] (live chat), Papercups [42] (maintenance-mode). Cross-cutting conclusion: every closest-fit OSS backend is the wrong runtime; **rebuild a thin FastAPI/`dol`/`py2mcp` backend** and reuse only MIT widget frontends.

---

## Design patterns to use (consolidated & prioritized)

Deduped across all six lanes, in rough priority order for v1.

| # | Pattern | When to use | Source |
|---|---|---|---|
| 1 | **Thin Shadow-DOM widget + stateless backend that holds the credential** | Always — the core architecture. No secret ever ships to the browser. | [1] |
| 2 | **Server-side GitHub App broker + per-request installation token + repo allowlist** | Whenever routing to GitHub from a public widget. | [20][21] |
| 3 | **Async loader stub + buffered command queue** | The embed entry point; guarantees no lost calls and never blocks paint. | [13] |
| 4 | **Web Component (open Shadow DOM) core + declarative `data-` mount + imperative `init()` API** | The framework-agnostic UI. Both contracts so it drops into SSR HTML and SPAs. | [12][17] |
| 5 | **Native `<dialog>` for the modal** | Always — avoids focus-trap/Radix breakage inside Shadow DOM. | [12] |
| 6 | **Thin React/npm wrapper over the vanilla core** | Optional sugar for React/shadcn users; never reimplement the widget. | [8] |
| 7 | **Pluggable `Sink` protocol + adapters (strategy + DI)** | The backend shape. GitHub default; others drop-in from config. | [23][35] |
| 8 | **Mountable `APIRouter` via `include_router`** | Standalone and `enlace` mount via one call; inject auth/config via `Depends`. | [24] |
| 9 | **Default-mask-everything; selectively unmask audited-safe nodes** | All capture. Client-side redaction beats server-side scrubbing. | [25] |
| 10 | **Tiered auto-capture gated by consent** (text → redacted screenshot → masked replay) | Dial richness per-deployment; replay strictly opt-in. | [28] |
| 11 | **Capture-on-report buffering (dashcam)** | If/when replay is offered — buffer ~30s in memory, persist only on report. | [2][6] |
| 12 | **Declarative PII masking via `data-*-mask` + auto-mask sensitive inputs; redact-before-capture** | Screenshots/replay. Redact the DOM before serialization. | [1][30] |
| 13 | **Image-as-committed-blob (or `dol` blob store) + URL in markdown** | The attachment workaround — durable, not the undocumented browser flow. | [1][22] |
| 14 | **Two-stage dedup: cheap retrieval → LLM/embedding confirm** | Submit-time dedup; merge votes onto the canonical request. | [37] |
| 15 | **Single structured-JSON LLM triage + whitelist validation + safe defaults + injection boundary markers** | AI triage of untrusted free text. | [37] |
| 16 | **Expose the queue as MCP tools via `py2mcp`** (`list/get/triage/dedup/create/close`) | Let Claude Code pull and resolve feedback; same funcs back the HTTP router (SSOT). | [36][41] |
| 17 | **Status-driven lifecycle + duplicate-merge contract** (`open→planned→started→completed`, +`declined`,`duplicate`+`originalNumber`) | If surfacing a board/roadmap; one enum drives board+roadmap+changelog. | [9] |
| 18 | **Signed SSO token for identity passthrough — optional, never required** | When mounted under `enlace_auth`; standalone falls back to anonymous/email. | [17] |
| 19 | **Moderation queue + anonymous submission** | A public no-auth widget needs an approval gate against spam. | [10] |
| 20 | **Short retention + automatic deletion as first-class config** | GDPR data-minimization; a TTL/eviction policy on the `dol` store. | [28] |
| 21 | **Pair user-initiated widget with automatic error tracking (GlitchTip)** | If automatic crash capture is wanted — complementary, don't rebuild. | [44] |

---

## Gotchas & hard limits (consolidated, each with mitigation)

| # | Gotcha | Mitigation |
|---|---|---|
| 1 | **Client screenshots are DOM rasterization, not real captures** — break on cross-origin images (canvas tainting), `<canvas>`/WebGL, iframes, some CSS, and cannot run inside a sandboxed iframe [15][30]. | Set expectations; lazy-load on opt-in; offer "describe + attach file" fallback; consider `getDisplayMedia()` for a true screenshot when a gesture/permission is acceptable. A strong reason to choose Web Component over iframe. |
| 2 | **No GitHub API to upload issue/comment attachments** (hard limit; web UI uses undocumented signed-S3) [22]. | Commit to a dedicated branch (BugDrop), use a Release asset, or `dol`/blob store + URL in markdown. Do **not** depend on gh-image/gitshot (replicate the browser flow, fragile). |
| 3 | **Screenshots/replays of strangers capture PII** (their data, others' data, tokens in URLs) — real GDPR/CCPA exposure [28]. | Mask sensitive inputs by default; support `data-*-mask` redaction; strip query strings/tokens from captured URLs; keep console/network/replay opt-in; show a clear consent line. |
| 4 | **For browser session replay, ePrivacy Art 5(3) requires consent (or strict-necessity) for the capture step** — legitimate interest covers only the downstream processing, not the recording [25]. *(Corrects the research's "legitimate-interest-as-alternative" framing.)* | Default to minimal capture; gate replay behind explicit opt-in consent; disclose clearly ("we record clicks/scrolls, not what you type"); respect DNT as an opt-out heuristic. The "15-30% opt-in" figure is a vendor estimate, not authoritative. |
| 5 | **Public, no-auth ingest = spam/abuse magnet** (junk issues, payload bombs, bot floods) [27][33]. | Turnstile (server-validated), per-IP rate limit (`slowapi`+Redis), hard content-length cap, dedupe identical payloads, moderation queue, treat all input as untrusted UGC (sanitize before rendering in issue bodies). Return 429/413 cleanly. |
| 6 | **`rrweb` is permissive by default** (`maskAllInputs=false`, only password inputs masked; hidden inputs recorded raw; known bugs #874/#1609) [29]. | Invert defaults to match Sentry; test redaction empirically; don't trust the config alone. |
| 7 | **No frontend secret can authenticate the widget** — browser code is public [33]. | Strict CORS allowlist + server-side Origin check + Turnstile; if integrity is needed, a short-lived **server-minted** per-page token, never a baked-in key. |
| 8 | **`slowapi` in-memory store doesn't share state across workers**; IP keys are weak behind NAT/proxies [27]. | Use a Redis backend on the multi-app `enlace` server; ensure Caddy sets a trustworthy `X-Forwarded-For`; combine with Turnstile (never IP alone). |
| 9 | **IP address is itself PII** and is the fallback anonymous identifier [34]. | Use IP transiently for rate-limiting; store an opaque random anonymous id; persist real identity only when an authenticated user is supplied. |
| 10 | **Shadow DOM is NOT a security boundary** — open roots are reachable from host JS; closed only deters [14]. | Validate/sanitize all widget input server-side; use open mode for debuggability; use a sandboxed iframe only if true isolation is mandatory. |
| 11 | **Focus traps & portal dialogs (Radix/shadcn) break inside Shadow DOM** [12]. | Use the native `<dialog>` with `showModal()`; don't drop a Radix/shadcn dialog into a shadow root unmodified. |
| 12 | **The host page's CSP governs your widget; CORS and CSP are both required** — `connect-src` must allow your backend AND the backend must send CORS headers; one without the other fails [11]. | Document required CSP additions (`script-src`, `connect-src`, `img-src`); avoid inline scripts/styles (nonces/external files); send correct CORS headers server-side. |
| 13 | **Never trust widget-supplied target repo IDs** — lets attackers redirect issues to any repo the App can write to [21]. | Server-side allowlist derived from the App installation's permitted repos; reject anything else. |
| 14 | **`Starlette .mount()` under an APIRouter drops routes from FastAPI routing/OpenAPI** [24]. | Use `app.include_router(router, prefix=...)`; nest with `include_router`. |
| 15 | **End-user free text flows into the LLM triage prompt → prompt injection** [37]. | Strip zero-width/control chars; wrap user text in boundary markers; validate LLM output against a whitelist with safe defaults; keep secrets out of model context. |
| 16 | **Synchronous/non-`async` script tag (BugDrop) can block render** [1]. | Ship the loader as `async`/`defer` or lazy-load on first interaction so the widget never costs first paint. |
| 17 | **Versioning the embed:** hardcoded versioned URLs never get fixes; "latest" can break every site at once [13]. | Serve a near-immutable loader at a stable URL that fetches a versioned, cacheable core bundle pinned by a server-controlled manifest. |
| 18 | **GitHub is migrating to stateless-JWT installation tokens during 2026** — token format may shift mid-rollout [39]. | Treat installation tokens as opaque; use the temporary `X-GitHub-Stateless-S2S-Token` override only if you must control format during transition. |
| 19 | **Cross-origin embedding hits CORS + third-party-cookie/iframe-storage restrictions** (browsers increasingly block 3rd-party cookies) [17]. | Avoid cookie-based widget auth; use postMessage + signed-token + per-origin CORS allowlist; render in the host DOM (Web Component) to dodge storage partitioning. |
| 20 | **Copyleft on reused tools constrains shipping** (AGPL/GPL boards; wafir & FasterFixes-server are AGPL; PyGithub is LGPL-3.0) [35]. | Don't fork copyleft into a permissive library; verify each LICENSE on the repo; build clean-room from public data models/APIs (not copyrightable). |

---

## OSS prior art: ADOPT / FORK / STUDY

The "don't reinvent the wheel" conclusion. **No project is a clean ADOPT** for the full need — every closest-fit OSS backend is the wrong runtime for a Python owner, so the honest verdict skews to STUDY-AND-REBUILD with selective MIT vendoring.

| Project | Verdict | Why | License |
|---|---|---|---|
| **BugDrop** (mean-weasel) [1] | **STUDY & REBUILD** (vendor masking/screenshot JS) | Closest prior art: GitHub-App broker, Shadow-DOM widget, branch-screenshot trick, sensitive-field masking, rate limits. But Cloudflare Worker backend — port to FastAPI/`dol`. Solo-dev, ~33 stars (bus-factor 1). | MIT ✓ |
| **FasterFixes** (`@fasterfixes/core`+`/mcp`) [41] | **FORK the MIT packages; STUDY the rest** | The **MCP design to copy**: `list_feedbacks` / `update_feedback_status` tool surface, org/project/page model, DOM-selector + component-tree capture. MIT widget core is vendorable. | Server **AGPLv3**; widget+MCP packages **MIT** |
| **shogomuranushi/feedback-widget** [40] | **STUDY & REBUILD** | Best template for **AI conversational intake → summarize to issue** (swap Gemini for Claude/`py2mcp`); domain→API-key multi-app gating; GitHub-App-vs-PAT dual auth. Node backend; new/small. | MIT ✓ |
| **Fider** [9] | **STUDY** (reuse data model & dedup semantics, not code) | The board data model, six statuses, and duplicate-merge contract to copy verbatim as facts. No widget; **AGPL-3.0**; Go runtime. | AGPL-3.0 |
| **Astuto** [10] | **STUDY ONLY** | Cleanest minimal model + moderation queue + anonymous feedback — but **archived/unmaintained (Feb 2026)**. Design reference, never a dependency. | AGPL-3.0 |
| **LogChimp** [16] | **STUDY** (copy the open-core packaging idea) | Open-core split (AGPL core + proprietary EE dirs) is a packaging model worth mirroring; JWT-token SSO. Pre-1.0, NOASSERTION license — don't fork. | AGPL core + proprietary EE |
| **Bromb** [35] | **STUDY** | The **widget-repo-separated-from-forkable-endpoint** model maps to "standalone widget points at a swappable backend." GPL; Svelte; not GitHub-native. | GPL-3.0 |
| **Open-Feedback** [38] | **STUDY** (the idea only) | "Backend = webhook + blob store" maps to GitHub + `dol`. Unmaintained since 2021, ~32 stars, LGPL-2.1. | LGPL-2.1 |
| **GlitchTip** [44] | **COMPOSE-WITH** | The only closest-category tool natively in **Python/Django**; Sentry-API-compatible **automatic** error capture — the complement to a user-initiated widget. Not an intake widget itself. | **MIT** (*not BSD — see corrections*) |
| **Formbricks** [45] | **STUDY** (targeting/trigger model only) | In-app survey targeting by event/attribute. AGPL + Next.js + survey-centric — wrong shape for bug→issue. | AGPL-3.0 |
| **Chatwoot** [43] | **COMPOSE-WITH** (only if chat is a goal) | Embeddable widget loader + contact/conversation model. Ruby; huge scope. | MIT |
| **Papercups** [42] | **SKIP** | Live chat, **maintenance-mode** (not archived; still forkable MIT) but not bug→GitHub; shogomuranushi is the better active template. | MIT |
| **Feedback Fish** (widget) [8] | **STUDY** (DX target) | The `<FeedbackFish projectId>` simplicity is the progressive-disclosure API target. Backend closed — fails the "own backend" requirement. | MIT (widget) |

**Bottom line:** STUDY-and-REBUILD a thin FastAPI + `dol` + `py2mcp` backend with a vanilla-JS Shadow-DOM Web Component widget; selectively vendor MIT JS from BugDrop (masking/screenshot) and `@fasterfixes/core`/`/mcp`; copy Fider's data model and Canny's embed contract as facts.

---

## Verification corrections

The adversarial verification pass **refuted** or **qualified** several research claims. These corrections are integrated throughout the body above; collected here so the rest can be trusted.

| Claim (as originally researched) | Verdict | Correction |
|---|---|---|
| **Sentry's User Feedback widget is FSL-licensed** (forbids competing SaaS) | **partially-true** | The technical sub-claims hold, but the **license is wrong**: the widget ships in the **MIT-licensed** Sentry JavaScript SDK. FSL covers the Sentry **server** web app, not the SDK. Treat the SDK/widget as MIT [2]. |
| **Hellonext requires login to contribute, costs $65/mo, has no free trial — an anti-pattern for anonymous feedback** | **refuted** | Hellonext is now **FeatureOS** and **explicitly supports anonymous submissions, comments, and votes without sign-in**. Pricing is **$60/$120/$250/mo** (not $65), and **all plans include a 30-day free trial**. It is **not** an anti-pattern on the anonymity dimension. The cited source 404s. [*] |
| **Canny's widget script is `sdk.io/sdk.js`** | **partially-true** | The script is loaded from **`https://sdk.canny.io/sdk.js`** (host `sdk.canny.io`). Every other detail (async load, `<div data-canny>`, `Canny('render', {boardToken, basePath, theme, ssoToken})`, Autopilot AI dedup, Groups→Ideas→Insights) is accurate [17]. |
| **Frill's embeddable ideas widget costs ~$10k/yr; good embeddable widgets are commercial-only** | **partially-true** | The OSS-board widget gap (Fider/Astuto/LogChimp are full-site apps) is **real**. But **Frill INCLUDES an embeddable widget on all plans from ~$25/mo** — the **~$10k/yr** figure comes from a Frill customer testimonial describing a **costlier competitor (implied Canny)** she rejected, **not Frill's price** [19]. |
| **Turnstile is free up to 1M siteverify requests/month, sets no cookies** | **partially-true** | Managed mode is **free for unlimited use**; advanced features are free below a **1M siteverify request limit** (the GA blog does **not** say "per month"); current pricing frames Free as **"up to 20 widgets/account."** No cross-site tracking, and **no cookies by default** — but it **does set the `cf_clearance` cookie in pre-clearance mode** [26]. |
| **Capturing inputs/content/URLs requires consent OR a documented legitimate-interest assessment; opt-in is 15-30%** | **partially-true** | "Personal data" is confirmed. But for **browser session replay, ePrivacy Art 5(3) requires consent (or strict-necessity)** for the **capture step**; legitimate interest can justify only the **downstream processing**, not the recording (per EDPB Guidelines 2/2023). The **15-30% opt-in** figure is the source's own estimate, not authoritative (reported rates range ≈8% to 50%+) [25]. |
| **wafir is MIT-licensed** | **partially-true** | wafir is **AGPL-3.0**, not MIT (the README's "MIT" applies only to a bundled `normalize.css`). The **architecture** claims (Fastify, GitHub App installation IDs via Octokit, S3 storage, YAML `targets`) all hold [23]. |
| **GlitchTip is BSD-licensed** | **partially-true** | GlitchTip is **MIT-licensed**, not BSD. All other facts hold (Sentry-API-compatible, Django/Python, GlitchTip 6 released 2026-02-03, ~512 MB RAM) [44]. |
| **Fider's license is reported inconsistently (AGPL vs MIT) — verify before forking** | **partially-true** | Fider is **unambiguously AGPL-3.0** (LICENSE file + GitHub badge); there is **no credible MIT report** — the "inconsistency" is unsubstantiated. The real consideration is AGPL's **network/Affero copyleft** clause before forking [9]. |
| **Astuto and Papercups are both archived/maintenance-mode → neither is a viable fork base** | **partially-true** | **Astuto is archived** (read-only since Feb 8, 2026) — correct. **Papercups is NOT archived**: it is an active, non-read-only, MIT repo in **maintenance mode** (accepts PRs, does major bug fixes) — still forkable, subject to a no-new-features caveat [10][42]. |

*Confirmed-as-stated claims* (no correction needed): BugDrop's full architecture & MIT license [1]; Usersnap's cloud-only + always-on capture + console recorder [4]; Marker.io's one-snippet install + anonymous submission + 2-way sync + $39/mo + no self-host [3]; the entire widget-architecture lane (Sentry Shadow DOM + `<dialog>`, Intercom loader queue + iframe, Shadow-DOM-not-a-security-boundary, CSP+CORS dual requirement, Radix-in-Shadow-DOM breakage) [2][13][14][11][12]; Sentry Replay & rrweb masking defaults [25][29]; `slowapi` multi-process limitation [27]; the no-GitHub-attachment-API limit [22]; `include_router` vs `.mount()` [24]; the stateless-token override header [39]; FasterFixes' license split + MCP tools [41].

---

## REFERENCES

1. [BugDrop (mean-weasel/bugdrop)](https://github.com/mean-weasel/bugdrop)
2. [Sentry User Feedback widget docs](https://docs.sentry.io/platforms/javascript/user-feedback/)
3. [Marker.io website feedback widget](https://marker.io/features/website-feedback-widget) · [pricing](https://marker.io/pricing)
4. [Usersnap console recorder feature](https://help.usersnap.com/docs/console-recorder-feature) · [FAQ](https://help.usersnap.com/docs/usersnap-faq)
5. [Userback (Marker.io alternative comparison)](https://userback.io/comparison/marker-io-alternative/)
6. [Bird Eats Bug](https://birdeatsbug.com/)
7. [Doorbell.io](https://doorbell.io/)
8. [Feedback Fish (React wrapper)](https://github.com/feedback-fish/feedback-fish-react)
9. [Fider (getfider/fider)](https://github.com/getfider/fider) · [Posts API](https://docs.fider.io/api/posts/) · [embed issue #442](https://github.com/getfider/fider/issues/442)
10. [Astuto (astuto/astuto)](https://github.com/astuto/astuto)
11. [CSP `connect-src` (CORS + CSP dual requirement)](https://content-security-policy.com/connect-src/)
12. [Radix UI dialog focus-trap in Shadow DOM (issue #3353)](https://github.com/radix-ui/primitives/issues/3353)
13. [Intercom web installation (loader stub + queue)](https://developers.intercom.com/installing-intercom/web/installation)
14. [Shadow DOM is not a security boundary (MDN)](https://developer.mozilla.org/en-US/docs/Web/API/Web_components/Using_shadow_DOM) · [Imperva](https://www.imperva.com/learn/application-security/shadow-dom/)
15. [html2canvas](https://html2canvas.org/) · [repo](https://github.com/html2canvas/html2canvas)
16. [LogChimp (logchimp/logchimp)](https://github.com/logchimp/logchimp)
17. [Canny widget (web)](https://developers.canny.io/install/widget/web) · [SSO](https://developers.canny.io/install/widget/sso)
18. [Featurebase](https://www.featurebase.app/)
19. [Frill](https://frill.co/) · [Canny-alternative / $10k testimonial context](https://frill.co/canny-alternative)
20. [Authenticating with a GitHub App](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app)
21. [GitHub community: server-side target allowlist discussion](https://github.com/orgs/community/discussions/187383)
22. [No GitHub API for issue attachments (community #46951)](https://github.com/orgs/community/discussions/46951) · [cli/cli #13256](https://github.com/cli/cli/issues/13256)
23. [wafir (BPS-Consulting/wafir)](https://github.com/BPS-Consulting/wafir) · [LICENSE (AGPL-3.0)](https://github.com/BPS-Consulting/wafir/blob/main/LICENSE)
24. [FastAPI: include_router vs Starlette mount (discussion #8682)](https://github.com/fastapi/fastapi/discussions/8682)
25. [Sentry Session Replay privacy/masking defaults](https://docs.sentry.io/platforms/javascript/session-replay/privacy/) · [EDPB consent guidelines](https://www.edpb.europa.eu/sites/default/files/files/file1/edpb_guidelines_202005_consent_en.pdf)
26. [Cloudflare Turnstile](https://developers.cloudflare.com/turnstile/) · [GA blog](https://blog.cloudflare.com/turnstile-ga/)
27. [slowapi](https://github.com/laurentS/slowapi) · [multi-worker issue #226](https://github.com/laurentS/slowapi/issues/226)
28. [GDPR session-replay PII masking guide](https://justanalytics.app/blog/gdpr-session-replay-pii-masking-guide)
29. [rrweb](https://github.com/rrweb-io/rrweb) · [guide.md defaults](https://github.com/rrweb-io/rrweb/blob/main/guide.md) · [issue #1609](https://github.com/rrweb-io/rrweb/issues/1609)
30. [modern-screenshot](https://github.com/qq15725/modern-screenshot)
31. [hCaptcha](https://www.hcaptcha.com/)
32. [Gleap replays docs](https://docs.gleap.io/documentation/javascript/replays)
33. [Common CORS errors (no frontend secret)](https://workos.com/blog/common-cors-errors-and-how-to-fix-them)
34. [Sentry data collected (IP as identifier)](https://docs.sentry.io/platforms/javascript/data-management/data-collected/)
35. [Bromb (samuelstroschein/bromb)](https://github.com/samuelstroschein/bromb)
36. [py2mcp](https://pypi.org/project/py2mcp/)
37. [Issue AI Agent — AI issue triage in ~500 LOC](https://www.squaredtech.co/ai-issue-triage-bot-built-in-500-lines-of-typescript-heres-how-it-wo)
38. [Open-Feedback (Neutron-Creative)](https://github.com/Neutron-Creative/Open-Feedback)
39. [GitHub App installation tokens: per-request override header (2026-05-15)](https://github.blog/changelog/2026-05-15-github-app-installation-tokens-per-request-override-header/)
40. [shogomuranushi/feedback-widget](https://github.com/shogomuranushi/feedback-widget)
41. [FasterFixes (manucoffin/faster-fixes)](https://github.com/manucoffin/faster-fixes)
42. [Papercups (papercups-io/papercups)](https://github.com/papercups-io/papercups)
43. [Chatwoot (chatwoot/chatwoot)](https://github.com/chatwoot/chatwoot)
44. [GlitchTip backend (GitLab)](https://gitlab.com/glitchtip/glitchtip-backend) · [LICENSE (MIT)](https://gitlab.com/glitchtip/glitchtip-backend/-/raw/master/LICENSE)
45. [Formbricks (formbricks/formbricks)](https://github.com/formbricks/formbricks)

\* **FeatureOS / Hellonext** corrected source: [features](https://featureos.com/features) · [pricing](https://featureos.com/pricing) (research's cited `productlift.dev` comparison URL returns 404).
