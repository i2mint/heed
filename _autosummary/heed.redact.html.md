# heed.redact

Redaction of personal data and secrets in captured console and network entries.

Opt-in capture (heed#7) sends console messages and request URLs, and both routinely
carry things nobody meant to report: an e-mail address in a log line, a session token
in a query string, a bearer header someone logged while debugging. The widget masks
them before anything leaves the page (redact *before* capture); the server applies
the same rules again here, because the ingest endpoint is public and a client can send
anything. The rules are deliberately greedy: a lost hex string costs a little
debugging, a leaked token costs an incident.

The widget’s `mask.ts` mirrors these rules; keep the two in step.

```pycon
>>> redact_text("login failed for ana@example.com with token=abc123")
'login failed for [email] with token=[redacted]'
>>> redact_url("https://u:pw@app.example/a/b?session=xyz&page=2#top")
'https://app.example/a/b?session=[redacted]&page=[redacted]'
```

### Functions

| [`redact_console`](#heed.redact.redact_console)(entries, \*[, redactor])   | Console entries with their level, message and source redacted.                                                            |
|--------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------|
| [`redact_network`](#heed.redact.redact_network)(entries, \*[, redactor])   | Network entries with their method redacted and their URL stripped of credentials, query values and id-like path segments. |
| [`redact_text`](#heed.redact.redact_text)(text)                         | Replace personal data and secrets in `text` by placeholders.                                                              |
| [`redact_url`](#heed.redact.redact_url)(url, \*[, redactor])           | `url` without credentials, fragment or query values (keys are kept).                                                      |

### heed.redact.redact_console(entries, \*, redactor=<function redact_text>)

Console entries with their level, message and source redacted.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`LogEntry`](heed.base.html.md#heed.base.LogEntry)]

### heed.redact.redact_network(entries, \*, redactor=<function redact_text>)

Network entries with their method redacted and their URL stripped of
credentials, query values and id-like path segments.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`NetEntry`](heed.base.html.md#heed.base.NetEntry)]

### heed.redact.redact_text(text)

Replace personal data and secrets in `text` by placeholders.

```pycon
>>> redact_text("Authorization: Bearer abc.def.ghi")
'Authorization: [redacted]'
>>> redact_text("paid with 4111 1111 1111 1111, order 1234567890123")
'paid with [card], order 1234567890123'
>>> redact_text('{"password": "hunter2", "user": "ana"}')
'{"password": "[redacted]", "user": "ana"}'
```

URLs in the text are masked like request URLs ([`redact_url()`](#heed.redact.redact_url)) first:

```pycon
>>> redact_text("callback https://app.example/cb?code=4/0AbC&state=x failed")
'callback https://app.example/cb?code=[redacted]&state=[redacted] failed'
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### heed.redact.redact_url(url, \*, redactor=<function redact_text>)

`url` without credentials, fragment or query values (keys are kept).

Path segments that look like ids (all digits, or 12+ characters with a digit) or
handles (`@name`) are replaced, path parameters dropped, and the rest of each
segment redacted as text.

```pycon
>>> redact_url("/api/users/ana@example.com?q=1")
'/api/users/[email]?q=[redacted]'
>>> redact_url("https://app.example/reset/9f8e7d6c5b4a3f2e;jsessionid=X/42")
'https://app.example/reset/[id]/[id]'
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)
