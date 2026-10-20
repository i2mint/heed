# heed.sinks

Sinks: pluggable report destinations (strategy pattern).

Import the sink you need, or look one up by short name via [`get_sink_class()`](#heed.sinks.get_sink_class).

### Functions

| [`github_sink_from_token`](#heed.sinks.github_sink_from_token)(repo, token, \*\*kwargs)   | Build a GitHubIssuesSink that creates issues in `repo` ('owner/name').   |
|----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| [`render_issue_body`](#heed.sinks.render_issue_body)(report, \*[, ...])              | Render a report as a GitHub-flavoured markdown issue body.               |
| [`get_sink_class`](#heed.sinks.get_sink_class)(name)                              | Return a registered sink class by short name (e.g. 'github', 'store').   |

### Classes

| [`Sink`](#heed.sinks.Sink)(\*args, \*\*kwargs)                          | Anything that can receive a report.                                       |
|----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| [`BaseSink`](#heed.sinks.BaseSink)()                                        | Convenience base giving no-op defaults for the optional sink methods.     |
| [`SinkResult`](#heed.sinks.SinkResult)(\*, ok[, external_ref, url, detail])   | Outcome of routing a report to a sink.                                    |
| [`DupCandidate`](#heed.sinks.DupCandidate)(\*, external_ref[, score, title])    | A possible duplicate of an incoming report.                               |
| [`StoreSink`](#heed.sinks.StoreSink)(store)                                  | Persist each report as JSON into a dol-style store keyed by report id.    |
| [`GitHubIssuesSink`](#heed.sinks.GitHubIssuesSink)(create_issue, \*[, ...])         | Route a report to a GitHub issue via an injected `create_issue` callable. |
| [`CategorySink`](#heed.sinks.CategorySink)(sinks, \*, default)                  | Route by `report.category`; unlisted categories go to `default`.          |
| [`CategoryTreatment`](#heed.sinks.CategoryTreatment)([labels, title_prefix, detail]) | What one category of report becomes as a GitHub issue.                    |

### *class* heed.sinks.BaseSink

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Convenience base giving no-op defaults for the optional sink methods.

#### acknowledge(report)

Hook called after a successful submit (default: no-op).

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### find_duplicates(report)

Return likely duplicates of `report` (default: none).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`DupCandidate`](heed.sinks.base.html.md#heed.sinks.base.DupCandidate)]

#### public_items(, category=None)

The items a public board may show (default: none; see [`heed.board`](heed.board.html.md#module-heed.board)).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`PublicItem`](heed.board.html.md#heed.board.PublicItem)]

### *class* heed.sinks.CategorySink(sinks, , default)

Bases: [`BaseSink`](heed.sinks.base.html.md#heed.sinks.base.BaseSink)

Route by `report.category`; unlisted categories go to `default`.

#### acknowledge(report)

Hook called after a successful submit (default: no-op).

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### find_duplicates(report)

Return likely duplicates of `report` (default: none).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`DupCandidate`](heed.sinks.base.html.md#heed.sinks.base.DupCandidate)]

#### public_items(, category=None)

Every distinct member sink’s public items (a shared link once).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`PublicItem`](heed.board.html.md#heed.board.PublicItem)]

#### sink_for(category)

The sink that receives reports of `category`.

* **Return type:**
  [`Sink`](heed.sinks.base.html.md#heed.sinks.base.Sink)

#### sinks()

Every distinct member sink, including the default.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Sink`](heed.sinks.base.html.md#heed.sinks.base.Sink)]

### *class* heed.sinks.CategoryTreatment(labels=None, title_prefix='', detail=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

What one category of report becomes as a GitHub issue.

`detail="light"` is the feature-request intake: the idea and who asked, without
the browser/console/network dump or a screenshot, which say nothing about a wish.

### *class* heed.sinks.DupCandidate(, external_ref, score=0.0, title=None)

Bases: `BaseModel`

A possible duplicate of an incoming report.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.sinks.GitHubIssuesSink(create_issue, , label_map=None, screenshot_url=None, treatments=None, list_issues=None, public_label='public', status_labels=None, allowed_report_labels=())

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
  [`CategoryTreatment`](heed.sinks.github.html.md#heed.sinks.github.CategoryTreatment)

### *class* heed.sinks.Sink(\*args, \*\*kwargs)

Bases: [`Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)

Anything that can receive a report. `submit` is the only required method.

### *class* heed.sinks.SinkResult(, ok, external_ref=None, url=None, detail=None)

Bases: `BaseModel`

Outcome of routing a report to a sink.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.sinks.StoreSink(store)

Bases: [`BaseSink`](heed.sinks.base.html.md#heed.sinks.base.BaseSink)

Persist each report as JSON into a dol-style store keyed by report id.

The zero-configuration default when no external sink (e.g. GitHub) is configured.
`store` is any `MutableMapping[str, str]` (a plain dict, a `dol` file store,
S3, …) — dependency-injected so the sink stays testable.

#### find_duplicates(report)

Cheap title-equality duplicate scan over stored reports.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`DupCandidate`](heed.sinks.base.html.md#heed.sinks.base.DupCandidate)]

### heed.sinks.get_sink_class(name)

Return a registered sink class by short name (e.g. ‘github’, ‘store’).

* **Return type:**
  [`type`](https://docs.python.org/3/builtins/functions.html#type)

### heed.sinks.github_sink_from_token(repo, token, \*\*kwargs)

Build a GitHubIssuesSink that creates issues in `repo` (‘owner/name’).

Requires the `heed[github]` extra. `token` is a server-side credential (a GitHub
App installation token or a PAT) — never shipped to the browser.

* **Return type:**
  [`GitHubIssuesSink`](heed.sinks.github.html.md#heed.sinks.github.GitHubIssuesSink)

### heed.sinks.render_issue_body(report, , screenshot_url=None, light=False)

Render a report as a GitHub-flavoured markdown issue body.

`light` leaves out the environment, console and network (a feature request’s
intake; a screenshot the server kept is still linked): the idea, the page it came
from, and who asked.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### Modules

| [`base`](heed.sinks.base.html.md#module-heed.sinks.base)         | The sink abstraction — where a report goes (strategy pattern).                   |
|--------------------------------------------------------------------------------------|----------------------------------------------------------------------------------|
| [`category`](heed.sinks.category.html.md#module-heed.sinks.category) | A sink that sends each category of report to its own sink.                       |
| [`github`](heed.sinks.github.html.md#module-heed.sinks.github)     | A sink that turns a report into a GitHub issue.                                  |
| [`store`](heed.sinks.store.html.md#module-heed.sinks.store)       | A sink that persists reports into a Mapping (the default, dependency-free sink). |
