"""A sink that persists reports into a Mapping (the default, dependency-free sink)."""

from __future__ import annotations

from collections.abc import MutableMapping

from heed.base import Report
from heed.sinks.base import BaseSink, DupCandidate, SinkResult


class StoreSink(BaseSink):
    """Persist each report as JSON into a dol-style store keyed by report id.

    The zero-configuration default when no external sink (e.g. GitHub) is configured.
    ``store`` is any ``MutableMapping[str, str]`` (a plain dict, a ``dol`` file store,
    S3, …) — dependency-injected so the sink stays testable.
    """

    def __init__(self, store: MutableMapping[str, str]):
        self.store = store

    def submit(self, report: Report) -> SinkResult:
        self.store[report.id] = report.model_dump_json()
        return SinkResult(ok=True, external_ref=report.id)

    def find_duplicates(self, report: Report) -> list[DupCandidate]:
        """Cheap title-equality duplicate scan over stored reports."""
        candidates: list[DupCandidate] = []
        needle = report.title.strip().lower()
        for key, raw in self.store.items():
            if key == report.id:
                continue
            other = Report.model_validate_json(raw)
            if other.title.strip().lower() == needle:
                candidates.append(
                    DupCandidate(external_ref=key, score=1.0, title=other.title)
                )
        return candidates
