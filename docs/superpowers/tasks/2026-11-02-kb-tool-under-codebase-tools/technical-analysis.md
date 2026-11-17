# Technical Research

**Task**: toolkit grouping search_kb codebase
**Generated**: 2026-11-02
**Research path**: codegraph

---

## 1. Original Context

Follow-up requirement: "Since KB toolkit has only one tool, it should be displayed under Codebase tools"

Today "Search Knowledge Base" (search_kb) is the only tool in its own "Knowledge Base" group in the assistant tools list. That group comes from the backend tool listing, which this ticket also exposed to the UI. The UI has no grouping logic of its own: it renders the groups the backend returns and handles default tools by name. So pre-selection and deselection keep working whichever group the tool is in.

At runtime the group has no effect on search_kb: the tool is added from the attached KB datasource and gated by deselected_default_tools. The one exception is the direct tool-invoke API, which reads the group from the API/SDK listing. Option chosen: "B. Move it everywhere (UI and API/SDK listings). The listings stay consistent. Direct tool invoke changes behaviour: invoking search_kb on an assistant whose datasource is a code datasource no longer returns the explicit 'Invalid tool ... for given datasource type' error, and there is no test for it. The SDKs and docs may refer to 'Knowledge Base'; not yet checked."

---

## 2. Codebase Findings

### Existing Implementations

Purpose A: the assistant tools list shows search_kb in some group.
- `src/codemie/service/tools/tools_info_service.py:79-83`: under `ElasticSearchClient.is_configured()`, `_merge_code_toolkit(toolkits, show_for_ui)` runs, then separately `toolkits.append(KBToolkit.get_tools_ui_info())`. The KB append is unconditional on `show_for_ui`, so UI and API listings both get the "Knowledge Base" group. Includes: only `search_kb`. Excludes: nothing by role/admin.
- `tools_info_service.py:118-131` `_merge_code_toolkit`: finds the dict with `toolkit == "Codebase Tools"` among autodiscovered toolkits and extends its `tools` with `CodeToolkit.get_tools_ui_info()` (UI) or `get_tools_api_info()` (API); if absent, appends the backend dict. This is the existing way tools are added to the Codebase group.
- `src/codemie/agents/tools/kb/kb_toolkit.py:25-36`: `KBToolkitUI(toolkit=ToolSet.KB_TOOLS, tools=[Tool.from_metadata(SEARCH_KB_TOOL)])`; `KBToolkit.get_tools_ui_info()` returns its dump. No separate API variant. `toolkit` has no `label` set (the Codebase autodiscovered toolkit sets `label="Codebase Tools"`, `src/codemie_tools/code/toolkit.py:30-32`).
- `src/codemie/agents/tools/code/code_toolkit.py:44-66`: `CodeToolkitUI` (toolkit `CODEBASE_TOOLS`) with 7 tools including `REPO_TREE_TOOL`, `CODE_SEARCH_TOOL`; `get_tools_api_info()` returns the same dump as UI info.
- `src/codemie_tools/base/models.py:120-121`: `CODEBASE_TOOLS = "Codebase Tools"`, `KB_TOOLS = "Knowledge Base"`. Uses of `ToolSet.KB_TOOLS` in src: `kb_toolkit.py:26`, `tool_execution_service.py:357` only.
- `src/codemie/agents/tools/kb/search_kb.py:76-78`: `SEARCH_KB_TOOL` (name `search_kb`, label "Search Knowledge Base").

Purpose B: search_kb is available at runtime for an assistant with a KB datasource, independent of group.
- `toolkit_service.py` `_add_kb_tools` (KB context, gated by `deselected_default_tools`); a stored `search_kb` entry in `assistant.toolkits` goes through `_initialize_tool` and is skipped for backend datasource tools (per prior research; not re-read line by line here). Workflows store only the tool name.

Purpose C: direct tool invoke on an assistant (`ToolExecutionService`).
- `src/codemie/service/tools/tool_execution_service.py:326-358` `_get_context_tools`: `toolkit = ToolsService.find_toolkit_for_tool(user, tool_name)`; VCS branch; deselected-default check (332-339); `if not assistant.context: return`; KB context returns `_get_kb_tools` before any toolkit check (348-349, any tool name); CODE context: `CODEBASE_TOOLS` -> `_get_code_tools`, `GIT` -> `_get_git_tools`, `KB_TOOLS` -> `raise ValueError("Invalid tool: ... for given datasource type: ...")` (357-358). Other contexts fall off the end (returns None).
- `tool_execution_service.py:461-484` `_get_code_tools`: `ToolDiscoveryService.get_toolkit_method_for_tool(tool_name, CodeToolkit)`; returns `None` if no method. Caller (`:255-262`): a falsy result falls through to `ToolsService.find_tool_by_invoke_request(...)`. What that returns for `search_kb` was not traced (unverified).
- `src/codemie/service/tools/tool_service.py:236-263` `find_toolkit_for_tool`: iterates all toolkits and keeps the **last** one containing the name; `_get_available_toolkits` (293-294) calls `get_tools_info(user=user)` with default `show_for_ui=False`. Other callers: `workflows/validation/tool_access.py:67`, `resources.py:386,422`, `config_resources_validation.py:191`, `tool_service.py:107`.

Other touched code (not named above):
- UI `codemie-ui/src/constants/assistants.ts:75-96` `TOOLKITS` (has `KnowledgeBase: 'Knowledge Base'` at 79) and `TOOLKIT_ORDER_KEYS` (98-129; includes both 'Codebase Tools' at 101 and 'Knowledge Base' at 116). `TOOLKITS.KnowledgeBase` is used only in `pages/assistants/components/ToolkitIcon.tsx:42` (maps to `ResearchSvg`); `TOOLKITS.CodebaseTools` -> `CodeSvg` at :41. `TOOLKIT_ORDER_KEYS` is used only in `AssistantDetailsSidebarSections.tsx:25,294` (`sortToolkitsByOrder`).
- UI `src/utils/defaultDatasourceTools.ts` keys on tool names (`SEARCH_KB: 'search_kb'`, line 21), not toolkit names; its tests use `search_kb` fixtures (`__tests__/defaultDatasourceTools.test.ts:29,36,96-126`).

### Decisions Already in the Code
- Group placement is decided only by the backend catalog (`tools_info_service.py`); UI has no KB special case beyond the icon/order constants above. Convention for adding tools to an existing group: extend its `tools` (`_merge_code_toolkit`, line 129).
- The direct-invoke error for KB tool + CODE context is keyed to the group name (`ToolSet.KB_TOOLS`), not to the tool name; no recorded reason found. Alternative in the code: KB context short-circuits by context type regardless of tool.
- 15114 made UI and API code listings identical (`code_toolkit.py:64-65` comment) and made the KB group visible to both (`tools_info_service.py:82-83`).
- A previous story artifact exists at `/Users/vladimir_sesum1/codemie-dev/docs/stories/2026-10-29-kb-tool-under-codebase-tools/story.md` (AC line 53, jira-comment-draft line 11) that records the opposite choice: only the UI listing changes, API/SDK keep the "Knowledge Base" group and the error. The task_context here selects option B instead.

### Task Statements Checked Against the Code
- "The group comes from the backend tool listing": holds (`tools_info_service.py:82-83`).
- "The UI has no grouping logic of its own: it renders the groups the backend returns": holds for grouping; the UI does hold KB-named constants (`TOOLKITS.KnowledgeBase`, `TOOLKIT_ORDER_KEYS` entry, icon mapping). Not verified in this run: the UI form and pickers' rendering code beyond constants; prior report states they render catalog groups (`useToolkitSelection.ts` keyed by `tk.toolkit`).
- "Pre-selection and deselection keep working whichever group": holds for `defaultDatasourceTools.ts` (name based, line 21). Sidebar merges default tools into stored toolkits by toolkit name (`AssistantDetailsSidebarSections.tsx:131-143`, per prior report; not re-read).
- "At runtime the group has no effect on search_kb": holds for assistant assembly per prior research; contradicted nowhere. Exception: `tool_execution_service.py:357-358`, consistent with the task.
- "The direct tool-invoke API reads the group from the API/SDK listing": holds (`tool_service.py:293-294` default `show_for_ui=False`).
- "Moving it makes invoke on a code-datasource assistant no longer return the explicit 'Invalid tool' error": holds as stated. With the tool in `CODEBASE_TOOLS`, the call reaches `_get_code_tools` (353-354), which returns `None` for `search_kb` (`ToolDiscoveryService` finds no method on `CodeToolkit`, 478-480), then falls to `find_tool_by_invoke_request` (262). Outcome of that fall-through: unverified. Keeps the `KB_TOOLS` branch (357-358) in place, which would become unreachable for the catalog, unless `search_kb` stays in two groups.
- "There is no test for it": holds. Invoke tests mock `find_toolkit_for_tool` -> `{"toolkit": "Knowledge Base"}` only with KB context (`test_tool_execution_service.py:348,392`, `test_tool_execution_invoke.py:169`); no CODE-context + KB toolkit test found.
- "The SDKs and docs may refer to 'Knowledge Base'": checked. codemie-sdk (python and nodejs) has no toolkit enum or constant with "Knowledge Base"/"Codebase Tools"/`search_kb` group; `search_kb` appears only as an example value of `deselected_default_tools` (python README:264, tests `test_assistant_deselected_default_tools.py`, nodejs `assistant*.test.ts`, `assistant.schema.test.ts`, nodejs README). "Knowledge Base" in the python SDK refers to AWS Vendor Knowledge Base service (unrelated). In the codemie repo, md/yaml/json hits outside tests are SDLC task docs and `config/categories/kata-tags.yaml:35` (unrelated tag). codemie-ui `releaseNotes.json` mentions of "Knowledge Base" are historical text. The separate docs repo (codemie-docs) is not in the workspace: unverified.

### Consumers of Changed Contracts
Contract: the toolkit name/group of `search_kb` in `ToolsInfoService.get_tools_info` (both listings).
- `tools_info_service.py:82-83` (producer).
- `tool_execution_service.py:357` branch on `ToolSet.KB_TOOLS`; `:353` branch on `CODEBASE_TOOLS`.
- `tool_service.py:236-263` `find_toolkit_for_tool` (last-match) and its callers: `tool_service.py:107`, `workflows/validation/tool_access.py:67`, `resources.py:386,422`, `config_resources_validation.py:191`.
- `toolkit_lookup_service.py:69` (tool search index, `show_for_ui=True`; stores toolkit name per document; stored value changes only on re-index); `assistant_service.py:122`; `rest_api/routers/assistant/toolkits.py:56` (`GET /assistants/tools`); `rest_api/routers/tool.py:38`; `assistant/catalog.py:219`; `workflows/validation/resources.py:802` (per prior report).
- Assistant generator: `validate_tools_node.py:85` (recommended toolkits from index), `make_decision_node.py:247` + `validation/utils.py:231` `find_toolkit_for_tool` map tool -> toolkit from recommended list; UI applies by `toolkitName` (`codemie-ui/src/utils/toolkit.ts:97-125`, per prior report).
- Tests asserting the group: `tests/codemie/service/tools/test_tools_info_service.py:47,58,72` (absence/presence of `KB_TOOLS` group; `search_kb` in a `KB_TOOLS` group for UI and API); `test_tool_execution_service.py:348,392`, `test_tool_execution_invoke.py:169`.
- UI: `ToolkitIcon.tsx:42`, `TOOLKIT_ORDER_KEYS` line 116 (only affects read-only sidebar sort order).
- Stored data: assistants/skills saved with `{toolkit: "Knowledge Base", tools: [search_kb]}` (possible from the 15114 UI and API clients); no migration touches them.

### Architecture and Layers Affected
- Backend service layer: `ToolsInfoService` (catalog), `ToolsService.find_toolkit_for_tool`, `ToolExecutionService._get_context_tools`.
- Agent toolkit definitions: `agents/tools/kb/kb_toolkit.py`, `agents/tools/code/code_toolkit.py`; shared enum `codemie_tools/base/models.py` (`ToolSet`).
- UI: constants and icon mapping only (catalog-driven rendering).

### Integration Points
- Elasticsearch availability gates both groups together (`tools_info_service.py:79-83`).
- Tool search index (Elasticsearch) built from the UI listing; assistant generator reads it.
- Workflow validation resolves toolkit by tool name via the non-UI listing.

### Patterns and Conventions
- Toolkit dicts built from pydantic `ToolKit` subclasses via `model_dump()`; in-place merge into the autodiscovered Codebase dict (`_merge_code_toolkit`).

---

## 3. Documentation Findings

### Guides and Architecture Docs
No `.ai-run/guides/` file was found that covers tool grouping; conventions derived from code. Prior SDLC artifacts: `docs/superpowers/tasks/2026-10-19-epmcdme-15114-deselect-default-datasource-tools/` (story line 45 says search_kb "appears under Knowledge Base tools") and `/Users/vladimir_sesum1/codemie-dev/docs/stories/2026-10-29-kb-tool-under-codebase-tools/`.

### Architectural Decisions
None recorded for the group split beyond the 15114 story AC (line 45).

### Derived Conventions
Group names are the `ToolSet` string values; the same string is used as the persisted `toolkit` name in assistants.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/tools/test_tools_info_service.py:34-74`: group presence/absence with ES on/off and search_kb in the KB group, both listings.
- `test_tool_execution_service.py:348,392`, `test_tool_execution_invoke.py:169`: KB context with mocked KB toolkit.
- `test_toolkit_service.py`: 15114 `_add_kb_tools` deselect tests.
- UI: `defaultDatasourceTools.test.ts`, `Toolkit.defaultTools.test.tsx`, `AssistantForm.defaultTools.integration.test.tsx`, `AssistantTab.defaultTools.test.tsx`, `VirtualAssistantForm.schema.test.ts`, `store/utils/__tests__/assistants.test.ts` (per prior report; fixture group names not re-checked).

### Testing Framework and Patterns
pytest with `unittest.mock.patch` on `toolkit_provider`, `ElasticSearchClient.is_configured`, plugin and provider stubs; UI Jest/RTL.

### Coverage Gaps
- No test for direct invoke of `search_kb` with CODE context (neither the current `ValueError` nor the fall-through).
- No test for `_merge_code_toolkit` with the autodiscovered Codebase group absent.

---

## 5. Configuration and Environment

### Environment Variables
None found controlling grouping; `ElasticSearchClient.is_configured()` gates both groups.

### Configuration Files
None relevant.

### Feature Flags and Deployment Concerns
Tool search index keeps toolkit names from the last indexing run.

---

## 6. Risk Indicators

- Keeps an existing mechanism in place: the `ToolSet.KB_TOOLS` branch in `tool_execution_service.py:357-358` and the `KB_TOOLS` enum member (consumers: only that branch and `kb_toolkit.py`). See Task Statements.
- Behaviour change on direct invoke, CODE context: error becomes `_get_code_tools` -> `None` -> `find_tool_by_invoke_request` (result unverified; no test).
- `find_toolkit_for_tool` returns the last match: if `search_kb` were listed in two groups, result depends on order (`tool_service.py:247-251`).
- `tools_info_service.py` has two paths to the Codebase group (merge into autodiscovered, or append backend dict); KB and Codebase are both gated by the same ES check, so they appear together.
- 4 backend test locations assert the "Knowledge Base" group or mock it (`test_tools_info_service.py` lines 47/58/72, two in `test_tool_execution_service.py`, one in `test_tool_execution_invoke.py`).
- Existing stored `{toolkit:"Knowledge Base"}` selections and an already-built tool search index carry the old name until re-indexed/re-saved.
- A conflicting earlier story artifact (`/Users/vladimir_sesum1/codemie-dev/docs/stories/2026-10-29-kb-tool-under-codebase-tools/story.md`) records the UI-only choice; downstream stages could pick it up.
- UI constants `TOOLKITS.KnowledgeBase`, `TOOLKIT_ORDER_KEYS` 'Knowledge Base', icon mapping stay referenced independent of catalog.
- codemie-docs repo and `find_tool_by_invoke_request` behaviour for `search_kb` not verified.

---

## 7. Summary for Complexity Assessment

The group is decided in one place, `ToolsInfoService.get_tools_info` (`tools_info_service.py:79-83`), which today appends a separate `KBToolkit` dict next to the Codebase merge. Both listings use the same code. The code-side convention for putting tools into the Codebase group is the in-place merge in `_merge_code_toolkit`. The UI renders catalog groups; its KB-named pieces are two constants and an icon map entry, and default-tool handling is by tool name. The SDKs (python and nodejs) contain no toolkit name constants for this group; `search_kb` appears there only as example values for `deselected_default_tools`.

Runtime assistant assembly does not depend on the group; the one dependent is the direct-invoke path (`_get_context_tools`), whose CODE-context `KB_TOOLS` branch would stop being reached, with the call routed through `_get_code_tools` and then a fall-through whose result was not traced. Surfaces: one backend service file, plus tests (`test_tools_info_service.py` and three invoke-test mocks), optional UI constants cleanup.

Test posture: group presence is covered; the changed invoke behaviour is not. Risks: unverified fall-through result for invoke, stale stored names and the tool search index, and a conflicting earlier story artifact that records the UI-only option.

---

## 8. External References

None named by the task. Prior research file `/Users/vladimir_sesum1/codemie-dev/codemie/EPMCDME-15114-kb-tool-grouping-technical-analysis.md` was read and re-checked: its claims about `tools_info_service.py`, `kb_toolkit.py`, `code_toolkit.py`, `tool_execution_service.py:325-358`, `find_toolkit_for_tool` and the test lines were confirmed against current code. Claims about `useToolkitSelection.ts`, `toolkit.ts`, sidebar merge, `toolkit_lookup_service` index, assistant-generator nodes were not re-read. Its note that SDK/docs were unresearched is now resolved for codemie-sdk (no group-name references).
