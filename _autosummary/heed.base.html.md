# heed.base

Domain model for heed — the single source of truth shared by widget and backend.

Everything that crosses the widget↔backend boundary, or moves between the ingest
layer and a sink, is one of the Pydantic models defined here. The widget POSTs a
[`ReportSubmission`](#heed.base.ReportSubmission) (what an untrusted client may assert); the backend enriches
it into a [`Report`](#heed.base.Report) (server-assigned id, timestamp, resolved identity, validated
origin, status) before handing it to a sink.

### Functions

| [`new_report_id`](#heed.base.new_report_id)()           | Return a fresh opaque report id (uuid4 hex).                            |
|----------------------------------------------------------------------------|-------------------------------------------------------------------------|
| [`normalize_newlines`](#heed.base.normalize_newlines)(text)  | Turn CRLF and lone CR into LF, so a line break counts as one character. |
| [`strip_control_chars`](#heed.base.strip_control_chars)(text) | Remove control characters (keeping tab/newline/CR) from untrusted text. |
| [`utcnow`](#heed.base.utcnow)()                  | Return the current UTC time (an injectable clock seam for tests).       |

### Classes

| [`Attachment`](#heed.base.Attachment)(\*\*data)       | A stored binary artifact (e.g. a screenshot), referenced by store key.       |
|-----------------------------------------------------------------------------|------------------------------------------------------------------------------|
| [`Category`](#heed.base.Category)(\*values)         | What kind of feedback a report is.                                           |
| [`Environment`](#heed.base.Environment)(\*\*data)      | Client environment captured by default (no PII beyond the user agent).       |
| [`Identity`](#heed.base.Identity)(\*\*data)         | Who submitted the report — anonymous by default.                             |
| [`LogEntry`](#heed.base.LogEntry)(\*\*data)         | One captured console entry (opt-in capture only).                            |
| [`NetEntry`](#heed.base.NetEntry)(\*\*data)         | One captured network entry (opt-in; metadata only, never bodies).            |
| [`Report`](#heed.base.Report)(\*\*data)           | A server-enriched report — the unit a sink receives.                         |
| [`ReportSubmission`](#heed.base.ReportSubmission)(\*\*data) | The (untrusted) payload a widget POSTs; server fields are assigned later.    |
| [`Status`](#heed.base.Status)(\*values)           | Lifecycle of a report (flattened; see misc/docs/design.md for the full map). |

### *class* heed.base.Attachment(\*\*data)

Bases: `BaseModel`

A stored binary artifact (e.g. a screenshot), referenced by store key.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.base.Category(\*values)

Bases: [`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Enum`](https://docs.python.org/3/library/enum.html#enum.Enum)

What kind of feedback a report is.

### *class* heed.base.Environment(\*\*data)

Bases: `BaseModel`

Client environment captured by default (no PII beyond the user agent).

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.base.Identity(\*\*data)

Bases: `BaseModel`

Who submitted the report — anonymous by default.

`anon_id` is an opaque random id (the default). `user` is set only when an
authenticated session is present (e.g. via enlace_auth). The client IP is NEVER
stored here — it is used transiently for rate limiting only.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.base.LogEntry(\*\*data)

Bases: `BaseModel`

One captured console entry (opt-in capture only).

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.base.NetEntry(\*\*data)

Bases: `BaseModel`

One captured network entry (opt-in; metadata only, never bodies).

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.base.Report(\*\*data)

Bases: `BaseModel`

A server-enriched report — the unit a sink receives.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.base.ReportSubmission(\*\*data)

Bases: `BaseModel`

The (untrusted) payload a widget POSTs; server fields are assigned later.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.base.Status(\*values)

Bases: [`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Enum`](https://docs.python.org/3/library/enum.html#enum.Enum)

Lifecycle of a report (flattened; see misc/docs/design.md for the full map).

### heed.base.new_report_id()

Return a fresh opaque report id (uuid4 hex).

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### heed.base.normalize_newlines(text)

Turn CRLF and lone CR into LF, so a line break counts as one character.

Browsers send every line break of a form field as CRLF; without this, a body at
the length limit would be refused for its line breaks.

```pycon
>>> normalize_newlines("a\r\nb\rc")
'a\nb\nc'
```

### heed.base.strip_control_chars(text)

Remove control characters (keeping tab/newline/CR) from untrusted text.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> strip_control_chars("a\x00b\tc\u202ed")
'ab\tcd'
```

### heed.base.utcnow()

Return the current UTC time (an injectable clock seam for tests).

* **Return type:**
  [`datetime`](https://docs.python.org/3/library/datetime.html#datetime.datetime)
