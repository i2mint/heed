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
    # the light intake leaves out the environment and the screenshot
    assert "Firefox" not in issue["body"] and "screenshot" not in issue["body"]
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
