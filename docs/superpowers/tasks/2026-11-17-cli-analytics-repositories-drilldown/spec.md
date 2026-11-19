# Spec: CLI Analytics Repositories drill-down (EPMCDME-17349) - backend

Backend half of D1. All routes are in the CLI analytics read path under `/v1/analytics/cli-analytics` (router `src/codemie/rest_api/routers/cli_analytics.py`, handler `cli_analytics_handler.py`, reader `repository/cli_analytics/postgres/reader.py`, `project_page_sql.py`, models `rest_api/models/cli_analytics.py`). Each modal level loads only its own data. The FE contract is `fe-contract.md` in this folder.

## Behavior

1. **`GET /projects` gets an opt-out `include_repositories` (bool query param, default `true`).** With the default the response is exactly what it was before this branch: each row carries the nested `repositories` (with branches), the search matches a project or repository name, and the Unattributed project is ranked last and counted. With `include_repositories=false` (what the UI sends) the rows are totals only: metric fields and `repository_count`, `repositories` is `null` (`LocalAnalyticsProjectRow.repositories` is optional, one model for both modes), and no nested repository or branch rows are built. (story AC1)
2. **With `include_repositories=false`, `/projects` search matches project name only.** The legacy search (`_LEGACY_SEARCH_CONDITION`, project or repository name) stays for the default; the drill-down mode uses `_SEARCH_CONDITION` in `project_page_sql.py` (reader flag `drilldown`). Totals follow the search as today. (story AC8)
3. **`GET /repositories` serves the project modal, without regrouping, and gets an opt-in `unattributed_last` (bool query param, default `false`).** With the default the paged response is exactly what it was before this branch: the no-repository group (`repository` null) is ranked and counted like any row (inside `per_page` and `total_count`). With `unattributed_last=true` (what the UI sends on the project modal) the Unattributed row rule below applies to it; it has no effect on the unpaged forced-all call or with `include_branches=true`. The project filter is applied to the sessions before grouping (`reader.py:1170-1173`, shared sessions CTE), so `projects=P` with repository-only grouping already gives P-only metrics, facts and estimates. The grouping stays as today. A comma list of projects is allowed and gives one combined view across them (a repository shared by them is summed; `project_name` stays its most frequent project). The FE sends exactly one project. (story AC2, AC3)
4. **No-project scope.** `/repositories` and `/branches` accept `is_project_unattributed=true` and build their scope with `FilterParams.resolve(user, project_unattributed)` (`cli_analytics.py:416-470`), not `resolve_sessions`. A non-super-admin gets an **empty list, HTTP 200** (`deny_all`), never an error and never their own sessions. This is stricter than `/sessions`, which falls back to own-only sessions; that difference is intentional, since these lists are shared aggregates. Before this branch `/repositories` silently ignored the flag because the router never passed it; honouring it is the one intentional behaviour change of the default `/repositories`.
5. **New paged branches list**: `GET /branches` with required `repository` query param (a repository name can contain `/`, so it is a query param, not a path segment) plus the same scope params as `/repositories` (`projects`, `is_project_unattributed`, `FilterParams`). `repository` is ANDed with `FilterParams.repositories`. Rows are `LocalAnalyticsRepositoryBranchRow`-style: `branch` (null = no branch) plus the metric set, paged in SQL. (story AC4, AC5)
6. **Unattributed row rule** (`/projects` with `include_repositories=false`, `/repositories` with `unattributed_last=true`, `/branches`). Pages are zero-based, `per_page` is the caller's (FE sends 10).
   - `total_count` and `has_more` count named rows only. The null-named row is excluded from ranking and from SQL paging.
   - It is appended once, as the last row of the last page, outside `per_page`, only when such sessions exist. Other pages never carry it; a page beyond the last returns no rows.
   - A list with only a null-named group returns that one row on page 0, with `total_count` 0 and `has_more` false.
   - It is absent whenever `search` is set (a null name cannot match).
   - A list with nothing returns `rows: []`, `total_count` 0. (story AC7, AC11)
7. **`GET /sessions` gets an optional additive `is_branch_unattributed` flag.** When true, only sessions with no branch are returned. It ANDs with `projects`, `is_project_unattributed` and `repositories`, so the opened sessions match the row's count. It requires `repositories` (422 without) and is invalid together with `branch` (422). Without the flag nothing changes. (story AC7)
8. **Ordering** stays as today for each list: projects by spend, repositories by session count then spend then name, branches by session count then spend then name.
9. **Viewer scope** follows `FilterParams.resolve`: non-super-admins are intersected with their admin projects, and no-project data is super-admin only. The three list routes use `_admin_gate`, `handle_errors` and `_statement_timeout_as_504` (`/projects` has no wrapper today and gets one).
10. **OpenAPI baseline** `tests/codemie/rest_api/router_guards/baselines/openapi.json` is regenerated.

## Acceptance criteria

- AC-B1: `/projects?include_repositories=false` rows carry `repositories: null` and no nested repository or branch rows are built; without the param the rows keep their nested `repositories` as before. (story AC1)
- AC-B2: `/projects?include_repositories=false&search=x` matches project names only (without the param the search still matches repository names); a repository named x under another project does not match; no null-named row is returned under a search. (story AC8)
- AC-B3: `/repositories?projects=P&page=0&per_page=10&unattributed_last=true` returns only repositories with sessions in P, with P-only metrics, tested with two projects sharing a repository. (story AC2, AC3)
- AC-B4: `/repositories?is_project_unattributed=true` and `/branches?...&is_project_unattributed=true` return the no-project data for a super-admin and an empty list with HTTP 200 for anyone else.
- AC-B5: `/branches?repository=R&projects=P&page=0&per_page=10` returns the first 10 branches of R in P with metrics and pagination; `repository` ANDs with `repositories`. (story AC4, AC5)
- AC-B6: In the three lists (`/projects` with `include_repositories=false`, `/repositories` with `unattributed_last=true`, `/branches`) a null-named row appears exactly once, as the last row of the last page, never counted in `total_count`, absent when there are no such sessions or when `search` is set. A list holding only that row returns it with `total_count` 0 and `has_more` false; a page beyond the last returns no rows. (story AC7)
- AC-B7: `/sessions?repositories=R&projects=P&is_branch_unattributed=true` returns only sessions of R in P with no branch; without `repositories` or with `branch` it returns 422. (story AC7)
- AC-B8: Existing unpaged `/repositories` calls (no `page`/`per_page`) return as before, and so does the paged call without `unattributed_last`: the no-repository group is ranked and counted like any row (test pins it).
- AC-B9: OpenAPI baseline matches the code; handler, ordering, router and reader tests are updated and pass. Tests pin name-only search, unattributed placement, the two-project case and the non-super-admin result.
- AC-B10: `fe-contract.md` documents request/response, page base, the null-name convention, the access outcome and backward compatibility.

## Non-goals

- Search or filters in the repository and branch lists. (story NG1)
- Changes to the branch modal or its sessions table. (story NG2)
- Client-side branch paging. (story NG3)
- FE implementation, and the FE modal stack, error and empty states (AC9-AC11).
- A summary endpoint: Summary comes from the clicked row (D7).
- Caching of these routes.
- Changing the pagination envelope or the zero-based `page` convention.
- Narrowing the window-wide funding read of `/projects` (`get_session_funding_costs`, which prices the estimate buckets of the whole window or search match). In `include_repositories=false` the six per-session reads and the nested repository and branch rows are gone: the page totals come from one grouped statement (`get_project_totals`, per project, over the page's sessions, priced in SQL).

## Decisions

- `repositories` on the `/projects` row is opt-out via `include_repositories` (default `true`, legacy response) over removing it: the default response stays unchanged for existing consumers, and the UI sends `include_repositories=false` to skip nested data it does not show. The null-row placement and name-only search (D2) apply to the opt-out mode only; the default keeps the legacy Unattributed placement and name-or-repository search.
- Cache keys: `/projects` is not served through `cached_dashboard`, so the two modes cannot share a cached answer today; if it is ever cached, `include_repositories` must be passed as a key `extra` (pinned by a `dashboard_key` test).
- `/repositories` keeps its default contract and the null-row placement is opt-in via `unattributed_last` (default `false`), as `include_repositories` does for `/projects`: existing consumers see no change. The reader switches only the ranking order, the paging filter and the count of the one statement; cache keys are not affected because `/repositories` is not served through `cached_dashboard`.
- Project modal uses existing `/repositories` with its repository-only grouping over regrouping by (project, repository): the project filter is already applied before grouping, so regrouping would add a second mode for no gain.
- New `/branches` with `repository` as a query param over a path segment: repository names can contain `/`.
- No-project scope on `/repositories` and `/branches` uses `resolve` and returns an empty list for non-super-admins over copying `/sessions` own-only behavior: it avoids widening access and mixing personal sessions into a shared list.
- Unattributed row appended on the last page, outside `per_page`, over ranking it inside the page: D9.
- `total_count` and `has_more` count named rows only over counting the Unattributed row: pages stay stable at `per_page`.
- Unattributed row absent under search, over always shown: a null name cannot match.
- Zero-based `page` kept over one-based: the FE converts, as the existing convention.
- Existing per-list ordering kept over unifying on spend: not asked for.
- `/repositories` stays additive: forced-all unpaged behavior and `include_branches` kept over removing them. Consumers: FE `OverviewView` (paged, 10) and the 17301 branch loader `fetchProjectBranches` (unpaged), which this story removes. Remove the unpaged mode when no consumer is left.
- Unattributed-repository row opens sessions only (existing `is_unattributed`), with no branch list over a null-repository `/branches` form: no AC asks for it.
- Add optional `is_branch_unattributed` to `/sessions` over leaving the Unattributed branch row unclickable — user, answers Q1.
- `/repositories` honours `is_project_unattributed` over leaving the unattributed project without a repository list — user, answers Q2.

## Implementation constraints (for the plan)

- Branch list gets its own reader method and port entry (`get_branch_ranking`), not another flag on `get_repository_ranking`.
- The null-row rule is applied once in the handler: SQL pages and counts named rows only, a one-row read finds the null group, one helper beside `_pagination` appends it on the last page. Page, count and null-group presence are read in one statement; the null row is best effort across requests.
- Measure `EXPLAIN ANALYZE` and p95 of `/repositories?projects=<largest>&per_page=10` and `/branches` on the largest repository before release.

## Assumptions

- The FE converts its 1-based page to the backend's zero-based `page`; the conversion point was not read.
- Per-branch rows need only the metric fields of the existing branch row model, not estimate buckets.
- The null project group in `/projects` can be separated from the ranking in `project_page_sql.py` (not read in full).
