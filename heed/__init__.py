"""heed — embeddable, no-install, framework-agnostic end-user feedback.

``heed`` lets any visitor of a deployed web app report a bug or request a feature,
with useful context gathered automatically, routed to a pluggable backend (GitHub
Issues first). It works standalone and integrates with ``enlace`` as an optional
add-on, without depending on it.

This package is in its **design phase**. The competitive landscape, the design
rationale, and the roadmap live in ``misc/docs/`` and in the project's GitHub
issues and discussions. The public API will grow from here; for now this module
exposes only the version.

See:
    - ``misc/docs/research-report.md`` — the deep-research landscape.
    - ``misc/docs/design.md`` — architecture, the data model, and the sink interface.
"""

__version__ = "0.0.1"

__all__ = ["__version__"]
