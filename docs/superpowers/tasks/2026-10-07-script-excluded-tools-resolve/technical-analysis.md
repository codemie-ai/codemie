# Technical Research

**Task**: script tool calls authorize resolve excluded
**Generated**: 2026-10-07
**Research path**: filesystem

---

## 1. Original Context

Fix backend defect in script tool calls (repo root /Users/yanaasadchaya/Projects/epam/codemie/codemie, Python backend, likely under src/codemie/.../script_tool_calls/). In a workflow tool step, a script calls `tool.call`; `ProjectScope.resolve` (script_tool_calls/context.py) builds the tool by name via `resolve_workflow_tool` BEFORE `authorize_tool_call` checks `is_excluded_from_script_calls` and returns `tool_unavailable`. `ProjectScope.resolve` only catches ValueError; any other exception at tool-build goes to tool_call_protocol.py:~229 and becomes `internal_error`. Two failure causes (reproduced): (1) `code_executor`, `generate_image_tool` -> ModelNotAllowedException: `find_tool_by_invoke_request` calls `toolkits[...](assistant, user, '', False, '')`, empty string goes as llm_model into get_file_system_toolkit -> get_llm_by_credentials(''). (2) `file_analysis`, `pptx/pdf/csv/excel/docx_tool`, `email_analysis_tool` -> KeyError 'File Analysis': get_core_tools returns empty because get_toolkit_methods() has no key for ToolSet.FILE_ANALYSIS (File Analysis built separately, toolkit_service.py:~583). Assistant-step scope uses ToolListScope (dict lookup) so is unaffected. `execute_workspace_script` is already rejected by name (tool_blocked). Desired fix: deny excluded tools by name (tool_unavailable) before building them in authorize/resolve path, and/or make ProjectScope.resolve robust to any build exception; avoid building excluded tools at all. Also: xfail tests for this are in origin/EPMCDME-15559_script-excluded-tools-tests (commit a29c60c4) under test-harness/ with KNOWN_DEFECT_REASON; find where existing script-tool-call unit tests live in this repo.

---

## 2. Codebase Findings

### Existing Implementations
Package: `/Users/yanaasadchaya/Projects/epam/codemie/codemie/src/codemie/service/script_tool_calls/` (note: under `service/`, not elsewhere).
- `context.py` — `ScriptScope` Protocol (`resolve(name) -> BaseTool|None`, `callable_tools()`); `NoScope` (raises `ToolCallRefused no_context`); `ToolListScope` (dict lookup); `ProjectScope.resolve` (lines ~96-101): `try: return self._resolver(user, project, name) except ValueError: return None`. `ScriptToolRegistry.context()` returns `ProjectScope` when `workflow_project` was given (bare workflow tool step).
- `workflow_resolution.py` — `resolve_workflow_tool(user, project, name)`: creates a virtual assistant (`VirtualAssistantService.create_from_tool_invocation`), calls `ToolsService.find_tool_by_invoke_request(name, ToolkitService.get_toolkit_methods(), assistant, user, project)`, deletes the assistant in `finally`. Docstring promises only `ValueError` for "no such tool".
- `authorizer.py` — `authorize_tool_call(context, name)`: blocks `EXECUTE_WORKSPACE_SCRIPT_TOOL.name` by name (`CODE_TOOL_BLOCKED`); otherwise `tool = context.scope.resolve(name)`; `if tool is None or is_excluded_from_script_calls(tool): raise _unavailable(name)` (`CODE_TOOL_UNAVAILABLE`). Exclusion is checked only AFTER the build; this is the defect ordering.
- `exclusions.py` — `is_excluded_from_script_calls(tool: BaseTool)`: opt-in via `script_callable is True` (ClassVar, default False in `src/codemie_tools/base/codemie_tool.py:88`), supervisor handoff name prefix, instance `thread_generator`. It takes an INSTANCE, never a name; no name- or class-level lookup exists.
- `handler.py` — line ~79 `tool = authorize_tool_call(context, name)`; catches `ToolCallRefused` -> coded response; line ~96 `except Exception` wraps tool *execution* only. Authorize is not inside that try for generic exceptions.
- Outer total handler in `src/codemie_tools/data_management/code_executor/tool_call_protocol.py` (~line 227, `except Exception  # noqa: BLE001`) converts any escaped exception into `internal_error` (the observed symptom).
- Build path: `/src/codemie/service/tools/tool_service.py:119` `find_tool_by_invoke_request`: `get_toolkit(tool_name,...)` (uses `find_toolkit_for_tool`, raises ValueError if not found), then `ToolkitService.get_core_tools(...)`; `if not tools: tools = toolkits[toolkit.toolkit](assistant, user, '', False, '')` (line 133: the `''` llm_model arg and the unguarded dict index -> KeyError for `ToolSet.FILE_ANALYSIS`); then `find_tool` (raises ValueError when not found). Contrast sibling `find_tool_from_config` (line ~101) which uses `toolkits.get(...)` guarded.
- `src/codemie/service/tools/toolkit_service.py`: `get_toolkit_methods` at line 163; `get_core_tools` at 639; File Analysis built separately at ~583 (`file_analysis_configured` -> `add_file_tools`).
- Opt-in snapshot: `tests/codemie/service/script_tool_calls/callable_tool_classes.txt` lists fully-qualified classes with `script_callable = True` (reviewed list; checked by `test_exclusions.py:185-191`). Excluded classes (code_executor, file analysis tools, email_analysis_tool, pptx/pdf/csv/excel/docx tools) are therefore not in it, but the repo has no name->class registry usable without building the tool.

### Architecture and Layers Affected
Service layer (`codemie.service.script_tool_calls`: authorizer, context scope, workflow_resolution); touches tool service layer (`codemie.service.tools.tool_service.ToolsService`, `toolkit_service.ToolkitService`) as the build path; protocol layer in `codemie_tools/data_management/code_executor` (error mapping, read-only here).

### Integration Points
- `ScriptToolRegistry` is wired from `codemie_tools/data_management/workspace/toolkit.py`, `service/tools/toolkit_service.py`, `service/agent_workspace_service.py`, `service/mcp/toolkit.py` (all import script_tool_calls), and the workflow tool node (`workflows` tool node passes workflow_project; tested in `tests/codemie/workflows/test_tool_node_script_step.py`).
- `context.py` imports `resolve_workflow_tool` lazily-importing services (circular import avoidance noted in docstring).

### Patterns and Conventions
- Refusals are raised as `ToolCallRefused(code, message)`; codes `CODE_TOOL_UNAVAILABLE`, `CODE_TOOL_BLOCKED`, `CODE_NO_CONTEXT` from `codemie_tools/.../runtime_sdk/codemie_runtime_sdk`.
- "The only place that decides" is `authorize_tool_call`; `exclusions.py` is "the single place" for exclusion rules and deliberately names no tool class.
- Resolver is injectable into `ProjectScope(user, project, resolver)` for tests.
- Apache license header on all source files; `from __future__ import annotations`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists (agents/, api/, architecture/, testing/, development/error-handling.md, etc.). Relevant: `architecture/service-layer-patterns.md`, `development/error-handling.md`, `testing/testing-patterns.md`, `testing/testing-service-patterns.md`, `testing/local-verification.md`, `quality-gates.md`. Not read in depth. Also `src/codemie_tools/data_management/code_executor/README.md` documents script tool calls / `script_callable`.

### Architectural Decisions
Inline docstrings: opt-in `script_callable` flag; exclusion rules never name a tool class (exclusions.py docstring) — a name-based deny list would diverge from this stated principle.

### Derived Conventions
Failures at lookup become `tool_unavailable` (not internal_error); `ProjectScope.resolve` returning `None` is the "no such tool" signal.

---

## 4. Testing Landscape

### Existing Coverage
Unit tests live in `/Users/yanaasadchaya/Projects/epam/codemie/codemie/tests/codemie/service/script_tool_calls/`:
- `test_authorizer.py`, `test_context.py` (ProjectScope tests lines ~101-116: resolver mock; ValueError -> None), `test_workflow_tool_step_scope.py` (real `resolve_workflow_tool` over fake catalog fixture patching `codemie.service.assistant.VirtualAssistantService`, `codemie.service.tools.ToolsService/ToolkitService`; tests for excluded tool unavailable, unknown tool unavailable, virtual assistant deleted), `test_exclusions.py` (+ `callable_tool_classes.txt` snapshot), `test_handler.py`, `test_concurrency.py`, `test_end_to_end.py`, `test_error_code_contract.py`.
- Related: `tests/codemie/workflows/test_tool_node_script_step.py`, `test_tool_node_running_user.py`, `tests/codemie/service/tools/test_toolkit_service_script_registry.py`, `tests/codemie/service/test_agent_workspace_service_tool_calling.py`.
- The xfail tests: commit `a29c60c4` / branch `origin/EPMCDME-15559_script-excluded-tools-tests` is NOT present locally (`git cat-file` fails; no remote branch listed; no `test-harness/` dir in this working tree). Needs `git fetch` to read; I did not fetch.

### Testing Framework and Patterns
pytest (`pytest.ini`), `unittest.mock` (MagicMock/patch), pydantic stub tool classes (`_CatalogTool(CodeMieTool)` with `script_callable`), class-based and function tests, `pytest.raises(ToolCallRefused)` with `excinfo.value.code`.

### Coverage Gaps
No existing test covers a resolver raising a non-ValueError (ModelNotAllowedException / KeyError) in `ProjectScope.resolve`, nor ordering (excluded name denied before build / no virtual assistant created). The KeyError path in `find_tool_by_invoke_request` line 133 has no test seen.

---

## 5. Configuration and Environment

### Environment Variables
None found specific to this area (not searched exhaustively).

### Configuration Files
None governing script tool calls found.

### Feature Flags and Deployment Concerns
None found. `script_callable` ClassVar is the per-tool opt-in toggle.

---

## 6. Risk Indicators

- `is_excluded_from_script_calls` needs a tool instance; excluding by name before build requires a source of truth for names/classes that does not exist today. Speculative: a name list would duplicate the opt-in flag and contradict the "no tool class named" design; alternatives are catching exceptions broadly or class-level lookup.
- Broad `except Exception` in `ProjectScope.resolve` would convert genuine infra failures (DB, auth, ModelNotAllowed for allowed tools) into `tool_unavailable`, masking errors; consider logging. Speculative.
- Excluded-by-build-failure also affects tools that are callable yet fail to build for other reasons (e.g. empty llm_model `''` at tool_service.py:133 for any toolkit needing an LLM).
- `resolve_workflow_tool` side effects: virtual assistant creation/deletion for each call, even for denied tools.
- `find_tool_by_invoke_request` is shared (tool_service.py:119) with other callers; changes there (guarding `toolkits[...]`) have wider blast radius.
- xfail test branch not locally available; harness layout (`test-harness/`, `KNOWN_DEFECT_REASON`) unverified; xfail markers will need removal when fixed.
- `callable_tool_classes.txt` snapshot test must stay in sync if any flag changes.

---

## 7. Summary for Complexity Assessment

The defect sits in the service layer package `src/codemie/service/script_tool_calls/` (context.py `ProjectScope.resolve`, authorizer.py ordering, workflow_resolution.py), with a possible adjacent touch in `service/tools/tool_service.py:133` (unguarded toolkit dict index and empty llm_model). Core change surface is small (2-4 source files, under ~50 lines), with existing unit tests in `tests/codemie/service/script_tool_calls/` giving a ready fixture pattern for new/updated tests.

The main design uncertainty is how to deny excluded tools "by name" before building when exclusion is currently instance-based and deliberately class-agnostic; options are a robust catch in `ProjectScope.resolve`, a name/class pre-check, or both. Test coverage of the area is good, but the specific failing paths are untested; the xfail tests branch must be fetched to align.

Risks: masking real errors with broad exception handling, shared `find_tool_by_invoke_request` blast radius, and the unavailable harness branch.

---

## 8. External References

None named by the task as a source of truth, aside from the test branch `origin/EPMCDME-15559_script-excluded-tools-tests` (commit a29c60c4, `test-harness/`, `KNOWN_DEFECT_REASON`): unresolved locally. The commit is not in the local object store, no matching remote-tracking branch exists, and there is no `test-harness/` directory in the working tree. A `git fetch` would be needed; not performed.
