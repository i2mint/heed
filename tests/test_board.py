"""Per-category sink behaviour and the board's read side (heed#9).

One widget and one Report cover bug | feature | question; the sink decides what each
becomes. These tests pin: feature requests get the light intake and their own labels,
categories can be routed to different sinks, and a board lists only maintainer-approved
items, exposing nothing but the public projection.
"""

from datetime import datetime, timezone

import pytest

from heed import (
    Category,
    CategorySink,
    CategoryTreatment,
    Environment,
    GitHubIssuesSink,
    Identity,
    PublicItem,
    Report,
    Status,
    StoreSink,
    changelog,
    roadmap,
)
from heed.board import BoardSource
from heed.sinks import get_sink_class, render_issue_body

ENV = Environment(browser="Firefox", os="Linux", viewport="1280x720")


def _report(**kw) -> Report:
    kw.setdefault("title", "T")
    kw.setdefault("identity", Identity(anon_id="anon12345"))
    kw.setdefault("env", ENV)
    kw.setdefault("page_url", "https://app.example/settings")
    return Report(**kw)


class FakeGitHub:
    """Records created issues; lists a canned issue set."""

    def __init__(self, issues=()):
        self.created = []
        self.issues = list(issues)
        self.list_calls = []

    def create_issue(self, *, title, body, labels):
        self.created.append({"title": title, "body": body, "labels": labels})
        return {"number": len(self.created), "html_url": "https://gh/x/y/issues/1"}

    def list_issues(self, *, labels, state="all"):
        self.list_calls.append((tuple(labels), state))
        return list(self.issues)


# ------------------------------------------------------------ per-category treatment


def test_a_feature_request_is_a_light_labelled_issue():
    gh = FakeGitHub()
    sink = GitHubIssuesSink(
        gh.create_issue, screenshot_url=lambda r: "https://x/shot.png"
    )
    sink.submit(_report(category=Category.feature, title="Dark mode", body="please"))
    (issue,) = gh.created
    assert issue["labels"] == ["enhancement"]
    assert "please" in issue["body"] and "**Category**: feature" in issue["body"]
    # the light intake leaves out the environment
    assert "Firefox" not in issue["body"]
    assert "![screenshot](https://x/shot.png)" in issue["body"]  # a kept shot is linked
    assert "https://app.example/settings" in issue["body"]


def test_a_bug_keeps_the_full_context():
    gh = FakeGitHub()
    sink = GitHubIssuesSink(
        gh.create_issue, screenshot_url=lambda r: "https://x/shot.png"
    )
    sink.submit(_report(category=Category.bug))
    body = gh.created[0]["body"]
    assert "Firefox on Linux" in body and "![screenshot](https://x/shot.png)" in body


def test_treatments_are_data_prefix_labels_and_detail():
    gh = FakeGitHub()
    sink = GitHubIssuesSink(
        gh.create_issue,
        treatments={
            Category.feature: CategoryTreatment(
                ("idea", "needs-triage"), title_prefix="[idea] ", detail="full"
            )
        },
    )
    sink.submit(_report(category=Category.feature, title="Dark mode"))
    issue = gh.created[0]
    assert issue["title"] == "[idea] Dark mode"
    assert issue["labels"] == ["idea", "needs-triage"]
    assert "Firefox" in issue["body"]  # detail="full" restores the context


def test_the_older_label_map_still_wins_for_labels():
    gh = FakeGitHub()
    sink = GitHubIssuesSink(gh.create_issue, label_map={Category.feature: ("wish",)})
    sink.submit(_report(category=Category.feature))
    assert gh.created[0]["labels"] == ["wish"]
    assert "Firefox" not in gh.created[0]["body"]  # still the light default


def test_an_unknown_detail_is_rejected_at_construction():
    with pytest.raises(ValueError, match="detail"):
        CategoryTreatment(detail="medium")


def test_render_light_keeps_the_idea_and_the_reporter_only():
    body = render_issue_body(_report(body="the idea"), light=True)
    assert "the idea" in body and "anonymous" in body and "Firefox" not in body


# ------------------------------------------------------------------ CategorySink


def test_categories_route_to_their_own_sinks_with_a_default():
    bugs, ideas = StoreSink({}), StoreSink({})
    sink = CategorySink({"feature": ideas}, default=bugs)
    sink.submit(_report(category=Category.feature, title="idea"))
    sink.submit(_report(category=Category.bug, title="bug"))
    sink.submit(_report(category=Category.question, title="q"))
    assert [Report.model_validate_json(v).title for v in ideas.store.values()] == [
        "idea"
    ]
    assert sorted(Report.model_validate_json(v).title for v in bugs.store.values()) == [
        "bug",
        "q",
    ]


def test_duplicate_detection_asks_the_category_s_own_sink():
    bugs, ideas = StoreSink({}), StoreSink({})
    sink = CategorySink({Category.feature: ideas}, default=bugs)
    first = _report(category=Category.feature, title="same")
    sink.submit(first)
    again = _report(category=Category.feature, title="same")
    assert [d.external_ref for d in sink.find_duplicates(again)] == [first.id]
    assert sink.find_duplicates(_report(category=Category.bug, title="same")) == []


def test_unknown_category_names_are_rejected():
    with pytest.raises(ValueError):
        CategorySink({"nonsense": StoreSink({})}, default=StoreSink({}))


def test_the_category_sink_is_registered_by_name():
    assert get_sink_class("category") is CategorySink


# ---------------------------------------------------------------------- the board


def _issue(number, title, labels, *, state="open", reactions=0, **extra):
    return {
        "number": number,
        "title": title,
        "html_url": f"https://gh/x/y/issues/{number}",
        "labels": [{"name": name} for name in labels],
        "state": state,
        "reactions": {"+1": reactions, "-1": 7},
        "created_at": "2026-01-02T03:04:05Z",
        "closed_at": "2026-02-03T00:00:00Z" if state == "closed" else None,
        "body": "SECRET reporter environment and identity",
        **extra,
    }


def _board_sink(issues):
    gh = FakeGitHub(issues)
    return GitHubIssuesSink(gh.create_issue, list_issues=gh.list_issues), gh


def test_a_sink_without_list_issues_has_an_empty_board():
    sink = GitHubIssuesSink(FakeGitHub().create_issue)
    assert sink.public_items() == []
    assert StoreSink({}).public_items() == []


def test_the_board_lists_only_opted_in_issues_and_asks_for_that_label():
    sink, gh = _board_sink(
        [
            _issue(1, "Dark mode", ["enhancement", "public", "planned"], reactions=4),
            _issue(2, "Not approved", ["enhancement"], reactions=99),
            _issue(3, "A PR", ["public"], pull_request={"url": "x"}),
        ]
    )
    items = sink.public_items()
    assert [i.title for i in items] == ["Dark mode"]
    assert gh.list_calls == [(("public",), "all")]


def test_an_item_carries_category_status_votes_and_dates():
    sink, _ = _board_sink(
        [_issue(1, "Dark mode", ["enhancement", "public", "in progress"], reactions=4)]
    )
    (item,) = sink.public_items()
    assert item.category is Category.feature
    assert item.status is Status.started
    assert item.votes == 4  # only the thumbs-up; the -1 is not a vote against
    assert item.url.endswith("/1") and item.ref == "1"
    assert item.created_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def test_status_falls_back_to_the_issue_state():
    sink, _ = _board_sink(
        [
            _issue(1, "shipped", ["public"], state="closed"),
            _issue(
                2, "declined", ["public"], state="closed", state_reason="not_planned"
            ),
            _issue(3, "new", ["public"]),
        ]
    )
    status = {i.title: i.status for i in sink.public_items()}
    assert status == {
        "shipped": Status.completed,
        "declined": Status.declined,
        "new": Status.received,
    }


def test_the_public_projection_leaks_nothing_else():
    sink, _ = _board_sink([_issue(1, "Dark mode", ["public", "bug"])])
    dumped = sink.public_items()[0].model_dump_json()
    assert "SECRET" not in dumped
    assert set(PublicItem.model_fields) == {
        "ref",
        "title",
        "category",
        "status",
        "url",
        "votes",
        "created_at",
        "closed_at",
    }


def test_category_filter_and_custom_public_label():
    gh = FakeGitHub(
        [
            _issue(1, "idea", ["roadmap", "enhancement"]),
            _issue(2, "bug", ["roadmap", "bug"]),
        ]
    )
    sink = GitHubIssuesSink(
        gh.create_issue, list_issues=gh.list_issues, public_label="roadmap"
    )
    assert [i.title for i in sink.public_items(category=Category.feature)] == ["idea"]
    assert len(sink.public_items()) == 2


def test_negative_or_missing_reactions_are_zero_votes():
    sink, _ = _board_sink(
        [_issue(1, "x", ["public"]), {**_issue(2, "y", ["public"]), "reactions": None}]
    )
    assert [i.votes for i in sink.public_items()] == [0, 0]


def test_category_sink_unions_its_members_boards_once():
    sink_a, _ = _board_sink([_issue(1, "idea", ["public", "enhancement"])])
    combined = CategorySink(
        {Category.feature: sink_a, Category.bug: sink_a}, default=sink_a
    )
    assert [i.title for i in combined.public_items()] == ["idea"]
    assert isinstance(sink_a, BoardSource)


# ------------------------------------------------------- roadmap / changelog views


def _items():
    def item(ref, status, votes, closed=None):
        return PublicItem(
            ref=ref, title=ref, status=status, votes=votes, closed_at=closed
        )

    return [
        item("a", Status.planned, 1),
        item("b", Status.planned, 9),
        item("c", Status.started, 0),
        item("d", Status.completed, 5, datetime(2026, 1, 1, tzinfo=timezone.utc)),
        item("e", Status.completed, 5, datetime(2026, 3, 1, tzinfo=timezone.utc)),
        item("f", Status.completed, 5),
        item("g", Status.declined, 50),
        item("h", Status.duplicate, 50),
    ]


def test_roadmap_orders_by_stage_then_votes_and_drops_the_rest():
    assert [i.ref for i in roadmap(_items())] == ["c", "b", "a"]


def test_changelog_is_the_shipped_items_newest_first():
    assert [i.ref for i in changelog(_items())] == ["e", "d", "f"]


# ------------------------------------------- review fixes (heed#50 review round 1)


def test_a_closed_issue_is_finished_whatever_stage_label_it_keeps():
    sink, _ = _board_sink(
        [
            _issue(1, "done", ["public", "planned"], state="closed"),
            _issue(
                2,
                "no",
                ["public", "in progress"],
                state="closed",
                state_reason="not_planned",
            ),
            _issue(3, "dup", ["public", "planned", "duplicate"], state="closed"),
            _issue(4, "far", ["public", "triaged", "planned", "in progress"]),
            _issue(5, "open dup", ["public", "duplicate"]),
        ]
    )
    status = {i.title: i.status for i in sink.public_items()}
    assert status == {
        "done": Status.completed,
        "no": Status.declined,
        "dup": Status.duplicate,
        "far": Status.started,
        "open dup": Status.duplicate,
    }


def test_label_map_still_replaces_the_whole_label_table():
    gh = FakeGitHub()
    sink = GitHubIssuesSink(gh.create_issue, label_map={Category.bug: ("defect",)})
    sink.submit(_report(category=Category.bug))
    sink.submit(_report(category=Category.feature))
    assert [c["labels"] for c in gh.created] == [["defect"], []]


def test_a_partial_treatment_keeps_the_categorys_other_defaults():
    gh = FakeGitHub()
    sink = GitHubIssuesSink(
        gh.create_issue,
        treatments={Category.feature: CategoryTreatment(title_prefix="[idea] ")},
    )
    sink.submit(_report(category=Category.feature, title="Dark mode"))
    assert gh.created[0]["labels"] == ["enhancement"]
    assert gh.created[0]["title"] == "[idea] Dark mode"
    assert "Firefox" not in gh.created[0]["body"]  # still the light intake


def test_category_filters_accept_strings():
    sink, _ = _board_sink([_issue(1, "idea", ["public", "enhancement"])])
    assert len(sink.public_items(category="feature")) == 1
    assert sink.public_items(category="bug") == []
    combined = CategorySink({"feature": sink}, default=sink)
    assert len(combined.public_items(category="feature")) == 1


def test_the_earliest_label_decides_a_category_tie_and_unnumbered_issues_are_skipped():
    sink, _ = _board_sink(
        [
            _issue(1, "a", ["public", "enhancement", "bug"]),
            _issue(2, "b", ["public", "bug", "enhancement"]),
            {**_issue(3, "c", ["public"]), "number": None},
        ]
    )
    assert [(i.title, i.category) for i in sink.public_items()] == [
        ("a", Category.feature),
        ("b", Category.bug),
    ]


def test_two_sinks_on_one_repo_list_each_item_once():
    one, _ = _board_sink([_issue(1, "idea", ["public", "enhancement"])])
    two, _ = _board_sink([_issue(1, "idea", ["public", "enhancement"])])
    combined = CategorySink({Category.feature: one}, default=two)
    assert len(combined.public_items()) == 1


def test_ingest_drops_screenshot_and_browser_details_for_light_categories():
    from heed import HeedConfig, LogEntry, ReportSubmission, process_submission

    attachments: dict = {}

    def run(category, **config):
        sub = ReportSubmission(
            title="t",
            category=category,
            env=ENV.model_copy(update={"extra": {"version": "1"}}),
            console=[LogEntry(level="error", message="boom")],
        )
        return process_submission(
            sub,
            StoreSink({}),
            screenshot=b"PNG",
            attachment_store=attachments,
            config=HeedConfig(accept_console=True, **config),
        )[0]

    feature = run("feature")
    assert feature.attachments == [] and attachments == {}
    assert feature.env.browser is None and feature.env.extra == {"version": "1"}
    assert feature.console is None
    bug = run("bug")
    assert bug.attachments and bug.env.browser == "Firefox" and bug.console
    kept = run("feature", light_categories=())
    assert kept.attachments and kept.env.browser == "Firefox"


# ------------------------------------------- review fixes (heed#50 review round 2)


@pytest.mark.parametrize("category", list(Category))
def test_every_category_goes_through_the_github_sink(category):
    gh = FakeGitHub()
    GitHubIssuesSink(gh.create_issue).submit(_report(category=category))
    assert len(gh.created) == 1 and isinstance(gh.created[0]["labels"], list)


def test_other_survives_partial_treatments_and_a_default_label_table():
    gh = FakeGitHub()
    sink = GitHubIssuesSink(
        gh.create_issue,
        treatments={Category.other: CategoryTreatment(title_prefix="x ")},
    )
    sink.submit(_report(category=Category.other, title="t"))
    assert gh.created[0]["title"] == "x t" and gh.created[0]["labels"] == []
    from heed.sinks.github import DEFAULT_LABELS

    assert all(isinstance(v, tuple) for v in DEFAULT_LABELS.values())
    GitHubIssuesSink(gh.create_issue, label_map=DEFAULT_LABELS)


def test_light_categories_are_validated_and_readable_from_the_environment():
    from heed import HeedConfig

    assert HeedConfig().light_categories == ["feature", "question"]
    with pytest.raises(ValueError, match="unknown categories"):
        HeedConfig(light_categories=["features"])
    with pytest.raises(TypeError):
        HeedConfig(light_categories="feature")
    env = {"HEED_LIGHT_CATEGORIES": "feature, bug"}
    assert HeedConfig.from_env(env).light_categories == ["feature", "bug"]
    assert HeedConfig.from_env({"HEED_LIGHT_CATEGORIES": "none"}).light_categories == []


def test_release_into_a_category_sink_that_writes_to_holding_is_refused():
    from heed import release_held

    held: dict = {}
    holding = StoreSink(held)
    report = _report(category=Category.feature)
    held[report.id] = report.model_dump_json()
    sink = CategorySink({"feature": holding}, default=StoreSink({}))
    with pytest.raises(ValueError, match="holding store"):
        release_held(report.id, holding_store=held, sink=sink)
    assert report.id in held


def test_a_duplicate_close_reason_is_a_duplicate_and_a_missing_title_is_blank():
    sink, _ = _board_sink(
        [
            _issue(1, "dup", ["public"], state="closed", state_reason="duplicate"),
            {**_issue(2, "x", ["public"]), "title": None},
        ]
    )
    items = sink.public_items()
    assert items[0].status is Status.duplicate and items[1].title == ""


def test_string_labels_are_one_label_and_light_categories_are_a_list():
    from heed import HeedConfig

    assert CategoryTreatment("bug").labels == ("bug",)
    assert HeedConfig(light_categories=("feature",)).light_categories == ["feature"]
    assert HeedConfig(light_categories=(c for c in ["bug"])).light_categories == ["bug"]


def test_reporter_labels_are_dropped_unless_allow_listed_and_never_reserved():
    gh = FakeGitHub()
    report = _report(labels=["public", "wontfix", "ui", "x"])
    GitHubIssuesSink(gh.create_issue).submit(report)
    assert (
        "ui" not in gh.created[-1]["labels"]
        and "public" not in gh.created[-1]["labels"]
    )
    sink = GitHubIssuesSink(
        gh.create_issue, allowed_report_labels=["ui", "Public", " WontFix"]
    )
    sink.submit(report)
    labels = gh.created[-1]["labels"]
    assert "ui" in labels and "x" not in labels
    assert "public" not in labels and "wontfix" not in labels


def test_issue_urls_and_shas_are_defanged():
    from heed.sinks.github import neutralise_refs

    out = neutralise_refs("see https://github.com/o/r/issues/3 and 0123abc4567def")
    assert "github.com/o/r/issues" not in out and "0123abc4567def" not in out
    assert neutralise_refs("https://example.com/a") == "https://example.com/a"


# ------------------------------------------- follow-ups from the #9 review (heed#52)


def test_board_survives_a_malformed_date_and_null_labels():
    issues = [
        {"number": 1, "title": "bad date", "labels": ["public"], "created_at": "not-a-date"},
        {"number": 2, "title": "null labels", "labels": None},
        {"number": 3, "title": "fine", "labels": ["public"], "created_at": "2026-01-02T03:04:05Z"},
    ]
    sink = GitHubIssuesSink(FakeGitHub().create_issue, list_issues=FakeGitHub(issues).list_issues)
    items = sink.public_items()
    assert [i.ref for i in items] == ["1", "3"]
    assert items[0].created_at is None and items[1].created_at is not None


def test_category_sink_lists_third_party_sinks_that_take_no_category_keyword():
    from heed.sinks import CategorySink

    class Plain:
        def submit(self, report):  # pragma: no cover
            ...

        def public_items(self):
            return [
                PublicItem(ref="1", title="a", category=Category.feature),
                PublicItem(ref="2", title="b", category=Category.bug),
            ]

    sink = CategorySink({"feature": Plain()}, default=StoreSink({}))
    assert [i.ref for i in sink.public_items(category=Category.bug)] == ["2"]
    assert len(sink.public_items()) == 2


def test_items_without_a_link_are_deduped_across_sinks_by_what_they_say():
    from heed.sinks import CategorySink

    item = PublicItem(ref="7", title="same", category=Category.feature)

    class Lists:
        def submit(self, report):  # pragma: no cover
            ...

        def public_items(self, *, category=None):
            return [item]

    sink = CategorySink({"feature": Lists()}, default=Lists())
    assert len(sink.public_items()) == 1


def test_only_a_marked_composite_sink_is_walked_for_leaves():
    from heed.routing import _leaf_sinks
    from heed.sinks import CategorySink

    class Odd:
        def sinks(self):  # an unrelated method that happens to share the name
            return ["not a sink"]

    odd, leaf = Odd(), StoreSink({})
    assert list(_leaf_sinks(odd)) == [odd]
    assert list(_leaf_sinks(CategorySink({"bug": leaf}, default=leaf))) == [leaf]
