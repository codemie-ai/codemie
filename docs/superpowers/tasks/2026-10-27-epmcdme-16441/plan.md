# EPMCDME-16441 backend: subagent cost, gate-discard visibility, cost-less sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Price subagent totals into session money at query time, make gate discards visible, list cost-less sessions with `cost_available`.

**Architecture:** Reader-only SQL additions UNIONed into the two model-cost queries (existing pricer unchanged); an opt-in left join in `get_session_cost_facts`; a log line at the two ingestor discard sites (the counter was dropped after review).

**Tech Stack:** Python, asyncpg SQL strings in `reader.py`, FastAPI/pydantic, pytest (live-DB pattern of `tests/codemie/repository/cli_analytics/postgres/test_live_session_duration_fallback.py`).

**Spec:** `docs/superpowers/tasks/2026-10-27-epmcdme-16441/spec.md`. Paths below are under `src/codemie/`; `R` = `repository/cli_analytics/postgres/rollups.py`, `D` = `.../reader.py`, `H` = `service/analytics/handlers/cli_analytics_handler.py`.

## Global Constraints

- Commit per task using the repository's existing convention (`.ai-run/guides/standards/git-workflow.md`).
- `_totals_are_a_source` (R:588-607) and all rollup SQL stay byte-identical; no `cost_daily` change, no backfill.
- No default-model price fallback; unknown model prices 0.0 and is logged unpriced.
- No NULL cost in the response model; `cost_usd` stays a required float.
- No email, project name or raw user id in logs; the hash is a log field only, never a metric label.
- English only in code, comments, docs.

## Plan-time findings (resolved)

- `session_usage` token columns: `model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, cache_creation_5m_tokens, cache_creation_1h_tokens` (R:673-676), same names as `cost_daily`. Totals rows leave 5m/1h NULL (and `cache_creation_tokens` NULL) when the event lacks them (R:634-640), so `sum()` over the UNION stays NULL when no row has a split. Wrap only the four base sums in `coalesce(..., 0)`; never coalesce the 5m/1h sums.
- Callers of `get_session_cost_facts`: H:409 (KPIs), :625 (repositories), :690 (sessions), :1024. Only the sessions list may see cost-less sessions, so the relaxed join is opt-in (`include_costless`); others keep the inner join and KPIs/priced aggregates stay unchanged. The port in `repository/cli_analytics/ports.py:183` needs the new parameter.
- TS/UI unknown-field check: note only, out of scope.

## Review Focus

- Session whose only cost is a totals-only agent: priced cost > 0, `cost_available` true.
- Totals model absent from the catalog: 0.0 plus unpriced warning, no crash.
- Totals with no cache split: 5m/1h stay NULL after the UNION.
- Cost-less session with sort `cost_usd`: no None in sort keys.
- Gate discard with empty HMAC key: no hash field, no exception.

### Task 1: Subagent totals in session money (AC-A, B, C, D)

**Files:**
- Modify: `D:364-384` (`get_session_model_costs`), `D:752-768` (`get_session_detail_model_costs`)
- Test: `tests/codemie/repository/cli_analytics/postgres/test_live_session_subagent_cost.py` (new)

**Interfaces:** Produces: same row shape as today (`session_id, model_name, normalized_model_name, *_tokens`), now including `session_usage` rows with `attrs->>'token_source' = 'subagent_usage'`.

- [ ] **Step 1: Write failing live-DB tests** (fixtures as in the duration-fallback live test): `test_totals_only_agent_adds_priced_cost_to_session_list_and_detail` (A), `test_agent_with_request_rows_is_not_double_counted_after_late_requests` (B: ingest totals, then request rows, rollup; session cost equals request-only cost), `test_unknown_totals_model_prices_zero_and_logs_unpriced` (C, caplog), `test_totals_without_cache_split_keep_5m_1h_null`, and a string test that `_totals_are_a_source` source is unchanged (D).
- [ ] **Step 2: Run, expect FAIL** (`poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_live_session_subagent_cost.py -v`).
- [ ] **Step 3: Implement.** In both queries replace `FROM cost_daily c` with a derived table `UNION ALL` of the `cost_daily` token columns and the `session_usage` rows (filter on the token_source, `session_id` scoped by `_scope(f, "u.session_id")` for the list query, `= $sid::text` for detail), then keep the existing GROUP BY. The reader adds no double-count guard.
- [ ] **Step 4: Run the new tests and `tests/codemie/repository/cli_analytics/postgres/test_reader*.py`; expect PASS.**
- [ ] **Step 5: Commit.**

Test-first: yes — totals-only agent session shows cost 0.0 today and must show its priced cost.

### Task 2: Cost-less sessions in the list with `cost_available` (AC-F)

**Files:**
- Modify: `D:639-678` (`get_session_cost_facts(f, search=None, is_unattributed=False, include_costless=False)`), `repository/cli_analytics/ports.py:183`, `rest_api/models/cli_analytics.py:224` (add `cost_available: bool = True` after `cost_usd`), `H:689-758` (`get_sessions`).
- Test: `tests/codemie/repository/cli_analytics/postgres/test_live_costless_sessions.py` (new); extend handler tests in `tests/codemie/service/analytics/` where `get_sessions` is covered.

**Interfaces:** Consumes Task 1 rows. Produces: `include_costless=True` makes the join `FROM sel s JOIN act a ... LEFT JOIN {_cost_rows} c`, grouped by `s.session_id`; cost columns are NULL for those sessions.

- [ ] **Step 1: Write failing tests:** `test_costless_session_listed_with_zero_cost_and_cost_available_false`, `test_priced_session_has_cost_available_true`, `test_kpi_cost_and_priced_aggregates_unchanged_by_costless_session` (overview totals and turns/files/tool-call aggregates equal with and without the session), `test_costless_session_sorts_by_cost_without_error`, and a caplog test for the count-only log line.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** the SQL branch (default path byte-identical); in `get_sessions` pass `include_costless=True`; set `s["cost_available"]` true when the session has at least one token row (totals included) whose model has a catalog entry (a priced row), false for no token rows or only rows of models missing from the catalog (a model with a catalog entry but no price for some token kind counts as priced, so `cost_usd` may be understated); `_i`/`_s` already tolerate None; log once the count of sessions listed with `cost_available=false`, split into `no_token_rows` and `unpriced_models` (no user or project data).
- [ ] **Step 4: Run new tests plus `tests/codemie/service/analytics` and `tests/codemie/rest_api` cli_analytics tests; expect PASS.**
- [ ] **Step 5: Commit.**

Test-first: yes — a session with dimension events and no cost rows is absent from the list today.

### Task 3: Gate-discard log line (AC-E; the counter was dropped after review)

**Files:**
- Create: `repository/cli_analytics/gate_discards.py` — `record_gate_discard(route: str, reason: str, count: int, user_hash: str | None) -> None` (one `logger.info`; the module-level `Counter` and `discard_count` were dropped after review).
- Modify: `repository/cli_analytics/postgres/ingestor.py:366` and `:379` (call it before returning, `route` = `"otlp"`/`"hooks"`, `count` = `batch.wire_count`, `user_hash = getattr(gate, "user_hash", None)`); `service/analytics/cli_analytics_project_gate.py:47-52` (`_ProjectIngestGate.user_hash: str | None`, set in `__init__` from `HMAC-SHA256(key, str(user.id))` hex truncated to 16 chars; None when the key is empty); `configs/config.py` near :533 (new `CLI_ANALYTICS_LOG_HMAC_KEY: str = ""`, not `MCP_AUTH_HMAC_SECRET`).
- Test: `tests/codemie/repository/cli_analytics/test_gate_discards.py` (new); extend `tests/codemie/service/analytics/test_cli_analytics_project_gate.py`.

- [ ] **Step 1: Write failing tests:** one parametrized test over the three reason codes and both routes (one log line with reason, route, count, hash); `test_empty_key_emits_no_hash_field`; `test_log_has_no_email_project_or_raw_user_id`; `test_accepted_request_logs_nothing`; hash is stable per user and differs across users.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** as above; the HTTP response is unchanged.
- [ ] **Step 4: Run these tests and `tests/codemie/rest_api/routers/test_cli_analytics_project_gate.py`; expect PASS.**
- [ ] **Step 5: Commit.**

Test-first: yes — a discarded request logs nothing and counts nothing today.

### Task 4: Storage guide (AC-G)

**Files:** Modify `.ai-run/guides/integration/cli-analytics-storage.md`.

- [ ] Document the query-time cost path for totals-only subagents, the accepted residuals (KPI/model-breakdown/users token counts and dispatch cost exclude totals; hourly views omit them), `cost_available`, and the gate discard log line plus the `CLI_ANALYTICS_LOG_HMAC_KEY` setting.

Test-first: no — documentation only.

## Negative constraints

Honored: no rollup/`_totals_are_a_source` change (Task 1 only edits reader), no backfill, no NULL cost (Task 2 uses 0.0), no persistence or HTTP change for discards (Task 3), no change to KPI/model-breakdown/users queries, `MCP_AUTH_HMAC_SECRET` not reused, no unkeyed hash (empty key emits none), zero-dimension-event sessions left alone.
