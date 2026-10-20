"""A sink that turns a report into a GitHub issue.

The issue-creating call is dependency-injected (``create_issue``) so the sink is
testable without network access and so the credential-brokering strategy (a GitHub App
minting short-lived installation tokens, or a server-side token) is pluggable. A
convenience factory builds a ``create_issue`` from a token via ``ghapi`` (lazy import,
the ``heed[github]`` extra).

Hard platform limit (see misc/docs/design.md): there is no GitHub API to attach an image
to an issue. Resolve a ``screenshot_url`` (committed-file / attachment-store URL) and
embed it in the body; base64 data-URIs do not render.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from heed.base import Category, Report, Status
from heed.board import PublicItem
from heed.sinks.base import BaseSink, SinkResult

# A callable that creates an issue and returns the GitHub API's issue dict.
CreateIssue = Callable[..., Mapping[str, Any]]

# A callable ``(*, labels, state) -> issues`` listing a repo's issues (the board's read
# side).
ListIssues = Callable[..., Iterable[Mapping[str, Any]]]


@dataclass(frozen=True)
class CategoryTreatment:
    """What one category of report becomes as a GitHub issue.

    ``detail="light"`` is the feature-request intake: the idea and who asked, without
    the browser/console/network dump or a screenshot, which say nothing about a wish.
    """

    labels: tuple[str, ...] | None = None  # None: the category's default labels
    title_prefix: str = ""
    detail: str | None = None  # "full" | "light"; None: the category's default

    def __post_init__(self) -> None:
        if isinstance(self.labels, str):  # "bug" means one label, not three letters
            object.__setattr__(self, "labels", (self.labels,))
        if self.detail not in (None, "full", "light"):
            raise ValueError(f"detail must be 'full' or 'light', got {self.detail!r}")


DEFAULT_TREATMENTS: dict[Category, CategoryTreatment] = {
    Category.bug: CategoryTreatment(("bug",)),
    Category.feature: CategoryTreatment(("enhancement",), detail="light"),
    Category.question: CategoryTreatment(("question",), detail="light"),
    Category.other: CategoryTreatment((), detail="full"),
}

# Kept for callers that read the label defaults.
DEFAULT_LABELS: dict[Category, tuple[str, ...]] = {
    category: treatment.labels for category, treatment in DEFAULT_TREATMENTS.items()
}

#: Issue labels a maintainer applies to move an item along; the board reads them back.
DEFAULT_STATUS_LABELS: dict[str, Status] = {
    "triaged": Status.triaged,
    "planned": Status.planned,
    "in progress": Status.started,
    "duplicate": Status.duplicate,
    "wontfix": Status.declined,
}

#: The most pages (of 100 issues) one board read will fetch.
MAX_BOARD_PAGES = 10

#: A maintainer adds this label to put an item on the public board. Opt-in, because the
#: title of an unmoderated report must not reach a public page by default.
DEFAULT_PUBLIC_LABEL = "public"


class GitHubIssuesSink(BaseSink):
    """Route a report to a GitHub issue via an injected ``create_issue`` callable.

    ``treatments`` says, per category, the labels, a title prefix and how much context
    the issue carries (``label_map`` is the older labels-only spelling and still wins
    for labels). With ``list_issues`` it can also read its issues back as a board's
    :class:`~heed.board.PublicItem` entries: only those carrying ``public_label``, with
    status from ``status_labels`` (or the issue's closed state) and votes from the 👍
    reactions.
    """

    def __init__(
        self,
        create_issue: CreateIssue,
        *,
        label_map: Mapping[Category, tuple[str, ...]] | None = None,
        screenshot_url: Callable[[Report], str | None] | None = None,
        treatments: Mapping[Category, CategoryTreatment] | None = None,
        list_issues: ListIssues | None = None,
        public_label: str = DEFAULT_PUBLIC_LABEL,
        status_labels: Mapping[str, Status] | None = None,
        allowed_report_labels: Iterable[str] = (),
    ):
        self._create_issue = create_issue
        self._allowed_report_labels = frozenset(_fold(x) for x in allowed_report_labels)
        self._screenshot_url = screenshot_url
        # ``label_map`` is the labels-only spelling and, as before, replaces the whole
        # label table (a category it omits gets no label).
        base = dict(DEFAULT_TREATMENTS)
        if label_map is not None:
            base = {
                category: CategoryTreatment(
                    tuple(label_map.get(category, ())),
                    detail=DEFAULT_TREATMENTS[category].detail,
                )
                for category in Category
            }
        self._treatments = {
            category: _resolve(base.get(category), (treatments or {}).get(category))
            for category in Category
        }
        self._list_issues = list_issues
        self._public_label = public_label
        self._status_labels = dict(
            DEFAULT_STATUS_LABELS if status_labels is None else status_labels
        )
        # Never let a reporter apply the board's gate or a maintainer status label.
        self._reserved_labels = {
            _fold(x) for x in (self._public_label, *self._status_labels)
        }

    def treatment(self, category: Category) -> CategoryTreatment:
        """The treatment applied to reports of ``category``."""
        return self._treatments[Category(category)]

    def submit(self, report: Report) -> SinkResult:
        treatment = self.treatment(report.category)
        labels = list(treatment.labels) + [
            label
            for label in report.labels
            if _fold(label) in self._allowed_report_labels
            and _fold(label) not in self._reserved_labels
        ]
        light = treatment.detail == "light"
        # A screenshot the server kept is always linked, even in a light issue: kept
        # data that nothing references would be retained for no reason.
        url = self._screenshot_url(report) if self._screenshot_url else None
        body = render_issue_body(report, screenshot_url=url, light=light)
        issue = self._create_issue(
            title=neutralise_refs(treatment.title_prefix + report.title),
            body=body,
            labels=labels,
        )
        number = issue.get("number")
        return SinkResult(
            ok=True,
            external_ref=str(number) if number is not None else None,
            url=issue.get("html_url"),
        )

    def public_items(self, *, category: Category | None = None) -> list[PublicItem]:
        """The board's items: issues labelled ``public_label``, as :class:`PublicItem`.

        Empty without ``list_issues``. Only title, category, status, link, votes and
        dates leave the sink: never an issue body (which holds the reporter's
        environment and identity).
        """
        if self._list_issues is None:
            return []
        wanted = None if category is None else Category(category)
        items = []
        for issue in self._list_issues(labels=[self._public_label], state="all"):
            if "pull_request" in issue or issue.get("number") is None:
                continue  # the issues API also returns pull requests
            names = [_label_name(label) for label in issue.get("labels", ())]
            if self._public_label not in names:
                continue
            item_category = self._category_of(names)
            if wanted is not None and item_category is not wanted:
                continue
            items.append(
                PublicItem(
                    ref=str(issue["number"]),
                    title=str(issue.get("title") or ""),
                    category=item_category,
                    status=self._status_of(issue, set(names)),
                    url=issue.get("html_url"),
                    votes=_votes(issue),
                    created_at=issue.get("created_at"),
                    closed_at=issue.get("closed_at"),
                )
            )
        return items

    def _category_of(self, names: list[str]) -> Category:
        """The category whose labels the issue carries; the earliest label wins."""
        have = set(names)
        for name in names:
            for category, treatment in self._treatments.items():
                labels = treatment.labels or ()
                if name in labels and set(labels) <= have:
                    return category
        return Category.other

    def _status_of(self, issue: Mapping[str, Any], names: set[str]) -> Status:
        """Status from the issue's state and labels.

        A closed issue is finished whatever stage label it still carries; a terminal
        label (duplicate, wontfix) wins over the state; of several stage labels the
        furthest along counts.
        """
        marked = [s for label, s in self._status_labels.items() if label in names]
        terminal = [s for s in marked if s in _TERMINAL]
        if terminal:
            return terminal[0]
        if issue.get("state") == "closed":
            reason = issue.get("state_reason")
            if reason == "duplicate":
                return Status.duplicate
            return Status.declined if reason == "not_planned" else Status.completed
        stages = [s for s in marked if s in _STAGES]
        return max(stages, key=_STAGES.index) if stages else Status.received


#: Open-issue stages, least to furthest along.
_STAGES = (Status.received, Status.triaged, Status.planned, Status.started)
_TERMINAL = (Status.duplicate, Status.declined, Status.completed)


def _resolve(
    default: CategoryTreatment | None, given: CategoryTreatment | None
) -> CategoryTreatment:
    """``given`` over ``default``, field by field (``None`` keeps the default)."""
    base_labels = () if default is None or default.labels is None else default.labels
    base_detail = (
        "full" if default is None or default.detail is None else default.detail
    )
    if given is None:
        return CategoryTreatment(
            base_labels, default.title_prefix if default else "", base_detail
        )
    return CategoryTreatment(
        labels=base_labels if given.labels is None else tuple(given.labels),
        title_prefix=given.title_prefix,
        detail=base_detail if given.detail is None else given.detail,
    )


def _fold(label: str) -> str:
    """GitHub label names are case-insensitive; compare them folded."""
    return str(label).strip().casefold()


def _label_name(label: Any) -> str:
    return str(label.get("name", "")) if isinstance(label, Mapping) else str(label)


def _votes(issue: Mapping[str, Any]) -> int:
    reactions = issue.get("reactions")
    plus = reactions.get("+1", 0) if isinstance(reactions, Mapping) else 0
    return plus if isinstance(plus, int) and plus > 0 else 0


_REF_CHARS = re.compile(r"(?<![\w`])([@#])(?=\w)")
# Bare issue/PR URLs and commit SHAs (7-40 hex chars) are auto-linked by GitHub too.
_GH_URL = re.compile(r"(?i)(github)\.com/(?=[\w.-]+/[\w.-]+/(?:issues|pull)/\d)")
_SHA = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b", re.I)


def neutralise_refs(text: str) -> str:
    """Defuse GitHub ``@mention``, ``#123``, issue-URL and SHA references.

    A zero-width joiner after ``@``/``#`` stops GitHub notifying a user or cross-linking
    an issue from the tracker's account. Only the rendered issue is changed; the stored
    report keeps the original text.
    """
    text = _REF_CHARS.sub("\\1\u200d", text)
    text = _GH_URL.sub("\\1\u200d.com/", text)
    return _SHA.sub(lambda m: m.group(0)[:3] + "\u200d" + m.group(0)[3:], text)


def render_issue_body(
    report: Report, *, screenshot_url: str | None = None, light: bool = False
) -> str:
    """Render a report as a GitHub-flavoured markdown issue body.

    ``light`` leaves out the environment, console and network (a feature request's
    intake; a screenshot the server kept is still linked): the idea, the page it came
    from, and who asked.
    """
    lines: list[str] = []
    if report.body:
        lines += [neutralise_refs(report.body), ""]

    reporter = (
        neutralise_refs(report.identity.user or "")
        or f"anonymous ({report.identity.anon_id[:8]}…)"
    )
    meta = [f"- **Category**: {report.category.value}"]
    if report.page_url:
        meta.append(f"- **URL**: {neutralise_refs(report.page_url)}")
    if not light and (report.env.browser or report.env.os):
        env_line = neutralise_refs(
            f"- **Env**: {report.env.browser or '?'} on {report.env.os or '?'}"
        )
        if report.env.viewport:
            env_line += neutralise_refs(f" ({report.env.viewport})")
        meta.append(env_line)
    meta.append(f"- **Reporter**: {reporter}")
    lines += meta + [""]

    if screenshot_url:
        lines += [f"![screenshot]({screenshot_url})", ""]
    if light:
        return "\n".join(lines).rstrip() + "\n"
    if report.console:
        lines += _collapsible(
            "Console",
            neutralise_refs(
                "\n".join(f"[{e.level}] {e.message}" for e in report.console)
            ),
        )
    if report.network:
        lines += _collapsible(
            "Network",
            neutralise_refs(
                "\n".join(
                    f"{e.method} {e.url} -> {e.status if e.status is not None else '?'}"
                    for e in report.network
                )
            ),
        )
    return "\n".join(lines).rstrip() + "\n"


def _collapsible(summary: str, content: str) -> list[str]:
    # A fence longer than any backtick run in the (untrusted) content cannot be closed
    # early by it.
    longest = max((len(m) for m in re.findall(r"`+", content)), default=0)
    fence = "`" * max(3, longest + 1)
    return [
        "<details>",
        f"<summary>{summary}</summary>",
        "",
        fence,
        content,
        fence,
        "",
        "</details>",
        "",
    ]


def github_sink_from_token(repo: str, token: str, **kwargs) -> GitHubIssuesSink:
    """Build a GitHubIssuesSink that creates issues in ``repo`` ('owner/name').

    Requires the ``heed[github]`` extra. ``token`` is a server-side credential (a GitHub
    App installation token or a PAT) — never shipped to the browser.
    """
    try:
        from ghapi.all import GhApi
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "GitHubIssuesSink via token needs the 'github' extra: "
            "pip install 'heed[github]'"
        ) from e

    owner, _, name = repo.partition("/")
    api = GhApi(owner=owner, repo=name, token=token)

    def create_issue(*, title: str, body: str, labels: list[str]) -> Mapping[str, Any]:
        return api.issues.create(title=title, body=body, labels=labels)

    def list_issues(*, labels: list[str], state: str = "all"):
        from itertools import islice

        from ghapi.all import paged

        pages = paged(
            api.issues.list_for_repo,
            labels=",".join(labels),
            state=state,
            per_page=100,
        )
        for page in islice(pages, MAX_BOARD_PAGES):
            yield from page

    kwargs.setdefault("list_issues", list_issues)
    return GitHubIssuesSink(create_issue, **kwargs)
