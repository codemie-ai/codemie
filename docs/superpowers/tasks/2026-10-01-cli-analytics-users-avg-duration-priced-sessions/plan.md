# Users avg_session_duration_ms priced-universe fix (EPMCDME-15228) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** `/cli-analytics/users` `avg_session_duration_ms` averages only priced sessions, matching `/cli-analytics/overview` (D1 rule).

**Architecture:** In `get_users`, filter `duration_rows` to the union of per-user `session_ids` (already derived from cost_daily JOIN sel) before averaging. No repo, port, router or response-model change. Chosen per research option (a): no extra query.

**Tech Stack:** Python, pytest-asyncio, AsyncMock repo fakes.

**Spec:** requirements inline (no spec.md); analysis in `technical-analysis.md` in this directory.

Commit per task using the repository's existing convention (`EPMCDME-15228: <Description>`); no AI attribution footers.

## Acceptance criteria

- `get_users` average duration includes only sessions present in users' `session_ids`.
- Sessions in `duration_rows` that are not priced do not change the average.
- Empty priced set (no users or no matching durations) yields `avg_session_duration_ms` of `None`.
- Readers, ports and the response model are unchanged.

## Task 1: Restrict get_users duration average to priced sessions

**Files:**
- Modify: `src/codemie/service/analytics/handlers/cli_analytics_handler.py:382-383` (inside `LocalAnalyticsHandler.get_users`)
- Test: `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` (append; reuse `make_filter`, imports already present)

Test-first: yes — `get_users` with user rows whose `session_ids` are `["s1","s2"]` and duration rows for s1, s2 and unpriced s3 returns the average of s1/s2 only (currently includes s3 and fails); a second case with no priced sessions returns `None`.

- [ ] **Step 1: Write failing tests.** Append two `@pytest.mark.asyncio` tests (e.g. `test_get_users_avg_duration_only_priced_sessions`, `test_get_users_avg_duration_none_when_no_priced_sessions`). Build a `MagicMock()` repo, set `AsyncMock(return_value=...)` for each of the eight reads called at handler lines 334-341: `get_users`, `get_users_daily_activity`, `get_users_last_active`, `get_lines_by_user`, `get_turns_by_session`, `get_file_facts_by_session`, `get_tool_success_by_session` (all `[]`), and `get_session_durations`. Construct `LocalAnalyticsHandler(repo)` and call `await handler.get_users(make_filter(), page=None, per_page=None)`.
  - Case 1: `get_users` returns one row `{"developer_name": "dev", "session_ids": ["s1", "s2"]}`; durations `[{"session_id": "s1", "duration_ms": 1000}, {"session_id": "s2", "duration_ms": 3000}, {"session_id": "s3", "duration_ms": 50000}]`; assert `avg_session_duration_ms == 2000`.
  - Case 2: `get_users` returns `[]` with the same durations; assert `avg_session_duration_ms is None`.
- [ ] **Step 2: Run** `poetry run pytest tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py -k get_users_avg -v` from `codemie/`. Expected: both FAIL (case 1 gives 18000, case 2 gives non-None).
- [ ] **Step 3: Implement.** Replace lines 382-383: before computing `durations`, build `priced_sessions = {sid for row in rows_source ...}` as the union of `_s(s)` over `row.get("session_ids") or []` for `row in user_rows` (use `user_rows`, not the paginated `rows`), add a D1-rule comment mirroring the overview comment near line 219, and filter `duration_rows` with `_s(r.get("session_id")) in priced_sessions`. Keep `avg_duration = int(sum/len) if durations else None`.
- [ ] **Step 4: Run** the same command, then the whole file and `test_cli_analytics_handler_ordering.py`. Expected: PASS.
- [ ] **Step 5: Run** `poetry run ruff check` and `ruff format --check` on the two touched files, then commit with `EPMCDME-15228: <Description>`.

## Negative-constraint pass

- "Readers and response model should not change": Task 1 touches only the handler and its test.
- "Empty priced set must still yield None": covered by Case 2 and by the unchanged `if durations else None` guard.
- "no git side effects beyond per-task commits, no AI attribution": stated in the header; no commit blocks are written.
