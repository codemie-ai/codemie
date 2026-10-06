# EPMCDME-15401 Bridge Channel and SDK with an Echo Handler (spec)

> **Superseded:** the bridge works in the jobs sandbox mode only (see the jobs-mode bridge spec); the 2026-10-02 decisions changed the SDK surface (`call_tool`, `ERROR_CODES`, a wait that follows the run's limit, `deadline_exceeded`) and the channel's failure handling (retry until the deadline, one exec per tick). See the code executor README.

> **Changed again by sub-task 5 (2026-10-05):** the structure moved: `tool_call_protocol.py` (no `kubernetes`), `exec_runner.py`, one polling thread for every exec and a thread pool for handlers (`maxParallelCalls`), `call_tools` in the SDK, named error-code constants, one `ToolCallingSettings` value, the pooled-mode leftovers (sweep, kill, `pid`/`start_time`) removed, scope as an object (`ScriptScope`), one result mechanism (`to_script_result`) and a log record factory. This document describes the first version; the code executor README describes the current one.

Scope: repo `codemie` only. The bridge works in pooled sandbox mode only. Size L (23/36). Source of requirements:
`docs/stories/2026-09-28-custom-tools/children/script-tool-calls/subtasks/1-channel-and-sdk/task.md`. Its ten
acceptance criteria apply unchanged; this spec fixes the design decisions the task left open.

## Goal

A script run by `WorkspaceScriptRunner` on a pooled pod can send a request to the backend and receive a response
during the same blocking run. A hardcoded `echo` handler answers. No platform tool is called.

## Design

**Layout.** New package `src/codemie_tools/data_management/code_executor/runtime_sdk/` with one file,
`codemie_runtime_sdk.py`: imports only `json`, `os`, `time`, `uuid`; relative paths only; one call at a time.
The protocol constants (file names, version, `MAX_PAYLOAD_BYTES`, call timeout) live in this file and nowhere
else. There is no `constants.py`; this deviates from task.md's "constants file" wording to keep one source of
truth. The module top level holds only constants, definitions and `_state = None`, so the backend can import it
without side effects. The backend-side channel (new `code_executor/tool_call_channel.py`) imports the constants
from the SDK module. Poll interval, retry count and handler names stay in `tool_call_channel.py`.

**Protocol.** Exchange folder `.codemie_bridge/<epoch>-<uuid4hex>/` in the workspace root. The SDK writes
`req.<id>.json` (tmp file, then `os.replace`) and polls for `resp.<id>.json`, deletes it, and returns the result.
Request: `{v, id, op, payload}`. Response: `{v, id, ok, result | error{code, message}}`. Public API:
`call(op, payload, timeout=None)`, raising `ToolCallError` on error response, timeout, oversize or an unconfigured
SDK. Handlers are an `op -> callable` registry holding only `echo`; unknown `op` gives error code `unknown_op`.

**Injection.** Only `build_guarded_workspace_script` gains an optional bootstrap, so the SDK is registered for both
pooled and job-mode workspace-script wrappers. `build_guarded_python_script` and the generic `CodeExecutorTool`
(`code_executor_tool.py:440`) are untouched. The launcher executes the SDK source (embedded with `!r`, as the
existing wrapper does) into a `types.ModuleType` registered as `codemie_runtime_sdk`. The name becomes reserved:
a workspace file of that name is shadowed. The module is configured (exchange dir set) only for an active
tool-calling run. An unconfigured `call()` raises one clear error ("tool calling is not available in this run"),
which covers switch off, run without tool calling, and job mode. The same bootstrap writes `pid` and the process
start time (from `/proc/self/stat`) into the exchange folder. It runs under the guard. `filesystem_policy.py` is
not modified; if the SDK cannot run under the unchanged guard, the SDK changes.

**Stdout invariant.** The script's result is its stdout only (`_format_execution_result`,
`code_executor_tool.py:709-741`). The SDK and the bootstrap never write to stdout. Bridge traffic uses files and
separate execs, so stdout parsing is undisturbed.

**Switch and limit (decision: customer config component).** One component in
`config/customer/customer-config.yaml`, appended to the `components:` list (existing entries untouched):
`id: "features:workspaceScriptBridge"`, `settings: {enabled: false, timeoutSeconds: 120, name, description}`.
`enabled` is the environment-wide switch (default off). `timeoutSeconds` is the tool-calling limit; other runs
keep `execution_timeout` (30 s). `CodeExecutorConfig` gets no new fields and no `CODE_EXECUTOR_TOOL_CALLING_*`
variables. The `features:` prefix also gives the existing load-time env override `FEATURE_WORKSPACE_SCRIPT_BRIDGE`
for `enabled` (`customer_config.py:123-139`; applies only where the component exists in the YAML). This replaces the
earlier `CodeExecutorConfig`/env decision.

**Where it is read (decision: fresh read per run, in the service).** `AgentWorkspaceService.execute_workspace_script`
(the only runner builder) calls `customer_config.is_feature_enabled("workspaceScriptBridge")` and
`customer_config.get_feature_setting("workspaceScriptBridge", "timeoutSeconds")` on every run (synchronous, no
I/O, `customer_config.py:317-343`) and passes the result to the runner as `tool_calling_timeout: float | None`
(`None` = off). This is the same gate-at-assembly pattern as `toolkit_service.py:283`, keeps `codemie_tools`
free of config lookups, and goes through the shared resolution (runtime, then override snapshot, then YAML). The
YAML itself is loaded once at process start (`customer_config.py:346`), so a ConfigMap change still needs a pod
restart. Making it a live admin toggle later is one declaration in `customer_config_declarations.py` (its
`INPUT` field type is a string, so `timeoutSeconds` would be parsed); no runner change. It is not declared now.

**Timeout value handling.** Missing, non-numeric or non-positive `timeoutSeconds` falls back to 120 with a warning.
The runner then clamps: `limit = min(timeout, session_timeout - 60)` using `CodeExecutorConfig.session_timeout`; a
clamp logs a warning, and if `limit <= 0` tool calling is switched off for the run with a warning. Nothing raises,
so a bad config never fails a run.

**Per-run flag (decision: explicit parameter).** `AgentWorkspaceService.execute_workspace_script(..., tool_calling=False)`
and `WorkspaceScriptRunner.tool_calling_timeout: float | None = None`. The flag exists only on the runner path,
not on `CodeExecutorTool`. Nothing in this sub-task sets it to true outside tests; sub-task 2 derives it from run
context. Effective tool calling requires the switch AND the flag AND pooled mode. With the switch on and job mode,
the flag is ignored, a warning is logged, the SDK stays unconfigured and the limit stays 30 s. Effective runs use
the limit for the wrapper call only; snapshot, export and health checks keep `execution_timeout`.

**Side channel.** A daemon thread per effective run, started inside `_execute_code_sandbox` after the pod lock is
taken (the runner passes an optional channel argument; the generic tool never does), stopped and joined in
`finally` before the lock is released. It uses its own `KubernetesClientManager` client, never the session's blocked
one. Pod name from `session._codemie_pod_name`, container from `session._session.container_name`. All pod-side
commands use only `sh`, `cat`, `mv`, `rm` and `python3`, which lets the test stand-in run the same argv locally.
One exec per 0.5 s poll returns all pending requests, each read bounded to cap+1 bytes. Responses go over exec
**stdin**, not argv, because argv is limited to about 128 KB per argument. The write is length-prefixed
(`python3 -c` reads exactly N bytes from stdin, `os.replace`s, removes the request), because a `v4.channel.k8s.io` exec has
no stdin half-close: `cat > tmp` would never see EOF. Every exec has a 15 s deadline; a stalled one is closed and retried. Failed
execs retry 3 times with backoff; a persistent failure stops the thread, and the script then gets an SDK timeout
error, never a hang. The per-call SDK wait is a constant (100 s) below the minimum 120 s limit.

**Size cap.** 256 KiB per request and per response. The SDK rejects an oversize request before writing. The backend
answers an oversize request or handler result with error `payload_too_large`, so the SDK never receives an
oversize response.

**Timeout.** On `SandboxTimeoutError`, under the lock and after stopping the thread: run a cleanup exec that reads
`pid` and start time from the run's folder and sends SIGKILL only if `/proc/<pid>/stat` start time matches and
its cmdline contains `SANDBOX_`. It never kills by name pattern, so other conversations' processes are safe and
nothing depends on `ps` or `pkill`. Then remove the folder and raise the existing timeout `ToolException` with the
effective limit.

**Session lifetime (decision).** `llm_sandbox` closes a session at `session_timeout` (300 s) and the manager TTL is
300 s. `SandboxSessionManager.get_session` gets an optional `min_remaining_seconds`; for an effective run it is
the effective limit + 60. A session with less life left is recreated by the same path as TTL expiry. The clamp
above guarantees this is satisfiable.

**Pod lock (decision: accept).** The lock is still held for the whole run, up to the limit for effective runs only.
The channel's exec calls do not use the lock or the session, so there is no deadlock. Log `tool_calling_run` with
lock wait and hold durations. A lock-wait bound is out of scope.

**Isolation model (no hiding, no chmod).** The folder is inside the workspace root because the unchanged guard
rejects absolute paths and anything outside the root (`filesystem_policy.py:217-236`); no allowed path outside the
root exists for the SDK. The script can list and read the folder, and the folder name is not a secret. Same uid
means file modes add nothing, so none are set. Isolation rests on the per-user, per-conversation script root
(`<base>/<user>/<conversation>`), the per-pod lock that serialises runs on a pod, and the pod filesystem
(different pods share nothing). Agent workspace tools read the DB, not the pod, so they never see it.

**Cleanup, sweep, snapshot.**
- Folder removed in `finally` on success, error and timeout, before the snapshot.
- Sweep runs under the pod lock, immediately before the run: remove every other child of `.codemie_bridge/` in
  this workdir.
- Snapshot pruning is mandatory, not only cleanup: the snapshot code prunes `.codemie_bridge` from its `os.walk`
  (there is no "before" snapshot; leftovers would count as changed files and be synced), and `_is_system_output_path`
  also excludes it as a backstop. Nothing under it reaches `_sync_execution_files`.

**Deployment and UI.**
- Backend serves the component through `GET /v1/config` (`routers/customer_config.py:46-48`), which returns only
  components with `enabled: true`. The UI stores the list as is and looks entries up by id (`appInfo.ts`
  `fetchCustomerConfig`, `useFeatureFlag`); unknown ids are ignored and nothing iterates over them. So the switch is
  visible to the UI with no `codemie-ui` change. A UI consumer, and typing `timeoutSeconds` in its `ConfigItem`, are
  follow-ups in that repo.
- Helm: the chart mounts ConfigMap `codemie-customer-config` at `/app/config/customer` (`deploy-templates/values.yaml`
  lines 640-650), which replaces the repo default file. A deployment whose ConfigMap lacks the component gets the
  feature off. To enable, add the component to the ConfigMap (or set `FEATURE_WORKSPACE_SCRIPT_BRIDGE=true` where
  the component exists) and restart. Document this in `code_executor/README.md`; no chart change.

## Tests (TDD, mirror `src/` under `tests/`)

- SDK file only, loaded by path via `importlib.util`: AST import allowlist (`json|os|time|uuid`, no `codemie*`);
  size cap (<= 12 KiB); clean-interpreter subprocess adds only `sys.stdlib_module_names` modules and no `codemie*`;
  no `print`/stdout writes in SDK and bootstrap.
- SDK under the real, unchanged guard via `subprocess.run([sys.executable, "-c", wrapper], cwd=root)`.
- Protocol vs stand-in: a local-directory exec runner runs the same argv; covers echo, sequential calls, unknown
  op, oversize request and response, exec retry and persistent failure, unconfigured SDK error.
- Service: patch `customer_config` in `codemie.service.agent_workspace_service` (mock with `is_feature_enabled` and
  `get_feature_setting`, as other tests do): component absent or disabled, flag off, enabled with valid, missing and
  invalid `timeoutSeconds`. One test loads a temporary YAML via `CustomerConfig` with the env override.
- Runner with a fake session and a chat-uploaded script: `tool_calling_timeout` None vs set, jobs mode, effective run,
  limit vs 30 s choice, clamp against `session_timeout`, timeout cleanup (kill only on matching start time),
  snapshot exclusion incl. leftover folder, sweep, cleanup after error. Generic `CodeExecutorTool` wrapper has no SDK.
- Session manager: `min_remaining_seconds` recreation.
- A manual minikube run of the spike scenario is recorded in the MR, not automated.

## Acceptance criteria

The ten criteria of the task file, with these clarifications: "clear error" means `ToolCallError` with a stable
message; the switch off or flag off keeps the 30 s limit and existing scripts unchanged; exchange data never
appears in `last_execution_files` or the workspace repository; `filesystem_policy.py` has no diff; the SDK and
bootstrap write nothing to stdout; the switch and limit come from the `features:workspaceScriptBridge` component.

## Trust boundary

LLM-written workspace scripts run via `execute_workspace_script` do get the SDK when the switch and the per-run
flag are on. The generic code executor is not a gate for that and is excluded entirely. Containment is: switch off
by default, per-run flag set only on the workspace-script path, and (sub-task 2) authorization derived from the
runner's identity, never from the request payload.

## Non-goals

- Real tools, authorization, run-context propagation, confirmation (sub-task 2). No caller turns the flag on yet.
- The generic code executor (`CodeExecutorTool`, `build_guarded_python_script`) gets neither the SDK nor the bridge.
- Any sandbox image change; job-mode bridge (job mode only gets the unconfigured SDK); multi-thread SDK calls; SDK
  features beyond request/response.
- Any change to `filesystem_policy.py` or the audit-hook rules; folder permissions or hiding.
- New `CodeExecutorConfig` fields or `CODE_EXECUTOR_TOOL_CALLING_*` variables.
- An admin-editable declaration in `customer_config_declarations.py`; Helm chart changes.
- Any change in `codemie-ui` (none is needed for exposure; a UI consumer is a follow-up in that repo).
- A lock-wait bound or pod-lock redesign; cross-replica locking; a general orphan-process reaper.
- Removal of `SANDBOX_<uuid>.py` left by a timed-out worker.

## Residual risks

- The lock is per backend process. A same-conversation concurrent run from another replica on a shared pod could get
  its folder swept; that run gets an SDK timeout, with no cross-user leak. Whether replicas share pods is
  unverified.
- Exchange data can remain on a pod after a backend crash until the next run in that conversation, or a pod restart.
- Per-run API-server load is about two exec websockets per second per effective run.
- Customer ConfigMaps that replace the default file must add the component before the feature can be enabled.
