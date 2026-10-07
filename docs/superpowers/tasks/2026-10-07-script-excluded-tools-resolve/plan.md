# Excluded tools give tool_unavailable in script tool calls (EPMCDME-15559) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** A script calling an excluded tool in a workflow tool step gets `tool_unavailable`, never `internal_error`, and known-excluded tools are not built.

**Architecture:** A central tool registry in `codemie_tools/base` is filled from `CodeMieTool.__pydantic_init_subclass__`. A new glue module in `script_tool_calls` consults it from `ProjectScope.resolve` before the resolver runs. `ProjectScope.resolve` also gets a broad-catch backstop.

**Tech Stack:** Python, pydantic v2, pytest. Run tests with `PYTHONPATH=src poetry run pytest <path>` (never put `tests/` ahead of `src`; see `.ai-run/guides/quality-gates.md`). Commit per task using the repository's existing convention. Apache header and `from __future__ import annotations` on new source files.

---

### Task 1: Backstop in ProjectScope.resolve

**Files:** Modify `src/codemie/service/script_tool_calls/context.py:92-96`; Test `tests/codemie/service/script_tool_calls/test_context.py` (ProjectScope tests ~101-116).

- Test-first: yes — a resolver raising `KeyError`/`RuntimeError` makes `ProjectScope.resolve` return `None` and log a warning (fails today: the exception propagates); `ValueError` still returns `None`.
- [ ] Add the failing tests, using the injectable `resolver` and `caplog`/logger patch, as in the existing ValueError test.
- [ ] Keep `except ValueError: return None`. Add `except Exception as exc:  # noqa: BLE001`, which logs `logger.warning(...)` with the tool name and exc (`codemie.configs.logger.logger`) and returns `None`.
- [ ] Run `tests/codemie/service/script_tool_calls/test_context.py`.

### Task 2: Tool registry module

**Files:** Create `src/codemie_tools/base/tool_registry.py`; Modify `src/codemie_tools/base/codemie_tool.py` (add `__pydantic_init_subclass__` on `CodeMieTool`, class at line 76; `script_callable` at line 88); Test `tests/codemie_tools/base/test_tool_registry.py`.

- Test-first: yes — defining a concrete `CodeMieTool` subclass with a default `name` in module `codemie_tools.*` registers it. Abstract, nameless and foreign-module (e.g. test module) classes are not registered. Two classes with one name are both kept. `is_known_excluded_from_script_calls` is True only when every class for the name has `script_callable` False, and False for unknown or mixed names. `ensure_loaded()` is idempotent and logs (does not swallow) import failures.
- [ ] Write the failing tests. Registering test-local classes needs a way past the module filter; call `register(cls)` directly with a faked `__module__`. Use a registry-reset fixture.
- [ ] New symbols to define:

```python
@dataclass(frozen=True)
class ToolRecord:
    name: str
    classes: tuple[type, ...]
    modules: tuple[str, ...]
    script_callable: tuple[bool, ...]  # one flag per class

def register(cls: type) -> None: ...          # skip rules: abstract, no default name, module root not codemie_tools./codemie.
def lookup(name: str) -> ToolRecord | None: ...
def ensure_loaded() -> None: ...              # importlib-imports codemie_tools, codemie.agents.tools, codemie.service.mcp, codemie.service.provider once; logs failures
def is_known_excluded_from_script_calls(name: str) -> bool: ...
```

  Use `inspect.isabstract` and `cls.model_fields["name"].default` for the skip rules (mirror the filter in `tests/tool_discovery.py`). The module must not import from `codemie` at module level.
- [ ] Call `tool_registry.register(cls)` from `CodeMieTool.__pydantic_init_subclass__(cls, **kwargs)` after `super()`. Import inside the method if there is a cycle.
- [ ] Run the new test file plus `tests/codemie_tools/base/`.

### Task 3: Pre-filter glue and wiring

**Files:** Create `src/codemie/service/script_tool_calls/registry_prefilter.py`; Modify `context.py:92-96`; Test `tests/codemie/service/script_tool_calls/test_context.py`, `test_workflow_tool_step_scope.py`.

- Test-first: yes — `ProjectScope.resolve("code_executor")` returns `None` without invoking the resolver (mock `assert_not_called`). In `test_workflow_tool_step_scope.py` no virtual assistant is created for a known-excluded name. Unknown and mixed names still reach the resolver.
- [ ] Add `is_known_excluded(name) -> bool` in the new module. It calls `tool_registry.ensure_loaded()`, then `is_known_excluded_from_script_calls(name)`. `exclusions.py` stays untouched, and its no-tool-package-import test must still pass.
- [ ] In `ProjectScope.resolve`, call it inside the same `try` as the resolver, so a registry failure falls to the Task 1 backstop (fail closed, `tool_unavailable`). If it returns True, return `None` before invoking the resolver.
- [ ] Add a parametrized test over `code_executor`, `generate_image_tool`, `file_analysis`, `pptx_tool`, `pdf_tool`, `csv_tool`, `excel_tool`, `docx_tool`, `email_analysis_tool`: `authorize_tool_call` (`authorizer.py`) with a `ProjectScope` raises `ToolCallRefused` with `CODE_TOOL_UNAVAILABLE`, with a resolver that raises so any build attempt is visible.
- [ ] Confirm callable tools (e.g. a Jira tool, `get_repository_tree`) still resolve: run `tests/codemie/service/script_tool_calls/` and `tests/codemie/workflows/test_tool_node_script_step.py`.

### Task 4: Sync test registry vs instance check

**Files:** Test `tests/codemie/service/script_tool_calls/test_registry_exclusion_sync.py`.

- Test-first: no — the test itself is the deliverable. After `ensure_loaded()`, for every registry name flagged not-callable, an instance of the class is also excluded per `is_excluded_from_script_calls`. Build instances via `model_construct()` to avoid required-arg and config problems. The existing `callable_tool_classes.txt` snapshot test (`test_exclusions.py:185-191`) stays as is and must still pass.

### Task 5: Documentation and comments

**Files:** Modify `.ai-run/guides/agents/tool-overview.md`, `.ai-run/guides/agents/custom-tool-creation.md`; comment-only edits in `src/codemie_tools/base/toolkit_provider.py`, `src/codemie/service/tools/discovery/metadata_finder.py` (`ToolMetadataFinder`), `tests/tool_discovery.py`, `src/codemie/service/tools/tools_info_service.py`, `src/codemie/service/tools/toolkit_service.py:163` (`get_toolkit_methods`).

- Test-first: no — docs and comments only.
- [ ] `tool-overview.md`: append a short section in the existing "Avoid | Prefer" table plus Evidence line style. It states that `codemie_tools/base/tool_registry.py` is THE single source of static tool facts, new static facts extend `ToolRecord`, and nobody adds a new scan or catalog. Do not rewrite existing content.
- [ ] `custom-tool-creation.md`: add one line saying a new tool class registers itself automatically and new static facts extend the registry.
- [ ] Add a brief comment in each listed code file pointing to the registry as the future source. At `get_toolkit_methods`, also note that FILE_ANALYSIS has no factory entry because it is built separately (`toolkit_service.py` ~583), and that a registry-toolkits-vs-factories sync test belongs there. No behavior change.

---

**Self-review notes**
- Spec coverage: criteria 1-5 map to Tasks 1 and 3, 6 to Task 2, 7 to Task 4, 8 to Tasks 3 and 5 (files untouched), 9 to Task 5, 10 to Task 4.
- negative-constraints: honored. No name deny list in `script_tool_calls` (Task 3 uses the registry only). `tool_service.py` and `exclusions.py` are not modified (Tasks 3 and 5). No extra registry fields (Task 2). No migration of `toolkit_provider`, `ToolMetadataFinder` or `ToolsInfoService` (comments only, Task 5). The xfail harness is not touched. The exclusion rules are unchanged.
