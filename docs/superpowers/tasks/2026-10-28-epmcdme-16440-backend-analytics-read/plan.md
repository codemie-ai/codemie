# backend-analytics-read Implementation Plan

> **For agentic workers:** use superpowers:subagent-driven-development or superpowers:executing-plans. Checkbox steps.

**Goal:** Subscription type / project origin on CLI-analytics reads, filters, and owner-only Personal visibility, per approved spec rev 3 (`spec.md`, same dir).
**Architecture:** One `ViewerScope` (modes `exclude`/`own_only`/`detail`) built in the router, carried in `LocalAnalyticsFilter`, enforced by one SQL predicate in `PostgresCliAnalyticsReader._sessions_cte` base conditions (lists) and a LEFT JOIN `session_dims` predicate (detail). Paths under `src/codemie/`; analysis facts in `technical-analysis.md` section 9.
**Tech Stack:** FastAPI, asyncpg-style Postgres reader, pytest.

## Global Constraints
- Commit per task using the repository's existing convention (read `.ai-run/guides/standards/git-workflow.md`).
- Tests: `C:/codemie-dev/codemie/.venv/Scripts/python.exe -m pytest -p no:cacheprovider <targets>` with `PYTHONPATH=src`; targeted tests only. Known Windows baseline failures (child-1 gate-run.json) are not to be fixed.
- Live tests: `TEST_LOCAL_PG_URL` on throwaway podman postgres:17 at `127.0.0.1` (not localhost), default xdist, unique ids per test.
- !4652 is merged and this branch is rebased onto it; rules still live in `_sessions_cte` base conditions / the shared scope predicate, and the !4652 shapes (`include_costless`, `_token_rows`, `get_session_page`, `session_ids`, `restricted_to`) keep them (combined checks below). Do not cherry-pick CLI #707.
- Scope is a required reader argument (no default); absent/malformed scope denies. Owner match: `lower(btrim())` both sides, both non-empty.
- Billing class is separate from private scope (EPMCDME-16440 adaptation): Personal-paid on a work project is N (activity visible to project readers, cost excluded from aggregate spend); `sql_private` / `sql_personal_billed` in `scope.py`. Personal ingest requires no separate activation setting. `member_spend_service` unchanged. `_admin_gate` relaxed only on `/sessions` and `/sessions/{trace_id}`.

## Task 1: ViewerScope and truth-table predicate
**Files:** Create `repository/cli_analytics/scope.py`; Test `tests/codemie/repository/cli_analytics/test_scope.py`.
**Produces:** `ViewerScope(viewer_email: str|None, unrestricted: bool, admin_projects: frozenset[str], mode: Literal["exclude","own_only","detail"])` with `normalise_email()`; `allowed(scope, row_class, owner_email, project_name) -> bool` (pure, classes P/N/M per spec); `sql_list_condition(scope) -> (sql, params)` over projected `x` and `sql_detail_condition(scope)` over a LEFT JOIN `session_dims d` (missing row = M, existing row with NULL owner = P-unowned).
- [ ] Test-first: yes — failing test: parametrised truth table (3 modes x P/N/M x owner/other/ownerless x viewer missing x super-admin/admin/ordinary); absent/malformed scope denies.
- [ ] Implement; run `test_scope.py`.

## Task 2: `_sessions_cte` rule and projection
**Files:** Modify `repository/cli_analytics/postgres/reader.py:251-291` (`conditions`, `x` projection, `sel` selects `subscription_type`, `project_origin`, `owner_email`); `ports.py` signatures; every list/aggregate reader method gets scope via the filter. Test `tests/.../postgres/test_reader.py`.
**Interfaces:** Consumes Task 1; `LocalAnalyticsFilter.scope` is required (Task 3 adds the field; add it here with no default and update existing test fixtures).
- [ ] Test-first: yes — failing SQL-text tests: base conditions contain the scope predicate for each mode; it is present when `session_ids`-style extra conditions are added (simulate by appending a condition); absent scope yields a deny-all condition.
- [ ] Implement; run `test_reader.py`.

## Task 3: Filter params and `LocalAnalyticsFilter` fields
**Files:** Modify `repository/cli_analytics/filters.py:21-64` (`scope`, `subscription_types`, `project_origins`; copy in `__init__`/`params()`; `window_params` at `reader.py:~163`); router `FilterParams` `rest_api/routers/cli_analytics.py:301-348` (multi-value `subscription_type`, `project_origin`, build `ViewerScope` mode `exclude` from `AccessFilter(user).get_project_access_context()`); `_sessions_cte` filter on `x` with `unknown`/`none` mapping and `other` bucket. Tests `test_filters.py`, `test_reader.py`, `rest_api/routers/test_cli_analytics_storage_wiring.py:289-410`.
- [ ] Test-first: yes — failing tests: `params()` carries new fields (also via `copy.copy`); `resolve` yields scope; `unknown` matches NULL/empty subscription, `none` matches NULL/empty origin (SQL text); Personal filter under `exclude` returns nothing.

## Task 4: Detail authorization
**Files:** Modify `reader.py:604` (`get_skill_names_by_session`) and the 7 detail methods `:704-900` (meta, cost, model_costs, scalars, tools, events, dispatches) to take required `scope` and apply `sql_detail_condition`; add `authorize_session(session_id, scope) -> bool` (cheap read); handler `get_session_detail` (`cli_analytics_handler.py:~780`) passes scope; router `:670-707`: replace post-load check with pre-read `authorize_session` returning the same 404 before any payload query. READ router 670-707 first: authNP for non-admins is `project_name in admin_projects` (M uses derived project, unattributed super-admin only); super-admin skipped it today. Tests `test_reader.py`, `test_cli_analytics_handler.py`, new `tests/.../postgres/test_reader_scope_guard.py`.
- [ ] Test-first: yes — failing tests: each of 8 methods' SQL contains the detail predicate; guard test greps reader.py SQL for any `FROM|JOIN session_usage|session_skills|cost_daily ... WHERE session_id` not routed via `sel` or the predicate (allow-list explicit); router returns 404 with no payload call when denied; existing `test_session_detail_without_dims_starts_at_its_earliest_event` still passes.

## Task 5: `/sessions` gate relaxation and `personal_only`
**Files:** Modify router: `/sessions` :632 and `/sessions/{trace_id}` :663 replace `_admin_gate` with an owner-aware dependency (others keep `_admin_gate`, still 403); `personal_only: bool=False` only on `/sessions`; `FilterParams.resolve` variant for `/sessions`: `personal_only=true` -> `own_only`, ignore projects/users/is_project_unattributed, user forced to viewer; ordinary non-admin always `own_only`, no viewer email -> empty list. Test `test_cli_analytics_storage_wiring.py` (+ new router test file).
- [ ] Test-first: yes — failing tests: non-admin gets 200 on `/sessions` and 403 on `/overview`; supplied projects/users cannot widen; `personal_only` on aggregate routes is ignored and absent from their openapi; no-email non-admin gets empty list.

## Task 6: Response fields and aggregate lists
**Files:** Modify `rest_api/models/cli_analytics.py`: add scalar `subscription_type`, `project_origin` to `LocalAnalyticsSessionRow` (:201; detail subclass :276 inherits); add `subscription_types: list[str]`, `project_origins: list[str]` (shared mixin) to aggregate models: `LocalAnalyticsOverviewKPIs`, `OverviewDailyBucket`, `ModelBreakdownRow`, `RepositoryRow` (and `RepositoryBranchRow` if it groups), `DailyActivityBucket`, `UserRow`, `CostKPIs`, `CostByUserRow`, `CostByModelRow`, `TokensByModelRow`, `InvocationRow`, `ToolWithSuccessRow`; confirm against the file and skip pure-config/static models (`FrameworksResponse`). Reader: lists via `sel` join on `session_id` inside the same grouping (no new group dimension); handler assembly in `cli_analytics_handler.py` (`get_sessions` :677-780, `get_repositories` :609, others). Normalise: lower-case, NULL/empty -> `unknown`/`none`, else `other`.
- [ ] Test-first: yes — failing mixed-entity fixture test (fake repo + SQL text): one repo/user/model/day with Corporate, CodeMie, NULL and two origins returns exactly sorted distinct classes, row counts and totals equal the same query without the fields; Personal never listed; legacy rows -> `unknown`/`none`.

## Task 7: openapi baseline
**Files:** Regenerate `tests/codemie/rest_api/router_guards/baselines/openapi.json` using the repo's existing baseline mechanism (find it in `tests/codemie/rest_api/router_guards/`).
- [ ] Test-first: no — run the router_guards openapi test, regenerate, rerun; diff shows only new params/fields.

## Task 8: Docs and switch description
**Files:** `.ai-run/guides/integration/cli-analytics-events.md`: document read visibility, fields, filters and the first-upgrade corner case; classification settings retain only `corporateOrganizations`.
- [ ] Test-first: no — edit text; run `test_cli_classification.py`.

## Task 9: Live PostgreSQL cases
**Files:** Create `tests/codemie/repository/cli_analytics/postgres/test_live_owner_scope.py` (helpers from `test_reader_live_session_range.py`: `_get_engine`, `_seed_session`, `_cleanup`; unique ids).
- [ ] Test-first: yes — live tests (AC1-AC10): ordinary-owner and project-admin-owner lists/detail; non-owner and super-admin/auditor see nothing of others' Personal in lists, aggregates, detail, skills; case/whitespace email; NULL owner/viewer; Personal seed changes none of overview/cost/users/repositories/efficiency totals; cost-less and `subagent_usage` rows; binding-only unlisted; M-class legacy detail resolves for permitted admin, denied to ordinary user; mixed-entity lists; `unknown`/`none` filters.
- [ ] Add SQL-text-only tests for the !4652 query shapes (`_facts_from` costless, `_token_rows`, paged `filtered`, widened detail meta) showing each subselects `sel`; after the rebase these run against the landed !4652 shapes, plus live cost-less, paged, subagent-totals and first-prompt cases under the scope.

## Negative-constraint pass
Honoured: !4652 integrated by rebase only, no re-implementation of its pricing (Tasks 2, 9); no `member_spend_service`/migration change (none planned; ingest only as adapted in the ingest-routing spec); `_admin_gate` only relaxed in Task 5; no switch code (Task 8 text only); no super-admin Personal exception (Tasks 1, 9); no new grouping dimension (Task 6); no ES analytics change; no backfill.
