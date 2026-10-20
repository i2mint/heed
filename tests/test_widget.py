"""The widget's contract with the backend, and the route that serves its files."""

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from heed import HeedConfig, StoreSink, make_app, make_widget_router, widget_contract
from heed.base import MAX_BODY_CHARS
from heed.config import HeedConfig as _Cfg
from heed.ingest import _TEXT_FIELDS
from heed.widget import (
    IMMUTABLE_CACHE_CONTROL,
    load_widget_manifest,
    render_contract_ts,
)

REPO = Path(__file__).resolve().parent.parent
CONTRACT_TS = REPO / "widget" / "src" / "contract.gen.ts"


def test_generated_contract_is_fresh():
    """widget/src/contract.gen.ts must be regenerated when base.py/config.py change."""
    assert CONTRACT_TS.read_text(encoding="utf-8") == render_contract_ts(), (
        "regenerate: python -m heed.widget contract > widget/src/contract.gen.ts, "
        "then rebuild the widget (cd widget && npm run build)"
    )


def test_contract_names_every_field_the_ingest_route_reads():
    fields = widget_contract()["fields"]
    cfg = _Cfg()
    assert set(_TEXT_FIELDS) <= set(fields.values())
    assert fields["bot_token"] == cfg.bot_token_field
    assert fields["honeypot"] == cfg.honeypot_field
    assert fields["screenshot"] == "screenshot"


def test_contract_follows_server_config():
    cfg = HeedConfig(bot_token_field="cf", honeypot_field=None)
    fields = widget_contract(cfg)["fields"]
    assert (fields["bot_token"], fields["honeypot"]) == ("cf", None)


def test_built_widget_is_shipped_with_its_manifest():
    manifest = load_widget_manifest()
    name = manifest["entries"]["widget"]
    meta = manifest["files"][name]
    assert meta["integrity"].startswith("sha384-")
    assert meta["gzip_bytes"] <= 13_420  # the budgets live in widget/build.mjs
    stable = manifest["files"]["heed-widget.js"]
    assert stable["immutable"] is False and "integrity" not in stable
    loader = manifest["entries"]["loader"]
    assert manifest["files"][loader]["gzip_bytes"] <= 1_500
    assert manifest["files"]["heed.js"]["immutable"] is False


def test_bundle_pins_the_rasterizer_it_loads_lazily():
    manifest = load_widget_manifest()
    raster = manifest["entries"]["raster"]
    meta = manifest["files"][raster]
    assert meta["gzip_bytes"] <= 14_000 and "heed-raster.js" not in manifest["files"]
    root = Path(__file__).resolve().parent.parent / "heed" / "static" / "widget"
    for bundle in (manifest["entries"]["widget"], "heed-widget.js"):
        code = (root / bundle).read_text()
        assert raster in code and meta["integrity"] in code
        assert "modern-screenshot" not in code  # the rasterizer stays out of the bundle
    # modern-screenshot's MIT licence travels inside the file served to browsers.
    assert "Includes modern-screenshot" in (root / raster).read_text()[:2000]


def test_bundle_pins_the_translations_it_loads_lazily():
    manifest = load_widget_manifest()
    locales = manifest["entries"]["locales"]
    meta = manifest["files"][locales]
    assert meta["gzip_bytes"] <= 5_000 and "heed-locales.js" not in manifest["files"]
    root = Path(__file__).resolve().parent.parent / "heed" / "static" / "widget"
    for bundle in (manifest["entries"]["widget"], "heed-widget.js"):
        code = (root / bundle).read_text()
        assert locales in code and meta["integrity"] in code
        assert "Envoyer" not in code  # the translations stay out of the bundle


def test_loader_pins_the_bundle_it_loads():
    """The loader embeds the bundle's hashed name and SRI hash (an SRI chain)."""
    manifest = load_widget_manifest()
    widget = manifest["entries"]["widget"]
    root = Path(__file__).resolve().parent.parent / "heed" / "static" / "widget"
    for loader in (manifest["entries"]["loader"], "heed.js"):
        code = (root / loader).read_text()
        assert widget in code
        assert manifest["files"][widget]["integrity"] in code


def _client(**kwargs) -> TestClient:
    app = FastAPI()
    app.include_router(make_widget_router(**kwargs), prefix="/heed")
    return TestClient(app)


def test_serves_manifest_files_with_cross_origin_headers():
    name = load_widget_manifest()["entries"]["widget"]
    r = _client().get(f"/heed/widget/{name}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    assert r.headers["access-control-allow-origin"] == "*"
    assert r.headers["cross-origin-resource-policy"] == "cross-origin"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == IMMUTABLE_CACHE_CONTROL
    assert b"heed-feedback" in r.content
    stable = _client().get("/heed/widget/heed-widget.js")
    assert stable.content == r.content
    assert stable.headers["cache-control"] == "public, max-age=300"


def test_serves_nothing_outside_the_manifest(tmp_path):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"files": {"a.js": {"immutable": False}}})
    )
    (tmp_path / "a.js").write_text("1")
    (tmp_path / "secret.js").write_text("2")
    client = _client(asset_dir=tmp_path)
    assert (
        client.get("/heed/widget/a.js")
        .headers["cache-control"]
        .startswith("public, max-age=")
    )
    for path in ("secret.js", "manifest.json", "..%2Fmanifest.json", "%2e%2e/x"):
        assert client.get(f"/heed/widget/{path}").status_code == 404


def test_unbuilt_widget_dir_serves_404_rather_than_failing(tmp_path):
    assert _client(asset_dir=tmp_path).get("/heed/widget/x.js").status_code == 404


def test_make_app_serves_the_widget_beside_the_ingest_route():
    name = load_widget_manifest()["entries"]["widget"]
    app = make_app(
        StoreSink({}), config=HeedConfig(allowed_origins=["https://a.example"])
    )
    assert TestClient(app).get(f"/heed/widget/{name}").status_code == 200
    bare = make_app(StoreSink({}), serve_widget=False)
    assert TestClient(bare).get(f"/heed/widget/{name}").status_code == 404


def test_cors_exposes_retry_after_to_the_widget():
    app = make_app(
        StoreSink({}),
        config=HeedConfig(
            allowed_origins=["https://a.example"], rate_limit_per_minute=1
        ),
    )
    client = TestClient(app)
    post = dict(
        headers={"Origin": "https://a.example"},
        files={"title": (None, "t")},
    )
    assert client.post("/heed/report", **post).status_code == 200
    r = client.post("/heed/report", **post)
    assert r.status_code == 429
    assert "Retry-After" in r.headers["access-control-expose-headers"]


def test_browser_line_breaks_count_once_against_the_body_limit():
    """Browsers send a textarea's line breaks as CRLF; a body at the limit must pass."""
    app = make_app(StoreSink(store := {}), config=HeedConfig(allowed_origins=None))
    body = ("ab\r\n" * (MAX_BODY_CHARS // 3))[: MAX_BODY_CHARS + MAX_BODY_CHARS // 3]
    assert len(body.replace("\r\n", "\n")) <= MAX_BODY_CHARS < len(body)
    r = TestClient(app).post(
        "/heed/report", files={"title": (None, "t"), "body": (None, body)}
    )
    assert r.status_code == 200, r.text
    (saved,) = [json.loads(v) for v in store.values()]
    assert "\r" not in saved["body"]
