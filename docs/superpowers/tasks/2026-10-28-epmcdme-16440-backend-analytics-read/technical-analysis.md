# Technical Research (sliced from parent: codemie repository, read side)

## 1. Original Context
Child of EPMCDME-16440; see ../../technical-analysis.md Section 1.

## 2. Codebase Findings
- `repository/cli_analytics/postgres/reader.py` (~122-170) filters `project_name = ANY($projects)` or `''` for unattributed; `filters.py` `LocalAnalyticsFilter` has users/projects/repositories/deny_all/project_unattributed.
- Router `FilterParams.resolve`: non-admins limited to `AccessFilter.get_project_access_context().admin_projects`; unattributed super-admin only. Personal projects have non-admin members → no non-super-admin sees them; owner self-view not implemented.
- Response models `rest_api/models/cli_analytics.py`.
- Spend: `service/analytics/handlers/member_spend_service.py` (~194) excludes personal projects; `budget/project_budget_service.py`, `spend_tracking/spend_collector_service.py`, `analytics/spending.py`. CLI-analytics cost via `usage_requests` → `cost_daily`; whether a project-spend endpoint uses CLI cost is unknown.
- Legacy rows: `session_dims` derived from `hook_events`; old rows lack new fields; `backfill.py` `reclassify`.
- Scope terminology: tracking scopes user/project are CLI-side (analytics rows record no scope term); the two analytics views are separate systems (legacy coding-agents vs OTel cli-analytics) — child must determine which read surface carries the new fields.

## 3. Documentation Findings
Same guide as parent; read contract must stay consistent with ingest child's contract update.

## 4. Testing Landscape
Reader/filter/router tests exist under `tests/codemie/repository/cli_analytics/` and rest_api router tests (exact files to be located in delivery). No subscription/origin cases.

## 5. Configuration and Environment
Reuses `cliAnalytics` feature flag and `ENABLE_USER_MANAGEMENT`.

## 6. Risk Indicators
- Personal visibility is a privacy boundary: per-user, not project-admin.
- Spend exclusion across two cost pipelines.
- Legacy rows → Unknown in filters.

## 7. Summary for Complexity Assessment
Backend read path: response models, filters, access rules and spend exclusion; moderate novelty, privacy-sensitive.

## 8. External References
Consumes columns/contract from `backend-ingest-routing`.


---

## 9. Refresh against the child-1 base and MR !4652 (2026-10-28)

Facts only (branch HEAD 19293cd37, child 1 committed). Anything marked "Speculative" is a risk note, not a requirement. Paths under `src/codemie/` unless noted.

### 9.1 Stored shape from child 1
- Migration `src/external/alembic_cli_analytics/versions/a1c1a0000007_cli_analytics_session_routing.py:34-43`: nullable `text` columns. `session_dims`: `owner_email`, `subscription_type`, `project_origin`, `route_note`. `hook_events`: `subscription_type`, `project_origin`, `route_note`, `client_subscription_type`, `client_policy_version`. The client-reported columns exist on `hook_events` only, not on `session_dims`. Legacy rows are NULL (read as unknown / empty origin; the migration docstring says so).
- Binding: `owner_email` set = bound (`repository/cli_analytics/postgres/session_binding.py:53-69`; guide `.ai-run/guides/integration/cli-analytics-events.md:67`). A binding-only row has `started_at` NULL (no dimension hook event yet); `_sessions_cte` requires `d.started_at IS NOT NULL` (`postgres/reader.py:~285`), so it is in no list. Test: `tests/codemie/repository/cli_analytics/postgres/test_live_session_binding.py:580`. The recompute never sets or overrides the binding. `session_dims.project_name` and `jwt_email` hold destination and owner for evidence sessions.
- Case: child 1 compares emails as `a.strip().lower() == b.strip().lower()` (`session_binding.py:112`) and `lower(btrim(e))` (line 63). The migration puts no lower-case constraint on `owner_email`; a read-side comparison should lower() both sides.
- Only `session_dims` carries `project_name` and the subscription type. `cost_daily`, `lines_daily`, `active_time_daily`, `turns_daily`, `tool_facts_daily`, `session_files_daily`, `invocations_hourly`, `session_usage`, `session_usage_hourly`, `usage_requests`, `subagent_invocations`, `session_skills` are keyed by `session_id` (+ day / user_email on some) with NO project, subscription or owner column (migrations a1c1...0001:179-250, ...0006:76-215). **No rollup table can be filtered by Personal or owner without a join (or `IN (SELECT ... FROM sel)`) to `session_dims`.** `hook_events` carries per-event `codemie_project_name` and `subscription_type`, which the reader's list queries do not read.

### 9.2 Read paths, access resolution and choke points
- Router `rest_api/routers/cli_analytics.py`: every read route has `dependencies=[Depends(_admin_gate)]` (`_admin_gate` :119 allows only `user.is_admin or user.is_applications_admin`; a plain user or auditor gets 403 before any filter). Routes: `/overview` :430, `/cost` :449, `/users` :468, user-charts :490, `/repositories` :530, `/tools` :561, `/activity` :581, `/efficiency` :601, `/frameworks` :620 (static labels, no reader), `/sessions` :632, `/sessions/{trace_id}` :663. Fact: a non-admin Personal owner cannot reach any read route today.
- Access resolution for list routes is in ONE place, `FilterParams.resolve` (:301-348): `AccessFilter(user).get_project_access_context()` (`service/analytics/access_filter.py:97`; super admin and auditor => `is_admin=True`, unrestricted); non-admin => `projects = admin_projects` (intersected with the request), `deny_all = not projects`; `project_unattributed` => `projects=None`, `deny_all` unless `ctx.is_admin`. Query params: time, users, projects, repositories (+ `is_project_unattributed`, branch, framework on /sessions). No subscription/origin param exists. `LocalAnalyticsFilter` (`repository/cli_analytics/filters.py:21-64`) has no such fields; `window_params` (`reader.py:~163`) and `params()` copy each filter explicitly.
- Detail route has its own, separate check (:670-707): the handler loads the whole detail first, then only for `not ctx.is_admin` re-reads `reader.get_session_detail_meta` and 404s if `project_name not in admin_projects`. Super admin skips it entirely. The detail queries (`reader.py:704-900`: meta, cost, model_costs, scalars, tools, events, dispatches) take only `session_id` and never go through `sel`.
- **Choke point for lists:** `PostgresCliAnalyticsReader._sessions_cte(f)` (`reader.py:251-291`) builds `sel` from `session_dims`; `conditions` (users/projects/repositories/branch/deny_all) are applied on the projected subquery `x`, where `user_email` is already the derived row email and `project_name` is coalesced to ''. Every list/aggregate method (get_cost_kpis, model_breakdown, session_model_costs, cost_by_user, users, users_daily_activity, users_last_active, lines_*, turns/file/tool facts, tool_usage, invocations, durations, active_ms, session_cost_facts, start_times, repository_sessions; ~25 methods) is `WITH {self._sessions_cte(f)}` and scopes facts with `... IN (SELECT session_id FROM sel)` (`_scope` :304, `_cost_rows` call sites). A rule added in `_sessions_cte` reaches all of them without per-query changes. `sel` does not select the new columns today (they would have to be added to its projection to expose fields or filter on them).
- Not on `sel` (per-query work): the 7 session-detail methods (:704-900) and `get_skill_names_by_session(session_ids)` (:604; `session_skills` by bare id list; callers pass ids from a filtered list or the detail id). Detail authorization is route-side only.
- Response models (`rest_api/models/cli_analytics.py`): `LocalAnalyticsSessionRow` :201 (detail subclass :276), `LocalAnalyticsRepositoryRow` :126 (has `project_name`); none has subscription/origin fields. Handler (`service/analytics/handlers/cli_analytics_handler.py`): `get_sessions` :677-780 builds session dicts, `get_repositories` :609 uses `get_repository_sessions` (already selects `project_name` from `sel`, reader :688-702), `get_session_detail` :780.
- "Both analytics views": the Postgres reader serves list/aggregate views (via `sel`) and per-session detail. The legacy Elasticsearch analytics (`service/analytics/query_builder.py`, `handlers/user_handler.py`) is not backed by `session_dims`. Speculative: the spec should say which two views AC2 means; only these two CLI-analytics surfaces were found.

### 9.3 Cost pipelines
- CLI-analytics cost is priced in the handler at query time (`handlers/session_money.price_session_models`, `CatalogPricer`) from per-(session, model) token rows read from `cost_daily` (`reader.get_session_model_costs` :364, `get_session_detail_model_costs` :752) plus token sums in `get_session_cost_facts`. Endpoints that aggregate it, all through `sel`: `/overview` (KPIs, model breakdown, daily), `/cost` (by user, by model), `/users`, `/repositories`, `/efficiency`, `/sessions`. There is no CLI-analytics "project spend" endpoint; shared-project cost means these aggregates under a project scope. Personal sessions enter all of them today.
- Member/project spend: `service/analytics/handlers/member_spend_service.py:275-305` excludes personal projects via `application_repository.aget_personal_project_names` (`repository/application_repository.py:879`). Budget/spend-tracking code is LiteLLM-metered; no path was found that reads `cost_daily` into member spend.

### 9.4 MR !4652 (EPMCDME-16441; head e53fde4e4, base 78baea70f; UNMERGED; fetched read-only, worktree unchanged)
What it changes in the files this child touches:
- `filters.py`: adds `LocalAnalyticsFilter.session_ids` and `restricted_to(ids)` (a `copy.copy`, so new fields carry over). `reader.window_params` adds `session_ids`; `_sessions_cte` adds `conditions.append("session_id = ANY($session_ids::text[])")` after the branch condition.
- `reader.py` (+176): `get_session_cost_facts(..., include_costless=False)`; with True the FROM is `sel s JOIN act a LEFT JOIN cost c LEFT JOIN totals t` (`_facts_from`), so cost-less selected sessions are listed. New `get_session_page(f, sort_by, page, per_page, search, is_unattributed) -> (total, ids)` (sorts `start_time`, `ctx_per_call` in SQL; `cost_usd` sort and framework filter stay in Python). `_token_rows` / `_totals_join` UNION `cost_daily` with `session_usage` rows where `attrs->>'token_source'='subagent_usage'`, each scoped by `session_id IN (SELECT session_id FROM sel)`; `get_session_model_costs` and `get_session_detail_model_costs` switch to `_token_rows`. `get_session_detail_meta`'s `c` CTE becomes `cost_daily UNION session_dims WHERE started_at IS NOT NULL`, so detail resolves for a cost-less session.
- `ports.py`: `SESSION_PAGE_SORTS`, `get_session_page`, `include_costless` (the `IngestGate` Protocol change is ingest-only).
- `cli_analytics_handler.py` `get_sessions`: calls `get_session_page`, then `f.restricted_to(page_ids)` and `get_session_cost_facts(include_costless=True)`; sets `cost_available` per session (`priced_session_ids`) and in detail (`has_priced_model`). `session_money.py` adds `has_priced_model`, `priced_session_ids`; `coding_agent_pricing.py` adds `CatalogPricer.is_priced`; model adds `LocalAnalyticsSessionRow.cost_available: bool = True`.

Where this child's owner-only and exclusion rules must also hold:
1. Cost-less sessions: `_facts_from(include_costless=True)` starts from `sel`, so a `sel` rule covers them; the default branch joins `cost_daily` to `sel`, also covered.
2. Subagent / model-cost paths: `_token_rows`, `_totals_join` and `get_session_page`'s `filtered` all subselect `sel`, so covered; any new query on `session_usage` / `subagent_invocations` by bare session id would not be.
3. Paged lists: `get_session_page` total and ids come from `sel`. `restricted_to` ANDs `session_ids` into the same `sel`; an owner rule in `_sessions_cte` must sit in the base conditions that every call receives and must not be skipped when `session_ids` is set.
4. Detail: the MR widens `get_session_detail_meta` to cost-less `session_dims` rows, making the guarded-only-in-the-router detail reachable for more sessions; the owner check there is still route/meta-side only.

Landing-order seams (textual conflicts): the `_sessions_cte` `conditions` block and `x` projection; `LocalAnalyticsFilter.__init__` / `params()` / `window_params`; `get_session_detail_meta` SELECT list and `c` CTE; `LocalAnalyticsSessionRow` field block (`cost_available` vs new fields) and the session-dict assembly in `get_sessions`; `ports.py` `CliAnalyticsReader` signatures; tests `test_reader.py`, `test_cli_analytics_handler.py`, and `tests/codemie/rest_api/router_guards/baselines/openapi.json` (the MR changes the baseline; new response fields here touch it too). Dry-run `git merge-tree --write-tree HEAD FETCH_HEAD`: conflicts only in `postgres/ingestor.py`, `service/analytics/cli_analytics_project_gate.py` and `tests/.../test_reader_live_session_range.py` (child 1 vs 16441, outside this child's read files); `reader.py`, handler, `filters.py`, `ports.py` and models auto-merge today, so conflicts with this child would appear only once it edits the seams above.

### 9.5 Who is "the owner" on the read path
- Authenticated identity: `User.email` (`rest_api/security/user.py:67`, default `""`); a personal project is named after the owner's email (`resolve_default_project`, user.py:31-46). Stored side: `session_dims.owner_email` (set only for evidence sessions), `jwt_email`, and the derived `user_email` in `sel` (`coalesce(nullif(jwt_email,''), nullif(dev_name_max,''), developer_name)`). Legacy sessions have `owner_email` NULL and no subscription type, so no stored value marks them Personal.
- Super admin / auditor: `get_project_access_context` returns `is_admin=True` and skips project filters; the unattributed rule is `deny_all or not ctx.is_admin` in `resolve`. `ProjectAccessContext` has no owner concept. Project admin: only `admin_projects`; a Personal session has `project_name` = owner email, which nothing excludes explicitly. The detail route has no owner check and skips all checks for super admin.
- Speculative risk: an empty `User.email` and NULL `owner_email` need explicit NULL handling in any owner predicate.

### 9.6 `personalIngestEnabled`
- Read: `rest_api/routers/cli_analytics_ingest_route.py:49-52` `personal_ingest_enabled()` -> `customer_config.get_feature_setting("cliAnalytics", "personalIngestEnabled", default=False)`, fail-closed (only `True` or the string "true"). Passed to the gate at :97; used in `service/analytics/cli_analytics_project_gate.py:151-186, 300-358` (503 `personal_ingest_not_enabled`). Declared as SWITCH in `service/customer_config_declarations.py:236-241`, description "Turn on only after owner-only read access is effective." An administrator turns it on by setting feature `cliAnalytics` setting `personalIngestEnabled` in customer config (60s TTL per `docs/superpowers/tasks/2026-10-28-epmcdme-16440-backend-ingest-routing/spec.md:69`, which assigns the flip to this child's rollout).
- Fact: no guard or read-side readiness check couples the switch to the read path; the only couplings are the description text and the guide line (`.ai-run/guides/integration/cli-analytics-events.md:59`). In code today the requirement is a documented rollout order only. A guard or readiness check would be new code; none was seen in the customer-config setter. No change made.

### 9.7 Tests and fixtures to extend
- Unit (no DB): `tests/codemie/repository/cli_analytics/postgres/test_reader.py` (SQL-text and `window_params` cases, deny_all / unattributed :97-136, :874), `tests/codemie/repository/cli_analytics/test_filters.py`, `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` and `..._handler_ordering.py` (fake repo), `tests/codemie/rest_api/routers/test_cli_analytics_storage_wiring.py:289-410` (`FilterParams` / `resolve` as admin; the only router-level read-filter tests), `test_cli_analytics_evidence_routing.py` (switch settings :136, :243, :462), `tests/codemie/service/analytics/test_cli_classification.py:99` (switch declaration).
- Live PostgreSQL (needs `TEST_LOCAL_PG_URL`, default `postgresql://postgres:password@localhost:5432/postgres`; pytest.ini forces `PG_URL` to a dead port; see `test_reader_live_session_range.py:39-41`, helpers `_get_engine`, `_seed_session`, `_cleanup`): `test_reader_live_session_range.py`, `test_live_catalog_money.py`, `test_live_dispatch_gate.py`, `test_reader_live_dispatches.py`, `test_live_summary_counters.py`, `test_live_session_binding.py` (seeds `owner_email` bindings; :580 binding-only row). The MR adds `test_live_costless_sessions.py`, `test_live_session_page.py`, `test_live_session_subagent_cost.py`.
- Gaps: no test of owner-only visibility, subscription/origin fields or filters, or Personal exclusion from aggregates; no test of the detail project check or `_admin_gate`.

### 9.8 Risk indicators added by this refresh
- Privacy: rollups cannot be filtered by owner without a `session_dims` join. The list side has one choke point, `_sessions_cte`/`sel`; the detail side (7 methods plus skills by id) has none and is authorized in the router after the data is loaded. Super admin and auditor have no owner concept.
- Any reader query that bypasses `sel` is a leak path (`get_skill_names_by_session`, future `session_usage` queries).
- Landing order: shared seams in 9.4 conflict textually; with !4652 cost-less sessions become listable, so Personal cost-less sessions appear unless the `sel` rule applies.
- The rollout switch is not enforced by code; enabling it before owner-only read is effective stores Personal rows that current read paths expose to super-admin and project-admin queries.
- Gate: all reads need `is_admin or is_applications_admin`; owner self-view for a plain user is a scope question for the spec (Speculative).
