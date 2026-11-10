# Technical Research

**Task**: research toolkit web-search standalone
**Generated**: 2026-11-02
**Research path**: codegraph

---

## 1. Original Context

Ticket EPMCDME-15701 "OSS standalone: Hide nonfunctional Google Search, Google Places, Google Places Find Near, Wikipedia, and Tavily Search tools". Full ticket body (context, story, 10 acceptance criteria, out of scope, decisions D1-D6) is verbatim in docs/superpowers/tasks/2026-11-02-epmcdme-15701-hide-nonfunctional-search-tools/ticket.md. Summary: in standalone mode (STANDALONE flag from EPMCDME-16473, single gate; see recent commits for how EPMCDME-15706 hid Code Executor), the Research toolkit's Available Tools list must be filtered in the backend: Google Search/Places/Places Find Near need GOOGLE_SEARCH_API_KEY, Tavily needs TAVILY_API_KEY, all four hidden when customer-config features:webSearch is off; Wikipedia and Web Scrapper always listed; chat's automatic web search must skip tools whose key is missing; no change when standalone is off; no frontend change.

---

## 2. Codebase Findings

### Existing Implementations

Purpose A: the Available Tools list shows only usable Research tools.
- `src/codemie_tools/research/toolkit.py:49-58` `ResearchToolkitUI` — fixed list of 6 tools (Google Search, Places, Places Find Near, Wikipedia, Tavily, Web Scrapper); no config input. Rules: none, always all six.
- `toolkit.py:69-71` `ResearchToolkit.get_tools_ui_info` — returns `ResearchToolkitUI().model_dump()`, no args used. Sole caller: `src/codemie/service/tools/tools_info_service.py:65` (`ToolsInfoService.get_tools_info`).
- `ToolsInfoService.get_tools_info` (`tools_info_service.py:38-94`) — assembles all toolkits, exclusion by toolkit name only (`exclude_toolkits`); no per-tool filter. Consumers listed under "Consumers of Changed Contracts".
- Sibling precedent (EPMCDME-15706, Code Executor): `src/codemie_tools/data_management/file_system/toolkit.py:96-124` — `get_tools_ui_info` removes a tool from `FileSystemToolkitUI().tools` via list comprehension when `_is_code_executor_offered()`; that helper branches `if config.STANDALONE: code_executor_available(config) else <old operator switch>`. Availability predicate lives in `src/codemie/configs/code_executor_availability.py:22-34` (`code_executor_available(cfg)`: non-standalone returns the flag alone; standalone ANDs extra prerequisites; imports are function-local, `# noqa: PLC0415`).
- Standalone gate: `config.STANDALONE: bool = False` (`src/codemie/configs/config.py:591`). Also a separate `is_standalone()` reading env `STANDALONE` in `code_executor_tool.py:70` (runtime tool only). Other uses: `customer_config.py:311,334`, `customer_service`/`customer_config_service.py:171`, `toolkit_service.py:1075`.

Purpose B: tools actually run only with their keys (runtime, already done).
- `toolkit.py:73-81` `ResearchToolkit.get_tools` — Wikipedia and Web Scrapper always; Tavily if `tavily_search_key`; Google Search/Places/Find Near if `google_search_api_key`. Built from `config.GOOGLE_SEARCH_API_KEY` / `GOOGLE_SEARCH_CSE_ID` / `TAVILY_API_KEY` (defaults `""`, `config.py:377-379`) at `toolkit_service.py:188-194` (`ToolSet.RESEARCH` factory in `get_toolkit_methods`). Does not consult `webSearch`.

Purpose C: chat's automatic web search skips unusable tools.
- `ToolkitService._augment_toolkits_with_feature_flags` (`toolkit_service.py:226-318`), web-search block `:271-289`: when `request.enable_web_search is True and customer_config.is_feature_enabled("webSearch")` and no Research toolkit and none of `config.DYNAMIC_WEB_SEARCH_TOOLS` (`config.py:698`: google_search_tool_json, tavily_search_results_json, web_scrapper) already configured, appends a `ToolKit(RESEARCH)` with metadata for Google Search, Tavily, Web Scrapper — unconditional on keys. Request flag: `src/codemie/core/models.py:730` `enable_web_search`. Sibling block `:294-316` (code interpreter) already uses `code_executor_available(config)` — the standalone-aware precedent.
- Downstream, the runtime factory (Purpose B) already drops tools with missing keys, so the augmented toolkit lists tools the factory then does not build.

Purpose D: `features:webSearch` master switch.
- `customer_config.is_feature_enabled` (`src/codemie/configs/customer_config.py:441`) resolves via shared resolver (YAML + DB override + env `CUSTOMER_CONFIG__FEATURES__WEB_SEARCH`); `configured_feature_enabled(key)` (`:340`) returns YAML/DB value ignoring runtime computation. Declaration `WEB_SEARCH` at `src/codemie/service/customer_config_declarations.py:200-211` (description: "Google Search, Tavily Search and Web Scraper tools in chat"). Defaults: `config/customer/customer-config.yaml:151`, `standalone/external-config/customer-config.yaml:149`. Missing feature defaults to disabled.
- Today `webSearch` gates only the dynamic web search block; it does not affect the Available Tools list.

Other touched code:
- `src/codemie/rest_api/models/prebuilt_assistants.py:27,319` — builds `ResearchToolkitUI()` directly (not via `get_tools_ui_info`), so prebuilt template assistant gets all six tools regardless of any filter in `get_tools_ui_info` (relevant to D5).
- `src/codemie_tools/research/tools_vars.py` — tool metadata constants (names/descriptions) used by both lists.

### Decisions Already in the Code
- Convention for standalone hiding: branch on `config.STANDALONE` inside the toolkit's `get_tools_ui_info` and filter `toolkit.tools` by `tool.name` (`file_system/toolkit.py:102-115`); non-standalone path left byte-identical. Availability predicate placed in `codemie/configs/*_availability.py` taking `cfg`, tests monkeypatch it.
- Static tool facts: `ToolsInfoService` NOTE (`tools_info_service.py:35`) says tool facts belong in `codemie_tools/base/tool_registry.py`; no registry is required for this.
- Key check is truthiness of the string (`if self.research_config.tavily_search_key`, `toolkit.py:75,77`), so "absent or empty" is already treated alike. No whitespace stripping.
- `ResearchToolkit.get_tools_ui_info(cls, *args, **kwargs)` ignores all args; stored static, needed config-dependent (the usual alternative is the file_system pattern of instance filtering after construction).
- Layering: `codemie_tools` package (`ResearchToolkit`) already imports `codemie.configs.logger`; file_system toolkit imports `codemie.configs.config` lazily inside methods.
- `webSearch` read in `toolkit_service` via function-local `from codemie.configs.customer_config import customer_config` (`:252`).

### Task Statements Checked Against the Code
- "Research toolkit already applies the key rules when an assistant runs" — holds (`toolkit.py:73-81`).
- "Only the Available Tools list and chat's automatic web search ignore them" — holds for those two; additionally `prebuilt_assistants.py:319` also ignores them (via `ResearchToolkitUI()` direct), which D5 covers only if that path follows the filtered list.
- "webSearch on by default, controls chat's automatic web search, adds Google Search and Tavily regardless of keys" — holds (`toolkit_service.py:273-289`; YAML default `config/customer/customer-config.yaml:151`, standalone YAML `:149` not read for its value here).
- "No frontend tool-specific filtering exists; renders what backend returns" — unverified (frontend repo not in reach; backend `get_tools_info` has no per-tool filter).
- "STANDALONE flag from EPMCDME-16473 landed, single gate" — holds: `config.STANDALONE` exists (`config.py:591`), commit 123b427c7. Ticket says "not yet merged"; code shows it merged.
- "Wikipedia never hidden" — holds in `get_tools` (always built). Ticket title lists Wikipedia as hidden; body says never hidden (inconsistent).
- D4 "rules do not apply outside standalone" — keeps in place `ResearchToolkitUI` fixed list and the unconditional dynamic block for non-standalone; consumers listed below.
- Ticket mentions the Research toolkit "already" filters; the dynamic block's `has_any_tool(DYNAMIC_WEB_SEARCH_TOOLS)` check means a user-configured Research toolkit blocks the dynamic add (so ticket's "assistants with hidden tool keep their config" fits).

### Consumers of Changed Contracts
Contract 1: `ResearchToolkit.get_tools_ui_info()` / its tool list shape.
- `tools_info_service.py:65` (only direct caller). Callers of `get_tools_info`: `rest_api/routers/tool.py:38`, `rest_api/routers/assistant/toolkits.py:56` (show_for_ui), `rest_api/routers/assistant/catalog.py:219`, `workflows/validation/resources.py:802`, `service/assistant_generator_service.py:892`, `service/workflow_generator_service.py:63,174`, `service/skill_generator_service.py:119`, `service/tools/tool_service.py:294`, `service/tools/toolkit_lookup_service.py:69`, `service/assistant_service.py:121` (used by alembic migration `1935354d6b50` at `:266`). The generators (assistant/workflow/skill) feed the list to an LLM, so a filtered list also changes what they propose; workflow validation (`resources.py:802`) validates saved tool names against this list, so a hidden tool name in a saved workflow/assistant may resolve as unknown (not verified, see Risks).
- `prebuilt_assistants.py:319` — `ResearchToolkitUI()` direct, unaffected by a change inside `get_tools_ui_info`.
- Tests: `tests/codemie_tools/research/test_toolkit.py:41-44` asserts 6 tools from `get_tools_ui_info()` with no standalone patch (non-standalone default, remains valid); `tests/codemie/service/tools/test_tools_info_service.py`, `test_tools_info_script_callable.py`.

Contract 2: dynamic web-search toolkit composition in `_augment_toolkits_with_feature_flags`.
- Only caller path is `ToolkitService` toolkit assembly; tests `tests/codemie/service/tools/test_toolkit_web_search_flag.py` (adds on flag, override removes), `test_toolkit_service.py`. `config.DYNAMIC_WEB_SEARCH_TOOLS` (`config.py:698`) is read only in this block.

### Architecture and Layers Affected
- Tool package layer: `codemie_tools/research/toolkit.py` (UI list + runtime factory).
- Service layer: `ToolsInfoService`, `ToolkitService`.
- Config layer: `codemie/configs/config.py` (STANDALONE, API keys), `customer_config` (webSearch), `configs/*_availability.py` precedent.
- REST routers consume `get_tools_info` unchanged.

### Integration Points
- `get_tools_info` -> routers, generators, workflow validation (above). `ToolkitService` -> `ResearchToolkit.get_toolkit`. `customer_config.is_feature_enabled("webSearch")` already imported in both toolkit_service blocks.

### Patterns and Conventions
- Standalone branch inside toolkit `get_tools_ui_info` + `config.STANDALONE` (not `is_standalone()` env helper, which is local to code executor tool).
- Function-local imports of `codemie.configs.*` in `codemie_tools` code with `# noqa: PLC0415`.
- Tool filtering by `tool.name` against `*_TOOL.name` constants from `tools_vars.py`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/agents/agent-tools.md`, `tool-overview.md`, `development/configuration-patterns.md` exist (not read in detail). Prior task analyses reference the webSearch gating: `docs/superpowers/tasks/2026-09-03-epmcdme-14705-shared-component-resolver/spec.md` (pilot on `features:webSearch`), `docs/superpowers/tasks/2026-09-07-epmcdme-14659-hide-elastic-retrieval-features/technical-analysis.md:108` (gating shape).

### Architectural Decisions
- Resolver spec (14705): `webSearch` resolved via shared component resolver with DB/env override. `ticket.md` D1-D6.

### Derived Conventions
- See Patterns above; the 15706 commit (774127535) is the template for standalone hiding.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie_tools/research/test_toolkit.py` — config, 6-tool UI count, `get_tools` with only Tavily key (3 tools), Tavily/Google/Wikipedia/Webscrapper factories.
- `tests/codemie/service/tools/test_toolkit_web_search_flag.py` — dynamic add / override removal.
- `tests/codemie/service/tools/test_toolkit_service.py`, `test_tools_info_service.py`, `tests/codemie/rest_api/models/test_prebuilt_assistants.py`.
- `tests/codemie/configs/test_config_code_executor_available.py` — precedent for standalone predicate tests.

### Testing Framework and Patterns
- pytest, unittest.mock `patch`; fixtures in-file.

### Coverage Gaps
- No test of `get_tools_ui_info` under STANDALONE; no test that dynamic web search varies by keys; no test for `webSearch` off in the Available Tools list.

---

## 5. Configuration and Environment

### Environment Variables
- `GOOGLE_SEARCH_API_KEY`, `GOOGLE_SEARCH_CSE_ID`, `TAVILY_API_KEY` (`config.py:377-379`, default empty; `deploy-templates/values.yaml:62-72`), `STANDALONE` (`config.py:591`), `CUSTOMER_CONFIG__FEATURES__WEB_SEARCH` (override of `features:webSearch`).

### Configuration Files
- `config/customer/customer-config.yaml:151`, `standalone/external-config/customer-config.yaml:149` (`features:webSearch`).

### Feature Flags and Deployment Concerns
- `webSearch` is runtime-overridable (DB) so list content can change without restart; `ToolsInfoService` deep-copies a cached toolkit list per call but `ResearchToolkit.get_tools_ui_info()` is evaluated per call.

---

## 6. Risk Indicators

- Two separate places ignore keys (Available Tools list `toolkit.py:69`, dynamic web search `toolkit_service.py:281-289`) plus a third that ignores them silently: `prebuilt_assistants.py:319` bypasses `get_tools_ui_info` (D5 touches it).
- Existing mechanism kept in place by D4: fixed `ResearchToolkitUI` list and unconditional dynamic block for non-standalone; consumers under Consumers of Changed Contracts, Contract 1/2.
- `get_tools_info` has ~10 consumers incl. LLM generators and workflow validation; hiding a tool name that saved assistants/workflows reference may surface in `workflows/validation/resources.py:802` (out of scope per ticket: "keep saved configuration") -- unverified behaviour.
- Ticket inconsistencies: title lists Wikipedia as hidden vs body "never hidden"; ticket says STANDALONE "not yet merged" but flag exists.
- Two standalone helpers exist (`config.STANDALONE` vs `is_standalone()` env); `customer_config.is_feature_enabled` vs `configured_feature_enabled` differ in override handling (resolver vs YAML/DB only).
- No existing tests for standalone behaviour of Research toolkit.
- Speculative: the change may need a key-aware predicate shared by the UI list and the dynamic block to avoid divergence; `ResearchToolkitUI` tools list is a class-level default, so filtering must copy (as `file_system/toolkit.py:112-114` does on a fresh instance).

---

## 7. Summary for Complexity Assessment

The change sits in the backend tool layer and service layer: `codemie_tools/research/toolkit.py` (Available Tools list) and `ToolkitService._augment_toolkits_with_feature_flags` (chat's automatic web search), with optional contact at `prebuilt_assistants.py:319`. The key rules and the `webSearch` resolver already exist; the runtime factory already applies the key rules. A direct precedent (EPMCDME-15706, `file_system/toolkit.py` + `code_executor_availability.py`, and the sibling code interpreter block in the same augment function) shows the standalone branching shape. Estimated surface is a small number of source files and their tests.

Technical novelty is low: standalone gate, tool-name filtering and `is_feature_enabled` are all present. Test posture is moderate: existing tests cover the unfiltered 6-tool list and the dynamic add on the flag, but nothing covers standalone or key-dependent behaviour; precedent tests exist for the standalone predicate.

Key risk factors: shared consumers of `get_tools_info` (generators, workflow validation), the prebuilt assistant bypass, and the non-standalone no-change requirement (AC1) on tests that assert six tools.

---

## 8. External References

None named by the task (ticket.md is in-repo and was read; attachments listed in it were not present in the run directory).

## Facts Added by Review

Researched for the names the spec relies on, after the analysis above was written. Read like the facts layer; where a line here contradicts one above, this one was read later.

- `validate_workflow_config_resources_availability` in src/codemie/workflows/validation/resources.py:749 runs `_validate_tools_avaiability` (:698) and `_validate_tools_from_assistants_availability` (:644); line :802 calls `ToolsInfoService.get_tools_info(user=user)` only to enrich error metadata (`_find_tool_meta` :312) so hiding Research tools there only makes `meta` None.
- The real availability check is `_is_tool_available` in src/codemie/workflows/validation/resources.py:370, which calls `ToolsService.find_toolkit_for_tool` (src/codemie/service/tools/tool_service.py:236) over `_get_available_toolkits` (tool_service.py:294) = `ToolsInfoService.get_tools_info(user=user)`.
- If the filter lives inside `ResearchToolkit.get_tools_ui_info` (added unconditionally at src/codemie/service/tools/tools_info_service.py:63-69), a saved workflow or assistant referencing a hidden Research tool fails validation with TOOL_NOT_FOUND_ERROR (resources.py:370), contradicting the out-of-scope line "assistants that already have a hidden tool keep their saved configuration".
- `ToolsInfoService.get_tools_info` (src/codemie/service/tools/tools_info_service.py:38) is consumed by tool_service.py:294, assistant_service.py:121, routers/tool.py:38, routers/assistant/catalog.py:219, routers/assistant/toolkits.py:56, toolkit_lookup_service.py:69, assistant_generator_service.py:892, skill_generator_service.py:119, workflow_generator_service.py:63.
- A second older `validate_workflow_config_resources_availability` exists in src/codemie/workflows/config_resources_validation.py:254; which one workflow.py:279 resolves to was not checked.
