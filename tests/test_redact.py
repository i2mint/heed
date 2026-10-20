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
