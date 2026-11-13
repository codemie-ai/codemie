# Technical Research

**Task**: cli analytics session cost pricing
**Generated**: 2026-11-13
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-17138 (Bug): [CLI Analytics] Sessions without model usage incorrectly display "Cost unknown: no price for this model"

Problem: CLI Analytics incorrectly displays a missing-model-pricing warning for sessions that have no model usage.
When a session contains zero model calls, zero tokens and zero cost, the UI shows: "Cost unknown: no price for this model".
This is misleading because the session did not use any model, so there is no missing model price.

Steps to reproduce: Start a Claude Code session using a project the user cannot access; trigger a model request that fails with HTTP 403 before any model usage is recorded; open CLI Analytics → Sessions; inspect estimated cost/tooltip; open Session Details.

Expected: Sessions with no model usage display an accurate indication such as "No model usage" or "$0.00", rather than an unavailable-model-pricing warning. Sessions with actual model usage but missing catalog pricing must retain their unpriced-model warning.

Actual API evidence: model_name = "", api_call_count = 0, all token counts = 0, cost_usd = 0, cost_available = false, unpriced_models = [].

Confirmed root cause:
Backend: CLI Analytics marks cost_available=false when no token usage rows exist (no_token_rows).
UI: SessionCostValue.tsx interprets cost_available=false as missing model pricing and shows the generic warning even when unpriced_models is empty.

Acceptance criteria:
1. Sessions with no model calls or token usage display an accurate no-usage cost state, without a missing-model-pricing warning.
2. Sessions containing actual usage of an unpriced model continue to show the pricing-unavailable state.
3. Sessions with correctly priced model usage retain their existing cost display.
4. Sessions List and Session Details display consistent cost states.
5. Add unit and integration regression tests covering no-usage, unpriced-model and priced-model sessions.

Related: EPMCDME-17139 "Sessions without model usage are ingested and included in analytics" (separate ticket, not in scope).

Note: this repository is the backend (Python/FastAPI). The UI component SessionCostValue.tsx lives in a separate UI repo and is out of scope here; scope this analysis to the backend fix (the cost_available / no_token_rows logic for the sessions list and session details endpoints, and any API field the UI could use to tell no-usage apart from unpriced).

---

## 2. Codebase Findings

### Existing Implementations

**Purpose A: say whether a session's cost is known (per session).**
- `has_priced_model(models, pricer)` — `src/codemie/service/analytics/handlers/session_money.py:75`. Docstring: "the one rule behind `cost_available`". True when any model name `pricer.is_priced(...)`. An empty iterable (no token rows) returns False — this is the no-usage → `cost_available=false` path.
- `priced_session_ids(rows, pricer)` — `session_money.py:81`. Groups (session, model) rows by session and applies `has_priced_model`; a session absent from the rows is never in the set.
- `CatalogPricer.is_priced(model)` — `src/codemie/service/analytics/handlers/coding_agent_pricing.py:238`. Catalog entry with a usable price; a zero-rate entry counts as missing (EPMCDME-16522). Ignores token counts, so an uncatalogued model with all-zero tokens (or the empty model `""`) is "not priced".
- Sessions list: `LocalAnalyticsHandler.get_sessions` — `src/codemie/service/analytics/handlers/cli_analytics_handler.py:~1580-1600`. `priced_sessions = priced_session_ids(session_model_rows, pricer)`; per row `s["cost_available"] = s["trace_id"] in priced_sessions` (line 1597), `cost_usd = money.by_session.get(..., 0.0)`.
- Session detail: `get_session_detail` — `cli_analytics_handler.py:1700`: `"cost_available": has_priced_model((row.model_name for row in model_cost_rows), pricer)`. Same rule, applied to `get_session_detail_model_costs` rows. Detail dict does not set `api_call_count` (model default 0).
- `_log_unavailable_cost` — `cli_analytics_handler.py:1409-1420`. Already distinguishes the two causes: `no_token_rows` (session id not in `model_rows_by_session`) vs `unpriced_models` (has rows, none priced). Logs counts only. This is the only place the two states are separated today; it is not exposed in the API.
- Token rows source: `model_token_groups` / `_token_rows` — `src/codemie/repository/cli_analytics/postgres/cost_key_sql.py:83,107`: `cost_daily` UNION ALL `session_usage` subagent-totals rows, grouped by (session, raw model), "the empty model kept". Read by `get_session_model_costs` (`reader.py:1426`) and `get_session_detail_model_costs` (`reader.py:3009`).
- Costless sessions are listed at all because of `include_costless` on `get_session_cost_facts` and `get_session_detail_meta` keying on `session_dims` (`reader.py:2947` comment: "a session with dimension events but no cost row is listed with cost_available false").

**Purpose B: name the models without a price.**
- Response `metadata.unpriced_models` + `metadata.cost_incomplete` — built in `src/codemie/rest_api/routers/cli_analytics.py:491-503` (`_metadata`, list) and `:985-993` (detail). `cost_incomplete = bool(unpriced)`. Scope: list = page rows' models (window for cost sort / framework filter); detail = that session's models recorded by `CatalogPricer.price` (positive tokens only). There is no per-session `unpriced_models` field on `LocalAnalyticsSessionRow`.
- Field docs in `src/codemie/rest_api/models/cli_analytics.py:55,71` (`LocalAnalyticsResponseMetadata`).

**Purpose C: report usage volume per session** (what a "no usage" signal could rest on): `LocalAnalyticsSessionRow` already serves `api_call_count`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `model_name` (`models/cli_analytics.py:285-330`). List fills `api_call_count` from the cost fact row (`cli_analytics_handler.py:~1459`); detail sums tokens from model rows but leaves `api_call_count` at default 0.

**Other touched code**: `LocalAnalyticsSessionRow.cost_available: bool = True` (`models/cli_analytics.py:329`), inherited by `LocalAnalyticsSessionDetail` (`:408`). No description on the field.

### Decisions Already in the Code
- `cost_available` is a boolean, default True, with one shared rule for list and detail (`has_priced_model`) — introduced in commit `a80c1922c5` (EPMCDME-16441, "list cost-less CLI analytics sessions"). Recorded in `.ai-run/guides/integration/cli-analytics-storage.md:354`: false "when the session has no token rows (cost-less) or only rows of models missing from the catalog". The no-usage → false mapping is a recorded, deliberate choice, not an accident.
- Zero-rate catalog entry = unpriced (EPMCDME-16522; test `test_a_model_with_a_catalog_entry_priced_at_zero_counts_as_unpriced`).
- Priced vs unpriced is decided per model name, not per token count (`is_priced` "whatever the token counts"), while `metadata.unpriced_models` uses positive-token rule (`price()`; guide line 361). So a session with rows of an uncatalogued model whose tokens are all zero gets `cost_available=false` and is not listed in `unpriced_models` — same visible symptom as no rows.
- Convention: list and detail must use the same helper (guide line 354; AC4 restates it).

### Task Statements Checked Against the Code
- "Backend marks cost_available=false when no token usage rows exist (no_token_rows)" — holds: `has_priced_model([])` is False; `no_token_rows` is the log counter name (`cli_analytics_handler.py:1412`).
- "Actual API evidence … unpriced_models = []" — holds for a session with no rows: nothing is priced, so `pricer.unpriced` stays empty. Note `unpriced_models` is response metadata, not a per-session field; on the list it covers the page (or window), so an empty list on a list response depends on the other sessions.
- "UI interprets cost_available=false as missing pricing" — unverified (UI repo outside reach).
- "Sessions with usage of an unpriced model continue to show pricing-unavailable" — keeps `has_priced_model`/`priced_session_ids` in place for that case; covered by `test_unpriced_only_session_is_listed_cost_available_false_...` and `test_get_session_detail_with_only_an_unpriced_model_reports_cost_available_false`.
- "EPMCDME-17139 (ingest of no-usage sessions) not in scope" — keeps `include_costless` listing and `get_session_detail_meta` session_dims key in place.
- "HTTP 403 before any model usage is recorded" yields no token rows — unverified: whether an `is_api_error` request can write a zero-token `cost_daily` row (with `model_name` set) was not traced in the writer; the reported `model_name=""` and `api_call_count=0` indicate no `cost_daily` row in this instance.

### Consumers of Changed Contracts
`cost_available` (searched `cost_available` across `src`, `tests`, `.ai-run`, `docs`):
- Producers: `cli_analytics_handler.py:1597, 1700`; model `models/cli_analytics.py:329`.
- Tests: `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` (lines 146, 157, 193, 208, 524-590, 1243, 1300-1328); `tests/codemie/repository/cli_analytics/postgres/test_live_route_pricing.py:619,639,909` (asserts True only).
- OpenAPI snapshot: `tests/codemie/rest_api/router_guards/baselines/openapi.json:17126,17512` (session row and detail schemas), checked by `test_openapi_snapshot.py`, regenerated by `regenerate_baselines.py`. Any field add/type change shows up here.
- Docs: `.ai-run/guides/integration/cli-analytics-storage.md:354`.
- External: UI `SessionCostValue.tsx` (separate repo).
`has_priced_model` / `priced_session_ids`: used only by the two handler sites above (and `_log_unavailable_cost` consumes `priced_sessions`). `metadata.unpriced_models`/`cost_incomplete`: produced for every CLI analytics endpoint via `_metadata`; per-bucket `unpriced_models` in `funding_buckets.py` is unrelated to per-session state.

### Architecture and Layers Affected
- Service/handler: `LocalAnalyticsHandler.get_sessions`, `_session_row`, `_log_unavailable_cost`, `get_session_detail`; helpers in `session_money.py`.
- API models: `LocalAnalyticsSessionRow` / `LocalAnalyticsSessionDetail` (`rest_api/models/cli_analytics.py`).
- Router: `rest_api/routers/cli_analytics.py` (metadata only; no per-session logic).
- Repository (read-only, unchanged by the symptom): `postgres/reader.py`, `postgres/cost_key_sql.py`.

### Integration Points
Model catalog via `CatalogPricer` (`coding_agent_pricing.py`, reads `llm_service.get_all_llm_model_info`). PostgreSQL CLI analytics storage (`cost_daily`, `session_usage`, `session_dims`). UI consumes the JSON (separate repo).

### Patterns and Conventions
Handler builds plain dicts, router validates through Pydantic response models (`_respond`). Money helpers centralised in `session_money.py`; list and detail share one rule. Fields documented with `Field(description=...)` in newer additions; `None` vs `0` semantics used elsewhere for "unknown vs real zero" (`turns`, `tool_calls_total`, `net_lines`).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/cli-analytics-storage.md:354` — exact current `cost_available` semantics (list and detail, costless sessions, logging).
- `.ai-run/guides/integration/cli-analytics-storage.md:361` — `metadata.unpriced` scope and positive-token rule.
- `.ai-run/guides/integration/cli-analytics-events.md:168-191` — `is_api_error` request field.
- `.ai-run/guides/testing/testing-patterns.md`, `testing-service-patterns.md` — test policy.

### Architectural Decisions
cost-less sessions listed with `cost_available: false` (EPMCDME-16441, commit `a80c1922c5`); zero-rate = unpriced (EPMCDME-16522); `price()` positive-token rule (MR review CR-002/CR-005, guide line 361).

### Derived Conventions
Guide paragraphs carry "*Changed <date> (ticket): …*" notes when behaviour changes.

---

## 4. Testing Landscape

### Existing Coverage
- Unit (`test_cli_analytics_handler.py`): costless list row → `cost_available False` (146); priced → True (157); zero-rate list/detail → False (193, 208); costless log counts (493-534); unpriced-only → False and log `(1,0,1)` (537); mixed priced+unpriced → True (558); detail without model rows → False (1300); detail unpriced-only → False (1310); detail mixed → True (1322).
- Live Postgres (`tests/codemie/repository/cli_analytics/postgres/test_live_*.py`): skipped when the database is unreachable (module docstring); `test_live_route_pricing.py` covers priced detail only.

### Testing Framework and Patterns
pytest + pytest-asyncio; `make_handler(cost_facts=...)` with `AsyncMock` repo methods (`get_session_model_costs`), `_priced_detail_handler([...])`, catalog patches `_two_model_catalog()`, `_zero_rate_catalog()`, `COSTLESS_ROW`, `UNPRICED_MODEL`, `DETAIL_SCOPE`.

### Coverage Gaps
- No test for a session whose model rows exist but all tokens are zero (or model `""`).
- No live/integration test of a costless session through list and detail together (AC4/AC5).
- No router-level test asserting session row cost state + `metadata.unpriced_models` together.

---

## 5. Configuration and Environment

### Environment Variables
None specific to cost state. Live tests need the CLI analytics Postgres reachable (see test module docstrings).

### Configuration Files
Model price catalog comes from LLM model config via `llm_service`.

### Feature Flags and Deployment Concerns
`_ensure_enabled()` gates CLI analytics routes. No migration or flag touches cost state.

---

## 6. Risk Indicators

- Existing tests assert the current behaviour: `test_costless_session_is_listed_with_zero_cost_and_cost_available_false` and `test_get_session_detail_without_model_rows_reports_cost_available_false` encode no-usage → False (see Task Statements).
- `cost_available` is a public contract read by the UI and frozen in `openapi.json` baseline; the guide (`cli-analytics-storage.md:354`) documents no-usage → false.
- Zero-token model rows (model `""` or uncatalogued with all-zero tokens) are "unpriced" by `is_priced` yet absent from `unpriced_models` — the same misleading state can arise even when token rows exist.
- Unverified whether 403/`is_api_error` requests write zero-token `cost_daily` rows.
- Detail does not populate `api_call_count` (always 0), so it is not a list/detail-consistent usage signal today.
- `metadata.unpriced_models` on the list is page/window scoped, not per session; it cannot by itself tell one session's state.
- Speculative: any new or changed field needs the OpenAPI baseline regenerated and the guide paragraph updated.

---

## 7. Summary for Complexity Assessment

The fix sits in the service layer of CLI analytics: `has_priced_model` / `priced_session_ids` in `session_money.py` decide `cost_available` for both the sessions list (`cli_analytics_handler.py:1597`) and session detail (`:1700`), and `_log_unavailable_cost` already distinguishes `no_token_rows` from `unpriced_models` internally. The API shape lives in `LocalAnalyticsSessionRow` (inherited by the detail model) with `cost_available: bool = True`; per-session unpriced model names are not exposed, only response-level `metadata.unpriced_models`/`cost_incomplete`. No repository SQL, migration or config is involved in the current decision.

Novelty is low: the rule is one function and the distinguishing data (presence of token rows, token sums, `api_call_count` on the list) is already in hand. The constraint is contractual: no-usage → `cost_available=false` was a recorded decision (EPMCDME-16441, guide line 354), the UI reads the field, and the OpenAPI snapshot test freezes the schema. Edge case: zero-token model rows are treated as unpriced by name.

Test posture is good at unit level (list and detail cases for costless, unpriced, zero-rate, mixed, priced), two of which assert the behaviour being reported as a bug. Live Postgres tests exist but only for priced sessions; integration coverage of no-usage across list+detail is absent.

---

## 8. External References

None named by the task. (`SessionCostValue.tsx` is mentioned but declared out of scope and lives in a separate UI repository; not read.)
