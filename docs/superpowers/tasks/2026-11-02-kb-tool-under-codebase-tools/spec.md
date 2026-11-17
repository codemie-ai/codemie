# Spec: show search_kb under "Codebase Tools" (EPMCDME-15114 follow-up)

## Problem

"Search Knowledge Base" (`search_kb`) is the only tool in its own "Knowledge Base" group in the tools listing. A one-tool group is noise. It should appear in the "Codebase Tools" group instead, in every listing (UI and API/SDK) so the listings stay consistent. This overrides the UI-only choice in `/Users/vladimir_sesum1/codemie-dev/docs/stories/2026-10-29-kb-tool-under-codebase-tools/story.md`.

## Behavior

1. `ToolsInfoService.get_tools_info` (`src/codemie/service/tools/tools_info_service.py:79-83`) no longer emits a separate "Knowledge Base" group. When Elasticsearch is configured, `search_kb` is added to the "Codebase Tools" group through the existing in-place merge convention (`_merge_code_toolkit`, :118-131), for both `show_for_ui=True` and `False`. It appears exactly once, and when the group has to be created it is created with `search_kb` included.
2. With Elasticsearch not configured, neither the Codebase group additions nor `search_kb` appear, as today.
3. Assistant assembly is unchanged: `search_kb` is still added from the attached KB datasource and gated by `deselected_default_tools` (name-based, UI `defaultDatasourceTools.ts:21`). No UI logic change is needed because the UI renders the groups the backend returns.
4. Direct tool invoke of `search_kb`:
   - On an assistant with a KB datasource: unchanged, returns the KB tool (`tool_execution_service.py:348-349`).
   - On an assistant with a code datasource: it no longer raises "Invalid tool ... for given datasource type". The tool resolves to the Codebase group, `_get_code_tools` returns `None`, and the call falls through to `find_tool_by_invoke_request` (`tool_service.py:198-213`), which looks the name up among the Codebase toolkit's tools and fails with the standard tool-not-found `ValueError`. Intended behavior: invoke must not succeed and must fail with an error naming the tool; the exact message is the generic one.
   - The now-unreachable `ToolSet.KB_TOOLS` branch (`tool_execution_service.py:357-358`) is removed. The `KB_TOOLS` enum member stays (it lives in the shared `codemie_tools` model, `base/models.py:121`).
5. `KBToolkit` (`kb_toolkit.py`) keeps providing the `search_kb` tool metadata. Its group-level `toolkit=KB_TOOLS` wrapper is no longer emitted as a listing group.

## Acceptance criteria

- AC1: UI and API listings contain no "Knowledge Base" group and list `search_kb` once, inside "Codebase Tools", when Elasticsearch is configured.
- AC2: With Elasticsearch unconfigured, `search_kb` is absent from both listings.
- AC3: Existing Codebase tools in the group are unchanged and unduplicated.
- AC4: `find_toolkit_for_tool(search_kb)` returns the "Codebase Tools" toolkit.
- AC5: Invoke of `search_kb` on a KB-datasource assistant still returns the KB tool.
- AC6: Invoke of `search_kb` on a code-datasource assistant raises the generic tool-not-found `ValueError` (not the "Invalid tool ... datasource type" error), pinned by a new test. If the traced fall-through differs from this, the test pins the real outcome and it must still be a failure, not a success.
- AC7: Existing tests asserting the KB group are updated: `tests/codemie/service/tools/test_tools_info_service.py:47,58,72`; the mocked `{"toolkit": "Knowledge Base"}` fixtures in `test_tool_execution_service.py:348,392` and `test_tool_execution_invoke.py:169` become "Codebase Tools" where they model the new catalog.
- AC8: Default-tool pre-selection/deselection for `search_kb` keeps working (no change to `deselected_default_tools` handling; existing tests stay green).

## Non-goals

- No migration of stored assistants/skills holding `{toolkit: "Knowledge Base", tools: [search_kb]}`; they are left as stored.
- No changes to codemie-sdk (python/nodejs): they carry no group-name constants, `search_kb` appears only as a `deselected_default_tools` example.
- No UI changes; the UI constants `TOOLKITS.KnowledgeBase`, the `TOOLKIT_ORDER_KEYS` entry and the icon mapping stay, since saved assistants may still carry the old group name.
- No removal of the `ToolSet.KB_TOOLS` enum member.
- No forced re-index of the tool search index (it picks up the new group on its next indexing run).
- No change to how KB tools are attached to assistants at runtime.
- The separate codemie-docs repo is not touched (not in the workspace).

## Decisions

- Move `search_kb` into "Codebase Tools" in both UI and API/SDK listings over UI-only — user
- Fail with the generic tool-not-found error on code-datasource invoke over keeping a dedicated "Invalid tool" error or making invoke succeed
- Remove the unreachable `KB_TOOLS` branch in `_get_context_tools` over keeping dead code; keep the enum member since it is shared
- Add the tool through the `_merge_code_toolkit` convention over listing `search_kb` in two groups, which would make `find_toolkit_for_tool` order-dependent (last match wins, `tool_service.py:247-251`)
- Leave UI KB constants and stored "Knowledge Base" selections untouched over cleaning them up, since old saved data may still reference them
- Leave the SDKs unchanged over adding group handling, as they have no group references

## Risks and assumptions

- Assumption: the fall-through in AC6 ends in tool-not-found; this was inferred from reading `find_tool_by_invoke_request`, not run. The new test confirms it.
- The tool search index and existing saved assistants keep the old group name until re-indexed/re-saved.
