# Technical Research

**Task**: analytics cache-cost model-pricing
**Generated**: 2026-10-07
**Research path**: filesystem

---

## 1. Original Context

CLI Analytics on codemie backend calculates costs of cache read tokens incorrectly when more than one model is used in a session. It takes only the first model as a base and calculates cache-read token costs based on it, despite other, more expensive models possibly being used later in the session. It should instead calculate cache read token costs per each model actually used in the session. Also, on UI wherever a model name is shown for a session (e.g. session dialog header), it should show the most expensive model if the session contained more than one model.

Verification data is available in a local Postgres instance at localhost:5432, with session id 'c9b8c2a5-268b-42de-adbd-92b17f1ec5f0' where three different models were used in one session. Investigate the relevant tables/schema (connection details should be discoverable from local env/config files in the repo) to understand how session/model/token data is structured, and if possible note the actual column names and table names involved (sessions, model usage, token counts, pricing) relevant to this bug — this will help the planning stage reason about correctness precisely.

---

## 2. Codebase Findings

### Existing Implementations

- `codemie/src/codemie/service/analytics/handlers/cli_analytics_handler.py` — `LocalAnalyticsHandler`. `_price_cache_reads(model_rows)` (lines 163-187) is the only place that prices cache-read tokens **per model**: it iterates `get_model_breakdown` rows (one row per `model_name`, already grouped) and sums `cache_read_cost(model, tokens)` — this path is correct for `get_overview`/`get_cost`/`get_efficiency` KPIs.
- The same file's `get_sessions` (per-row, ~line 545-579), `get_session_detail` (~line 616-716) and `_build_dispatches` instead take **one** `model_name` per session/dispatch and **one** total `cache_read_tokens` figure, then call `cache_read_cost(model, cache_read)` once — pricing the session's *entire* cache-read total at a single model's rate.
- `codemie/src/codemie/service/analytics/handlers/coding_agent_pricing.py` — `cache_read_cost(model, tokens)` and `lookup_cost_config(model)`: pure functions, one model in, one price out; they have no awareness of "a session has several models" and are not themselves the bug.
- `codemie/src/codemie/repository/cli_analytics/postgres/reader.py` — `_TOP_MODEL` (lines 71-74) and `_cost_rows`/`_GRAIN_CALLS` (lines 69-79): a window function picks the single model with the most API calls in the session (`array_agg(...ORDER BY grain_calls DESC, ...)[1]`), used by `get_session_cost_facts`, `get_session_detail_cost`, and `get_users`. This is where the single "model_name" value for a session/user originates; `cache_read_tokens` in the same queries is `sum(...)` across **all** models of the session, so the handler receives one model name plus the combined token total of every model.
- `get_model_breakdown` (reader.py, lines 213-229) is the only reader query that keeps model as a `GROUP BY` key — it has the per-model split the session-level queries lack, but it is aggregated over the whole filtered window, not per individual session.

### Architecture and Layers Affected

- **Repository layer** (`codemie/src/codemie/repository/cli_analytics/postgres/reader.py`): SQL queries `get_session_cost_facts`, `get_session_detail_cost`, `get_users` (via `_TOP_MODEL`) collapse per-model rows to one model + summed tokens before they reach the handler. A per-session, per-model cache-read breakdown is not currently returned by any reader method (only the per-window `get_model_breakdown` has it).
- **Service/handler layer** (`codemie/src/codemie/service/analytics/handlers/cli_analytics_handler.py`): `_price_cache_reads` is model-aware (correct pattern); `get_sessions`/`get_session_detail`/`_build_dispatches` are not — each prices one `(model, cache_read_tokens)` pair per session/dispatch row.
- **Pricing module** (`coding_agent_pricing.py`): single-model, single-token-count function signature; any per-model-breakdown fix will call it once per model instead of once per session.
- **Frontend** (`codemie-ui/src/pages/analytics/components/cli-analytics/views/SessionModal.tsx`): the session dialog header subtitle (`title`/`subtitle` built at lines 33-42) reads `session?.model_name` straight from the API response — whatever single model name the backend returns is shown verbatim; the UI does no selection logic of its own.

### Integration Points

- `cost_daily` (PostgreSQL, schema `codemie_analytics`) is the single source for cost/token facts; it is keyed `(day, session_id, user_email, model_name, query_source)` plus the `a1c1a0000006` extension columns (`speed`, `inference_geo`, `scope_kind`, `scope_name`, `agent_type`), so one session can and typically does have several rows, one set per model (confirmed live, see §8).
- `lookup_cost_config` (coding_agent_pricing.py) resolves pricing from `llm_service.get_all_llm_model_info()` (YAML `config/llms/llm-dial-config.yaml` or the LiteLLM proxy) first, then falls back to `litellm.get_model_info()`.
- No DB migration is implicated: the per-model data already exists in `cost_daily`; the gap is in the reader/handler aggregation, not the schema.

### Patterns and Conventions

- The handler never queries SQL directly; all DB access goes through the `CliAnalyticsReader` port (`codemie/src/codemie/repository/cli_analytics/ports.py`), and the guide (`cli-analytics-storage.md`) states "a new reader query goes into the port and the PostgreSQL reader, with a test" — any fix that needs per-session, per-model rows should add a reader method rather than compute it from existing aggregated rows.
- `_price_cache_reads` is the existing precedent for "iterate model rows, price each, sum" and is the pattern a per-session fix would mirror.
- The single "top model per session" (`_TOP_MODEL`) is a **deliberate, tested, carried-over design choice**, not an oversight: `docs/superpowers/specs/2026-09-23-cli-analytics-postgres-storage-design.md` defect **D4** records that the prior ClickHouse behaviour (`argMax(model, api_call_count)`) picks "the top model" and that it can change nondeterministically after a background merge; the PostgreSQL port (`_TOP_MODEL` plus the `grain_calls` window function) makes that same "most-called model" selection deterministic rather than changing its meaning. `codemie/tests/codemie/repository/cli_analytics/postgres/test_reader.py::test_the_top_model_is_the_largest_row_of_the_original_cost_grain` pins this exact behaviour for `get_users`, `get_session_cost_facts`, and `get_session_detail_cost`. The task's request to show "the most expensive model" instead is a deliberate change to this existing, documented, tested behaviour — not a bug fix to it.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `codemie/.ai-run/guides/integration/cli-analytics-storage.md` — authoritative on table layout, `cost_daily` grain, and the rule "a new reader query goes into the port and the PostgreSQL reader, with a test"; also states the API response shape is contractually fixed per design decisions D1/D3/D5/D10/D11.
- `codemie/.ai-run/guides/integration/cli-analytics-events.md` — event contract; relevant background (`model` is normalised per request) but not itself implicated in the bug.
- `docs/superpowers/specs/2026-09-23-cli-analytics-postgres-storage-design.md` — origin of the "top model" concept (D4) and of the "API contract is fixed" rule referenced by the storage guide.

### Architectural Decisions

- D4 (storage design doc): top-model-by-call-count is inherited ClickHouse behaviour, made deterministic in Postgres — directly relevant, since the task now asks for a different selection rule (most expensive) for display purposes.
- No existing ADR covers "price cache reads per model within a session" — this is new ground; the only precedent is `_price_cache_reads`'s per-window-aggregate version.

### Derived Conventions

- SQL query naming/shape convention: one `async def get_*` method per fact, returning the port's row shape; `_q()` helper handles param binding and type coercion (reader.py).
- Handler convention: coerce every row field with `_i`/`_f`/`_s`/`_iso` helpers before building response dicts (cli_analytics_handler.py lines 66-99).

---

## 4. Testing Landscape

### Existing Coverage

- `codemie/tests/codemie/repository/cli_analytics/postgres/test_reader.py::test_the_top_model_is_the_largest_row_of_the_original_cost_grain` — pins the current single-top-model SQL shape for `get_users`, `get_session_cost_facts`, `get_session_detail_cost`; a fix changing this selection must update or replace this test deliberately.
- `codemie/tests/codemie/service/analytics/handlers/test_coding_agent_pricing.py` — unit tests for `cache_read_cost`/`lookup_cost_config` with a single model each time (Bedrock-prefixed and bare names, unknown model, zero tokens).
- `codemie/tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` and `test_cli_analytics_handler_ordering.py` — handler tests mock the reader; existing session-detail/session-list tests use a single model per session fixture (`BASE_COST_ROW`, `"model_name": "claude"`), so there is no fixture today exercising a multi-model session.

### Testing Framework and Patterns

- `pytest` with `pytest.mark.asyncio`; reader tests use a `_SqlRecorder` fake connection to assert generated SQL text and bound params without a live DB; handler tests use `AsyncMock` on `CliAnalyticsReader`. No test in this suite touches a live PostgreSQL instance (per the guide: "They need no database").

### Coverage Gaps

- No reader or handler test exercises a session with **more than one model** priced for cache reads — the exact scenario the task describes is untested today.
- No test asserts which model a session-level or dispatch-level response should display when models differ in cost.
- `_build_dispatches` (per-dispatch cache-read pricing) has the same single-model assumption as session-level pricing and is likewise untested for multi-model dispatches.

---

## 5. Configuration and Environment

### Environment Variables

- `CLI_ANALYTICS_PG_URL` (empty by default — uses the application database); `CLI_ANALYTICS_PG_SCHEMA` (default `codemie_analytics`); other `CLI_ANALYTICS_PG_*` tuning vars are documented in the storage guide. None of these affect pricing logic; they only affect where/how the data is read.
- `FEATURE_CLI_ANALYTICS=true` is set in `codemie/.env`, enabling the CLI Analytics feature locally.

### Configuration Files

- `codemie/config/llms/llm-dial-config.yaml` — defines `base_name`/`deployment_name`/`cost` per model (e.g. `claude-4-5-haiku` → `us.anthropic.claude-haiku-4-5-20251001-v1:0`); this is the primary source `lookup_cost_config` resolves against.
- `codemie/litellm_config.yaml` — LiteLLM proxy config, used when `LLM_PROXY_ENABLED` is true (not confirmed either way locally).

### Feature Flags and Deployment Concerns

- No feature flag specific to cache-read pricing; `FEATURE_CLI_ANALYTICS` gates the whole CLI Analytics surface.
- No migration required for a reader/handler-only fix — `cost_daily` already carries per-model rows.

---

## 6. Risk Indicators

- Changing the session-level `model_name` selection rule (most-expensive vs. most-called) touches a path with an existing, named, tested defect precedent (D4) and a reader test that currently pins the opposite behaviour — the plan must decide whether to change that query's semantics or add a separate "display model" alongside the existing "top model by calls" used elsewhere (e.g. `_dead_sessions`, user `top_model`).
- Speculative: pricing cache reads per model within a session will likely require a new reader method that returns per-session, per-model `cache_read_tokens` (something `get_model_breakdown` does per window but no method does per session) — this is a design decision for the plan, not a confirmed requirement.
- Live data shows raw Bedrock model names reaching `cost_daily.model_name` unnormalised beyond the `anthropic.`/`us.anthropic.` prefix strip (e.g. `anthropic.claude-haiku-4-5-20251001-v1:0`, normalized to `claude-haiku-4-5-20251001-v1:0`), which may or may not match any `deployment_name` in `llm-dial-config.yaml` (the configured deployment name there carries a `us.` prefix the stored value lacks). If it fails to resolve, `cache_read_cost` silently returns 0.0 for that model's share, independent of the per-model-vs-top-model bug — worth the plan double-checking against the live DB (see §8) rather than assuming all three models in the verification session resolve to a non-zero cache-read rate.
- No existing test fixture covers a multi-model session; any fix needs new fixtures (handler-level `AsyncMock` rows and/or reader-level `_SqlRecorder` SQL assertions) built around the shape confirmed in §8.
- `_build_dispatches` has the same single-model-price-the-whole-total pattern as session-level pricing; the task only mentions "session" cache-read cost and "session dialog header" model name, so dispatch-level pricing may be out of scope — flagging so the plan can decide explicitly rather than by omission.

---

## 7. Summary for Complexity Assessment

The fix spans two layers already identified precisely: the PostgreSQL reader (`get_session_cost_facts`, `get_session_detail_cost`, and the `_TOP_MODEL`/`_GRAIN_CALLS` window-function machinery in `reader.py`) and the handler (`get_sessions`, `get_session_detail`, and the `_price_cache_reads` helper in `cli_analytics_handler.py`), plus one frontend file (`SessionModal.tsx`) that merely renders whatever `model_name` the API returns. The correct per-model pricing pattern already exists in the codebase (`_price_cache_reads` against `get_model_breakdown`'s per-model rows) and can likely be adapted rather than invented from scratch, but no reader method today returns a per-session, per-model cache-read breakdown — session-level queries intentionally collapse to one "top model by call count," a behaviour from a named, tested, documented defect (D4) that the task is now asking to partially reverse for display purposes (show the most expensive model, not the most-called one) while fixing pricing to use every model's own rate.

Technical novelty is moderate: the data already exists in `cost_daily` (one row per model per session, confirmed live against the named verification session), so no migration is needed, but a new reader query/shape is probably required, and the choice of "most expensive" needs its own tie-break/aggregation rule mirroring the existing deterministic tie-break pattern used for `_TOP_MODEL`. Test coverage for the exact scenario (session with several models, differing cache-read costs) is absent in both the reader and handler suites, and the one existing test that encodes the current single-top-model query shape (`test_the_top_model_is_the_largest_row_of_the_original_cost_grain`) will need deliberate reconciliation, not silent breakage. Key risk factors: a possible separate, unconfirmed model-name-resolution gap for Bedrock-raw names reaching `cost_daily` (could mask or inflate the bug's visible effect in the live verification session), and an unresolved scope question — whether per-dispatch cache-read pricing (`_build_dispatches`) should receive the same fix as session-level pricing.

---

## 8. External References

The task names a local Postgres instance (localhost:5432, session id `c9b8c2a5-268b-42de-adbd-92b17f1ec5f0`) as verification data and asks for connection details to be discovered from local env/config files; both were found and the instance was queried directly.

- **Connection**: `codemie/docker-compose.yaml` defines the `postgres` service — `postgresql://postgres:password@postgres:5432/postgres` (host-mapped to `localhost:5432`), image `pgvector/pgvector:pg17`. `codemie/.env` sets no `CLI_ANALYTICS_PG_URL` override, so CLI Analytics uses this same application database, schema `codemie_analytics` (the default, per `cli-analytics-storage.md`). Verified live: `docker exec postgres psql -U postgres -d postgres` shows schemas `codemie`, `codemie_analytics`, `public`.
- **Tables involved**: `codemie_analytics.cost_daily` (cost/token facts, one row per `(day, session_id, user_email, model_name, query_source)`), `codemie_analytics.session_dims` (session identity/dimensions), `codemie_analytics.usage_requests` (raw per-request facts; empty for this session, meaning its data arrived via OTel log events, not the CLI plugin's usage-request event).
- **Live query result** for the named session (`SELECT model_name, day, cost_usd, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, api_call_count, query_source FROM cost_daily WHERE session_id = 'c9b8c2a5-268b-42de-adbd-92b17f1ec5f0'`) returned 8 rows across exactly **three models**, confirming the scenario:

  | model_name | query_source | cost_usd | cache_read_tokens | api_call_count |
  |---|---|---|---|---|
  | `anthropic.claude-haiku-4-5-20251001-v1:0` | generate_session_title | 0.000895 | 0 | 1 |
  | `anthropic.claude-haiku-4-5-20251001-v1:0` | prompt_suggestion | 0.0292776 | 23,416 | 1 |
  | `anthropic.claude-haiku-4-5-20251001-v1:0` | repl_main_thread | 0.0208454 | 66,294 | 2 |
  | `claude-opus-5` | away_summary | 0.0281075 | 52,425 | 1 |
  | `claude-opus-5` | prompt_suggestion | 0.0405735 | 52,047 | 1 |
  | `claude-opus-5` | repl_main_thread | 0.555937 | 50,444 | 2 |
  | `claude-sonnet-5` | prompt_suggestion | 0.0140146 | 51,333 | 1 |
  | `claude-sonnet-5` | repl_main_thread | 0.1050374 | 82,707 | 2 |

  Session totals: `cost_usd` ≈ $0.7947, `cache_read_tokens` = 378,666 across the three models. Per-model `repl_main_thread` call counts tie at 2 each (haiku, opus, sonnet), so `_TOP_MODEL`'s tie-break (`model_name COLLATE "C"` ascending) picks `anthropic.claude-haiku-4-5-20251001-v1:0` — alphabetically first, and the **cheapest** of the three by cost (its own rows sum to ~$0.051 of the session's ~$0.79 total) — demonstrating concretely that today's session-level `cache_read_cost(top_model, total_cache_read_tokens)` prices all 378,666 cache-read tokens at the cheapest model's rate, understating the true per-model cache-read cost, and that the "most expensive model" for display (`claude-opus-5`, cost ≈ $0.6246 across its three rows) differs from the "top model by calls" currently shown.
  `session_dims` for this session: `started_at` 2026-10-07 12:48:56 UTC, `developer_name` `admin@codemie.ai`, `repository` `C:\Projects\codemie-dev`, no `branch`.
