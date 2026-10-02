# Jobs-Mode Script Tool-Call Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the script tool-call bridge against `BatchJobRunner` Jobs and delete the pooled-only bridge wiring.

**Architecture:** `BatchJobRunner.run()` gains keyword-only `exchange_dir`/`tool_calling_timeout` (defaults `None` = today's behaviour). Once the Job pod is Running it starts the existing `ToolCallChannel` (unchanged, pod-agnostic) bound to that pod and container `"executor"`; it stops and cleans the channel right after the done sentinel and before any download. Tasks are ordered so the suite stays green after each one: add jobs support, wire the caller, then delete pooled symbols, then docs.

**Tech Stack:** Python, pytest (`unittest.mock`), kubernetes client. Strict typing: full annotations, no `Any`, `X | None`.

**Conventions:** Commit per task using the repository's existing convention (subjects start `EPMCDME-15401: `). Spec: `spec.md`; research: `technical-analysis.md` (same dir). Run tests with `poetry run pytest <file> -q`. Line numbers are as of HEAD `e3b3d866a` and drift as tasks land. Paths are relative to `src/codemie_tools/data_management/` unless they start with `tests/`. Remove imports that become unused (ruff).

---

### Task 1: Widen Job deadline for a tool-calling budget

**Files:** Modify `code_executor/batch_job_runner.py` (`run` :154-199, `_build_manifest` :209-211, `_create_job` :297); Test `tests/codemie_tools/data_management/code_executor/test_batch_job_runner.py`

**Test-first: yes — with `tool_calling_timeout=120` and `execution_timeout=5`, the created manifest has `activeDeadlineSeconds == 180` and the shared deadline passed to `_wait_for_pod_running` is about now+180; with `None` it stays `5+60`.**

- [ ] Add keyword-only `tool_calling_timeout: float | None = None` to `run()`. Thread it through `_create_job`/`_build_manifest` (new optional third parameter, default `None`).
- [ ] Add one helper used by both the manifest and the `deadline` line: `_budget_seconds(self, tool_calling_timeout: float | None) -> float` returning `max(self.config.execution_timeout, tool_calling_timeout or 0) + _DEADLINE_BUFFER_SECONDS`. `activeDeadlineSeconds = int(budget)`.
- [ ] Follow the existing `_patch_runner_internals` test style; assert the `deadline` via `runner._wait_for_pod_running.call_args.args[1]` with `time.monotonic` patched.

### Task 2: Exclude the bridge folder from changed files (defense in depth)

**Files:** Modify `code_executor/batch_job_runner.py` (`_download_changed_files` :361-368; import `BRIDGE_DIR_NAME` from `code_executor.runtime_sdk.codemie_runtime_sdk`); Test `test_batch_job_runner.py`

**Test-first: yes — with `_snapshot_workdir` returning `{"a.txt": "h1", ".codemie_bridge/x/req.1.json": "h2", ".codemie_bridge_other.txt": "h3"}` and empty baseline, `_download_exports` is called with `["a.txt", ".codemie_bridge_other.txt"]` only.**

- [ ] In the `changed_paths` comprehension drop paths whose first segment (`rel_path.split("/", 1)[0]`) equals `BRIDGE_DIR_NAME`. Host-side only; do not touch `_snapshot_workdir` or its in-pod snippet; no `script.py`/`.stderr` filter (separate ticket).

### Task 3: Channel lifecycle and ordering in `run()`

**Files:** Modify `code_executor/batch_job_runner.py` (`run`; import `KubernetesExecRunner`, `ToolCallChannel` from `code_executor.tool_call_channel`; check for an import cycle); Test `test_batch_job_runner.py`

**Test-first: yes — a recording fake (patch `ToolCallChannel`/`KubernetesExecRunner` in the module, side effects append to one `events` list, plus `_wait_for_sentinel`, `_download_exports`, `_snapshot_workdir`) asserts `events` equals `[start, upload, sentinel, stop, cleanup(kill=False), exports, snapshot]`. A second test asserts `exchange_dir=None` never constructs a channel.**

- [ ] Add keyword-only `exchange_dir: str | None = None` to `run()`.
- [ ] After `_wait_for_pod_running` returns, when `exchange_dir is not None`, call `self._start_channel(pod_name, workdir, exchange_dir)`. It builds `KubernetesExecRunner(pod_name=..., container_name="executor", namespace=self.config.namespace, workdir=workdir, kubeconfig_path=self.config.kubeconfig_path or None)`, then `ToolCallChannel(runner, exchange_dir)`, calls `start()` and returns the channel. Start it before `_upload_payload`.
- [ ] Right after `_wait_for_sentinel` returns, call `channel = self._close_channel(channel)` before `_download_exports`. New helper:

```python
@staticmethod
def _close_channel(channel: ToolCallChannel | None) -> None:
    """Stop the polling thread and drop the exchange folder; idempotent, never raises."""
    if channel is None:
        return
    try:
        channel.stop()
        channel.cleanup(kill=False)
    except Exception:  # noqa: BLE001 - housekeeping must not mask the run result
        logger.warning("Tool-call channel shutdown failed", exc_info=True)
```

  Make it return `None` so the caller rebinds `channel = None` and the second call in Task 4 is skipped.
- [ ] `sweep()` is never called here (each Job has a fresh emptyDir).

### Task 4: Error-path safety net and no-leak guarantee

**Files:** Modify `code_executor/batch_job_runner.py` (`run` `finally`); Test `test_batch_job_runner.py`

**Test-first: yes — (a) `_wait_for_sentinel` raising `ToolException` still gives one `stop` and one `cleanup(kill=False)` before `_delete_job`, and `sweep` and `cleanup(kill=True)` are never called; (b) on the happy path stop/cleanup run exactly once; (c) with a channel whose `cleanup` does nothing and a snapshot containing `.codemie_bridge/x/req.1.json`, `run()` returns `JobResult` with no bridge path in `changed_files` or `exported_files`.**

- [ ] In the existing `finally`, call `self._close_channel(channel)` before `_delete_job` and semaphore release. `channel` is initialised to `None` before the `try`; a failing `_start_channel` must not skip Job deletion.
- [ ] A raising `channel.stop` must not mask the original exception (covered by the helper's catch).

### Task 5: Wire the jobs branch in `WorkspaceScriptRunner`

**Files:** Modify `workspace/execute_workspace_script_tool.py` (`_resolve_tool_calling_limit` :161-200, `_execute_sandbox_script_jobs` :349-385, imports :46-52); Test `tests/codemie_tools/data_management/workspace/test_execute_workspace_script_tool.py`, `tests/codemie_tools/data_management/code_executor/test_sandbox_dispatch.py`

**Test-first: yes — a JOBS-mode runner with `tool_calling_timeout=120.0` builds the wrapper with `exchange_dir` starting `.codemie_bridge/` and calls `BatchJobRunner(...).run` with that same `exchange_dir` and `tool_calling_timeout=120.0`; with `None`/`0` it passes neither and the kwargs equal today's four.**

- [ ] `_resolve_tool_calling_limit`: delete the `SandboxMode.JOBS` hardcoded `None` and its `tool_calling_ignored` warning. For JOBS return the requested value (non-positive still warns and returns `None`) and skip the session clamp. The shared-mode clamp stays until Task 6.
- [ ] `_execute_sandbox_script_jobs`: call `self._resolve_tool_calling_limit()` itself. When not `None`, set `exchange_dir = exchange_dir_path(new_exchange_dir_name())`, pass it to `_build_script_wrapper(..., exchange_dir=exchange_dir)`, and add `exchange_dir=`/`tool_calling_timeout=` to the `run(...)` kwargs only in that case.
- [ ] Rewrite: `TestJobsModeIgnoresToolCalling` becomes a jobs tool-calling test (enabled passes exchange dir and timeout; disabled does not). Rewrite the two jobs cases in `TestResolveToolCallingLimit` (:204-214): a set value is returned unchanged and logs no `jobs_mode` warning. Keep `TestWorkspaceScriptRunnerJobsMode` (test_sandbox_dispatch.py:359-413); its exact kwargs assertion still holds with the bridge off. Add a case with the bridge on asserting `sb.assert_not_called()` and the extra kwargs.

### Task 6: Remove pooled bridge wiring from `WorkspaceScriptRunner`

**Files:** Modify `workspace/execute_workspace_script_tool.py` (:59-60, :71-93, :161-226, :301-347); Test `test_execute_workspace_script_tool.py`

**Test-first: yes — a SHARED-mode runner with `tool_calling_timeout=120.0` calls `_sandbox_session("/home/codemie/u/conv")` with no kwargs, calls `_execute_code_sandbox(session, wrapper)` with no kwargs, and builds the wrapper with `exchange_dir=None`.**

- [ ] Delete `SESSION_HEADROOM_SECONDS`, `_session_pod_name`, `_session_container_name`, `_build_exec_runner`, `_build_channel`, `_build_sweep_hook`, and the `sweep_stale_exchange_folders`/`KubernetesExecRunner`/`ToolCallChannel` imports. Keep `_is_bridge_path`, `_is_system_output_path`, and the snapshot bridge pruning.
- [ ] `_resolve_tool_calling_limit`: drop the session-timeout clamp and its warnings (keep the non-positive check and the jobs behaviour from Task 5).
- [ ] `_execute_sandbox_script`: remove the `tool_calling_limit`/`exchange_dir` computation before the JOBS early return and every channel, sweep and `min_remaining_seconds` use below it. The pooled body becomes plain `_sandbox_session(user_workdir)` and `_execute_code_sandbox(session, wrapper_code)`.
- [ ] Delete tests: `TestPooledRunWithToolCalling`, `TestChannelConstruction`, `TestChannelPodNameFromLlmSandboxSession`, `TestChannelCleanupAfterScriptError`, the clamp cases in `TestResolveToolCallingLimit` (:186-196). In `TestPooledRunWithoutToolCalling` delete the sweep-hook and no-pod-binding tests (:272-303) and assert no kwargs at the `execute_code` call. Remove `build_channel` from `_PooledRun`/`_pooled_run`. Keep `TestSnapshotPrunesBridgeFolder`.

### Task 7: Remove the channel branch from `_execute_code_sandbox`

**Files:** Modify `code_executor/code_executor_tool.py` (`_sandbox_session` :529-543, `_acquire_session` :545-585, `_execute_code_sandbox` :653-733, `_run_locked` :735-767); Test `tests/codemie_tools/data_management/code_executor/test_sandbox_dispatch.py`

**Test-first: yes — `_execute_code_sandbox(session, code)` still runs `session.run(code, timeout=config.execution_timeout)` inside the pod lock, and a `SandboxTimeoutError` reports the config timeout; `inspect.signature(_execute_code_sandbox)` has no `channel`/`pre_run`/`timeout` and `_sandbox_session`/`_acquire_session` have no `min_remaining_seconds`.**

- [ ] `_execute_code_sandbox`: drop the `channel`, `timeout`, `pre_run` params, the `tool_calling_run` lock-hold log, and the `ToolCallChannel` type import if unused. Keep the pod-lock mechanism and the timeout-to-`ToolException` mapping (effective timeout is `config.execution_timeout`). `_run_locked` becomes a plain `session.run(code, timeout=timeout)`.
- [ ] Drop `min_remaining_seconds` from `_sandbox_session` and `_acquire_session`; `_acquire_session` stops passing it to `get_session`.
- [ ] Tests: delete `TestExecuteCodeSandboxChannel` channel, sweep, kill and `tool_calling_run` log cases (:508-630). Keep and simplify two: runs under the lock with config timeout, and timeout raises `ToolException`. Delete the two `min_remaining_seconds` tests at :44-63; keep :34-42. Adjust the `_sandbox_session` tests.

### Task 8: Remove `min_remaining_seconds` from the session manager

**Files:** Modify `code_executor/session_manager.py` (`get_session` :200-261, `_try_reuse_session` :263-296, `_is_session_healthy` :446-482); Test `tests/codemie_tools/data_management/code_executor/test_session_manager.py`

**Test-first: no — pure deletion of a now-unused parameter and its clamp branch; TTL behaviour is covered by the existing tests that stay.**

- [ ] Remove the parameter from the three signatures and docstrings, the `remaining`/too-little-TTL branch (:470-482) and the forwarding at :231, :239, :283. TTL expiry, pool selection and locking are untouched.
- [ ] Delete tests at test_session_manager.py:289-336 (`recreates_session_below_min_remaining`, the lock-held variant, `get_session_forwards_min_remaining_seconds...`); keep :280 (aged session reused), renaming it to drop "without_min_remaining".

### Task 9: Documentation and admin-settings copy

**Files:** Modify `code_executor/README.md` (:239-245), `../../codemie/service/customer_config_declarations.py` (`WORKSPACE_SCRIPT_BRIDGE.description` :208-211); Test `tests/codemie/service/` (find the existing customer-config declarations test file; else add `test_customer_config_declarations_bridge.py`)

**Test-first: yes — `WORKSPACE_SCRIPT_BRIDGE.description` does not contain "pooled" (case-insensitive) and still contains "Applies to runs that start after the change".**

- [ ] Description: drop the "Works only with the pooled sandbox mode." sentence.
- [ ] README "Workspace Script Bridge": replace the pooled-only and `unavailable` bullet with a statement that the bridge works in `sandbox-jobs` (default), that the Job deadline widens to `max(execution timeout, timeoutSeconds) + 60s` when it is on, and that the exchange folder is removed before exports and changed files are pulled. In the smoke check, drop "set pooled mode and restart if the mode was changed".

---

## Negative-constraint pass

- Do not change `SandboxSessionManager` core pooling or the `_execute_code_sandbox` pod lock: Tasks 7-8 touch only the bridge parameters and keep TTL, selection and locks.
- Do not change `codemie_runtime_sdk.py`, `ToolCallChannel`, or `pod_scripts/*.py`: no task edits them.
- Do not fix `_snapshot_workdir` or add a `script.py`/`.stderr` filter: Task 2 filters on the host and only for the bridge folder.
- No sub-task 2 tool dispatch: the echo handler stays. No queueing semantics, no Helm or deploy changes.
- Do not call `sweep()` or `kill=True` in jobs mode: Tasks 3-4 assert both.

## Self-review

- Spec coverage: §1 is Tasks 6-8, §2 is Tasks 1 and 3, §3 is Tasks 3-4, §3b is Task 2, §4 is Task 5, §5 is Task 1, §6 is Tasks 3-4 (emptyDir workdir, no sweep), §7 is Task 9. The plan adds no acceptance-criteria section because `spec.md` carries them.
- Type consistency: `exchange_dir: str | None` and `tool_calling_timeout: float | None` are the same on `run()`, `_build_manifest` and the caller. `_close_channel` and `_start_channel` names are used consistently in Tasks 3-4.
- Dependency note: the ordering keeps the suite green because Tasks 1-5 only add behind `None` defaults; Task 6 stops using the symbols that Task 7 then deletes.
