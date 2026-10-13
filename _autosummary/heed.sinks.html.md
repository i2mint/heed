# heed.sinks

Sinks: pluggable report destinations (strategy pattern).

Import the sink you need, or look one up by short name via [`get_sink_class()`](#heed.sinks.get_sink_class).

### Functions

| [`github_sink_from_token`](#heed.sinks.github_sink_from_token)(repo, token, \*\*kwargs)   | Build a GitHubIssuesSink that creates issues in `repo` ('owner/name').   |
|----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| [`render_issue_body`](#heed.sinks.render_issue_body)(report, \*[, screenshot_url])   | Render a report as a GitHub-flavoured markdown issue body.               |
| [`get_sink_class`](#heed.sinks.get_sink_class)(name)                              | Return a registered sink class by short name (e.g. 'github', 'store').   |

### Classes

| [`Sink`](#heed.sinks.Sink)(\*args, \*\*kwargs)                  | Anything that can receive a report.                                       |
|--------------------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| [`BaseSink`](#heed.sinks.BaseSink)()                                | Convenience base giving no-op defaults for the optional sink methods.     |
| [`SinkResult`](#heed.sinks.SinkResult)(\*\*data)                      | Outcome of routing a report to a sink.                                    |
| [`DupCandidate`](#heed.sinks.DupCandidate)(\*\*data)                    | A possible duplicate of an incoming report.                               |
| [`StoreSink`](#heed.sinks.StoreSink)(store)                          | Persist each report as JSON into a dol-style store keyed by report id.    |
| [`GitHubIssuesSink`](#heed.sinks.GitHubIssuesSink)(create_issue, \*[, ...]) | Route a report to a GitHub issue via an injected `create_issue` callable. |

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

### *class* heed.sinks.DupCandidate(\*\*data)

Bases: `BaseModel`

A possible duplicate of an incoming report.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.sinks.GitHubIssuesSink(create_issue, , label_map=None, screenshot_url=None)

Bases: [`BaseSink`](heed.sinks.base.html.md#heed.sinks.base.BaseSink)

Route a report to a GitHub issue via an injected `create_issue` callable.

### *class* heed.sinks.Sink(\*args, \*\*kwargs)

Bases: [`Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)

Anything that can receive a report. `submit` is the only required method.

### *class* heed.sinks.SinkResult(\*\*data)

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

### heed.sinks.render_issue_body(report, , screenshot_url=None)

Render a report as a GitHub-flavoured markdown issue body.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### Modules

| [`base`](heed.sinks.base.html.md#module-heed.sinks.base)     | The sink abstraction — where a report goes (strategy pattern).                   |
|----------------------------------------------------------------------------------|----------------------------------------------------------------------------------|
| [`github`](heed.sinks.github.html.md#module-heed.sinks.github) | A sink that turns a report into a GitHub issue.                                  |
| [`store`](heed.sinks.store.html.md#module-heed.sinks.store)   | A sink that persists reports into a Mapping (the default, dependency-free sink). |
