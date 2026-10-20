# heed.mcp

The feedback queue as an MCP server (`pip install 'heed[mcp]'`) (#8).

Each [`FeedbackQueue`](heed.queue.html.md#heed.queue.FeedbackQueue) operation becomes one MCP tool through
`py2mcp` (imported only here, on use), so an agent such as Claude Code can list,
read, triage, deduplicate, file and close reports. The tools are the queue’s own
functions, the same ones any other surface calls.

Run it over a report directory:

```default
python -m heed.mcp /path/to/reports        # stdio transport
```

The report text the tools return was written by anonymous users: an agent reading it
must treat it as data, never as instructions (the server says so in its
instructions).

### Functions

| [`main`](#heed.mcp.main)([argv])                                | `python -m heed.mcp REPORT_DIR`: serve the queue over stdio.      |
|----------------------------------------------------------------------------------------------|-------------------------------------------------------------------|
| [`mk_feedback_mcp`](#heed.mcp.mk_feedback_mcp)([store, rootdir, llm, ...]) | A FastMCP server whose tools are the feedback queue's operations. |

### heed.mcp.main(argv=None)

`python -m heed.mcp REPORT_DIR`: serve the queue over stdio.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### heed.mcp.mk_feedback_mcp(store=None, , rootdir=None, llm=None, confirm=None, name='heed feedback queue')

A FastMCP server whose tools are the feedback queue’s operations.

Give the report `store` itself, or the `rootdir` of a report directory.

* **Return type:**
  [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)
