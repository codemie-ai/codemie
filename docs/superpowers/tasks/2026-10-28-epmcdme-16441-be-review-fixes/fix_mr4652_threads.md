# Fix: MR !4652 review threads (3 code fixes + 1 spec amendment)

Branch EPMCDME-16441-backend-session-end-subagent-cost. Commit prefix would be `EPMCDME-16441:` (the architect
commits; you do not touch git). Follow `.ai-run/guides/testing/testing-patterns.md` for tests and run only the
narrow suites listed under Gates. English only.

## Fix 1 — offset overflow (thread 062ed3ab, reply by sviatoslav_likhtarchyk)

`src/codemie/repository/cli_analytics/postgres/reader.py:774` (`get_session_page`): `params["offset"] = page * per_page`
has no bound. A huge `?page=` overflows int64 in asyncpg (`LIMIT $limit::bigint OFFSET $offset::bigint`, ~line 810)
and the endpoint returns 500. The router (`src/codemie/rest_api/routers/cli_analytics.py:614`) has `page: Query(None, ge=0)`
without `le=`.

Do: clamp the offset in `get_session_page`, e.g. `min(page * per_page, 2**62)` (name the constant), so a huge page
returns an empty page. Do NOT add `le=` to the router (API contract change). The Python paging path slices safely,
leave it. Test in `tests/codemie/repository/cli_analytics/postgres/test_reader.py`: with a huge `page` the bound
`offset` param is the clamped value (follow the existing `_ArgRecorder`/`_SqlRecorder` style used for
`get_session_page`; if none exists for it, mirror the nearest one).

## Fix 2 — "cost unavailable" log counts unlisted sessions (thread 3e35cfd2, point 2)

`src/codemie/service/analytics/handlers/cli_analytics_handler.py` `get_sessions`: `_log_unavailable_cost` is called
at ~line 827 with `session_ids` from `cost_facts` (~815), BEFORE `_filter_sessions` (~842) and `_page_sessions`
(~851). On the SQL-paged path `cost_facts` is already one page so it is right there; on the Python-paged paths
(`cost_usd` sort, `framework` filter) it counts sessions that are not listed. Spec and guide
(`.ai-run/guides/integration/cli-analytics-storage.md` ~line 320) say "sessions listed".

Do: move the call after `_page_sessions`, computed from the trace/session ids of the page rows actually returned
(`priced_sessions` and `model_rows_by_session` are already in scope). Test in
`tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` (existing coverage ~175-177 is the SQL
path with 2 no-token sessions): add a case for the `cost_usd` sort or `framework` filter where more sessions are
fetched than listed and the logged count equals the listed page only.

## Fix 3 — session detail tokens omit subagent totals (thread 84581316)

`get_session_detail_cost` (`reader.py` ~885-895) sums tokens from `_cost_rows` only; the list and
`get_session_detail_model_costs` (~909) use `_token_rows`, which adds the totals-only `session_usage` rows. The
handler builds the detail's four token columns from `cost` (`cli_analytics_handler.py` ~934-937, 955-958).

Do (preferred, no reader change): in `get_session_detail`, take input/output/cache token columns from the sum of
`model_cost_rows` (the `_token_rows`-based result); keep `cost` only for what it still supplies (e.g. `model_name`).
Add a handler test with a totals-only (subagent) session where the detail tokens equal the model-cost-row sums, and,
if the live-test file `tests/codemie/repository/cli_analytics/postgres/test_live_session_subagent_cost.py` has a
suitable seed helper, a live test that list and detail tokens agree for a totals-only session (it may skip without a
DB; say so in the CHECKPOINT). If `.ai-run/guides/integration/cli-analytics-storage.md` (~line 331, accepted
residuals) mentions the detail tokens, keep it consistent.

## Spec amendment — discard log line carries session_count / session_ids (thread 7282b2f0)

Decision (owner): keep the fields, amend the OLD spec; no code change in `gate_discards.py` / `ingestor.py`.
`docs/superpowers/tasks/2026-10-27-epmcdme-16441/spec.md` Design 2 (~line 21: "No … raw header or body values")
and AC-E (~line 38) list only route, reason, dropped count, keyed user hash. The newer
`docs/superpowers/tasks/2026-10-28-epmcdme-16441-be-review-fixes/spec.md` (lines ~11-12, 24, AC1) allows
`session_count` and `session_ids`. Add a short amendment to Design 2 and AC-E of the OLD spec: the discard line may
also carry `session_count` and up to 3 `session_ids` taken from the request body, which are client-supplied text,
sanitized, capped at 64 characters each, de-duplicated; still no email, project name, raw user id or raw header; the
line is not rate limited (see storage guide ~line 108). Point to the newer spec's AC1. Keep it to a few lines.

## Out of scope

Everything else: no router change, no `gate_discards.py`/`ingestor.py` change, no config/migration/dependency
change, no change to threads already handled (pagination, `cost_available` rule, CR-002, CR-003).

## Gates

- ruff format --check and ruff check on touched files (commands in `.ai-run/guides/quality-gates.md`; no repo-wide `--fix`).
- Narrow tests: `tests/codemie/repository/cli_analytics/postgres/test_reader.py`, `tests/codemie/service/analytics/handlers/`, plus any file you added a test to. The live suite needs Docker and is flaky under `-n 3` by itself; do not run it.
- No git mutations.

## Report

Write `docs/superpowers/tasks/2026-10-28-epmcdme-16441-be-review-fixes/fix_mr4652_threads.CHECKPOINT.md`: per fix, files
changed (file:line), tests added (name), exact commands with totals, anything not done or uncertain.
