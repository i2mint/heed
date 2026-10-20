"""The feedback queue as plain functions: list, get, triage, dedup, create, close (#8).

One set of core functions over a report store (the ``MutableMapping`` a
:class:`~heed.sinks.store.StoreSink` writes, e.g.
:func:`heed.storage.make_report_store`),
shared by every surface: :mod:`heed.mcp` exposes them as MCP tools, and a CLI or an
admin page would call the same ones. Arguments are flat and results are JSON-ready
dicts, so a tool layer adds nothing but transport.

``create`` goes through :func:`heed.ingest.process_submission`, the same path as a
widget submission (validation, enrichment), so an agent cannot write a report the
HTTP router would not.

>>> q = FeedbackQueue({})
>>> rid = q.create("Export to CSV fails", body="the button errors")["id"]
>>> [r["title"] for r in q.list()]
['Export to CSV fails']
>>> q.triage(rid)["severity"]
'high'
>>> q.close(rid, status="completed")["status"]
'completed'
"""

from __future__ import annotations

from collections.abc import Callable, MutableMapping
from typing import Any

from heed.base import Category, Report, ReportSubmission, Status
from heed.dedup import Confirm, find_duplicates, mark_duplicate, stored_reports
from heed.triage import LLM, triage

#: Statuses a report can be closed with (``duplicate`` needs ``duplicate_of``).
CLOSING_STATUSES = (Status.completed, Status.declined, Status.duplicate)
DEFAULT_LIST_LIMIT = 50


def _summary(report: Report) -> dict[str, Any]:
    return {
        "id": report.id,
        "title": report.title,
        "category": report.category.value,
        "status": report.status.value,
        "labels": list(report.labels),
        "created_at": report.created_at.isoformat(),
    }


class FeedbackQueue:
    """The queue's core operations over a report store.

    :param store: ``report id -> report JSON`` (a StoreSink's store).
    :param llm: for :meth:`triage` (``prompt -> text``); keyword rules without it.
    :param confirm: the dedup judge for :meth:`duplicates` (optional).
    """

    def __init__(
        self,
        store: MutableMapping[str, str],
        *,
        llm: LLM | None = None,
        confirm: Confirm | None = None,
    ):
        self.store, self.llm, self.confirm = store, llm, confirm

    def _load(self, report_id: str) -> Report:
        try:
            return Report.model_validate_json(self.store[report_id])
        except KeyError:
            raise KeyError(f"no report {report_id!r}") from None

    def _save(self, report: Report) -> None:
        self.store[report.id] = report.model_dump_json()

    # -- the operations --------------------------------------------------------------

    def list(
        self,
        *,
        status: str | None = None,
        category: str | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> list[dict[str, Any]]:
        """Reports, newest first, optionally of one ``status`` and ``category``."""
        wanted_status = Status(status) if status else None
        wanted_category = Category(category) if category else None
        reports = [
            r
            for r in stored_reports(self.store)
            if (wanted_status is None or r.status is wanted_status)
            and (wanted_category is None or r.category is wanted_category)
        ]
        reports.sort(key=lambda r: r.created_at, reverse=True)
        return [_summary(r) for r in reports[: max(limit, 0)]]

    def get(self, report_id: str) -> dict[str, Any]:
        """One report in full (JSON-ready)."""
        return self._load(report_id).model_dump(mode="json")

    def triage(self, report_id: str, *, apply: bool = False) -> dict[str, Any]:
        """A triage proposal; with ``apply``, its category and labels are recorded and
        the status becomes ``triaged`` (a report already closed is left as it is)."""
        report = self._load(report_id)
        proposal = triage(report, llm=self.llm)
        if apply and report.status in (Status.received, Status.triaged):
            report = report.model_copy(
                update={
                    "category": proposal.category,
                    "labels": list(dict.fromkeys([*report.labels, *proposal.labels])),
                    "status": Status.triaged,
                    "extra": {**report.extra, "severity": proposal.severity.value},
                }
            )
            self._save(report)
        return {**proposal.model_dump(mode="json"), "applied": apply}

    def duplicates(self, report_id: str, *, k: int = 5) -> list[dict[str, Any]]:
        """Likely duplicates of a stored report (nothing is marked)."""
        report = self._load(report_id)
        dups = find_duplicates(
            report, stored_reports(self.store), k=k, confirm=self.confirm
        )
        return [d.model_dump(mode="json") for d in dups]

    def create(
        self, title: str, *, body: str = "", category: str = "bug"
    ) -> dict[str, Any]:
        """File a report through the widget's own submission path."""
        from heed.ingest import process_submission
        from heed.sinks.store import StoreSink

        submission = ReportSubmission(title=title, body=body, category=category)
        report, _ = process_submission(submission, StoreSink(self.store))
        return _summary(report)

    def close(
        self,
        report_id: str,
        *,
        status: str = "completed",
        duplicate_of: str | None = None,
    ) -> dict[str, Any]:
        """Close a report: ``completed``, ``declined`` or ``duplicate`` (of another)."""
        closing = Status(status)
        if closing not in CLOSING_STATUSES:
            raise ValueError(f"close with one of {[s.value for s in CLOSING_STATUSES]}")
        if closing is Status.duplicate:
            if not duplicate_of:
                raise ValueError("closing as duplicate needs duplicate_of")
            return _summary(mark_duplicate(self.store, report_id, of=duplicate_of))
        report = self._load(report_id).model_copy(update={"status": closing})
        self._save(report)
        return _summary(report)

    def tools(self) -> list[Callable[..., Any]]:
        """The operations as named functions (the MCP tools, one per operation)."""

        def list_reports(
            status: str | None = None, category: str | None = None, limit: int = 50
        ) -> list[dict]:
            """List feedback reports, newest first (filter by status and category)."""
            return self.list(status=status, category=category, limit=limit)

        def get_report(report_id: str) -> dict:
            """Get one feedback report in full."""
            return self.get(report_id)

        def triage_report(report_id: str, apply: bool = False) -> dict:
            """Propose category, labels, severity, summary; apply=True records it."""
            return self.triage(report_id, apply=apply)

        def find_duplicate_reports(report_id: str, k: int = 5) -> list[dict]:
            """Find likely duplicates of a report (nothing is marked)."""
            return self.duplicates(report_id, k=k)

        def create_report(title: str, body: str = "", category: str = "bug") -> dict:
            """File a new feedback report."""
            return self.create(title, body=body, category=category)

        def close_report(
            report_id: str, status: str = "completed", duplicate_of: str | None = None
        ) -> dict:
            """Close a report: completed, declined, or duplicate (with duplicate_of)."""
            return self.close(report_id, status=status, duplicate_of=duplicate_of)

        return [
            list_reports,
            get_report,
            triage_report,
            find_duplicate_reports,
            create_report,
            close_report,
        ]
