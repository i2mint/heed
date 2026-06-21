"""Storage helpers — dol-backed stores for reports and binary attachments.

Stores are plain ``MutableMapping``s, so the rest of heed never depends on a specific
backend. Defaults are in-memory; pass a directory to persist to the filesystem, or
inject any ``dol`` store (S3, Mongo, …).
"""

from collections.abc import MutableMapping


def make_report_store(rootdir: str | None = None) -> MutableMapping:
    """A text store for report JSON. In-memory unless ``rootdir`` is given."""
    if rootdir is None:
        return {}
    from dol import TextFiles

    return TextFiles(rootdir)


def make_attachment_store(rootdir: str | None = None) -> MutableMapping:
    """A bytes store for attachments. In-memory unless ``rootdir`` is given."""
    if rootdir is None:
        return {}
    from dol import Files

    return Files(rootdir)
