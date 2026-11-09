# Technical Research

**Task**: cli-analytics unpriced_models cost lookup_cost_config (EPMCDME-16522)
**Generated**: 2026-11-05
**Research path**: filesystem

---

## 1. Original Context

Summary: [CLI Analytics] Unpriced models are not identified: a model with zero rates is not listed in unpriced_models, and the UI never marks an unpriced model (any agent)

Found while verifying EPMCDME-16457.
Precondition: managed session through codemie-copilot (model gpt-5.5-2026-04-24) and desktop app session (model mai-code-1.1-flash). The pricing lookup depends on the backend's model catalog: the local stack has no gpt-5.5-2026-04-24 entry, so it falls through to the LiteLLM library. On an environment whose catalog prices gpt-5.5 the first part may not reproduce with this model, but any model the library knows with zero rates does.
Description: unpriced_models (session and list metadata) lists a model only when lookup_cost_config(model) returns None (cli_analytics_handler.py ~362-403). For gpt-5.5-2026-04-24 the lookup does not return None: the LiteLLM library fallback (coding_agent_pricing.py, lookup_cost_config step 2) returns CostConfig(input=0.0, output=0.0, cache_read_input_token_cost=None, cache_creation_input_token_cost=None). The handler treats that as priced, so the cache-read cost is 0 and the model is never reported as unpriced.
Steps: 1. codemie-copilot (model gpt-5.5-2026-04-24), one prompt with tool calls, /exit (session b9fa19b1-8adc-4001-ae3c-47414ac992f6). 2. GET /v1/analytics/cli-analytics/sessions/<id> and open the session in the UI.
Actual: cache_read_tokens 40,110; cache_read_cost_usd 0.0; cost_usd 0.0; metadata.unpriced_models: []; UI: COST $0.00, CACHE-READ $0.00, no unpriced indicator. For comparison gpt-6-luna resolves to real rates (cache read 1e-08/token) and gets non-zero cache-read cost.
Expected: Per EPMCDME-16457 AC "Pricing" (unavailable rates clearly identified): a model whose lookup has no usable rate (None, or zero input/output and no cache-read rate) is listed in unpriced_models and the UI marks its cost as unknown instead of a confirmed $0.00.
Notes: Likely pre-existing in lookup_cost_config / the unpriced check, not introduced by !4659. mai-code-1.1-flash (desktop app, session 3428257e-9fdf-428c-b2c3-7bccc043047f) gets None from lookup and IS listed in unpriced_models. Only models the LiteLLM library knows with zero/missing rates slip through. UI: even for a model that IS in unpriced_models (mai-code-1.1-flash), Session Detail shows COST $0.00 / CACHE-READ $0.00 with no unpriced marker. Not Copilot-specific: lookup_cost_config and the unpriced check are shared by all agents, and the UI (codemie-ui main @ a0e8f5cc) has no reference to unpriced_models (grep "unpriced" in src: 0 matches).
Related: EPMCDME-16644 (local analytics cost differences), EPMCDME-16457 (parent story).

---

## 2. Codebase Findings

Backend paths are under `src/codemie/`. Backend HEAD `cdfa7083e` on `EPMCDME-16522_reproduce-bug`. UI checkout is on `94bb766ef`, not `a0e8f5cc`.

### Existing Implementations

**P1: decide whether a model has a usable price, and what it costs.**
- `lookup_cost_config(model)` — `service/analytics/handlers/coding_agent_pricing.py:51-81`. Reads only `llm_service.get_all_llm_model_info()`. Tiers: raw, unwrapped, normalised; per tier `deployment_name` then `base_name`; first matched identity's cost (`_identity_cost` 39-48), else normalised-alias pass (71-80). A matched identity with `cost=None` returns None without falling through. A zero-valued CostConfig is returned unchanged. Takes a model string only; no agent identifier.
- `CatalogPricer` — `coding_agent_pricing.py:96-145`, one instance per request, memoised per model name (`_cost`, 112-115).
  - `is_priced` (117-119): True iff lookup is not None. Zero rates count as priced.
  - `price` (121-145): cost None and any tokens → model added to `unpriced`, returns 0. Otherwise per token kind (input, output, cache_read, cache_5m+unsplit, cache_1h): tokens > 0 and rate `is None` → added to `unpriced` (141-143); rate `0.0` is multiplied in silently.
  - `unpriced` (108-110): sorted list.
- Catalog source: `service/llm_service/llm_service.py:60-77`. LiteLLM proxy models when `catalog_source()=="litellm"` (`config.LLM_PROXY_ENABLED` and loaded proxy models), else YAML `llm_models`. `LLM_PROXY_ENABLED` defaults False (`configs/config.py:717`).
- Proxy cost parse: `enterprise/litellm/models.py:71-86` (`_parse_cost`). Returns None if input or output price key is absent (line 79). Explicit `0` gives a zero CostConfig (`input_cost or 0.0`). Cache keys pass through and stay None when absent.
- `CostConfig` — `configs/llm_config.py:26-34`. `input`, `output` required floats; cache and batch rates Optional. No field marks a rate as unknown.
- Session gate: `service/analytics/handlers/session_money.py:73-86` (`has_priced_model`, `priced_session_ids`) → `cost_available`.
- Aggregation: `session_money.py:94-117` (`price_session_models`). Prices each (session, model) row twice: total (line 100) and cache-read only (line 101-102). Logs a warning when `pricer.unpriced` is non-empty (111-117). `dispatch_money` prices per model row the same way.

Readers of the unpriced and cost fields:
- List: `service/analytics/handlers/cli_analytics_handler.py:929-964` (`CatalogPricer()` 929, `priced_sessions` 930, `cost_available` 957, count-only log 961).
- Detail: `cli_analytics_handler.py:968-1079` (`get_session_detail`). `cost_available` 1060; returns `pricer.unpriced`, `pricer.catalog_source` 1079.
- Events: `cli_analytics_handler.py:1095` (`pricer.price`).
- Count-only log: `cli_analytics_handler.py:780-792`.
- Router: `rest_api/routers/cli_analytics.py:402-410` (`_metadata`, adds `unpriced_models`, `catalog_source`), used by list endpoints (496, 515, 537, 558, 608, 667, 713). Detail metadata built at 745-754, returned as `{"data", "metadata"}`.
- Repository inputs: `repository/cli_analytics/postgres/reader.py:538` (`get_session_model_costs`), `:1068` (`get_session_detail_model_costs`).

**P2: show a reader that a cost is unknown rather than zero.**
- No such marker exists in the backend beyond `cost_available` and the `unpriced_models` metadata.
- UI formatter: `codemie-ui/src/utils/currency.ts:72-77`. `formatCliAnalyticsCost(v)` returns `'$0.00'` for any falsy value.
- Session Detail: `codemie-ui/src/pages/analytics/components/cli-analytics/views/SessionBody.tsx:110` (`cost_usd` via `formatCliAnalyticsCost`), `:112-116` (`cache_read_cost_usd`: formatted when not null, else `'—'`). No branch reads `cost_available`.
- Overview and Efficiency read `cache_read_cost_usd` (`views/OverviewView.tsx:143-147`, `views/EfficiencyView.tsx:71-75`).
- Store: `codemie-ui/src/store/cliAnalytics.ts:281-290` — `fetchSessionDetail` keeps only `data.data`; `metadata` is discarded.
- Types: `codemie-ui/src/types/cliAnalytics.ts:46-66` (`SessionRow`), `:91-101` (`SessionDetail`) — no `cost_available`, `unpriced_models`, `catalog_source`.
- Search: non-test UI `src` has no match for `unpriced`, `cost_available` or `catalog_source`; `git grep -i unpriced a0e8f5cc -- src` is empty. Test-name matches only in unrelated workflow and spend widgets (`WorkflowExecutionInfoPopup.test.tsx:186`, `BarChartWidget.spendQualification.test.tsx`).

### Decisions Already in the Code

- **A price is exact or absent; no fallback.** Docstring `coding_agent_pricing.py:60`. Alternative removed: LiteLLM library fallback (`litellm.get_model_info`), step 2, present at `fa1bfc246`, removed in `699b7c128` (2026-10-27, "Price CLI Analytics money at the catalog", EPMCDME-15542). Recorded reason: commit title only; none found in code.
- **Unpriced is per token kind, and only when used.** `coding_agent_pricing.py:141-143`; test `test_missing_1h_price_marks_unpriced_only_when_1h_tokens_used` (`tests/codemie/service/analytics/handlers/test_coding_agent_pricing.py:386-394`). A `None` rate is missing; `0.0` is a price. Alternative (zero treated as absent): none found.
- **Explicit zero survives parsing; absent does not.** `enterprise/litellm/models.py:79-84`.
- **Data direction.** Token counts are stored per (session, model) row (`reader.py:538`, `:1068`) and priced at read time against the live catalog; no price is stored with the session.
- **`cost_available` is model-level.** True when any model of the session has a catalog entry (`session_money.py:73-76`), independent of per-kind unpriced tokens.

### Task Statements Checked Against the Code

- **"unpriced_models lists a model only when lookup_cost_config returns None (cli_analytics_handler.py ~362-403)"** — contradicted. The list is `CatalogPricer.unpriced`, filled in `price()` at 127-130 (cost None) and 141-143 (`rate is None` with tokens). It is returned at `cli_analytics_handler.py:1079` and `rest_api/routers/cli_analytics.py:752`. A non-None lookup can still yield an unpriced entry. The ~362-403 range is not the unpriced path in this checkout.
- **"LiteLLM library fallback (coding_agent_pricing.py, lookup_cost_config step 2) returns CostConfig(input=0.0, output=0.0, cache_read_input_token_cost=None, ...)"** — contradicted. `coding_agent_pricing.py` has no `litellm` import; step 2 was removed in `699b7c128`. A residual test patches `litellm.get_model_info` (`test_coding_agent_pricing.py:311-316`); no production path calls it.
- **"The handler treats that as priced, so the cache-read cost is 0 and the model is never reported as unpriced"** — contradicted for the CostConfig as described. With `cache_read_input_token_cost=None` and 40,110 cache-read tokens, `price()` adds the model to `unpriced` (`coding_agent_pricing.py:141-143`), and the cache-read pass runs too (`session_money.py:101-102`). Holds for a cache-read rate of `0.0`: no unpriced entry, cost 0. Which variant the ticket saw is unresolved (see Section 4).
- **"The local stack has no gpt-5.5-2026-04-24 entry"** — contradicted in part. `litellm_config.yaml:77-85` defines `gpt-5.5-2026-04-24` (`bedrock_mantle/openai.gpt-5.5`) with no price fields. Whether the proxy prices it is outside this repository.
- **"mai-code-1.1-flash gets None and IS listed"** — unverified. No `mai-code` reference in the backend repo.
- **"UI has no reference to unpriced_models (grep 'unpriced' in src: 0)"** — holds at `a0e8f5cc` (`git grep`) and at `94bb766ef`.
- **"Not Copilot-specific; lookup and unpriced check shared by all agents"** — holds. Both take only a model string (`coding_agent_pricing.py:51`, `:112`).
- **"Likely pre-existing, not introduced by !4659"** — unverified. MR !4659 not inspected. The `rate is None` rule dates from `699b7c128` (`git log -S`).
- **"UI shows COST $0.00, CACHE-READ $0.00, no unpriced indicator"** — holds in code: `formatCliAnalyticsCost(0)` returns `'$0.00'` (`currency.ts:73`); no `cost_available` or unpriced branch in `SessionBody.tsx`.

### Consumers of Changed Contracts

- **`lookup_cost_config` / `is_priced`**: `CatalogPricer._cost` (`coding_agent_pricing.py:112-115`); `has_priced_model`, `priced_session_ids` (`session_money.py:73-86`); `cost_available` at `cli_analytics_handler.py:957, 1060`. Tests: `test_cli_analytics_handler.py:92,105`; `test_live_session_page.py:236`; `test_live_session_subagent_cost.py:159,314,317,405`; `test_live_mixed_model_capture.py:133`; `test_live_costless_sessions.py:121,162-164,192-195`. UI: none.
- **`unpriced` / `unpriced_models`**: `rest_api/routers/cli_analytics.py:409, 496, 515, 537, 558, 608, 667, 713, 752`; `session_money.py:111-117`; `cli_analytics_handler.py:1079`. Tests asserting the value: `test_live_mixed_model_capture.py:135` (expects `[]`); `tests/codemie/rest_api/routers/test_cli_analytics_storage_wiring.py:494, 538, 556` (expects `["", "mystery-model"]`). UI: none; store discards it (`store/cliAnalytics.ts:283-288`).
- **`catalog_source`**: `rest_api/routers/cli_analytics.py:410, 752`; `cli_analytics_handler.py:991, 1079`; `coding_agent_pricing.py:104`; test `test_coding_agent_pricing.py:427-431`. UI: none.
- **`llm_service.get_model_cost` (separate lookup)**: `service/llm_service/llm_service.py:579-589` uses `and model.cost`, object truthiness, so a zero-rate CostConfig passes. Callers include `agents/callbacks/tokens_callback.py:131,312,325,502` and `enterprise/litellm/proxy_router.py:1461,1479`. Not on the analytics unpriced path, but it reads the same catalog entry.

### Architecture and Layers Affected

- Service (analytics handlers): `coding_agent_pricing.py`, `session_money.py`, `cli_analytics_handler.py` (list, detail, events, log).
- Repository read: `repository/cli_analytics/postgres/reader.py:538, 1068`.
- REST router: `rest_api/routers/cli_analytics.py` (`_metadata`, detail envelope).
- Model catalog: `service/llm_service/llm_service.py:60-89`, `configs/llm_config.py:26-34`, `enterprise/litellm/models.py:71-86`.
- UI: `src/store/cliAnalytics.ts`, `src/types/cliAnalytics.ts`, `views/SessionBody.tsx`, `src/utils/currency.ts`.

### Integration Points

- LiteLLM proxy (opt-in via `LLM_PROXY_ENABLED`) supplies per-token prices through `enterprise/litellm/models.py:71-86`. Repo config: `litellm_config.yaml`.
- YAML catalog (default when proxy off): `llm_config.llm_models` cost blocks, e.g. `config/llms/llm-aws-config.yaml:13,26,40,57,69`. Whether a gpt-5.5 cost block exists there was not checked.

### Patterns and Conventions

- Exact-or-absent pricing; no cross-identity fallback (`coding_agent_pricing.py:52-61`).
- Per-kind unpriced tracking, gated on tokens used (`coding_agent_pricing.py:127-143`).
- Count-only logging with no session or developer identifiers (`cli_analytics_handler.py:780-792`).
- Response envelope `{"data", "metadata"}` with `unpriced_models` and `catalog_source` (`rest_api/routers/cli_analytics.py:402-410, 745-754`).

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/cli-analytics-storage.md` and `cli-analytics-events.md` exist; not read in full for this run.
- `.ai-run/guides/integration/llm-providers.md` exists; not read for this run.

### Architectural Decisions

- Pricing semantics are recorded only in the `lookup_cost_config` docstring (`coding_agent_pricing.py:52-61`) and commit `699b7c128`. No ADR found.

### Derived Conventions

- Prices come from the platform catalog only; no price table in code (`coding_agent_pricing.py:60`).
- Tests fake the catalog with `MagicMock` and patch `coding_agent_pricing.llm_service.get_all_llm_model_info` (`test_coding_agent_pricing.py:20-37`).

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/analytics/handlers/test_coding_agent_pricing.py`: `TestBedrockAliasPricing` (88-291, includes partial-rate test at 165), `TestLookupCostConfig` (293-323), `TestCatalogPricer` (346-431). Helper `_make_llm_model` (20-29) gives non-zero rates or `cost=None`; no zero-rate fixture.
- `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py`: costless session and `cost_available` (87-105); count-only unpriced log (150-180); `make_handler` and `_two_model_catalog()` helpers.
- `tests/codemie/repository/cli_analytics/postgres/` (live, DB-backed): `test_live_costless_sessions.py` (`_seed` at 50; list and detail `cost_available` at 159-195, patches `lookup_cost_config` via `_PRICING`); `test_live_session_subagent_cost.py` (patches `lookup_cost_config` at 150-397); `test_live_mixed_model_capture.py:135`; `test_live_session_detail_consistency.py:60`.
- `tests/codemie/rest_api/routers/test_cli_analytics_storage_wiring.py:494-556`: asserts `unpriced_models` and `catalog_source == "litellm"`.
- UI: cli-analytics view tests cover `cache_read_cost_usd` display; no test covers unpriced or `cost_available`.

### Testing Framework and Patterns

- Backend: pytest, `pytest.mark.asyncio`, `unittest.mock` (`patch`, `MagicMock`, `AsyncMock`). Handler tests mock `handler._repo` methods.
- Live tests use a Postgres engine fixture and patch the pricing lookup, not the catalog.
- Test-run commands not verified in this pass; see `.ai-run/guides/testing/testing-patterns.md`.

### Coverage Gaps

- No test passes a zero-input/output `CostConfig` to `lookup_cost_config`, `CatalogPricer.price`, `has_priced_model` or the handler.
- No pricer test for a `None` cache-read rate with cache-read tokens (the None-rate test covers the 1h rate only).
- No UI test for unpriced or `cost_available` rendering.
- `test_coding_agent_pricing.py:311-316` patches a library call nothing uses.

### Reproduction path (static reading; nothing executed)

Unit, no database:
1. Fake entry: `MagicMock(base_name="gpt-5.5-2026-04-24", deployment_name=None, cost=CostConfig(input=0.0, output=0.0, cache_read_input_token_cost=X))`, with X = `None` and then `0.0`.
2. Patch `codemie.service.analytics.handlers.coding_agent_pricing.llm_service.get_all_llm_model_info` to return `[entry]` (pattern at `test_coding_agent_pricing.py:36-37`).
3. `lookup_cost_config("gpt-5.5-2026-04-24")` → not None for both X (ticket's first claim reproduces).
4. `CatalogPricer().price("gpt-5.5-2026-04-24", TokenCounts(cache_read=40110))` then `.unpriced`. Expected from code: X=None → `["gpt-5.5-2026-04-24"]`; X=0.0 → `[]` with cost 0.0. The X=0.0 result is the gap.
5. Session level: `price_session_models([{"session_id": "s", "model_name": "gpt-5.5-2026-04-24", "cache_read_tokens": 40110}])` (`session_money.py:94`) under the same patch; read `.total`, `.cache_read`, `.pricer.unpriced`.

Handler, no database:
6. `make_handler(cost_facts=[BASE_COST_ROW])` with `handler._repo.get_session_model_costs = AsyncMock(return_value=[{"session_id": "s1", "model_name": "gpt-5.5-2026-04-24", "cache_read_tokens": 40110}])` under the same patch; assert `cost_available` and `cost_usd`. For detail, call `get_session_detail` and read the third return value.

Live (Postgres, pattern of `test_live_costless_sessions.py:172-195`):
7. Seed a session for `gpt-5.5-2026-04-24`; patch `coding_agent_pricing.lookup_cost_config` with a `_lookup` returning the zero CostConfig (pattern `test_live_session_subagent_cost.py:150`); call list and detail; assert `cost_available`, `cost_usd`, `unpriced`.

Stack:
8. With `LLM_PROXY_ENABLED=true` and a proxy entry carrying explicit zero input and output prices and no cache keys, `GET /v1/analytics/cli-analytics/sessions/<id>` should return `data.cost_available` true, `data.cost_usd` 0.0, `metadata.unpriced_models` `[]`, `metadata.catalog_source` `"litellm"`. Check `catalog_source` first: with the proxy off, the YAML catalog is used.
9. UI: `fetchSessionDetail` drops `metadata` (`store/cliAnalytics.ts:283-288`); `SessionBody` renders `$0.00` for both cost cells (`currency.ts:73`). The UI symptom follows from code alone.

Unresolved: the ticket's `unpriced_models: []` with cache-read None cannot come from the current path (step 4, X=None). Either the ticket's build predates `699b7c128`, or its catalog entry had cache-read `0.0`. Steps 4 and 8 decide which.

---

## 5. Configuration and Environment

### Environment Variables

- `LLM_PROXY_ENABLED` — selects the catalog source (`configs/config.py:717`, default False; `llm_service.py:73-77`).

### Configuration Files

- `litellm_config.yaml:77-85` — `gpt-5.5-2026-04-24` deployment, no price fields.
- `litellm_config.yaml:121-129` — `gpt-6-luna` deployment, no price fields in repo.
- `config/llms/llm-aws-config.yaml` — YAML catalog cache-read rates for AWS models.

### Feature Flags and Deployment Concerns

- No feature flag gates pricing or unpriced logic. Proxy price data is outside the repository. No secrets handling is involved in this path.

---

## 6. Risk Indicators

- The ticket's mechanism (LiteLLM library fallback at lookup step 2) is absent from `cdfa7083e`; it was removed in `699b7c128`. A fix written against the ticket would change code off the failing path (Section 2).
- The "priced" gate is `is not None` (`coding_agent_pricing.py:119`, `session_money.py:73-76`). Zero rates pass it: cost_available True, cost $0.00, unpriced empty. This is the core gap.
- `CostConfig` cannot mark a rate as unknown (`configs/llm_config.py:26-34`). A proxy entry with explicit `0` and absent cache keys (`enterprise/litellm/models.py:79-84`) looks like a free model. Speculative: any distinction needs a source flag or a different rule for zero, and the spec must choose.
- The unpriced rule fires only when tokens of a kind are used (`coding_agent_pricing.py:141-143`). Speculative: a rule keyed on zero input/output would also change `cost_available` for sessions with only cache-read tokens.
- The UI has no consumer of `unpriced_models`, `catalog_source` or `cost_available`; the store drops metadata (`store/cliAnalytics.ts:283-288`) and the types omit the fields (`types/cliAnalytics.ts:46-101`). Speculative: a visible marker needs an API session field plus store and type changes.
- No zero-rate test fixture exists at any level (Section 4). A fix starts without regression coverage.
- `test_coding_agent_pricing.py:311-316` patches a library call no code uses; it suggests the fallback is live when it is not.
- Unverified: whether the proxy returns zero or absent prices for gpt-5.5 and gpt-6-luna. Speculative: the first-part claim may depend on this one fact.
- Unverified: the "pre-existing, not from !4659" claim; MR !4659 not inspected.

---

## 7. Summary for Complexity Assessment

The backend change surface is narrow and concentrated: the pricing decision sits in `service/analytics/handlers/coding_agent_pricing.py` (lookup 51-81, CatalogPricer 96-145), with aggregation and the `cost_available` gate in `session_money.py` and two handler paths (`cli_analytics_handler.py` list 929-964, detail 1060-1079). The REST envelope already carries `unpriced_models` (`rest_api/routers/cli_analytics.py:402-410, 745-754`). On the UI side, `SessionBody.tsx`, the store and the types do not read `cost_available`, `unpriced_models` or `catalog_source`, so a visible marker is new surface, not a tweak. Layers touched: service, REST response, UI store, types and view.

Technical novelty is low: one gate (`is_priced` and `price()`) already decides `cost_available` and `unpriced`. The open question is semantic: zero as a real price versus zero as unknown, which `CostConfig` cannot express today. Test coverage of the pricer and handler is good for None and Bedrock aliasing but has no zero-rate case at any level, and the UI has no unpriced coverage. Live Postgres tests patch `lookup_cost_config` directly, so they would not catch a zero-rate regression without a new fixture.

Key risks: the ticket's stated root mechanism is stale against this checkout, so the first reproduction step must confirm which CostConfig reaches the handler (cache-read None versus 0.0); the zero-versus-unknown semantics need an explicit decision; and the UI change crosses store, types and view. Section 4 holds the reproduction path.

---

## 8. External References

- Task named no source-of-truth path. Jira EPMCDME-16457 (AC "Pricing") and EPMCDME-16644 are referenced but not in the repository; their wording is unverified here.
- UI commit `a0e8f5cc` exists in `codemie-ui` (`git cat-file -t` → commit). `git grep -i unpriced a0e8f5cc -- src` is empty. Local UI checkout is `94bb766ef`.
- Ticket's "lookup_cost_config step 2" and "cli_analytics_handler.py ~362-403" are stale against this checkout (Section 2).
- Session ids `b9fa19b1-...` and `3428257e-...` are not in the repository; no data was read from them.
