# CHECKPOINT: fix_mr_review (EPMCDME-16435)

All line refs below are in the worktree after the change. No git add/commit run.

## New modules
- `src/codemie/service/usage_attribution/postgres_sources.py` (F1, F4, F5, F13): `reader_or_none()`, `async pg_framework_rows(start, end) -> list[list]`, `async skill_names_by_session(reader, session_ids) -> {sid: [names]}`, `postgres_names_win(extra, names)`.
- `src/codemie/service/usage_attribution/framework_merge.py` (F13): `merge_postgres_framework(job, sess_spec, engine, db_timings)`, moved verbatim from worker.py.

## Per item

- **F2 done.** `analysis.py:747-751`: `[] if (inputs_blob or evidence_blob) else await pg_framework_rows(rng.evidence_start, rng.end)`. Tests `test_framework_wiring.py` `test_postgres_is_read_only_when_nothing_is_cached` (params inputs / evidence / nothing: 0, 0, 1 reads; the old "cached inputs still read" test is flipped into the `inputs` case).
- **F3 + F9 done.** `reader.py:847` `get_framework_sessions(start, end, framework_labels: Sequence[str])` uses `delivery_framework = ANY($labels::text[])`; the per-pair `sessions` count is removed from the query (tuples are now 4 items). `ports.py:215` signature updated. `framework_sessions.py:151` `aggregate_pg_rows` now returns `[email, repo, framework]`; `merge_framework` reads 3-item rows and keeps `min(es_sessions, max(es, pg))`. Caller passes `get_framework_labels()[:-1]` (verified: `delivery_framework.py:62-65` returns rule labels + `[fallback]`). Tests updated: `test_reader.py::test_framework_sessions_group_raw_keys_and_bind_the_window_and_label`, `test_framework_sessions.py` (4-tuples/3-item rows; the aggregate test now sums 1+3), `test_framework_wiring.py` fake and labels `["STLC Factory", "SDLC Factory"]`, live `test_live_framework_sessions.py` (labels list; a row with a renamed fallback label "Old plain chat" must not count; result `("owner@x.com", remote, "repo", 1)`).
- **F1 done.** `postgres_sources.py:reader_or_none` checks `customer_config.is_feature_enabled("cliAnalytics")`, then builds the storage; any build error returns None (debug log, exception type only). Used by `pg_framework_rows` (A5) and `skill_names_by_session` (person view, when no reader is injected). Handler `_resolve_reader` removed. Tests (`test_person_recent_sessions.py`): `test_reader_or_none_is_none_when_the_feature_is_off`, `..._is_the_storage_reader_when_the_feature_is_on`, `..._is_none_when_the_storage_cannot_be_built`, `test_person_view_reads_postgres_names_only_when_the_feature_is_on`; A5 flag-off path stays in `test_framework_wiring.py::test_feature_off_gives_no_rows_and_no_query`.
- **F4 done.** `postgres_sources.postgres_names_win`: Postgres names replace the ES keys of a session; ES keys stay only when Postgres has none/failed. Test `test_postgres_names_win_over_installed_es_skills` (ES `sdlc-factory:complexity-assessor`, Postgres `stlc-factory:suite-health` -> STLC label).
- **F7 done.** `handler.py:~899-904`: `_gather(self._person_session_extras(...), skill_names_by_session(self._reader, ids))`, then `postgres_names_win`. `test_person_live_labels_recent_sessions_from_postgres_names` covers the wired path.
- **F5 done.** `postgres_sources.py:pg_framework_rows`: `asyncio.wait_for(reader.get_framework_sessions(...), PG_FRAMEWORK_TIMEOUT_S)` then `asyncio.to_thread(aggregate_pg_rows, rows)`; any error/timeout logs the exception type and returns `[]`. Constant `constants.py:195` `PG_FRAMEWORK_TIMEOUT_S = 10.0`. Test `test_a_slow_query_gives_no_rows_after_the_timeout` (timeout patched to 0.05 s, reader sleeps 5 s; asserts `[]` and "TimeoutError" logged).
- **F13 partly done.** Moved out: `_pg_framework_rows` (analysis), `_resolve_reader` + `_with_postgres_skill_names` (handler), `_merge_postgres_framework` (worker). Call sites are one line each. Sizes now: analysis.py 892 (was 914), handler.py 1037 (was 1065), worker.py 526 (was 548). The three files are still over ~500 lines because of pre-existing code; splitting them further was not in the item.
- **F8 done.** `session_derived.py:30-33` comment rewritten to describe the unconditional union.
- **F6 done (SQL), EXPLAIN not verified.** `session_derived.py:39-40,47`: kind-2 arm gets `i.hour >= coalesce(date_trunc('hour', sd.started_at) - interval '1 hour', '-infinity') AND i.hour <= coalesce(date_trunc('hour', sd.last_event_at) + interval '1 hour', 'infinity')`. A NULL start/end leaves that side unbounded (without the coalesce a NULL would drop every dispatch row). No `summary_ts` gate. Tests: `test_session_derived.py::test_the_classifier_reads_the_skills_of_otel_and_of_hooks_...`, `test_reader.py` names-SQL fragments, new live `test_live_summary_skill_names.py::test_dispatch_names_are_bounded_to_the_session_hours_with_one_hour_margin` (rows at -2h and +2h are excluded, -1h/0/+1h included). Not verified: EXPLAIN / partition pruning (the compose DB has no `invocations_hourly` table; no scratch session was built). Risk to know: a kind-2 row more than one hour after `last_event_at` or before `started_at` is no longer a name (the derived column then also misses it).
- **F10 done.** `reader.py:847-853` docstring says it is deliberately global per person/repository (no project scoping, no `_NOT_WINDOWED` handling). Assertion `"project" not in sql.lower()` in the `test_reader.py` framework-sessions test.
- **F12 done.** `test_reader.py::test_skill_names_bind_the_distinct_session_ids_...`: `"coalesce("` replaced by `UNION` count == 4 and literal fragments of all five sources, including the `jsonb_typeof(sd.commands) = 'array'` guard.
- **Doc (not in the fix file, behaviour changed).** `.ai-run/guides/integration/cli-analytics-storage.md:298` sentences on the person view (Postgres names win), A5 labels, timeout, cache rule.

## Tests that can fail without the change
- `test_postgres_is_read_only_when_nothing_is_cached[inputs|evidence]`: reader calls == 0; fails on the old guard `if evidence_blob` (inputs case read once).
- `test_a_slow_query_gives_no_rows_after_the_timeout`: without `wait_for` the 5 s sleep returns rows (AGGREGATED), not `[]`.
- `test_postgres_names_win_over_installed_es_skills`: merging gives SDLC first-match, not STLC.
- `test_reader_or_none_is_none_when_the_feature_is_off`: storage_calls == 0 / None; the old person-view path built the storage regardless.
- live `test_counts_current_framework_sessions_...`: the "renamed" row is counted with the old `<> fallback` predicate.
- live hour-bound test: `out-before` / `out-after` appear without the hour predicate.

## Results
- `make ruff`: all checks passed.
- Narrow: `tests/codemie/service/usage_attribution` + `tests/codemie/repository/cli_analytics`: all green except the unrelated live turns test below.
- `make test-cli-analytics-live`: 206 passed, 1 failed: `test_live_turns_daily.py::test_an_interrupted_prompt_whose_hook_lands_59_minutes_later_is_a_turn` (turns == 2, expected 1). Turns rollup is outside this change; not checked on a clean tree. On the compose DB the same test fails earlier with `alembic ... Can't locate revision 'a1c1a0000008'` (the DB is migrated by another branch).
- Full `poetry run pytest tests/`: 25492 passed, 59 failed, 2 errors. Failures are outside my files: `tests/enterprise/mcp_auth/*`, `tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py`, `tests/codemie/service/oauth/adapters/test_provider_adapters.py`, `tests/codemie/service/security/test_logout_token_cleanup_mock_tms.py` (error), `tests/codemie/rest_api/router_guards/test_openapi_snapshot.py` (baseline has `/chat/completions`, live does not), plus the turns live test above. All other agents' work in progress / not touched here.

## Not done
- F11 (out of scope). Dead code noticed, not touched: none.
