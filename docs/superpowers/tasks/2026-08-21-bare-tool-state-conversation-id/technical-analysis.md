# Technical Research

**Task**: workflow runtime, ToolsService, AgentWorkspace, tool-state execution, AssistantChatRequest, conversation_id, execution_id
**Generated**: 2026-08-21T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-14138 — Bare workflow tool states create isolated AgentWorkspace instances because execution_id is not passed as conversation_id.

Summary: Bare workflow tool states create isolated AgentWorkspace instances because execution_id is not passed as conversation_id.

Description: Bare, non-assistant-mediated tool states create a new empty AgentWorkspace for each invocation because ToolsService.find_tool_from_config builds AssistantChatRequest without conversation_id. This breaks workflows that expect sequential bare tool states to share the same workspace during one execution.

Preconditions: Workflow has sequential bare AgentWorkspace tool states. First state writes a file to the workspace. Later state tries to execute or read that file in the same workflow execution.

Steps to Reproduce: Run write_workspace_file as one bare tool state. Run execute_workspace_script as a later bare tool state in the same execution. Observe the workspace available to the second state.

Expected Result: Later bare tool states can access files written earlier in the same workflow execution.

Actual Result: execute_workspace_script fails with: Script file 'X' was not provided to the execution workspace.

Affected Areas: Workflow runtime, AgentWorkspace, Tool-state execution.

Acceptance Criteria:
- execution_id is passed into ToolsService.find_tool_from_config.
- Constructed AssistantChatRequest uses conversation_id=execution_id.
- Bare tool states in one execution share the same workspace.
- Regression test covers write_workspace_file followed by execute_workspace_script.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/workflows/nodes/tool_node.py` — `ToolNode`; LangGraph workflow node that executes bare tool states. Stores `self.execution_id` (from `kwargs["execution_id"]`). `_execute_regular_tool` calls `ToolsService.find_tool_from_config` without passing `execution_id`. The MCP path (`_execute_mcp_tool`, line ~139) already correctly passes `conversation_id=self.execution_id` — demonstrating the intended fix pattern.
- `src/codemie/service/tools/tool_service.py` — `ToolsService.find_tool_from_config`; the primary bug site. Signature accepts `tool_config, toolkits, assistant, user, project_name, owner_user_id` but no `execution_id`. Line 110 constructs `AssistantChatRequest(tools_config=config)` with no `conversation_id` argument, causing the default factory `lambda: str(uuid.uuid4())` to fire on every call.
- `src/codemie/core/models.py` — defines `AssistantChatRequest`. Line 567: `conversation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))` — every instantiation without an explicit value produces a fresh random UUID.
- `src/codemie/service/tools/toolkit_service.py` — `ToolkitService.get_toolkit_methods`; builds the toolkit-factory lambda map. Each lambda receives `(assistant, user, llm_model, request_uuid, request)` where `request` is the `AssistantChatRequest`.
- `src/codemie/service/tools/toolkit_settings_service.py` — `ToolkitSettingService.get_agent_workspace_toolkit` (line ~243); reads `request.conversation_id` and returns `[]` (empty tools list) when it is falsy. Currently the bare tool-state path hits this guard and silently returns no workspace tools.
- `src/codemie_tools/data_management/workspace/toolkit.py` — `AgentWorkspaceToolkit`; initialized with `conversation_id` and `request`. `get_tools()` (line ~82) calls `AgentWorkspaceService.create_workspace(CreateAgentWorkspaceRequest(conversation_id=self.conversation_id), ...)`. The workspace identity is entirely determined by the `conversation_id` it receives.
- `src/codemie/service/agent_workspace_service.py` — `AgentWorkspaceService`; `_get_or_create_workspace` uses `conversation_id` as the workspace key — same `conversation_id` yields the same workspace row.
- `src/codemie/rest_api/models/agent_workspace.py` — `AgentWorkspace` DB entity, keyed by `(conversation_id, user_id)`.
- `src/codemie/repository/agent_workspace_repository.py` — `AgentWorkspaceRepository.get_by_conversation_for_user(conversation_id, user_id)` — the lookup that determines workspace reuse vs creation.
- `src/codemie_tools/data_management/workspace/tools_vars.py` — declares `AGENT_WORKSPACE_TOOLKIT = "AgentWorkspace"` and workspace tool metadata constants.

### Architecture and Layers Affected

- **Workflow runtime (LangGraph node)**: `ToolNode._execute_regular_tool` — must pass `execution_id` as a new parameter to `ToolsService.find_tool_from_config`.
- **Service layer — tool orchestration**: `ToolsService.find_tool_from_config` — must accept `execution_id: str | None = None` and forward it as `conversation_id` in the `AssistantChatRequest` construction.
- **Service layer — toolkit resolution**: `ToolkitService.get_toolkit_methods` and `ToolkitSettingService.get_agent_workspace_toolkit` — currently receive the `AssistantChatRequest`; no change needed here if `conversation_id` is correctly injected upstream. The guard in `get_agent_workspace_toolkit` that returns `[]` on falsy `conversation_id` will start passing once the fix is in place.
- **Toolkit implementation**: `AgentWorkspaceToolkit` — no change needed; it already uses `self.conversation_id` correctly.
- **Domain model**: `AssistantChatRequest` — no change needed to the model itself; the fix is at the construction callsite.

### Integration Points

- `ToolNode._execute_regular_tool` → `ToolsService.find_tool_from_config` (call site of the bug)
- `ToolsService.find_tool_from_config` → `toolkits[toolkit_name](assistant, user, '', '', AssistantChatRequest(...))` (line 110 — the exact line to fix)
- `ToolkitService.get_toolkit_methods` (lambda) → `ToolkitSettingService.get_agent_workspace_toolkit` → `AgentWorkspaceToolkit.get_toolkit`
- `AgentWorkspaceToolkit` → `AgentWorkspaceService.create_workspace` / `_get_or_create_workspace`
- `AgentWorkspaceService` → `AgentWorkspaceRepository.get_by_conversation_for_user`
- MCP path (already working, serves as reference implementation): `ToolNode._execute_mcp_tool` passes `conversation_id=self.execution_id` directly at line ~139.

### Patterns and Conventions

- `conversation_id` is the workspace identity key throughout the stack: DB lookup, service creation, and toolkit initialization all use it as the primary correlation handle.
- Service-layer pattern (from `.ai-run/guides/architecture/service-layer-patterns.md`): pass explicit IDs rather than constructing request objects with implicit defaults. The current code violates this pattern; the fix restores it.
- The MCP path in `ToolNode` is the reference implementation for the correct fix: `conversation_id=self.execution_id` must be replicated in the regular toolkit path.
- `execution_id` on `ToolNode` is already used for MCP, `build_unique_file_objects_list`, and other context propagation — it is the canonical identifier for a workflow execution.
- `find_tool_from_config` is a `@classmethod` — the `execution_id` parameter must be added as an optional keyword argument (`execution_id: str | None = None`) to preserve backward compatibility with any non-workflow callers.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/workflows/langgraph-workflows.md` — establishes that workflow behavior is extended through `WorkflowExecutor` and its node model; directly relevant as `ToolNode` is a workflow node.
- `.ai-run/guides/agents/agent-tools.md` — establishes that agent runtime adapters live under `src/codemie/agents/tools/` and reusable integrations under `src/codemie_tools/`; relevant because `AgentWorkspaceToolkit` is in `codemie_tools` while the callsite is in `tool_node.py` / `tool_service.py`.
- `.ai-run/guides/architecture/service-layer-patterns.md` — establishes the "pass explicit IDs" convention that the current code violates.
- `.ai-run/guides/testing/testing-patterns.md` — seam test rule: when a helper controls a boundary value (`conversation_id`), each callsite needs a test asserting the value that reaches the outer boundary. Directly applicable to the regression test requirement.
- `.ai-run/guides/testing/testing-service-patterns.md` — service orchestration tests should mock provider/toolkit boundaries; async test support required.

### Architectural Decisions

No formal ADR files address the workspace-isolation vs shared-workspace question for tool states. No `DECISION:`, `HACK:`, or `NOTE:` markers exist in `tool_node.py`, `tool_service.py`, `toolkit_settings_service.py`, or `agent_workspace_service.py`.

### Derived Conventions

- Workspace identity is entirely determined by `conversation_id`; for assistant-mediated tool calls this comes from the chat session; for bare workflow tool states it must come from `execution_id`.
- The `ToolkitSettingService.get_agent_workspace_toolkit` guard (`return []` when `request.conversation_id` is falsy) is a silent-failure mode: bare tool states today get no workspace tools at all rather than a wrong workspace. This means the bug has been silently suppressing workspace tools entirely for the bare path.
- `execution_id` is the workflow-scope equivalent of `conversation_id`; binding them is the architecturally correct approach as evidenced by the MCP path.
- Unrelated TODO at `src/codemie/core/workflow_models/workflow_models.py:408`: `# API Models, TODO: move to rest_api module`.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/workflows/test_tool_node_context.py` — tests `ToolNode._execute_regular_tool` and `_execute_mcp_tool` (TC_TNC_009 through TC_TNC_018); validates `owner_user_id`, `file_objects`, and token-limit behavior. `find_tool_from_config` is always patched at the class level — the internal `AssistantChatRequest` construction is never executed in any existing test.
- `tests/codemie_tools/data_management/workspace/test_execute_workspace_script_tool.py` — tests `WorkspaceScriptRunner._build_script_wrapper` for resource-limit forwarding only; no workspace-identity or `conversation_id` coverage.
- `tests/codemie_tools/data_management/workspace/test_generate_image_tool_v2.py` — tests `AgentWorkspaceToolkit` instantiation for image generation; unrelated to the isolation bug.
- `tests/codemie/service/tools/test_tool_execution_service.py` — tests `ToolExecutionService.invoke*` methods; mocks `ToolsService.find_tool` (not `find_tool_from_config`); no workflow context.
- `tests/codemie/core/test_models_assistant_chat_request.py` — tests `AssistantChatRequest` model validation (`save_history` field only); no `conversation_id` injection or propagation coverage.
- Adjacent toolkit-service tests (`test_tool_discovery_service.py`, `test_toolkit_service.py`, `test_toolkit_service_headers.py`) — none exercise the `AGENT_WORKSPACE_TOOLKIT` lambda path.

### Testing Framework and Patterns

- Framework: pytest with `unittest.mock` (`Mock`, `MagicMock`, `patch`).
- Module-level class patches via stacked `@patch("codemie.workflows.nodes.tool_node.ToolsService")` decorators; `Mock` instances injected as function arguments in reverse decorator order.
- Shared pytest fixtures per file built with `Mock(spec=<class>)` for type-safe mocking (`mock_workflow_execution_service`, `mock_user`, `mock_tool_config`, `mock_workflow_config`).
- Test ID naming convention: `test_tc_<area>_NNN_<description>` (e.g. `test_tc_tnc_011_...`).
- Inline `with patch(...)` context managers for function-scoped patches.
- Assertions on `call_args` / `call_args[1]` (kwargs) to verify exact arguments passed to mocked services.
- Global session-scoped `mock_database_engine` fixture in `tests/conftest.py` suppresses all DB connections without per-test boilerplate.

### Coverage Gaps

1. **`find_tool_from_config` `execution_id` threading** — no test asserts that a caller-supplied `execution_id` is threaded through to the `AssistantChatRequest` construction and reaches `get_agent_workspace_toolkit` as `request.conversation_id`.
2. **`ToolkitSettingService.get_agent_workspace_toolkit` untested entirely** — the method at `src/codemie/service/tools/toolkit_settings_service.py` has no test; particularly the branch that resolves `request.conversation_id` and passes it to `AgentWorkspaceToolkit.get_toolkit(conversation_id=...)`.
3. **Cross-tool-state workspace sharing regression** — no test exercises two successive `ToolNode._execute_regular_tool` invocations and asserts they share the same `AgentWorkspace`. This is the primary acceptance criterion for EPMCDME-14138.
4. **`AgentWorkspaceToolkit.get_tools()` workspace identity** — `CreateAgentWorkspaceRequest(conversation_id=...)` is never tested to confirm it receives the correct (non-random) ID when called from the workflow tool-state path.
5. **`find_tool_from_config` toolkit fallback branch** — the `if not tools: ... tools = toolkits[toolkit_name](...)` fallback (lines ~108-110) is never exercised; all existing tests short-circuit it by mocking the whole `ToolsService` class.

---

## 5. Configuration and Environment

### Environment Variables

No environment variables govern `AgentWorkspace` instance creation, workspace scoping, `conversation_id` propagation, or `execution_id`-to-workspace mapping. The workspace is solely identified by `conversation_id` at the DB layer.

### Configuration Files

- `src/codemie/configs/customer_config.py` — operator-level feature flags via `is_feature_enabled(feature_key)`; no workspace-specific flags present.
- `src/codemie_tools/data_management/workspace/tools_vars.py` — declares `AGENT_WORKSPACE_TOOLKIT = "AgentWorkspace"` constant and workspace tool metadata objects; purely static, no env vars.
- `.env.example` / `.env` — contain no workspace-, `conversation_id`-, or `execution_id`-related variables.

### Feature Flags and Deployment Concerns

- `webSearch` (`features:webSearch`) — gates Research toolkit; unrelated to this bug.
- `dynamicCodeInterpreter` (`features:dynamicCodeInterpreter`) — gates FileSystem/CodeExecutor toolkit; unrelated to this bug.
- No feature flag exists to control `AgentWorkspace` creation or `conversation_id`/`execution_id` binding.
- **Secondary deployment concern**: before this fix, each bare tool-state invocation creates a new `AgentWorkspace` DB row with a fresh random UUID that is never reused or cleaned up. In long-running deployments this results in accumulated orphaned rows. The fix stops new orphans from being created; a cleanup migration may be warranted separately but is not required for correctness.

---

## 6. Risk Indicators

- **Silent failure mode currently active**: `ToolkitSettingService.get_agent_workspace_toolkit` returns `[]` when `request.conversation_id` is falsy. This means the `AGENT_WORKSPACE_TOOLKIT` is silently not added to bare tool states today. After the fix, workspace tools will be active for the first time in the bare path — any latent bugs in `AgentWorkspaceToolkit.get_tools()` or `AgentWorkspaceService` in the workflow context would be newly exposed.
- **No test coverage for the `find_tool_from_config` toolkit fallback branch** — the exact lines containing the bug (`tool_service.py:108-110`) are never executed under test. The regression test must penetrate below the ToolsService mock.
- **`find_tool_from_config` is a `@classmethod` called from multiple sites** — adding `execution_id: str | None = None` as an optional parameter is backward-compatible, but all call sites must be audited to confirm none inadvertently expects the old positional signature.
- **No test for `ToolkitSettingService.get_agent_workspace_toolkit`** — this method contains the `conversation_id` guard that is central to the fix working correctly; it is entirely untested and must be covered by the regression test.
- **DB pollution from orphaned `AgentWorkspace` rows** — pre-existing issue from the bug; not introduced by the fix but worth tracking for operational cleanup.
- **MCP vs regular-toolkit path asymmetry** — the MCP path already passes `execution_id` correctly; the fix must achieve parity without diverging the two paths further. Review of `_execute_mcp_tool` provides the exact pattern to follow.
- **No formal ADR for bare tool-state workspace lifecycle** — the correct behavior (share workspace within one execution) is only documented via the ticket; if future refactors touch workspace scoping they have no recorded decision to reference.
- **`AssistantChatRequest.conversation_id` default factory** — the `uuid.uuid4()` default is intentional for assistant-mediated flows where no conversation exists yet; the fix must not change the model default, only the construction callsite in `find_tool_from_config`.

---

## 7. Summary for Complexity Assessment

This task is a targeted parameter-threading fix spanning three architectural layers: the LangGraph workflow node (`ToolNode`), the tool orchestration service (`ToolsService.find_tool_from_config`), and the domain model construction site (`AssistantChatRequest`). The change surface is narrow — two production files require modification and the fix pattern already exists in the MCP path of `ToolNode` (`conversation_id=self.execution_id` at line ~139). The core change is: add `execution_id: str | None = None` to `find_tool_from_config`, pass `conversation_id=execution_id` in the `AssistantChatRequest(...)` construction at line 110, and pass `execution_id=self.execution_id` from `ToolNode._execute_regular_tool`. Total estimated production file changes: 2 files, 3-5 lines.

The fix follows established patterns and introduces no technical novelty. The MCP path serves as a complete reference implementation. The only non-trivial judgment call is confirming that all other callers of `find_tool_from_config` can safely receive `execution_id=None` (the default) without regression — this requires a call-site audit across the codebase, but based on current findings only `ToolNode._execute_regular_tool` calls this method in a workflow context.

Test coverage posture for the affected area is poor. The `find_tool_from_config` toolkit fallback branch (the exact bug site) has zero test coverage. `ToolkitSettingService.get_agent_workspace_toolkit` is completely untested. The acceptance criteria require a regression test covering the `write_workspace_file` → `execute_workspace_script` sequential bare tool state scenario — this test must penetrate below the `ToolsService` class mock used in all existing `ToolNode` tests and exercise the `AssistantChatRequest` construction directly. The test complexity (multi-layer mock setup with workspace service and repository) is moderate and is the largest effort item in this task. Key risk: the fix activates the workspace toolkit for the bare tool-state path for the first time, meaning any latent bugs in the `AgentWorkspaceToolkit` workflow path would be newly surfaced in production.
