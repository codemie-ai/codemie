# Technical Research

**Task**: sandbox pooled-mode runner wrapper exec-session code-interpreter script (EPMCDME-15401, "Bridge Channel and SDK with an Echo Handler")
**Generated**: 2026-09-29
**Research path**: filesystem

---

## 1. Original Context

task_context (verbatim requirements): The full task is in /Users/yanaasadchaya/Projects/epam/airun/codemie-dev/codemie/docs/stories/2026-09-28-custom-tools/children/script-tool-calls/subtasks/1-channel-and-sdk/task.md — read it first (Goal, Context, Scope, Acceptance Criteria, Out of Scope, Sub-steps). Ticket EPMCDME-15401: "Bridge Channel and SDK with an Echo Handler" — a script in the workspace sends a request to the backend and gets a response while it runs, through an injected stdlib-only SDK (`codemie_runtime_sdk`), on a pooled sandbox pod; a side thread in the pooled-mode runner with its own exec calls reads requests / writes responses from a unique per-run exchange folder in the workspace root; hardcoded `echo` handler; exchange folder excluded from changed-file snapshot, removed after each run, stale folders swept at next run in the conversation; on timeout stop the script's process in the pod and remove the folder; environment-wide switch (off by default) + time limit for tool-calling runs (setting, 120 s; other runs stay 30 s); tests: import allowlist for SDK+constants, clean-interpreter module check, size cap, protocol vs stand-in, runner with script uploaded in chat. Sandbox access enforcement must not be weakened.
Prior notes (orientation only, verify against code): /Users/yanaasadchaya/Projects/epam/airun/codemie-dev/codemie/docs/stories/2026-09-28-custom-tools/children/script-tool-calls/subtasks/1-channel-and-sdk/technical-analysis.md and the story at .../script-tool-calls/story.md, plus the spike at docs/stories/2026-09-28-custom-tools/spike/ if it exists.
feature_area: sandbox pooled-mode runner wrapper exec-session code-interpreter script
run_dir: /Users/yanaasadchaya/Projects/epam/airun/codemie-dev/codemie/docs/superpowers/tasks/2026-09-29-script-tool-calls-channel-sdk
Repo root: /Users/yanaasadchaya/Projects/epam/airun/codemie-dev/codemie (Python backend; guides at .ai-run/guides/). Write technical-analysis.md with "Codebase Findings" and "Risk Indicators" sections to run_dir and return the ≤400-word digest.

---

## 2. Codebase Findings

Repo: Python >=3.12 (Poetry; `llm-sandbox[k8s] ^0.3.21`, `kubernetes ^35.0.0`, pydantic, langchain-core). Source packages `src/codemie` and `src/codemie_tools`. No `.codegraph/` index. Tests: pytest (`pytest.ini`: `pythonpath = src`, `-n 2`, importlib mode).

### Existing Implementations

All under `/Users/yanaasadchaya/Projects/epam/airun/codemie-dev/codemie/src/codemie_tools/data_management/`:

- `workspace/execute_workspace_script_tool.py` (311 lines)
  - `WorkspaceScriptRunner(CodeExecutorTool)`: fields `conversation_id`, `last_execution_files`. `_get_user_workdir()` = `<workdir_base>/<sanitized user_id>/<sanitized conversation_id>` (default base `/home/codemie`). The workspace root is therefore per user + conversation.
  - `_execute_sandbox_script` (l.186): validates export paths; `SandboxMode.JOBS` goes to `_execute_sandbox_script_jobs` (BatchJobRunner). Pooled path: `with self._sandbox_session(workdir) as session` -> `_upload_files_to_sandbox` (all workspace files) -> `_validate_code_security(session, script_code)` (user script only, not the wrapper) -> `_build_script_wrapper` -> `_execute_code_sandbox(session, wrapper)` -> `_log_guard_denials` -> `_format_execution_result` (raises `ToolException` on non-zero exit) -> `_collect_sandbox_changed_files` -> `_export_files_from_execution`.
  - `_build_script_wrapper` (l.108) calls `build_guarded_workspace_script(script_path, workspace_root, max_threads, max_open_files)`. There is no hook for extra prelude or injected modules.
  - `_get_sandbox_file_snapshot` (l.157): snapshot code is a string run through `session.run(..., timeout=config.execution_timeout)`. It `os.walk`s the whole workdir, skips only `__pycache__` and `.pyc`, and opens and hashes every file, so a file that disappears mid-walk raises. `_collect_sandbox_changed_files` filters afterwards with `_is_system_output_path` (`.pyc`, `SANDBOX_<32hex>.<ext>`, venv and pip-cache dirs). The exchange folder is not excluded today. The spike confirmed response files show up in the changed-file list.
  - `ExecuteWorkspaceScriptTool` (l.260): the LangChain tool. It calls `workspace_service.execute_workspace_script`. It has no run-context or "tool calling" attribute.
- `code_executor/sandbox_guard.py`: `build_guarded_python_script(customer_code, ...)` = prelude + `install_guard()` + `exec(compile(<customer_code!r>, '<customer_code>', 'exec'), {'__name__': '__main__'})`, catching `FilesystemAccessDenied`. `build_guarded_workspace_script` passes the launcher `import runpy; runpy.run_path(<script>, run_name='__main__')` as `customer_code`. This is the natural injection point for `codemie_runtime_sdk`: it is generated by the backend, so the image is untouched.
- `code_executor/filesystem_policy.py` (`render_runtime_prelude`, l.43-990): the runtime guard.
  - `_authorize_path` rejects absolute paths (`absolute_path`, l.217-218) and anything resolving outside `WORKSPACE_ROOT`.
  - `install_guard` chdirs to the root and patches `open`, `io.open`, `os.open/listdir/scandir/stat/lstat/access/readlink/mkdir/remove/unlink/rmdir/chmod/utime/truncate`, `os.rename/replace/link`, `os.symlink`, `makedirs`, `shutil.*`, `sqlite3.connect`.
  - A `sys.addaudithook` denies process creation (`os.exec/fork/posix_spawn/system`, `subprocess.Popen`) and native library loads.
  - It sets `RLIMIT_NPROC` (`max_threads`) and `RLIMIT_NOFILE`, and sets `TMPDIR` to `<root>/.tmp`.
  - Import-context and installed-library exemptions rely on frame inspection (`_is_import_context`, `_is_installed_library_caller`, `_allow_library_workspace_access`). The behaviour of an SDK module injected into `sys.modules` under the guard is not covered by any existing test (the spike used a hand-written script).
- `code_executor/code_executor_tool.py`: `CodeExecutorTool._execute_code_sandbox` (l.639-693) resolves `pod_name = session._codemie_pod_name`, takes the singleton manager's per-pod `threading.Lock`, and runs `session.run(code, timeout=self.config.execution_timeout)` while holding it. On `SandboxTimeoutError` it raises a `ToolException` ("Code execution timed out after N seconds...") and does not touch the pod process. This is the single place where the tool-calling time limit, the side thread and timeout cleanup would attach. `_acquire_session`, `_sandbox_session` (SHARED-only contextmanager) and `_validate_export_paths` are also here.
- `code_executor/session_manager.py`: `SandboxSessionManager` singleton.
  - `_sessions` is keyed by pod name. `_try_reuse_session` reuses a session only if `session._codemie_workdir == workdir`, otherwise it reconnects and replaces the stored session (`_store_session` sets `_codemie_pod_name` and `_codemie_workdir`).
  - Sessions have a TTL of 300 s, checked in `_is_session_healthy` via `session.run("print('health_check')")`.
  - `_get_or_create_lock(pod_name)` returns the per-pod lock.
- `code_executor/session_factory.py`: builds `ArtifactSandboxSession` (KUBERNETES backend), `execution_timeout=config.execution_timeout`, `session_timeout=config.session_timeout` (300 s), `client=k8s_client`. The pod's container name is at `session._session.container_name`. `connect_to_existing_pod` retries with tenacity and recreates the client on websocket or handshake errors.
- `code_executor/llm_sandbox.py`: monkey-patches `BaseSession.run` (`_patched_session_base_run`). It writes the code to a temp file, copies it into the workdir as `SANDBOX_<uuid32>.py` (via `copy_to_runtime`, tar over exec stdin), and runs it via `execute_commands` (`sh -c "cd <workdir> && <cmd>"`) inside `_execute_with_timeout`. It also patches `copy_to_container` (64 KB chunks). The backend does not learn the generated file name, so it cannot use it to find the process.
- `code_executor/k8s_client_manager.py`: `KubernetesClientManager(kubeconfig_path)` lazily builds `CoreV1Api` (kubeconfig or in-cluster). The spike created its own manager for the side channel. `pod_discovery.py` lists pods by label `app=codemie-executor`.
- `code_executor/models.py`: `CodeExecutorConfig` (pydantic) with `execution_timeout=30.0`, `session_timeout=300.0`, `default_timeout=30.0`, `sandbox_mode` (default `JOBS`; `sandbox-shared` = pooled), `max_pod_pool_size=5`, `max_threads=64`, `max_open_files=256`, `workdir_base`. `from_env()` reads `CODE_EXECUTOR_*`. Neither the switch nor the tool-calling limit exists yet.
- `code_executor/file_upload_service.py` / `file_export_service.py`: upload and export via `session.copy_to_runtime` / `copy_from_runtime`.
- `src/codemie/service/agent_workspace_service.py`: `execute_workspace_script` (l.502) builds `WorkspaceScriptRunner(file_repository, user_id, input_files, conversation_id)`, runs it, then `_sync_execution_files(workspace.id, executor.last_execution_files)`, which upserts every changed file into the workspace (this is why exchange files must be excluded). `get_workspace_input_files` merges workspace files with conversation-uploaded files.
- `workspace/toolkit.py` (l.125): builds `ExecuteWorkspaceScriptTool(conversation_id, user, workspace_service, workspace_id)`. There is no run-level flag today.
- Spike (`docs/stories/2026-09-28-custom-tools/spike/`): `spike_channel.py` has a `SideChannel(threading.Thread)` polling at 0.5 s. It uses a separate `Exec` class calling `kubernetes.stream(connect_get_namespaced_pod_exec)` with `["sh","-c","ls <wd>/.codemie_bridge/*/req.*.json"]`, `cat <path>`, and `printf %s <shlex.quote(json)> > tmp && mv tmp target && rm -f req`. The file protocol is `req.<id>.json` / `resp.<id>.json`, written atomically via `.tmp` + `os.replace`. It attaches to the first Running pod, not the session's pod, so pod binding from `session._codemie_pod_name` is new.

### Architecture and Layers Affected

- Tool/runtime layer: `codemie_tools/data_management/workspace` (runner) and `codemie_tools/data_management/code_executor` (sandbox_guard, code_executor_tool, models). A new folder holds the SDK and the protocol constants, and is read as text.
- Configuration layer: `CodeExecutorConfig` and `from_env` (switch, tool-calling limit).
- Service layer: `agent_workspace_service.execute_workspace_script` (the only caller path that builds the runner) may need a per-run opt-in. Which layer sets it is undecided (see Section 6).
- Remote runtime: the pooled pod (workdir emptyDir, `readOnlyRootFilesystem`, gVisor runtime class by default). Nothing here changes the image.
- No DB tables, no REST API, no UI.

### Integration Points

- Kubernetes exec websocket on the pod (`kubernetes.stream`, `connect_get_namespaced_pod_exec`), separate from the session's exec.
- `SandboxSessionManager` singleton: per-pod lock, session pool, TTL.
- llm_sandbox (`ArtifactSandboxSession`, patched `BaseSession.run`, `_execute_with_timeout` -> `_handle_timeout`).
  - Verified in site-packages: on timeout `_execute_with_timeout` starts a daemon thread calling `_handle_timeout`, which calls `session.close()`. For an existing (pooled) container `close()` only disconnects (`self.container = None`); it does not kill anything. The worker thread keeps running.
  - This matches the spike ("process keeps running"). It also means the timed-out pooled session object is unusable afterwards.
- `FileExportService` / `_sync_execution_files` (write-back to the workspace) is the leakage path to guard.

### Patterns and Conventions

- Tools follow the `CodeMieTool` / `Tool.from_metadata(ToolMetadata)` pattern; errors are `ToolException`.
- Sandbox code is generated as strings (guard prelude and snapshot script are string templates with `.replace("__CODEMIE_...__")` or f-strings).
- Config: pydantic `CodeExecutorConfig.from_env()` with `CODE_EXECUTOR_*` variables, kept in `codemie_tools` rather than `src/codemie/configs`. The guide says to prefer central config, so the placement of the new settings is a choice (Section 6).
- Structured log lines are `key=value` with `domain=code_executor` and `created_by_env` (e.g. `code_execution_started`, `filesystem_access_denied`).
- Per-pod serialisation: all pooled runs on one pod are serialised by an in-process (per backend process) lock.
- Strict typing is mandatory per `CLAUDE.local.md` (type hints everywhere, `X | None`, no `Any`, dataclass/TypedDict/Pydantic over dict). The runner file currently uses `Any` and untyped params, so new code must be stricter than its neighbours.
- New files need the Apache license header (`make license-check`). The gates are `make ruff`, `make license-check`, `make gitleaks`, `make test`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/` exists. Relevant: `development/configuration-patterns.md` (central config, feature flags at assembly points, `DynamicConfigService` runtime toggle precedent), `testing/testing-patterns.md` (mirror `src/` under `tests/`, seam tests per branch of a policy helper), `development/security-patterns.md`, `integration/external-services.md`, `quality-gates.md`, `standards/code-quality.md`. There is no guide for the code executor or sandbox specifically.
- `src/codemie_tools/data_management/code_executor/README.md` documents the `CODE_EXECUTOR_*` env vars, execution modes and the timeout section. It would need the new settings if they are added there.
- Story docs: `docs/stories/2026-09-28-custom-tools/{architecture-spec.md,technical-analysis.md,technical-proposal.md,story.md}`, and `children/script-tool-calls/{story.md,technical-analysis.md,subtasks/1-channel-and-sdk/task.md, subtasks/2-tool-hookup/}`.

### Architectural Decisions

- Recorded in the story: SDK kept in this repo next to the protocol constants and injected into the generated wrapper (no image release). The bridge is pooled-mode only (job mode is out of scope unless the pooled check fails). The tool-calling limit is a new setting (120 s start value); other runs stay at 30 s. Requests are served one at a time. Exchange data must not reach the workspace or stay in a shared pod.
- Inline: the "DEPRECATED `_get_available_pod_name`" note in `code_executor_tool.py` says all pod selection must go through `session_manager.get_session(pod_name=None, ...)`.

### Derived Conventions

- Tests for guard output run the generated wrapper with `subprocess.run([sys.executable, "-c", script], cwd=workspace_root)` (`test_sandbox_guard.py`). This is a ready pattern for running the SDK under the real guard locally.
- AST-based source checks have precedent (`tests/architecture/test_routing_meta_isolation.py`).

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie_tools/data_management/workspace/test_execute_workspace_script_tool.py` (117 lines): `_dump_json` and the `_build_script_wrapper` limit forwarding only. Nothing exercises `_execute_sandbox_script`, the snapshot, timeout handling or the side-effect path of the runner.
- `tests/codemie_tools/data_management/code_executor/`: `test_sandbox_guard.py` (wrapper builders; runs the wrapper in a real subprocess), `test_sandbox_dispatch.py` (mocked session, `patch.object(tool, "_sandbox_session")`; asserts `session.run.call_args`), `test_session_manager.py` (365 lines), `test_filesystem_policy.py`, `test_models.py`, `test_code_executor_tool.py`, `test_batch_job_runner.py`, `test_export_path_validation.py`, `test_execution_modes.py`, `test_security_config.py`, `test_ast_security_checker.py`.

### Testing Framework and Patterns

- pytest (+ xdist `-n 2`, `unittest.TestCase` classes mixed with plain functions), `unittest.mock` (`MagicMock`, `patch.object`), `tmp_path`. Session is faked via `MagicMock` `session.run.return_value = MagicMock(exit_code, stdout, stderr)`.
- Run scope per guide: narrowest relevant (`poetry run pytest <path>`); the full target is `make test`.

### Coverage Gaps

- No test for the pooled `_execute_sandbox_script` path end to end (with a fake session), the snapshot exclusion, timeout ToolException, the per-pod lock plus side thread, or any k8s exec helper.
- No fake for `kubernetes.stream` exec; the protocol-vs-stand-in test needs a new local stand-in (for example a pod-less local directory plus a stubbed exec runner).
- No existing import-allowlist or clean-interpreter module-diff test to copy. A clean-interpreter check would run in a subprocess (like `test_sandbox_guard`).
- No real-cluster test in the repo; the spike (minikube) is manual only.
- "Runner with a script uploaded in a chat" has no existing fixture in tests. `_StubRepo` in the spike is the only model.

---

## 5. Configuration and Environment

### Environment Variables

- `CODE_EXECUTOR_*` in `CodeExecutorConfig.from_env` (`models.py` l.337-413): `EXECUTION_TIMEOUT` (30.0), `SESSION_TIMEOUT` (300.0), `DEFAULT_TIMEOUT` (30.0), `SANDBOX_MODE` (`sandbox-jobs` default, `sandbox-shared` = pooled), `MAX_POD_POOL_SIZE` (5), `MAX_THREADS` (64), `MAX_OPEN_FILES` (256), `NAMESPACE` (`codemie-code-executor`), `WORKDIR_BASE` (`/home/codemie`), `DOCKER_IMAGE` (`codemie/codemie-python:2.52.0`), `KUBECONFIG_PATH`, `RUNTIME_CLASS_NAME` (gvisor), etc. No variable exists for the switch or the tool-calling limit.
- The spike uses `CODE_EXECUTOR_SANDBOX_MODE=sandbox-shared`, `CODE_EXECUTOR_MAX_POD_POOL_SIZE=1`, `CODE_EXECUTOR_NAMESPACE=codemie-runtime`.

### Configuration Files

- `src/codemie_tools/data_management/code_executor/models.py` (config schema), `README.md` in the same folder (env var docs), `deploy-templates/values.yaml` and `deploy-templates/README.md` (only namespace/RBAC/`customEnv` examples for code executor; no per-setting Helm values), `deploy-templates/templates/code-executor-rbac.yaml` (Role for the API service account to manage executor pods).
- RBAC for pod exec: `code-executor-rbac.yaml` exists; whether it grants `pods/exec` was not examined in this run (existing exec calls already work).

### Feature Flags and Deployment Concerns

- Central-config guide names `DynamicConfigService` (admin-togglable at runtime, used in `langgraph_agent.py`, `assistant_agent.py`) and static `config.py` as the two toggle mechanisms; the code executor currently uses neither (own env config).
- The pooled pods come from the Helm chart (label `app=codemie-executor`) and also from the backend's own creation until the pool cap (per the story and spike). `runtime-deployment.yaml` in the spike dir mirrors the chart pod (not re-read here).
- Session TTL/lifetime is 300 s in two places: manager `_session_ttl_seconds` and `session_timeout` (llm_sandbox starts a `threading.Timer` that closes the session at that age).

---

## 6. Risk Indicators

Speculative items are marked as such; the rest are observed in code.

1. Per-pod lock spans the whole run. `_execute_code_sandbox` holds the pod lock for the full `session.run`. Raising the limit to 120 s for tool-calling runs blocks every other user or conversation routed to that pod for up to 4x longer (pool of 5, or 1 pod in the spike/chart setup).
2. Session lifetime vs longer runs. `session_timeout` (300 s timer that closes the session) and the manager TTL (300 s) are unchanged. A 120 s run started late in a session's life can have its session closed mid-run, and the post-run snapshot/export use the same session (`_collect_sandbox_changed_files`, `_export_files_from_execution`). Nothing currently guards this because the 30 s limit made the window small.
3. Timeout path does not stop the process. `_handle_timeout` for a pooled container only disconnects the session (Section 2). After `SandboxTimeoutError` the runner raises immediately, before any snapshot, and the pooled session is now closed. Cleanup (kill process, remove folder) must therefore run on a fresh exec path that does not depend on the session, and inside a `finally`. Speculative: the `SANDBOX_<uuid>.py` file the timed-out worker would `rm` in its own `finally` is left until the worker finishes.
4. Finding the script's process in a shared pod. The command line only contains the generated `SANDBOX_<32hex>.py` path, which is created inside the patched llm_sandbox `run` and is not returned. Tools available in the image (`ps`, `pkill`) were not verified; the spike scanned `/proc/*/cmdline`. Killing by name pattern risks other conversations' processes (open point already noted in the prior notes). Speculative: the wrapper could publish its own PID or a run token into the exchange folder.
5. Sweep vs concurrent runs in one conversation. The workdir is per user + conversation and persists across runs (upload does not clean it). Two runs of one conversation on the same pod are serialised by the lock, but a sweep executed before the lock is taken could delete a running run's folder. Speculative: sweep only after acquiring the lock, or by run-token/age.
6. Snapshot exclusion and race. `_get_sandbox_file_snapshot` reads every file under workdir (only `__pycache__` and `.pyc` skipped) and `_sync_execution_files` persists everything changed, so an unexcluded folder leaks request and response data into the workspace. The snapshot also raises if a file vanishes mid-walk, so the side thread has to be stopped and the folder removed before the snapshot, or the folder excluded inside the snapshot walk. The exclusion has to be applied where the snapshot code is built, not only in `_is_system_output_path` (post-filter still hashes the files).
7. Guard interplay unverified. The SDK must use only relative paths; the guard denies absolute paths even inside the root and patches `os.replace`/`listdir`/`stat`/`utime` etc. An SDK module registered in `sys.modules` and running under the audit hook and the frame-inspection exemptions has not been tested (spike used a plain script). Any change touching `filesystem_policy.py` risks weakening the enforcement the acceptance criteria say must not change; the safest reading is that the SDK requires no guard change.
8. Thread and file limits in the sandbox: `RLIMIT_NPROC` (64) and `RLIMIT_NOFILE` (256) apply to the script; the SDK must not leave open file handles and should not spawn threads.
9. Side channel load and client sharing. Every 0.5 s poll is at least one k8s exec websocket per run (spike: `ls` plus `cat` plus write plus `rm` per request, three or four exec calls per request). Sharing the session's `CoreV1Api` client concurrently with the blocked exec is untested; `SessionFactory` already treats websocket/handshake failures as connection-pool corruption and recreates the client. The spike used its own client manager. Speculative: per-run API-server load grows linearly with concurrent tool-calling runs.
10. Payload transport. The spike writes responses by embedding JSON in `sh -c "printf %s <quoted> > ..."`, so size is bounded by exec argument length and quoting. Existing upload code sends data over exec stdin as a tar stream (`_patched_copy_to_container`). Speculative: the size cap and response write path must be chosen with this in mind (an above-cap response must yield a clear error, not a hang).
11. Mode coverage. Default `sandbox_mode` is `sandbox-jobs`; the bridge only exists in the pooled path (`_execute_sandbox_script`). Behaviour when the switch is on but the mode is jobs is not defined in the task (Out of Scope only says the jobs mode is not supported).
12. Per-run opt-in has no carrier. `WorkspaceScriptRunner`, `AgentWorkspaceService.execute_workspace_script` and `ExecuteWorkspaceScriptTool` have no flag meaning "this run has tool calling"; the task's "run without tool calling gets a clear SDK error and 30 s" needs one, and where the flag originates overlaps with sub-task 2 (run context). Speculative: sub-task 1 may need a minimal explicit parameter and a default of off.
13. Config placement. New settings would follow `CodeExecutorConfig`/`from_env` (consistent with neighbours), not `src/codemie/configs` or `DynamicConfigService` (the guide's preferred, runtime-togglable mechanism). The choice changes the "environment-wide switch" semantics (restart vs runtime).
14. Test infrastructure gap. No fake for the k8s exec stream and no existing test of the pooled runner; the required tests (protocol vs stand-in, runner with a chat-uploaded script) need new fixtures. The clean-interpreter check needs subprocess-based isolation; the import allowlist has no precedent test in the repo.
15. Data remaining on the pod after interruption. The exchange folder lives in the pod's workdir emptyDir; if the backend dies and the conversation is never run again, request/response data stays on the shared pod until the pod restarts. The acceptance criterion accepts "start of next run in the same conversation", so this is a known residual.
16. Strict typing requirement (`CLAUDE.local.md`) versus existing neighbours: the runner file uses `Any`/untyped parameters, so new code in that file will be inconsistent unless typed carefully. Sonar and ruff gates apply (`make ruff`).

---

## 7. Summary for Complexity Assessment

Layers touched: tool/runtime code in `codemie_tools` (runner in `workspace/execute_workspace_script_tool.py`; `code_executor/code_executor_tool.py`, `sandbox_guard.py`, `models.py`), plus a new SDK-and-constants folder read as text. Config gets a switch and a limit. The per-run opt-in touches `AgentWorkspaceService.execute_workspace_script` and possibly the toolkit. There are no DB, API or UI changes and no image change. Estimated surface is roughly 5-7 existing files edited and 3-5 new source files (constants, SDK, channel/exchange helper, folder cleanup), plus about 4-6 new test files. This is one repository with existing seams: the single-blocking-call wrapper, the per-pod lock, and the wrapper builder.

Technical novelty is moderate to high. The concurrent side channel over separate Kubernetes exec calls has been shown only in a manual spike and has no production precedent in the repo. Three parts are new and unverified in code: stopping a script's process in a shared pod, running the injected SDK under the audit-hook guard, and running a 120 s run under the 300 s session and TTL limits. The security-sensitive constraints (no weakening of the guard, no exchange data in the workspace or the shared pod, isolation between concurrent runs) all depend on details in the timeout, snapshot and sweep paths.

Test coverage posture is thin. The pooled runner path has no tests and there is no k8s exec fake. The only relevant examples are the guard wrapper subprocess test and mocked-session dispatch tests. New tests must build a stand-in for the exec channel, a clean-interpreter check and an import allowlist. Key risks are the ones in Section 6: lock hold time and session lifetime with longer runs, process identification and kill in a shared pod, snapshot exclusion and race, guard behaviour with the SDK, the missing per-run flag carrier, and the config placement decision.

---

## 8. External References

The task named these sources; all resolved and were read.

- `/Users/yanaasadchaya/Projects/epam/airun/codemie-dev/codemie/docs/stories/2026-09-28-custom-tools/children/script-tool-calls/subtasks/1-channel-and-sdk/task.md` (resolved). Task text: Size L (21/36). SDK is one self-contained module in its own folder next to the protocol constants (file names, keys, version, size cap), using only `json`, `os`, `time`, `uuid`, relative paths only, one call at a time, clear error on failure or timeout; the backend reads its source as text and injects it into the run's wrapper as `codemie_runtime_sdk`. Side thread: own exec calls, pod name from the session, retry on failed exec, poll every 0.5 s, hardcoded `echo`. Unique exchange folder per run in the workspace root, excluded from the changed-file snapshot, removed after every run, stale folders swept at the start of the next run in the conversation. On timeout, stop the script's process in the pod and remove the folder. Environment-wide switch (off by default); tool-calling limit is a setting (120 s to start), all other runs 30 s. Tests: import allowlist for SDK and constants, clean-interpreter module check, size cap; protocol vs stand-in; runner with a script uploaded in a chat. Out of scope: real tools, authorization, run-context propagation, confirmation, image changes, job-mode, multi-threaded SDK calls. The header says `Ticket: —`; the ticket given in the request is EPMCDME-15401. Ten acceptance criteria, including: the switch off leaves existing scripts unchanged and the SDK gives a clear error; a run without tool calling gets a clear SDK error and the 30 s limit; two concurrent runs see only their own responses; above-cap request or response gives a clear error rather than a hang; on completion, error, timeout or backend interruption exchange data is not persisted to the workspace and is removed by the end of the run or at the start of the next run; sandbox access enforcement not weakened.
- `.../subtasks/1-channel-and-sdk/technical-analysis.md` (resolved, prior notes). Its claims checked against code: the pooled path shape, per-call `session.run` timeout, `session._codemie_pod_name`, and the whole-workdir snapshot are all confirmed. Its "Not verified" items remain open: the SDK under the guard, and safe process stop in a shared pod. The note that pooled pods come from the Helm chart is from the spike and story and was not re-verified here.
- `.../children/script-tool-calls/story.md` (resolved). Adds: the tool's output limit is 30,000 tokens by default (sub-task 2). Execution limits: 256 MiB memory, one CPU, 1 GiB temp storage, 64 threads, 256 open files, pool of 5 pods, 30 s default timeout covering the whole script in the pooled mode. Tool-call data must not end up in the workspace or stay in a shared pod. The SDK must fail if it imports anything outside a short stdlib allowlist or anything from the platform.
- `docs/stories/2026-09-28-custom-tools/spike/` (resolved: `README.md`, `spike_channel.py` read; `runtime-deployment.yaml` listed, not read). Results dated 2026-09-29: `echo` three sequential calls served, 0.4-0.7 s each, 0.5 s poll, response files appear in the changed-file list; `guard` relative paths allowed, absolute paths rejected even inside the root; `timeout` after the 30 s limit `session.run` raised at about 32 s and the script's process kept running in the pod. Spike env: `CODE_EXECUTOR_SANDBOX_MODE=sandbox-shared`, `CODE_EXECUTOR_MAX_POD_POOL_SIZE=1`, minikube, image `codemie/codemie-python:2.41.0` (the repo default is now 2.52.0).
