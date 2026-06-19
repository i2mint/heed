"""Command-line entry point for ``heed`` (design-phase placeholder).

The full CLI arrives with the backend. For now this reports the version and points
at the documents and the GitHub project where the plan lives.
"""

from heed import __version__


def main():
    """Print heed status and where to find the plan."""
    print(f"heed {__version__} — embeddable end-user feedback (design phase)")
    print("Plan & docs: https://github.com/i2mint/heed  |  see misc/docs/")


if __name__ == "__main__":
    main()
