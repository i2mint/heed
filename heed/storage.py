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
#: Attribute a holding store made by `make_holding_store` carries: the atomic move
#: `heed.routing.release_held` claims its reports with (issue #24).
CLAIM_MOVE_ATTR = "heed_claim_move"

#: ``move(holding_store, report_id, claims_store, claim_key)``: take the report out of
#: holding and put it under ``claim_key`` in the claims store, as one atomic step. Of
#: several concurrent moves of one report exactly one succeeds; the others raise
#: ``KeyError`` and change nothing. A crash leaves the report in at least one of the
#: two (`heed.routing.recover_claimed` handles a report in both). A release also puts a
#: report back with it, claims to holding: ``move(claims_store, claim_key,
#: holding_store, report_id)``.
ClaimMove = Callable[[MutableMapping, str, MutableMapping, str], None]


def pop_claim_move(
    holding_store: MutableMapping,
    report_id: str,
    claims: MutableMapping,
    claim_key: str,
) -> None:
    """The atomic claim move for in-process stores: ``dict.pop`` is atomic, so of two
    threads releasing one report only one gets it.

    >>> held, claims = {"r1": "{}"}, {}
    >>> pop_claim_move(held, "r1", claims, "r1--token")
    >>> held, claims
    ({}, {'r1--token': '{}'})
    >>> pop_claim_move(held, "r1", claims, "r1--other")
    Traceback (most recent call last):
      ...
    KeyError: 'r1'
    """
    raw = holding_store.pop(report_id)
    try:
        claims[claim_key] = raw
    except BaseException:
        holding_store[report_id] = raw  # never leave it in neither
        raise


def rename_claim_move(
    holding_store: MutableMapping,
    report_id: str,
    claims: MutableMapping,
    claim_key: str,
) -> None:
    """The atomic claim move for file stores (``dol`` ``Files``/``TextFiles``) on one
    filesystem: ``os.rename``, so of two processes releasing one report only one gets
    it. Raises ``FileNotFoundError`` if the claims directory is missing, and refuses
    (``ValueError``) a store whose keys do not resolve to absolute paths under its
    ``rootdir`` (e.g. one wrapped by a key codec) or a held report that is a symlink.
    On Windows a rename of a file open elsewhere raises ``PermissionError``; the report
    stays held.
    """
    src = _file_path(holding_store, report_id)
    dst = _file_path(claims, claim_key)
    if os.path.islink(src):
        raise ValueError(f"held report {report_id!r} is a symlink: not moving it")
    try:
        os.rename(src, dst)  # claim keys are unique, so dst never exists
    except FileNotFoundError:
        if not os.path.isdir(os.path.dirname(dst)):  # the claims directory is missing
            raise
        raise KeyError(report_id) from None


def _file_path(store, key: str) -> str:
    """The absolute path of ``key`` in a dol file store, or ``ValueError`` if ``store``
    does not map keys to paths under its own ``rootdir``. A relative ``rootdir`` is
    resolved against the working directory, as the store itself resolves it."""
    rootdir = getattr(store, "rootdir", None)
    path = store._id_of_key(key) if rootdir else None
    if not isinstance(path, str) or not isinstance(rootdir, str):
        path = None
    else:
        path, rootdir = os.path.abspath(path), os.path.abspath(rootdir)
    if path is None or os.path.commonpath([rootdir, path]) != rootdir:
        raise ValueError(
            f"{type(store).__name__} does not map keys to files under its rootdir: "
            "rename_claim_move needs the store make_report_store(rootdir) returns"
        )
    return path


def carried_claim_move(holding_store) -> ClaimMove | None:
    """The claim move ``holding_store`` itself carries (set by `make_holding_store`),
    or None. Only the store's own attribute counts: a ``dol`` wrapper forwards
    attribute lookups to the store it wraps, but its keys are not that store's keys.
    """
    return getattr(holding_store, "__dict__", {}).get(CLAIM_MOVE_ATTR)


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


def _stays_under(path: str, rootdir: str) -> bool:
    """Whether ``path``, with every symlink resolved, is inside ``rootdir``."""
    root = os.path.realpath(rootdir)
    return os.path.commonpath([root, os.path.realpath(path)]) == root


def _confined_files(rootdir: str, **kwargs) -> MutableMapping:
    from dol import Files

    class ConfinedFiles(Files):
        """Files that treat a key whose file resolves, through symlinks, to outside
        ``rootdir`` as missing -- to read, write, delete, test and list -- so a
        misconfigured root cannot make heed touch other files."""

        def _id_of_key(self, k):
            path = super()._id_of_key(k)
            if not _stays_under(path, self.rootdir):
                raise KeyError(k)
            return path

        def __contains__(self, k):
            try:
                return super().__contains__(k)
            except KeyError:
                return False

        def __iter__(self):
            for k in super().__iter__():
                if _stays_under(os.path.join(self.rootdir, k), self.rootdir):
                    yield k

    return ConfinedFiles(rootdir, **kwargs)


def make_attachment_store(
    rootdir: str | None = None,
    *,
    delete_func: Callable[[str], None] = os.remove,
    follow_symlinks: bool = False,
) -> MutableMapping:
    """A bytes store for attachments. In-memory unless ``rootdir`` is given.

    Deletion is permanent by default, for the reason given in `make_report_store`.
    Reading a key whose file is a symlink leading outside ``rootdir`` raises
    ``KeyError``, so a misconfigured root cannot make the attachment route serve other
    files; ``follow_symlinks=True`` opts out.

    >>> make_attachment_store()
    {}
    """
    if rootdir is None:
        return {}
    if follow_symlinks:
        from dol import Files

        return Files(rootdir, delete_func=delete_func)
    return _confined_files(rootdir, delete_func=delete_func)


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
    holding store's recursive listing). It also carries the atomic move a release claims
    with (``heed_claim_move``: ``os.rename`` on disk, ``dict.pop`` in memory), so two
    releases of one report at the same moment cannot both send it (issue #24).

    >>> held = make_holding_store()
    >>> held["r1"] = "{}"
    >>> list(held), dict(held.heed_claims)
    (['r1'], {})
    """
    if rootdir is None:
        store, claims, move = _HoldingDict(), {}, pop_claim_move
    else:
        rootdir = os.path.expanduser(os.fspath(rootdir))
        rootdir = os.path.join(os.path.abspath(rootdir), "")  # immune to a chdir
        claims_dir = os.path.normpath(rootdir) + ".claims"
        os.makedirs(rootdir, exist_ok=True)
        os.makedirs(claims_dir, exist_ok=True)
        store = make_report_store(rootdir, delete_func=delete_func)
        claims = make_report_store(claims_dir + os.sep, delete_func=delete_func)
        move = rename_claim_move
    setattr(store, CLAIMS_ATTR, claims)
    setattr(store, CLAIM_MOVE_ATTR, move)
    return store
