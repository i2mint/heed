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

### Functions

| [`github_sink_from_token`](#heed.sinks.github.github_sink_from_token)(repo, token, \*\*kwargs)   | Build a GitHubIssuesSink that creates issues in `repo` ('owner/name').   |
|----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| [`render_issue_body`](#heed.sinks.github.render_issue_body)(report, \*[, screenshot_url])   | Render a report as a GitHub-flavoured markdown issue body.               |

### Classes

| [`GitHubIssuesSink`](#heed.sinks.github.GitHubIssuesSink)(create_issue, \*[, ...])   | Route a report to a GitHub issue via an injected `create_issue` callable.   |
|----------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------|

### *class* heed.sinks.github.GitHubIssuesSink(create_issue, , label_map=None, screenshot_url=None)

Bases: [`BaseSink`](heed.sinks.base.md#heed.sinks.base.BaseSink)

Route a report to a GitHub issue via an injected `create_issue` callable.

### heed.sinks.github.github_sink_from_token(repo, token, \*\*kwargs)

Build a GitHubIssuesSink that creates issues in `repo` (‘owner/name’).

Requires the `heed[github]` extra. `token` is a server-side credential (a GitHub
App installation token or a PAT) — never shipped to the browser.

* **Return type:**
  [`GitHubIssuesSink`](#heed.sinks.github.GitHubIssuesSink)

### heed.sinks.github.render_issue_body(report, , screenshot_url=None)

Render a report as a GitHub-flavoured markdown issue body.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)
