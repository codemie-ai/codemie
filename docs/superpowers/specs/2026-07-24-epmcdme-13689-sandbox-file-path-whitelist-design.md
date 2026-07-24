# Sandbox-Only File Path Whitelist for Code Executor

**Ticket:** EPMCDME-13689
**Date:** 2026-07-24

---

## Problem

The `export_files` parameter accepted by `CodeExecutorTool` and `WorkspaceScriptRunner` is populated from LLM output and passes untrusted path strings directly to sandbox file-copy operations. No validation exists on these paths today. An adversarial or confused model could pass paths such as `/etc/passwd`, `/proc/self/environ`, or `../../etc/shadow`, and the sandbox layer would attempt to retrieve them from the container.

---

## Approach

Add a single `@staticmethod _validate_export_paths(export_files, workdir)` to `CodeExecutorTool`. Call it as the first statement in both sandbox entry points before any I/O occurs.

**Primary gate — workdir containment:**
All export paths must resolve inside the user's `workdir`. This is correct by construction: it does not rely on enumerating known-bad paths and is robust to any path that escapes the working directory regardless of target.

**Secondary gate — blocked prefix denylist (defense-in-depth):**
A module-level constant covers well-known sensitive Linux path prefixes as a secondary check. This guards the edge case where `workdir` itself is somehow misconfigured to sit under a sensitive root.

---

## Implementation

### Constant (module level in `code_executor_tool.py`)

```python
_BLOCKED_EXPORT_PATH_PREFIXES: frozenset[str] = frozenset({
    "/proc", "/etc", "/sys", "/var",
    "/root", "/boot", "/dev", "/run",
})
```

### Static method

```python
@staticmethod
def _validate_export_paths(export_files: Optional[List[str]], workdir: str) -> None:
    if not export_files:
        return
    normalized_workdir = os.path.normpath(workdir)
    for path in export_files:
        if os.path.isabs(path):
            raise ToolException(
                f"Export path must be relative to the working directory: {path!r}"
            )
        normalized = os.path.normpath(os.path.join(workdir, path))
        if not normalized.startswith(normalized_workdir + os.sep):
            raise ToolException(
                f"Export path must resolve inside the working directory: {path!r}"
            )
        for blocked in _BLOCKED_EXPORT_PATH_PREFIXES:
            if normalized == blocked or normalized.startswith(blocked + os.sep):
                raise ToolException(
                    f"Export path targets a restricted system directory: {path!r}"
                )
```

### Call sites

**`CodeExecutorTool._execute_sandbox()`** — insert before the JOBS/SHARED branch:

```python
def _execute_sandbox(self, code: str, export_files: Optional[List[str]] = None) -> str:
    self._validate_export_paths(export_files, self._get_user_workdir())
    try:
        if self.config.sandbox_mode == SandboxMode.JOBS:
            ...
```

**`WorkspaceScriptRunner._execute_sandbox_script()`** — insert as the first statement:

```python
def _execute_sandbox_script(self, script_path: str, export_files: Optional[list[str]] = None) -> str:
    user_workdir = self._get_user_workdir()
    self._validate_export_paths(export_files, user_workdir)
    ...
```

Note: `_get_user_workdir()` is already called on the next line in `_execute_sandbox_script`, so the call is shared with no duplication. In `_execute_sandbox` it is called once upfront instead of twice (minor cleanup the change enables for the SHARED path; JOBS path still calls it inside `run_via_jobs`).

---

## Validation algorithm (ordered)

1. **Early return** — `export_files` is `None` or empty: no-op.
2. **Absolute path reject** — fast-fail with a clear message before any join.
3. **Normalize** — `os.path.normpath(os.path.join(workdir, path))` collapses `../` sequences without touching the filesystem.
4. **Workdir containment (primary gate)** — normalized path must start with `normpath(workdir) + os.sep`. Rejects `../other_user/data.csv`, `../../etc/passwd`, and any other escape regardless of target.
5. **Blocked prefix check (secondary gate)** — normalized path must not match any prefix in `_BLOCKED_EXPORT_PATH_PREFIXES`.

---

## Error responses

All failures raise `ToolException`. The message names the rule violation and includes the caller-supplied path (which is already known to the caller) but never exposes file content or host details. The existing `except ToolException: raise` block in `_execute_sandbox` propagates it cleanly.

---

## Out of scope

- **Symlink resolution inside the container** — the path name passes the check; a symlink pointing outside workdir on the container filesystem is a residual risk. Container capability drops and gVisor `runtimeClassName` provide defense-in-depth at that layer.
- **YAML-configurable blocked prefixes** — the blocked set is a security constant, not an operator-tunable policy. A future `CodeExecutorConfig` field can be added if extensibility is needed.
- **Changes to `FileExportService` or `BatchJobRunner` internals** — validation at the entry point is sufficient; inner layers are not the right boundary.

---

## Test plan

New file: `tests/codemie_tools/data_management/code_executor/test_export_path_validation.py`

| # | Case | `export_files` | `workdir` | Expected |
|---|---|---|---|---|
| 1 | None input | `None` | `/home/user` | passes |
| 2 | Empty list | `[]` | `/home/user` | passes |
| 3 | Allowed relative | `["output.csv"]` | `/home/user` | passes |
| 4 | Allowed nested | `["subdir/result.png"]` | `/home/user` | passes |
| 5 | Normalizes to inside workdir | `["subdir/../output.csv"]` | `/home/user` | passes (resolves to `/home/user/output.csv`, still inside workdir) |
| 6 | Absolute path | `["/proc/1/environ"]` | `/home/user` | `ToolException` (absolute) |
| 7 | `/etc` blocked | `["/etc/passwd"]` | `/home/user` | `ToolException` (absolute + blocked) |
| 8 | `/sys` blocked | `["/sys/class/net"]` | `/home/user` | `ToolException` |
| 9 | `/var` blocked | `["/var/log/syslog"]` | `/home/user` | `ToolException` |
| 10 | Nested blocked | `["/proc/net/tcp"]` | `/home/user` | `ToolException` |
| 11 | Traversal to blocked dir | `["../../etc/passwd"]` | `/home/user` | `ToolException` (containment) |
| 12 | Traversal escapes workdir | `["../other_user/data.csv"]` | `/home/user` | `ToolException` (containment) |
| 13 | Workdir-exact escape | `[".."]` | `/home/user` | `ToolException` (containment — resolves to parent) |

Two additional integration tests confirm the call sites:
- `test_execute_sandbox_blocks_bad_export_path` — mocks `_execute_sandbox` dependencies; verifies `ToolException` is raised before any session or job I/O.
- `test_execute_sandbox_script_blocks_bad_export_path` — same for `WorkspaceScriptRunner._execute_sandbox_script`.
