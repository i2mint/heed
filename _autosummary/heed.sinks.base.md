# heed.sinks.base

The sink abstraction — where a report goes (strategy pattern).

A sink is the only thing that decides what becomes of a report. The ingest layer calls
`Sink.submit()`; richer sinks may also offer duplicate detection and
acknowledgement. New destinations are added by writing a sink, never by editing a
dispatcher.

### Classes

| [`BaseSink`](#heed.sinks.base.BaseSink)()               | Convenience base giving no-op defaults for the optional sink methods.   |
|---------------------------------------------------------------------------|-------------------------------------------------------------------------|
| [`DupCandidate`](#heed.sinks.base.DupCandidate)(\*\*data)   | A possible duplicate of an incoming report.                             |
| [`Sink`](#heed.sinks.base.Sink)(\*args, \*\*kwargs) | Anything that can receive a report.                                     |
| [`SinkResult`](#heed.sinks.base.SinkResult)(\*\*data)     | Outcome of routing a report to a sink.                                  |

### *class* heed.sinks.base.BaseSink

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Convenience base giving no-op defaults for the optional sink methods.

#### acknowledge(report)

Hook called after a successful submit (default: no-op).

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### find_duplicates(report)

Return likely duplicates of `report` (default: none).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`DupCandidate`](#heed.sinks.base.DupCandidate)]

### *class* heed.sinks.base.DupCandidate(\*\*data)

Bases: `BaseModel`

A possible duplicate of an incoming report.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* heed.sinks.base.Sink(\*args, \*\*kwargs)

Bases: [`Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)

Anything that can receive a report. `submit` is the only required method.

### *class* heed.sinks.base.SinkResult(\*\*data)

Bases: `BaseModel`

Outcome of routing a report to a sink.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].
