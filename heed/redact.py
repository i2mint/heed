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

import math
import re
from collections import Counter
from collections.abc import Callable, Iterable
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from heed.base import LogEntry, NetEntry

Redactor = Callable[[str], str]

REDACTED = "[redacted]"

# Sensitive keys in "key=value" / "key: value" / "key": "value". The long words match
# inside a longer key (client_secret, x_auth_token, AWS_SECRET_ACCESS_KEY); the short
# ones only on their own (so "spinner" is not a pin).
_LONG_KEYS = (
    r"secret|token|passw|pwd|api[_-]?key|apikey|auth|session|cookie|csrf|xsrf"
    r"|credential|private[_-]?key|card[_-]?number|credit[_-]?card"
)
_SHORT_KEYS = r"pass|pin|sid|otp|ssn|cvv|cvc|sig|signature"
_W = "A-Za-z0-9_-"  # an identifier-ish run
_HEX = "0-9A-Fa-f"

# Every rule starts at the beginning of a run (a lookbehind on the run's own
# characters, or a literal prefix), so no rule rescans a run from each of its
# positions: matching stays linear in the length of hostile input.


_SENSITIVE_KEY = re.compile(rf"(?i)[{_W}]*(?:{_LONG_KEYS})[{_W}]*|{_SHORT_KEYS}")


def _secret_value(replacement: str) -> Callable[[re.Match[str]], str]:
    """When group 1 is a sensitive key, replace the value (group 3), keeping its quotes.

    The key is checked here rather than in the pattern: a pattern with the key words
    inside a run backtracks quadratically on a long run of them.
    """

    def sub(m: re.Match[str]) -> str:
        key, sep, value = m.group(1), m.group(2), m.group(3)
        if not _SENSITIVE_KEY.fullmatch(key):
            return m.group()
        quote = value[0] if value[:1] in ("'", '"') else ""
        return f"{key}{sep}{quote}{replacement}{quote}"

    return sub


def _if_mixed(replacement: str) -> Callable[[re.Match[str]], str]:
    """Replace only a run holding both a letter and a digit (a key, not a word)."""

    def sub(m: re.Match[str]) -> str:
        run = m.group()
        mixed = any(c.isdigit() for c in run) and any(c.isalpha() for c in run)
        return replacement if mixed else run

    return sub


_Replacement = str | Callable[[re.Match[str]], str]

# (pattern, replacement), applied in order: the specific shapes (an Authorization
# header, a JWT, a provider key) before the generic ones.
_RULES: tuple[tuple[re.Pattern[str], _Replacement], ...] = (
    # The whole value of an Authorization header: scheme and credential
    (
        re.compile(r"(?i)(\b(?:proxy-)?authorization[\"']?\s*[:=]\s*)([\"']?)[^\r\n\"']*"),
        rf"\1\2{REDACTED}",
    ),
    # JSON Web Tokens
    (re.compile(rf"\beyJ[{_W}]{{5,}}\.[{_W}]{{5,}}\.[{_W}]*"), "[jwt]"),
    # Well-known provider key shapes
    (
        re.compile(
            r"\b(?:(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{8,}|gh[pousr]_[A-Za-z0-9]{8,}"
            r"|github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}"
            rf"|AIza[{_W}]{{30,}}|sk-[{_W}]{{16,}})"
        ),
        "[key]",
    ),
    # Authorization schemes followed by a credential
    (
        re.compile(r"(?i)\b(bearer|basic|digest|token)(\s+)(?!\[)[A-Za-z0-9._~+/=-]{3,}"),
        rf"\1\2{REDACTED}",
    ),
    # key=value / key: value / "key": "value" with a sensitive key
    (
        re.compile(
            rf"(?<![{_W}])([{_W}]+)([\"']?\s*[:=]\s*)"
            r"(\"[^\"\n]*\"|'[^'\n]*'|[^\s\"'&,;}{\[]+)"
        ),
        _secret_value(REDACTED),
    ),
    # E-mail addresses
    (
        re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
        "[email]",
    ),
    # 40-character base64 secrets (AWS secret access keys and the like)
    (
        re.compile(r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+=])"),
        _if_mixed("[key]"),
    ),
    # Long runs of letters and digits (ids, hashes, keys)
    (re.compile(rf"(?<![{_W}])[{_W}]{{32,}}"), _if_mixed("[id]")),
    # IPv6 (full, or compressed with "::" between groups) and IPv4 addresses
    (
        re.compile(
            rf"(?<![{_HEX}:])(?:(?:[{_HEX}]{{1,4}}:){{7}}[{_HEX}]{{1,4}}"
            rf"|(?:[{_HEX}]{{1,4}}:){{1,6}}(?::[{_HEX}]{{1,4}}){{1,6}})(?![{_HEX}:])"
        ),
        "[ip]",
    ),
    (re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"), "[ip]"),
    # US social security numbers, then phone numbers (North American and international)
    (re.compile(r"(?<![\w-])\d{3}-\d{2}-\d{4}(?![\w-])"), "[ssn]"),
    (re.compile(r"(?<![\w(])\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\w)"), "[phone]"),
    (re.compile(r"\+\d[\d ().-]{6,}\d"), "[phone]"),
    # The user name in home-directory paths
    (re.compile(r"(/home/|/Users/|\b[A-Za-z]:\\Users\\)[^/\\\s\"']+"), r"\1[user]"),
)

_URL_IN_TEXT = re.compile(r"\bhttps?://[^\s\"'<>`]+")
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
    'Authorization: [redacted]'
    >>> redact_text("paid with 4111 1111 1111 1111, order 1234567890123")
    'paid with [card], order 1234567890123'
    >>> redact_text('{"password": "hunter2", "user": "ana"}')
    '{"password": "[redacted]", "user": "ana"}'

    URLs in the text are masked like request URLs (:func:`redact_url`) first:

    >>> redact_text("callback https://app.example/cb?code=4/0AbC&state=x failed")
    'callback https://app.example/cb?code=[redacted]&state=[redacted] failed'
    """
    text = _URL_IN_TEXT.sub(lambda m: redact_url(m.group()), text)
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return _CARD_CANDIDATE.sub(_mask_card, text)


_ID_SEGMENT = re.compile(r"\d+|(?=[A-Za-z_.~+=-]*\d)[A-Za-z0-9_.~+=-]{12,}")
_TOKEN_CHARS = re.compile(r"[A-Za-z0-9_.~+=-]+")
_OPAQUE_SCHEMES = frozenset({"mailto", "tel", "sms", "data", "javascript", "blob"})
_MIN_LETTER_TOKEN_LEN = 16  # all-letter tokens shorter than this read as words
_MIN_TOKEN_ENTROPY = 3.5  # bits per character; slugs and words fall below


def _entropy(text: str) -> float:
    counts = Counter(text)
    return -sum(c / len(text) * math.log2(c / len(text)) for c in counts.values())


def _looks_like_token(segment: str) -> bool:
    """Whether ``segment`` (percent-decoded) is an id or an opaque token.

    >>> [_looks_like_token(s) for s in ("42", "AbCdEfGhIjKlMnOpQrSt", "eyJh.eyJz.sig9",
    ...                                 "settings", "terms-and-conditions-page")]
    [True, True, True, False, False]
    """
    if _ID_SEGMENT.fullmatch(segment):
        return True
    return (
        len(segment) >= _MIN_LETTER_TOKEN_LEN
        and _TOKEN_CHARS.fullmatch(segment) is not None
        and segment != segment.lower()
        and _entropy(segment) >= _MIN_TOKEN_ENTROPY
    )


def _redact_segment(segment: str, redactor: Redactor) -> str:
    segment = segment.split(";", 1)[0]  # path parameters (;jsessionid=...)
    decoded = unquote(segment)
    if _looks_like_token(decoded) or _looks_like_token(decoded.replace("/", "")):
        return "[id]"
    if segment.startswith("@"):
        return "[user]"
    return redactor(segment)


def redact_url(url: str, *, redactor: Redactor = redact_text) -> str:
    """``url`` without credentials, fragment or query values (keys are kept).

    Path segments that look like ids (all digits, or 12+ characters with a digit) or
    handles (``@name``) are replaced, path parameters dropped, and the rest of each
    segment redacted as text.

    >>> redact_url("/api/users/ana@example.com?q=1")
    '/api/users/[email]?q=[redacted]'
    >>> redact_url("https://app.example/reset/9f8e7d6c5b4a3f2e;jsessionid=X/42")
    'https://app.example/reset/[id]/[id]'
    >>> redact_url("https://app.example/r/AbCdEfGhIjKlMnOpQr%2Fx/eyJhbGci.eyJzdWIi.sigpart")
    'https://app.example/r/[id]/[id]'
    >>> redact_url("mailto:ana@example.com")
    'mailto:[redacted]'
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return redactor(url)
    if parts.scheme.lower() in _OPAQUE_SCHEMES:  # payload is not a path
        return f"{parts.scheme.lower()}:{REDACTED}"
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
    path = "/".join(_redact_segment(seg, redactor) for seg in parts.path.split("/"))
    return urlunsplit((parts.scheme, netloc, path, query, ""))


def redact_console(
    entries: Iterable[LogEntry], *, redactor: Redactor = redact_text
) -> list[LogEntry]:
    """Console entries with their level, message and source redacted."""
    return [
        e.model_copy(
            update={
                "level": redactor(e.level),
                "message": redactor(e.message),
                "source": redactor(e.source) if e.source else e.source,
            }
        )
        for e in entries
    ]


def redact_network(
    entries: Iterable[NetEntry], *, redactor: Redactor = redact_text
) -> list[NetEntry]:
    """Network entries with their method redacted and their URL stripped of
    credentials, query values and id-like path segments."""
    return [
        e.model_copy(
            update={
                "method": redactor(e.method),
                "url": redact_url(e.url, redactor=redactor),
            }
        )
        for e in entries
    ]
