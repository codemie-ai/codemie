# Script tool calls: connect the bridge to platform tools (EPMCDME-15402) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** A workspace script calls platform tools through `sdk.call("tool.call", ...)` and gets real, complete results; echo goes away.

**Architecture:** A mutable `ScriptToolRegistry` rides on the script tool, is filled at the end of `ToolkitService.get_tools`, and becomes a frozen `ScriptRunContext`. `AgentWorkspaceService` turns it into a handler closure that flows through `WorkspaceScriptRunner` -> `JobBridgeOptions` -> `ToolCallChannel`. Results are structured by `CodeMieTool.execute_structured` (`HttpResult` keeps status/body of four HTTP tools), never via `_run`.

**Tech Stack:** Python 3.12, pydantic v2, LangChain `BaseTool`, pytest. Strict typing everywhere (no `Any`, `X | None`, TypedDict/dataclass, hints on all signatures). License header on every new file. Commit per task using the repository's existing convention (prefix `EPMCDME-15402:`). Run only the task's own tests; whole-suite gates belong to Stage 7. Line numbers were checked against current code; the spec's paths for GitLab and xWiki were wrong (real: `codemie_tools/core/vcs/gitlab/tools.py:191`, `codemie_tools/core/project_management/xwiki/tools.py:131-142`).

**Plan-time findings (settled, do not reopen):**
- Pod write (`pod_scripts/write_response.py`) copies raw bytes and the SDK reads with `encoding="utf-8"`, so `ensure_ascii=False` is safe on responses. SDK *requests* still use `json.dumps` default (escaped), so the request cap stays lower for non-ASCII; document only.
- Workflow identity is the run `user`. `owner_user_id` only matters for an explicit `integration_alias` on a global workflow (`tool_node.py:369`); by-name calls carry no alias.
- Settings lookup (`settings_handler.py:235`) resolves default, user+project, user-global, then project-level settings, so project and global integrations resolve in the workflow's project.
- `find_tool_by_invoke_request` passes `request=''`, so workspace toolkit tools are `tool_unavailable` in workflow scope. Document it.
- Only `RequestUserInputTool` in the final list holds `thread_generator`; MCP classes are `MCPTool`/`ContextAwareMCPTool`.
- `_run` overrides (code-datasource tools' health notice / index-error precheck, `request_user_input`) are bypassed by `run_for_script`; accepted. Hedge tools return pydantic (default conversion). `SearchKBResponse` is pydantic with base64 images, so it needs `to_script_value`.
- Jira calls `raise_for_status`, so its 404 stays `tool_failed`; Confluence, GitLab, xWiki return non-2xx as data.

## Task 1: Channel error path, UTF-8 responses, oversize message
**Files:** Modify `codemie_tools/data_management/code_executor/tool_call_channel.py` (`_answer` 393-414, handler type 92); Test `tests/codemie_tools/data_management/code_executor/test_tool_call_channel.py`
Test-first: yes — a handler raising `ToolCallRefused("tool_blocked", "m")` gives the script code `tool_blocked` and message `m`; a non-ASCII result under the cap arrives intact; an oversize result gives `payload_too_large` whose message states the encoded size and limit in bytes.
- [ ] Add `class ToolCallRefused(Exception)` with `code: str`, `message: str`; in `_answer` catch it before `TypeError/ValueError` and return `_error_bytes`. Encode with `ensure_ascii=False`; oversize message: `f"result is {len(encoded)} bytes, above the {MAX_PAYLOAD_BYTES} byte limit"`. Echo stays for now.

## Task 2: `HttpResult`, `ScriptResult`
**Files:** Create `codemie_tools/base/http_result.py`, `codemie_tools/base/script_result.py`; Test `tests/codemie_tools/base/test_http_result.py`
Test-first: yes — each layout's `str()` equals the legacy f-string for the same inputs (incl. `reason=None`), `with_suffix` changes only the string, fields survive `copy.copy`, JSON object/array body parses, non-JSON stays string.
- [ ] `HttpResult(str)`: `__new__(cls, method, url, status, reason, body, layout, rendered=None)`; layouts `spaced` (`HTTP: {m} {u} -> {s} {r} {b}`), `compact` (`HTTP: {m}{u} -> {s}{r}{b}`), `body_on_new_line` (`... {s} {r}\n{b}`); attributes kept; `__getnewargs__`; `with_suffix(text)`; `to_script_value()`.
- [ ] `script_result.py`: `type JsonValue`, frozen `ScriptResult(result, http_status=None, http_reason=None)`, `ScriptValueSource` runtime-checkable Protocol.

## Task 3: `execute_structured` and `run_for_script`
**Files:** Modify `codemie_tools/base/codemie_tool.py` (add after `_run`, do not touch `_run`); Test `tests/codemie_tools/base/test_codemie_tool_script.py`
Test-first: yes — table: `ScriptValueSource`, pydantic -> `model_dump(mode="json")`, dict/list (non-JSON values via `str`), None/bool/number, `"{...}"`/`"[...]"` string parsed, other strings kept, other types `str()`; `run_for_script` validates config, does no token limiting for a 100k-token string, and raises (not returns) on failure.
- [ ] `execute_structured(*args, **kwargs) -> ScriptResult` in that order of conversion; `run_for_script` = `_validate_config()` then `execute_structured`. No logging inside either.

## Task 4: Adopt `HttpResult` in four tools; output-type sources
**Files:** Modify jira `tools.py:214,230,232,237` (+ `JiraMultimodalResponse` line 51), `confluence/tools.py:163`, `gitlab/tools.py:191`, xwiki `tools.py:131-142`, `codemie/agents/tools/kb/search_kb.py:96`; Test `tests/codemie_tools/base/test_http_result_golden.py`
Test-first: yes — for each tool, mocked HTTP gives an `execute()` string byte-equal to the pre-change expected literal (Jira with attachment-transfer suffix), `execute_structured` gives parsed body and int `http_status` (Confluence 404 is data); Jira multimodal and `SearchKBResponse` give text only, no images.
- [ ] Build `HttpResult` at each site (Jira/GitLab `spaced`, Confluence `compact`, xWiki `body_on_new_line`); Jira `+=` become `with_suffix`. `JiraMultimodalResponse` holds the `HttpResult` in a `PrivateAttr` (pydantic would coerce a str subclass in a field) and implements `to_script_value`; `SearchKBResponse.to_script_value` returns its text. Fake-build tools like neighbouring tests do (Glob `tests/codemie_tools/core/**`). `logger.debug(response_string)` lines stay.

## Task 5: Script-call log guard
**Files:** Create `codemie/configs/script_call_log_guard.py`; Modify `codemie/configs/logger.py:226-232`; Test `tests/codemie/configs/test_script_call_log_guard.py`
Test-first: yes — a handler with the filter drops records while `script_call_active` is set and keeps them after; `install_script_call_log_filter()` adds it to handlers of root and all existing loggers, idempotently.
- [ ] `script_call_active: ContextVar[bool]` (default False), `suppress_call_logging()` context manager, `ScriptCallLogFilter`, `install_script_call_log_filter()`; call it at the end of `logger.py`. Note `codemie` loggers have `propagate=False`, so tests attach their own handler rather than `caplog`.

## Task 6: Run context, registry, exclusions
**Files:** Create `codemie/service/script_tool_calls/{__init__,context,exclusions}.py`; Test `tests/codemie/service/script_tool_calls/test_context.py`
Test-first: yes — registry unfilled -> scope `none`; filled -> `assistant` with tools by name; `workflow_project` set (constructor or `fill`) -> `workflow` and wins; exclusion drops the script tool, `MCPTool`, `ContextAwareMCPTool`, `RequestUserInputTool` and any tool with a non-None `thread_generator`.
- [ ] `ScriptScope(StrEnum)`; frozen `ScriptRunContext(user: User, scope, project: str | None, tools: Mapping[str, BaseTool])` with `without_context(user)`; `ScriptToolRegistry(user, workflow_project=None)` with `fill(tools, workflow_project=None)` and `context()`; `is_excluded_from_script_calls(tool)` (MCP classes imported lazily).

## Task 7: Authorizer and workflow resolution
**Files:** Create `codemie/service/script_tool_calls/{authorizer,workflow_resolution}.py`; Test `tests/codemie/service/script_tool_calls/test_authorizer.py`
Test-first: yes — table, first match wins: script tool name -> `tool_blocked`; scope none -> `no_context`; assistant: name in registry else `tool_unavailable`; workflow: resolver result else `tool_unavailable` (resolver raising `ValueError` or returning an excluded tool included).
- [ ] `authorize_tool_call(context, name, resolver=resolve_workflow_tool) -> BaseTool` raising `ToolCallRefused`. `resolve_workflow_tool(user, project, name)`: `VirtualAssistantService.create_from_tool_invocation(name, user, project)`, `ToolsService.find_tool_by_invoke_request(name, ToolkitService.get_toolkit_methods(), assistant, user, project)`, `VirtualAssistantService.delete` in `finally`; import services lazily (circularity).

## Task 8: `tool.call` handler
**Files:** Create `codemie/service/script_tool_calls/handler.py`; Test `tests/codemie/service/script_tool_calls/test_handler.py`
Test-first: yes — success gives `{"result": ...}` plus `"http": {"status", "reason"}` only for `HttpResult`; malformed payload and schema errors give `bad_arguments` naming fields and kinds, never values; a raising tool gives `tool_failed` with sanitized, 300-char-capped cause; the confirmation mixin is never called; a sentinel logged inside `execute` is dropped by the guard (Task 5) and the handler's own log line holds only tool name, code, exception type.
- [ ] `TOOL_CALL_OP = "tool.call"`; `build_tool_call_handlers(context) -> Mapping[str, ToolCallHandler]`. Order: parse `{name, args}` -> authorize -> validate against `args_schema` when it is a pydantic class (call with provided fields only) -> `run_for_script` inside `suppress_call_logging()` -> envelope.

## Task 9: Carry handlers through runner and bridge
**Files:** Modify `code_executor/job_bridge.py:36-75`, `workspace/execute_workspace_script_tool.py:66-87,235-239`; Tests `test_job_bridge.py`, `tests/codemie_tools/data_management/workspace/test_execute_workspace_script_tool.py`
Test-first: yes — `JobToolCallBridge.start` passes the options' handlers to `ToolCallChannel`; a handler run on the channel thread sees the request thread's contextvars and cannot change them for the next call.
- [ ] `JobBridgeOptions.handlers: Mapping[str, ToolCallHandler] | None = None`, `new_job_bridge_options(timeout, handlers=None)`; `start` captures `contextvars.copy_context()` and wraps each handler in `base.copy().run(...)`. `WorkspaceScriptRunner.tool_call_handlers` field feeds the options.

## Task 10: Wire context from tool and service; remove echo
**Files:** Modify `execute_workspace_script_tool.py:280-331` (field `script_registry: ScriptToolRegistry | None`), `workspace/toolkit.py:125-130`, `codemie/service/agent_workspace_service.py:503-525`, `tool_call_channel.py:92-100`; Tests `test_agent_workspace_service_tool_calling.py`, `test_tool_call_channel.py`, toolkit and REST tests
Test-first: yes — service builds handlers from `run_context` only when the timeout resolves, and a `None` context (REST `POST /workspaces/{id}/execute`, no change to the router) answers `no_context`; toolkit builds the registry with `workflow_project = assistant.project` only for a `VirtualAssistant` with `execution_id`; `DEFAULT_HANDLERS == {}`.
- [ ] `execute_workspace_script(..., run_context: ScriptRunContext | None = None)`; tool passes `script_registry.context()` or `without_context(user)`. Replace `test_default_registry_holds_only_echo` with `test_default_registry_is_empty`; give the channel tests a local `_echo` handler as `_channel()` default (line 176-180, 325).

## Task 11: Fill the registry at the end of `get_tools`; workflow kwarg
**Files:** Create `codemie/service/script_tool_calls/binding.py`; Modify `toolkit_service.py:466-602` (new kwarg `workflow_project: str | None = None`), `assistant_service.py:840-853` (pass `workflow_project=project_name`); Test `tests/codemie/service/tools/test_toolkit_service_script_registry.py`
Test-first: yes — final list minus excluded tools fills each script tool's registry (skill toolkit tools included, de-duped, `request_user_input` appended last is excluded); a chat request carrying `workflow_execution_id` stays `assistant` scope; the kwarg gives `workflow` scope for a stored assistant; an assistant without the script tool yields no script tool; no script tool in the list changes nothing.
- [ ] `bind_script_registries(tools, workflow_project)` called just before `get_tools` returns; unfilled registries stay closed.

## Task 12: Confirmation tests with the real script tool
**Files:** Test `tests/codemie/agents/tool_confirmation/test_script_tool_confirmation.py`
Test-first: yes — a real `ExecuteWorkspaceScriptTool` in `mixin.tools`: `ask_for_approval` and `approve_for_me` interrupt (pending saved, no auto-resume); `auto_approve` gives `require_tool_confirmation` False via `configure_agent_kwargs` (copy helpers from the two neighbouring test files).
- [ ] No production change; `is_safe` stays the default.

## Task 13: End-to-end unit checks through the real channel
**Files:** Test `tests/codemie/service/script_tool_calls/test_end_to_end.py` (reuse `LocalExecRunner` and SDK loader from `test_tool_call_channel.py`)
Test-first: yes — via SDK `call("tool.call")` against a fake assistant-scope tool: result above 30,000 tokens and under 256 KiB arrives whole; just above the cap gives `payload_too_large` promptly, also for non-ASCII text; two sequential calls get their own results; DEBUG capture on all handlers finds no sentinel from arguments, result or a tool that logs inside `execute`, on success, failure and oversize.

## Task 14: Author docs and rollout checklist
**Files:** Modify `code_executor/README.md:226-252`, `config/customer/customer-config.yaml:315-323`
Test-first: no — docs only.
- [ ] Rewrite the Workspace Script Bridge section: usage, envelope, codes, no token limit, 256 KiB cap on the encoded response, request escaping caveat, one call at a time, workflow-scope workspace-tool limit; replace the echo smoke with a real credential-free tool call. Add the rollout checklist in both places: network policy, `CODE_EXECUTOR_RUNTIME_CLASS_NAME` (default `gvisor`), stream/gateway idle timeouts above `timeoutSeconds` (API ingress 600 s; LiteLLM `LLM_PROXY_TIMEOUT` 300 s; others unverified).

## Task 15: Update story acceptance criteria
**Files:** Modify `docs/stories/2026-09-28-custom-tools/children/script-tool-calls/subtasks/2-tool-hookup/task.md:37,85` and `.../story.md:36,115-117`
Test-first: no — docs only.
- [ ] Replace "limited the same way as for the model" with: no token limit, full result up to the transport cap, above it `payload_too_large` with size and limit. Fix the stale "pooled" wording in `task.md` scope (runtime class applies to Jobs).

## Negative-constraint check
- No token limit/truncation on script path: Task 3 (`run_for_script`), Task 13. `_run` and model-path output unchanged: Tasks 3, 4 (byte-identical goldens). No per-tool `execute_structured` override: Task 3; sources sit on output types only (Task 4).
- No SDK/protocol/switch/cap changes beyond `ToolCallRefused`, message text, UTF-8 encoding: Task 1. MCP and stream tools refused: Tasks 6, 7. Request-supplied `workflow_execution_id` untrusted: Task 11. No content in logs: Tasks 5, 8, 13. No images: Task 4. Confirmation unchanged: Task 12. No pooled support, parallel calls or per-call deadline: none added.
