"""Storage helpers — dol-backed stores for reports and binary attachments.

Stores are plain ``MutableMapping``s, so the rest of heed never depends on a specific
backend. Defaults are in-memory; pass a directory to persist to the filesystem, or
inject any ``dol`` store (S3, Mongo, …).
"""

import os
from collections.abc import Callable, MutableMapping

#: Attribute a holding store made by `make_holding_store` carries: the store its
#: reports are claimed in while `heed.routing.release_held` sends them.
CLAIMS_ATTR = "heed_claims"


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


class _HoldingDict(dict):
    """In-memory holding store carrying its own claims store (a dict takes no attrs)."""


def make_holding_store(
    rootdir: str | None = None, *, delete_func: Callable[[str], None] = os.remove
) -> MutableMapping:
    """A report store for held reports, with a dedicated claims store beside it.

    ``release_held`` and ``recover_claimed`` find the claims store on the holding store
    (``heed_claims``), so a release lists only the reports being released right now,
    not every held report (issue #21), and nothing listing holding (a moderation view,
    ``StoreSink.find_duplicates``) ever sees a claim. With ``rootdir``, claims live in a
    sibling directory, ``<rootdir>.claims`` (a subdirectory would show up in the
    holding store's recursive listing).

    >>> held = make_holding_store()
    >>> held["r1"] = "{}"
    >>> list(held), dict(held.heed_claims)
    (['r1'], {})
    """
    if rootdir is None:
        store, claims = _HoldingDict(), {}
    else:
        claims_dir = os.path.normpath(rootdir) + ".claims"
        os.makedirs(rootdir, exist_ok=True)
        os.makedirs(claims_dir, exist_ok=True)
        store = make_report_store(rootdir, delete_func=delete_func)
        claims = make_report_store(claims_dir + os.sep, delete_func=delete_func)
    setattr(store, CLAIMS_ATTR, claims)
    return store
