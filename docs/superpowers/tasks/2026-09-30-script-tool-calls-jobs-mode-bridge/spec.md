# Spec: Jobs-Mode Script Tool-Call Bridge (EPMCDME-15401 follow-up)

## Context

Sub-task 1 built the script tool-call bridge (`codemie_runtime_sdk`, `tool_call_channel.py`,
`features:workspaceScriptBridge`) wired only into pooled (`sandbox-shared`) mode. Pooled mode is
not deployed anywhere; `sandbox-jobs` (the default) is the only mode in real use. This spec removes
the pooled-only bridge wiring and re-implements the bridge against `BatchJobRunner`
(`src/codemie_tools/data_management/code_executor/batch_job_runner.py`).

## Approach

### 1. Remove pooled-mode bridge wiring (bridge-only, not pooled infra itself)

- `code_executor_tool.py`: delete `_build_channel`, `_build_exec_runner`, `_session_pod_name`,
  `_session_container_name`, `_build_sweep_hook`; revert the channel branch inside `_run_locked`
  to a plain `session.run(...)` call with no channel start/stop/sweep/cleanup bracket.
- `session_manager.py`: remove the `min_remaining_seconds` clamp added for the bridge from
  `_is_session_healthy`; leave TTL-based pool selection/locking untouched.
- `execute_workspace_script_tool.py`: remove `SESSION_HEADROOM_SECONDS`, the `session_timeout`
  clamp branch of `_resolve_tool_calling_limit`, and the `min_remaining_seconds=` argument passed
  to `_sandbox_session` in `_execute_sandbox_script`.
- Keep untouched: `SandboxSessionManager` core pooling, `_execute_code_sandbox`'s pod-lock
  mechanism, and `_resolve_tool_calling_limit`'s customer-config lookup (`enabled`/`timeoutSeconds`);
  only the SHARED-only clamp math and the JOBS-mode hardcoded `None` go away.

### 2. New parameters on `BatchJobRunner.run()`

Two keyword-only parameters are added to `run(code, input_files, export_files, workdir,
baseline_hashes, ...)`; the return type (`JobResult`) is unchanged:

```
exchange_dir: str | None = None,
tool_calling_timeout: float | None = None,
```

- `exchange_dir` - the `.codemie_bridge/<epoch>-<uuid>` path sub-task 1 builds via
  `exchange_dir_path(new_exchange_dir_name())`. `None` (default, used by every existing caller:
  `run_via_jobs`, plain execution) means no bridge; `run()` is byte-identical to today.
- `tool_calling_timeout` - the resolved tool-calling budget in seconds (same value pooled mode
  resolved via `_resolve_tool_calling_limit()`). Only meaningful together with `exchange_dir`.

### 3. Channel lifecycle inside `run()` (ordering is a hard requirement)

When `exchange_dir is not None`, once `_wait_for_pod_running` yields `pod_name` (container is the
hardcoded `"executor"`), build `KubernetesExecRunner(pod_name, "executor", namespace, workdir)` and
`ToolCallChannel(runner, exchange_dir)` and call `channel.start()`. The channel's existing
background thread is the only poller added; it runs concurrently with `_upload_payload` and
`_wait_for_sentinel`, which is safe because every exec opens its own connection.

Happy-path order is fixed:

1. script completion sentinel observed (`_wait_for_sentinel` returns)
2. `channel.stop()` (joins the polling thread)
3. `channel.cleanup(kill=False)` (removes the exchange folder from the pod)
4. `_download_exports`
5. `_download_changed_files` (only if `baseline_hashes` given; the snapshot runs here)
6. stderr pull, `_signal_cleanup`, `_wait_for_completion`, Job deletion (unchanged)

Steps 2-3 must complete before step 4, so no snapshot or export of the workdir can ever observe live
request/response files. A `finally`-style best-effort stop/cleanup (idempotent, skipped if steps 2-3
already ran, catch + log only) remains as a second safety net for error paths (pod failed, deadline,
exec errors), so the channel never outlives the Job. `kill=False` always: on the happy path the
script has already exited, and on error paths Kubernetes kills the container when the Job is deleted.

### 3b. Defense in depth: exclude the bridge folder from changed files

If cleanup fails (logged, not raised), exchange data must still never leave the pod. In
`_download_changed_files` (batch_job_runner.py:361-368), drop from `changed_paths` every path whose
first segment is `BRIDGE_DIR_NAME` (imported from `codemie_runtime_sdk`). The filter is host-side,
so `_snapshot_workdir` and its in-pod snippet stay untouched. This is a bridge-specific exclusion
only; no general system-file filter (`script.py`, `.stderr`) is added - that is a separate ticket.

### 4. `execute_workspace_script_tool.py` wiring

- `_resolve_tool_calling_limit()` for `SandboxMode.JOBS` resolves the customer-config value as
  pooled mode did (drop the hardcoded `None`); still `None` when the feature is disabled.
- `_execute_sandbox_script_jobs` builds `exchange_dir` as pooled mode did, passes it to
  `_build_script_wrapper` (SDK bootstrap) and to `BatchJobRunner.run()` with
  `tool_calling_timeout=tool_calling_limit`, only when `tool_calling_limit is not None`.

### 5. `activeDeadlineSeconds` / shared deadline widening

`_build_manifest`'s `activeDeadlineSeconds` and `run()`'s shared Python-side `deadline` both use,
when a bridge is active:

```
budget = max(execution_timeout, tool_calling_timeout or 0) + _DEADLINE_BUFFER_SECONDS
```

With `tool_calling_timeout is None` this is exactly today's formula.

### 6. Exchange folder location and security properties carried over

The folder lives under the `workdir` emptyDir (one of the only two writable mounts). The Job's
`readOnlyRootFilesystem: True` applies to the root filesystem, not to emptyDir mounts, and the
existing upload already writes `script.py`/`.ready` there, so creating `.codemie_bridge/...` by the
script's SDK is not blocked.

`ToolCallChannel`, `pod_scripts/*.py` and the SDK are reused unchanged, so these stay in force:
pod scripts run via `python3 -I`; strict UTF-8 request decoding; the 256 KiB payload cap; the
pid+start_time+cwd kill check (not exercised in jobs mode, see `kill=False`). The stale-folder
`sweep()`/`pre_run` sweep is NOT called in jobs mode: each Job gets a fresh emptyDir workdir, so
there is nothing stale to remove.

### 7. Documentation

- `README.md` "Workspace Script Bridge" section and smoke-check steps: drop the "pooled mode only" /
  "`sandbox-jobs` raises `unavailable`" claims; describe jobs-mode support.
- `customer_config_declarations.py` `WORKSPACE_SCRIPT_BRIDGE.description`: drop pooled-only wording.

### Typing constraint

All new/changed signatures: full parameter and return annotations, `X | None`, no `Any`.

## Non-goals

- Altering `SandboxSessionManager` core pooling or `_execute_code_sandbox`'s pod-lock mechanism
  beyond the bridge-only clamp in §1.
- Sub-task 2's tool-dispatch work (real handler, run-context, authorization); the echo handler stays.
- Fixing `_snapshot_workdir`'s `import hashlib, json, os` pattern, or adding a general
  system-file filter for `script.py`/`.stderr` leaks (separate filed tickets).
- Updating sub-task 2's task.md (flagged only).
- Any change to `codemie_runtime_sdk.py`, `ToolCallChannel`'s core class/protocol, `pod_scripts/*.py`.
- New queueing/ordering semantics for concurrent tool calls.
- Helm, deploy-templates, or network-policy changes outside this repo.

## Acceptance Criteria

- Pooled-mode bridge symbols in §1 no longer exist; plain pooled execution is unchanged.
- `run()` with `exchange_dir=None` is behaviorally identical to today (no channel, no widening).
- `run()` with `exchange_dir` starts a channel bound to the Job's pod once Running and guarantees
  stop/cleanup before Job deletion on success and failure paths.
- A jobs-mode script using `codemie_runtime_sdk` with the bridge enabled gets a real (non-
  `unavailable`) response from the existing echo handler.
- Deadline and `activeDeadlineSeconds` widen to the §5 formula only when a tool-calling timeout is
  resolved; unchanged otherwise.
- Ordering test (call-order assertions on a fake runner): channel stop and cleanup happen after the
  sentinel and before `_download_exports` and the changed-files snapshot.
- The exchange folder never appears in `JobResult.changed_files` or exports, tested with a normal
  cleanup and with a simulated cleanup failure (the §3b filter is the only barrier in the latter).
- `cleanup` is invoked with `kill=False`, and `sweep()` is not called, in jobs mode.
- README.md and `WORKSPACE_SCRIPT_BRIDGE.description` no longer claim pooled-mode-only support.

## Open Risks

- Bridge-enabled Jobs can run far longer than today's 30s default and hold their semaphore slot
  longer; no capacity sizing change is in scope.
- Sub-task 2's task.md still assumes the bridge works end-to-end; needs its own update.
- `_snapshot_workdir` still leaks `script.py`/`.stderr` (known, separate ticket).
