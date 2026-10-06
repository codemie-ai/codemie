# Technical Research

**Task**: toolkit service script tool bridge workspace code-execution authorizer
**Generated**: 2026-09-30
**Research path**: filesystem

---

## 1. Original Context

Sub-task 2 of 2 "Connect the Bridge to Platform Tools" (EPMCDME-15401), repo /Users/yanaasadchaya/Projects/epam/airun/codemie-dev/codemie, branch EPMCDME-15401_script-tool-calls-sdk. Full requirements are in /Users/yanaasadchaya/Projects/epam/airun/codemie-dev/codemie/docs/stories/2026-09-28-custom-tools/children/script-tool-calls/subtasks/2-tool-hookup/task.md (read it; parent story at ../../story.md; sub-task 1 at ../1-channel-and-sdk/task.md is ALREADY IMPLEMENTED on this branch — ~54 commits ahead of main: channel, SDK, switch, tool-calling time limit, size cap, cleanup, Job tool-call bridge extracted from BatchJobRunner, echo stand-in handler, workspace script bootstrap module, timeout clamp module). Summary of requirements: Goal — a script (in the code-execution workspace) can call platform tools available to it and use real results, replacing the echo stand-in handler; authorized by where the script runs, run with the same settings the model would use, confirmed exactly as running a script is today. Scope — (1) pass run context (user, assistant or workflow, conversation or execution) from ToolkitService/workspace service down to the runner; for assistants hand the runner the run's tools from where the agent tool list is assembled (ToolkitService.get_tools incl. attached skills' toolkits), excluding the script tool, MCP tools and chat-stream-dependent tools; (2) authorizer: script-execution tool blocked; assistant scope = the run's tools; workflow scope = any tool user is authorized for, resolved by name with user's integrations in the workflow's project (workflow step runs under temp assistant with one tool, so recognize workflow from the run/execution, not the assistant); no context => refuse; (3) perform call through the tool's normal entry point (validates config, executes, limits output to 30,000 tokens); errors returned to script as clear errors; (4) keep script-execution tool confirmation unchanged, with tests under each tool-call policy (always / not-safe / auto-approve); (5) verify call request/result content is never logged at any level on this path; (6) replace echo handler, end-to-end checks, authors' docs for SDK and limits; (7) rollout checks per environment (network policy, runtime class of pooled pods, stream/gateway idle timeouts > tool-calling limit) written down with the switch. Acceptance criteria: real tool result usable in same run; multiple sequential calls each get own result; assistant scope enforced incl. attached skills + user authorization + assistant's own settings/credentials; workflow scope any user-authorized tool w/ user's integrations in workflow's project; confirmation policies unchanged; clear error for unavailable/blocked/failing tool; large results limited like for model; no-context run (e.g. direct request to run a workspace script) refuses; assistant without script-execution tool runs no script; no call content in logs. Out of scope: channel/SDK/switch/cleanup (done), skills/assistants/workflows carrying scripts, workflow script step, named tools, trace/analytics, MCP tools.

Additional instructions from the caller: pay particular attention to what already exists on this branch (git log main..HEAD, the bridge/echo handler/tool-calling modules) versus what must be added. The existing `docs/stories/.../2-tool-hookup/technical-analysis.md` is a pre-implementation story-level note and may be stale; verify against current code. Also identify: (a) local test infrastructure — the existing sanity test setup (how `sanity` tests are run, the LiteLLM proxy toggle), (b) integration/test-datasource setup available locally, (c) codemie-sdk repo (/Users/yanaasadchaya/Projects/epam/airun/codemie-dev/codemie-sdk) tests related to script tool calls / code execution / tool invocation.

---

## 2. Codebase Findings

Project: Python 3.12 / Poetry (`pyproject.toml`), FastAPI + SQLModel backend, LangChain/LangGraph agents, `kubernetes` client 35.0.0 (poetry.lock). Two packages under `src/`: `codemie` (platform) and `codemie_tools` (reusable toolkits). No `.codegraph/` index in this repo, so built-in search was used. Source of truth was current code; nothing below was inferred from the pre-implementation notes without checking.

### What already exists on this branch (sub-task 1 plus the jobs-mode refactor)

`git log main..HEAD` lists roughly 50 commits (local `main` is behind: unrelated tickets EPMCDME-13806, 15184, 15382, 10913, 15207, 15072, 13887 appear at the bottom). The bridge commits run from `f8ef0e0a6` (customer-config component) to `48f45372a` (qa-gates record). Two phases are visible:

1. Sub-task 1 built the channel for the pooled (`sandbox-shared`) runner.
2. A follow-up (`docs/superpowers/tasks/2026-09-30-script-tool-calls-jobs-mode-bridge/spec.md`) removed all pooled-mode bridge wiring and re-implemented the bridge only in `sandbox-jobs` mode, because "Pooled mode is not deployed anywhere; `sandbox-jobs` (the default) is the only mode in real use". The `WorkspaceScriptRunner` bridge path exists only in `_execute_sandbox_script_jobs`; `_execute_sandbox_script` (pooled) calls `_build_script_wrapper(..., exchange_dir=None)` and has no channel.

Existing bridge components (all present and tested):

- `src/codemie_tools/data_management/code_executor/runtime_sdk/codemie_runtime_sdk.py` - stdlib-only SDK. Public API is `call(op: str, payload: dict, timeout: float | None) -> object`, `ToolCallError(message, code)`. Protocol constants: `PROTOCOL_VERSION=1`, `BRIDGE_DIR_NAME=".codemie_bridge"`, `MAX_PAYLOAD_BYTES=256 KiB`, `CALL_TIMEOUT_SECONDS=100.0`, `POLL_INTERVAL_SECONDS=0.1`. Files `req.<id>.json` / `resp.<id>.json`, marker `channel_unavailable`. One call at a time. Nothing in the SDK is tool-specific: the op name is free text.
- `runtime_sdk/workspace_bootstrap.py` and `sandbox_guard.build_guarded_workspace_script(..., exchange_dir=...)` - inject the SDK into the wrapper the pod executes.
- `code_executor/tool_call_channel.py` (475 lines) - `ToolCallChannel(runner, exchange_dir, *, poll_interval, attempts, backoff_seconds, handlers=None)`; daemon thread `codemie-tool-call-channel` polls via pod-side `python3 -I` scripts (`pod_scripts/poll.py`, `write_response.py`, `sweep.py`, `kill.py`). `KubernetesExecRunner` is one k8s exec per call on a private client. The echo stand-in is `_echo(payload) -> dict(payload)` registered as `DEFAULT_HANDLERS = {"echo": _echo}`. `ToolCallHandler = Callable[[Mapping[str, object]], object]`: a handler receives only the payload dict (not the op, the call id, or any run context) and returns a JSON-serialisable object. `_answer` maps only `TypeError`/`ValueError` from a handler to `bad_request` ("operation ... produced no valid result"); any other exception falls to `_build_response`, which returns `internal_error` / "the request could not be processed". There is no typed handler-error path today, so a handler cannot return a specific clear error message or code to the script.
- `code_executor/job_bridge.py` - `JobBridgeOptions(exchange_dir, tool_calling_timeout)` (frozen dataclass), `new_job_bridge_options(timeout)`, `JobToolCallBridge(options, *, namespace, kubeconfig_path)` whose `start(pod_name, workdir)` builds `KubernetesExecRunner` (container `"executor"`) and `ToolCallChannel(exec_runner, exchange_dir)` with no `handlers` argument, so the echo default is what serves real Job runs. `close()` stops and cleans up (`kill=False`). `is_bridge_path` keeps the bridge folder out of exports and changed files.
- `code_executor/batch_job_runner.py` - `BatchJobRunner.run(code, input_files, export_files, workdir, baseline_hashes, *, bridge: JobBridgeOptions | None)`. With a bridge it widens `activeDeadlineSeconds` and the wait deadline to `max(execution_timeout, tool_calling_timeout) + buffer`, starts the channel once the pod is Running, stops and cleans it before exports and the changed-file snapshot, and closes it again in `finally`.
- `code_executor/tool_calling_limits.py` - `clamp_tool_calling_timeout` (`MAX_TOOL_CALLING_SECONDS = 3600.0`; non-positive or non-finite turns tool calling off).
- `src/codemie/service/workspace_script_bridge.py` - `resolve_tool_calling_timeout()` reads customer-config component `features:workspaceScriptBridge` on every call (`enabled`, `timeoutSeconds`, default 120.0); returns `None` when off. Declared in `customer_config_declarations.py` as `WORKSPACE_SCRIPT_BRIDGE` (admin-editable) and in `config/customer/customer-config.yaml` (default `enabled: false`, `timeoutSeconds: 120`); `FEATURE_WORKSPACE_SCRIPT_BRIDGE` env overrides `enabled`.
- `src/codemie/service/agent_workspace_service.py::execute_workspace_script(workspace_id, script_path, user, export_files)` builds `WorkspaceScriptRunner(file_repository, user_id, input_files, conversation_id, tool_calling_timeout=resolve_tool_calling_timeout())`. No assistant, workflow, project or tools argument exists on it.
- `src/codemie_tools/data_management/workspace/execute_workspace_script_tool.py` - `WorkspaceScriptRunner(CodeExecutorTool)` with fields `conversation_id`, `tool_calling_timeout`; `ExecuteWorkspaceScriptTool(CodeMieTool)` with fields `conversation_id`, `user`, `workspace_service`, `workspace_id`; its `execute` calls `workspace_service.execute_workspace_script(...)`. The tool does not override `is_safe`, so `CodeMieTool.is_safe` returns `False`.
- Documentation: `src/codemie_tools/data_management/code_executor/README.md` section "Workspace Script Bridge" says "the only handler in this version is `echo`" and gives an echo smoke script. There are no author-facing docs for real tool calls.

### What does not exist yet (verified by grep)

- No run-context type, no authorizer, no handler that resolves or invokes a tool, no op name for tool calls, no typed error path from handler to script. `grep` finds `echo`/`DEFAULT_HANDLERS` only in `tool_call_channel.py` and its tests.
- `ToolkitService.get_tools` does not pass anything to the script tool; `ToolkitSettingService.get_agent_workspace_toolkit` / `AgentWorkspaceToolkit.get_toolkit` accept `assistant` and `request` but `AgentWorkspaceToolkit.get_tools()` constructs `ExecuteWorkspaceScriptTool(conversation_id, user, workspace_service, workspace_id)` only.
- No test of script-tool confirmation under `ask_for_approval` / `approve_for_me` / `auto_approve`; existing confirmation tests are generic and use mocks.

### Architecture and Layers Affected

Call chain today for an assistant run, top to bottom:

1. `AssistantService.build_agent` (`service/assistant_service.py:500`) calls `ToolkitService.get_tools(assistant, request, user, llm_model, request_uuid, is_react, thread_generator, file_objects=..., ...)` and passes the returned list to the agent.
2. `ToolkitService.get_tools` (`service/tools/toolkit_service.py:466`): `_merge_skill_toolkits(assistant)` (assistant toolkits plus attached skills' toolkits, de-duplicated by toolkit name), `_augment_toolkits_with_feature_flags`, `get_core_tools`, `add_context_tools`, skill tools, file tools, `_get_tools` (which runs `add_tools_with_creds`, MCP tools when `MCP_CONNECT_ENABLED`, then `_process_final_tools_traditional`: de-dupe by name, `ToolsPreprocessorFactory` chain), then `_append_workspace_image_tool_if_enabled` and `_append_request_user_input_tool_if_enabled`, which returns the final list.
3. The script tool is created inside step 2, in `add_tools_with_creds` -> `get_toolkit_methods()[AGENT_WORKSPACE_TOOLKIT]` -> `ToolkitSettingService.get_agent_workspace_toolkit` (`service/tools/toolkit_settings_service.py:234`, returns `[]` when the request has no `conversation_id`) -> `AgentWorkspaceToolkit.get_tools()`, then `filter_tools` keeps only tools the assistant configured. So the script tool exists in a run only if the assistant (or an attached skill) configured `execute_workspace_script`, and it is built before the final tool list is complete.
4. Execution: agent calls `ExecuteWorkspaceScriptTool.execute` -> `AgentWorkspaceService.execute_workspace_script` -> `WorkspaceScriptRunner.execute_script` -> `_execute_sandbox_script_jobs` -> `BatchJobRunner.run(bridge=...)` -> `JobToolCallBridge` -> `ToolCallChannel` thread.

Layers involved by the task: service (`codemie/service/tools/*`, `agent_workspace_service`, `assistant_service` for workflow entry), agent-tool layer (`codemie_tools/data_management/workspace`, `code_executor`), and the workflow layer for the workflow scope. No tables, no migrations, no UI, no sandbox image change were found to be involved by current code.

Workflow paths (relevant to "recognize workflow from the run"):

- Assistant-mediated step: `AssistantService.build_agent_for_workflow` (`assistant_service.py:772`) builds `AssistantChatRequest(conversation_id=execution_id, workflow_execution_id=execution_id, tools_config=...)` and calls `ToolkitService.get_tools`. The assistant is either a stored `Assistant` (when `workflow_assistant.assistant_id` is set; its `project` is the assistant's own) or a `VirtualAssistant` (has `execution_id`, `project` = workflow project). `project_name` (the workflow's project) is a parameter of `build_agent_for_workflow` but is not passed on to `ToolkitService.get_tools`, and it is not on the request. So for a stored assistant inside a workflow, the workflow's project is not reachable from `get_tools` arguments.
- Bare tool step: `ToolNode._execute_regular_tool` (`workflows/nodes/tool_node.py:178`) creates `VirtualAssistantService.create_from_tool_config(...)` (one toolkit, one tool, `execution_id`), resolves the tool with `ToolsService.find_tool_from_config(tool_config, toolkits, assistant, user, workflow_config.project, owner_user_id, execution_id)` and calls `tool.execute(**args)` through `_execute_tool_with_args`. In `find_tool_from_config` the fallback request is `AssistantChatRequest(tools_config=config, conversation_id=execution_id)`; it does not set `workflow_execution_id`. So on this path the only workflow signal reaching the toolkit method is `conversation_id == execution_id` plus `assistant.execution_id` on the `VirtualAssistant`.
- The harness proves the bare-step script path exists: `test_workflow_execute_workspace_script_generates_simple_deck_presentation` (EPMCDME-15167) runs `execute_workspace_script` from a bare workflow tool state.

Direct/no-context paths:

- REST `POST /workspaces/{workspace_id}/execute` (`rest_api/routers/agent_workspace.py:256`) calls `workspace_service.execute_workspace_script` with only user, script path and exports. That path gets `tool_calling_timeout=resolve_tool_calling_timeout()` too, so with the switch on a script run this way reaches the channel with no assistant or workflow context.
- `ToolExecutionService.invoke_tool_with_system_integration` (direct invocation) builds a `VirtualAssistant` for one tool and reaches `toolkits[toolkit](assistant, user, '', False, '')` with `request=''`; `get_agent_workspace_toolkit` returns `[]` for a falsy request, so the script tool cannot be built from that path.

The two by-name resolutions already in the codebase:

- `ToolsService.find_tool_from_config` / `find_tool_by_invoke_request` (`service/tools/tool_service.py:84,119`): `ToolsService.get_toolkit(tool_name, user, project_name, integration_alias)` (uses `find_toolkit_for_tool(user, name)` over `_get_available_toolkits(user)`, and `find_setting_for_tool(user, project, alias)` only when an alias is given), then `ToolkitService.get_core_tools(...)` with `SettingsService.get_config(user_id, project_name, assistant_id, tool_config, ...)` (lookup by user, project, integration id or alias), falling back to the toolkit factory lambda. `ToolsService.find_tool(name, tools)` raises `ValueError(TOOL_NOT_FOUND_ERROR...)` when absent.
- `ToolExecutionService.get_tool_with_system_integration` / `invoke_tool_with_system_integration` (`service/tools/tool_execution_service.py`): temporary single-tool `VirtualAssistant` in `request.project`, `validate_tool_args`, then `execute_tool` which calls `tool.execute(**args)` when present, else `tool.invoke(args)`. It does not apply an assistant's configuration. Its `finally` deletes the virtual assistant and clears request summary metrics.
- Not verified in code: whether `SettingsService._lookup_setting` returns a project-level or global integration in addition to the user's own for a workflow's project (only the signature and call sites were read). The `tools_config`/alias path is verified.

The normal entry point (`codemie_tools/base/codemie_tool.py`):

- `CodeMieTool._run(*args, **kwargs)`: `_validate_config()` -> `execute(...)` -> `_limit_output_content` (token limit `tokens_size_limit = config.TOOL_TOKENS_SIZE_LIMIT = 30000`, appends "Tool output is truncated." text, or raises `TruncatedOutputError` when `throw_truncated_error`) -> `_post_process_output_content` (non-strings JSON-serialised). Any exception is re-raised as `ToolException`.
- `handle_tool_error = True` on `CodeMieTool`: through `BaseTool.invoke`/`run` LangChain converts a `ToolException` into a returned error string instead of raising. Calling `_run` directly raises. `ToolExecutionService.execute_tool` and `ToolNode._execute_tool_with_args` call `tool.execute(...)` (which skips validation and the token limit; the node applies `apply_tokens_limit` separately only when a tool node sets `tokens_size_limit`).
- Workflow tool nodes with `limit_tool_output_tokens` call `AssistantService.propagate_token_limit(assistant, tools, N)`; for MCP the limit is `MCP_TOOL_TOKENS_SIZE_LIMIT = 30000`.

Confirmation:

- `ToolCallPolicy` (`core/models.py:608`) values: `ask_for_approval`, `approve_for_me`, `auto_approve`. `ToolPermissionsService.get_effective_permissions` (`service/tool_permissions_service.py`) resolves the policy; when customer feature `tool_permissions` is disabled it returns `AUTO_APPROVE`; a customer `min_tool_call_policy` floor clamps stricter.
- `ToolCallConfirmationMixin.ask_for_tool_confirmation` (`agents/tool_confirmation/tool_call_confirmation_mixin.py:42`): under `APPROVE_FOR_ME` it auto-resumes only when `all(tool.is_safe(args))` over the agent's `self.tools`; otherwise it saves a pending tool call and interrupts. The script tool has default `is_safe -> False`, so it is confirmed under both approval policies and not under auto-approve. Nothing on the bridge path calls into the confirmation layer.
- Existing tests: `tests/codemie/agents/tool_confirmation/test_ask_for_tool_confirmation_approve_for_me.py` (mock tools, no script tool), `tests/codemie/service/test_tool_permissions_service.py`, `tests/codemie/service/assistant/test_configure_agent_kwargs_confirmation.py`, `tests/codemie/agents/test_langgraph_tool_confirmation.py`.

### Integration Points

- Internal: `AssistantService` and `ToolNode` -> `ToolkitService` / `ToolsService` -> `ToolkitSettingService` -> `AgentWorkspaceToolkit` -> `ExecuteWorkspaceScriptTool` -> `AgentWorkspaceService` -> `WorkspaceScriptRunner` -> `BatchJobRunner` -> `JobToolCallBridge` -> `ToolCallChannel`. Dependency direction: `codemie_tools/data_management/code_executor/*` (channel, job_bridge, batch_job_runner) import nothing from `codemie`; `execute_workspace_script_tool.py` in `codemie_tools` already imports `codemie.rest_api.models`/`security.user`, and `codemie/service/workspace_script_bridge.py` is the existing pattern for a `codemie`-side helper the tools layer consumes.
- Threading: `ToolCallChannel` runs handlers synchronously on its own plain `threading.Thread`, one request at a time, inside `_respond`; `stop()` joins for up to 10 s (`STOP_JOIN_TIMEOUT_SECONDS`). Python contextvars are not copied into a plain `Thread`. The codebase keeps request-scoped state in contextvars (`core/dependecies.py`: `litellm_context`, `dial_credentials`, `disable_prompt_cache`; `configs/logger.py`: `logging_uuid`, `logging_user_id`, `logging_conversation_id`, with `copy_logging_context` / restore helpers for forked threads) and `set_llm_context(assistant, None, user)` is called in `build_agent` / `build_agent_for_workflow` on the request thread.
- External: Kubernetes API (exec into the Job pod, container `executor`; Job/pod RBAC in `deploy-templates/templates/code-executor-rbac.yaml`: pods create/get/list/watch/delete, `pods/exec` create/get, `pods/log` get, `jobs` verbs, in the executor namespace).
- Excluded tool categories the task names: MCP tools (`MCPToolkitService.get_mcp_server_tools`, classes `MCPTool` / `ContextAwareMCPTool` in `service/mcp/toolkit.py`, only added when `config.MCP_CONNECT_ENABLED`), and stream-dependent tools (`RequestUserInputTool` in `agents/tools/interactive/request_user_input.py`, which holds `thread_generator`, appended last in `get_tools`; `agents/tools/agent.py` and `code_toolkit.py` also accept a `thread_generator`). LangGraph handoff tools are added later inside `langgraph_agent.py:418`, outside the `get_tools` list.

### Patterns and Conventions

- Guides say check `.ai-run/guides/` first: layered architecture, service-layer patterns, agent tools, error handling, logging, security, testing (see Section 3).
- `CLAUDE.local.md` mandates maximal strict typing in new code: full signatures, `X | None`, no `Any`, `TypedDict`/dataclass/Pydantic over bare dicts. The bridge modules follow this: `from __future__ import annotations`, frozen dataclasses (`JobBridgeOptions`), `Protocol` (`ExecRunner`), `@final`.
- Small single-purpose modules with a paired test file (`tool_calling_limits.py`, `job_bridge.py`, `workspace_script_bridge.py`); license header on every file (checked by `make license-check`).
- Tools are `CodeMieTool` subclasses with `execute`; per-tool `is_safe(args)` for the confirmation layer; `filter_tools` gates by assistant configuration.
- Log messages on the bridge path use `key=value` style with type names only (`tool_call_channel: request %s failed: %s`, `type(exc).__name__`), never payloads.

---

## 3. Documentation Findings

### Guides and Architecture Docs

`.ai-run/guides/` exists. Relevant: `agents/tool-overview.md` and `agents/agent-tools.md`, `agents/custom-tool-creation.md`, `architecture/layered-architecture.md`, `architecture/service-layer-patterns.md`, `development/error-handling.md`, `development/logging-patterns.md` ("avoid leaking secrets or tokens"), `development/security-patterns.md`, `integration/external-services.md`, `testing/testing-patterns.md`, `testing/testing-service-patterns.md`, `quality-gates.md`, `development/local-testing.md` (run tests only when asked; report missing prerequisites as blocked, not failed). `AGENTS.md` rule: tests are run or written only when the user explicitly asks; git operations likewise.

Other docs for this feature: story `docs/stories/2026-09-28-custom-tools/children/script-tool-calls/story.md` and both sub-task `task.md` files; `docs/superpowers/tasks/2026-09-29-script-tool-calls-channel-sdk/` and `2026-09-30-script-tool-calls-jobs-mode-bridge/` (spec, plan, technical-analysis, task reports, gate runs); `code_executor/README.md` (Workspace Script Bridge section).

### Architectural Decisions

- Story/task text: the call is performed on the same tool instances the agent built (assistant) or by name for the user (workflow), through the tool's normal entry point; the approval of running a script stands for its calls; the script tool is blocked from calling itself; MCP tools out of scope; SDK is one module injected by the backend.
- Jobs-mode spec (2026-09-30): bridge exists only for `sandbox-jobs`; ordering hard requirement is script sentinel, then `channel.stop()`, then `cleanup(kill=False)`, then exports; the bridge folder is excluded from changed files; explicit non-goal in that spec: "Sub-task 2's tool-dispatch work (real handler, run-context, authorization); the echo handler stays", and "Sub-task 2's task.md still assumes the bridge works end-to-end; needs its own update."

### Derived Conventions

- Pre-implementation note `.../2-tool-hookup/technical-analysis.md` checked against code: accurate on where `get_tools` assembles the list, on the runner receiving only `user_id`/`conversation_id` (now also `tool_calling_timeout`), on the script tool's default `is_safe`, on `CodeMieTool._run`, and on the direct invocation service. Stale or incomplete: it does not mention that the bridge is jobs-mode only; it does not note that the script tool is built before the list is final (inside `add_tools_with_creds`); it lists its "Not verified" items, of which this research resolved: the workflow step's execution id does not reach the toolkit as `workflow_execution_id` on the bare-step path (only `conversation_id` and `VirtualAssistant.execution_id`), and payload logging on the path (see Section 6).
- Stale wording in `task.md` and `story.md`: "runtime class of the pooled pods" and "Environments run the sandbox in the pooled mode" no longer match the code. The runtime class knob that applies to the bridge is `CODE_EXECUTOR_RUNTIME_CLASS_NAME` (default `gvisor`; setting `none`/empty omits `runtimeClassName`), used in `BatchJobRunner._build_manifest`.
- README mismatch: root `README.md:159` says `make test-harness` runs `uvx codemie-test-harness --sanity`, but the `Makefile` target runs `--sanity-api`. `quality-gates.md` also refers to a "setup guide" ENV=local Bearer-hijack patch that `.ai-run/guides/development/setup-guide.md` does not contain.
- Branch naming: `.state.json` in the run dir records branch `EPMCDME-15402_script-tool-calls-tool-hookup`, while the current git branch is `EPMCDME-15401_script-tool-calls-sdk`.

---

## 4. Testing Landscape

### Existing Coverage

Bridge and neighbours (all present on the branch):

- `tests/codemie_tools/data_management/code_executor/test_tool_call_channel.py` (~1000 lines): protocol against a local-directory runner and the real SDK; `test_default_registry_holds_only_echo` asserts `set(DEFAULT_HANDLERS) == {"echo"}`, so replacing the echo handler changes this test.
- `test_job_bridge.py`, `test_batch_job_runner.py` (ordering: stop and cleanup after sentinel, before export/snapshot; bridge folder excluded), `test_pod_scripts.py`, `test_sandbox_guard.py`, `test_sandbox_dispatch.py`, `test_session_manager.py`, `test_tool_calling_limits.py`, `runtime_sdk/test_codemie_runtime_sdk.py` and `test_workspace_bootstrap.py` (SDK import allowlist, size cap, clean-interpreter check).
- `tests/codemie_tools/data_management/workspace/test_execute_workspace_script_tool.py` (runner options, timeout clamp, jobs-mode bridge options).
- `tests/codemie/service/test_agent_workspace_service_tool_calling.py` (runner receives the resolved timeout; `WorkspaceScriptRunner` patched), `test_workspace_script_bridge.py`, `tests/codemie/configs/test_customer_config.py`, `tests/codemie/service/test_customer_config_declarations.py`.
- Tool assembly and resolution: `tests/codemie/service/tools/test_toolkit_service.py` (many `ToolkitService.get_tools` cases with mocks), `test_toolkit_service_file_tools.py`, `test_toolkit_settings_service.py`, `test_find_tool_from_config_execution_id.py`, `test_tool_service_owner_user_id.py`, `test_tool_execution_service.py`, `test_tool_execution_invoke.py`, `test_missing_integration_tool.py`.
- Confirmation: the four files listed in Section 2. None uses the real script tool.

### Testing Framework and Patterns

pytest 8.3.3 with `-n 2` (xdist) and `--import-mode=importlib`, `pythonpath = src`, env `ENV=local` (`pytest.ini`); `make test` = `poetry run pytest tests/`. Patterns: `unittest.mock` (`MagicMock`, `patch.object(module, "Symbol")`), `AgentWorkspaceService.__new__` with attribute injection to avoid DB, fixtures that drive the channel's real argv against a temp directory, `MagicMock` tools with `is_safe` return values for confirmation tests. Conventions in new tests on this branch: module docstring, `from __future__ import annotations`, strict typing, parametrised policies.

### Coverage Gaps

- No test that asserts what a real (non-mock) `ExecuteWorkspaceScriptTool` does under each `ToolCallPolicy` (the task requires it).
- No test for run-context propagation or for the workflow-recognition signals on the bare-step path.
- No log-capture test on the bridge path (`caplog` at DEBUG for payload strings). `CodeMieTool._run` and `_parse_input` log arguments; neither is covered by a "no content in logs" assertion.
- No test that a handler exception surfaces a specific error message to the script (the channel only produces generic `internal_error`).
- No end-to-end test with a real cluster; the channel is tested against a local directory, and Kubernetes exec is mocked.

### Local test infrastructure asked for by the caller

(a) Sanity tests and the LiteLLM proxy toggle
- The backend repo has no `sanity` pytest marker or suite of its own. "Sanity" is the `codemie-test-harness` package in the sibling repo (`codemie-sdk/test-harness`). Backend `make test-harness` runs `uvx codemie-test-harness --sanity-api`, which selects marks `api and sanity` (8 workers, 2 reruns), defined in `codemie_test_harness/cli/constants.py` (`TEST_SUITES`: `sanity`, `sanity-api`, `sanity-ui`); marker declared in `pyproject.toml` and `codemie_test_harness/pytest.ini`. README says the sanity suite needs no AWS/integration credentials. Config comes from `~/.codemie/test-harness.json` or a `.env` in `codemie_test_harness/` (`CODEMIE_API_DOMAIN=http://localhost:8080` for local). `quality-gates.md` requires the terminal summary in the MR under `## Test harness`; the `/sanity` regression run is a reviewer comment on the MR.
- Prerequisites for local runs (from `quality-gates.md` and `docker-compose.yml`): `docker compose up -d` (services: codemie, postgres, elasticsearch, litellm, kibana, jaeger, prometheus, grafana, otelcollector, clickhouse, pyroscope, mermaid-server), superadmin fixtures (`SUPERADMIN_EMAIL`, `SUPERADMIN_PASSWORD` in `.env.local`).
- LiteLLM proxy toggle: `LLM_PROXY_ENABLED` (bool, default `False`) and `LLM_PROXY_MODE` (`internal` | `lite_llm`, default `internal`) in `src/codemie/configs/config.py:670`; `.env.local` currently has `LLM_PROXY_MODE=lite_llm`, `LLM_PROXY_ENABLED=True` with a commented `#LLM_PROXY_ENABLED=False` line, and `LITE_LLM_URL` / `LITE_LLM_APP_KEY` / `LITE_LLM_MASTER_KEY` pointing at a preview proxy (values not reproduced here). Setting `LLM_PROXY_ENABLED=False` disables the proxy entirely and models come from the YAML config with the Azure OpenAI key/URL already in `.env.local`. The compose stack also has a local `litellm` service (`ghcr.io/berriai/litellm-database:1.96.2`, config `litellm_config.yaml`).
- The script bridge cannot be exercised by `docker compose` alone: it needs a Kubernetes cluster reachable from the backend (`CODE_EXECUTOR_*` settings, `CODE_EXECUTOR_ENABLED=true` for the code-executor tool, kubeconfig via `CODE_EXECUTOR_KUBECONFIG_PATH`), the `codemie-code-executor` namespace with the RBAC above, and `features:workspaceScriptBridge` enabled (`FEATURE_WORKSPACE_SCRIPT_BRIDGE=true` or admin settings). The jobs-mode spec's smoke check was a manual "upload an SDK script to a chat and ask the assistant to run it".
- Unit gate evidence from the previous run (`.../2026-09-30-script-tool-calls-jobs-mode-bridge/gate-run.json`): `make ruff`, `make build`, `make license-check`, `make gitleaks` (needs Docker), `make test` (16959 passed, 175 skipped), `make sonar-local` (stale `coverage.xml` caveat because `SONAR_SKIP_TESTS` is set in `.env.local`), commit-msg hook (every commit needs an `EPMCDME-<n>:` prefix), pre-push hook. `make test-harness` was recorded N/A ("still owed before the MR").

(b) Integration and test-datasource setup available locally
- Backend: no fixtures create integrations or datasources for local runs; `local/integations_and_tools.md` exists but is empty (0 bytes). `local/` is gitignored (verified with `git check-ignore`).
- Harness: integrations are created per test through `integration_utils` and loaded from AWS Parameter Store credentials via `CredentialsManager` (README: AWS is required for every suite except sanity; otherwise integrations must be put into `.env`). Default-integration precedence (user vs project vs global) is covered by `tests/assistant/default_integrations/*` and `tests/workflow/direct_tools_calling/default_integrations/*`. Datasource fixtures live under `tests/assistant/datasource/` and `test_data/`; the local stack provides postgres and elasticsearch for data-management tools.
- Tools with no external credentials that could serve as the "real tool" in an end-to-end check: the Research toolkit (Wikipedia and web tools are used by the harness confirmation tests), the workspace tools themselves (`list_workspace_files`, `read_workspace_file`, `write_workspace_file` are already used from bare workflow states), and the platform toolkit. `TAVILY_API_KEY` and `GOOGLE_SEARCH_*` are read from `.env.local`/config for the other research tools.

(c) `codemie-sdk` repo (`/Users/yanaasadchaya/Projects/epam/airun/codemie-dev/codemie-sdk`, branch `main`, clean)
- Nothing in it covers script tool calls: no reference to `codemie_runtime_sdk`, `ToolCallChannel` or the bridge. The Python SDK (`sdk/codemie-python`) has no tool-invocation service in `src/codemie_sdk/services/` (list: assistant, workflow, datasource, integration, skill, files, llm, ... no `tool.py`); the Node SDK likewise. Note `codemie-sdk` here means the client SDK; the script-side SDK is `codemie_runtime_sdk` in the backend repo.
- Related harness tests (under `test-harness/codemie_test_harness/tests/`): `workflow/direct_tools_calling/test_workflow_with_workspace_tools.py` (EPMCDME-14138 bare states share one workspace; EPMCDME-15167 `execute_workspace_script` from a bare workflow state with `write_workspace_file` then `execute_workspace_script`, markers `workflow`, `direct_tool`, `workspace`, `api`); `enums/tools.py::AgentWorkspaceTool` (includes `EXECUTE_WORKSPACE_SCRIPT = "execute_workspace_script"`); `assistant/tool_permissions/` (`conftest.py` builds assistants with `ToolPermissionsConfig(tool_call_policy=ASK_FOR_APPROVAL, allow_override=True)` and a NDJSON stream reader; `test_tool_call_resume.py`; EPMCDME-13903 / 15182) and `ui/chats/test_tool_call_permissions.py`; `assistant/test_sub_assistants.py` and `ui/workflows/test_code_executor_tool_config.py` (code executor); `assistant/default_integrations/` and `workflow/direct_tools_calling/default_integrations/` (integration selection by user/project/global). Existing harness tests are natural templates for an end-to-end check in a chat, an assistant and a workflow; none uses the script bridge.

---

## 5. Configuration and Environment

### Environment Variables

- `FEATURE_WORKSPACE_SCRIPT_BRIDGE` - overrides `enabled` of `features:workspaceScriptBridge` at load time, only where the component exists in the loaded customer config; process env wins over `.env.local` (commit `72a20aa4e`).
- `CODE_EXECUTOR_SANDBOX_MODE` - `sandbox-jobs` (default) or `sandbox-shared`; the bridge runs only in jobs mode on this branch.
- `CODE_EXECUTOR_EXECUTION_TIMEOUT` (default 30 s), `CODE_EXECUTOR_NAMESPACE` (`codemie-code-executor`), `CODE_EXECUTOR_RUNTIME_CLASS_NAME` (`gvisor`, `none`/empty omits it), `CODE_EXECUTOR_TOLERATIONS`, `CODE_EXECUTOR_KUBECONFIG_PATH`, `CODE_EXECUTOR_MAX_POD_POOL_SIZE` (5; also the Job capacity semaphore), `CODE_EXECUTOR_MEMORY_LIMIT` / `CPU_LIMIT` / `MAX_THREADS` / `MAX_OPEN_FILES`, `CODE_EXECUTOR_ENABLED` (code-executor tool opt-in) - see README tables and `code_executor/models.py`.
- `TOOL_TOKENS_SIZE_LIMIT` (30000) and `MCP_TOOL_TOKENS_SIZE_LIMIT` (30000) in `codemie/configs/config.py:551`.
- `LLM_PROXY_ENABLED`, `LLM_PROXY_MODE`, `LITE_LLM_URL`, `LITE_LLM_APP_KEY`, `LITE_LLM_MASTER_KEY` - see Section 4(a). `MCP_CONNECT_ENABLED` decides whether MCP tools exist in the list.
- `CUSTOMER_CONFIG_CACHE_TTL_SECONDS` - how soon other replicas pick up admin-saved bridge settings.

### Configuration Files

- `config/customer/customer-config.yaml` - `features:workspaceScriptBridge` (`enabled: false`, `timeoutSeconds: 120`); also `tool_permissions` feature (`min_tool_call_policy` floor) governs confirmation.
- `src/codemie/service/customer_config_declarations.py` - admin-editable declaration of the bridge component.
- `deploy-templates/values.yaml` / `templates/code-executor-rbac.yaml` / `code-executor-namespace.yaml` - executor namespace and RBAC (off by default: `rbac.enabled: false`, `namespace.create: false`); ingress annotation `nginx.ingress.kubernetes.io/proxy-read-timeout: "600"` on the API ingress (also on three more ingresses at lines 354, 370, 453 of `values.yaml`).
- `docker-compose.yml`, `litellm_config.yaml`, `.env.example`, `.env.local` (gitignored, contains credentials - do not copy), `pytest.ini`.

### Feature Flags and Deployment Concerns

- The switch is `features:workspaceScriptBridge.enabled`, read on every script run (no restart). `resolve_tool_calling_timeout()` returns `None` when off; then `WorkspaceScriptRunner` builds no bridge.
- Rollout checks named in `task.md`, and what the repo shows today:
  - Network policy: no `NetworkPolicy` template exists in `deploy-templates` and none was found in `codemie-helm-charts` (only `codemie-runtime/values.yaml` and `codemie-api/values-*.yaml` mention the executor). The Job manifest sets `hostNetwork: False`; whether egress is blocked is a cluster concern not expressed in this repo. Not verifiable from the filesystem.
  - Runtime class: `CODE_EXECUTOR_RUNTIME_CLASS_NAME`, default `gvisor` (see above); README describes gVisor in JOBS mode as the actual boundary against native code.
  - Idle timeouts: API ingress `proxy-read-timeout: 600` s versus the tool-calling limit (`timeoutSeconds` default 120, clamp maximum 3600 s in `tool_calling_limits.py`). No SSE keepalive/heartbeat code was found in `routers/assistant.py` or `assistant_service.py`. Gateway and stream timeouts outside this repo (customer ingress, API gateway, LiteLLM `LLM_PROXY_TIMEOUT` 300 s in `config.py:682`) were not inspected.
  - Executor capacity: each bridge Job holds its semaphore slot for up to the widened budget.
- Secrets: no new secret is involved by the described work; tool credentials are resolved through `SettingsService` for each tool as today.

---

## 6. Risk Indicators

Findings (facts about the current code that shape risk):

- Payload logging exists on the normal entry point. `CodeMieTool._run` logs `Arguments: {kwargs}` at ERROR in its failure path (`codemie_tool.py:88-93`, and puts the same string in the raised `ToolException`); `CodeMieTool._parse_input` logs "Tool input: ..." at ERROR; `_limit_output_content` logs token counts only. `ToolNode` logs "Tool node execution result: {result}" at DEBUG, but that is on the workflow node path, not the bridge. `ToolExecutionService` logs argument keys only (`sorted(request.tool_args)`). The channel and `JobToolCallBridge` log op names, exception type names and exec failure summaries (`ExecFailed` includes up to 200 chars of a pod-script's stderr), not payloads. `kubernetes` `ws_client.py` has no logging of frames; `kubernetes/client/rest.py:235` logs response bodies at DEBUG for REST calls only. So the requirement "never logged at any level" is not met if the bridge invokes `_run` and a tool fails.
- `handle_tool_error = True`: going through `invoke`/`run` returns the error text as a normal string, so a script cannot tell a failure from a result unless the caller detects it; going through `_run` directly raises `ToolException`.
- The channel's error model is closed: handler exceptions become `internal_error` with a fixed message, and only `TypeError`/`ValueError` become `bad_request`. Clear per-cause errors (unavailable, blocked, no context, tool failed) have no path today. The `ToolCallHandler` type sees only the payload, not the op or the run context.
- Handlers run on the channel's own thread, serialised, blocking further polling while a tool runs. `stop()` waits at most 10 s, then logs a warning and continues to cleanup with the thread possibly alive. The SDK's default call wait (100 s) is independent of the tool's own duration; the run limit (`timeoutSeconds`) is not enforced on handler execution. Contextvars (LLM context, logging ids) are not propagated into that thread.
- `JobToolCallBridge` is built inside `BatchJobRunner.run` from the frozen `JobBridgeOptions`; there is no field to carry a handler or run context to it.
- The script tool is created inside `add_tools_with_creds` before the list is final, then the list is de-duplicated and run through preprocessors; `get_tools` has two later `_append_*` steps. Any hand-over "at the end of `get_tools`" must locate the already-built script tool instance in the final list.
- Workflow signals are inconsistent: the assistant-mediated path sets `request.workflow_execution_id` but the bare-step fallback request in `ToolsService.find_tool_from_config` sets only `conversation_id=execution_id`; the workflow's project is not on the request and, for a stored assistant inside a workflow, `assistant.project` is the assistant's own.
- Two different resolution paths already exist by name (`find_tool_from_config`, `find_tool_by_invoke_request`/`invoke_tool_with_system_integration`); they differ in how they build the request (`AssistantChatRequest(...)` versus `''`/`False`), whether they set a temporary assistant's `project`, and whether `execute` or `invoke` is used.
- Direct REST run (`POST /workspaces/{id}/execute`) reaches the channel with the switch on and no context, so a refusal must be produced by the handler side, not by the absence of the bridge.
- `test_default_registry_holds_only_echo` and the README echo smoke script pin the stand-in and will need to change with it.
- The bridge works only in `sandbox-jobs` mode; task text and story text still describe pooled mode. `AGENTS.md` prohibits running tests unless asked, so no gate was run during this research.
- Doc drift found: `README.md:159` vs `Makefile:74` (`--sanity` vs `--sanity-api`); `quality-gates.md` refers to a setup-guide patch that is absent; `.state.json` branch differs from the checked-out branch.
- Environment-level unknowns not checkable from files: network policy, gateway/stream idle timeouts beyond the API ingress's 600 s, whether the target clusters run `gvisor`.
- Harness gaps: nothing in `codemie-sdk` exercises the bridge; an end-to-end check needs a cluster with the executor namespace and RBAC, which the local compose stack does not provide.

Speculative (design that the spec and plan must decide, not discovered constraints):

- Speculative: the handler needs a new op name (the SDK and channel are op-agnostic) and, to give clear errors and see the op and run context, either a richer handler signature or a typed exception mapped by `ToolCallChannel._answer`.
- Speculative: `JobBridgeOptions` or `JobToolCallBridge` would need to carry the handler or its factory, and `WorkspaceScriptRunner` / `AgentWorkspaceService.execute_workspace_script` a run-context argument; `ExecuteWorkspaceScriptTool` and `AgentWorkspaceToolkit` would need to receive context and a late-bound holder for the run's tools.
- Speculative: calling the tool's `_run` (or a wrapper that suppresses argument logging) versus `invoke` decides both the error mapping and the logging outcome; the logging requirement may need a change in `CodeMieTool._run` or a log filter test.
- Speculative: capturing contextvars (`copy_logging_context` pattern, `litellm_context`) before the channel thread starts may be required for tools that call an LLM.
- Speculative: a workflow-project field is likely to be required on the run context, since neither `get_tools` arguments nor the request carry it.
- Speculative: size band drivers are the security-sensitive authorizer, the cross-layer threading (assistant service, toolkit service, workflow tool node, workspace service, runner, bridge), and the absence of any real-cluster test path.

---

## 7. Summary for Complexity Assessment

Layers touched: service (`ToolkitService`, `ToolsService`/`ToolExecutionService` resolution, `AgentWorkspaceService`, `AssistantService`/`ToolNode` as the sources of run context), the tools layer (`AgentWorkspaceToolkit`, `ExecuteWorkspaceScriptTool`, `WorkspaceScriptRunner`), and the code-executor bridge (`BatchJobRunner`, `JobToolCallBridge`, `ToolCallChannel`). The sub-task 1 work is confirmed present: SDK, channel, jobs-mode bridge, switch, tool-calling time limit, size cap, cleanup, echo handler. What is absent is everything that makes calls real: run context, the run's tool list hand-over, the authorizer, the handler that performs calls, typed error mapping to the script, confirmation tests for the script tool, the logging check, author docs and rollout notes. The change surface touches about eight existing production modules and adds a few new small ones; no schema, migration, UI or image change was found to be involved. The bridge exists only in `sandbox-jobs` mode, which task text still describes as pooled.

Technical novelty is moderate to high. Handlers run on a plain thread outside the request context, one at a time, with a closed error model and no run-context parameter; the run's tools must be captured from a list that is finalised after the script tool is built; and workflow scope is signalled inconsistently (assistant-mediated versus bare step) with the workflow's project not carried into `get_tools`. Two by-name resolution paths already exist and differ. The normal entry point logs tool arguments on failure and converts errors to strings by default, so the "no content in logs" and "clear error" criteria are not satisfied by simply calling it.

Test posture: strong unit coverage for the channel, SDK, job bridge, timeouts and switch; good tool-assembly tests with mocks; confirmation tests exist only generically. Gaps are the script tool under the three policies, context propagation, workflow recognition, log capture, and any real-cluster end-to-end path. Local sanity tests are the external `codemie-test-harness` (`make test-harness`, marks `api and sanity`), with the LiteLLM proxy toggled by `LLM_PROXY_ENABLED`; the sibling `codemie-sdk` repo contains no tests of the bridge, but has workflow and confirmation tests usable as templates. Key risks: payload leakage through `CodeMieTool._run`, error mapping through `handle_tool_error`, handler thread and contextvars, workflow-project plumbing, and environment checks that cannot be verified from this repository.

---

## 8. External References

Paths named by `task_context` and their outcome:

- `/Users/yanaasadchaya/Projects/epam/airun/codemie-dev/codemie/docs/stories/2026-09-28-custom-tools/children/script-tool-calls/subtasks/2-tool-hookup/task.md` - resolved and read. Facts used: goal "A script can call the platform tools available to it and use their real results. The echo stand-in is replaced by real tool calls, authorized by where the script runs, run with the same settings the model would use, and confirmed exactly as running a script is confirmed."; scope items 1-7 and the 11 acceptance criteria quoted in Section 1; sub-steps 1-6 (run-context propagation; authorizer and per-context resolution "assistant instances; workflow by name"; calls through the normal entry point with error mapping; confirmation tests under each policy and logging check; end-to-end checks in chat, assistant and workflow plus authors' documentation; rollout checks written with the switch). Size recorded as L (21/36), recommended flow sdlc-standard (brainstorm first). Its wording "runtime class of the pooled pods" and "on a pooled sandbox pod" is stale against the jobs-mode refactor (Section 3).
- Parent story `.../script-tool-calls/story.md` (`../../story.md` from the sub-task) - resolved and read. Facts used: scope of a script's tools is bounded by where it runs (assistant: only the assistant's configured tools; workflow: any tool the user is authorized for); approval of a run stands for the calls the script makes; what the person sees when approving is the script's path and arguments; tool-call request and result data "must not end up in the workspace, nor stay in a shared pod"; tool-calling run limit 120 s starting value; code executor limits (256 MiB, 1 CPU, 1 GiB temporary storage, 64 threads, 256 open files, pool of 5). Its "Environments run the sandbox in the pooled mode" is stale (Section 3).
- Sub-task 1 `.../subtasks/1-channel-and-sdk/task.md` - resolved and read (lines 20-108). Facts used: it explicitly leaves "Calling any real tool, authorization by context, run-context propagation, confirmation behaviour" to sub-task 2; SDK is stdlib-only (`json`, `os`, `time`, `uuid`), relative paths, one call at a time; the runtime file guard rejects absolute paths.
- Existing pre-implementation note `.../subtasks/2-tool-hookup/technical-analysis.md` - resolved and read; verified against code in Section 3.
- `/Users/yanaasadchaya/Projects/epam/airun/codemie-dev/codemie-sdk` - resolved (branch `main`, clean). Contents relevant to this task are summarised in Section 4(c): no bridge tests; harness tests for workspace-script workflow steps, tool-call permissions and default integrations exist.
- Additional in-repo sources read because they record decisions on this branch: `docs/superpowers/tasks/2026-09-30-script-tool-calls-jobs-mode-bridge/spec.md` and `gate-run.json`.
