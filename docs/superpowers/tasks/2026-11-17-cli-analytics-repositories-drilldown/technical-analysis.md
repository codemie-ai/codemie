# Technical Research

**Task**: cli analytics repositories branches
**Generated**: 2026-11-17
**Research path**: filesystem

---

## 1. Original Context

Jira EPMCDME-17349, Story "CLI Analytics Repositories Drill-down". This repo is the CodeMie BACKEND; the story is UI-driven but its backend dependency (D1) is the part relevant here.

Context: Repositories tab in AI Attribution / CLI Analytics shows paginated list of projects; each project is an accordion expanding to repositories then branches. Clicking project/repository/branch opens one shared modal with Summary block and Sessions table. Repositories already available as separate paginated list filterable by project. Branches loaded per project without paging. Each project row currently carries nested repositories. Backend contract for per-project repository paging and per-repository branch paging is not confirmed. Sessions with no project/repository/branch must stay reachable.

Story: As a platform admin/team lead I want to drill down projects -> repositories -> branches -> sessions, one modal at a time, so each screen loads only its level's data.

ACs: AC1 Repositories tab lists projects only (paginated), no nested repo/branch data requested. AC2 project modal: same Summary + paginated repositories list (page size 10). AC3 only that project's summary and first page of repos loaded. AC4 repo modal: Summary + paginated branches list (10). AC5 only that repo's summary and first page of branches. AC6 branch modal unchanged. AC7 sessions with no project/repo/branch: one "Unattributed" row per level as last row of last page, outside the page of 10, clickable. AC8 Projects search filters by project name only. AC9 closing top modal leaves lower modal open on same page. AC10 failed request -> summary stays, list error state. AC11 empty -> empty state, no pagination.
Out of scope: search/filters in repo/branch lists; branch modal changes; implementing backend endpoints (requested as dependency); client-side branch paging; finding repo by name from Projects tab.
Decisions: D1 story depends on backend endpoints; each returns only needed data; built in parallel, released as one change. D2 Projects search = project names only. D3 no search in repo/branch lists. D4 unattributed clickable row per level. D5 page size 10. D6 nested repo data in project rows and per-project branch loader are removed; project modal repo list uses existing paged repositories listing filtered by project; branch list uses paged branches endpoint. D7 Summary comes from clicked row (project/repo rows must carry Summary metrics). D8 backend + story built in parallel, one release. D9 Unattributed row is last row of last page, outside the 10, once per list.
Backend dependency (D1): Projects: paginated list with totals only, no nested repositories (new or changed). Repositories of one project: paginated with row metrics (existing paged repositories listing filtered by project). Branches of one repository: paginated with row metrics (new). Each list returns the Unattributed row where sessions have no project/repo/branch. Contract to confirm: branches endpoint pages, projects list omits repositories.

Scope addition (coordinator): also produce an FE-facing API contract file; report current FE request/response shapes, where FE contract docs live in this repo, and the convention.

---

## 2. Codebase Findings

### Existing Implementations

Base: router `src/codemie/rest_api/routers/cli_analytics.py` (prefix `/v1/analytics/cli-analytics`, all reads gated by `_admin_gate`, `handle_errors`), handler `src/codemie/service/analytics/handlers/cli_analytics_handler.py`, ports `src/codemie/repository/cli_analytics/ports.py`, Postgres reader `src/codemie/repository/cli_analytics/postgres/reader.py`, models `src/codemie/rest_api/models/cli_analytics.py`.

**Purpose 1: paged list of projects with totals only.**
- `GET /projects` — `cli_analytics.py:795-815`; params `page` (default `DEFAULT_PAGE`, ge 0), `per_page` (default `DEFAULT_PER_PAGE`, le `MAX_PER_PAGE`), `search` ("Filter by project or repository name"), plus `FilterParams`. Docstring: "each with its repositories and their branches nested".
- Handler `get_projects` `cli_analytics_handler.py:1377-1407`: the DB pages projects (`get_project_page`, `project_page_sql.py`: order = spend `cost_key DESC`, NULL project (unattributed) LAST via `p.project_name IS NULL`; `ports.py:617`); then reads ALL sessions of the page's projects with `include_branches=True` (`_repository_rows`) and nests via `_group_repositories_into_projects` (`:708`). Pagination via `_pagination` (`{page, per_page, total_count, has_more}`, zero-based `page`).
- Search rule: `_SEARCH_CONDITION` (`project_page_sql.py:~36`) matches project name OR repository name (substring, lower-cased, `strpos`). Docstring `ports.py:625` says the same. Contradicts D2/AC8 (project names only).
- Row model `LocalAnalyticsProjectRow` (`models/cli_analytics.py:210-225`): `project_name` (None = no project), `repository_count`, `session_count`, `turns`, `cost_usd`, `files_changed`, `lines_added/removed`, `net_lines`, `tool_calls`, `tool_calls_success`, `tool_success_rate`, estimate buckets + class lists, and REQUIRED `repositories: list[LocalAnalyticsRepositoryRow]`. Response envelope `{data:{rows, pagination, ...estimate buckets}, metadata}`.
- Unattributed project: one row with `project_name: null`, already last, but it is one of the page's `per_page` rows (inside the page, not outside). It exists only on the page where it falls.

**Purpose 2: paged repositories of one project with row metrics.**
- `GET /repositories` — `cli_analytics.py:763-792`; params `page`, `per_page`, `include_branches` (default False), `search` (repository name), `FilterParams` (`projects` = comma list of project names, `repositories`, `users`, time, subscription_type, project_origin). Omitting BOTH `page` and `per_page` forces `include_branches=True` and returns everything (`:782-785`).
- Handler `get_repositories` `:1305-1375`; reader `reader.py:2075` `get_repository_ranking`. Without branches: groups by repository ONLY (not project), paged in SQL (`rn` window), `project_name` on a row = most frequent project of that repo (ties alphabetical). Rank order = session count DESC, then cost DESC, then name (`reader.py` `ranked` CTE) — not spend, and the NULL-repository (unattributed) group is NOT forced last.
- Row model `LocalAnalyticsRepositoryRow` (`models/cli_analytics.py:175-194`): `repository` (None = unattributed), same metric fields, `project_name`, optional `branches`. Pagination `{page, per_page, total_count, has_more}`; with `include_branches=True` pagination is a synthetic one page (`:1352-1355`).
- Project filter is the generic `projects` param of `FilterParams`: `FilterParams.resolve` (`cli_analytics.py:~420-470`) intersects it with the viewer's admin projects for non-super-admins; a project filter has no "unattributed project" form on `/repositories` (`is_project_unattributed` is read only by `/sessions`, `:938`, via `resolve_sessions`).
- Repositories of the unattributed project: not reachable through `/repositories` with a project filter (no param; FE works around this today, see Section 2 FE).

**Purpose 3: paged branches of one repository with row metrics.**
- None found as an endpoint. Searched: router for `branch` routes (only `/sessions` has a `branch` query param `:~940-950`; `FilterParams` has no `branch`), ports/reader for branch-level paging (`get_repository_ranking(include_branches=True)` returns every (project, repository, branch) group unpaged, `paging = ""` at `reader.py` include_branches branch). Branch rows exist only as `LocalAnalyticsRepositoryBranchRow` (`models/cli_analytics.py:161-172`: `branch` None = no branch, metrics, no `project_name`, no bucket fields) nested in a repository row.
- No route `projects/{project}/branches` exists in this repo (the FE branch comments mention one; see FE).

**Purpose 4: summary of the clicked row.** Project row and repository row already carry metric fields (above); the project row also carries `repository_count`. Summary metrics for a row come from the row, no backend summary endpoint exists.

**Purpose 5: sessions of a project / repository / branch / unattributed.** `GET /sessions` `cli_analytics.py:922-953`: `is_unattributed` ("no attributed repository"), `is_project_unattributed` ("no project", super-admin only), `branch`, `search`, `framework`, paging, `FilterParams.repositories`/`projects`. No "branch unattributed" flag found (searched `is_.*unattributed` in `rest_api`); sessions with repository set but no branch are not selectable via a dedicated flag.

Other touched code (not above): `src/codemie/repository/cli_analytics/postgres/reader.py` `get_repository_facts`/`get_repository_buckets` (counts and estimate buckets of the page's groups); `tests/codemie/rest_api/router_guards/baselines/openapi.json` (committed OpenAPI baseline, 87.5k lines).

### Decisions Already in the Code
- Zero-based `page`, `per_page` bounded by `MAX_PER_PAGE`, pagination object `{page, per_page, total_count, has_more}` (`_pagination` `handler:728`) — the convention for paged lists.
- Unattributed is represented as a `null` name on a normal row (project `null`, repository `null`, branch `null`), not a separate flag; projects: ordered last by SQL (`project_page_sql.py` `_PROJECT_ORDER`); repositories: ranked by session count, no last-placement rule. Reason: none found.
- Paging is in SQL for projects (`get_project_page`) and non-branch repositories; the branch variant is rolled up in Python after an unpaged read (`_build_repository_rows`, `_group_branches_into_repositories`).
- Ordering differs by list: projects by spend, repositories by session count then spend.
- Viewer scope: non-super-admins are restricted to their admin projects; no-project data is super-admin only (`cli_analytics.py:~465`).
- Contract changes require regenerating the OpenAPI baseline (`tests/codemie/rest_api/router_guards/baselines/openapi.json`, per spec of EPMCDME-16440 item 7).

### Task Statements Checked Against the Code
- "Repositories already available as separate paginated list filterable by project": holds (`/repositories` + `projects`), with caveats: rows grouped by repository only, ordered by session count, no unattributed-project filter, unattributed repository group not placed last.
- "Branches loaded per project without paging": holds on FE (loads `/repositories?projects=X` unpaged); backend has no branch endpoint (section above).
- "Each project row currently carries nested repositories": holds (`LocalAnalyticsProjectRow.repositories` required; handler reads every nested session).
- Contract question "projects list omits repositories": contradicted today — field required and populated; removing it keeps `LocalAnalyticsProjectRow.repositories` in place, consumed by the FE `ProjectRow.repositories` (FE `src/types/cliAnalytics.ts:217`) and `RepositoriesView` (FE).
- "Contract to confirm: branches endpoint pages": no endpoint exists; unverified beyond that.
- D2/AC8 "Projects search = project names only": contradicted — `/projects` search also matches repository names (`project_page_sql.py` `_SEARCH_CONDITION`, `ports.py:625`, router description `:807`). The `get_projects` handler also derives `window` from `matched_session_ids`, so totals follow the search.
- D6 "project modal repo list uses existing paged repositories listing filtered by project": keeps `/repositories` in place; its consumers: FE `useCliAnalyticsRepositories` (also used by other FE views), FE `fetchProjectBranches` (unpaged form), and `include_branches` forced-all behavior.
- D9 "Unattributed row last row of last page, outside the 10, once per list": not implemented in any list today (projects: inside the page; repositories: by rank; branches: n/a). `total_count` today includes the unattributed group.
- D7 "project/repo rows carry Summary metrics": holds (both models carry the metric set).

### Consumers of Changed Contracts
- `GET /v1/analytics/cli-analytics/projects` (`ProjectRow.repositories`, search semantics): consumers found in this repo — router `cli_analytics.py:795`, handler `:1377`, models `:210`, tests `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py:850` (`test_projects_sum_repositories_and_compute_success_from_counts`), `test_cli_cost_cross_screen_parity.py:153` (projects activity row vs CLI insights figure), `test_bounded_reader.py` (get_project_page). FE: `useCliAnalyticsProjects`, `RepositoriesView.tsx`, store `fetchProjects`, `ProjectRow`.
- `GET .../repositories` (`include_branches`, forced-all default): handler `:1305`, tests `test_cli_analytics_handler_ordering.py:161,177,188`; FE `useCliAnalyticsRepositories`, `fetchRepositories`, `fetchProjectBranches`, `RepositoriesTable`.
- OpenAPI baseline `tests/codemie/rest_api/router_guards/baselines/openapi.json` (guard test; changes on any new route/model field).
- `.ai-run/guides/integration/cli-analytics-*.md` do not document these read endpoints (grep of "repositor" hits only registration/ingest text), so no guide consumes them.

### Architecture and Layers Affected
Router (`cli_analytics.py`) -> handler (`cli_analytics_handler.py`) -> reader port (`ports.py`) -> Postgres reader and SQL builders (`reader.py`, `project_page_sql.py`) -> Pydantic response models (`models/cli_analytics.py`). No ORM models or migrations in the read path.

### Integration Points
- `FilterParams` / `LocalAnalyticsFilter` (`cli_analytics.py:382`, `repository/cli_analytics/filters.py`) shared by every read route; viewer scope through `viewer_access`.
- Pricing: `model_rates_for`, `CatalogPricer`, estimate buckets (`_dashboard_buckets`, `buckets`) attached to project and repository rows.
- `dashboard_cache` (`cached_dashboard`) wraps some routes (cost, users); repositories/projects are not cached.
- Errors: `handle_errors`, `_statement_timeout_as_504`; `/projects` has no timeout wrapper, `/repositories` does.

### Patterns and Conventions
- Response envelope `{"data": ..., "metadata": ...}` built with `_metadata(...)` and `_respond(payload, ResponseModel)`.
- Query param style: `Query(default, ge, le, description)`; comma-separated multi-values split by `FilterParams._split`.
- Reader methods live on the `ports.py` protocol, are required to take a `LocalAnalyticsFilter`, and fail closed on scope.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/api/rest-api-patterns.md`, `endpoint-conventions.md` (P0/P1 for API); `.ai-run/guides/integration/cli-analytics-storage.md` and `cli-analytics-events.md` (ingest/storage; no read-endpoint contract for repositories/projects).
- No guide documents `/repositories`, `/projects` response shapes.

### Architectural Decisions
- Prior spec `docs/superpowers/tasks/2026-10-28-epmcdme-16440-backend-analytics-read/spec.md`: read-route scope modes, models in `rest_api/models/cli_analytics.py`, "Regenerate `.../openapi.json`".
- Inline: `_group_branches_into_repositories` docstring ties tool_success_rate to EPMCDME-15585 (backend computes rates, UI never averages).

### Derived Conventions
- FE contracts for backend changes live in the task dir: `docs/superpowers/tasks/<date>-<slug>/fe-contract.md`. Example: `docs/superpowers/tasks/2026-08-26-optional-project-description/fe-contract.md` — header (ticket, backend branch, Status: proposed, Applies to), Summary, per-endpoint Request/Response with JSON samples, behavior table, "Recommended FE usage", OpenAPI note ("no committed schema file", though a baseline exists for guards), Backwards compatibility. Also `docs/superpowers/tasks/2026-09-21-status-persistence-api/contract-questions.md` (open cross-team contract questions).
- `docs/` top level holds design notes (e.g. `docs/activity-events-ui-plan.md`); no dedicated FE-contract directory; `docs/codemie/analytics/*.json` are exported analytics data, not contracts.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` (projects sums, line 850), `test_cli_analytics_handler_ordering.py` (repository-only/branch modes, lines 161-188), `test_cli_cost_cross_screen_parity.py`, `tests/codemie/repository/cli_analytics/postgres/test_bounded_reader.py`, `tests/codemie/rest_api/routers/test_cli_analytics_*.py` (wiring, flags, timeouts, owner sessions), `tests/codemie/rest_api/router_guards/baselines/openapi.json`.

### Testing Framework and Patterns
pytest (async handlers with fake readers/ports; router tests with TestClient); `test_ports` checks the reader protocol naming (comment at handler `:1403`).

### Coverage Gaps
- No found test for `/projects` search semantics (repository-name match) or for unattributed placement in `/repositories` pages (not exhaustively verified).
- No branch-level paging exists to test.

---

## 5. Configuration and Environment

### Environment Variables
`CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS`, `CLI_ANALYTICS_DASHBOARD_REQUEST_BUDGET_S` (`config.py`; used by `_statement_timeout_as_504`).

### Configuration Files
Feature enablement via `_ensure_enabled()` (`cli_analytics.py:173`); storage settings per `cli-analytics-storage.md`.

### Feature Flags and Deployment Concerns
No flag specific to repositories/projects found. No Dockerfile/CI change surface identified.

### Frontend (read-only, `/Users/yevhen_slyva/codemie-dev/codemie-ui`)
- Local FE branch `EPMCDME-17301_cli-analytics-branches-on-expand` (3 commits ahead of `main`; FE checkout is on `main`). A FE story folder `docs/stories/2026-11-17-cli-analytics-repositories-drilldown/` exists untracked (story, technical-analysis).
- Client: `src/store/cliAnalytics.ts` `fetchProjects` -> `GET v1/analytics/cli-analytics/projects`; `fetchRepositories` -> `GET .../repositories`; `fetchSessions` -> `GET .../sessions`. Hooks in `src/pages/analytics/components/cli-analytics/hooks/` (`useCliAnalyticsProjects`, `useCliAnalyticsRepositories` (page state 1-based in hook, `initialPerPage===0` means omit paging = fetch all, search debounced 350 ms), `useCliAnalyticsSessions`, `params.ts` `buildCliAnalyticsParams`, and on the 17301 branch `useCliAnalyticsProjectBranches`). Views: `views/RepositoriesView.tsx`, `RepositoriesTable.tsx`, `ExtendedSessionsModal.tsx`.
- Types (`src/types/cliAnalytics.ts:160-225`): `RepositoryBranchRow {branch|null, session_count, turns, cost_usd, files_changed, lines_added|null, lines_removed|null, net_lines|null, tool_calls, tool_calls_success, tool_success_rate}`; `RepositoryRow` = same metrics + `repository|null`, `project_name?`, `branches?`; `CliAnalyticsRepositoriesData {rows, pagination{page, per_page, total_count, has_more}}`; `ProjectRow {project_name|null, repository_count, ...metrics, repositories: RepositoryRow[]}`; `CliAnalyticsProjectsData {rows, pagination}`. These mirror the backend models 1:1 (estimate buckets, class lists and `metadata` not typed in the cited block).
- 17301 branch loader: `fetchProjectBranches(projectName, params)` -> `GET v1/analytics/cli-analytics/repositories` with `projects=<name>` (or `is_project_unattributed=true` when name is null) and NO page params (so backend forces include_branches and returns all); result cached per `projectBranchesKey`. Constant `UNATTRIBUTED_PROJECT_PATH = 'project_unattributed'` and comments about `projects/{project}/branches` are stale vs the code (no such route in backend; FE sends `is_project_unattributed` to `/repositories`, which the backend `/repositories` does not declare, so it is ignored there; `/sessions` is the only declared consumer).
- Sessions modal passes `branch`, `repositories`, `is_unattributed`, `is_project_unattributed` to `/sessions` (`useCliAnalyticsSessions.ts:103-117`). Unattributed label constant `UNATTRIBUTED_LABEL = 'Unattributed'` (`src/constants/cliAnalytics.ts`).
- FE sends 1-based page in hook state; backend page is zero-based (conversion location not read; check before writing the contract).

---

## 6. Risk Indicators

- `/projects` row keeps required `repositories` (and handler reads all nested sessions) — keeps an existing mechanism in place; consumers listed under Consumers of Changed Contracts (FE `ProjectRow`, handler tests at `test_cli_analytics_handler.py:850`).
- Projects search matches repository names today (`project_page_sql.py` `_SEARCH_CONDITION`); D2/AC8 contradicts it; the same match drives `matched_session_ids` and window totals.
- `/repositories` ranks by session count, groups by repository only, and has no "unattributed project" selector; an `is_project_unattributed` param FE already sends is silently ignored there (`FilterParams` only; read by `/sessions`).
- No branch paging exists; branch rows exist only nested in `get_repository_ranking(include_branches=True)`, which has no paging and returns the (project, repository, branch) groups.
- Unattributed-row placement (D9, outside the page, `total_count` meaning) is unlike every current list; `total_count` today counts the unattributed group.
- A repository name is not scoped by project in the repo-only grouping, so one repository used by two projects collapses into one row labelled with the most frequent project (`reader.py:2075` docstring) — relevant to "repositories of one project".
- Super-admin-only access to the no-project data and the viewer-scope intersection apply to any new list.
- Speculative: contract changes will require refreshing `tests/codemie/rest_api/router_guards/baselines/openapi.json`.
- Speculative: the FE contract file must name page-index base (backend zero-based) and the null-name convention for unattributed rows.
- FE comments referencing `projects/{project}/branches` do not match any backend route.
- Existing endpoints are consumed by other FE views; shape changes need an explicit compatibility statement in the FE contract.

---

## 7. Summary for Complexity Assessment

The change sits in the CLI analytics read path: router `cli_analytics.py`, handler `cli_analytics_handler.py`, reader port and Postgres reader/SQL builders, and Pydantic models, plus the OpenAPI baseline and an FE contract doc. No ORM, migrations or config. The projects list exists and is paged in SQL, but it nests repositories and branches (required field, all nested sessions read) and its search also matches repository names. The repositories list exists and is paged in SQL by repository, filterable by `projects`, but is ordered by session count, has no unattributed-project selector and no last-row placement. No branch list endpoint or branch paging exists at any layer.

Technical novelty is moderate: paging patterns and the pagination envelope are established, but branch-level paging, an "Unattributed row outside the page" rule and project-scoped repository paging have no precedent in these lists. Test coverage of the area is real (handler, ordering, router wiring, reader) but nothing pins search semantics or unattributed placement. Key risks: removing nested data from `/projects` touches FE consumers and existing tests; D9 changes `total_count`/page semantics; the FE already sends a parameter the backend ignores.

The FE repo's types mirror the backend models 1:1, so a contract file can be written against `models/cli_analytics.py`. FE contract convention: `fe-contract.md` in the task directory (example above).

---

## 8. External References

None named by the task. FE repository (named by the coordinator, read-only) resolved: `/Users/yevhen_slyva/codemie-dev/codemie-ui`; facts under Section 5 "Frontend". Branch `EPMCDME-17301_cli-analytics-branches-on-expand` read via git show; the FE untracked story folder `docs/stories/2026-11-17-cli-analytics-repositories-drilldown/` was not read.
