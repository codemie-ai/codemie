# EPMCDME-15401 Channel and SDK with Echo Handler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A script run by `WorkspaceScriptRunner` on a pooled pod can call the backend during its run and get an `echo` answer.

**Architecture:** A stdlib-only SDK module is registered by the workspace-script wrapper. A backend side thread (own k8s exec client) serves a per-run exchange folder in the workspace root. The switch and limit come from the customer-config component `features:workspaceScriptBridge`, read per run in the service. The design is fixed in `spec.md` (same dir); read it and `technical-analysis.md` first.

**Conventions:** Commit per task using the repository's existing convention (subject starts with `EPMCDME-15401: `). Strict typing everywhere (no `Any`, `X | None`, dataclass/TypedDict). New files carry the Apache header. Tests mirror `src/` under `tests/`. `filesystem_policy.py` must have no diff; `CodeExecutorConfig` gets no new fields; `codemie-ui` is untouched. Paths are under `src/codemie_tools/data_management/` unless stated (`CE/` = `code_executor/`).

---

### Task 1: Customer-config component and README note

**Files:** Modify `config/customer/customer-config.yaml` (append after the last component, :239; other entries untouched), `CE/README.md`. Test `tests/codemie/configs/test_customer_config.py`.

- [ ] Append component `id: "features:workspaceScriptBridge"` with `settings: {enabled: false, timeoutSeconds: 120, name, description}`. README: a short section on the component, that a customer ConfigMap replacing the default file must add it, the `FEATURE_WORKSPACE_SCRIPT_BRIDGE` override (only where the component exists), and that a restart is needed.

Test-first: yes — loading the repo YAML yields the component with `enabled` false and `timeoutSeconds` 120, `is_feature_enabled("workspaceScriptBridge")` is false, and the pre-existing component ids are unchanged.

### Task 2: Runtime SDK module

**Files:** Create `CE/runtime_sdk/__init__.py` (header only), `CE/runtime_sdk/codemie_runtime_sdk.py`. Test `tests/codemie_tools/data_management/code_executor/runtime_sdk/test_codemie_runtime_sdk.py`.

- [ ] Single module, imports only `json`, `os`, `time`, `uuid`, relative paths only, top level holds only constants, definitions and `_state = None`; never writes to stdout. Constants live here only (no `constants.py`): `PROTOCOL_VERSION`, `BRIDGE_DIR_NAME = ".codemie_bridge"`, `REQ_PREFIX`/`RESP_PREFIX`, `MAX_PAYLOAD_BYTES = 256 * 1024`, `CALL_TIMEOUT_SECONDS = 100.0`. Public surface:

```python
class ToolCallError(Exception): ...
def _configure(exchange_dir: str) -> None: ...   # bootstrap only
def call(op: str, payload: dict[str, object], timeout: float | None = None) -> object: ...
```

`call` rejects an oversize request before writing, writes `req.<id>.json` via tmp + `os.replace`, polls `resp.<id>.json` (0.1 s), deletes it, returns `result` or raises `ToolCallError` on error response, timeout, or unconfigured state ("tool calling is not available in this run").

Test-first: yes — AST import allowlist (`json|os|time|uuid`, no `codemie*`); file <= 12 KiB; clean-interpreter subprocess import adds only `sys.stdlib_module_names` and no `codemie*`; no `print`/`sys.stdout` usage; unconfigured `call` and oversize request raise `ToolCallError` (module loaded by path via `importlib.util`).

### Task 3: Wrapper bootstrap under the unchanged guard

**Files:** Modify `CE/sandbox_guard.py:50-68` (`build_guarded_workspace_script` only). Test `tests/codemie_tools/data_management/code_executor/test_sandbox_guard.py`.

- [ ] The launcher (inside the guarded exec, before `runpy`) executes the SDK source (read as text from the SDK file, embedded with `!r`) into a `types.ModuleType("codemie_runtime_sdk")` and registers it in `sys.modules`, for every workspace-script wrapper (pooled and jobs). New optional keyword `exchange_dir: str | None = None`: when set, also call `_configure(exchange_dir)`, create the folder and write `pid` and start time (field 22 of `/proc/self/stat`) into it; when `None` the SDK stays unconfigured. `build_guarded_python_script` is untouched. Update any existing wrapper-content assertions.

Test-first: yes — run the wrapper via `subprocess.run([sys.executable, "-c", wrapper], cwd=root)`: with `exchange_dir` a script doing `import codemie_runtime_sdk` works and the `pid` file matches the child; without it `call()` raises `ToolCallError`; stdout carries only script output; `build_guarded_python_script` output contains no SDK; `filesystem_policy.py` has no diff.

### Task 4: Side channel

**Files:** Create `CE/tool_call_channel.py`. Test `tests/codemie_tools/data_management/code_executor/test_tool_call_channel.py`.

- [ ] Imports constants from the SDK module. New symbols:

```python
class ExecRunner(Protocol):
    def __call__(self, argv: Sequence[str], stdin: bytes | None = None) -> ExecResult: ...
def new_exchange_dir_name() -> str: ...          # "<epoch>-<uuid4hex>"
class KubernetesExecRunner: ...                  # own KubernetesClientManager client, pod + container binding
class ToolCallChannel:
    def sweep(self) -> None: ...                 # rm every other child of .codemie_bridge/
    def start(self) -> None: ...                 # daemon thread, 0.5 s poll
    def stop(self) -> None: ...                  # signal + join
    def cleanup(self, *, kill: bool) -> None: ...  # optional pid kill, then rm -rf folder
```

  One poll exec (`python3`) returns all pending requests, each read bounded to cap+1 bytes. Responses written over exec stdin (`cat > tmp && mv tmp target`), not argv. Handler registry holds only `echo`; unknown op gives `unknown_op`; oversize request or result gives `payload_too_large`. Exec retried 3 times with backoff; persistent failure stops the thread (script then times out in the SDK). Kill reads `pid`+start time from the run folder and sends SIGKILL only if `/proc/<pid>/stat` start time matches and cmdline contains `SANDBOX_`; never by name pattern. Pod-side commands only `sh`, `cat`, `mv`, `rm`, `python3`. The channel never uses the session or the pod lock.

Test-first: yes — stand-in `ExecRunner` running the same argv locally in a tmp dir plus the real SDK: echo, sequential calls, unknown op, oversize request and response, retry then success, persistent failure ends in SDK timeout, `sweep` keeps only the current folder, `cleanup(kill=True)` kills a matching child and leaves a process with mismatched start time alive.

### Task 5: Session lifetime guard

**Files:** Modify `CE/session_manager.py:200-291,435-470` (`get_session`, `_try_reuse_session`, `_is_session_healthy`), `CE/code_executor_tool.py:520-571` (`_sandbox_session`, `_acquire_session`). Test `tests/codemie_tools/data_management/code_executor/test_session_manager.py`.

- [ ] Add optional `min_remaining_seconds: float | None = None` threaded through those methods; when remaining TTL (`_session_ttl_seconds` minus age in `_session_timestamps`) is below it, close and recreate through the existing TTL-expiry path.

Test-first: yes — a session aged 200 s of 300 is reused with `None` and recreated with `180`.

### Task 6: Channel hook in `_execute_code_sandbox`

**Files:** Modify `CE/code_executor_tool.py:639-693`. Test `tests/codemie_tools/data_management/code_executor/test_sandbox_dispatch.py`.

- [ ] Add optional `channel: ToolCallChannel | None = None` and `timeout: float | None = None` (defaults preserve behaviour and `config.execution_timeout`). With a channel, inside the pod lock: `sweep()`, `start()`, run with `timeout`; `finally` `stop()` then `cleanup(kill=False)`; on `SandboxTimeoutError` `stop()`, `cleanup(kill=True)` before raising the existing `ToolException`, message reporting the effective limit. Log `tool_calling_run` with lock wait and hold durations. Calls without a channel behave exactly as before.

Test-first: yes — mocked session: without channel `session.run` gets 30 s and no channel calls; with channel the order is sweep, start, run(limit), stop, cleanup; the timeout path calls `cleanup(kill=True)` and raises `ToolException` naming the limit; the error path still cleans up.

### Task 7: Runner tool-calling run, clamp, snapshot pruning

**Files:** Modify `workspace/execute_workspace_script_tool.py:51-52,63-81,108-114,157-213`. Test `tests/codemie_tools/data_management/workspace/test_execute_workspace_script_tool.py`.

- [ ] Add `tool_calling_timeout: float | None = None` (`None` = off) to `WorkspaceScriptRunner`. Effective limit = `min(tool_calling_timeout, config.session_timeout - 60)`; a clamp logs a warning, `<= 0` turns tool calling off for the run with a warning; nothing raises. In jobs mode a set value logs a warning and is ignored (limit stays 30 s, SDK unconfigured).
- [ ] In the pooled `_execute_sandbox_script` when effective: generate the exchange dir name and pass it to `_build_script_wrapper` as `exchange_dir`, acquire the session with `min_remaining_seconds=limit + 60`, build the channel from `_codemie_pod_name` and `_session.container_name` (a `_build_channel` seam), and call `_execute_code_sandbox(..., channel=..., timeout=limit)`. Snapshot, export and health checks keep `execution_timeout`.
- [ ] `_get_sandbox_file_snapshot` code prunes `.codemie_bridge` from `os.walk` at the root; `_is_system_output_path` also excludes it as a backstop.

Test-first: yes — fake session and chat-uploaded script: `None`, jobs mode and clamp-to-off give no exchange dir and 30 s; a set value gives the limit and the exchange-dir wrapper; `session_timeout=150` with `120` clamps to 90; a leftover `.codemie_bridge/x/req.1.json` never appears in `last_execution_files` (run the snapshot code against a tmp dir); channel cleanup runs after a script error.

### Task 8: Service reads the customer-config component

**Files:** Modify `src/codemie/service/agent_workspace_service.py:502-517` (`execute_workspace_script`). Create test `tests/codemie/service/test_agent_workspace_service_tool_calling.py`.

- [ ] Add `tool_calling: bool = False`. On every call, when the flag is true and `customer_config.is_feature_enabled("workspaceScriptBridge")`, read `get_feature_setting("workspaceScriptBridge", "timeoutSeconds")`, parse to a positive float (missing, non-numeric or non-positive falls back to 120 with a warning) and pass it as `tool_calling_timeout` to the runner; otherwise pass `None`. Import `customer_config` at module level so tests can patch it.

Test-first: yes — patch `customer_config` in the service module: component absent or disabled gives `None`; flag off gives `None`; enabled gives 120 for valid, missing and invalid values and 45 for `45`; one test loads a temporary YAML via `CustomerConfig` with the `FEATURE_WORKSPACE_SCRIPT_BRIDGE` override.

---

**Negative-constraint pass:** no `filesystem_policy.py` edit and no chmod/hiding (Tasks 3, 4; guard test asserts no diff); no `CodeExecutorConfig` fields or `CODE_EXECUTOR_TOOL_CALLING_*` (Tasks 1, 8 use customer config); no `customer_config_declarations.py` entry, Helm change or `codemie-ui` change (Task 1 touches YAML and README only); existing YAML entries untouched (Task 1 test); generic `CodeExecutorTool` and `build_guarded_python_script` untouched (Task 3 test); SDK and bootstrap write no stdout (Tasks 2, 3); no name-pattern kill (Task 4); side channel avoids the session and lock (Task 4); responses via stdin, not argv (Task 4); no job-mode bridge (Task 7 warns and stays off); no lock redesign or orphan reaper (Task 6 logs only); no image change; no caller sets the flag true (Task 8); bad config never raises (Tasks 7, 8).
