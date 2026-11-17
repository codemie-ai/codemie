# Technical Research (sliced from parent: backend component `codemie`)

**Task**: Backend: deselectable datasource default tools (assistant-level list of deselected default tools; runtime, persistence, listing, invoke)
**Generated**: 2026-10-16
**Research path**: codegraph (sliced from ../../technical-analysis.md; no re-analysis)

---

## Design decision applied

Deselects are stored in one new assistant-level list, empty by default, on the assistant and assistant-version records. The existing tool selection (`toolkits`) is unchanged, so legacy assistants behave as today and smart tool selection (which runs only when no toolkits are selected) is unaffected.

## Codebase Findings (backend, `codemie/src/codemie/`)

- `agents/tools/code/code_toolkit.py` — `CodeToolkitUI` (ToolSet.CODEBASE_TOOLS) lists only REPO_TREE_TOOL_V2, CODE_SEARCH_TOOL_V2, READ_FILES_TOOL, READ_FILES_WITH_SUMMARY_TOOL, CODE_SEARCH_BY_PATHS_TOOL. `get_tools_ui_info()` returns that; `get_tools_api_info()` appends the standard REPO_TREE_TOOL and CODE_SEARCH_TOOL ("hidden from UI but required"). `CodeToolkit.search_code_tool(with_filtering)` and `get_repo_tree_tool(with_filtering)` pick V2 vs standard.
- `agents/tools/code/tools_vars.py` — standard `get_repository_file_tree`, `search_code_repo`; V2 `get_repository_file_tree_v2` ("Get Repo Tree with filtering (Experimental)"), `search_code_repo_v2`. `REPO_TREE_TOOL.description`/`.react_description` say "It must be the first tool to use to get project context"; V2 description says "must be the first tool ... if direct file name or path is not specified"; V2 `react_description` is reused from REPO_TREE_TOOL. `CODE_SEARCH_TOOL` descriptions reference file paths "from repository tree". `CODE_SEARCH_BY_PATHS_TOOL`, READ_FILES_* descriptions and system-prompt templates were not read.
- `agents/tools/kb/kb_toolkit.py` — `KBToolkitUI` (ToolSet.KB_TOOLS) with `SEARCH_KB_TOOL`; `KBToolkit.get_tools()` always returns `SearchKBTool`.
- `service/tools/toolkit_service.py` — `add_context_tools()` (l.1203) iterates `assistant.context`: KNOWLEDGE_BASE -> `_add_kb_tools()` (l.1463, unconditional); CODE -> `_add_code_tools()` (l.1550) then `_add_git_related_tools()`. `_add_code_tools` gates the optional tools on `tool_exists(name)` against `assistant.toolkits`; repo tree/search are added when the V2 variant is selected OR `not exclude_extra_context_tools`. `get_tools()` (l.477) runs semantic tool search only when `not selected_toolkits and smart_tool_selection_enabled` (l.528).
- `service/tools/tools_info_service.py` — `get_tools_info(show_for_ui, ...)`; `_merge_code_toolkit()` uses UI info when `show_for_ui` else API info; `KBToolkit.get_tools_ui_info()` appended only when Elasticsearch is configured and `not show_for_ui`. Listing consumers: tool-search index (`ToolkitLookupService._get_tools_metadata`, `show_for_ui=True`), assistant-generator validation, SDK `get_tools`.
- `service/tools/tool_execution_service.py` — direct invoke: `invoke()` -> `invoke_tool_with_system_integration` -> `get_tool_with_system_integration` -> `_get_context_tools()` (l.302) takes `next(iter(assistant.context))` only; `_get_kb_tools()` has no selection check; `_get_code_tools()` sets `with_filtering` for V2 names. Router `rest_api/routers/tool.py::invoke_tool` blocks `config.HTTP_BLOCKED_TOOLS`.
- `rest_api/models/assistant.py` — `AssistantBase`, `Assistant` (table `assistants`, `toolkits` and `context` as JSON lists), version configuration model, `Assistant.update_assistant` -> `AssistantVersionCompareService.has_configuration_changes` and `AssistantVersionService.create_new_version`. `Context.__eq__` compares only `context_type` and `name`.
- `service/assistant/assistant_integration_validator.py` / `credential_validator.py` — save-time validation is credential-only; unknown tool names pass (`ToolMetadataService.requires_credentials` returns false). Request-model constraints on toolkit names not checked.
- `core/workflow_models/workflow_models.py`, `service/assistant/virtual_assistant_service.py` — virtual assistants built from workflow config. The workflow YAML setting is plumbed through `exclude_extra_context_tools`; call sites and the workflow-referenced-assistant path not traced.
- `service/assistant/assistant_version_migration_service.py` — builds `AssistantConfiguration` from an assistant field by field (`context`, `toolkits`, ...); a new field must be added here as well as to version save/restore.
- Alembic migrations live in `src/external/alembic/versions/` (new columns on assistant and assistant-configuration tables).

## Testing Landscape

- `tests/codemie/service/tools/test_toolkit_service.py`, `test_toolkit_service_git_datasource_warning.py` — `add_context_tools`, `filter_tools`, `_add_kb_tools`.
- `tests/codemie/service/tools/test_tool_execution_service.py`, `test_tool_execution_invoke.py`, `test_tool_execution_search.py`, `test_param_mapping.py` — invoke and `_get_code_tools`.
- `tests/codemie/agents/tools/code/test_code_tools_health.py`; `tests/codemie/service/tools/test_tools_info_script_callable.py`, `test_toolkit_lookup_service.py`, `test_tool_discovery_service.py`; `tests/codemie/rest_api/models/test_assistant_model.py`.
- Gaps: version compare/restore, export/import and `virtual_assistant_service` with default-tool selection have no located tests.

## Risk Indicators

- Schema change on assistants and versions with migration; version save/restore/compare, migration service and export/import must carry the field.
- Standard-vs-filtering exclusivity must be enforced server-side; today V2 silently wins.
- Listing change affects tool-search index and generator validation.
- Direct invoke has no selection check and only inspects the first context.
- "Call repo tree first" text sits in several tool descriptions; unread descriptions and prompts may contain more.
- Not traced: version compare/service, export/import, workflow-referenced assistant, workflow YAML plumbing.

## Summary for Complexity Assessment

Single-repo backend slice: one new persisted assistant field with migration, models, version/compare/restore/export-import and workflow assistants; listing changes; runtime gating of default code and KB tools; direct-invoke rejection; server-side variant-conflict and unknown-name validation; tool description cleanup. Dozen-plus files plus tests. Existing unit tests can be extended; versions, import/export and workflow paths have little coverage.
