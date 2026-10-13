"""The attachment route: signed URLs, a dedicated host, safe headers (issue #19)."""

import pytest
from fastapi.testclient import TestClient

from heed import (
    AttachmentSigner,
    HeedConfig,
    ReportSubmission,
    StoreSink,
    make_attachment_app,
    process_submission,
    screenshot_url_resolver,
)
from heed.images import _tiny_png

KEY = "k" * 32
REF = "0123456789abcdef0123456789abcdef.png"
HOST = "files.example.com"


class _Recording(dict):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.reads = []

    def __getitem__(self, k):
        self.reads.append(k)
        return super().__getitem__(k)


def _client(store=None, **kwargs):
    store = {REF: _tiny_png()} if store is None else store
    kwargs.setdefault("signer", AttachmentSigner(KEY))
    kwargs.setdefault("allowed_hosts", [HOST])
    return TestClient(make_attachment_app(store, **kwargs), base_url=f"http://{HOST}")


def test_a_signed_url_serves_the_image_with_attachment_headers():
    signer = AttachmentSigner(KEY)
    r = _client(signer=signer).get(signer.url("/attachments", REF))
    assert r.status_code == 200 and r.content == _tiny_png()
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert r.headers["cross-origin-resource-policy"] == "same-site"
    assert r.headers["cache-control"] == "private, no-store"


def test_cross_origin_is_opt_in():
    signer = AttachmentSigner(KEY)
    r = _client(signer=signer, cross_origin=True).get(signer.url("/attachments", REF))
    assert r.headers["cross-origin-resource-policy"] == "cross-origin"


@pytest.mark.parametrize(
    "query",
    ["", "?sig=", "?sig=AAAA", "?sig=" + AttachmentSigner("x" * 32).sign(REF)["sig"]],
    ids=["none", "empty", "garbage", "other-key"],
)
def test_without_a_valid_signature_it_is_a_bare_404(query):
    r = _client().get(f"/attachments/{REF}{query}")
    assert r.status_code == 404 and r.content == b""


def test_a_signature_is_bound_to_its_ref():
    signer = AttachmentSigner(KEY)
    other = "f" * 32 + ".png"
    store = {REF: _tiny_png(), other: _tiny_png()}
    sig = signer.sign(REF)["sig"]
    assert _client(store, signer=signer).get(f"/attachments/{other}?sig={sig}").status_code == 404


def test_expiry_and_key_rotation():
    now = [1000.0]
    old = AttachmentSigner("o" * 32, ttl_seconds=60, clock=lambda: now[0])
    new = AttachmentSigner(KEY, previous_keys=["o" * 32], ttl_seconds=60, clock=lambda: now[0])
    client = _client(signer=new)
    assert client.get(old.url("/attachments", REF)).status_code == 200  # rotated key
    url = new.url("/attachments", REF)
    now[0] += 61
    assert client.get(url).status_code == 404  # expired
    # An expiring signer refuses a non-expiring signature, even with the right key.
    sig = AttachmentSigner(KEY).sign(REF)["sig"]
    assert client.get(f"/attachments/{REF}?sig={sig}").status_code == 404


def test_another_host_is_refused():
    signer = AttachmentSigner(KEY)
    store = {REF: _tiny_png()}
    app = make_attachment_app(store, signer=signer, allowed_hosts=[HOST])
    r = TestClient(app, base_url="http://app.example.com").get(signer.url("/attachments", REF))
    assert r.status_code == 421


@pytest.mark.parametrize(
    "ref",
    ["..%2F..%2Fetc%2Fpasswd", "0123456789ABCDEF0123456789ABCDEF.png", REF + ".html", "x.png"],
)
def test_refs_heed_did_not_write_never_reach_the_store(ref):
    store = _Recording({REF: _tiny_png()})
    signer = AttachmentSigner(KEY)
    r = _client(store, signer=signer).get(f"/attachments/{ref}?sig={signer.sign(ref)['sig']}")
    assert r.status_code == 404 and store.reads == []


def test_bytes_that_are_not_the_named_image_are_an_opaque_download():
    signer = AttachmentSigner(KEY)
    r = _client({REF: b"<html><script>alert(1)</script>"}, signer=signer).get(
        signer.url("/attachments", REF)
    )
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment")


def test_a_signer_is_required_unless_unsigned_is_explicit():
    with pytest.raises(ValueError, match="ATTACHMENT_SIGNING_KEY"):
        make_attachment_app({}, allowed_hosts=[HOST])
    client = _client(signer=None, allow_unsigned=True)
    assert client.get(f"/attachments/{REF}").status_code == 200


def test_config_supplies_signer_and_hosts():
    cfg = HeedConfig.from_env(
        {
            "HEED_ATTACHMENT_SIGNING_KEY": KEY,
            "HEED_ATTACHMENT_HOSTS": HOST,
            "HEED_ATTACHMENT_URL_TTL_SECONDS": "60",
        }
    )
    assert KEY not in repr(cfg)
    signer = AttachmentSigner.from_config(cfg)
    assert signer.ttl_seconds == 60 and KEY not in repr(signer)
    client = TestClient(make_attachment_app({REF: _tiny_png()}, config=cfg), base_url=f"http://{HOST}")
    assert client.get(signer.url("/attachments", REF)).status_code == 200


def test_short_keys_are_refused():
    with pytest.raises(ValueError, match="at least 32 bytes"):
        AttachmentSigner("short")


def test_the_resolver_links_a_submitted_screenshot():
    attachments, reports = {}, {}
    report, _ = process_submission(
        ReportSubmission(title="t"),
        StoreSink(reports),
        screenshot=_tiny_png(),
        attachment_store=attachments,
    )
    signer = AttachmentSigner(KEY)
    url = screenshot_url_resolver(f"http://{HOST}/attachments", signer)(report)
    assert url.startswith(f"http://{HOST}/attachments/{report.id}.png?sig=")
    assert _client(attachments, signer=signer).get(url).content == _tiny_png()
    assert screenshot_url_resolver("http://x", signer)(report.model_copy(update={"attachments": []})) is None


# ---------------------------------------------------------------------------
# Adversarial-review regressions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query", ["?sig=%C3%A9", "?sig=x&exp=%C2%B2", "?sig=x&exp=" + "9" * 5000]
)
def test_malformed_signatures_are_a_404_not_a_500(query):
    signer = AttachmentSigner(KEY, ttl_seconds=60)
    assert _client(signer=signer).get(f"/attachments/{REF}{query}").status_code == 404


def test_a_trailing_slash_is_not_redirected_to_the_requested_host():
    signer = AttachmentSigner(KEY)
    url = signer.url("/attachments", REF).replace(".png?", ".png/?")
    r = _client(signer=signer).get(url, headers={"host": "evil.com"}, follow_redirects=False)
    assert r.status_code in (404, 421) and "location" not in r.headers


def test_a_string_of_hosts_is_refused():
    with pytest.raises(TypeError, match="list of hostnames"):
        make_attachment_app({}, signer=AttachmentSigner(KEY), allowed_hosts=HOST)


@pytest.mark.parametrize("method,path", [("post", f"/attachments/{REF}"), ("get", "/nope")])
def test_other_methods_and_paths_get_a_bare_status(method, path):
    r = getattr(_client(), method)(path)
    assert r.status_code in (404, 405) and r.content == b""
    assert r.headers["cache-control"] == "no-store"


def test_the_resolver_skips_refs_the_route_would_not_serve():
    from heed import Attachment, Report, Identity

    report = Report(
        title="t",
        identity=Identity(anon_id="a"),
        attachments=[Attachment(ref="../x.png", size=1, media_type="image/png")],
    )
    assert screenshot_url_resolver("http://x", AttachmentSigner(KEY))(report) is None
