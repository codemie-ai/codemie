# Calling Platform Tools from a Workspace Script

For people who write scripts by hand. A short reference is given to the model in the script tool's description
(`src/codemie_tools/data_management/code_executor/runtime_sdk/codemie_runtime_sdk_reference.md`); the details of an
error (its code, `retryable`, `may_have_run`) reach the model in the `ToolCallError` itself; operators find the
rollout gates and limits in the code executor README, section "Workspace Script Bridge".

A script in a conversation's or run's workspace, run with the workspace script tool, can call platform tools through
the module `codemie_runtime_sdk` (protocol version 1). Tool calling is available only while the environment's
`features:workspaceScriptBridge` setting is on **and** the sandbox runs in jobs mode (the default); otherwise a call raises
`ToolCallError` with code `unavailable`.

```python
from codemie_runtime_sdk import call_tool, ToolCallError

envelope = call_tool("generic_jira_tool", {"method": "GET", "relative_url": "/rest/api/2/myself"})
print(envelope["http"]["status"], envelope["result"])
```

## What a script may call

| Where the script runs | Tools it may call | Credentials |
|---|---|---|
| A chat, or a workflow step that is an assistant (stored or inline) | The tools in that assistant's own tool list, attached skills included | The assistant's own settings and credentials |
| A workflow tool step | Any tool of the catalog, by name | The running user's integrations in the workflow's project |
| A direct workspace run with no assistant or workflow (REST) | None: every call gives `no_context` | - |

A tool class decides for itself whether a script may call it: `script_callable` is **opt-in**, `False` on the base
class, so a new tool is not callable from scripts until its class sets `script_callable = True`. The script tool
itself, MCP tools and `request_user_input` keep `script_callable = False`; supervisor handoff tools and tools bound to
the chat stream are blocked by name or by their shape whatever the flag says.
Use plain Python for files. A tool outside the allowed set gives `tool_unavailable` or `tool_blocked`.

## Several calls at once

```python
from codemie_runtime_sdk import call_tools, ToolCallError

keys = ["PROJ-1", "PROJ-2", "PROJ-3"]
calls = [{"name": "generic_jira_tool", "args": {"method": "GET", "relative_url": f"/rest/api/2/issue/{key}"}} for key in keys]
for key, item in zip(keys, call_tools(calls)):
    if isinstance(item, ToolCallError):
        print(key, "failed:", item.code)
    else:
        print(key, item["result"]["fields"]["summary"])
```

`call_tools(calls, *, timeout=None)` takes a list of dicts, each with a `name` and optionally `args` and returns a list in the order given. Each item
is the call's envelope, or a `ToolCallError` that is **returned, not raised**, so one failure does not hide the other
results. Up to the `maxParallelCalls` setting (default 5) of the calls run at the same time; the rest wait their turn. The
timeout is one for the whole batch, never beyond the run's time limit; a call not answered in time is a `ToolCallError` with
code `timeout`. A batch holds at most 32 calls; a problem with the batch itself (too many calls, an item that is not a
dict with a `name`, tool calling not available) raises `ToolCallError`.

Put only independent calls that read data into one batch. Calls that create, update, delete or send, and calls that need
another call's result, are made one by one with `call_tool`: nothing orders the calls of a batch. Do not use threads for
this; `call_tools` is the way to call tools at once.

## The result

`call_tool(name, args=None, *, timeout=None)` returns an envelope:

- `envelope["result"]`: the tool's result as JSON. A tool that returns an object, an array or JSON text gives the
  parsed value; any other text stays a string.
- `envelope["http"]`: only for the Jira, Confluence, GitLab and xWiki tools, `{"status": <int>, "reason": <str or None>}`.
  `result` is then the parsed body of the third party's response. A non-2xx status is data, not an error.

This differs from calling the same tool directly, where the model sees text such as `HTTP: GET ... 200 ...` cut to a
token limit. In a script there is no token limit; a result above 256 KiB gives `payload_too_large`.

## Errors

A failed call raises `ToolCallError`; `error.code` is one of the closed set in `codemie_runtime_sdk.ERROR_CODES`:
`no_context`, `tool_blocked`, `tool_unavailable`, `bad_arguments`, `tool_failed`, `unknown_op`, `bad_request`,
`payload_too_large`, `internal_error`, `unavailable`, `timeout`, `deadline_exceeded` and the generic `error`.

Two properties classify a code instead of memorizing what each one means: `error.retryable` says whether trying the
same call again could succeed, `error.may_have_run` whether the tool may already have run before the error, so a
call that changes data (create, update, delete, send) must not be retried without checking first. Both default to
what `code` alone implies; a few raise sites override one or both where the same code covers cases that disagree
(for example `unavailable` covers both "never configured", certainly not run, and "backend stopped answering
mid-run", which may have run). `deadline_exceeded` means too little of the run's time was left to start the call;
it was never sent.

## Limits

- A call waits at most 100 seconds (the `timeout` argument changes it) and never beyond the run's time limit.
- The run's time limit is the `timeoutSeconds` setting: default 120 seconds, at most 480.
- Up to `maxParallelCalls` calls of one run are served at the same time (default 5; 1 serves them one after another).
  All runs of a backend process also share a process-wide limit, so a call may wait for a free place, and that wait
  counts against the call's time. The limit is soft (a place held by a hung call is given back after 8 minutes) and
  not shared fairly between runs.
- A batch holds at most 32 calls and shares one timeout.
- Print or save what you have as you go; print only what the user needs and do not write raw tool results into the
  workspace unless asked.
