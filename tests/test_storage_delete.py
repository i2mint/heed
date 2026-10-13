"""File-backed stores delete permanently, never to the OS trash (issue #20)."""

import pytest

from heed import make_attachment_store, make_report_store


@pytest.fixture
def trash_calls(monkeypatch):
    """Record any use of dol's trash-based delete (which would leave a PII copy)."""
    import dol.trash

    calls = []
    monkeypatch.setattr(dol.trash, "default_delete_func", calls.append)
    return calls


@pytest.mark.parametrize(
    "make, value",
    [(make_report_store, "{}"), (make_attachment_store, b"\x89PNG")],
)
def test_delete_is_permanent_by_default(tmp_path, trash_calls, make, value):
    store = make(str(tmp_path))
    store["k"] = value
    del store["k"]
    assert list(tmp_path.iterdir()) == []
    assert trash_calls == []


def test_delete_func_is_configurable(tmp_path):
    seen = []
    store = make_report_store(str(tmp_path), delete_func=seen.append)
    store["k"] = "{}"
    del store["k"]
    assert seen == [str(tmp_path / "k")]
