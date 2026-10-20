# ADR 0001: Review pages and public comment threads live in heed

Status: accepted (2026-10-20)

## Context

The owner wanted a page where an outside collaborator (a writer with no account) can watch a video or listen to audio takes and leave comments under it, persisted so that every visit shows the whole conversation and anyone with the page can add to it. It must be a reusable tool and agent skill, not a one-off, and the first consumer is a video made for a writer on a public enlace platform.

Candidates weighed:

- **heed** (this repo): an embeddable end-user feedback capability with a hardened unauthenticated endpoint (per-IP rate limiting, body caps, honeypot, bot gate, strict validation), dol-backed storage, a public read side (`board.py`) and an optional enlace add-on. A comment thread is end-user feedback with a public read side.
- **a zodal-suite package**: zodal is the schema-driven TypeScript UI layer. The hard part here is a hostile public write endpoint and its storage, which is Python backend work; zodal has no server.
- **annals**: already serves media with byte ranges, but it is the owner's *private* in-tray (`protected:user`), and making it public per document would mix two trust models in one app.
- **reelee's comment threads**: tied to reelee's server and domain model, not reusable standalone.
- **a new package**: no second mechanism to justify one; the hardening it would need is already in heed.

## Decision

Two modules in heed, layered:

1. `heed.threads`, the reusable mechanism: a comment thread per key (`Comment`, pure `add/list/delete` functions over any `MutableMapping`, and `make_thread_router`). It knows nothing of media. Who may reach a thread is the host's `thread_exists` callable; who may delete is the host's `is_moderator` callable (`email_moderator` reads `request.state.user_email`, which enlace_auth sets).
2. `heed.review`, one consumer of it: media plus a thread, published by `heed review publish` / `publish_review`, served by `make_review_app`, with a static page (`heed/static/review/`). It ships an agent skill, `heed-review`.

**Abuse gate: a capability link.** The page id is 128 random bits and travels in the URL fragment, so the link is never sent in a `Referer` (the page's API calls do carry it in their paths, so the host's access logs see it); without it the page, media and thread are 404. This is the strongest gate that needs nothing from the visitor. Layered on it: per-IP and per-thread rate limits, a per-thread cap, 20 kB body cap, honeypot, strict validation, `noindex` and `no-referrer`. heed's Turnstile/hCaptcha gate is not on by default because its keys are an open decision (heed#14); it can be added to the thread router when they exist.

**Moderation**: deleting a comment needs `is_moderator(request)` and an `X-Heed-Moderate` header; on tw that is the owner's enlace_auth session. A moderator's comments are marked "owner" on the page, so nobody can pass for the owner by name. There is no HTTP listing of pages: on a shared origin, any script running as the owner could read it and so collect every link. The CLI lists and deletes. Serving the app from its own origin would close the same-origin risk for deletes as well (follow-up issue).

**Frontend**: a static page with one script and one stylesheet, no build and no framework. It is a public page whose only state lives on the server, so the zodal/zustand stack's benefits do not apply; the one per-viewer convenience (the commenter's name) sits behind a small wrapper with try/catch.

## Seams

| Seam | v1 default | Replacement already in view |
|---|---|---|
| thread and page store | dol text files under one data root, one folder per thread (`folder_threads`) | an S3 dol store, same keys (tw_platform ADR-0024) |
| moderator check | nobody over HTTP | `email_moderator` (enlace_auth), used on tw |
| rate limiter | heed's in-memory limiter, per process | heed's `RedisRateLimiter` |

Not seams: the page's markup and style, the id length, the CLI's argument parsing. Media are local files (video needs byte ranges); serving them from S3 is a later change at `ReviewStores.media_path`.

## Consequences

- heed now has a public *read and write* surface besides the ingest endpoint, so the hardening rules in `embedding-security.md` apply to it as well.
- heed is not on PyPI (heed#11), so a host installs it from git, pinned to a commit.
- Anyone holding a link can read and post; leaking the link leaks the page. The skill says so.
