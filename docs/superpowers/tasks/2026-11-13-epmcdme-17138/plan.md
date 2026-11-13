# EPMCDME-17138 No-usage sessions are a known $0.00 cost: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A CLI analytics session with no model usage (no token rows, or only all-zero-token rows) reports `cost_available=true`, `cost_usd=0` on both the sessions list and session detail.

**Architecture:** The rule lives once in `session_money.py`: ignore (session, model) rows whose tokens are all zero, then a session's cost is available when no usage rows remain or at least one usage row's model is priced. List and detail both call it. No API field and no SQL change.

**Tech Stack:** Python, FastAPI handler layer, pytest + pytest-asyncio, live Postgres harness (`TEST_LOCAL_PG_URL`).

**Spec:** inline requirements (EPMCDME-17138) given to the planner; research in `technical-analysis.md` in this folder.

Commit per task using the repository's existing convention.

## Acceptance criteria

- A session with no token rows: list and detail both return `cost_available: true`, `cost_usd: 0.0`.
- A session whose rows all have zero tokens (model `""` or an uncatalogued model): `cost_available: true`, `cost_usd: 0.0`, and that model is not in `metadata.unpriced_models`.
- A session with positive tokens of only unpriced models: `cost_available: false`; `unpriced_models` behaviour unchanged.
- A session with priced usage (alone or alongside unpriced): `cost_available: true`, cost unchanged.
- List and detail give the same `cost_available` for the same session (one shared helper).
- No-usage sessions no longer produce the "listed with cost_available=false" log line.
- OpenAPI schema unchanged: `test_openapi_snapshot.py` passes without regenerating the baseline.

## Global Constraints

- No new response field; do not edit `Field(description=...)` on `cost_available` / `unpriced_models` (`rest_api/models/cli_analytics.py`).
- Zero-token test is the same one `CatalogPricer.price` uses: any of `kind_tokens(token_counts(row))` > 0 (`coding_agent_pricing.py:187`, `session_money.py:64`).
- Zero-rate catalog entries stay unpriced (EPMCDME-16522).
- Out of scope: UI repo, EPMCDME-17139 (keep `include_costless` listing and `session_dims`-keyed detail).

## Review Focus

- Negative token sums (bad data) count as no usage, same as `price()`. Pinned by the zero-token unit test using `input_tokens: -5`.
- Fractional dispatch sums round via `_i`; a row with 0.4 tokens rounds to 0 and counts as no usage. Accepted, matches `price()`.
- A session mixing a zero-token unpriced row with a priced positive row stays `true`. Covered by the existing mixed test.
- Cost sort pages on the `cost_usd` sort: no change, since no-usage sessions already sort as 0.

---

### Task 1: One no-usage-aware cost rule for list and detail

**Files:**
- Modify: `src/codemie/service/analytics/handlers/session_money.py:75-86`
- Modify: `src/codemie/service/analytics/handlers/cli_analytics_handler.py:93-99` (imports), `:1408-1420` (`_log_unavailable_cost`), `:1576`, `:1597-1599` (list), `:1700` (detail)
- Modify: `.ai-run/guides/integration/cli-analytics-storage.md:354` (and `:361` if it restates the no-usage rule)
- Test: `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py`

**Interfaces:**
- Produces: `cost_available(rows: Iterable[dict[str, Any]], pricer: CatalogPricer) -> bool` and `unpriced_session_ids(rows: list[dict[str, Any]], pricer: CatalogPricer) -> set[str]` in `session_money.py`. They replace `has_priced_model` and `priced_session_ids`, which have no other callers. A private `_has_usage(row) -> bool` implements the zero-token test from Global Constraints.

Test-first: yes. New and updated handler tests fail: a no-usage list row and detail still return `cost_available False`, and a zero-token-row session is still unpriced.

- [ ] **Step 1: Update the existing tests and write new failing ones** in `test_cli_analytics_handler.py`:
  - Rename `test_costless_session_is_listed_with_zero_cost_and_cost_available_false` (line 146) to `..._cost_available_true` and assert `cost_available is True`, `cost_usd == 0.0`.
  - Rename `test_get_session_detail_without_model_rows_reports_cost_available_false` (line 1300) to `..._true` and assert `True`.
  - New list test `test_a_session_with_only_zero_token_rows_is_listed_cost_available_true`: rows `{"session_id": "s1", "model_name": ""}` and `{"session_id": "s1", "model_name": "zero-token-model-xyz", "input_tokens": -5}` under `_two_model_catalog()`. Assert `cost_available is True`, `cost_usd == 0.0`, `pricer.unpriced == []`.
  - New detail test `test_get_session_detail_with_only_zero_token_rows_reports_cost_available_true`: same rows (without `session_id`) via `_priced_detail_handler`. Assert `True`, `unpriced == []`.
  - Log tests (lines 495, 507, 523): the costless sessions are no longer logged, so assert `log.info` was not called with the "cost_available=false" message. In test 523 assert the map is `{"s2": True, "s4": True}`.
  - Test 537: the log args become `(message, 1)` (single unpriced count). Keep the unpriced-only list/detail tests (537, 1310), the mixed tests (558, 1322) and the zero-rate test (573) otherwise unchanged; they pin the unpriced and priced paths.
- [ ] **Step 2: Run** `pytest tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py -v`. Expected: the renamed, new and log tests FAIL; the others PASS.
- [ ] **Step 3: Implement** the two helpers in `session_money.py`. `cost_available` filters rows with `_has_usage`, then returns `True` if none remain, else `any(pricer.is_priced(_s(r.get("model_name"))) ...)`. `unpriced_session_ids` groups rows by session and returns the ids where `cost_available` is False. Update the docstrings: no usage is a known $0.00 cost (EPMCDME-17138).
- [ ] **Step 4: Wire the handler.**
  - List: `unpriced_sessions = unpriced_session_ids(session_model_rows, pricer)` and `s["cost_available"] = s["trace_id"] not in unpriced_sessions`.
  - Detail line 1700: `cost_available(model_cost_rows, pricer)`. Check that the rows carry token fields, which they do because detail sums tokens from them.
  - `_log_unavailable_cost(session_ids, unpriced_sessions)`: count only page ids in `unpriced_sessions`, and log `"cli_analytics: %d sessions listed with cost_available=false: unpriced models"` with that one count when it is non-zero. Drop `no_token_rows`.
- [ ] **Step 5: Update the guide** at `cli-analytics-storage.md:354`. `cost_available` is false only when the session has positive tokens and none of its usage models has a catalog price. A session with no token rows or only zero-token rows is a known $0.00. Add *Changed 2026-11-13 (EPMCDME-17138): supersedes the EPMCDME-16441 no-usage → false decision.* Fix the log description in the same paragraph.
- [ ] **Step 6: Run** the handler test file and `pytest tests/codemie/rest_api/router_guards/test_openapi_snapshot.py -v`. Expected: all PASS, baseline untouched.

### Task 2: Live Postgres regression for a no-usage session through list and detail

**Files:**
- Test: `tests/codemie/repository/cli_analytics/postgres/test_live_route_pricing.py` (next to `test_live_a_cowork_session_...`, line 877)

**Interfaces:**
- Consumes: Task 1 behaviour. Existing helpers `_engine`, `_hook` (line 672), `_gate`, `PostgresTelemetryIngestor.ingest_hook_events`, `_refresh`, `_cleanup`, `_cowork_figures(engine, sid)` (returns list row, detail, dims, cost_daily model names).

Test-first: yes. `test_live_a_session_without_model_usage_is_a_known_zero_cost_on_list_and_detail` fails before Task 1 with `cost_available False`; with Task 1 merged it passes and guards the SQL path.

- [ ] **Step 1: Write the test.** Use a fresh `sid = f"no-usage-{uuid4().hex[:8]}"`. Ingest only session hook events built with `_hook` (for example SessionStart and a UserPromptSubmit): no request or api event, so there are no `cost_daily` rows. Then call `_refresh` and `_cowork_figures`, and clean up in `finally`. Assert:
  - `models == set()`
  - `row["cost_available"] is True and row["cost_usd"] == 0.0`
  - `detail["cost_available"] is True and detail["cost_usd"] == 0.0`
- [ ] **Step 2: Run** `TEST_LOCAL_PG_URL=... pytest tests/codemie/repository/cli_analytics/postgres/test_live_route_pricing.py -k no_usage -v`. Expected: PASS (it skips when `TEST_LOCAL_PG_URL` is unset). If `_cowork_figures` cannot find the row because hooks alone create no `session_dims` row, add the minimal dimension event the cowork fixture uses. Do not add a request event.

## Negative constraints

- "No new API field / OpenAPI unchanged": Task 1 changes behaviour only, and Step 6 runs the snapshot test.
- "Unpriced usage must not become available": Task 1 keeps `false` for sessions with positive tokens on unpriced models only (tests 537, 1310 kept).
- "Zero-token rows must not be reported in unpriced_models": `price()` already skips them, and the new Task 1 tests assert `unpriced == []`.
- "No no_token_rows unavailable-cost log": Task 1 Step 4 removes that counter.
- "Out of scope: UI, EPMCDME-17139": no task touches the UI or ingestion, and `include_costless` stays as it is.
