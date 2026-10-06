# Local verification — EPMCDME-15402 (tool.call bridge hookup)

Run on 2026-09-30 against branch `EPMCDME-15402_script-tool-calls-tool-hookup` (HEAD `a8a1bc95e`), backend started from
the checkout on the host (`uvicorn`, port 8080), LiteLLM proxy OFF (`LLM_PROXY_ENABLED=False`, model `gpt-4.1` direct),
bridge ON (`FEATURE_WORKSPACE_SCRIPT_BRIDGE=true`), `LOG_LEVEL=DEBUG`, sandbox Jobs in the local minikube
(`codemie-code-executor` namespace, image `codemie/codemie-python:2.52.0`, `CODE_EXECUTOR_RUNTIME_CLASS_NAME=none`,
`CODE_EXECUTOR_KUBECONFIG_PATH` set). Postgres and Elasticsearch: the already-running Podman containers.
Real integration used: the Confluence integration of `testuser2@epam.com` (kb.epam.com, reachable). No Jira, GitLab or
xWiki integration exists locally.

## Checklist

Status: PASS / FAIL / NOT RUN. Evidence is the observed script output.

### A. Assistant scope, script uploaded into a chat, LLM runs it (policy `auto_approve`)

| # | Check | Status | Evidence |
|---|---|---|---|
| A1 | Script calls a workspace tool and gets its real result | PASS | `list_workspace_files` -> `result` list |
| A2 | Several calls in a row each get their own result | PASS | write s1/s2, read s1/s2 -> each result holds its own marker, results differ |
| A3 | Tool not in the assistant's tool list | PASS | `generic_jira_tool` -> `tool_unavailable` |
| A4 | Unknown tool | PASS | `no_such_tool` -> `tool_unavailable` |
| A5 | Script tool called from a script | PASS | `execute_workspace_script` -> `tool_blocked` |
| A6 | Misspelled argument name | PASS | `bad_arguments`: `file_path: missing; file_pathh: extra_forbidden` (no values) |
| A7 | `name` not a string / `args` not an object | PASS | both `bad_arguments` |
| A8 | Large result below the cap, no token limit | PASS | 200,199-character file returned whole |
| A9 | Result above 256 KiB | PASS | `payload_too_large`: "result is 300277 bytes, above the 262144 byte limit", immediate, no content |
| A10 | Real HTTP-style tool (Confluence, assistant's own integration) | PASS | `http {status: 200, reason: OK}`, `result` is a dict, status is an int |
| A11 | Non-2xx is data | PASS | Confluence 404 returned normally, `http.status == 404`, `result` dict |
| A12 | Sentinel content never logged on the script path (DEBUG) | PASS | 0 log lines with the runtime-built request sentinel; the Confluence body/space key appears in no log line of the script window |
| A12c | Positive control: same tools via the model path do log it | PASS | model path logged `Calling Tool: write_workspace_file with input {... SENT-<id>...}` and `HTTP: GET/rest/api/space -> 200OK{...key...}`, so the absence in A12 is meaningful |

### B. Confirmation policies (script tool run, real assistant)

| # | Check | Status | Evidence |
|---|---|---|---|
| B1 | `ask_for_approval`: person is asked to approve running the script | PASS | stream carries `thought.interrupted: true`, script did not run (0 output lines) |
| B2 | `approve_for_me`: person is asked (script tool is not safe) | PASS | same as B1 |
| B3 | `auto_approve`: script runs and its calls run without asking | PASS | all of A ran in one turn with no interruption |
| B4 | After approval, the script's calls run without asking each | NOT RUN | `POST .../tool-call/resume` (allow) answered `EmptyInputError: Received no input for __start__` (no checkpoint found for the conversation); not investigated whether it also happens on `main`; covered by unit tests and by B3 |

### C. Workflow scope

| # | Check | Status | Evidence |
|---|---|---|---|
| C1 | Workflow assistant node, stored assistant | PASS | Confluence 200 and 404-as-data with the user's integration; unknown -> `tool_unavailable`; script tool -> `tool_blocked`; `list_workspace_files` -> `tool_unavailable` (documented limitation) |
| C2 | Workflow assistant node, inline (virtual) assistant | PASS | same results as C1 |
| C3 | Bare workflow tool step (`tools:` + state with `tool_id`, `execute_workspace_script`) | PASS (after fix) | first run FAILED (`no_context` on every call, see finding). After the fix (`ScriptToolRegistry.context()` gives workflow scope from the trusted constructor project even unfilled), re-run against the local backend at the fix commit: Confluence 200 with `http.status` 200 and dict result, Confluence 404 as data (`http.status` 404), unknown tool refused with a `tool_*` code; both workflow states Succeeded |

### D. No context

| # | Check | Status | Evidence |
|---|---|---|---|
| D1 | Direct REST run (`POST /v1/workspaces/{id}/execute`) | PASS | tool calls -> `no_context`, script tool -> `tool_blocked` |
| D2 | Assistant without the script tool runs no script | NOT RUN | covered by unit tests only |
| D3 | Switch off | NOT RUN | sub-task 1 scope |

## Finding (defect, fixed after this run)

C3: `ScriptToolRegistry.context()` (`src/codemie/service/script_tool_calls/context.py`) returns a scope-NONE context
while `_tools is None`, and `fill()` is called only from `ToolkitService.get_tools` (via `bind_script_registries`,
`toolkit_service.py:608`). The bare workflow tool step builds its tool through
`ToolsService.find_tool_from_config(...)` after `ToolkitService.get_toolkit_methods(...)`
(`src/codemie/workflows/nodes/tool_node.py:196-215`); `AgentWorkspaceToolkit.get_tools` creates the registry with
`workflow_project=...` from the virtual assistant, but nothing fills it, so the workflow project is never used.
The task text names this step as the workflow case ("a workflow step runs under a temporary assistant with a single
tool"). A workflow scope does not need the tool list (tools are resolved by name), so an unfilled registry that
already has a workflow project could return a WORKFLOW context; unit tests that fill the registry by hand do not
cover this path.

Fixed after this run: `ScriptToolRegistry.context()` now returns a WORKFLOW context (project from the constructor,
no tools needed because workflow scope resolves by name) when a workflow project was given at construction, even if
`fill()` was never called. The project reaches the constructor only from `AgentWorkspaceToolkit._workflow_project()`
(a `VirtualAssistant` with an `execution_id`, built server-side); a request-supplied `workflow_execution_id` still
yields scope NONE until `fill()` and ASSISTANT afterwards (test
`test_request_supplied_workflow_execution_id_gains_no_workflow_scope`). Covered by unit tests through the real
toolkit and authorizer (`test_bare_workflow_step_*` in `test_toolkit_script_registry.py`) and by the C3 re-run above.

## Gaps not covered by this run

- B4: after approving, the script's own calls run without asking (resume returned `EmptyInputError`); auto_approve covers
  the same path, and unit tests cover the confirmation policies.
- D2: an assistant without the script tool runs no script (unit tests only).
- No Jira, GitLab or xWiki integration exists locally, so those HTTP-style tools were not exercised end to end.
