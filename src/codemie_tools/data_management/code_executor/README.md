# Code Executor Tool

Secure Python code execution tool with Kubernetes-based sandboxing and comprehensive configuration support.

## Overview

The Code Executor Tool provides a secure, isolated sandbox environment for executing Python code with resource limits, security policies, and complete isolation. It supports Kubernetes-backed sandbox execution only.

## Features

- **Secure Execution**: Production-grade security policy for multi-tenant environments
- **File Upload & Export**: Upload files to sandbox and export generated files with optimized parallel transfer
- **Resource Management**: Configurable CPU and memory limits
- **Timeout Protection**: Automatic timeout for infinite loops and long-running operations
- **Session Management**: Persistent session pooling with health checks
- **Full Configuration**: Environment variables and programmatic configuration
- **Kubernetes Integration**: Supports shared-pod and jobs-based sandbox execution modes

## Execution Mode

Code runs in an isolated Kubernetes sandbox pod. `CODE_EXECUTOR_EXECUTION_MODE`
defaults to `sandbox`, which is the only accepted value, so it does not need to
be set explicitly.

## Configuration

All configuration is managed through environment variables. The tool automatically loads settings on initialization.

### Quick Configuration Reference

**Essential Settings:**
- `CODE_EXECUTOR_EXECUTION_MODE` - Execution mode (default: `sandbox`)
- `CODE_EXECUTOR_SECURITY_THRESHOLD` - Security policy threshold (default: `LOW`)
- `CODE_EXECUTOR_NAMESPACE` - Kubernetes namespace (default: `codemie-code-executor`)
- `CODE_EXECUTOR_EXECUTION_TIMEOUT` - Code timeout in seconds (default: `30.0`)
- `CODE_EXECUTOR_MEMORY_LIMIT` - Pod memory limit (default: `256Mi`)

### Environment Variables

#### Required Execution And Security Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `CODE_EXECUTOR_EXECUTION_MODE` | Execution mode. Code runs in an isolated Kubernetes sandbox pod. | `sandbox` |
| `CODE_EXECUTOR_SECURITY_THRESHOLD` | Security policy: `SAFE`, `LOW`, `MEDIUM`, `HIGH` | `LOW` |

#### Kubernetes Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `CODE_EXECUTOR_NAMESPACE` | Kubernetes namespace for executor pods | `codemie-code-executor` |
| `CODE_EXECUTOR_DOCKER_IMAGE` | Docker image for Python execution environment | `codemie/codemie-python:2.52.0` |
| `CODE_EXECUTOR_MAX_POD_POOL_SIZE` | Maximum number of pods to create dynamically | `5` |
| `CODE_EXECUTOR_POD_NAME_PREFIX` | Prefix for dynamically created pod names | `codemie-executor-` |
| `CODE_EXECUTOR_SANDBOX_MODE` | Sandbox mode: `sandbox-shared` or `sandbox-jobs` | `sandbox-jobs` |

#### Working Directory

| Variable | Description | Default |
|----------|-------------|---------|
| `CODE_EXECUTOR_WORKDIR_BASE` | Base working directory for code execution | `/home/codemie` |

#### Timeout Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `CODE_EXECUTOR_EXECUTION_TIMEOUT` | Code execution timeout in seconds (protects against infinite loops) | `30.0` |
| `CODE_EXECUTOR_SESSION_TIMEOUT` | Session lifetime in seconds | `300.0` |
| `CODE_EXECUTOR_DEFAULT_TIMEOUT` | Default operation timeout in seconds | `30.0` |

#### Resource Limits

| Variable | Description | Default |
|----------|-------------|---------|
| `CODE_EXECUTOR_MEMORY_LIMIT` | Memory limit for executor pods | `256Mi` |
| `CODE_EXECUTOR_MEMORY_REQUEST` | Memory request for executor pods | `256Mi` |
| `CODE_EXECUTOR_CPU_LIMIT` | CPU limit for executor pods | `1` |
| `CODE_EXECUTOR_CPU_REQUEST` | CPU request for executor pods | `500m` |
| `CODE_EXECUTOR_MAX_THREADS` | Maximum number of threads allowed per execution (enforced inside the sandbox process, both modes) | `64` |
| `CODE_EXECUTOR_MAX_OPEN_FILES` | Maximum number of open files allowed per execution (enforced inside the sandbox process, both modes); the Python runtime opens roughly 20 files before user code runs | `256` |

#### Pod Security Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `CODE_EXECUTOR_RUN_AS_USER` | User ID for pod execution | `1001` |
| `CODE_EXECUTOR_RUN_AS_GROUP` | Group ID for pod execution | `1001` |
| `CODE_EXECUTOR_FS_GROUP` | Filesystem group ID for pod execution | `1001` |

#### Other Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `CODE_EXECUTOR_VERBOSE` | Enable verbose logging (`true`/`false`) | `false` |
| `CODE_EXECUTOR_KEEP_TEMPLATE` | Persist template after code execution | `true` |
| `CODE_EXECUTOR_SKIP_ENVIRONMENT_SETUP` | Skip environment setup in sandbox (`true`/`false`) | `false` |
| `CODE_EXECUTOR_YAML_POLICY_PATH` | Optional path to custom YAML policy file | `""` |
| `CODE_EXECUTOR_KUBECONFIG_PATH` | Optional kubeconfig path | `""` |

## Usage

### Basic Usage

```python
from codemie_tools.data_management.code_executor import CodeExecutorTool

tool = CodeExecutorTool(
    file_repository=file_repo,
    user_id="user123"
)

result = tool.execute(code="print('Hello, World!')")
print(result)
```

### With File Upload

```python
from codemie_tools.data_management.code_executor import CodeExecutorTool
from codemie_tools.base.file_object import FileObject

files = [FileObject(name="data.csv", mime_type="text/csv", owner="user", content=...)]
tool = CodeExecutorTool(
    file_repository=file_repo,
    user_id="user123",
    input_files=files
)

code = """
import pandas as pd
df = pd.read_csv('data.csv')
print(f"Loaded {len(df)} rows")
"""
result = tool.execute(code=code)
```

### With File Export

```python
tool = CodeExecutorTool(file_repository=repo, user_id="user")

code = """
import pandas as pd
df = pd.DataFrame({'x': [1, 2, 3], 'y': [4, 5, 6]})
df.to_csv('output.csv', index=False)
print('File created')
"""

result = tool.execute(code=code, export_files=["output.csv"])
```

## Environment Setup Examples

### Local Development Against Sandbox Infrastructure

```bash
export CODE_EXECUTOR_EXECUTION_MODE=sandbox
export CODE_EXECUTOR_SECURITY_THRESHOLD=LOW
export CODE_EXECUTOR_NAMESPACE=dev-runtime
export CODE_EXECUTOR_EXECUTION_TIMEOUT=60
export CODE_EXECUTOR_VERBOSE=true

python app.py
```

### Production (In-Cluster)

```bash
export CODE_EXECUTOR_EXECUTION_MODE=sandbox
export CODE_EXECUTOR_SECURITY_THRESHOLD=LOW
export CODE_EXECUTOR_MEMORY_LIMIT=512Mi
export CODE_EXECUTOR_CPU_LIMIT=2
export CODE_EXECUTOR_EXECUTION_TIMEOUT=120
export CODE_EXECUTOR_MAX_POD_POOL_SIZE=10
export CODE_EXECUTOR_POD_NAME_PREFIX=prod-executor-

python app.py
```

### Docker Compose

```yaml
services:
  app:
    image: your-app
    environment:
      - CODE_EXECUTOR_EXECUTION_MODE=sandbox
      - CODE_EXECUTOR_SECURITY_THRESHOLD=LOW
      - CODE_EXECUTOR_NAMESPACE=docker-runtime
      - CODE_EXECUTOR_EXECUTION_TIMEOUT=45
      - CODE_EXECUTOR_MEMORY_LIMIT=256Mi
```

### Kubernetes Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: codemie-app
spec:
  template:
    spec:
      containers:
      - name: app
        image: your-app
        env:
        - name: CODE_EXECUTOR_EXECUTION_MODE
          value: "sandbox"
        - name: CODE_EXECUTOR_SECURITY_THRESHOLD
          value: "LOW"
        - name: CODE_EXECUTOR_NAMESPACE
          value: "production-runtime"
        - name: CODE_EXECUTOR_EXECUTION_TIMEOUT
          value: "120"
        - name: CODE_EXECUTOR_MEMORY_LIMIT
          value: "512Mi"
        - name: CODE_EXECUTOR_CPU_LIMIT
          value: "2"
        - name: CODE_EXECUTOR_MAX_POD_POOL_SIZE
          value: "10"
        - name: CODE_EXECUTOR_POD_NAME_PREFIX
          value: "prod-exec-"
```

## Workspace Script Bridge

Scripts run by the workspace script runner can call platform tools during their run when the
customer-config component `features:workspaceScriptBridge` is enabled. The component ships in
`config/customer/customer-config.yaml` disabled (`enabled: false`), with `timeoutSeconds: 120` and `maxParallelCalls: 5`.

### Switch and limits

- The component is editable at runtime in the admin settings (`enabled`, `timeoutSeconds` and `maxParallelCalls`). A saved
  value applies to runs that start afterwards, without a restart: the backend reads the setting on every
  script run, and other replicas pick it up within `CUSTOMER_CONFIG_CACHE_TTL_SECONDS`. A saved value
  also works when a customer ConfigMap that replaces the default file has no such component.
- The value in the YAML file is the default when nothing is saved. `FEATURE_WORKSPACE_SCRIPT_BRIDGE=true|false`
  overrides `enabled` in the YAML at load time, only where the component exists in the loaded file; changing
  the file or this variable needs a backend restart.
- The bridge works in `sandbox-jobs` mode (`CODE_EXECUTOR_SANDBOX_MODE`, the default) **only**. In the pooled
  `sandbox-shared` mode the component counts as off: the script tool's description has no SDK reference and a script
  that calls the SDK gets `unavailable`. When it is on in jobs mode, the
  Job deadline (`activeDeadlineSeconds`) widens to `max(execution timeout, timeoutSeconds) + 60s`. The
  tool-call exchange folder is stopped and removed right after the script finishes, before exports and
  changed files are pulled from the Job.
- **One settings value.** The backend reads the component once per run (`resolve_tool_calling_settings()`, which also
  checks the sandbox mode) into one frozen value, `ToolCallingSettings` (`run_timeout_seconds`, `max_payload_bytes`,
  `max_parallel_calls`). It builds the run budget and the configuration the SDK gets in the sandbox, and it is the only
  thing passed between the layers, so a new setting for script runs is a field there, its reading and its declaration.
- **`timeoutSeconds` rules.** One validator reads the value everywhere: a missing, non-numeric, non-positive or
  non-finite value falls back to the default of 120 seconds with a warning and never turns tool calling off while
  the component is enabled; a value above the ceiling of 480 seconds is clamped to it with a warning. The admin
  field accepts at most three digits. The value is wall-clock for the script run, and the Job budget adds up to
  60 seconds for pod start and download.
- **`maxParallelCalls` rules.** How many tool calls of one run the backend serves at the same time: default 5, `1`
  serves them one after another. It is a ceiling for one run, not a guarantee. The same validator rules apply: a
  missing or invalid value falls back to the default, and a value above the backend's process-wide limit is lowered to
  it. The process-wide limit is `WORKSPACE_SCRIPT_TOOL_CALLS_MAX_CONCURRENT` (default 10): all runs of one backend
  process share it, so the worst case is bounded by it and not by the number of runs times `maxParallelCalls`. A call
  waiting for a free place counts that wait against its time and is answered `deadline_exceeded` if too little of the
  run's time is left once it gets the place. Two properties of the process-wide limit are accepted as they are:
  (1) it is **soft**: a call that holds its place longer than `WORKSPACE_SCRIPT_TOOL_CALL_MAX_HOLD_SECONDS`
  (default 480 seconds, the ceiling of `timeoutSeconds`) gives it back although it has not returned (nothing can interrupt a hung tool), so the number
  of tool calls really running in one process can exceed the limit by the number of such hung calls; (2) it is **not
  fair**: places are not shared per run or per user, so two runs with `maxParallelCalls` 5 can occupy all 10 places
  and make every other run wait. There is no quota of calls per run either. The values are not measured; check the event loop latency, memory and the
  database pool under a load run of parallel calls before enabling (see the rollout gates).
- **The SDK and the backend follow the limit.** A call waits at most its own timeout (default 100 seconds) and never
  beyond the time left of `timeoutSeconds` since the script started; when nothing is left it raises `timeout` before
  anything is sent. The backend does not start a call when less than 2 seconds of the Job budget remain and answers
  `deadline_exceeded` instead.
- **Streams and gateways.** The budget a chat stream must survive is `timeoutSeconds` plus up to 60 seconds for pod
  start and download plus the wait for a free executor slot (30 seconds). Set `timeoutSeconds` below the shortest
  idle limit of the gateways in front of the backend minus about 90 seconds.

### Calling a tool from a script

The runtime SDK `codemie_runtime_sdk` is importable in the script. Scripts call `call_tool(name, args=None, *,
timeout=None)`, a thin wrapper over the one operation that exists, `tool.call` (payload `{"name": <tool name>,
"args": {<tool arguments>}}`); `sdk.call(op, payload, timeout)` stays public. To make several calls at once
a script calls `call_tools([{"name": name, "args": args}, ...], *, timeout=None)` (see "Several calls at once" below). The arguments are validated against
the tool's own argument schema. The model-facing description of the SDK (shown in the script tool's description
while the component is on) is `runtime_sdk/codemie_runtime_sdk_reference.md`, and a page for people who write
scripts by hand is [docs/workspace-script-sdk.md](../../../docs/workspace-script-sdk.md).

```python
import codemie_runtime_sdk as sdk

reply = sdk.call_tool("generic_jira_tool", {"method": "GET", "relative_url": "/rest/api/2/myself"})
print(reply["http"]["status"], reply["result"])
```

A successful call returns an object with these keys:

| Key | Present | Meaning |
|---|---|---|
| `result` | always | The tool's result as JSON. A tool that returns a pydantic model, a dict or a list gives that value; a string that parses as a JSON object or array gives the parsed value; any other string stays a string. |
| `http` | only for Jira, Confluence, GitLab and xWiki tools | `{"status": <int>, "reason": <str or null>}` of the HTTP response. `result` then holds the response body, parsed when it is a JSON object or array and a string otherwise. |

A non-2xx HTTP status is data, not an error: a Jira-, Confluence-, GitLab- or xWiki-tool call whose response
is 404 returns normally with `http.status == 404` and the response body in `result`. (For Jira this holds on the
script path only; the model path still raises on a non-2xx status.)
The `HTTP: ...` text the model sees is not parsed by the script path; read `http`.

A failed `call_tool` raises `sdk.ToolCallError`, with a `code` from the closed set of named constants in the SDK
module (`CODE_NO_CONTEXT`, ..., and `ERROR_CODES`, built from them plus the generic `error`; a contract test fails if
the backend passes a literal instead of a constant, names one the SDK does not define, or `ERROR_CODES` drifts from
the set of constants). Two properties classify a code instead of a per-code table: `error.retryable` says whether
the same call could succeed if tried again; `error.may_have_run` says whether the tool may already have run before
the error, so a call that changes data (create, update, delete, send) must not be retried without checking first.
Both default to what `code` alone implies, except at the few raise sites that know with certainty which way a
code-shared ambiguity resolves (for example `unavailable` covers both "never configured", always `False`/`False`,
and "backend stopped answering mid-run", which may have run). The message never contains argument values or result
content, and is written to say what to do, not just what happened.

### Several calls at once

```python
import codemie_runtime_sdk as sdk

calls = [{"name": "generic_jira_tool", "args": {"method": "GET", "relative_url": f"/rest/api/2/issue/{key}"}} for key in keys]
for key, item in zip(keys, sdk.call_tools(calls)):
    print(key, item.code if isinstance(item, sdk.ToolCallError) else item["http"]["status"])
```

`call_tools` writes every request, then waits for every answer in one loop (no threads, no new import; the wire format
and the protocol version are unchanged). It returns a list in the order given: the envelope of a call that succeeded,
or a `ToolCallError` that is **returned, not raised**, so one failure does not hide the other results. There is one
timeout for the whole batch (default 100 seconds) and never beyond the time left of the run's limit; a call that is
not answered in time is a `ToolCallError` with code `timeout` and is **withdrawn**: the SDK removes its request file, the channel sees that, and a call that has not started yet never starts and leaves the pool and the process gate at once. A call that is already running cannot be stopped (nothing can interrupt a tool); its answer is dropped. Any other key of a batch item goes to the backend with the call, which refuses a key it does not know. A batch holds at most 32 calls (`MAX_BATCH_CALLS`);
a problem with the batch itself (too many calls, an item that is not a dict with a string `name`, tool calling not
available) raises. There is no batch operation on the wire: one response for a batch would hit the 256 KiB cap for
all of it. Calls that change data, or that depend on each other, do not belong in one batch: nothing orders them. The
backend takes no lock per tool instance and tools declare nothing about it, so the script decides what runs at once;
tools written for a script's parallel calls must not write state during a call (the Jira tool builds its client under
a lock, and the Confluence tool no longer writes its output limit on the script path).

Rules and limits:

- **No token limit.** The result goes to a script, not to the model, so the 30,000-token limit of the model
  path does not apply and nothing is truncated. The only bound is the transport cap of 256 KiB (`MAX_PAYLOAD_BYTES`)
  on the JSON-encoded response (UTF-8, not ASCII-escaped, so non-ASCII text counts at its real size). A larger
  result gives `payload_too_large` at once; narrow the tool arguments (for example a path filter or a page size)
  and call again.
- **The request is measured after escaping.** The SDK encodes the request with `json.dumps` defaults, which
  escape non-ASCII characters (six bytes per character, more outside the Basic Multilingual Plane). The 256 KiB
  request cap therefore holds fewer characters of non-ASCII text than of ASCII. Documented only; nothing changes.
- **Up to `maxParallelCalls` at once.** The backend serves up to that many requests at the same time: the handlers
  run in a thread pool, while **every exec into the pod runs on the one polling thread** (`kubernetes.stream` swaps the
  API client's `request` function for the duration of a call, so two overlapping execs on one client are not safe). A
  poll names the requests already being served, so their bodies are not read again. There is no extra length-prefix
  framing (the pod-side response writer is already length-prefixed and publishes the file atomically with
  `os.replace`, so the SDK never reads a partial response), and the exec channel has no stdin half-close, which is why
  the writer reads exactly the announced number of bytes. A failed response write is retried on a later tick; the call
  is never run again. When the run ends, calls still in flight write nothing and are settled as not delivered.
  Every request ends in one `CallOutcome` record made by the dispatcher (call id, op, tool name, error code, duration,
  response size; refused and malformed requests too), completed by the channel with `delivered` and `withdrawn` and
  handed to `on_settled(outcome)`; nothing listens yet.
- **An oversized HTTP result is refused before it is parsed.** The body's length is compared with the cap first and
  `payload_too_large` is returned without parsing it, so a huge response is not held, parsed and encoded just to be
  refused.
- **Which tools.** In a chat, and in a workflow assistant step (stored or inline), the tools in the assistant's
  final tool list, with the assistant's settings and credentials. In a bare workflow tool step, any tool of the
  catalog resolved by name for the running user in the workflow's project, with that user's integrations. The
  scope comes from the node kind set by trusted code, never from a request field. A tool class decides for itself
  whether it may be called this way: `CodeMieTool.script_callable` is **opt-in**, `False` on the base class, so a
  new tool is not callable from scripts until its class sets `script_callable = True`
  (`tests/codemie/service/script_tool_calls/callable_tool_classes.txt` lists the classes that currently do, and a
  test fails on drift). The script tool itself, MCP tools, tools that need the chat stream (`request_user_input`)
  and supervisor handoff tools are additionally blocked outright regardless of the flag. A tool outside both checks
  gives `tool_unavailable` or `tool_blocked`.
- **Approval is unchanged.** Running the script is confirmed by the assistant's confirmation policy as before;
  the calls the script makes are never confirmed one by one.
- **Tool `_run` overrides are bypassed.** The script path calls the tool's structured result, so behavior a
  tool adds only in `_run` (for example the health notice and index-error precheck of the code-datasource
  tools) is not applied.

### Rollout gates

The one list of what must hold in an environment before the component is switched on there. Any admin can switch the
component on at runtime, so nothing in the code enforces these gates: whoever enables it answers for them. Fill the
"Owner" and "Checked" columns per environment (who verified, when, with which evidence); a gate with no evidence is
not passed. The accepted risks of the feature are recorded in the architecture specification, section 7.5
(`docs/stories/2026-09-28-custom-tools/architecture-spec.md`, a local planning document).

| # | Gate | Pass criterion | How to check | Owner | Checked |
|---|---|---|---|---|---|
| 1 | **Network egress** | Executor pods cannot open any outbound connection. The codemie chart ships no NetworkPolicy for the executor namespace (`CODE_EXECUTOR_NAMESPACE`, default `codemie-code-executor`), so a deny-all egress policy must exist and be enforced. Without it a script can send tool results out; this is the only defence against exfiltration. Tool calls travel over the exec channel the backend uses, which the policy does not affect | `kubectl get networkpolicy -n <executor namespace>`; from a test run, `urllib.request.urlopen("https://example.com")` must fail | | |
| 2 | **Runtime class** | `CODE_EXECUTOR_RUNTIME_CLASS_NAME` (default `gvisor`) names a runtime class that exists in the cluster; it is not empty or `none` outside local development | `kubectl get runtimeclass`; the executor pod's `runtimeClassName` | | |
| 3 | **Executor pool size** | `CODE_EXECUTOR_MAX_POD_POOL_SIZE` is at least 5 on every replica that serves the component (default 5). Runs and `code_executor` share one per-process pool and a run with tool calls holds its slot for as long as it runs (up to the budget, see "Time limit"), so with a pool of 1 one such run blocks code execution on that replica; a caller without a slot after 30 seconds fails with "at capacity" | The value in each environment's deployment settings | | |
| 4 | **Time limit and gateways** | `timeoutSeconds` is at most 480 (the ceiling). The longest silence on a chat stream is slot wait 30 s + `timeoutSeconds` + 60 s for pod start (570 s at the ceiling) and every stream and gateway between the browser and the backend stays open longer. Known: API ingress 600 s; LiteLLM proxy `LLM_PROXY_TIMEOUT` 300 s (lower the setting if it matters on that path). Other gateways are unverified | Ingress `proxy-read-timeout` and any other proxy in front of the backend, per environment | | |
| 5 | **Image contract** | The pinned executor image has `python3` 3.10 or newer (the SDK's annotations are evaluated at run time) and the shell tools `sh` and `rm`, which the exchange helpers use | Run the smoke check below on that image | | |
| 6 | **Load check** | Several runs making parallel calls to the Jira tool and to knowledge-base search keep the event loop latency, memory and database pool within the usual range. `maxParallelCalls` (default 5) and `WORKSPACE_SCRIPT_TOOL_CALLS_MAX_CONCURRENT` (default 10) are decided but not measured; both are settings and can be lowered without a release. Include one hung tool call (the process-wide limit gives its place back after `WORKSPACE_SCRIPT_TOOL_CALL_MAX_HOLD_SECONDS`) | A load run on a staging replica | | |
| 7 | **One recorded run on a real Job** | The smoke check below ran end to end on the build that is being deployed (not an earlier build) and its output is kept as evidence | Smoke check, then attach the output | | |
| 8 | **Left-behind Jobs are visible** | Someone is alerted when a Job labelled `app=codemie-executor` is older than its budget plus the 60 s TTL (a backend restart mid-run leaves its Job to Kubernetes, which stops it at `activeDeadlineSeconds` and removes it 60 s later; nothing in the backend reconciles them) | An alert rule on the Job age by label | | |
| 9 | **Whose credentials a script uses** | The deployment accepts that a script in a shared assistant runs the tools with the integration the assistant is configured with, in bulk, for any user of that assistant (approval covers the whole script, section 5.7 of the specification) | A decision recorded with the environment | | |

### Smoke check

Enable the component (admin settings or YAML). In an assistant that has the workspace toolkit and at least one
integration-backed tool configured (the example uses the Generic Jira tool; use any tool the assistant has), upload
this script to a chat and ask the assistant to run it:

```python
import codemie_runtime_sdk as sdk

reply = sdk.call_tool("generic_jira_tool", {"method": "GET", "relative_url": "/rest/api/2/myself"})
print("STATUS", reply["http"]["status"])
```

Expect `STATUS 200`. Run again with a wrong name to see the coded error:

```python
import codemie_runtime_sdk as sdk

try:
    sdk.call_tool("no_such_tool")
except sdk.ToolCallError as exc:
    print("CODE", exc.code)  # tool_unavailable
```

## Security

### Security Policy

The tool implements a production-grade security policy that blocks:
- System operations (os, subprocess, sys manipulation)
- File system operations (shutil, pathlib, glob, tempfile)
- Network operations (socket, urllib, requests, httpx)
- Process/thread manipulation (threading, multiprocessing)
- Code evaluation/compilation (eval, exec, compile)
- Inspection/introspection modules (inspect, importlib)

### Pod Security

Executor pods are configured with:
- Non-root user execution
- Read-only root filesystem, with explicit `emptyDir` mounts for the
  workdir/tmp/cache paths the sandboxed code and its dependencies need
- No privilege escalation
- All capabilities dropped
- Seccomp profile (`RuntimeDefault`) for system call restriction
- No host namespace access

The in-process `FilesystemAccessDenied` guard (`filesystem_policy.py`) is
defense-in-depth only — it does not intercept syscalls made directly by
native/C-extension code. The kernel-level controls above, and gVisor in
JOBS mode, are the actual security boundary against native code.

### User Isolation

Each user gets an isolated working directory based on their sanitized user ID, preventing directory traversal attacks and ensuring data isolation.
