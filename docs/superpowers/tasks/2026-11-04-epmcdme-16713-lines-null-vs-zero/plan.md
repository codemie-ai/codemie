# EPMCDME-16713 Unknown lines are null, not 0 - Implementation Plan

**Goal:** `lines_daily` row present = lines known; API returns null for unknown lines, 0 for real zeros.
**Spec:** `spec.md` (approved). **Analysis:** `technical-analysis.md`. Rules from the arch review (`arch-review-brief.local.md`) are folded in below.
Commit per task using the repository's existing convention. No whole-suite gates or manual verification here; later stages own them.
Live tests: `tests/codemie/repository/cli_analytics/postgres/test_live_lines_daily.py` helpers (`_tool_end`, `_lines_metric`, `_session`), skipped without `TEST_LOCAL_PG_URL`.

### Task 1: Store zero-sum rows in lines_daily
- Modify `rollups.py:1763-1781`: delete both `HAVING sum(...) <> 0 ...` clauses of the `lines_daily` arms; update the comment at :1763 to say only `active_time_daily` omits zero rows. `active_time_daily` is untouched.
- Source selection (`_LINES_SESSION`, `_carries_lines`, `_hook_lines`) unchanged; mixed sessions keep sum-of-known.
- Test-first: yes - live tests in `test_live_lines_daily.py`: (a) numeric `lines_added: 0` session has a row summing 0; (b) OTel metric summing 0 has a row; (c) session with no line source has no row; (d) control session 84/5 unchanged; (e) an OTel zero row is replaced when numeric hook lines arrive later.

### Task 2: Reader: session-level "known" signal and non-zero fragment
- Modify `reader.py:636-680` and `:1087-1093`. Define one module-level non-zero fragment (`lines_added <> 0 OR lines_removed <> 0`).
- `get_lines_by_session`: return a row for every selected session having a `lines_daily` row on ANY day; sums are limited to the request window and coalesced to 0 (a known session with no row in the window reports 0, not unknown). Sessions with no row at all stay absent.
- `get_lines_totals` / `get_lines_daily` / `get_lines_by_user` keep returning NULL or no row when the window has no row. `get_lines_daily` also returns a `nonzero` boolean per day for `data_as_of`.
- `get_session_detail_scalars` stays NULL-when-no-row (not window bound).
- `get_class_lists(by="day")` (`reader.py:478`): add the non-zero fragment so classification is unchanged by zero rows.
- Leave `_LINES_EMAIL` (:138) as is: attributing a zero-line session to a developer is acceptable (arch review).
- Port `ports.py:271-277`: no new method; only update docstrings of changed readers.
- Test-first: yes - live tests: a two-day hook session with lines on day 1 and only a no-lines tool end on day 2 queried with a day-2 window returns known with 0; a zero-sum session is absent from the day class list while a non-zero one is present; totals for a window with only an unknown session are NULL.

### Task 3: Share the numeric lines_added predicate
- Modify `ingestor.py:206-228` (`_LINES_SOURCE_SQL`, `_LINES_COPY_DAYS_SQL`): build the `jsonb_typeof(...) = 'number'` predicate from `rollups._carries_lines` (expose a public alias) instead of copied text. `:215` only adds recompute keys when zero rows exist; verified harmless, no logic change.
- Test-first: no - behaviour is pinned by the existing switch/late-batch tests in `test_live_lines_daily.py`.

### Task 4: Nullable lines on the 7 response models
- Modify `rest_api/models/cli_analytics.py` (analysis lists the seven models): `lines_added`, `lines_removed`, `net_lines` become `int | None`; defaults that were `0` become `None`. Session row keeps only `net_lines`.
- Test-first: yes - model test in `tests/codemie/rest_api/` (or the existing cli analytics models test): each model validates with `None`, and `LocalAnalyticsRepositoryRow.model_json_schema()` shows `lines_added` as nullable (`anyOf` with null).

### Task 5: Handler helpers; sessions, session detail, repositories
- Modify `cli_analytics_handler.py`: add `_nsum(*values)` (None only when all inputs are None, else sum of the non-None) and `_nnet(added, removed)` (None when both None, else `_nsum(added) - _nsum(removed)` with Nones as 0). Use them at every site; no per-site hand-written rule.
- `_session_row` :830 and `get_session_detail` :1054-1077: session with no entry in `lines_by_session` / NULL scalars gets `net_lines=None`.
- `_new_repository_bucket` :233 seeds lines `None`; :364-365 accumulate with `_nsum`; `_finalize_repository_bucket` :263 uses `_nnet`; `_group_branches_into_repositories` :294-333 adds branch values null-safely (:321) and recomputes `net_lines` with `_nnet` (:328). Check `get_repositories` (~:731) passes the same inputs.
- Test-first: yes - handler tests in `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` with mocked reader: unknown session -> `net_lines` None; known zero -> 0; repository/branch rows for all-unknown (None), some-unknown (sum of known) and none-unknown inputs; `_nsum`/`_nnet` unit cases.

### Task 6: Handler: users, overview, efficiency
- `get_users` :642-683: user without a lines row gets None (not `.get(name, 0)`).
- `get_overview` :466-535: KPI `net_lines` via `_nnet` on totals (None when totals NULL); daily bucket `net_lines` None for days with no row; `data_as_of` (:547) uses only days whose `nonzero` is true.
- `get_efficiency` :1250-1336: `code_changes.net_lines` via `_nnet`; `code_ids` (:1299-1304) must test non-zero lines explicitly (zero-sum known sessions are not "code change").
- Test-first: yes - handler tests (same file, plus `test_cli_analytics_class_lists.py` for `code_ids`): users/overview KPI/daily/efficiency for all-unknown, some-unknown, none-unknown; zero-sum session excluded from `code_ids`; `data_as_of` ignores zero-only days. Update existing assertions that expected 0 for missing lines.

### Task 7: Docs and rollout decision
- Modify `.ai-run/guides/integration/cli-analytics-storage.md:209` and `cli-analytics-events.md:439-442`: row present means known; API returns null when unknown; zero-sum rows are stored for `lines_daily`.
- Document the rollout in the same guide (no new code; `postgres/backfill.py` exists): after ALL instances run the new code, run `python -m codemie.repository.cli_analytics.postgres.backfill --from <today - raw_retention_days> --to <today>` in batches of days (it queues every (day, session) with raw rows; `--now` takes the refresher lock). Run it once after the rollout completes, because an old instance recomputing a key can drop its zero row. Count keys first (`rollup_dirty` queue size) to size the run. Days past raw retention keep their stored rollups, so older real-zero sessions show null; AC2/AC3 hold for data recomputed after deploy.
- State residual risks: partly unknown aggregates show the sum of known sessions (accepted, AR-3, not reopened); coverage indicator for mixed sessions is a follow-up to raise on the ticket; UI must handle null.
- Test-first: no - documentation only.

## Negative-constraint pass
negative-constraints checked against spec Non-goals: no UI change (no task touches codemie-ui); no root-cause work; no coverage indicator (Task 5-6 return only null/sum); no `lines_added`/`lines_removed` on the session row (Task 4 keeps `net_lines` only); source selection and `active_time_daily` zero-row rule untouched (Tasks 1, 3 keep them); `config/customer/customer-config.yaml` not touched; an aggregate is never null-if-any-unknown (Task 5 `_nsum`).
