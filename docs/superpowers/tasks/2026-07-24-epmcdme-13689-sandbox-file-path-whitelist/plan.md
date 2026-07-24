# Sandbox File Path Whitelist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `_validate_export_paths` to `CodeExecutorTool` and call it at both sandbox entry points to block any export path that escapes the user workdir or targets a sensitive system directory.

**Architecture:** A single `@staticmethod` on `CodeExecutorTool` performs four ordered checks (absolute-path reject → normpath → workdir-containment primary gate → blocked-prefix secondary gate) and raises `ToolException` on violation. It is called as the first statement of `_execute_sandbox` and `_execute_sandbox_script` before any I/O, covering SHARED and JOBS modes in both the base tool and the `WorkspaceScriptRunner` subclass.

**Tech Stack:** Python 3.12, `os.path` (stdlib, no new deps), `langchain_core.tools.ToolException`, `pytest` + `unittest.mock`.

## Global Constraints

- No new dependencies — use `os.path` only.
- `ToolException` is the only allowed exception type for validation failures (matches existing `_validate_code_security` pattern).
- Error messages must not expose file content or host filesystem details beyond the caller-supplied path string.
- The blocked-prefix constant is a `frozenset[str]` at module level in `code_executor_tool.py`; it is not configurable at runtime.
- Tests use `pytest` + `unittest.mock`; match the style of `tests/codemie_tools/data_management/code_executor/test_code_executor_tool.py`.
- `Test-first: yes` — write the failing test before each implementation step.

---

### Task 1: Add `_BLOCKED_EXPORT_PATH_PREFIXES` constant and `_validate_export_paths` static method

**Files:**
- Modify: `src/codemie_tools/data_management/code_executor/code_executor_tool.py` — add constant after imports, add static method after `_validate_code_security_policy`
- Create: `tests/codemie_tools/data_management/code_executor/test_export_path_validation.py`

**Interfaces:**
- Produces: `CodeExecutorTool._validate_export_paths(export_files: Optional[List[str]], workdir: str) -> None` — raises `ToolException` on invalid path, returns `None` on success.

- [ ] **Step 1: Write failing tests for `_validate_export_paths`**

Create `tests/codemie_tools/data_management/code_executor/test_export_path_validation.py`:

```python
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import pytest
from langchain_core.tools import ToolException

from codemie_tools.data_management.code_executor.code_executor_tool import CodeExecutorTool

WORKDIR = "/home/codemie/test_user"


@pytest.mark.parametrize("export_files", [None, []])
def test_validate_export_paths_falsy_input_passes(export_files):
    CodeExecutorTool._validate_export_paths(export_files, WORKDIR)


@pytest.mark.parametrize("path", ["output.csv", "subdir/result.png"])
def test_validate_export_paths_allowed_relative_passes(path):
    CodeExecutorTool._validate_export_paths([path], WORKDIR)


def test_validate_export_paths_normalizes_to_inside_workdir():
    # subdir/../output.csv resolves to /home/codemie/test_user/output.csv — still inside workdir
    CodeExecutorTool._validate_export_paths(["subdir/../output.csv"], WORKDIR)


@pytest.mark.parametrize("path", [
    "/proc/1/environ",
    "/etc/passwd",
    "/sys/class/net",
    "/var/log/syslog",
    "/proc/net/tcp",
])
def test_validate_export_paths_absolute_path_raises(path):
    with pytest.raises(ToolException):
        CodeExecutorTool._validate_export_paths([path], WORKDIR)


@pytest.mark.parametrize("path", [
    "../../etc/passwd",
    "../other_user/data.csv",
    "..",
])
def test_validate_export_paths_traversal_raises(path):
    with pytest.raises(ToolException):
        CodeExecutorTool._validate_export_paths([path], WORKDIR)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/codemie_tools/data_management/code_executor/test_export_path_validation.py -v
```

Expected: `AttributeError: type object 'CodeExecutorTool' has no attribute '_validate_export_paths'`

- [ ] **Step 3: Add the constant and static method to `code_executor_tool.py`**

After the existing imports block (after line 52 where `logger` is defined), add the constant:

```python
_BLOCKED_EXPORT_PATH_PREFIXES: frozenset[str] = frozenset({
    "/proc", "/etc", "/sys", "/var",
    "/root", "/boot", "/dev", "/run",
})
```

After the existing `_validate_code_security_policy` method (around line 570), add the static method inside `CodeExecutorTool`:

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

Note: `os` is not yet imported in `code_executor_tool.py` — add `import os` to the imports block at the top of the file.

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie_tools/data_management/code_executor/test_export_path_validation.py -v
```

Expected: all 13 cases PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie_tools/data_management/code_executor/code_executor_tool.py \
        tests/codemie_tools/data_management/code_executor/test_export_path_validation.py
git commit -m "EPMCDME-13689: Add _validate_export_paths static method and unit tests"
```

---

### Task 2: Wire `_validate_export_paths` into `CodeExecutorTool._execute_sandbox`

**Files:**
- Modify: `src/codemie_tools/data_management/code_executor/code_executor_tool.py:392-435` — add call at start of `_execute_sandbox`
- Modify: `tests/codemie_tools/data_management/code_executor/test_export_path_validation.py` — add integration test

**Interfaces:**
- Consumes: `CodeExecutorTool._validate_export_paths` from Task 1.

- [ ] **Step 1: Write a failing integration test**

Add to `tests/codemie_tools/data_management/code_executor/test_export_path_validation.py`:

```python
from unittest.mock import MagicMock, patch


def test_execute_sandbox_blocks_bad_export_path_before_io():
    tool = CodeExecutorTool(file_repository=MagicMock(), user_id="test_user")
    with patch.object(tool, "_sandbox_session"), \
         patch.object(tool, "run_via_jobs", side_effect=AssertionError("should not reach I/O")):
        with pytest.raises(ToolException, match="working directory"):
            tool._execute_sandbox("print('hi')", export_files=["../../etc/passwd"])
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/codemie_tools/data_management/code_executor/test_export_path_validation.py::test_execute_sandbox_blocks_bad_export_path_before_io -v
```

Expected: FAIL — no `ToolException` raised (validation not wired yet).

- [ ] **Step 3: Add the call to `_execute_sandbox`**

In `code_executor_tool.py`, modify `_execute_sandbox` to call validation before the `try` block. Current code at line 392:

```python
def _execute_sandbox(self, code: str, export_files: Optional[List[str]] = None) -> str:
    ...
    try:
        if self.config.sandbox_mode == SandboxMode.JOBS:
```

Change to:

```python
def _execute_sandbox(self, code: str, export_files: Optional[List[str]] = None) -> str:
    ...
    self._validate_export_paths(export_files, self._get_user_workdir())
    try:
        if self.config.sandbox_mode == SandboxMode.JOBS:
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie_tools/data_management/code_executor/test_export_path_validation.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Run full code executor test suite to check for regressions**

```bash
poetry run pytest tests/codemie_tools/data_management/code_executor/ -v
```

Expected: all existing tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codemie_tools/data_management/code_executor/code_executor_tool.py \
        tests/codemie_tools/data_management/code_executor/test_export_path_validation.py
git commit -m "EPMCDME-13689: Wire _validate_export_paths into _execute_sandbox"
```

---

### Task 3: Wire `_validate_export_paths` into `WorkspaceScriptRunner._execute_sandbox_script`

**Files:**
- Modify: `src/codemie_tools/data_management/workspace/execute_workspace_script_tool.py:181-185` — add call at start of `_execute_sandbox_script`
- Modify: `tests/codemie_tools/data_management/code_executor/test_export_path_validation.py` — add integration test for the subclass

**Interfaces:**
- Consumes: `CodeExecutorTool._validate_export_paths` from Task 1 (inherited by `WorkspaceScriptRunner`).

- [ ] **Step 1: Write a failing integration test**

Add to `tests/codemie_tools/data_management/code_executor/test_export_path_validation.py`:

```python
from codemie_tools.data_management.workspace.execute_workspace_script_tool import WorkspaceScriptRunner


def test_execute_sandbox_script_blocks_bad_export_path_before_io():
    tool = WorkspaceScriptRunner(file_repository=MagicMock(), user_id="test_user")
    with patch.object(tool, "_sandbox_session"), \
         patch.object(tool, "_get_script_content", return_value="print('hi')"), \
         patch.object(tool, "_validate_code_security"), \
         patch.object(tool, "_execute_code_sandbox", side_effect=AssertionError("should not reach execution")):
        with pytest.raises(ToolException, match="working directory"):
            tool._execute_sandbox_script("/workspace/script.py", export_files=["../other_user/secret.txt"])
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/codemie_tools/data_management/code_executor/test_export_path_validation.py::test_execute_sandbox_script_blocks_bad_export_path_before_io -v
```

Expected: FAIL — no `ToolException` raised (not wired in `_execute_sandbox_script` yet).

- [ ] **Step 3: Add the call to `_execute_sandbox_script`**

In `execute_workspace_script_tool.py`, current code at line 181:

```python
def _execute_sandbox_script(self, script_path: str, export_files: Optional[list[str]] = None) -> str:
    user_workdir = self._get_user_workdir()

    if self.config.sandbox_mode == SandboxMode.JOBS:
```

Change to:

```python
def _execute_sandbox_script(self, script_path: str, export_files: Optional[list[str]] = None) -> str:
    user_workdir = self._get_user_workdir()
    self._validate_export_paths(export_files, user_workdir)

    if self.config.sandbox_mode == SandboxMode.JOBS:
```

- [ ] **Step 4: Run all validation tests**

```bash
poetry run pytest tests/codemie_tools/data_management/code_executor/test_export_path_validation.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Run the full test suite for both modules**

```bash
poetry run pytest tests/codemie_tools/data_management/ -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codemie_tools/data_management/workspace/execute_workspace_script_tool.py \
        tests/codemie_tools/data_management/code_executor/test_export_path_validation.py
git commit -m "EPMCDME-13689: Wire _validate_export_paths into WorkspaceScriptRunner._execute_sandbox_script"
```
