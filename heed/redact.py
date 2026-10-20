"""Redaction of personal data and secrets in captured console and network entries.

Opt-in capture (heed#7) sends console messages and request URLs, and both routinely
carry things nobody meant to report: an e-mail address in a log line, a session token
in a query string, a bearer header someone logged while debugging. The widget masks
them before anything leaves the page (redact *before* capture); the server applies
the same rules again here, because the ingest endpoint is public and a client can send
anything. The rules are deliberately greedy: a lost hex string costs a little
debugging, a leaked token costs an incident.

The widget's ``mask.ts`` mirrors these rules; keep the two in step.

>>> redact_text("login failed for ana@example.com with token=abc123")
'login failed for [email] with token=[redacted]'
>>> redact_url("https://u:pw@app.example/a/b?session=xyz&page=2#top")
'https://app.example/a/b?session=[redacted]&page=[redacted]'
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from heed.base import LogEntry, NetEntry

Redactor = Callable[[str], str]

REDACTED = "[redacted]"

_SECRET_KEYS = (
    r"password|passwd|pwd|pass|secret|token|api[_-]?key|apikey|access[_-]?token"
    r"|refresh[_-]?token|id[_-]?token|auth|authorization|session|sessionid|sid"
    r"|cookie|set-cookie|csrf|xsrf|otp|pin|ssn|credit[_-]?card|card[_-]?number|cvv|cvc"
)

# (pattern, replacement), applied in order. Earlier rules see the raw text, so the
# specific shapes (a JWT, a provider key) are caught before the generic ones.
_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # JSON Web Tokens
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"), "[jwt]"),
    # Authorization schemes followed by a credential
    (
        re.compile(r"(?i)\b(bearer|basic|digest|token)(\s+)[A-Za-z0-9._~+/=-]{6,}"),
        rf"\1\2{REDACTED}",
    ),
    # key=value / key: value / "key": "value" with a sensitive key
    (
        re.compile(
            rf"(?i)([\"']?\b(?:{_SECRET_KEYS})\b[\"']?\s*[:=]\s*[\"']?)([^\s\"'&,;}}]+)"
        ),
        rf"\1{REDACTED}",
    ),
    # Well-known provider key shapes
    (
        re.compile(
            r"\b(?:(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{8,}|gh[pousr]_[A-Za-z0-9]{20,}"
            r"|github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}"
            r"|AIza[0-9A-Za-z_-]{30,})"
        ),
        "[key]",
    ),
    # E-mail addresses
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[email]"),
    # Long runs of letters and digits (ids, hashes, keys): at least one of each
    (
        re.compile(
            r"\b(?=[A-Za-z0-9_-]*[0-9])(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{32,}\b"
        ),
        "[id]",
    ),
    # IPv4 addresses
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[ip]"),
    # International phone numbers
    (re.compile(r"\+\d[\d ().-]{6,}\d"), "[phone]"),
)

_CARD_CANDIDATE = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _mask_card(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group())
    return "[card]" if _luhn_ok(digits) else match.group()


def redact_text(text: str) -> str:
    """Replace personal data and secrets in ``text`` by placeholders.

    >>> redact_text("Authorization: Bearer abc.def.ghi")
    'Authorization: [redacted] [redacted]'
    >>> redact_text("paid with 4111 1111 1111 1111, order 1234567890123")
    'paid with [card], order 1234567890123'
    >>> redact_text('{"password": "hunter2", "user": "ana"}')
    '{"password": "[redacted]", "user": "ana"}'
    """
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return _CARD_CANDIDATE.sub(_mask_card, text)


def redact_url(url: str, *, redactor: Redactor = redact_text) -> str:
    """``url`` without credentials, fragment or query values (keys are kept).

    A string that is not an absolute URL is redacted as text.

    >>> redact_url("/api/users/ana@example.com?q=1")
    '/api/users/[email]?q=[redacted]'
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return redactor(url)
    host = parts.hostname or ""
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    netloc = f"{host}:{port}" if port else host
    query = "&".join(
        f"{redactor(key)}={REDACTED}"
        for key, _ in parse_qsl(parts.query, keep_blank_values=True)
    )
    return urlunsplit((parts.scheme, netloc, redactor(parts.path), query, ""))


def redact_console(
    entries: Iterable[LogEntry], *, redactor: Redactor = redact_text
) -> list[LogEntry]:
    """Console entries with their message and source redacted."""
    return [
        e.model_copy(
            update={
                "message": redactor(e.message),
                "source": redactor(e.source) if e.source else e.source,
            }
        )
        for e in entries
    ]


def redact_network(
    entries: Iterable[NetEntry], *, redactor: Redactor = redact_text
) -> list[NetEntry]:
    """Network entries with their URL stripped of credentials and query values."""
    return [
        e.model_copy(update={"url": redact_url(e.url, redactor=redactor)})
        for e in entries
    ]
