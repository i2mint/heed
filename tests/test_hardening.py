"""Tests for public-endpoint hardening of the ingest router (issue #5)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from heed import HeedConfig, ReportSubmission, StoreSink, make_router
from heed.security import (
    InMemoryRateLimiter,
    RedisRateLimiter,
    SiteverifyBotVerifier,
    client_ip,
    make_bot_verifier,
    make_rate_limiter,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
ORIGIN = "https://app.example"


def _client(store=None, attachments=None, **router_kwargs):
    config = router_kwargs.pop("config", None) or HeedConfig(allowed_origins=[ORIGIN])
    app = FastAPI()
    app.include_router(
        make_router(
            StoreSink({} if store is None else store),
            config=config,
            attachment_store=attachments,
            **router_kwargs,
        )
    )
    return TestClient(app)


def _post(client, data=None, files=None, origin=ORIGIN, **kw):
    headers = {"origin": origin} if origin else {}
    return client.post(
        "/report", data=data or {"title": "x"}, files=files, headers=headers, **kw
    )


# --------------------------------------------------------------------------- CORS


def test_cors_headers_echo_allowed_origin():
    resp = _post(_client())
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ORIGIN
    assert "origin" in resp.headers["vary"].lower()


def test_preflight_allowed_and_refused():
    client = _client()
    ok = client.options(
        "/report",
        headers={"origin": ORIGIN, "access-control-request-method": "POST"},
    )
    assert ok.status_code == 204
    assert ok.headers["access-control-allow-origin"] == ORIGIN
    assert "POST" in ok.headers["access-control-allow-methods"]
    bad = client.options("/report", headers={"origin": "https://evil.example"})
    assert bad.status_code == 403
    assert "access-control-allow-origin" not in bad.headers


def test_missing_origin_rejected_when_allowlist_set():
    assert _post(_client(), origin=None).status_code == 403


def test_origin_comparison_is_normalized():
    assert _post(_client(), origin="HTTPS://APP.example").status_code == 200


def test_credentials_without_allowlist_is_a_config_error():
    with pytest.raises(ValueError, match="allowed_origins"):
        HeedConfig(cors_allow_credentials=True)


# ------------------------------------------------------------------- payload caps


def test_body_cap_rejects_large_request():
    cfg = HeedConfig(
        allowed_origins=[ORIGIN], max_body_bytes=2_000, max_screenshot_bytes=1_000
    )
    resp = _post(_client(config=cfg), files={"screenshot": ("s.png", PNG * 100)})
    assert resp.status_code == 413
    assert resp.headers["access-control-allow-origin"] == ORIGIN  # widget can read it


def test_screenshot_cap():
    cfg = HeedConfig(
        allowed_origins=[ORIGIN], max_body_bytes=10_000, max_screenshot_bytes=100
    )
    resp = _post(_client(config=cfg), files={"screenshot": ("s.png", PNG * 10)})
    assert resp.status_code == 413


def test_field_cap():
    cfg = HeedConfig(allowed_origins=[ORIGIN], max_field_bytes=1_000)
    resp = _post(
        _client(config=cfg),
        data={"title": "x", "body": "y" * 5_000},
        files={"screenshot": ("s.png", PNG)},  # force multipart
    )
    assert resp.status_code in (400, 413)


def test_body_cap_must_fit_screenshot():
    with pytest.raises(ValueError, match="max_body_bytes"):
        HeedConfig(max_body_bytes=10, max_screenshot_bytes=100)


def test_non_form_content_type_rejected():
    client = _client()
    resp = client.post("/report", json={"title": "x"}, headers={"origin": ORIGIN})
    assert resp.status_code == 415


# --------------------------------------------------------------- input validation


@pytest.mark.parametrize(
    "data",
    [
        {"title": "   "},
        {"title": "x" * 301},
        {"title": "x", "page_url": "javascript:alert(1)"},
        {"title": "x", "category": "pwn"},
        {"title": "x", "anon_id": "has spaces"},
        {"title": "x", "env": "[1, 2]"},
        {"title": "x", "env": "not json"},
        {"title": "x", "env": "[" * 5_000 + "]" * 5_000},
        {"body": "no title"},
    ],
)
def test_invalid_submissions_rejected(data):
    resp = _post(_client(), data=data)
    assert resp.status_code == 422, resp.text
    assert "javascript" not in resp.text  # errors do not echo the input back


def test_control_chars_stripped():
    sub = ReportSubmission(title="hi\x00‮ there", body="a\x07b\nc")
    assert sub.title == "hi there" and sub.body == "ab\nc"


def test_screenshot_must_be_an_image():
    resp = _post(_client(), files={"screenshot": ("s.png", b"<svg onload=x>")})
    assert resp.status_code == 415


def test_screenshot_stored_with_sniffed_type():
    attachments: dict = {}
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 16
    resp = _post(
        _client(attachments=attachments), files={"screenshot": ("s.png", jpeg)}
    )
    assert resp.status_code == 200
    assert list(attachments) == [f"{resp.json()['id']}.jpg"]


def test_extra_file_field_rejected():
    resp = _post(_client(), files={"other": ("a.png", PNG)})
    assert resp.status_code == 400


# ------------------------------------------------------------------ rate limiting


def test_ip_rate_limit_returns_429_with_retry_after():
    limiter = InMemoryRateLimiter(max_per_window=2)
    client = _client(rate_limiter=limiter)
    codes = [_post(client).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    resp = _post(client)
    assert resp.headers["retry-after"] == "60"
    assert resp.headers["access-control-allow-origin"] == ORIGIN


def test_origin_rate_limit_is_separate():
    client = _client(origin_rate_limiter=InMemoryRateLimiter(max_per_window=1))
    assert [_post(client).status_code for _ in range(2)] == [200, 429]


def test_in_memory_limiter_window_and_key_bound():
    now = [0.0]
    lim = InMemoryRateLimiter(
        max_per_window=1, window_seconds=10, max_keys=2, clock=lambda: now[0]
    )
    assert lim.allow("a") and not lim.allow("a")
    now[0] = 10.0
    assert lim.allow("a")
    lim.allow("b")
    lim.allow("c")
    assert len(lim._hits) == 2  # bounded


class _FakePipe:
    def __init__(self, store, fail):
        self.store, self.fail, self.ops = store, fail, []

    def incr(self, key):
        self.ops.append(key)

    def expire(self, key, seconds):
        pass

    def execute(self):
        if self.fail:
            raise ConnectionError("redis down")
        key = self.ops[0]
        self.store[key] = self.store.get(key, 0) + 1
        return [self.store[key], True]


class _FakeRedis:
    def __init__(self, fail=False):
        self.store: dict = {}
        self.fail = fail

    def pipeline(self):
        return _FakePipe(self.store, self.fail)


def test_redis_limiter_counts_per_window_shared_across_instances():
    redis = _FakeRedis()
    a = RedisRateLimiter(redis, max_per_window=2, clock=lambda: 0.0)
    b = RedisRateLimiter(redis, max_per_window=2, clock=lambda: 0.0)
    assert a.allow("k") and b.allow("k") and not a.allow("k")  # one shared count
    later = RedisRateLimiter(redis, max_per_window=2, clock=lambda: 61.0)
    assert later.allow("k")  # new window


def test_redis_limiter_falls_back_when_redis_down():
    lim = RedisRateLimiter(
        _FakeRedis(fail=True),
        max_per_window=1,
        fallback=InMemoryRateLimiter(max_per_window=1),
    )
    assert lim.allow("k") and not lim.allow("k")


def test_make_rate_limiter_registry():
    assert isinstance(make_rate_limiter(max_per_window=3), InMemoryRateLimiter)
    with pytest.raises(ValueError, match="redis_url"):
        make_rate_limiter("redis", max_per_window=3)
    with pytest.raises(ValueError, match="Known"):
        make_rate_limiter("nope", max_per_window=3)


def test_client_ip_trusts_forwarded_for_only_from_proxies():
    assert client_ip("127.0.0.1", "1.1.1.1", trusted_proxies=["127.0.0.1"]) == (
        "1.1.1.1"
    )
    assert client_ip("8.8.8.8", "1.1.1.1", trusted_proxies=["127.0.0.1"]) == "8.8.8.8"
    # spoofed left-most entry is ignored; the proxy-appended address wins
    assert (
        client_ip("127.0.0.1", "9.9.9.9, 2.2.2.2", trusted_proxies=["127.0.0.1"])
        == "2.2.2.2"
    )


# ----------------------------------------------------------------- bot gate & trap


def test_bot_gate_off_without_secret():
    assert make_bot_verifier(None, None) is None
    assert make_bot_verifier("hcaptcha", "") is None
    with pytest.raises(ValueError, match="BOT_SECRET"):
        HeedConfig(bot_gate="turnstile")


def test_bot_gate_on_with_secret_and_provider_url():
    v = make_bot_verifier("hcaptcha", "s3cret")
    assert "hcaptcha" in v.verify_url and "s3cret" not in repr(v)
    assert "cloudflare" in make_bot_verifier(None, "s3cret").verify_url


def _verifier(answer=None, exc=None, **kw):
    calls = []

    def post(url, data, timeout):
        calls.append(data)
        if exc:
            raise exc
        return answer

    return SiteverifyBotVerifier("sec", http_post=post, **kw), calls


def test_siteverify_success_failure_and_fail_closed():
    ok, calls = _verifier({"success": True})
    assert ok.verify("tok", remote_ip="1.2.3.4")
    assert calls[0] == {"secret": "sec", "response": "tok", "remoteip": "1.2.3.4"}
    assert not ok.verify(None) and not ok.verify("")
    assert not _verifier({"success": False})[0].verify("tok")
    assert not _verifier({"success": "true"})[0].verify("tok")
    assert not _verifier(exc=OSError("timeout"))[0].verify("tok")
    pinned, _ = _verifier(
        {"success": True, "hostname": "evil.example"},
        expected_hostnames=["app.example"],
    )
    assert not pinned.verify("tok")


def test_router_enforces_bot_gate():
    verifier, _ = _verifier({"success": True})
    store: dict = {}
    client = _client(store=store, bot_verifier=verifier)
    assert _post(client, data={"title": "x"}).status_code == 403
    assert _post(client, data={"title": "x", "bot_token": "t"}).status_code == 200
    assert len(store) == 1


def test_honeypot_fakes_success_and_drops():
    store: dict = {}
    resp = _post(_client(store=store), data={"title": "x", "hp_website": "spam.biz"})
    assert resp.status_code == 200 and resp.json()["ok"]
    assert store == {}


def test_config_from_env():
    cfg = HeedConfig.from_env(
        {
            "HEED_BOT_SECRET": "sekrit-value",
            "HEED_RATE_LIMIT_BACKEND": "redis",
            "HEED_REDIS_URL": "redis://r:6379/0",
            "HEED_TRUSTED_PROXIES": "127.0.0.1,::1",
            "HEED_CORS_ALLOW_CREDENTIALS": "false",
        }
    )
    assert cfg.bot_gate_enabled and cfg.trusted_proxies == ["127.0.0.1", "::1"]
    assert "sekrit" not in repr(cfg) and "redis://" not in repr(cfg)


# ------------------------------------------------- adversarial-review regressions


def test_gate_runs_before_user_dependency():
    """Origin + IP limit reject before user_dependency (an auth lookup) runs."""
    calls = []

    async def user_dep():
        calls.append(1)
        return None

    client = _client(
        user_dependency=user_dep, rate_limiter=InMemoryRateLimiter(max_per_window=1)
    )
    assert _post(client, origin="https://evil.example").status_code == 403
    assert _post(client).status_code == 200
    assert _post(client).status_code == 429
    assert calls == [1]


def test_screenshot_as_text_field_is_422_not_500():
    resp = _post(_client(), data={"title": "x", "screenshot": "abc"})
    assert resp.status_code == 422


def test_bad_trusted_proxy_fails_at_config_time():
    with pytest.raises(ValueError, match="trusted_proxies"):
        HeedConfig(trusted_proxies=["not-a-cidr"])


def test_null_origin_never_echoed():
    client = _client(config=HeedConfig())
    resp = _post(client, origin="null")
    assert "access-control-allow-origin" not in resp.headers


def test_honeypot_hits_do_not_spend_origin_budget():
    client = _client(origin_rate_limiter=InMemoryRateLimiter(max_per_window=1))
    for _ in range(3):
        _post(client, data={"title": "x", "hp_website": "spam"})
    assert _post(client).status_code == 200


def test_ipv6_clients_share_a_bucket_per_64():
    from heed.security import rate_limit_key

    assert rate_limit_key("2001:db8:1:2::1") == rate_limit_key("2001:db8:1:2:ffff::9")
    assert rate_limit_key("2001:db8:1:2::1") != rate_limit_key("2001:db8:1:3::1")
    assert rate_limit_key("::ffff:1.2.3.4") == "1.2.3.4"


def test_validation_errors_truncate_client_chosen_keys():
    import json

    env = json.dumps({"extra": {"k" * 500: "v" * 600}})
    resp = _post(_client(), data={"title": "x", "env": env})
    assert resp.status_code == 422
    assert "k" * 100 not in resp.text and "v" * 100 not in resp.text


def test_expected_hostnames_reach_the_verifier():
    v = make_bot_verifier(None, "s", expected_hostnames=["app.example"])
    assert v.expected_hostnames == {"app.example"}


def test_redis_from_url_sets_socket_timeouts(monkeypatch):
    redis = pytest.importorskip("redis")
    seen = {}

    def fake_from_url(url, **kw):
        seen.update(kw)
        return _FakeRedis()

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(fake_from_url))
    RedisRateLimiter.from_url("redis://x:6379/0")
    assert seen["socket_timeout"] == seen["socket_connect_timeout"] == 0.5
