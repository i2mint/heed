"""Smoke tests for the heed package skeleton."""

import heed


def test_import_and_version():
    """heed imports cleanly and exposes a non-empty string version."""
    assert isinstance(heed.__version__, str)
    assert heed.__version__
