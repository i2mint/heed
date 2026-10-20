"""Masking of personal data and secrets in opt-in console/network capture."""

import pytest

from heed.redact import redact_text, redact_url


@pytest.mark.parametrize(
    "text, leaked",
    [
        ("mail me at ana.b+x@example.co.uk", "ana.b+x@example.co.uk"),
        ("Authorization: Bearer abcdef123456", "abcdef123456"),
        ('{"api_key": "k-1234"}', "k-1234"),
        ("password=hunter2&next=/", "hunter2"),
        ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig_nature", "eyJhbGciOiJIUzI1NiJ9"),
        ("key sk_live_abcdefghij1234", "sk_live_abcdefghij1234"),
        ("token ghp_" + "a" * 36, "ghp_" + "a" * 36),
        ("card 4111-1111-1111-1111 ok", "4111-1111-1111-1111"),
        ("client 192.168.1.20 failed", "192.168.1.20"),
        ("call +33 6 12 34 56 78", "6 12 34 56 78"),
        ("id 3f2a9c1e8b7d6a5f4e3d2c1b0a9f8e7d", "3f2a9c1e8b7d6a5f4e3d2c1b0a9f8e7d"),
    ],
)
def test_redact_text_removes(text, leaked):
    assert leaked not in redact_text(text)


@pytest.mark.parametrize(
    "text",
    ["TypeError: x is undefined", "GET /api/items -> 500", "took 1234 ms", "v1.2.3"],
)
def test_redact_text_keeps_ordinary_text(text):
    assert redact_text(text) == text


def test_redact_url_strips_credentials_fragment_and_query_values():
    out = redact_url("https://bob:pw@api.example:8443/u/ana@example.com?q=x&page=2#t")
    assert out == "https://api.example:8443/u/[email]?q=[redacted]&page=[redacted]"


def test_redact_url_keeps_relative_and_ipv6():
    assert redact_url("/a?b=1") == "/a?b=[redacted]"
    assert redact_url("http://[::1]:8000/x") == "http://[::1]:8000/x"


@pytest.mark.parametrize(
    "text, leaked",
    [
        ("client_secret=abc", "abc"),
        ("AWS_SECRET_ACCESS_KEY: xyz", "xyz"),
        ('password="two words"', "words"),
        ("Authorization: Bearer abc", "abc"),
        ("token abc", "abc"),
        ("key sk-proj-abcdefghijklmnop", "abcdefghijklmnop"),
        ("aws wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "wJalrXUtnFEMI"),
        ("call 415-555-1234", "555-1234"),
        ("from 2001:db8::1", "2001:db8::1"),
        ("ssn 123-45-6789", "45-6789"),
        ("at C:\\Users\\ana\\app.js", "ana"),
        ("at /home/ana/app.js", "ana"),
    ],
)
def test_redact_text_removes_more(text, leaked):
    assert leaked not in redact_text(text)


@pytest.mark.parametrize(
    "text", ["12:30:45", "std::vector", "/api/users/me", "2024-10-20"]
)
def test_redact_text_keeps_lookalikes(text):
    assert redact_text(text) == text


def test_redact_url_masks_id_like_path_segments():
    assert redact_url("/u/12345/@ana/abcdef0123456789;jsessionid=Z/x") == (
        "/u/[id]/[user]/[id]/x"
    )


@pytest.mark.parametrize(
    "hostile",
    [
        "1-" * 1000,
        "a" * 2000,
        "a@" * 1000,
        "1:" * 1000,
        "secret" * 333,
        "secret" * 333 + "=x",
    ],
)
def test_redact_text_is_linear_on_hostile_input(hostile):
    import time

    start = time.perf_counter()
    for _ in range(50):
        redact_text(hostile)
    assert time.perf_counter() - start < 1.0


def test_redact_text_masks_urls_and_nested_values():
    out = redact_text("cb https://app.example/cb?code=4/0AbC&sig=abc12 then")
    assert out == "cb https://app.example/cb?code=[redacted]&sig=[redacted] then"
    nested = redact_text('{"auth": {"token": "SECRETVALUE123"}, "sig": "x"}')
    assert "SECRETVALUE123" not in nested and '"sig": "[redacted]"' in nested
