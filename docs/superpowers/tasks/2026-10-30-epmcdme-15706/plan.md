# EPMCDME-15706 Gate Code Executor in standalone Implementation Plan

> For agentic workers: use superpowers:subagent-driven-development or superpowers:executing-plans. Commit per task using the repository's existing convention.

**Goal:** In standalone, offer Code Executor and the workspace script runner only when config says code can run; make failures self-explanatory.

**Architecture:** One predicate `code_executor_available(cfg)` beside `mcp_connect_available` feeds settings, chat assembly, toolkit UI info, startup warning and the engine short-circuit. Error classification lives inside the code_executor package.

**Spec:** `docs/superpowers/tasks/2026-10-30-epmcdme-15706/spec.md`

## Global Constraints

- Non-standalone behavior unchanged (D7). No new operator-settable flag; `features:workspaceScriptRunner` is derived-only.
- Rule (standalone): `features:dynamicCodeInterpreter` on AND `CODE_EXECUTOR_ENABLED` true AND (`CODE_EXECUTOR_KUBECONFIG_PATH` non-empty OR env `KUBERNETES_SERVICE_HOST` present). No filesystem or reachability check.
- Do not gate `features:workspaceScriptBridge`; no per-entry-point script runner gating; no UI change.

## Review Focus

- `CODE_EXECUTOR_ENABLED` unset/"false"/"TRUE" casing: use the same parsing as `FileSystemToolkit._is_code_executor_enabled` (task 1 test).
- Empty-string `KUBERNETES_SERVICE_HOST` counts as absent (task 1 test).
- Chat request with `enable_code_interpreter=True` while unavailable returns normally without the tool (task 2 test).
- Existing tests asserting "Error executing code" (task 4).

### Task 1: Availability predicate

**Files:** Modify `src/codemie/configs/config.py:1181-1191`; Test `tests/codemie/configs/test_config_code_executor_available.py` (new)

**Interfaces:** Produces `code_executor_available(cfg) -> bool` and a pure-env helper `cluster_connection_configured() -> bool` in the code_executor package (`k8s_client_manager.py`), so `codemie_tools` needs no `codemie.configs` import.

- [ ] Test-first: yes — a 2x2x2x2 matrix test (standalone, flag, operator, cluster) fails with ImportError/AttributeError; non-standalone returns the flag value regardless of the other conditions.
- [ ] Implement per Interfaces; flag is read via `customer_config.is_feature_enabled("dynamicCodeInterpreter")`; operator switch reuses `FileSystemToolkit._is_code_executor_enabled` parsing (reuse, do not re-implement). Run `pytest tests/codemie/configs/test_config_code_executor_available.py`.

### Task 2: Settings, chat assembly and toolkit UI

**Files:** Modify `src/codemie/configs/customer_config.py:~304-338` (runtime components), `src/codemie/service/tools/toolkit_service.py:290-314`, `src/codemie_tools/data_management/file_system/toolkit.py:84-114`, `src/codemie_tools/data_management/workspace/toolkit.py:58-87`; Test `tests/codemie/configs/test_customer_config.py`, `tests/codemie/service/tools/test_toolkit_service_script_registry.py` (or a new sibling), `tests/codemie_tools/data_management/workspace/`

**Interfaces:** Consumes `code_executor_available`.

- [ ] Test-first: yes — in standalone with prerequisites missing, `features:dynamicCodeInterpreter` and `features:workspaceScriptRunner` are false in runtime components; with prerequisites present both true; non-standalone: dynamicCodeInterpreter is the existing value and workspaceScriptRunner true; a YAML/DB override cannot change either.
- [ ] Test-first: yes — toolkit_service with `enable_code_interpreter=True` and predicate false builds tools without the FileSystem code executor and does not raise; `AgentWorkspaceToolkitUI` omits `EXECUTE_WORKSPACE_SCRIPT_TOOL` when unavailable.
- [ ] Add both runtime components (mermaid mechanism); replace the inline expression in toolkit_service with the predicate; in the UI-info methods, use the predicate in standalone and the existing env read otherwise; hide the script tool when `features:workspaceScriptRunner` is false.

### Task 3: Startup warning

**Files:** Modify `src/codemie/rest_api/main.py:516-536` (`_run_standalone_startup`); Test `tests/codemie/rest_api/test_standalone_startup.py` (new)

- [ ] Test-first: yes — with STANDALONE and prerequisites missing, one `logger.warning` is emitted naming each missing item (feature flag off / operator not enabled / no cluster connection) and "chat Code Executor", "workspace script runner"; with all present no Code Executor warning; non-standalone no warning; no exception in any case.
- [ ] Add the check beside the MCP Connect and Mermaid warnings, listing only the missing items.

### Task 4: Engine short-circuit and error categories

**Files:** Modify `src/codemie_tools/data_management/code_executor/code_executor_tool.py:~448-534`, `.../workspace/workspace_script_runner.py:48`, `.../code_executor/run_observer.py:48` (only if a new type is needed); Tests in `tests/codemie_tools/data_management/code_executor/` and `.../workspace/test_execute_workspace_script_tool.py`; update tests asserting "Error executing code".

**Interfaces:** Three distinguishable `ToolException` messages: "Code Executor is not configured", "execution environment unavailable" (never blames the code), "code failure" plus the code's error output. Keep new types inside the package.

- [ ] Test-first: yes — standalone and unconfigured: `CodeExecutorTool` and `WorkspaceScriptRunner` return/raise the "not configured" error and the K8s client factory mock is never called.
- [ ] Test-first: yes — `SandboxFailure` (`RunOutcome.SANDBOX_ERROR`) and a cluster connection error yield the "environment unavailable" text without the code's output; a reachable sandbox where user code raises yields the "code failure" text containing the stderr output; the three messages are pairwise distinct.
- [ ] Gate at the start of the execution path in the shared engine (standalone only, using `cluster_connection_configured()` and the operator switch; the settings flag need not be re-read here), then replace the generic wrapper at ~528-534 with the classification. Entry points inherit; add no per-entry-point checks.
