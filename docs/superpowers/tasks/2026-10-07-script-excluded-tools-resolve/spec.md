# Spec: excluded tools give tool_unavailable in script tool calls (EPMCDME-15559)

## Problem
In a workflow tool step, `tool.call` goes through `ProjectScope.resolve` (`src/codemie/service/script_tool_calls/context.py` ~96-101), which builds the tool via `resolve_workflow_tool` BEFORE `authorize_tool_call` (`authorizer.py`) checks `is_excluded_from_script_calls`. `ProjectScope.resolve` catches only `ValueError`. Other build errors escape to `tool_call_protocol.py:~229` and surface as `internal_error`:
1. `code_executor`, `generate_image_tool`: `ModelNotAllowedException` (`tool_service.py:133` passes `''` as llm_model).
2. `file_analysis`, `pptx/pdf/csv/excel/docx_tool`, `email_analysis_tool`: `KeyError 'File Analysis'` (`get_toolkit_methods()` has no `ToolSet.FILE_ANALYSIS` entry; unguarded `toolkits[...]`).
Assistant-step scope (`ToolListScope`, dict lookup) is not affected.

## Goal
An excluded tool always yields `tool_unavailable`, never `internal_error`, and is not built when its exclusion is statically known.

## Design
Three layers, each independent of the others:

### 1. Central tool registry (static tool facts)
- New module next to the base class, `src/codemie_tools/base/` (e.g. `tool_registry.py`). `codemie_tools` ships in the same Poetry project but is a separate top-level package, so the module must not import from `codemie` at module level.
- Populated at class-definition time from `CodeMieTool.__pydantic_init_subclass__` (`codemie_tool.py`), where `cls.model_fields["name"].default` and `cls.script_callable` are readable.
- Skipped: abstract classes; classes without a default `name` (IdeTool, MCPTool, ContextAwareMCPTool, ProviderToolBase); classes whose module is not under `codemie_tools.` or `codemie.` (keeps test-defined subclasses out, same filter as `tests/tool_discovery.py`).
- Record per name: name, class(es), module, `script_callable`. One name can map to several classes (git tools per provider). The record is a small dataclass so later fields (toolkit, ToolMetadata, config class, output_format) can be added; none are added now.
- API: `register`, `lookup(name)`, `ensure_loaded()`, `is_known_excluded_from_script_calls(name)`.
- `ensure_loaded()` imports the tool packages once (`codemie_tools`, `codemie.agents.tools`, `codemie.service.mcp`, `codemie.service.provider`) via importlib. Import failures are logged, not swallowed. It is idempotent.
- "Known excluded" is true only when the name is registered and ALL classes with that name have `script_callable` False. Unknown or mixed names are not known-excluded.

### 2. Pre-filter in script_tool_calls
- A separate module in `src/codemie/service/script_tool_calls/` holds the glue (registry check). `exclusions.py` is unchanged and still imports nothing from tool packages (an existing test asserts this).
- `ProjectScope.resolve` asks the registry before calling the resolver. Known-excluded returns `None` (so `tool_unavailable`): no tool is built and no virtual assistant is created.
- Unknown or mixed names follow the existing path (build, then the `is_excluded_from_script_calls` instance check in `authorize_tool_call`). The instance check stays the authority; the registry is a fail-closed pre-filter and a missing entry never allows anything.
- `execute_workspace_script` keeps its by-name `tool_blocked` in `authorize_tool_call`.

### 3. Backstop
`ProjectScope.resolve` catches any non-`ValueError` exception from the resolver, logs a warning that includes the exception, and returns `None` (`tool_unavailable`). `ValueError` handling is unchanged.

### 4. Documentation and comments
- `.ai-run/guides/agents/tool-overview.md`: add a minimal section stating that the registry is THE single source of static tool information, where it lives, that static tool facts go there by extending the record, and that nobody should add a new scan or catalog. Follow the existing "Avoid | Prefer" table plus Evidence line style. Do not rewrite existing content.
- `.ai-run/guides/agents/custom-tool-creation.md`: one-line pointer that a new tool class registers itself automatically and new static facts extend the registry.
- Comment-only changes (no behavior change) in the overlapping components, pointing at the registry as the future source: `codemie_tools/base/toolkit_provider.py`, `codemie/service/tools/discovery/metadata_finder.py` (`ToolMetadataFinder`), `tests/tool_discovery.py`, `codemie/service/tools/tools_info_service.py` (hand-listed standard toolkits), `ToolkitService.get_toolkit_methods`. The last comment notes that FILE_ANALYSIS has no factory entry because it is built separately, and that a future sync test between registry toolkits and factories belongs there.

## Acceptance criteria
1. A script calling `code_executor`, `generate_image_tool`, `file_analysis`, `pptx_tool`, `pdf_tool`, `csv_tool`, `excel_tool`, `docx_tool` or `email_analysis_tool` in a workflow tool step gets `tool_unavailable`, never `internal_error`.
2. For registry-known excluded names, the resolver is not invoked and no virtual assistant is created.
3. If the resolver raises a non-`ValueError` exception, `ProjectScope.resolve` returns None and logs a warning.
4. `ValueError` from the resolver still returns None. Callable tools (e.g. Jira, `get_repository_tree`) still resolve and run.
5. A name with mixed `script_callable` flags, or an unknown name, takes the build path and the instance check.
6. Registry: registers concrete subclasses with a default `name` under the `codemie_tools.` / `codemie.` roots; skips abstract classes, nameless classes and classes from other modules; supports multiple classes per name; `ensure_loaded()` is idempotent and logs import failures.
7. Sync test: for every registry name flagged not-callable, an instance of that class is also reported excluded by `is_excluded_from_script_calls`.
8. `exclusions.py` is unchanged and its no-tool-package-import test still passes. `tool_service.py` is unchanged.
9. The docs and comments listed in section 4 exist, and existing content is not rewritten.
10. The existing `callable_tool_classes.txt` snapshot test still passes. It moves onto the registry only if that is trivial.

## Non-goals
- No change to `ToolsService.find_tool_by_invoke_request` or `tool_service.py`.
- No API endpoint and no docs generation.
- No migration of `toolkit_provider`, `ToolMetadataFinder` or `ToolsInfoService` onto the registry (comments only).
- No extra registry fields (toolkit, metadata, config class, output_format).
- No name-based deny list in `script_tool_calls`.
- No change to the exclusion rules themselves.
- No work on the xfail test-harness branch (not available locally).

## Follow-ups (separate tickets)
- Fix the empty `llm_model` argument and the unguarded `toolkits[...]` at `tool_service.py:133` (sibling `find_tool_from_config` already uses `.get`).
- Consolidate `toolkit_provider`, `ToolMetadataFinder`, `ToolsInfoService` and `tests/tool_discovery.py` onto the registry.
- Test harness (origin/EPMCDME-15559_script-excluded-tools-tests, a29c60c4): refine `KNOWN_DEFECT_REASON` with the two causes, drop the xfails after this fix, and optionally assert that `internal_error` is not returned.

## Risks
- Importing every tool package in `ensure_loaded()` adds first-call latency and may hit circular imports. Mitigation: lazy, once, import errors logged.
- The broad catch can hide real infrastructure failures as `tool_unavailable`. Mitigation: warning log with the exception.
- Registry population depends on import side effects, so an unimported tool is simply unknown and falls back to the old path.
