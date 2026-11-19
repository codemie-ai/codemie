# CLI Analytics Repositories Drill-down (EPMCDME-17349, backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Totals-only `/projects` with name-only search, project-scoped paged `/repositories`, new paged `/branches`, Unattributed row rule, `is_branch_unattributed` on `/sessions`.

**Architecture:** All in the CLI analytics read path: router -> handler -> reader port -> Postgres reader/SQL -> models. The null-row rule lives once in the handler; SQL pages and counts named rows only.

**Spec:** `spec.md` in this folder (context: `technical-analysis.md`, `fe-contract.md`).

Commit per task using the repository's existing convention. Guides: `.ai-run/guides/api/rest-api-patterns.md`, `.ai-run/guides/testing/testing-patterns.md`.

## Global Constraints

- Zero-based `page`; pagination `{page, per_page, total_count, has_more}` unchanged; `total_count`/`has_more` count named rows only.
- Scope via `FilterParams.resolve(user, project_unattributed)` (`cli_analytics.py:416-470`), never `resolve_sessions`, for `/repositories` and `/branches`; non-super-admin on no-project scope gets `rows: []`, HTTP 200.
- Routes use `_admin_gate`, `handle_errors`, `_statement_timeout_as_504`. No caching, no search on repo/branch lists, no summary endpoint, no regrouping of `/repositories`.
- Existing ordering kept per list (projects spend; repos/branches session count, spend, name).

## Review Focus

- Only-null-group list: one row on page 0, `total_count` 0, `has_more` false (Task 1 test).
- Page beyond last: no rows, no null row (Task 1).
- Two projects sharing a repository: P-only metrics (Task 3).
- Non-super-admin + `is_project_unattributed`: empty 200 (Tasks 3, 4).
- `/sessions` flag without `repositories` or with `branch`: 422 (Task 5).

---

### Task 1: Null-row helper and `/projects` totals-only

**Files:**
- Modify: `src/codemie/service/analytics/handlers/cli_analytics_handler.py` (`get_projects` :1377-1407, `_pagination` :728, `_group_repositories_into_projects` :708), `src/codemie/repository/cli_analytics/postgres/project_page_sql.py` (`_SEARCH_CONDITION`, `_PROJECT_ORDER`), `ports.py:617-625`, `src/codemie/rest_api/models/cli_analytics.py:210-225`, router `cli_analytics.py:795-815`
- Test: `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` (rework :850), `tests/codemie/repository/cli_analytics/postgres/test_bounded_reader.py`, new `tests/codemie/service/analytics/handlers/test_cli_analytics_null_row.py`

**Interfaces:**
- Produces: `with_unattributed(rows: list[T], null_row: T | None, pagination: dict) -> list[T]` beside `_pagination`; appends `null_row` only when `not has_more` and (`rows` non-empty or `page == 0`); never when a page is beyond the last.

Test-first: yes — helper tests: null row on last page only, only-null list gives that row with total 0, page beyond last gives `[]`; `/projects` rows lack `repositories`, no nested-session read is issued, `search=x` does not match repository named x, no null row under search.

- [ ] Write the failing tests above (fake reader pattern in the existing handler tests).
- [ ] Remove `repositories` from `LocalAnalyticsProjectRow`; drop `_repository_rows` read and `_group_repositories_into_projects` use in `get_projects`; keep `repository_count`.
- [ ] Drop repository-name match from `_SEARCH_CONDITION`; fix docstrings (`ports.py:625`, router description :807, endpoint docstring).
- [ ] In `project_page_sql.py` exclude the NULL project from ranking, paging and count; add a null-group existence read (same statement as count) and a one-row null-group read; handler applies `with_unattributed`; skip it when `search` is set.
- [ ] Wrap `/projects` in `_statement_timeout_as_504`.
- [ ] Run `pytest tests/codemie/service/analytics tests/codemie/repository/cli_analytics -q`; fix `test_cli_cost_cross_screen_parity.py:153` if affected.

### Task 2: `/repositories` project scope and Unattributed row

**Files:**
- Modify: router `cli_analytics.py:763-792` (accept `is_project_unattributed`, resolve via `FilterParams.resolve`), handler `get_repositories` :1305-1375, `reader.py:2075` `get_repository_ranking` (named-only paging/count, null-group read)
- Test: `tests/codemie/service/analytics/handlers/test_cli_analytics_handler_ordering.py:161-188`, `tests/codemie/rest_api/routers/test_cli_analytics_*.py`

**Interfaces:**
- Consumes: `with_unattributed` (Task 1).

Test-first: yes — `projects=P&page=0&per_page=10` with two projects sharing repository R returns P-only metrics for R; null-repository row last on last page only, absent under `search`; `is_project_unattributed=true` returns no-project data for super-admin and `[]` with 200 otherwise; unpaged call (no `page`/`per_page`) returns as before including `include_branches` forced-all.

- [ ] Write failing tests (two-project case, unattributed, access, unpaged).
- [ ] Pass `project_unattributed` into `FilterParams.resolve`; in paged mode exclude the NULL-repository group from rank/paging/count and append it through `with_unattributed`; leave unpaged mode untouched.
- [ ] Run the router and handler tests.

### Task 3: `/branches` endpoint

**Files:**
- Modify: `ports.py` (add `get_branch_ranking`), `reader.py` (implement beside `get_repository_ranking`, SQL paged, named-only count, null-branch read, ordered session count, spend, name), handler (`get_branches`), router (`GET /branches`), `models/cli_analytics.py` (branch response envelope using `LocalAnalyticsRepositoryBranchRow`)
- Test: new `tests/codemie/service/analytics/handlers/test_cli_analytics_branches.py`, `tests/codemie/rest_api/routers/test_cli_analytics_branches_router.py`, `test_bounded_reader.py`

**Interfaces:**
- Consumes: `with_unattributed`. Produces: `GET /v1/analytics/cli-analytics/branches?repository=R&projects=&is_project_unattributed=&page=&per_page=` (`repository` required, ANDed with `FilterParams.repositories`; no `search`).

Test-first: yes — first 10 branches of R in P with metrics and pagination; `repository` ANDs with `repositories`; null-branch row last on last page only; non-super-admin no-project scope gives `[]` 200; missing `repository` gives 422; port test checks the reader protocol naming.

- [ ] Write failing tests.
- [ ] Add port method and reader implementation reusing the shared sessions CTE and filter scope; no new flag on `get_repository_ranking`.
- [ ] Add handler and route with `_admin_gate`, `handle_errors`, `_statement_timeout_as_504`.
- [ ] Run the new and existing reader/router tests.

### Task 4: `/sessions` `is_branch_unattributed`

**Files:**
- Modify: router `cli_analytics.py:922-953`, `LocalAnalyticsFilter` (`repository/cli_analytics/filters.py`) and the reader sessions query
- Test: `tests/codemie/rest_api/routers/test_cli_analytics_*.py`, reader sessions test

Test-first: yes — `repositories=R&projects=P&is_branch_unattributed=true` returns only R-in-P sessions with no branch; 422 without `repositories`; 422 with `branch`; omitting the flag leaves results unchanged.

- [ ] Write failing tests.
- [ ] Add optional query param, validate (422) and add a branch-IS-NULL condition ANDed with the existing project/repository scope.
- [ ] Run the sessions tests.

### Task 5: OpenAPI baseline and FE contract sync

**Files:**
- Modify: `tests/codemie/rest_api/router_guards/baselines/openapi.json` (regenerate with the repo's baseline generator, see the guard test), `fe-contract.md` (sync only: edit lines that differ from final behavior, do not rewrite)

Test-first: no — regenerate baseline; the router-guard test must pass and the diff must show only `/projects` row, `/repositories` param, `/branches`, `/sessions` flag.

- [ ] Regenerate the baseline and review the diff.
- [ ] Reconcile `fe-contract.md` with final param names, null-row rule, access outcome and backward compatibility.
- [ ] Record the pre-release measurement in the task notes: `EXPLAIN ANALYZE` and p95 of `/repositories?projects=<largest>&per_page=10` and `/branches` on the largest repository.

## Self-review notes

negative-constraints: honored. Spec non-goals (no repo/branch search, no summary endpoint, no caching, envelope and zero-based page unchanged, no regroup) are respected by Tasks 1-3; the no-project scope uses `resolve`, not `resolve_sessions` (Tasks 2-3).
