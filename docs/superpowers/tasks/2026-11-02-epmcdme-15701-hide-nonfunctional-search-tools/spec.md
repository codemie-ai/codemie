# EPMCDME-15701: Standalone - hide nonfunctional Research web tools

## Problem

In standalone, Available Tools -> Research always lists Google Search, Google Places, Google Places Find Near and Tavily Search, and chat's automatic web search always adds Google Search and Tavily. Without `GOOGLE_SEARCH_API_KEY` / `TAVILY_API_KEY` these tools fail on use. The runtime factory already skips keyless tools (`src/codemie_tools/research/toolkit.py:73-81`); the list (`toolkit.py:69-71`) and the dynamic block (`src/codemie/service/toolkit_service.py:271-289`) do not.

## Behavior (only when `config.STANDALONE` is true)

A web tool is *available* when `webSearch` is on (`customer_config.is_feature_enabled("webSearch")`) AND its key is non-empty (same truthiness as the runtime factory, so a whitespace-only key counts as present): Google tools need `config.GOOGLE_SEARCH_API_KEY`, Tavily needs `config.TAVILY_API_KEY`. Wikipedia and Web Scrapper are always available.

1. **Offering paths** (what a user can pick) list only available tools: Available Tools -> Research (assistant toolkits router, tool router, assistant catalog) and the LLM generators that propose tools. Hidden tools are absent from the backend response, so they cannot be selected (frontend unchanged).
2. **Lookup and validation paths keep the full list.** `ToolsInfoService.get_tools_info` (`src/codemie/service/tools/tools_info_service.py:38`) feeds both offering and "is this a known tool" checks (`ToolsService.find_toolkit_for_tool`, `tool_service.py:236`, used by workflow validation `_is_tool_available`, `workflows/validation/resources.py:370`). Filtering must not reach the latter, or a saved workflow/assistant using Google Search would fail with TOOL_NOT_FOUND. So `get_tools_info` gains an opt-in parameter (default off = today's behavior, next to the existing `exclude_toolkits`); only offering consumers pass it. Lookup/validation callers (`tool_service.py:294`, `toolkit_lookup_service.py:69`, `resources.py:802`, `assistant_service.py:121`) are unchanged.
3. A saved assistant or workflow referencing a hidden tool still loads, validates and runs; the runtime factory skips the keyless tool, so it runs without it.
4. Chat's automatic web search adds only available tools from Google Search / Tavily plus Web Scrapper. If neither key is set, the toolkit is still added with Web Scrapper only.
5. The prebuilt researcher template (`src/codemie/rest_api/models/prebuilt_assistants.py:319`, built per request inside `prebuilt_assistants()`, so no import-time concern) calls `cast_to_toolkit_details([ResearchToolkitUI()])` directly and would bypass a filter in `get_tools_info`. The filter therefore lives on `ResearchToolkitUI` (opt-in argument on `get_tools_ui_info`), and line 319 uses the same filtered builder.
6. With standalone off, all paths behave exactly as today.

**Shared predicate.** One module, in the style of `src/codemie/configs/code_executor_availability.py:22-34`, takes `cfg` and a `web_search_enabled` bool resolved by the service layer (so `codemie_tools` does not import `customer_config`). It holds the per-tool key check and the `webSearch` term, and serves the list, the template and the dynamic block. The runtime factory may call its key check without behavior change, but is otherwise untouched. In the dynamic block the predicate evaluates `webSearch` itself; the block's own existing gate stays for non-standalone. Hiding in the list follows the EPMCDME-15706 shape (`src/codemie_tools/data_management/file_system/toolkit.py:96-124`): filter by tool name on a fresh instance, never mutate the class-level default list.

## Acceptance criteria

1. Standalone off: Research list, template and chat web search are unchanged.
2. Standalone, `webSearch` on, Google key non-empty: Google Search, Places, Places Find Near listed.
3. Standalone, Google key absent/empty: those three not listed.
4. Standalone, `webSearch` on, Tavily key non-empty: Tavily Search listed.
5. Standalone, Tavily key absent/empty: Tavily not listed.
6. Standalone, `webSearch` off: all four keyed tools hidden even with keys set.
7. Standalone: Wikipedia and Web Scrapper always listed.
8. Standalone, `webSearch` on, key missing: automatic web search does not add the tool needing it; chat answers without tool error.
9. Standalone, both keys set, `webSearch` on: automatic web search uses Google Search and Tavily as today.
10. Frontend shows the backend list with no tool-specific filtering (verified by no frontend diff).
11. Standalone, no Google key: the prebuilt researcher template omits Google tools.
12. Standalone, no Google key: workflow/assistant validation of a saved config that uses Google Search still passes (no TOOL_NOT_FOUND), and `find_toolkit_for_tool` still finds it.

## Decisions

- `config.STANDALONE` as the gate over the env-reading `is_standalone()` helper: it is the single gate from EPMCDME-16473 and the 15706 list/dynamic-block precedent uses it.
- One shared predicate (key check and `webSearch` inside it) over per-site key checks: three call sites need identical rules.
- Filter only offering consumers via an opt-in `get_tools_info` parameter over filtering the shared list: validation and lookup read the same list and must keep seeing hidden tools (AR-1).
- Filter placed on `ResearchToolkitUI` so the prebuilt template shares it over a separate template-only filter (AR-2).
- Runtime factory intentionally ignores `webSearch` and is left unchanged over aligning it: a saved assistant keeps running Google Search when `webSearch` is off; only the picker hides it (AR-3).
- Dynamic web search keeps Web Scrapper when no key is set over skipping the toolkit: Web Scrapper is always available (D2).
- Wikipedia is never hidden over the ticket title's wording: the body and out-of-scope section say so.
- `webSearch` is a master switch for the four keyed tools in standalone (D1, D2) - user
- Chat's automatic web search follows the keys in standalone (D3) - user
- Rules do not apply outside standalone (D4); prebuilt templates follow the filtered list (D5); availability decided in backend (D6) - ticket decisions proposed by PO

## Non-goals

- Frontend changes.
- Wikipedia failures or network access in standalone.
- Any behavior change outside standalone, including the meaning of `webSearch` there.
- New feature flags.
- Altering, invalidating or failing validation of saved assistants/workflows that already reference a hidden tool.
- Changing the behavior of the runtime factory `ResearchToolkit.get_tools`, which already applies the key rules and intentionally ignores `webSearch`.

## Assumptions and risks

- Which `validate_workflow_config_resources_availability` (`resources.py:749` or `config_resources_validation.py:254`) `workflow.py:279` reaches is unchecked; both call `_is_tool_available`, and both stay on the unfiltered path, so the outcome is the same.
- The `webSearch` DB override may change between calls through a cached `ToolsInfoService` result: unverified; plan must check the offering path reads it per request.
- Frontend does no per-tool filtering: unverified (frontend repo not in reach).
- Generators (`assistant_generator_service.py:892`, `skill_generator_service.py:119`, `workflow_generator_service.py:63`) are treated as offering consumers and stop proposing hidden tools.
