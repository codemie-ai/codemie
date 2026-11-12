# EPMCDME-17051: Users, User Charts and Cost aggregated in SQL — Spec

**Story**: `story.md` (approved; criteria and D1–D12 apply to the three requests below)
**Size**: L (21/36)
**Shared contract**: `docs/stories/2026-11-09-cli-analytics-python-load/shared-cost-sql-contract.md` (updated by this spec)

## Problem

`GET /users`, `/user-charts` and `/cost` load every session of the window into Python. They price per-session-model rows there and run `buckets()` (a linear scan over every priced row) once per user and once per model (`funding_buckets.py:68`, `cli_analytics_handler.py:612-664, 668-759`). `/users` paginates only at the end (`H:749-752`). Its 9 statements run through `asyncio.gather`, each on its own connection (`R:704-708`), against a pool of 8. On these endpoints a statement timeout surfaces as 503 (`cli_analytics.py:282-295`). `get_users` fetches `get_file_facts_by_session` and never reads it (`H:677,687`).

## Goal

Each of the three requests runs on one held connection and gets back rows already aggregated to what it returns. The response stays as it is, with the parity rules of story D1/D11. A shared grouped SQL cost module is delivered that EPMCDME-17052 reuses.

## Design

**Shared module** `src/codemie/repository/cli_analytics/postgres/grouped_cost_sql.py`. It builds SQL only and is used inside a statement that already defines `sel`, as `session_cost_key_sql` is. It reuses `model_token_groups`, `KIND_TOKENS`, `RATE_COLUMNS` and `rates_params` unchanged.

The SQL pricing terms become public builders in `cost_key_sql.py`, and both `session_cost_key_sql` and `priced_groups_cte` call them, so each pricing rule exists once. They are today's private `_cost_key_terms()` and `_RATES_JOIN` (`cost_key_sql.py:134-147`), plus a new cache-read term and a new unpriced test. Sessions' results do not change.

The module has two public levels, and its consumers never price tokens themselves.

The first level, `priced_groups_cte()`, returns the CTE `priced_groups AS MATERIALIZED (...)`: one row per (session, raw model) for every session of `sel` that has a token row. There is no spend filter. A session with no token row has no row here. Each row is priced once with the existing clamp; its columns are pinned below.

The second level, `grouped_cost_sql(groupings)`, builds the derived table `gc` over `priced_groups` and needs that CTE in the same statement. It returns every requested grouping from that one priced set.

```python
def priced_groups_cte() -> str: ...
# priced_groups(session_id, model_name, normalized_model_name, user, project, repository, branch,
#               funding_route text,      -- NULL or outside FUNDING_ROUTES -> 'unknown'
#               personal_billed bool, spend bool,   -- spend: not Personal-paid (_spend, cost_key_sql.py:54)
#               has_cost_daily bool,     -- the session has at least one cost_daily row
#               cost numeric,            -- round(_, 9) at this grain
#               cache_read_cost numeric, -- cache-read part, round(_, 9) at this grain
#               unpriced bool)           -- a kind with tokens > 0 and no rate

GroupKey = Literal["user", "project", "repository", "branch", "model", "funding_route", "personal_billed"]

@dataclass(frozen=True)
class Grouping:
    name: str                       # returned in column `grouping`
    keys: Sequence[GroupKey]        # [] = whole window
    scope: Literal["spend", "all"]  # spend: WHERE spend
    cost_daily_only: bool = False   # WHERE has_cost_daily

def grouped_cost_sql(groupings: Sequence[Grouping]) -> str: ...
# derived table gc(grouping text, <one column per key used by any grouping, NULL where unused>,
#                  cost_usd numeric, cache_read_cost_usd numeric, session_count bigint,
#                  unpriced_models text[], cost_incomplete bool)
```

- `user` is the developer column of `sel`. `model` is `normalized_model_name`. A blank model is left out of `model` groupings only, as `by_model` does today. `user`, `project`, `repository`, `branch` and `funding_route` are taken from `sel` at the session grain.
- `session_count` counts distinct sessions of the group and is used only for buckets. A row's own session count comes from its row source.
- `unpriced_models` lists the raw names of rows with `unpriced`, sorted `COLLATE "C"` (code-point order, as Python sorts at `funding_buckets.py:59`).
- To get per-session values, sum `priced_groups` by `session_id` (with `WHERE spend` for spend money).

**The developer column.** `_sessions_cte` gets an opt-in `with_developer` option. With it on, `sel` carries `developer`: today's `_DEVELOPER` text (`R:387-416`), computed once per session. Existing callers keep today's `sel`. `LocalAnalyticsFilter` gets an optional `developers` list; `_filter_conditions` applies it to that column. Unlike `f.users` (`R:716-717`), which matches only the session's own email, this keeps the sessions whose developer comes from a fallback cost or lines email.

**Statement sequence.** Each request runs these steps, in order, on its one connection.

**Cost.**
1. Rates: `get_cost_models(f)` over the same `f`, then `model_rates_for`.
2. One money statement prices the window once and returns these groupings:
   - spend: `[]`, `[user]` (`cost_daily_only`), `[model]`;
   - all: `[funding_route]`, `[personal_billed]`, `[user, funding_route]` and `[user, personal_billed]` (both `cost_daily_only`), `[model, funding_route]`, `[model, personal_billed]`.
3. `get_cost_kpis`, the two `get_class_lists` calls and `get_cost_by_user`, the last now without `session_ids`.

`cost_by_user` membership still comes from `get_cost_by_user`, and the money is joined on by user. Today the per-user money and buckets cover only the sessions that `get_cost_by_user` lists, which are those with a `cost_daily` row (`H:636-638`). `cost_daily_only` keeps that set: a session that has only subagent-totals rows still counts in the totals, per model and in the window buckets, but in no user's figure. `cost_by_model` lists the models that have spend rows. `models_without_spend` lists the models that appear in the all-scope bucket groupings but have no spend rows. Both keep today's sorts.

**Users and User Charts.** Each request makes its own SQL calls; the two requests share nothing.
1. Rates, as in Cost.
2. One money and ranking statement.
   - `sel` with the developer column; `priced_groups` once.
   - `gc` groupings: spend `[]` and `[user]`; all `[funding_route]`, `[personal_billed]`, `[user, funding_route]` and `[user, personal_billed]`.
   - The ranking is lean: the distinct developers of `sel` (the same membership `get_users` has today) LEFT JOIN the spend `[user]` cost. Rows are ordered `round(cost_usd, 9) DESC, developer COLLATE "C"`, with `count(*) OVER ()` as `total_count`.
   - `/users` applies LIMIT/OFFSET here, and the per-user bucket rows are returned only for the page's developers.
   - `avg_session_duration_ms` is computed over every session of `sel`, with NULL skipped (`H:736-745`), in this statement or in a window statement of its own (the plan picks).
3. Page facts, restricted by `developers` = the page's developers (`/user-charts`: no restriction):
   - from `get_users`: tokens, `session_count`, `top_model` and class lists, with no `session_ids`, no producer `cost_usd` and no ORDER BY;
   - per-developer sums of turns, tool calls and tool successes, with today's NULL-as-0 rule;
   - net lines, last active and daily activity.

   Each fact keeps today's attribution. The lines statement still attributes by `_session_developer("l.session_email")` (`R:1053`) and is restricted to the page's names on that same value. The file-facts fetch is dropped.

**metadata.unpriced** is the `unpriced_models` of the spend `[]` grouping (the same scope the spend pricer covers today). The estimate buckets never feed it.

**Rounding parity.** Every value is a numeric sum of costs rounded to 9 decimals per (session, raw model). Python today sums unrounded floats (`total`, `by_session`, `cache_read`, `cache_read_by_session` in `session_money.py:94`). The gap is at most 5e-10 per (session, model) row. At 600K sessions and a few models each that is under $0.001 for a whole-window total, so it stays within $0.01.
- **Cost above zero.** Counts that depend on cost being above zero use the rounded value (D11). A session differs from Python only if every one of its groups priced below $5e-10. That needs a single token at a rate under $0.0005 per million tokens, which no catalog rate is near. If the baseline shows such a session, it is reported, not hidden.
- **cache_read_cost.** It is rounded separately from `cost`, so `cost - cache_read_cost` can differ from today's split by the same 5e-10. `bloat_pct` (17052) is derived from these dollar figures.
- **Sessions sort.** A session's sum of `cost` can differ from the Sessions sort `cost_key` (rounded once per session) by the same margin. Only Sessions reads `cost_key`.

**Connections and timeouts.** Each handler runs its statements in sequence inside `reader.bounded()` (`R:679`), one connection per request. Row building happens after the block.
- **Request budget.** The block runs inside `asyncio.timeout(budget)`, with the budget below the 600 s ingress proxy-read-timeout (`deploy-templates/values.yaml:273`). The plan sets the value and confirms that no shorter proxy sits in front of the API on the target environment. When the budget runs out, the current statement is cancelled and the connection goes back to the pool.
- **Timeout mapping.** `handle_errors` gets one opt-in parameter, `timeout_504`. With it set, `asyncpg.QueryCanceledError` and `TimeoutError` are caught before the 503 branches (`cli_analytics.py:282-297`). The response is a 504 whose message names the time limit and `CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS`.
- **Who passes it.** The three endpoints pass the parameter. `/sessions` replaces its inline mapping (`cli_analytics.py:745-765`) with the parameter and keeps its message text.

**Docs.** Mark the storage guide line "dashboard endpoints keep their own acquires" as superseded for these three requests. Update the shared contract (done with this spec).

## Decisions

- Users tab: split the work with no shared state, over an in-flight share or a short per-pod cache: `/users` ranks in SQL and builds only its page; only `/user-charts` builds every user's row. The per-user cost ranking runs once per request. — user
- Module signature: several groupings (each with its own scope) from one pricing pass, with a `grouping` column, over one grouping per call. This changes the proposed contract and needs EPMCDME-17052's developer to agree. — user
- Pool: one connection per request through `bounded()`, over a pod-wide dashboard limit. The k-tabs test in the plan decides whether that is enough. — user
- Two public levels (`priced_groups` per (session, raw model), and `gc` over it), over `gc` alone. 17052 needs per-session cost and cache-read values without pricing a second time. — user
- `cache_read_cost` on `priced_groups` and `cache_read_cost_usd` on `gc`, over leaving the cache-read part out of the module. — user
- The columns of both levels are the contract, and a consumer never prices tokens itself, over endpoint-local pricing. — user
- Rounding: 9 decimals per (session, raw model), summed as numeric, over rounding per session. A per-session rounded value cannot be split by model, and this is the clamp's grain.
- `/repositories` unpriced and window buckets (17052): they cover every session that passes the filters and `search`, before pagination (a spend `[]` grouping and the window bucket groupings over `sel` with the search condition applied), never the page rows, and no LIMIT inside the module. With `include_branches=true` the groupings are keyed by `project, repository` (and `branch`); the paged call keys by `repository` only.
- Connection ownership: each handler owns `bounded()` and the module builds SQL only, over the module acquiring a connection itself.
- Parity tests: `test_live_grouped_cost.py` next to `test_live_session_cost_key.py`, covering every pricing case including a model with no rates and an unknown funding route, as the contract proposed. 17052 reuses it.
- Per-user and per-model funding and personal groupings are added to the contract, over keeping only the whole-window buckets. Today's response carries them (`H:638,646`).
- Row membership comes from today's per-user queries, over the money groupings.
- New reader methods, over changing `get_session_model_costs`, `get_session_funding_costs` or `price_bucket_rows`, which Efficiency, Overview and Sessions still use.
- 504 mapping on these three endpoints only, over changing `handle_errors` for every endpoint.
- Request-time pricing, over a stored per-session rollup (D12 fallback only).
- Stored token groups (changed after approval, by the user): at 600K sessions request-time pricing took 33 to 57 s per money statement (limit 30 s) after the SQL rewrite, an index, ANALYZE, work_mem and pre-aggregation did not help, and a scratch experiment showed about 18 s per request with the groups stored. So the D12 fallback is used, in this ticket and on this branch: a table `session_model_groups` of rate-independent token sums per (session, raw model), kept by the existing 16427 stamp, backfill and kill-switch machinery, read by `priced_groups` with a live fallback for unstamped sessions. `priced_groups` and `gc` output columns do not change. Plan: `docs/stories/2026-11-09-cli-analytics-python-load/stored-model-groups-plan.md`. — user
- Baseline dataset (changed after approval, by the user): output parity (criteria 6 and 7) is recorded and checked on a 300K-session / 8,000-user scratch DB, because recording the current code on 600K sessions took about 30 minutes per request. Speed and the k = 3 volume test (criteria 2, 4 and 13) stay on the 600K-session DB; the pre-change timing there is measured in the final volume task by running the baseline recorder on the commit before the handler changes. — user
- No use of the stored `session_dims.cost_key`, over reading it, because it is a whole-session value.
- A fixed statement sequence per request (rates step, one money statement that prices once and ranks, then page facts), over leaving the layout to the plan. Proposed by the spec writer in answer to AR-1 (review plan A); to be confirmed at the gate.
- The developer is computed once per session as an opt-in `sel` column with a `developers` filter, over restricting the page through `f.users` or re-deriving the developer in each statement. Proposed by the spec writer in answer to AR-1 (review plan A); to be confirmed at the gate.
- `/cost` per-user money and buckets keep today's set of sessions (those with a `cost_daily` row, via `cost_daily_only`), over including sessions that have only subagent totals. Answers AR-2.
- 504 through an opt-in `handle_errors(timeout_504=...)` parameter, which `/sessions` also adopts, over copying the `/sessions` inline mapping into each endpoint. Answers AR-3.
- A per-request `asyncio.timeout` budget below the 600 s ingress proxy-read-timeout, over relying on per-statement limits only. — user, answers AR-4
- k = 3 Users tabs and 3 Cost tabs open at once per pod, background jobs running, with one connection per request. If that load test fails, a pod-wide limit becomes a new decision. — user, answers
- k = 3 load test failed at 600K (381 of 1,519 ingest calls got 503, 4 of 9 dashboard requests got 504), so the user decided a per-pod cap on concurrent /users, /user-charts and /cost requests (scoped to these three, with a queue; configurable; pool size minus 2 as the default). It replaces the non-goal below. — user AR-5
- `/efficiency` per-session cache read reads `priced_groups WHERE spend`, and `project_name` comes from the row source, over the earlier contract rows. Answers AR-6.
- "Every session of `sel` that has a token row", and `gc.session_count` as the bucket count only, over "all sessions included". Answers AR-7.
- Shared public pricing builders in `cost_key_sql.py`, over a second copy of the priced expression in the new module. Answers AR-8.
- `unpriced_models` sorted `COLLATE "C"`, over the database default collation. Answers AR-9.
- 17052 deletes the old Python bucket path once its endpoints move, over leaving its removal unassigned. Answers AR-10.

## Acceptance Criteria

1. Before any change, the outputs of `/users?page=0&per_page=50`, `/user-charts` and `/cost`, and their timings, are recorded from the current code on the scratch dataset (`postgresql://postgres:scratch@127.0.0.1:55420/scratch`).
   - Shape: about 8,000 users and about 600K sessions over 90 days, 200K per 30-day month. The scratch DB is reloaded to that size with `scripts/cli_analytics_bench/endpoint_data.py`.
   - It contains unpriced models, Personal-paid sessions, unknown durations and near-equal costs.
   - It also contains a session with only subagent-totals rows, a developer whose name comes from a fallback cost or lines email, and two unpriced model names that differ only in case.
   - Criteria 2, 4, 6 and 13 run on this same dataset.
2. At about 8,000 users and 600K sessions over 90 days, each request returns with no 503 or 504.
3. Each request holds at most one pool connection at a time, and none of its statements runs outside `bounded()`.
4. With k = 3 (three Users tabs and three Cost tabs open at once on one pod) while ingestion and the background jobs run:
   - ingestion returns no 503;
   - no request is slower than the timing recorded in criterion 1 on that dataset;
   - every one of those Users and Cost tab requests returns with no 503 or 504.
5. No statement of these requests returns per-session or per-(session, model) rows or `session_ids` arrays, and no `buckets()` scan runs.
6. Against the baseline:
   - every dollar figure matches within $0.01;
   - counts and row membership (users, models, `models_without_spend`) match exactly;
   - every other field matches exactly;
   - order matches exactly, except rows whose costs differ by less than $0.000001.
7. `metadata.unpriced`, and every bucket's `unpriced_models` and `cost_incomplete`, match the baseline exactly.
8. A filter matching no sessions returns today's empty response.
9. A statement timeout, or the request budget running out, returns 504 with a message naming the time limit, and no connection stays held afterwards.
10. The OpenAPI baseline (`tests/codemie/rest_api/router_guards/baselines/openapi.json`) is unchanged.
11. `test_live_grouped_cost.py` covers every pricing case (including a mixed-case model pair) and checks:
    - every `gc` grouping (`cost_usd`, `cache_read_cost_usd`, `session_count`, `unpriced_models`) equals the Python pricer within 1e-9 per group;
    - `priced_groups` summed by `session_id` equals `price_session_models` `by_session` and `cache_read_by_session` (spend rows) within 1e-9 per session;
    - the window sums equal `total` and `cache_read`.
12. `get_users` no longer fetches file facts.
13. While `/users`, `/user-charts` and `/cost` run at volume, ingestion requests made at the same time are delayed by no more than a threshold that the plan sets next to k. The delay is measured against a run with no dashboard request.
14. The volume test reports wall time per request next to the time of each statement.

## Non-goals

- Overview, Repositories and Efficiency (EPMCDME-17052); Tools and Activity.
- Pricing rules, the kind table, model-name normalisation, and stored usage data.
- A stored per-user rollup, and a stored cost (as opposed to token sums) unless the spike shows pricing at read time costs more than about 3 s. (The stored token groups table is in scope, see Decisions.)
- Clipping facts to the selected days.
- Any dashboard or API-field change other than 503 becoming 504 on timeout.
- Changing the shared readers or helpers still used by other endpoints.
- A pod-wide limit on connections for other dashboard requests (the cap added in this ticket covers /users, /user-charts and /cost only).
- A fixed millisecond target for these requests (story D2). Speed is judged against the recorded baseline, and the criterion 13 threshold limits ingestion delay only.

## Open risks

- `/user-charts` returns every user with their daily activity (about 8,000 users times up to 90 days). SQL cannot shrink that, and the response build plus `_attach_user_ids` still run on every row.
- The two-level signature needs EPMCDME-17052's agreement.
- No timings exist at volume yet.
- `MATERIALIZED` on `priced_groups` holds every (session, raw model) row of the window in work_mem, or spills to disk. The plan measures it at volume.
- With k = 3, up to nine dashboard requests can want a connection while the background jobs hold two of the eight. Passing depends on how long each request holds its connection, and a failure becomes a new pool decision (AR-5).
- Statement times at 600K sessions are not measured: the money and ranking statement, the token read in the IN form versus the join form (`R:1617-1620`), and daily activity.
