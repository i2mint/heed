"""Serve the embeddable widget, and state the contract it is built against.

The widget (TypeScript sources under ``widget/`` at the repository root) is built into
``heed/static/widget/`` and shipped inside the Python package, so the backend that
receives reports can also serve the script that sends them: one host, no CDN, no npm
install for the embedding site.

Two things live here:

* :func:`make_widget_router` serves the built files. Only the names listed in the
  build's ``manifest.json`` are served (no path ever reaches the filesystem), with
  headers that let any site load them (``Access-Control-Allow-Origin: *`` and
  ``Cross-Origin-Resource-Policy: cross-origin``, needed for Subresource Integrity and
  for pages with COEP) and long-lived caching for content-hashed names.
* :func:`widget_contract` is the single source of truth for what the widget sends:
  the form field names the ingest route reads and the limits ``base.py`` enforces.
  ``widget/src/contract.gen.ts`` is generated from it (``python -m heed.widget
  contract``) and a test fails when the two drift.

>>> c = widget_contract()
>>> c["fields"]["title"], c["limits"]["title_chars"]
('title', 300)
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Optional, Union

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from heed import base
from heed.config import HeedConfig

WIDGET_ASSET_DIR = files("heed") / "static" / "widget"
MANIFEST_NAME = "manifest.json"
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
MUTABLE_CACHE_CONTROL = "public, max-age=300"
_MEDIA_TYPES = {".js": "text/javascript; charset=utf-8", ".map": "application/json"}
_ASSET_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Access-Control-Allow-Origin": "*",
    "Cross-Origin-Resource-Policy": "cross-origin",
}

AssetDir = Union[str, Path, Any]  # a path or an importlib Traversable


def widget_contract(config: Optional[HeedConfig] = None) -> dict[str, Any]:
    """What the widget sends and the limits it must respect (from heed's SSOT).

    ``config`` supplies the server-configurable names (honeypot, bot token field) and
    the screenshot cap; their defaults are what the widget assumes unless an embedder
    overrides them.

    >>> widget_contract()["categories"]
    ['bug', 'feature', 'question', 'other']
    """
    cfg = config or HeedConfig()
    return {
        "fields": {
            "title": "title",
            "body": "body",
            "category": "category",
            "page_url": "page_url",
            "env": "env",
            "anon_id": "anon_id",
            "console": "console",
            "network": "network",
            "screenshot": "screenshot",
            "bot_token": cfg.bot_token_field,
            "honeypot": cfg.honeypot_field,
        },
        "categories": [c.value for c in base.Category],
        "limits": {
            "title_chars": base.MAX_TITLE_CHARS,
            "body_chars": base.MAX_BODY_CHARS,
            "url_chars": base.MAX_URL_CHARS,
            "short_chars": base.MAX_SHORT_CHARS,
            "extra_items": base.MAX_EXTRA_ITEMS,
            "extra_key_chars": base.MAX_EXTRA_KEY_CHARS,
            "anon_id_chars": base.MAX_ANON_ID_CHARS,
            "log_entries": base.MAX_LOG_ENTRIES,
            "log_message_chars": base.MAX_LOG_MESSAGE_CHARS,
            "field_bytes": cfg.max_field_bytes,
            "screenshot_bytes": cfg.max_screenshot_bytes,
            "screenshot_pixels": cfg.max_screenshot_pixels,
        },
        "patterns": {"anon_id": base.ANON_ID_PATTERN},
        "statuses": {"received": base.Status.received.value, "held": "held"},
    }


def render_contract_ts(contract: Optional[Mapping[str, Any]] = None) -> str:
    """The TypeScript module ``widget/src/contract.gen.ts`` (generated, not edited).

    >>> render_contract_ts().splitlines()[0].startswith("// Generated")
    True
    """
    body = json.dumps(contract or widget_contract(), indent=2)
    return (
        "// Generated from heed's Pydantic models by "
        "`python -m heed.widget contract`.\n"
        "// Do not edit: change heed/base.py or heed/config.py and regenerate.\n"
        f"export const CONTRACT = {body} as const;\n"
    )


def load_widget_manifest(asset_dir: Optional[AssetDir] = None) -> dict[str, Any]:
    """The build's manifest: ``{"version", "entries", "files": {name: {...}}}``.

    Returns an empty file list when the widget has not been built (a source checkout
    that never ran ``npm run build``), so the backend still starts.
    """
    path = _as_traversable(asset_dir) / MANIFEST_NAME
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": None, "entries": {}, "files": {}}


def make_widget_router(*, asset_dir: Optional[AssetDir] = None) -> APIRouter:
    """A router serving the built widget at ``GET /widget/{name}``.

    Include it beside :func:`heed.make_router` (``make_app`` does) so the widget is
    served from the same host that receives its reports. ``asset_dir`` defaults to the
    files shipped in the package; point it elsewhere to serve another build.
    """
    root = _as_traversable(asset_dir)
    manifest = load_widget_manifest(root)

    @lru_cache(maxsize=None)
    def _read(name: str) -> bytes:
        return (root / name).read_bytes()

    router = APIRouter()

    @router.get("/widget/{name}", include_in_schema=False)
    def widget_asset(name: str) -> Response:
        meta = manifest["files"].get(name)
        if meta is None:
            raise HTTPException(status_code=404, headers={"Cache-Control": "no-store"})
        cache = (
            IMMUTABLE_CACHE_CONTROL if meta.get("immutable") else MUTABLE_CACHE_CONTROL
        )
        media_type = _MEDIA_TYPES.get(Path(name).suffix, "application/octet-stream")
        return Response(
            _read(name),
            media_type=media_type,
            headers={**_ASSET_HEADERS, "Cache-Control": cache},
        )

    return router


def _as_traversable(asset_dir: Optional[AssetDir]):
    if asset_dir is None:
        return WIDGET_ASSET_DIR
    return Path(asset_dir) if isinstance(asset_dir, str) else asset_dir


def main(argv: Optional[list[str]] = None) -> None:
    """``python -m heed.widget contract``: print the generated TypeScript contract."""
    args = sys.argv[1:] if argv is None else argv
    if args != ["contract"]:
        raise SystemExit("usage: python -m heed.widget contract")
    sys.stdout.write(render_contract_ts())


if __name__ == "__main__":
    main()
