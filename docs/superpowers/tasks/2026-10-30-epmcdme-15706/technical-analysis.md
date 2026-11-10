# Technical Research

**Task**: code-executor standalone feature-flag settings workspace-script-runner
**Generated**: 2026-10-30
**Research path**: codegraph

---

## 1. Original Context

EPMCDME-15706, Sub-Bug, "OSS standalone: Hide nonfunctional Code Executor settings in chat"

Context
* Two switches decide whether Code Executor works. The chat "Code Executor" toggle is shown according to the Code Executor feature flag, which is on by default. The backend only provides the tool when the operator enables Code Executor, which is off by default. The standalone image does not enable it, so in standalone the toggle is shown but does nothing.
* Code Executor runs user code in a Kubernetes cluster, reached either through a configured cluster connection file or by running inside the cluster. The standalone stack (application and database only) has no cluster, so even with Code Executor enabled every run fails.
* When a run fails today, the user sees a generic "Error executing code" message that does not tell a missing cluster apart from a failure in the user's own code.
* The workspace script runner is a separate feature with its own flag (off by default). It uses the same Code Executor engine and has the same cluster dependency.
* The settings returned to the UI already have a pattern for hiding capabilities whose dependency is not configured, and the chat UI already hides the toggle when the feature flag is off. Startup already has non-blocking readiness warnings. Depends on the standalone switch from EPMCDME-16473 (already merged on this branch: commit 243f75eb3 "Add STANDALONE flag and standalone image defaults"). Only the codemie backend changes; codemie-ui and codemie-onboarding are not affected.

Story: As a user of a standalone CodeMie installation, I want Code Executor and the workspace script runner offered only when the deployment can actually run code so that I never turn on a toggle that silently does nothing or fails with an unclear error.

Acceptance Criteria
1. Non-standalone deployment: behavior same as today (chat Tools Settings, Code Executor, workspace script runner).
2. Standalone mode, UI loads application settings: Code Executor reported available only if its feature flag is on, the operator has enabled Code Executor, and a cluster connection is configured (connection file set or app runs inside a cluster); otherwise unavailable.
3. Standalone: only existing flags/settings used; no new feature flag.
4. Standalone + Code Executor unavailable: chat Tools Settings does not show the Code Executor toggle.
5. Standalone + unavailable, chat request asks for Code Executor anyway (e.g. via API): request processed without the tool and does not fail.
6. Standalone + prerequisites missing: workspace script runner reported unavailable in settings even if its own flag is on.
7. Standalone + script runner unavailable, user/workflow tries to use it: not offered, or returns a "not configured" error; nothing tries to reach a cluster.
8. Standalone + prerequisites missing at startup: a warning names the missing configuration and degraded features (chat Code Executor, workspace script runner); startup continues.
9. Standalone + all prerequisites present: no Code Executor warning at startup.
10. Standalone + prerequisites + reachable cluster: enabling Code Executor in chat and running code returns the result.
11. Configured cluster unreachable, Code Executor runs code: error says execution environment unavailable, does not blame user's code.
12. Reachable cluster, user's code raises: error identifies it as a code failure and includes the code's error output.
13. Direct request to run code while Code Executor not configured: error says Code Executor is not configured, distinct from "environment unavailable" and "code failure".

Out of scope: fixing the flag mismatch in non-standalone; bundling a k8s cluster/sandbox in the standalone image; live reachability check at settings load; chat UI changes; delivering the standalone switch itself.

Decisions: D1 config check only (flag on + operator enabled + cluster connection configured: connection file or in-cluster env), no live reachability. D2 workspace script runner in scope, same gate in standalone. D3 non-blocking startup warning naming missing config and degraded features. D4 three error categories: not configured / execution environment unavailable / code failure. D5 reuse existing flags, no new flag. D6 hide the toggle (UI already hides when flag off; no UI change). D7 gate does not apply outside standalone.

Constraints: single repository (codemie); end-to-end check: run standalone image without Code Executor configured -> startup warning, toggle hidden, script runner unavailable; then enable with reachable cluster and confirm code runs.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/configs/config.py:589` `STANDALONE: bool = False`; `:1181-1191` module-level predicates `retrieval_available(cfg)` and `mcp_connect_available(cfg)` (non-standalone returns the plain flag, standalone adds the dependency check). This is the precedent for a "dependency configured" gate.
- `src/codemie/configs/customer_config.py:30` `CONFIG_IDS` map; `:~304-316` runtime-computed `features:mermaid` component: `True if not config.STANDALONE else bool(config.MERMAID_ENABLED and MermaidService.is_configured())`. This is the settings-to-UI hiding pattern ("Runtime-computed ... never overridable from YAML or DB"; see `get_runtime_components`, `get_enabled_components` at 320-338). Code Executor flag is `features:dynamicCodeInterpreter` (YAML, `standalone/external-config/customer-config.yaml:154`); it is not in `CONFIG_IDS` today (Grep found only mermaid and the marketplace entries).
- `src/codemie_tools/data_management/file_system/toolkit.py:84-114` `FileSystemToolkit._is_code_executor_enabled()` reads env `CODE_EXECUTOR_ENABLED` (default "false"); `get_tools_ui_info` filters CODE_EXECUTOR_TOOL by it.
- `src/codemie/service/tools/toolkit_service.py:290-314` chat path: `enable_code_interpreter = request.enable_code_interpreter is True and customer_config.is_feature_enabled("dynamicCodeInterpreter") and FileSystemToolkit._is_code_executor_enabled()`; if false the toolkit is silently not added (already the AC5 "processed without tool" behavior; the gate would extend this expression).
- `src/codemie_tools/data_management/code_executor/code_executor_tool.py` `CodeExecutorTool` (line 169); `_execute_sandbox` (448); generic failure wrapper at ~528-534 raises `ToolException("Error executing code: ...")`. `SandboxFailure(ToolException)` with `RunOutcome` in `run_observer.py:48`, raised from `batch_job_runner.py` (e.g. "Failed to create sandbox Job", outcome `SANDBOX_ERROR`) and `session_manager.py:359`.
- `src/codemie_tools/data_management/code_executor/models.py:233,413` `CodeExecutorConfig.kubeconfig_path` from env `CODE_EXECUTOR_KUBECONFIG_PATH` (default ""). `k8s_client_manager.py:98-111` uses `load_kube_config(config_file=...)` if path set, else `load_incluster_config()`. In-cluster detection is not currently implemented as a helper.
- `src/codemie_tools/data_management/workspace/workspace_script_runner.py:48` `WorkspaceScriptRunner(CodeExecutorTool)` (same engine); `execute_workspace_script_tool.py:45` `ExecuteWorkspaceScriptTool` (agent tool); `workspace/toolkit.py:58-87` `AgentWorkspaceToolkitUI` lists `EXECUTE_WORKSPACE_SCRIPT_TOOL` unconditionally; `src/codemie/service/agent_workspace_service.py:594` `execute_workspace_script`; REST `src/codemie/rest_api/routers/agent_workspace.py:258-267`; script tool-call handler `src/codemie/service/script_tool_calls/handler.py:185`; workflow `tool_node.py:~237`; `skill_workspace_preparation.py:49`.
- `src/codemie/rest_api/main.py:516-536` `_run_standalone_startup()`: no-op unless `config.STANDALONE`; non-blocking `logger.warning` for MCP Connect and Mermaid naming the missing setting and the degraded features. This is the startup-warning pattern.
- Not located: the specific "own flag (off by default)" of the workspace script runner. Only `features:workspaceScriptBridge` (`customer_config_declarations.py:242`, `workspace_script_bridge.py:28`) was found, which governs tool calls from scripts, not the runner itself. To be confirmed in the spec stage.

### Architecture and Layers Affected
- Config layer: `configs/config.py`, `configs/customer_config.py`.
- Service layer: `service/tools/toolkit_service.py`, `service/agent_workspace_service.py`, `service/script_tool_calls`.
- Tool layer (`codemie_tools`): `file_system/toolkit.py`, `code_executor/*`, `workspace/*`.
- API/startup: `rest_api/main.py`, `rest_api/routers/agent_workspace.py`.

### Integration Points
- Kubernetes API via `KubernetesClientManager`; customer-config settings consumed by the UI; `FileSystemToolkit._is_code_executor_enabled` is the shared operator switch used by both UI info and chat assembly.

### Patterns and Conventions
- Standalone-only gating: `if not config.STANDALONE: return <old behavior>` (no change outside standalone).
- Runtime-computed `Component` entries in customer_config; startup warnings in `_run_standalone_startup`.
- `config.HTTP_BLOCKED_TOOLS` includes `code_executor` (config.py:707), so direct HTTP invoke of that tool is already blocked.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/development/configuration-patterns.md` (config conventions); `src/codemie_tools/data_management/code_executor/README.md` (env var table, `CODE_EXECUTOR_KUBECONFIG_PATH`, modes, line ~428 deployment checklist). Guide contents were not read in detail.

### Architectural Decisions
- Comments in customer_config.py describe runtime components as the single source of truth for UI and backend; mermaid and MCP Connect set the precedent for standalone gating.

### Derived Conventions
- Gate helpers live beside `STANDALONE` in config.py or as a service `is_configured()`.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/configs/test_customer_config.py`, `tests/codemie/configs/test_customer_config_override.py` (runtime components).
- `tests/codemie_tools/data_management/code_executor/` (test_code_executor_tool, test_execution_modes, test_export_path_validation, test_sandbox_dispatch, +4).
- `tests/codemie_tools/data_management/workspace/test_execute_workspace_script_tool.py`, `tests/codemie/workflows/test_tool_node_script_step.py`, `tests/codemie/service/tools/test_toolkit_service_script_registry.py`, `tests/codemie/service/tools/test_tool_access_assembly.py`, `tests/codemie/agents/tool_confirmation/test_script_tool_confirmation.py`.
- Startup warnings: no test file located in this pass for `_run_standalone_startup`.

### Testing Framework and Patterns
- pytest with mock/patch (Arrange/Act/Assert comments seen); `patch("codemie....")` style.

### Coverage Gaps
- No tests found for the new categorized error messages or a cluster-configured helper (none exist yet).

---

## 5. Configuration and Environment

### Environment Variables
- `STANDALONE` (config.py:589; set `true` in `standalone/Dockerfile:122`); `CODE_EXECUTOR_ENABLED` (read via `os.getenv`, not a Config field); `CODE_EXECUTOR_KUBECONFIG_PATH`; `CODE_EXECUTOR_NAMESPACE`, `CODE_EXECUTOR_SANDBOX_MODE`, others in the code_executor README. In-cluster signal is `load_incluster_config()` in k8s_client_manager (env `KUBERNETES_SERVICE_HOST` is the kubernetes client convention; not read by the repo today).

### Configuration Files
- `standalone/external-config/customer-config.yaml` (`features:dynamicCodeInterpreter` at :154), `docker-compose.yml:57` sets `CODE_EXECUTOR_KUBECONFIG_PATH`, `.env.example:80-90`, `deploy-templates/values.yaml`.

### Feature Flags and Deployment Concerns
- `features:dynamicCodeInterpreter` (customer config), `CODE_EXECUTOR_ENABLED` (env), `features:workspaceScriptBridge` (related, distinct).

---

## 6. Risk Indicators

- Two separate read sites for the operator switch and the feature flag (toolkit_service.py:293-297 and file_system/toolkit.py:94, plus UI customer config); a single gate must feed all of them or they drift.
- Workspace script runner has multiple entry points (agent tool, REST route, workflow tool_node, script tool-call handler, skill preparation); AC7 needs every one covered. Its "own flag" was not found.
- Error categorization touches `CodeExecutorTool` generic wrapper (code_executor_tool.py ~528-534) and `SandboxFailure`/`RunOutcome`; existing tests may assert the "Error executing code" text.
- `CODE_EXECUTOR_ENABLED` is read via `os.getenv` and not part of `Config`; the in-cluster check has no existing helper.
- Speculative: the three error categories may need a new exception type or `RunOutcome` values.
- Speculative: `dynamicCodeInterpreter` is not in `CONFIG_IDS`, so reporting availability may need it added as a runtime component.
- Speculative: guides under `.ai-run/guides` were not read in full; conventions may add requirements.

---

## 7. Summary for Complexity Assessment

The change spans the config layer (a standalone-only availability predicate, modeled on `mcp_connect_available` and the mermaid runtime component), the settings component list in customer_config.py, chat tool assembly in toolkit_service.py, the file-system and workspace toolkits' UI info, startup in main.py, and error handling in the code_executor package. Roughly 6-10 source files are involved, with a matching test set. Everything is in the single codemie repository.

Technical novelty is low for the gating and startup warning, since two precedents (Mermaid, MCP Connect) exist in the same files. The error classification is the less-precedented part: it must distinguish a missing cluster configuration, a sandbox/environment failure (`SandboxFailure`, `RunOutcome.SANDBOX_ERROR`) and user-code failure inside `CodeExecutorTool`, whose wrapper is currently generic.

Test posture is good for code_executor, workspace tools and customer_config, but there is none for the new behaviors. Main risks are the multiple entry points to the workspace script runner, duplicated reads of the operator switch, and not finding the script runner's own flag.

---

## 8. External References

None named by the task.
