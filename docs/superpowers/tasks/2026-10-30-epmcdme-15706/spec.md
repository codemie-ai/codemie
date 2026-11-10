# EPMCDME-15706 Spec: Gate Code Executor and workspace script runner in standalone

Backend only (codemie). Depends on STANDALONE flag (EPMCDME-16473, merged).

## Problem

In standalone there is no Kubernetes cluster, yet the chat shows a Code Executor toggle that does nothing, the workspace script runner is offered, and failures surface as a generic "Error executing code". Make both features available only when the deployment can run code, and make failures self-explanatory.

## Availability rule (standalone only)

Code Executor is available iff all three hold:
1. Feature flag `features:dynamicCodeInterpreter` is on.
2. Operator switch `CODE_EXECUTOR_ENABLED` is true.
3. A cluster connection is configured: `CODE_EXECUTOR_KUBECONFIG_PATH` is non-empty, or env `KUBERNETES_SERVICE_HOST` is present. No filesystem check, no live reachability check.

The workspace script runner uses the same rule. It has no flag of its own (confirmed: only `features:workspaceScriptBridge` exists, which governs tool calls from scripts and is unrelated). No new operator-settable flag is added.

Outside standalone, behaviour is unchanged (D7).

## Design

- Predicate `code_executor_available(cfg)` in `src/codemie/configs/config.py` beside `mcp_connect_available` (`:1181-1191`). Non-standalone returns today's result; standalone adds conditions 2 and 3. This is the single source: it feeds every site below so reads cannot drift.
- `src/codemie/configs/customer_config.py`: runtime-computed components (same mechanism as `features:mermaid`, `:~304-338`; derived only, never overridable from YAML or DB):
  - `features:dynamicCodeInterpreter` (add to runtime components): standalone value is the predicate; otherwise the existing flag value.
  - `features:workspaceScriptRunner` (new, derived only): true outside standalone; the predicate in standalone.
- Chat assembly `src/codemie/service/tools/toolkit_service.py:290-314`: the existing expression uses the shared predicate. When unavailable the toolkit is silently not added and the request proceeds (AC5).
- `src/codemie_tools/data_management/file_system/toolkit.py:84-114` UI info uses the same predicate instead of its own env read in standalone.
- `AgentWorkspaceToolkitUI` (`workspace/toolkit.py:58-87`) omits `EXECUTE_WORKSPACE_SCRIPT_TOOL` when unavailable.
- Script runner enforcement: gate once in the shared engine (`CodeExecutorTool` / `WorkspaceScriptRunner`, `code_executor_tool.py`, `workspace_script_runner.py`). When not configured it returns the "not configured" error before any cluster client is created. All entry points (agent tool, REST route `agent_workspace.py:258`, workflow `tool_node.py`, script tool-call handler, skill preparation) inherit this with no per-entry-point gating.
- Startup: `_run_standalone_startup()` (`rest_api/main.py:516-536`) logs one non-blocking `logger.warning` when the rule fails, naming each missing item (feature flag off, operator not enabled, no cluster connection) and the degraded features (chat Code Executor, workspace script runner). No warning when all present. Startup always continues.
- Errors, in the code_executor package, three distinguishable categories replacing the generic wrapper at `code_executor_tool.py:~528-534`:
  - Not configured: message says Code Executor is not configured.
  - Execution environment unavailable: sandbox/cluster failures (`SandboxFailure`, `RunOutcome.SANDBOX_ERROR`, cluster connection errors); must not blame the user's code.
  - Code failure: user code raised in a reachable sandbox; message says code failure and includes the code's error output.
  Implementation may add exception types or `RunOutcome` values; keep them inside the package.

## Acceptance criteria

1. Non-standalone: Code Executor and script runner behave exactly as today.
2. Standalone settings report Code Executor available only when flag on, operator enabled and cluster connection configured; otherwise unavailable.
3. Only existing flags/settings drive the gate; no operator-settable flag is added (the derived `features:workspaceScriptRunner` component is not overridable).
4. Standalone and unavailable: the chat Code Executor toggle is hidden (via settings; no UI change).
5. Standalone and unavailable: a chat request asking for Code Executor is processed without the tool and does not fail.
6. Standalone and prerequisites missing: settings report the script runner unavailable even if other flags are on.
7. Standalone and script runner unavailable: tool not listed in the toolkit UI; any invocation returns a "not configured" error and nothing reaches a cluster.
8. Standalone and prerequisites missing at startup: a warning names the missing config and the degraded features; startup continues.
9. Standalone and all prerequisites present: no Code Executor warning.
10. Standalone with prerequisites and a reachable cluster: running code in chat returns the result.
11. Configured but unreachable cluster: error says the execution environment is unavailable and does not blame the code.
12. Reachable cluster and user code raises: error is identified as a code failure and includes the code's error output.
13. Running code while not configured: error says Code Executor is not configured, distinct from the other two.

## Tests

Unit tests for the predicate (matrix of 3 conditions, standalone and not), the runtime components, toolkit_service gating, startup warning, the three error categories, and the engine-level "not configured" short-circuit. Update existing tests asserting "Error executing code".

## Non-goals

- Fixing the flag mismatch in non-standalone.
- Bundling a Kubernetes cluster or sandbox in the standalone image.
- Live reachability check at settings load.
- Any UI change (codemie-ui, codemie-onboarding).
- Delivering the standalone switch itself.
- A new operator-settable feature flag, or a separate script-runner flag.
- Gating `features:workspaceScriptBridge`.
- Per-entry-point gating of the script runner.
