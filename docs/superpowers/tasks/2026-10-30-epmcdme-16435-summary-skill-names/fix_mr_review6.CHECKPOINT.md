# Fix 6 CHECKPOINT (CR-001..CR-004)

Verification: `make ruff` clean; `env -u CLI_ANALYTICS_PG_URL poetry run pytest tests/codemie/repository/cli_analytics tests/codemie/service/usage_attribution -q -k "not live"` -> 1719 passed (before: 1718 passed + 1 failing, my own test, fixed). The new live file also ran locally against the reachable local Postgres: 5 passed (3 new + 2 old).

## CR-001 done
`tests/codemie/repository/cli_analytics/postgres/test_live_framework_sessions.py`: added `_INSERT_PERSON_DIMS` + `_seed_person` helper and three live tests:
- `test_sessions_of_one_person_and_repo_are_counted_together`: two sessions (STLC, SDLC labels) -> one row, count 2.
- `test_the_person_is_the_jwt_email_else_the_largest_developer_name_else_the_developer_name`: jwt wins over names; NULL / empty jwt -> `dev_name_max` lower-cased (mixed-case seed); NULL / empty jwt and max -> `developer_name` lower-cased.
- `test_a_session_without_any_person_is_absent`: all NULL and all empty -> absent; a named one stays.
Can fail: a changed `_SESSION_USER_EMAIL` precedence or dropped `user_email <> ''` filter changes the asserted rows.

## CR-002 done
`analysis.py` `_store_computed_result`: `_save_last_good` is called only `if not result.get("degraded")` (comment added). Covers both degraded paths (failed read, merge failure), both set `degraded`.
Tests (`test_framework_wiring.py`): helpers `Numbered` runner (marks meta `es_ms` and the summary section with the job number) and `last_good(svc)`.
- `test_a_failed_read_is_not_kept_for_the_hour` rewritten: degraded first result -> `last_good is None`; short TTL, hit within it, retry after `PG_DEGRADED_TTL_S` as a synchronous `miss` with 15 min TTL (no stale copy any more); then last good is the complete job 2.
- New `test_a_degraded_analysis_leaves_the_complete_last_good_copy_intact`: complete job 1 saved, a later degraded job 2 leaves last good `(1.0, 1)`, while the served analysis is the degraded one (`es_ms` 2.0, TTL `PG_DEGRADED_TTL_S`).
- `test_a_failed_merge_in_the_worker_...`: added `assert await last_good(svc) is None`.
Can fail: without the `if not degraded` the last-good assertions return the ES-only copy `(2.0, 2)` / not None.

## CR-003 done
`postgres_sources.py`: new `feature_enabled()` and private `_storage_reader()`; `reader_or_none()` rebuilt on them (behaviour unchanged, any absence -> None, person view -> `{}`). `pg_framework_rows`: `[]` only for feature off or no rule labels; feature check, label read and storage build are inside the `try`, any failure -> `None` with a warning (exception type only). Docstring rewritten (`[]` vs `None`).
Tests changed in `test_framework_wiring.py` (`TestRowsBeforeDispatch`): the storage-config-error test now expects `None` (renamed `test_a_storage_build_failure_is_a_failed_read_logged_at_warning`); the failing feature check test now expects `None` (renamed `test_a_failing_feature_check_is_a_failed_read_instead_of_an_error`; the feature check can raise only through `is_feature_enabled`, which I treat as a failed read, a consequence of moving it into the `try`). Removed `test_no_reader_gives_no_rows` (a storage with `reader=None` is not a real state; it would now raise AttributeError -> `None`). Existing tests already cover feature off -> `[]`, query raises -> `None`, healthy -> rows. `test_person_recent_sessions.py` `reader_or_none` tests pass unchanged.

## CR-004 done
Parameterised: worker.py `_fetch_meta(ev_window: dict[str, Any])`; `_assemble_inputs` (`ev_window`, `evidence`, `persons`, `web_people`, `est`, `static`: `dict[str, Any]`; `shared_cli: dict[str, dict[str, Any]]`; `by_key: dict[str, Spec]`; `people: list[list[Any]]`); analysis.py `read_postgres(started_job: dict[str, Any])`, `_run_job(job: dict[str, Any], before_start: Callable[[dict[str, Any]], ...], -> dict[str, Any])`; framework_sessions.py `aggregate_pg_rows(Iterable[Sequence[Any]])`, `merge_framework(Sequence[Sequence[Any]], Iterable[Sequence[Any]]) -> list[list[Any]]`, `merged: list[list[Any]]`; added `Any` to the typing import.

## Notes
- Other bare annotations outside this MR's changes left alone.
- `.codemie/` and doctor.json untouched; no git add/commit.
- Behaviour change worth knowing: after a degraded result expires and no complete copy exists, the next request waits for a synchronous compute instead of getting a stale ES-only copy (intended by CR-002).
