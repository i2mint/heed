# heed docs

Reference material for `heed`. Read on demand; this index is the map.

| Doc | What it is |
|---|---|
| [`design.md`](design.md) | **Architecture source of truth** — scope, the two-halves architecture, data model, the `Sink` interface, widget design, privacy/consent, public-endpoint hardening, GitHub sink, AI/MCP, enlace integration, phasing, open questions. |
| [`embedding-security.md`](embedding-security.md) | **Operating the public endpoint** — every hardening knob (`HEED_*`), the check order, production checklist, and CSP guidance for sites embedding the widget. |
| [`adr/0001-review-pages-and-comment-threads-live-in-heed.md`](adr/0001-review-pages-and-comment-threads-live-in-heed.md) | **Why review pages and comment threads are in heed**, the capability-link abuse gate, and the seams. |
| [`research-report.md`](research-report.md) | **Deep-research landscape** (with adversarial verification) — the spectrum of approaches, tool-by-tool what-to-steal/avoid, design patterns, gotchas, and the OSS ADOPT/FORK/STUDY shortlist. The evidence behind `design.md`. |

Durable design decisions and open questions are deliberated in GitHub
**Discussions**; near-term work is tracked in GitHub **Issues**.
