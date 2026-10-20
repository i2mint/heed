"""Command-line entry point for ``heed``.

``heed review ...`` publishes media review pages and reads their comment threads back
(see :mod:`heed.review`); ``heed`` alone reports the version and where the plan lives.
"""

import sys

from heed import __version__


def main(argv=None):
    """Dispatch ``heed <command>``; with no command, print heed's status."""
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "review":
        from heed.review import main as review_main

        return review_main(argv[1:])
    print(f"heed {__version__} — embeddable end-user feedback")
    print("Commands: heed review {publish,comments,ls,delete-comment,page-shell,serve}")
    print("Plan & docs: https://github.com/i2mint/heed  |  see misc/docs/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
