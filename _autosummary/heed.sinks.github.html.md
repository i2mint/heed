# heed.sinks.github

A sink that turns a report into a GitHub issue.

The issue-creating call is dependency-injected (`create_issue`) so the sink is
testable without network access and so the credential-brokering strategy (a GitHub App
minting short-lived installation tokens, or a server-side token) is pluggable. A
convenience factory builds a `create_issue` from a token via `ghapi` (lazy import,
the `heed[github]` extra).

Hard platform limit (see misc/docs/design.md): there is no GitHub API to attach an image
to an issue. Resolve a `screenshot_url` (committed-file / attachment-store URL) and
embed it in the body; base64 data-URIs do not render.

### Module Attributes

| [`DEFAULT_STATUS_LABELS`](#heed.sinks.github.DEFAULT_STATUS_LABELS)   | Issue labels a maintainer applies to move an item along; the board reads them back.   |
|--------------------------------------------------------------------------|---------------------------------------------------------------------------------------|
| [`MAX_BOARD_PAGES`](#heed.sinks.github.MAX_BOARD_PAGES)         | The most pages (of 100 issues) one board read will fetch.                             |
| [`DEFAULT_PUBLIC_LABEL`](#heed.sinks.github.DEFAULT_PUBLIC_LABEL)    | A maintainer adds this label to put an item on the public board.                      |

### Functions

| [`github_sink_from_token`](#heed.sinks.github.github_sink_from_token)(repo, token, \*\*kwargs)   | Build a GitHubIssuesSink that creates issues in `repo` ('owner/name').   |
|----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| [`neutralise_refs`](#heed.sinks.github.neutralise_refs)(text)                             | Defuse GitHub `@mention` and `#123` references in untrusted text.        |
| [`render_issue_body`](#heed.sinks.github.render_issue_body)(report, \*[, ...])              | Render a report as a GitHub-flavoured markdown issue body.               |

### Classes

| [`CategoryTreatment`](#heed.sinks.github.CategoryTreatment)([labels, title_prefix, detail])   | What one category of report becomes as a GitHub issue.                    |
|------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| [`GitHubIssuesSink`](#heed.sinks.github.GitHubIssuesSink)(create_issue, \*[, ...])           | Route a report to a GitHub issue via an injected `create_issue` callable. |

### *class* heed.sinks.github.CategoryTreatment(labels=None, title_prefix='', detail=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

What one category of report becomes as a GitHub issue.

`detail="light"` is the feature-request intake: the idea and who asked, without
the browser/console/network dump or a screenshot, which say nothing about a wish.

### heed.sinks.github.DEFAULT_PUBLIC_LABEL *= 'public'*

A maintainer adds this label to put an item on the public board. Opt-in, because the
title of an unmoderated report must not reach a public page by default.

### heed.sinks.github.DEFAULT_STATUS_LABELS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [Status](heed.base.html.md#heed.base.Status)]* *= {'duplicate': Status.duplicate, 'in progress': Status.started, 'planned': Status.planned, 'triaged': Status.triaged, 'wontfix': Status.declined}*

Issue labels a maintainer applies to move an item along; the board reads them back.

### *class* heed.sinks.github.GitHubIssuesSink(create_issue, , label_map=None, screenshot_url=None, treatments=None, list_issues=None, public_label='public', status_labels=None)

Bases: [`BaseSink`](heed.sinks.base.html.md#heed.sinks.base.BaseSink)

Route a report to a GitHub issue via an injected `create_issue` callable.

`treatments` says, per category, the labels, a title prefix and how much context
the issue carries (`label_map` is the older labels-only spelling and still wins
for labels). With `list_issues` it can also read its issues back as a board’s
[`PublicItem`](heed.board.html.md#heed.board.PublicItem) entries: only those carrying `public_label`, with
status from `status_labels` (or the issue’s closed state) and votes from the 👍
reactions.

#### public_items(, category=None)

The board’s items: issues labelled `public_label`, as `PublicItem`.

Empty without `list_issues`. Only title, category, status, link, votes and
dates leave the sink: never an issue body (which holds the reporter’s
environment and identity).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`PublicItem`](heed.board.html.md#heed.board.PublicItem)]

#### treatment(category)

The treatment applied to reports of `category`.

* **Return type:**
  [`CategoryTreatment`](#heed.sinks.github.CategoryTreatment)

### heed.sinks.github.MAX_BOARD_PAGES *= 10*

The most pages (of 100 issues) one board read will fetch.

### heed.sinks.github.github_sink_from_token(repo, token, \*\*kwargs)

Build a GitHubIssuesSink that creates issues in `repo` (‘owner/name’).

Requires the `heed[github]` extra. `token` is a server-side credential (a GitHub
App installation token or a PAT) — never shipped to the browser.

* **Return type:**
  [`GitHubIssuesSink`](#heed.sinks.github.GitHubIssuesSink)

### heed.sinks.github.neutralise_refs(text)

Defuse GitHub `@mention` and `#123` references in untrusted text.

A zero-width joiner after `@`/`#` stops GitHub notifying a user or cross-linking
an issue from the tracker’s account. Only the rendered issue is changed; the stored
report keeps the original text.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### heed.sinks.github.render_issue_body(report, , screenshot_url=None, light=False)

Render a report as a GitHub-flavoured markdown issue body.

`light` leaves out the environment, console and network (a feature request’s
intake; a screenshot the server kept is still linked): the idea, the page it came
from, and who asked.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)
