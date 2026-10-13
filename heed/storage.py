"""Storage helpers — dol-backed stores for reports and binary attachments.

Stores are plain ``MutableMapping``s, so the rest of heed never depends on a specific
backend. Defaults are in-memory; pass a directory to persist to the filesystem, or
inject any ``dol`` store (S3, Mongo, …).
"""

import os
from collections.abc import Callable, MutableMapping


def make_report_store(
    rootdir: str | None = None, *, delete_func: Callable[[str], None] = os.remove
) -> MutableMapping:
    """A text store for report JSON. In-memory unless ``rootdir`` is given.

    Deleting a key removes the file for good (``os.remove``). dol's own default moves it
    to the OS trash, which would keep a copy of every deleted, claimed or released
    report -- and reports can carry personal data. Pass ``delete_func`` to opt back in.
    """
    if rootdir is None:
        return {}
    from dol import TextFiles

    return TextFiles(rootdir, delete_func=delete_func)


def make_attachment_store(
    rootdir: str | None = None, *, delete_func: Callable[[str], None] = os.remove
) -> MutableMapping:
    """A bytes store for attachments. In-memory unless ``rootdir`` is given.

    Deletion is permanent by default, for the reason given in `make_report_store`.
    """
    if rootdir is None:
        return {}
    from dol import Files

    return Files(rootdir, delete_func=delete_func)
