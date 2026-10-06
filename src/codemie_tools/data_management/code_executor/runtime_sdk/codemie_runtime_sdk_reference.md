## Calling platform tools from a script

Call platform tools through `codemie_runtime_sdk` (protocol version 1; already available in the run, nothing to install). Calling tools this way is cheaper and more predictable than calling them one by one yourself: use it for multi-step work, loops over results and several reads at once.

```text
from codemie_runtime_sdk import call_tool, call_tools, ToolCallError

call_tool(name: str, args: dict | None = None, *, timeout: float | None = None) -> dict
call_tools(calls: list[dict], *, timeout: float | None = None) -> list[dict | ToolCallError]
```

- Use the exact tool names and arguments from your own tool list; do not invent a tool.
- `timeout` has a default <<default_wait_seconds>> and never waits beyond what is left of the run's own time limit.
- `call_tool` returns `{"result": ..., "http"?: {"status": int, "reason": str | None}}`. `result` is the tool's result as JSON, with no token limit, only a <<max_payload_kib>> KiB cap. For Jira, Confluence, GitLab and xWiki a non-2xx `http.status` is data, not an error.
- `call_tools` takes a list of `{"name": ..., "args": ...}` dicts and returns one item per call, in the order given: the envelope, or a `ToolCallError` (returned, not raised, so one failure never hides the others). Up to <<max_parallel_calls>> calls run at the same time, up to <<max_batch_calls>> calls per batch. Put only independent reads into one batch; a call that changes data, or needs another call's result, goes one by one with `call_tool` instead.
- A run has a time limit; print or save what you need as you go, and print only what the user needs.

### Errors

A failed call raises `ToolCallError(message, code)`. `error.retryable` says whether trying the same call again could succeed; `error.may_have_run` whether the tool may already have run before the error, so a call that changes data (create, update, delete, send) must not be retried without checking first. The message says what happened and, where there is one, what to do.

```python
from codemie_runtime_sdk import call_tool, ToolCallError

try:
    envelope = call_tool("generic_jira_tool", {"method": "GET", "relative_url": "/rest/api/2/myself"})
    print(envelope["http"]["status"], envelope["result"])
except ToolCallError as error:
    print(error.code, error.retryable, str(error))
```

```python
from codemie_runtime_sdk import call_tools, ToolCallError

calls = [{"name": "generic_jira_tool", "args": {"method": "GET", "relative_url": f"/rest/api/2/issue/{key}"}} for key in ("PROJ-1", "PROJ-2")]
for item in call_tools(calls):
    print(item.code if isinstance(item, ToolCallError) else item["result"])
```
