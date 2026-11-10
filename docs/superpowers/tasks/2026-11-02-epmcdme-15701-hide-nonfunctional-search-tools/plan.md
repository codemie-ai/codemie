# EPMCDME-15701 Hide nonfunctional Research web tools - Implementation Plan

**Goal:** In standalone, list and auto-add only usable Research web tools (spec.md). **Architecture:** one availability predicate; opt-in filter on `ResearchToolkitUI`; opt-in `offered_only` on `get_tools_info`; dynamic web-search block uses the predicate. Commit per task using the repository's existing convention. Tests: pytest + `unittest.mock.patch`, in-file fixtures (see `tests/codemie/configs/test_config_code_executor_available.py`).

**Negative-constraint pass:** frontend untouched (no task); no new flag (none added); Wikipedia never hidden (T1); non-standalone unchanged (T1 returns True, T3/T5 branch on `config.STANDALONE`); saved configs/validation unaffected (T3 default off, lookup callers untouched); runtime factory `ResearchToolkit.get_tools` behavior untouched (no task edits it).

**Unchanged callers (must keep default off):** `tool_service.py:294`, `toolkit_lookup_service.py:69`, `workflows/validation/resources.py:802`, `assistant_service.py:121`, and the no-arg `get_tools_ui_info()` lookups at `codemie_tools/base/base_toolkit.py:31`, `service/tools/discovery/metadata_finder.py:272`, `service/tools/toolkit_service.py:368`.

**webSearch staleness check (arch-review note):** `ResearchToolkit` is not in the cached `toolkit_provider.get_available_toolkits_info()`; `get_tools_info` calls `ResearchToolkit.get_tools_ui_info` on every call (`tools_info_service.py:65`). So T3 resolves `customer_config.is_feature_enabled("webSearch")` inside `get_tools_info` per call and nothing is cached. T3's test proves it by toggling webSearch between two calls.

---

### T1: Availability predicate
**Files:** create `src/codemie/configs/research_tool_availability.py`; test `tests/codemie/configs/test_research_tool_availability.py`.
Add `research_tool_available(tool_name: str, cfg, web_search_enabled: bool) -> bool` in the style of `code_executor_availability.py:22-34`: `not cfg.STANDALONE` -> True; Wikipedia / Web Scrapper -> True; Google Search/Places/Places Find Near -> `web_search_enabled and bool(cfg.GOOGLE_SEARCH_API_KEY)`; Tavily -> `web_search_enabled and bool(cfg.TAVILY_API_KEY)`. Compare against the `*_TOOL.name` constants in `codemie_tools/research/tools_vars.py`. Plain truthiness, no strip (matches `toolkit.py:75-77`).
**Test-first: yes — parametrized test over standalone on/off, webSearch on/off, key empty/set/whitespace for each tool name fails with ImportError until the module exists.**

### T2: Filter on ResearchToolkitUI
**Files:** modify `src/codemie_tools/research/toolkit.py:49-71`; test `tests/codemie_tools/research/test_toolkit.py`.
Add `ResearchToolkitUI.offered(web_search_enabled: bool)` classmethod returning a fresh instance whose `tools` is a list comprehension filtered by `research_tool_available` (never mutate the class default; lazy `config` import with `# noqa: PLC0415`, as `file_system/toolkit.py:99-100`). Give `ResearchToolkit.get_tools_ui_info` keyword args `offered_only=False, web_search_enabled=False`; default path stays `ResearchToolkitUI().model_dump()`.
**Test-first: yes — with STANDALONE patched on and empty keys, `get_tools_ui_info(offered_only=True, web_search_enabled=True)` lists only Wikipedia and Web Scrapper; default call still returns 6 tools (existing test at `:41-44`); class-level default list stays length 6 after a filtered call.**

### T3: Opt-in `offered_only` on `get_tools_info`, offering consumers pass it
**Files:** modify `src/codemie/service/tools/tools_info_service.py:38-69`, `rest_api/routers/tool.py:38`, `rest_api/routers/assistant/toolkits.py:56`, `rest_api/routers/assistant/catalog.py:219`, `service/workflow_generator_service.py:63,174`, `service/skill_generator_service.py:119`, `service/assistant_generator_service.py:892`; tests `tests/codemie/service/tools/test_tools_info_service.py`.
Add `offered_only: bool = False` next to `exclude_toolkits`. When true, resolve `customer_config.is_feature_enabled("webSearch")` in the call (function-local import) and pass both to `ResearchToolkit.get_tools_ui_info`. Pass `offered_only=True` at the listed offering call sites only. Existing router/generator tests that assert call args may need the new kwarg.
**Test-first: yes — standalone, no keys: `get_tools_info()` still contains Google Search and Tavily (AC12 lookup path), `get_tools_info(offered_only=True)` omits them; toggling the mocked webSearch between two `offered_only=True` calls changes the result (no staleness).**

### T4: Prebuilt researcher template
**Files:** modify `src/codemie/rest_api/models/prebuilt_assistants.py:319`; test `tests/codemie/rest_api/models/test_prebuilt_assistants.py`.
Replace `ResearchToolkitUI()` with `ResearchToolkitUI.offered(customer_config.is_feature_enabled("webSearch"))` (resolved inside `prebuilt_assistants()`, per request).
**Test-first: yes — standalone, no Google key: researcher template toolkit has no Google tools (AC11); standalone off still lists all six.**

### T5: Dynamic web search follows availability
**Files:** modify `src/codemie/service/tools/toolkit_service.py:271-289`; test `tests/codemie/service/tools/test_toolkit_web_search_flag.py`.
Keep the existing gate and `has_toolkit`/`has_any_tool` conditions. Build the tool list from `[GOOGLE_SEARCH_RESULTS_TOOL, TAVILY_SEARCH_TOOL, WEB_SCRAPPER_TOOL]` keeping those where `research_tool_available(t.name, config, enable_web_search)` holds; Web Scrapper is always kept, so the toolkit is still added with Web Scrapper only when no key is set. Non-standalone yields the same three tools as today.
**Test-first: yes — standalone, only Tavily key: added toolkit has Tavily + Web Scrapper, no Google Search; no keys: Web Scrapper only; both keys: all three (AC9); standalone off: all three regardless of keys (AC1).**
