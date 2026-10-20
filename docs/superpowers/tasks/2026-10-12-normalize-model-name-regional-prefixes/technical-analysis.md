# Technical Research

**Task**: cli-analytics model-name normalization aggregation
**Generated**: 2026-10-12
**Research path**: filesystem

---

## 1. Original Context

Bug: CLI Analytics API statistics-by-model endpoints (overview, tools, cost — e.g. /api/v1/analytics/cli-analytics/overview, /tools, /cost) report model_name values that include regional/routing prefixes (e.g. 'converse/us.anthropic.claude-haiku-4-5-20251001-v1:0', 'converse/global.anthropic.claude-haiku-4-5-20251001-v1:0', 'converse/eu.anthropic.claude-haiku-4-5-20251001-v1:0', 'anthropic.claude-haiku-4-5-20251001-v1:0') alongside the plain model id ('claude-haiku-4-5-20251001'). This causes the same underlying model to be split into multiple series in model_breakdown, tokens_by_model, and cost_by_model, fragmenting session_count/total_tokens/cost_usd aggregation and legend entries in the UI dashboard. Jira: https://jiraeu.epam.com/browse/EPMCDME-15523.

Steps to reproduce:
1. Connect Claude Code through the CodeMie proxy.
2. Start a Claude Code session in any repository and send prompts, including one that triggers a background Haiku call (session title generation) and one that triggers a subagent or tool call.
3. Wait for the analytics rollup refresh, then open the CLI Analytics dashboard in the UI.
4. Set the date range to include the session. Look at Cost by model and Tokens by model.

Actual result: The same Haiku model appears as up to three separate series, one per region prefix (converse/us.*, converse/global.*, converse/eu.*), each with its own cost, token count and legend entry, next to claude-sonnet-5-5. Long names truncate with '...' in the legend. In the sample session Haiku is ~19% of tokens, split as 6.8% / 6.1% / 6.1% across the three prefixed variants.

Expected result: One entry per underlying model (e.g. claude-haiku-4-5), with region prefix and 'converse/' routing prefix normalized away so cost and tokens are summed into a single series. Model names should look the same regardless of user profile or CLI version/routing path used to reach the model.

Need to find: where model_name is stored/ingested from CLI analytics events, where model_breakdown/tokens_by_model/cost_by_model aggregation queries group by model_name (likely ClickHouse or Postgres), and whether a model-name normalization utility already exists anywhere in the codebase (routers, services, repositories) that could be reused or needs to be added, plus any existing config/mapping for model name aliases.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/repository/cli_analytics/postgres/reader.py` — `CliAnalyticsPostgresReader.get_model_breakdown(f)` (line 215) is the **single** query backing all three series the task names. It runs `SELECT d.model_name, count(DISTINCT d.session_id) AS session_count, sum(d.cost_usd) AS cost_usd, ... GROUP BY d.model_name HAVING d.model_name <> ''` against `cost_daily`. There is no other `model_breakdown`/`tokens_by_model`/`cost_by_model` query — one grouping point feeds all three endpoints.
- `src/codemie/service/analytics/handlers/cli_analytics_handler.py` — `CliAnalyticsHandler` calls `self._repo.get_model_breakdown(f)` from `get_overview` (line 269, builds `model_breakdown`), `get_cost` (line 363, builds `cost_by_model`), `get_tools` (line 823, builds `tokens_by_model`), and `get_efficiency` (line 870, feeds `_price_cache_reads`). Each call site reshapes the **same unmodified `model_name` string** from the repo rows (`_s(r.get("model_name"))`) with no normalization step anywhere in this file.
- `src/codemie/service/analytics/handlers/coding_agent_pricing.py` — `normalize_model(model)` (line 24) strips only the literal prefixes `"us.anthropic."` and `"anthropic."`. It does **not** strip `"converse/"`, `"global.anthropic."`, `"eu.anthropic."`, `"jp.anthropic."`, or any other region code. It feeds `lookup_cost_config()` / `cache_read_cost()`, used only for **pricing lookups** (`_price_cache_reads`), never for the `model_name` value stored or displayed in `model_breakdown`/`cost_by_model`/`tokens_by_model`.
- `src/codemie/service/analytics/handlers/llm_handler.py` — a **separate, unrelated analytics surface** (Elasticsearch-backed LLM usage analytics, not CLI analytics) already solves the identical symptom with a hardcoded `MODEL_NAME_MAPPING: dict[str, list[str]]` (line 32) that lists every raw variant per canonical name, including exactly `"converse/eu.anthropic.claude-haiku-4-5-20251001-v1:0"`, `"converse/global.anthropic...`, `"converse/us.anthropic...`, `"converse/jp.anthropic...` mapped to canonical `"claude-4-5-haiku-20251001"`. `_combine_model_names(rows)` (line 47) builds a reverse variant→canonical lookup and sums `total_requests` per canonical name. This is a precedent for the exact fragmentation symptom described in the ticket, implemented with a static enumeration rather than prefix stripping — it does not generalize to new region codes or models without a code/config change, and has no test coverage (`grep` for `_combine_model_names`/`MODEL_NAME_MAPPING` under `tests/` found nothing).
- `src/external/llm_pricing_validator.py` — a **third**, independent ad-hoc normalization (`normalize_model_name`, line 104, plus a `normalizations` dict of raw→variant lists, line 138) used only to build LiteLLM pricing lookup keys; unrelated to CLI analytics display.
- `src/codemie/repository/cli_analytics/postgres/rollups.py` — `cost_daily.model_name` is populated (lines 640-648) from `coalesce(r.model_name, '')`, where `r.model_name` is the **raw name as OTel/the client sent it** (`-- OTel name normalised as the plugin does it; model_name keeps the name as OTel sent it`, line 436). `.ai-run/guides/integration/cli-analytics-storage.md` states this explicitly as a documented, intentional design point: *"`model` is normalised ... so a model has one name in every row ... `cost_daily.model_name` keeps the raw OTel name."* The raw/unnormalized value is a deliberate storage choice, not an oversight — the fragmentation bug is in the **read path** (`get_model_breakdown`'s `GROUP BY d.model_name`) and the handler layer that passes it straight through to the API response, not in ingestion.
- `src/codemie/repository/cli_analytics/postgres/ingestor.py` and the `agent.usage.request` event contract (`.ai-run/guides/integration/cli-analytics-events.md`) show the CLI plugin itself sends both `model_raw` (API's own name) and `model` ("normalised, without speed or region suffixes") per request — the plugin-normalized `model` field is used in `session_usage`/`subagent_invocations`/`session_dims.models`, but `cost_daily.model_name` (what the three buggy endpoints read) is populated from OTel data, which can arrive without that normalization (e.g., Bedrock Converse routing through region hosts), explaining why the same model fragments only in these dashboards.

### Architecture and Layers Affected

- **Repository layer** (`src/codemie/repository/cli_analytics/postgres/reader.py`): the `GROUP BY d.model_name` in `get_model_breakdown` is where rows are keyed; a fix could normalize the grouping key here (SQL expression or Python post-aggregation).
- **Service/handler layer** (`src/codemie/service/analytics/handlers/cli_analytics_handler.py`): three call sites (`get_overview`, `get_cost`, `get_tools`) reshape `model_rows` into the three response shapes; a fix could instead normalize in Python after fetching rows, mirroring the `llm_handler.py` precedent, and would need to re-aggregate (sum `session_count`/`cost_usd`/`total_tokens`) across merged variants rather than just relabeling, since `get_model_breakdown` already pre-aggregates per raw `model_name`.
- **Pricing utility** (`src/codemie/service/analytics/handlers/coding_agent_pricing.py`): `normalize_model` is the closest existing reusable function but is scoped to pricing lookups and incomplete for this bug's prefix set.

### Integration Points

- `get_model_breakdown` is consumed by four handler methods (`get_overview`, `get_cost`, `get_tools`, `get_efficiency`); any normalization must keep `session_count` (distinct sessions), `cost_usd`, and `total_tokens` sums correct when merging rows that were previously computed with separate `GROUP BY` keys in SQL (naively merging in Python after a `COUNT(DISTINCT session_id)` is already final cannot double-count sessions, but a session hitting two prefix variants of the same model within the window would currently count toward two separate `session_count` rows and need de-duplication by SQL-level regrouping, not a Python re-key, to stay correct).
- `_price_cache_reads` (`cli_analytics_handler.py` line 230) iterates the same `model_rows` to look up `cache_read_input_token_cost` via `coding_agent_pricing.normalize_model` — currently also mis-prices `global.`/`eu.`/`converse/`-prefixed rows since that function doesn't strip those prefixes either, a related but currently out-of-scope-sounding side effect worth flagging to the ticket owner.

### Patterns and Conventions

- The repository (`reader.py`) is a thin asyncpg wrapper (`self._q(...)`); other `_q` queries show adding a SQL expression (e.g., `regexp_replace`) to the `SELECT`/`GROUP BY` is the established way to reshape a grouping key without touching the handler.
- The handler layer (`cli_analytics_handler.py`) uses small module-level helpers (`_s`, `_i`, `_f`, `_first`, `_pct`) for value coercion — a normalization helper would fit the same style.
- `llm_handler.py`'s `_combine_model_names` is the one in-repo precedent for "merge rows under canonical model name, sum a measure, re-sort" — reusable as a pattern reference, not as code (different repository, different row shape, no distinct-session-count concern).

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/cli-analytics-storage.md` — authoritative source for this domain: documents `cost_daily.model_name` as intentionally the raw OTel name (not normalized), the "Reading the session tables" section's note that `session_dims.models` and `session_usage` use the plugin-normalized `model` field instead, and the full table/column data lineage. No existing section proposes or specifies model-name normalization for the dashboard read path.
- `.ai-run/guides/integration/cli-analytics-events.md` — defines the `agent.usage.request` contract: `model_raw` ("the model as the API names it") vs `model` ("normalised, without speed or region suffixes") are both sent by the client/plugin; neither field is `cost_daily.model_name`'s source for OTel-derived rows.

### Architectural Decisions

- The storage guide explicitly frames the raw-vs-normalized split as deliberate ("Reading the session tables" bullet: *"`model` is normalised ... so a model has one name in every row ... `cost_daily.model_name` keeps the raw OTel name"*), meaning any fix must not silently change `cost_daily.model_name` itself (that would be "Changing a response shape"/storage contract, flagged in the storage guide's "Avoid" table as needing a design doc) — the guide's own "Avoid" row for this area says: *"Changing a response shape → The API contract is fixed ... a new reader query goes into the port and the PostgreSQL reader, with a test."* This points toward normalizing in the reader query or handler, not the stored column.
- No ADR or design doc addresses model-name alias/normalization specifically; `docs/superpowers/specs/2026-09-23-cli-analytics-postgres-storage-design.md` and the handoff doc referenced in the storage guide are the closest related design records but were not found to mention region-prefix handling.

### Derived Conventions

- Both existing ad-hoc normalizers (`coding_agent_pricing.normalize_model`, `llm_pricing_validator.normalize_model_name`) use simple prefix-stripping or static dict lookups rather than regex; a prefix-list or regex approach (`converse/` + `{region}.anthropic.`) would be a departure from, but consistent in spirit with, both.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/analytics/handlers/test_coding_agent_pricing.py` — covers `normalize_model`, `lookup_cost_config`, `cache_read_cost` for the two prefixes it currently handles (`us.anthropic.`, `anthropic.`); no case for `global.`, `eu.`, `jp.`, or `converse/`.
- `tests/codemie/repository/cli_analytics/postgres/test_reader.py` — has a parametrized SQL-shape test covering `get_model_breakdown` among other methods (line 332) and asserts on `_TOP_MODEL`/`_GRAIN` SQL fragments (lines 369-370), but no test exercises actual row data for `model_breakdown` grouping/merging behavior.
- `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` and `test_cli_analytics_handler_ordering.py` — exclusively mock `repo.get_model_breakdown = AsyncMock(return_value=[])`, i.e., every existing test for `get_overview`/`get_cost`/`get_tools`/`get_efficiency` passes with an empty model list and never exercises model-name content, let alone region-prefix fragmentation.

### Testing Framework and Patterns

- `pytest` with `unittest.mock.AsyncMock`/`MagicMock` and module-level `patch` for the pricing config; handler tests build a `MagicMock` repo and assert on returned dict shapes.

### Coverage Gaps

- No test anywhere constructs `model_rows` with multiple prefixed variants of the same model and asserts they collapse to one `model_breakdown`/`cost_by_model`/`tokens_by_model` entry — this is a complete coverage gap for the exact bug described.
- No test covers `coding_agent_pricing.normalize_model` against `global.`, `eu.`, `jp.` region codes or the `converse/` routing prefix.
- No test exists for `llm_handler._combine_model_names`/`MODEL_NAME_MAPPING` either, despite it being the one existing precedent for this symptom class.

---

## 5. Configuration and Environment

### Environment Variables

- No CLI-analytics-specific env var governs model-name normalization. The relevant `CLI_ANALYTICS_*` settings (`CLI_ANALYTICS_PG_URL`, `CLI_ANALYTICS_PG_SCHEMA`, retention/rollup variables) are storage/runtime configuration only (see `.ai-run/guides/integration/cli-analytics-storage.md` "PostgreSQL Settings") and have no bearing on this bug.

### Configuration Files

- No config file (YAML, `.env.example`) holds a model-alias/region-prefix mapping. `llm_handler.MODEL_NAME_MAPPING` and `llm_pricing_validator.normalizations` are both **in-code** dicts, not externalized config.

### Feature Flags and Deployment Concerns

- None found specific to this area. The CLI analytics endpoints are gated by `_ensure_enabled()` / `_admin_gate` in the router (unrelated to this bug).

---

## 6. Risk Indicators

- Speculative: Merging rows at the Python/handler level after `get_model_breakdown` already pre-aggregates `session_count` via `COUNT(DISTINCT session_id)` per raw `model_name` risks double-counting or under-counting sessions that hit more than one prefix variant of the same model in the window — a correct fix likely needs the normalization applied *inside* the SQL `GROUP BY` (reader layer), not as a post-hoc merge of already-aggregated rows.
- Speculative: Three independent, inconsistent model-name normalizers already exist in the codebase (`coding_agent_pricing.normalize_model`, `llm_handler.MODEL_NAME_MAPPING`/`_combine_model_names`, `llm_pricing_validator.normalize_model_name`), none of which fully covers the prefixes in this ticket (`converse/`, `global.`, `eu.`, `jp.` region codes) — introducing a fourth bespoke normalizer in `cli_analytics_handler.py`/`reader.py` would compound this fragmentation of normalization logic itself; whether to centralize is a design decision, not a research finding.
- `cost_daily.model_name` is documented as intentionally storing the raw, unnormalized OTel name; a change that normalizes the stored column (rather than the read/display path) would alter a documented storage contract and the `a1c1a0000006` downgrade path described in the storage guide — out of scope unless explicitly chosen.
- Zero test coverage exists today for model-name content in any of the three affected handler methods (`get_overview`, `get_cost`, `get_tools`) — every current test stubs `get_model_breakdown` to return `[]`, so a regression here would not have been caught and a fix needs new tests, not just a passing existing suite.
- `_price_cache_reads` uses the same incomplete `normalize_model` for pricing lookups on the same `model_rows`; fixing only the display grouping without checking this path risks leaving mispriced `cache_read_cost_usd`/`unpriced` lists for `global.`/`eu.`/`jp.`-prefixed models even after the display bug is fixed.

---

## 7. Summary for Complexity Assessment

The bug lives entirely in the CLI Analytics read path: one SQL query, `CliAnalyticsPostgresReader.get_model_breakdown` in `src/codemie/repository/cli_analytics/postgres/reader.py`, groups `cost_daily` rows by the raw, unnormalized `model_name` column, and that single query backs all three affected response shapes (`model_breakdown` in `/overview`, `cost_by_model` in `/cost`, `tokens_by_model` in `/tools`) via four call sites in `src/codemie/service/analytics/handlers/cli_analytics_handler.py`. `cost_daily.model_name` is documented as deliberately storing the raw OTel-reported name (region/routing prefixes included), so the fix is a read-path normalization, not an ingestion or schema change — this narrows the file-change surface to the reader query and/or handler, plus the one existing (but incomplete) normalizer `coding_agent_pricing.normalize_model`.

Technical novelty is low — the codebase already contains three independent prior attempts at the same class of problem (`coding_agent_pricing.normalize_model` for pricing, `llm_handler.MODEL_NAME_MAPPING`/`_combine_model_names` for a different Elasticsearch-backed analytics surface with the exact `converse/{region}.anthropic...` variants named in this ticket, and `llm_pricing_validator.normalize_model_name` for LiteLLM key resolution). None of the three is reusable as-is for this bug without modification, and choosing between extending one of them, writing a new shared normalizer, or pushing the fix into SQL (to preserve correct `COUNT(DISTINCT session_id)` semantics when merging previously-separate groups) is a design decision for the spec, not something research resolved.

Test coverage is a real gap: every existing test for the affected handler methods stubs `get_model_breakdown` to return an empty list, so none exercises model-name content at all, and the one in-repo precedent (`_combine_model_names`) has no tests either. The main correctness risk flagged for the spec is that `get_model_breakdown` pre-aggregates `session_count`/`cost_usd`/`total_tokens` per raw model name before any Python-side merge could happen — collapsing rows after that aggregation can mis-count sessions that touched multiple prefix variants within one window, favoring a SQL-level (reader-query) fix over a handler-level row-merge.

---

## 8. External References

The task names only a Jira ticket URL (`https://jiraeu.epam.com/browse/EPMCDME-15523`) as the bug tracker reference, not a filesystem path, guide, or document to source content from. No `~/`, repo-external, or in-repo path was named as a source of truth for requirements. None named by the task.
