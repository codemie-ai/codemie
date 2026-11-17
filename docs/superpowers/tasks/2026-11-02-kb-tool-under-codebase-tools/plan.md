# search_kb under "Codebase Tools" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** List `search_kb` once inside "Codebase Tools" (UI and API listings) instead of its own "Knowledge Base" group, and keep direct invoke on code-datasource assistants a clear failure.

**Architecture:** Extend `_merge_code_toolkit` with an extra-tools parameter so `search_kb` joins the Codebase group in place. Replace the group-keyed `KB_TOOLS` invoke check with an explicit tool-name rule.

**Tech Stack:** Python, pytest, `unittest.mock`.

**Spec:** `/Users/vladimir_sesum1/codemie-dev/codemie/docs/superpowers/tasks/2026-11-02-kb-tool-under-codebase-tools/spec.md`

Commit per task using the repository's existing convention.

## Global Constraints

- No migration of stored `{toolkit: "Knowledge Base"}` data; no SDK, UI-constant, or `ToolSet.KB_TOOLS` enum changes.
- `search_kb` must never be listed in two groups (`find_toolkit_for_tool` keeps the last match, `tool_service.py:247-251`).
- ES unconfigured: no Codebase additions and no `search_kb`, as today.
- AC6 (reworded per arch review): invoke of `search_kb` on a code-datasource assistant raises `ValueError` naming the tool, via an explicit tool-name rule; the test asserts that exact type and message, never `KeyError`.

## Review Focus

- Codebase group absent from autodiscovered toolkits: group is created with `search_kb` included (Task 1).
- `show_for_ui` True and False both yield exactly one `search_kb` (Task 1).
- Invoke `search_kb` on a code-datasource assistant: `ValueError` naming the tool, not `KeyError` (Task 2).
- Invoke `search_kb` on an assistant with no context: behavior pinned (Task 2).

---

### Task 1: Move search_kb into the Codebase group in the catalog

**Test-first: yes — search_kb listed once under Codebase Tools in UI and API listings; no Knowledge Base group; group created with search_kb when absent; find_toolkit_for_tool returns Codebase Tools.**

**Files:**
- Modify: `src/codemie/service/tools/tools_info_service.py:79-83,118-131`
- Modify (comment only): `src/codemie/agents/tools/kb/kb_toolkit.py:25-36`
- Test: `tests/codemie/service/tools/test_tools_info_service.py:34-74`

**Interfaces:**
- Produces: `ToolsInfoService._merge_code_toolkit(toolkits: List[Dict], show_for_ui: bool, extra_tools: Optional[List[Dict]] = None) -> None`. `extra_tools` are added to the Codebase group's `tools` in both the merge and the create paths.

- [ ] **Step 1: Write failing tests** in `test_tools_info_service.py`. Update the existing assertions at lines 47, 58, 72 to the new catalog (no `KB_TOOLS` group; `search_kb` in `Codebase Tools`, once, for UI and API; absent with ES off). Add:
  - `test_search_kb_created_with_codebase_group_when_group_absent`: autodiscovery returns no Codebase dict; assert one `Codebase Tools` group containing `search_kb` once, for `show_for_ui` True and False.
  - `test_existing_codebase_tools_unchanged_and_unduplicated`: each Codebase tool name appears once (AC3).
  - `test_find_toolkit_for_tool_search_kb_returns_codebase_tools`: `ToolsService.find_toolkit_for_tool(user, "search_kb")["toolkit"] == "Codebase Tools"` with ES configured (AC4).
- [ ] **Step 2: Run** `pytest tests/codemie/service/tools/test_tools_info_service.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** In `get_tools_info`, fold the two identical `ElasticSearchClient.is_configured()` checks into one block calling `_merge_code_toolkit(toolkits, show_for_ui, extra_tools=KBToolkit.get_tools_ui_info()["tools"])`, and drop the separate append. In `_merge_code_toolkit`, add `extra_tools` to the target group's `tools` in the merge path and to the created group in the append path. Add a comment at `kb_toolkit.py:25-26` that `KB_TOOLS` and the UI KB constants stay because saved assistants may still carry the old group name.
- [ ] **Step 4: Run** the same file. Expected: PASS.

### Task 2: Explicit invoke rule for search_kb in a code context

**Test-first: yes — invoke search_kb on a code-datasource assistant raises ValueError naming the tool (not KeyError, not "Invalid tool").**

**Files:**
- Modify: `src/codemie/service/tools/tool_execution_service.py:351-358`
- Test: `tests/codemie/service/tools/test_tool_execution_service.py:348,392`, `tests/codemie/service/tools/test_tool_execution_invoke.py:169`

**Interfaces:**
- Consumes: `SEARCH_KB_TOOL` (`codemie.agents.tools.kb.search_kb`), `TOOL_NOT_FOUND_ERROR` (already used at `:339`).

- [ ] **Step 1: Write failing tests.**
  - `test_invoke_search_kb_on_code_datasource_raises_tool_not_found`: assistant with `ContextType.CODE`, `find_toolkit_for_tool` mocked to `{"toolkit": "Codebase Tools"}`; `pytest.raises(ValueError)`, message contains `search_kb` and not "Invalid tool" (AC6).
  - `test_invoke_search_kb_without_context_unchanged`: empty context; pin the current outcome of `_get_context_tools` (`None`).
  - Keep the KB-context test green (AC5). Change the mocked toolkit to `"Codebase Tools"` at `test_tool_execution_service.py:348,392` and `test_tool_execution_invoke.py:169` where they model the catalog.
- [ ] **Step 2: Run** `pytest tests/codemie/service/tools/test_tool_execution_service.py tests/codemie/service/tools/test_tool_execution_invoke.py -v`. Expected: the new code-datasource test FAILS.
- [ ] **Step 3: Implement.** In the `ContextType.CODE` branch of `_get_context_tools`, before the `CODEBASE_TOOLS` dispatch, add `if tool_name == SEARCH_KB_TOOL.name: raise ValueError(TOOL_NOT_FOUND_ERROR.format(tool_name=tool_name, available_tools=[]))`. Delete the `ToolSet.KB_TOOLS` branch (`:357-358`).
- [ ] **Step 4: Run** the same files. Expected: PASS.

### Task 3: Check legacy-group UI paths (read-only)

**Test-first: no — read-only check, no behavior change.**

**Files:**
- Read: `codemie-ui/src/pages/assistants/components/AssistantDetailsSidebarSections.tsx:131-143`, `codemie-ui/src/utils/toolkit.ts:97-125`

- [ ] **Step 1:** Read both ranges. Report in the task digest whether an assistant saved with the old "Knowledge Base" group can show `search_kb` twice, or the generator can offer a group the catalog no longer has. Do not change UI code (non-goal).

## Negative-constraint pass

- No `search_kb` in two groups: Task 1 (single merge, no separate append).
- No UI/SDK/enum/migration/re-index changes: no task edits them (Task 3 is read-only).
- ES unconfigured: Task 1 keeps one gated block and asserts absence.
- No dedicated "Invalid tool" error retained: Task 2 asserts its absence.
