# CHECKPOINT: fix_mr4652_threads

## Fix 1: offset overflow (done)
- `src/codemie/repository/cli_analytics/postgres/reader.py:72` new constant `_MAX_PAGE_OFFSET = 2**62`; `get_session_page` now binds `params["offset"] = min(page * per_page, _MAX_PAGE_OFFSET)`. Router untouched.
- Test added: `tests/codemie/repository/cli_analytics/postgres/test_reader.py::test_a_huge_session_page_binds_a_clamped_offset_that_fits_bigint` (mirrors `_page_query` / `_PageArgRecorder`; page=10**18, per_page=50).
- Can fail: without the clamp the bound offset is 5 * 10**19, so `4611686018427387904 in args` and the `<= 2**63 - 1` assert both fail.

## Fix 2: "cost unavailable" log counts unlisted sessions (done)
- `src/codemie/service/analytics/handlers/cli_analytics_handler.py` `get_sessions`: the `_log_unavailable_cost` call moved from before the row build to right after `_page_sessions`, fed with `[s["trace_id"] for s in page_rows]`.
- Test added: `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py::test_python_paged_costless_log_counts_only_the_listed_page`, parametrized over `cost_usd` sort and `framework="Pure chat"`: 3 costless sessions fetched, per_page=1, log args `(1, 1, 0)`.
- Can fail: with the old call position the log reports 3 sessions (`(3, 3, 0)`).
- Existing log tests (SQL path, `test_costless_sessions_are_logged_as_a_count_only`, `test_totals_only_session_is_not_counted...`) stay green unchanged.

## Fix 3: detail tokens omit subagent totals (done, no reader change)
- `cli_analytics_handler.py` `get_session_detail`: `input_tokens`, `output_tokens`, `cache_read`, `cache_creation` are now sums over `model_cost_rows` (the `_token_rows` result). They feed the detail's four token columns and the session lane of `_build_dispatches`. `cost` still supplies `model_name` (fallback for `_display_models`).
- Test added: `test_cli_analytics_handler.py::test_get_session_detail_tokens_include_totals_only_subagent_rows`: empty `get_session_detail_cost` row, two model rows; detail tokens `(1020, 205, 307, 50)` and the session lane agrees.
- Can fail: with the old code the detail tokens come from the (empty) cost row and are 0.
- Live test added: `tests/codemie/repository/cli_analytics/postgres/test_live_session_subagent_cost.py::test_totals_only_session_detail_tokens_match_the_list_tokens` (seeds a totals-only session via the file's `_seed_start`/`_seed_totals`/`_refresh`, asserts list and detail tokens equal, and input/output/cache_read are 1000/200/300). NOT run, as instructed (needs the local Postgres; it skips without one). Only `--collect-only` was run: it collects. Treat it as unverified until run against a DB.
- Storage guide (`.ai-run/guides/integration/cli-analytics-storage.md`, accepted residuals ~line 331) does not mention the detail tokens (it lists KPI, model-breakdown, users and dispatch cost), so it needed no edit.

## Spec amendment (done)
- `docs/superpowers/tasks/2026-10-27-epmcdme-16441/spec.md`: Design 2 gets an "Amended (2026-10-28 ...)" bullet (session_count, up to 3 session_ids from the request body, client-supplied, sanitized, 64 chars, de-duplicated, still no email / project / raw user id / raw header, not rate limited, pointer to AC1 of the newer spec); AC-E extended with the two fields. No code change in `gate_discards.py` / `ingestor.py`.
- Note: the brief points to "storage guide ~line 108" for the not-rate-limited claim; the guide has no such sentence (grep for "rate limit" finds nothing), so I did not cite it. `gate_discards.py` has no throttling code either.

## Commands and totals
- `poetry run pytest tests/codemie/service/analytics/handlers/ tests/codemie/repository/cli_analytics/postgres/test_reader.py -q`: 705 passed, 8 skipped (run after all code and test edits).
- `poetry run ruff format --check` and `poetry run ruff check` on the 5 touched python files: 5 files already formatted; all checks passed.
- `poetry run pytest .../test_live_session_subagent_cost.py --collect-only -q`: new test collected.
- Full `tests/` suite not run (brief: narrow suites only).

## Not done / uncertain
- Live test for Fix 3 unexecuted (see above).
