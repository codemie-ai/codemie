# Technical Analysis: Sandbox-Only File Path Whitelist for Code Executor

**Task:** EPMCDME-13689
**Date:** 2026-07-24

---

## 1. Codebase Findings

### Primary Implementation Files

The Code Executor tool lives entirely under:

```
src/codemie_tools/data_management/code_executor/
├── __init__.py
├── code_executor_tool.py       # Main tool class
├── ast_security_checker.py     # AST-based code security (Layer 1)
├── security_policies.py        # YAML-driven policy loader and check_security_policy() (Layer 2)
├── default_security_policies.yaml  # Default blocked modules/patterns
├── models.py                   # CodeExecutorConfig, ExecutionMode, SandboxMode
├── batch_job_runner.py         # Jobs-mode backend (Kubernetes V1Job per execution)
├── file_export_service.py      # Pulls files from sandbox after execution
├── file_upload_service.py      # Pushes files into sandbox before execution
├── llm_sandbox.py              # Performance patch + is_sandbox_system_file_path()
├── session_factory.py
├── session_manager.py
├── k8s_client_manager.py
└── pod_discovery.py
```

### Current Security Checks

There are two pre-execution code validation layers:

1. **AST layer** (`ast_security_checker.py`, `check_code_with_ast()`): detects `__import__`, `exec`, `eval`, `compile`, `getattr(__builtins__, ...)` calls in the Python AST.
2. **Regex layer** (`security_policies.py`, `check_security_policy()`): matches against `SecurityPattern` patterns (e.g., `os.system`, `subprocess.run`, `os.environ[`, `eval(`, `exec(`, `__builtins__`) loaded from `default_security_policies.yaml`. Restricted Python modules (`os`, `subprocess`, `sys`, `shutil`, `pathlib`, `socket`, `urllib`, etc.) are also checked.

Both layers run before code is sent to the sandbox pod. They operate on code **content** — not on file paths requested by the caller in `export_files`.

### Existing Partial Protections

- `_read_input_file_bytes()` in `CodeExecutorTool` (line 710): rejects absolute paths and paths with `..` parts for **input** file uploads.
- `_upload_single_file()` and `_download_files_to_temp()` in `FileUploadService` (lines 92-94, 167-168): same absolute-path and `..` check on upload file names.
- `_get_user_workdir()` (lines 251-254): sanitizes `user_id` by replacing `/` and `\` with `_` to prevent directory traversal via the user ID.

**Gap:** There is no validation on the `export_files` parameter paths (what the agent requests to pull out of the sandbox). The `FileExportService._export_single_file()` constructs the sandbox path as `f"{workdir}/{src_path}"` without any sanitization (line 147). An adversarial or confused LLM could pass `export_files=["/proc/1/environ"]` or `export_files=["../../etc/passwd"]` and the service would attempt to retrieve it from the container.

---

## 2. Current Architecture

### Execution Flow

```
LLM call -> CodeExecutorTool.execute()
         -> _execute_sandbox()
            JOBS mode: _validate_code_security_policy(code) -> run_via_jobs()
                                                                  -> BatchJobRunner.run()
                                                                     -> _download_exports(pod, workdir, export_files)
            SHARED mode: _sandbox_session(user_workdir)
                         -> _upload_files_to_sandbox(session, input_files, workdir)
                         -> _validate_code_security(session, code)
                         -> _execute_code_sandbox(session, code)
                         -> _export_files_from_execution(session, export_files, workdir)
                              -> FileExportService.export_files_from_execution()
                                 -> session.copy_from_runtime(f"{workdir}/{src_path}", ...)
```

### Sandbox Mode Flag

`CodeExecutorConfig.sandbox_mode` (env: `CODE_EXECUTOR_SANDBOX_MODE`):
- `SandboxMode.SHARED` (`"sandbox-shared"`, default): long-lived pooled pods, session-based.
- `SandboxMode.JOBS` (`"sandbox-jobs"`): one Kubernetes `V1Job` per execution, ephemeral.

The whitelist check needs to apply in **both** modes because:
- SHARED mode: `export_files` paths flow through `FileExportService.export_files_from_execution()`.
- JOBS mode: `export_files` paths flow through `BatchJobRunner._download_exports()` via `_exec_tar_out()`.

### Tool Registration

`CodeExecutorTool` is a `CodeMieTool` (LangChain `BaseTool`). It is instantiated by agent/workflow factory code with `file_repository`, `user_id`, and optional `input_files`. The `execute()` method is the LangChain tool entry point.

---

## 3. Implementation Approach

### Where to Inject

The validation should happen as early as possible — immediately after `export_files` is received by `_execute_sandbox()`, before any I/O against the sandbox. This is the single chokepoint for both modes.

A private static method `_validate_export_paths(export_files)` on `CodeExecutorTool` is the cleanest insertion point. It should be called:

1. In `_execute_sandbox()` near the top, before the `JOBS` / `SHARED` branch split — so it covers both modes with one call.
2. In `WorkspaceScriptRunner._execute_sandbox_script()` if that class also accepts `export_files` (it does, line 90).

### Whitelist Design

The whitelist is a **sandbox-only** concept: it controls what relative paths inside `user_workdir` can be exported. System directories (`/proc`, `/etc`, `/sys`, `/var`) are the primary blocked targets, plus canonical path traversal mitigation.

Proposed approach — a **denylist of absolute path prefixes** that must not appear after normalization, combined with **relative-path enforcement**:

```python
BLOCKED_SANDBOX_PATH_PREFIXES = frozenset([
    "/proc",
    "/etc",
    "/sys",
    "/var",
])

def _validate_export_paths(export_files: Optional[List[str]], workdir: str) -> None:
    if not export_files:
        return
    for path in export_files:
        _validate_single_export_path(path, workdir)

def _validate_single_export_path(path: str, workdir: str) -> None:
    from pathlib import PurePosixPath
    from langchain_core.tools import ToolException

    # Step 1: resolve as relative inside workdir (prevents absolute leakage)
    try:
        pure = PurePosixPath(path)
    except Exception:
        raise ToolException(f"Invalid export path: {path!r}")

    # Reject absolute paths
    if pure.is_absolute():
        raise ToolException(
            f"Export path must be relative to the working directory, got: {path!r}"
        )

    # Step 2: normalize the combined path to collapse ../ sequences
    resolved = PurePosixPath(workdir).joinpath(pure)
    # Collapse all .. components without touching the filesystem
    normalized = PurePosixPath(*resolved.parts)  # re-parse to normalize
    # Use os.path.normpath for .. collapsing (string-only, no I/O)
    import os
    normalized_str = os.path.normpath(str(resolved))

    # Step 3: ensure the resolved path is still inside workdir
    workdir_resolved = os.path.normpath(workdir)
    if not normalized_str.startswith(workdir_resolved + "/") and normalized_str != workdir_resolved:
        raise ToolException(
            f"Export path escapes working directory: {path!r}"
        )

    # Step 4: block sensitive system path prefixes (defense in depth)
    for blocked in BLOCKED_SANDBOX_PATH_PREFIXES:
        if normalized_str == blocked or normalized_str.startswith(blocked + "/"):
            raise ToolException(
                f"Export path targets a restricted system directory ({blocked}): {path!r}"
            )
```

### Recommended Injection Point in `_execute_sandbox`

In `code_executor_tool.py`, `_execute_sandbox()` (line 392), add validation as the **first statement**:

```python
def _execute_sandbox(self, code: str, export_files: Optional[List[str]] = None) -> str:
    user_workdir = self._get_user_workdir()
    self._validate_export_paths(export_files, user_workdir)  # <-- new
    try:
        if self.config.sandbox_mode == SandboxMode.JOBS:
            ...
```

Note: `user_workdir` is computed early but currently computed twice. Consolidating to one call before the branch is a minor cleanup this change enables.

### YAML Policy Extension (Optional, Recommended)

The `default_security_policies.yaml` could gain a top-level `file_restrictions` section for future extensibility:

```yaml
file_restrictions:
  sandbox_only: true
  blocked_path_prefixes:
    - /proc
    - /etc
    - /sys
    - /var
```

However, loading this from YAML adds complexity and the blocked prefixes are security constants that should not be operator-overridable without review. Hardcoding them in `code_executor_tool.py` as a `frozenset` constant is safer and simpler for the initial implementation. If the set needs to be extended in future, a new `CodeExecutorConfig` field (`blocked_export_path_prefixes: List[str]`) can be added, defaulting to the hardcoded set.

---

## 4. Test Surface

### Existing Test Files

| File | Coverage |
|------|----------|
| `test_code_executor_tool.py` | File upload, pod discovery, user workdir sanitization, execute + file upload integration |
| `test_security_config.py` | Security threshold config, policy loading, requests pattern detection |
| `test_ast_security_checker.py` | All AST check cases: `__import__`, `exec`/`eval`/`compile`, `__builtins__`, severity, syntax errors |
| `test_execution_modes.py` | Mode selection, dynamic schema generation, routing (SANDBOX/SHARED/JOBS) |
| `test_sandbox_dispatch.py` | SHARED/JOBS mode dispatch, BatchJobRunner routing, workspace runner |
| `test_models.py` | `CodeExecutorConfig` defaults, validators, `from_env()` |
| `test_session_manager.py` | Session pool management |
| `test_batch_job_runner.py` | BatchJobRunner internals |

### Gaps for the Acceptance Criteria

The following test cases do not exist and must be added (suggested location: `test_code_executor_tool.py` or a new `test_export_path_validation.py`):

| Test Case | Description |
|-----------|-------------|
| Allowed relative path | `export_files=["output.csv"]` passes validation |
| Allowed nested relative path | `export_files=["subdir/result.png"]` passes |
| Blocked `/proc` | `export_files=["/proc/1/environ"]` raises `ToolException` |
| Blocked `/etc` | `export_files=["/etc/passwd"]` raises `ToolException` |
| Blocked `/sys` | `export_files=["/sys/class/net"]` raises `ToolException` |
| Blocked `/var` | `export_files=["/var/log/syslog"]` raises `ToolException` |
| Nested blocked path | `export_files=["/proc/net/tcp"]` raises `ToolException` |
| Path traversal to blocked dir | `export_files=["../../etc/passwd"]` raises `ToolException` |
| Path traversal within workdir | `export_files=["../other_user/data.csv"]` raises `ToolException` |
| Path traversal normalized | `export_files=["subdir/../../output.csv"]` passes (stays in workdir) |
| Absolute path in workdir | `export_files=["/home/codemie/user/out.csv"]` raises `ToolException` (must be relative) |
| Empty export list | `export_files=[]` passes without error |
| None export list | `export_files=None` passes without error |
| JOBS mode blocks same paths | Same cases exercised via `_execute_sandbox` with `sandbox_mode=JOBS` to confirm both branches go through the same validation |
| Symlink traversal path | `export_files=["link_to_proc"]` — see Risk section |

---

## 5. Risk Indicators

### High-Priority Risks

**Symlinks inside the sandbox container:**
A user's executed code could create a symlink inside `workdir` pointing to `/proc`, `/etc`, or another sensitive path. The path validation checks the *name* passed in `export_files`, not the resolved filesystem path inside the container. If `export_files=["my_link"]` and `my_link -> /etc/passwd` inside the container, the name check passes. Mitigation options:
- Accept this as a residual risk given the container already has `readOnlyRootFilesystem: false` and drops ALL capabilities. The seccomp `RuntimeDefault` profile and gVisor `runtimeClassName` provide defense-in-depth.
- Alternatively, extend `FileExportService._export_single_file()` to run a `test -L {path}` check via the sandbox exec API before `copy_from_runtime`, rejecting symlinks.

**Path traversal via `../` sequences in relative paths:**
`export_files=["../../etc/passwd"]` joined with `workdir=/home/codemie/userX` produces `/home/codemie/userX/../../etc/passwd`. After `os.path.normpath()` this becomes `/etc/passwd`. The proposed validation catches this via the "must stay inside workdir" check and the blocked-prefix check. Both checks are needed; neither alone is sufficient.

**`None` / empty export list bypass:**
No risk — the guard returns early on falsy input, matching existing behavior.

**JOBS mode: `_exec_tar_out` accepts `rel_path` without validation:**
`BatchJobRunner._exec_tar_out()` (line 544) passes `rel_path` directly to a shell command: `tar cf - -C "$1" "$2"`. If validation is applied before `BatchJobRunner.run()` is called (i.e., at the `_execute_sandbox()` level), this is safe. If `BatchJobRunner` is ever called from a different path, the inner method will lack protection. Consider adding an assertion or secondary guard inside `BatchJobRunner._download_exports()` as defense in depth.

**`FileExportService._export_single_file` path construction:**
Line 147: `session.copy_from_runtime(f"{workdir}/{src_path}", temp_file_path)`. If `src_path` is absolute (e.g., `/etc/passwd`), the join produces `/home/codemie/userX//etc/passwd` which most POSIX paths resolve to `/etc/passwd`. Python's string concatenation does not normalize this. The blocked-prefix check on the normalized path would catch this, but the validation must happen before this point.

**`collect_files_from_execution` in `FileExportService`:**
Line 88 — this method also calls `copy_from_runtime` with `{workdir}/{normalized_path}`. It is currently only used by `WorkspaceScriptRunner`. Any callers should also pass through the same path validation before invoking this method.

**LLM-generated paths:**
The `export_files` list is populated from LLM output. A jailbroken or hallucinating model could intentionally or accidentally produce paths targeting system directories. The whitelist check must treat all values in `export_files` as untrusted input.

### Lower-Priority / Integration Points

- `WorkspaceScriptRunner.execute_script()` accepts `export_files` and passes it to `_execute_sandbox_script()` which calls `_export_files_from_execution()`. This path must also be validated.
- `BatchJobRunner._snapshot_workdir()` runs Python code inside the pod to list the workdir. The code is internal and not user-supplied, so it is not a concern for this feature.
- `run_via_jobs()` is the shared chokepoint for JOBS mode. If validation is placed before this call in `_execute_sandbox()`, `run_via_jobs()` itself does not need changes.
