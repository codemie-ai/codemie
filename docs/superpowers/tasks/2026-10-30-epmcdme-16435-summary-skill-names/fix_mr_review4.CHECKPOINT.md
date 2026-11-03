# Fix 4 CHECKPOINT: third MR review (EPMCDME-16435, MR !4696)

Gates: `make ruff` green. `env -u CLI_ANALYTICS_PG_URL poetry run pytest tests/codemie/repository/cli_analytics tests/codemie/service/usage_attribution -q -k "not live"`: before 1665 passed, after 1719 passed (+54, 40 of them seeds of the equivalence test). The live suite was not run (as instructed); the 12 live tests of `test_live_summary_skill_names.py` collect.

Full `poetry run pytest tests/` (run in the foreground): green for everything of this fix. Red outside the zone, not touched: the `*_live_*` tests of `cli_analytics/postgres` (the local Postgres has no `session_dims` table, `UndefinedTableError`; the controller runs them against a prepared DB), `tests/enterprise/mcp_auth/*`, `tests/codemie/service/oauth/adapters/test_provider_adapters.py`, `tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py`, `tests/codemie/rest_api/router_guards/test_openapi_snapshot.py` (no route of this fix changed). Work in progress by others or environment.

SQL sanity (no suite): the new `_READ` was run on throw-away TEMP tables (gone with the connection) in the local Postgres: a dispatch 5 h before `started_at` dropped, 30 min before kept, 5 h after kept, NULL `started_at` keeps a 40-day-old row, a kind-4 row alone gives its name, a kind-3 row is not read.

## Items

### R3 (+R6) done
- `constants.py:195-198`: `PG_DEGRADED_TTL_S = 60`.
- `analysis.py` `_store_computed_result` (~714-731): `ttl = min(full_ttl, PG_DEGRADED_TTL_S)` when `result["degraded"]`; meta and sections use it (so the same range is computed again after 60 s), `meta["ttl_s"]` reports it; the last-good copy keeps `full_ttl` semantics (`_save_last_good(..., full_ttl)`).
- `analysis.py` `_compute` (~768-775): the failed-read branch sets `degraded: True` besides nulling `inputs_blob`/`evidence_blob`; comment rewritten to say what is true (kept PG_DEGRADED_TTL_S, last good copy keeps its lifetime).
- Tests (`test_framework_wiring.py`): `test_a_failed_read_is_not_kept_for_the_hour` rewritten: SAME range; within 60 s a hit with `ttl_s == 60` and one Postgres read; after the cache clock advances 61 s the range recomputes (stale + refreshing, then the new reader is asked once, job 2 carries the rows, and the complete result is cached with `ttl_s == 900`). `test_a_successful_read_is_kept_for_the_full_time`: success case stays a hit after 61 s, one job, one read. The old week-range test is kept as `test_a_failed_read_leaves_no_inputs_or_evidence_for_other_ranges`.
- Can fail: `first.meta["ttl_s"] == PG_DEGRADED_TTL_S` / the post-advance recompute (`len(runner.jobs)` and `[j["pg_framework_rows"] ...] == [[], AGGREGATED]`) fail without the change (meta kept 900 s, the range stays a hit).

### R9 done
- `framework_merge.py`: `merge_postgres_framework(...) -> bool` returns True after the failure (still logged at warning with the exception type only), False when merged or nothing to merge.
- `worker.py:414, ~436-440`: `merge_failed = merge_pg and merge_postgres_framework(...)`; on failure `inputs["meta"]["postgres_merge_failed"] = True`, which travels in the result meta.
- `analysis.py` `_compute`: `postgres_failed or result["meta"].get("postgres_merge_failed")` takes the same degraded path (no inputs/evidence stored, short TTL).
- Tests: `test_a_failing_merge_is_flagged_in_the_meta_and_a_good_one_is_not` (failed / merged / no rows); `test_a_failed_merge_in_the_worker_is_kept_as_briefly_as_a_failed_read` (runner returns the flag: `ttl_s == 60`, a second range reads evidence and inputs anew). Can fail: the flag assert and `ttl_s` / `evidence_blob is None` of job 2 fail without the change.

### R7 done
- `analysis.py` `_run_job` (~866-872): after `await before_start(job)`, `if self._closing: admission.started = False; raise AnalysisUnavailableError(_RESTARTING, 503, 10)`; the `finally` releases the slot; `started = False` gives the counts back (as for the pre-start refusal).
- Test `test_shutdown_while_the_job_is_completed_starts_no_worker`: the Postgres read (the `before_start`) calls `svc.shutdown()`; asserts the "restarting" error, `runner.jobs == []`, `counted(svc, "u") == (0, 0)`. Can fail: without the re-check the runner gets the job.

### R2 done
- `session_derived.py` `CLASSIFIER_NAMES_SQL`: `AND i.kind IN (2, 4) AND i.hour >= coalesce(date_trunc('hour', sd.started_at) - interval '1 hour', '-infinity'::timestamptz)`; no upper bound; comment above the SQL rewritten (why only a lower bound, why the coalesce).
- Tests: `test_session_derived.py` literal updated; `test_reader.py::test_skill_names_read_session_dims_with_the_sql_of_the_classifier` (fragment `i.kind IN (2, 4)`, exactly one `i.hour`, the lower-bound text). Live (`test_live_summary_skill_names.py`): `test_dispatch_long_after_the_last_dimension_hook_is_still_classified` now uses a dispatch 30 min before and 5 h after `started_at` (both kept); new `test_dispatch_far_before_the_session_start_is_outside_the_lower_bound` (-5 h dropped, -1 h kept), `test_a_session_without_started_at_keeps_its_dispatch_names` (40-day-old row, classifies STLC). Not run live (see above).

### R8 done
- Same SQL, `i.kind IN (2, 4)`, lower bound kept.
- Live test `test_a_slash_command_row_alone_classifies_a_session_without_summary_or_dispatch`: only a kind-4 `stlc-factory:x` row, no summary, no kind-2 row: names `["stlc-factory:x"]`, STLC. Can fail: with `kind = 2` the names are `[]`.

### R4 done
- `postgres_sources.py` `skill_names_by_session`: one entry for every row the reader returned, an empty list for a session that used nothing; `postgres_names_win` therefore replaces the ES keys of every session with a row (docstring updated), ES keys stay only for a session without a row or after a failure (`{}`).
- `reader.get_skill_names_by_session` verified: `SELECT sd.session_id, coalesce({CLASSIFIER_NAMES_SQL}, ARRAY[]::text[]) AS skill_names FROM session_dims sd WHERE sd.session_id = ANY(...)` (reader.py ~685-700): a row for every existing `session_dims` row. No change.
- Tests (`test_person_recent_sessions.py`): `test_a_known_session_that_used_nothing_is_labelled_from_postgres_alone` (row with `[]` plus an STLC ES key gives the fallback label, not STLC; helper returns `{"s1": []}`); existing `test_missing_postgres_row_keeps_es_label` (no row: ES keys) and `test_reader_error_falls_back_to_es_keys` (failure: ES keys) cover the other two cases. Can fail: the old filter dropped the empty entry, so the label came out STLC.

### R10 done
- `postgres_sources.py`: `reader_or_none` has the `customer_config` import and the feature check inside the try; the failure is logged at `warning` with `type(exc).__name__` only (was `debug`). `pg_framework_rows`: the lazy imports, `reader_or_none()` and `get_rule_framework_labels()` moved inside the try, so a config error is the existing `None` (failed read, logged) and never an exception.
- Tests: `test_storage_config_error_gives_no_rows_and_is_logged_at_warning` (type logged, `secret-token` not), `test_a_failing_feature_check_gives_no_rows_instead_of_raising`, `test_a_broken_label_configuration_is_a_failed_read_not_an_error` (wiring); `test_a_broken_storage_configuration_is_logged_at_warning_with_the_exception_type_only`, `test_a_failing_feature_check_degrades_the_person_view_to_the_es_keys` (person view). Can fail: the recorder logger has no `debug`, and a raising label config propagated before.

### R12 done
Removed: the per-key re-sum of `pg_rows` in `merge_framework` (it is a plain dict now; `aggregate_pg_rows` already gives one row per key, and with R13 it also folds keys that differ only by slashes, so the guarantee holds); the copy of every row (only rows that actually change are copied, the others are returned as they are); the in-loop `if rest <= 0: break` (a key with `rest <= 0` is skipped before the loop; inside the loop `add > 0` already stops adding). Kept, with reason: the `min(sessions, max(ES, PG))` clamp and the per-row `row[2] - row[5]` room (over-count protection; ES `framework` may exceed `sessions` as an approximate count), `type(row[5])(...)` (keeps float from ES), the `add > 0` check.
Proof: `_previous_merge_framework` (the old code) kept in `test_framework_sessions.py`; `test_merge_framework_equals_the_previous_implementation_on_random_input` (40 seeds: ints and floats, framework above sessions, unmatched uids, mixed-case emails) asserts equal output and unmutated input; all 15 earlier merge tests pass unchanged.

### R13 done
`framework_sessions.py`: `aggregate_pg_rows` keys on `canonical_value(normalize_repository(...))`; `merge_framework` compares `canonical_value` on the Postgres key and on the ES value, the email `.strip().lower()` on the ES side. `normalize_repository` and the golden vectors untouched. Tests: `test_merge_framework_matches_a_repository_by_its_canonical_form_on_both_sides` (`Acme//Api/` ES vs `acme/api` PG, and the reverse), `test_merge_framework_ignores_a_trailing_slash_and_keeps_a_folder_distinct` (`acme/api/` matches; `acme/` does not), `test_merge_framework_trims_the_email`, `test_aggregate_pg_rows_sums_repositories_that_differ_only_by_slashes`.

### R14 done
`reader_or_none() -> CliAnalyticsReader | None`; `skill_names_by_session(reader: CliAnalyticsReader | None, ...)` (None is the "injected one, else reader_or_none()" case the handler and tests use, so the directive's `CliAnalyticsReader` got `| None`); `pg_framework_rows -> list[list[str | int]] | None`; `aggregate_pg_rows -> list[list[str | int]]`; `merge_postgres_framework(job, sess_spec: Spec, people: list[list[Any]]) -> bool` (`Spec` of `fetch.py` under `TYPE_CHECKING`). The `Protocol` import is under `TYPE_CHECKING`. Fakes in tests unchanged; `make ruff` green. `Any` remains only inside `list[list[Any]]` for the mixed-type people rows and `dict[str, Any]` for the job.

### R15 done
- `framework_sessions.py` module docstring: `repo_remote` is the parsed id (older or other writers may store a raw URL, both handled), `repository` the earliest raw working directory; the merge compares through `canonical_value`.
- `classify_delivery_framework` docstring: input is skill names, subagent types, hook-dispatch names and slash-command names (`commands`), example `["reload-plugins", "stlc-factory:suite-health"]`.
- `.ai-run/guides/integration/cli-analytics-events.md:413`: CHANGED, the `agent.git.snapshot` example had `"repo_remote": "https://gitlab.example.com/org/repo.git"`; the CLI sends the parsed id (fix_mr_review3 facts), so it now reads `"org/repo"`.
- `postgres_sources.py` `skill_names_by_session` docstring: matches only when the ES proxy session id equals `session_dims.session_id` (pinned `--session-id`, not Claude Code whose ES id is the CLI-generated `CODEMIE_SESSION_ID`); a miss is not an error and the ES keys stay.

## Not done / notes
- not done: none.
- The live suite was not run; the new live tests are written against the SQL checked on TEMP tables above.
- Noticed, not changed: `session_derived.py` `CLASSIFIER_NAMES_SQL` is shared by the classifier and the reader views, so the kind-4 arm and the lower bound apply to the list and details views too (intended: one expression).
- Out of scope as listed: R1, R5, R11, `.codemie/`, `doctor.json`.
