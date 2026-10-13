"""The widget's contract with the backend, and the route that serves its files."""

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from heed import HeedConfig, StoreSink, make_app, make_widget_router, widget_contract
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
    assert meta["gzip_bytes"] <= 8_000


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
