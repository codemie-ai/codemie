# Technical Research

**Task**: cli-analytics repositories projects branches sessions
**Generated**: 2026-10-05
**Research path**: filesystem

---

## 1. Original Context

When a user calls GET /api/v1/analytics/cli-analytics/repositories?time_period=last_7_days&include_branches=true the API should return data grouped by project, repositories and branch. Need to support nulls for the projects, and afterwards be able to filter by null projects in the sessions endpoint (GET /api/v1/analytics/cli-analytics/sessions?time_period=last_7_days&repositories=epm-cdme%2Fcodemie&page=0&per_page=20&sort_by=start_time), as the UI has such options. UI lives at C:\Projects\codemie-dev\codemie-ui (look at how it consumes these endpoints). Backend repo: C:\Projects\codemie-dev\codemie (bug ticket EPMCDME-15446).

---

## 2. Codebase Findings

### Existing Implementations
Backend (`C:\Projects\codemie-dev\codemie\src\codemie\`):
- `rest_api/routers/cli_analytics.py` — `GET /repositories` (~L482-511): params `page`, `per_page`, `include_branches`, `search`, plus `FilterParams` (`projects`, `repositories` comma-separated, ~L245-310). If page and per_page are both omitted, `include_branches` is forced True and everything is returned. `GET /sessions` (~L589-610): `sort_by`, `search`, `framework`, `is_unattributed` (repository IS NULL only), `branch`. Both are admin-gated (`_admin_gate`) and feature-flagged (`cliAnalytics`).
- `service/analytics/handlers/cli_analytics_handler.py` `get_repositories` (L408-508): builds buckets from `get_session_cost_facts` rows keyed `(repository, branch)` or `(repository,)`. Each bucket keeps a `_projects` counter, and `project_name` is set to `_most_frequent(...)`. So a repo used under several projects collapses into ONE row labelled with the dominant project. Sessions with null project add nothing to the counter. Output is a flat `rows` list. `get_sessions` (L512+) takes `is_unattributed` and `branch`, and sets `f.branch`.
- `repository/cli_analytics/postgres/reader.py`: `_sessions_cte` (L153-189) normalises NULL to `''` for `project_name`, `repository` and `branch`. The filter is `project_name = ANY($projects::text[])` (L166-167), so there is no way to select empty or null project. `get_session_cost_facts` (L478-514) returns `max(s.project_name)` per session and supports `is_unattributed` through HAVING on repository. `window_params` (L111-127) binds `projects`, `repositories` and `branch`. `get_repository_sessions` (L524) also selects `project_name` (not used by the handler's `get_repositories` now).
- `repository/cli_analytics/filters.py` `LocalAnalyticsFilter` has `users`, `projects`, `repositories`, `branch`, `deny_all`. It has no null-project flag. `ports.py:154` declares the `get_session_cost_facts` protocol (signature must stay in sync).
- `rest_api/models/cli_analytics.py`: `LocalAnalyticsRepositoryRow` (L102-116) has `repository|None`, `branch|None`, `project_name|None`, metrics. `LocalAnalyticsRepositoriesData` = `rows` + `pagination`.

Frontend (`C:\Projects\codemie-dev\codemie-ui\src\`):
- `store/cliAnalytics.ts` L247 `fetchRepositories` hits `v1/analytics/cli-analytics/repositories`, L225 sessions.
- `pages/analytics/components/cli-analytics/hooks/useCliAnalyticsRepositories.ts` — the view calls it with `includeBranches=true`, `initialPerPage=0` (fetch all), so the API call is include_branches=true with no page params.
- `views/RepositoriesView.tsx` — `aggregateRepos` (L46-94) already groups client-side by `row.project_name || null`, then by repository, with branches nested. A null project renders as "(no project)". Click handlers (L126-166) open `ExtendedSessionsModal` with `repositories=[repo]`, `isUnattributed` (repository===null) and `branch`. No project is passed to the sessions call, so a click under a project or "(no project)" group loses the project context.
- `hooks/useCliAnalyticsSessions.ts` (L44-121) sends `projects` (from the global filters), `repositories`, `is_unattributed`, `branch`. There is no null-project param. `hooks/params.ts` L38 joins `filters.projects` into a comma string.
- `types/cliAnalytics.ts` L137 `RepositoryRow` has `project_name?: string | null`.

### Architecture and Layers Affected
Router (`cli_analytics.py`), handler/service (`cli_analytics_handler.py`), reader/repository (`postgres/reader.py`, `filters.py`, `ports.py`), Pydantic response models, and the UI view, hooks and types. The three layers are router → handler → reader.

### Integration Points
Router → `LocalAnalyticsHandler(get_cli_analytics_storage().reader)` → `PostgresCliAnalyticsReader` (asyncpg, `$name` params converted by `positional`). Project-admin scoping lives in `FilterParams.resolve` (intersects `projects` with `ctx.admin_projects`; `deny_all` if empty). The UI global project filter flows to every endpoint as `projects`.

### Patterns and Conventions
Handler helpers `_s`, `_i`, `_f`, `_most_frequent`. NULL is collapsed to `''` in SQL and mapped back to `None` in the handler (`_s(x) or None`). Router uses `@handle_errors`, `_respond(model)` and `_metadata`. Filter params are comma-separated strings. Reader methods are protocol-declared in `ports.py`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists. Relevant: `integration/cli-analytics-storage.md`, `integration/cli-analytics-events.md`, `api/rest-api-patterns.md`, `api/endpoint-conventions.md`, `testing/testing-api-patterns.md`. I did not read them in this pass.

### Architectural Decisions
The router docstring records D1-D3: D2 is the repository key; D3 (in the handler comment) is the "most frequent project" label. CR-010 (handler comment): plugin-less sessions must show up in the unattributed bucket.

### Derived Conventions
Null means "unattributed" and is surfaced as a `None` field. The UI treats it as a special case through a boolean (`is_unattributed`), not a sentinel string.

---

## 4. Testing Landscape

### Existing Coverage
Backend, under `tests/codemie/`:
- `service/analytics/handlers/test_cli_analytics_handler.py` — unattributed bucket (L120), `is_unattributed` delegation (L142, L164), search delegation.
- `service/analytics/handlers/test_cli_analytics_handler_ordering.py` — ordering, and the project label as the most frequent project (L137, parametrised).
- `rest_api/models/test_cli_analytics_models.py` — nullable repository row.
- `rest_api/routers/test_cli_analytics_storage_wiring.py` — project-admin filter scoping.
- `repository/test_cli_analytics_repository.py` exists (only the pyc was listed).

Frontend: `__tests__/RepositoriesView.test.tsx`, `SessionsView.test.tsx`, and the hooks tests (vitest).

### Testing Framework and Patterns
pytest (8.3) with async tests and a mocked reader (handler tests delegate to a fake repo). vitest and testing-library for the UI.

### Coverage Gaps
No test found for per-project bucketing of one repository, nor for a null/empty project filter in SQL or the router. Reader SQL has no obvious unit coverage for `_sessions_cte` project conditions.

---

## 5. Configuration and Environment

### Environment Variables
Not examined in depth. `config.ANALYTICS_INGEST_MAX_BODY_BYTES` is used for ingest only.

### Configuration Files
`config/customer/customer-config.yaml` and `src/codemie/configs/customer_config.py` (both already modified in the working tree and unrelated to this task, as far as can be seen); the feature flag `cliAnalytics` is checked through `customer_config.is_feature_enabled`.

### Feature Flags and Deployment Concerns
The `cliAnalytics` flag gates every endpoint (404 when disabled). No migrations were observed to be involved: `project_name` already exists in `session_dims` (`rollups.py` L186, L1608).

---

## 6. Risk Indicators

- Speculative: grouping the repositories response by project would change row cardinality (more rows per repo when a repo spans projects). That would alter the `session_count` and `total_count` pagination for the non-branch mode, and the ordering tests in `test_cli_analytics_handler_ordering.py` (most-frequent-project test would be superseded).
- Speculative: the response shape could be kept flat (add the project to the bucket key) or become nested; the UI `aggregateRepos` currently assumes flat rows with `project_name`. The choice affects the OpenAPI model and UI types.
- The null-project filter needs a new sessions-side parameter. The existing `projects` filter cannot express it (`''` is normalised, `= ANY`), and mixing "null plus named projects" is a semantic question. Project admins are scoped via `FilterParams.resolve`: null-project sessions fall outside their visible set, so the null filter likely matters only for super admins (needs a decision).
- `get_session_cost_facts` is a protocol method (`ports.py`), so any signature change touches the port and the Postgres reader. Fakes in handler tests may need updating.
- The UI never forwards the project in the sessions modal, so the modal under one project group can show sessions from other projects for the same repo (this is the bug the filter addresses).
- The working tree has uncommitted changes in the customer config files; avoid mixing them in.
- Guides not read, so the conventions in section 3 are partly inferred.

---

## 7. Summary for Complexity Assessment

The change spans three backend layers (router `cli_analytics.py`, handler `cli_analytics_handler.py`, reader `postgres/reader.py` with `filters.py` and `ports.py`), the Pydantic models, and a small UI slice (`RepositoriesView.tsx`, `useCliAnalyticsSessions.ts`, `ExtendedSessionsModal` target, types). The surface is perhaps 6-10 files, with no DB migration or new dependencies. The data already carries `project_name`, and the UI already groups by project client-side with a "(no project)" group, so the work is mostly bucket-key and filter plumbing.

Novelty is low. It extends existing patterns (the `is_unattributed` flag and the `branch` filter are close analogues). The main design ambiguity is the response shape and the semantics of null/empty projects combined with the admin scoping and pagination. The wire contract with the UI must stay consistent.

Test posture: handler tests exist and are mock-based, so they are easy to extend. SQL and router-level coverage for the project filter is thin, and UI tests exist for the views. Key risks are pagination and ordering semantics changes, the port signature change, and the unscoped project in the UI sessions modal.

---

## 8. External References

None named by the task. The UI path it gave (`C:\Projects\codemie-dev\codemie-ui`) was resolved and read; findings are in Section 2.
