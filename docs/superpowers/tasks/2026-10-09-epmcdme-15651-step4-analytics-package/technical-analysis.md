# Technical Research

**Task**: analytics router package split
**Generated**: 2026-10-09
**Research path**: codegraph

---

## 1. Original Context

Implement delivery step 4 of Jira EPMCDME-15651 (split oversized REST API router modules): convert src/codemie/rest_api/routers/analytics.py (3,113 lines) into an `analytics/` package whose __init__.py builds one combined `router` and exports only `router` (no re-exports), split by concern per the ticket's plan item 4; keep move-only, no API change; re-point importers/callers/patch targets (src and tests). Note routers/cli_analytics.py is a separate router: check whether it imports from analytics.py and keep it out of scope. The ticket body is in docs/superpowers/tasks/2026-10-08-epmcdme-15651-split-router-modules/ticket.md and the user-resolved criteria in .../spec.md in the same dir. Steps 0-3 are done on this branch: step 2 (assistant/ package) in docs/superpowers/tasks/2026-10-08-epmcdme-15651-step2-assistant-package/ and step 3 (index/ package, the latest and closest precedent: private _common module, restated route-order criterion with a before/after dump script, stale-patch detector, patch-target retargeting rules, byte-identical bodies) in docs/superpowers/tasks/2026-10-09-epmcdme-15651-step3-index-package/. Guard tests: tests/codemie/rest_api/router_guards. The spec must decide the OpenAPI snapshot treatment of any router-local Pydantic class component-key renames if those classes move.

Notes from earlier steps to build on (do not re-derive): src/codemie/rest_api/routers/analytics.py is 3,113 lines. Step 0's research (docs/superpowers/tasks/2026-10-08-epmcdme-15651-split-router-modules/technical-analysis.md) covers the five target routers; steps 2 and 3 technical-analysis.md/spec.md show the format and the findings that mattered (route include order and registration, 500-line module cap, helpers shared between modules, patch retargeting to the module that resolves the name, OpenAPI component-key renames for same-named classes, router-level dependencies). Focus the new research on: the route/handler inventory of analytics.py grouped by concern with line ranges (the ticket's plan item 4 gives the intended module list), module-level helpers/constants and which handlers share them, router-local Pydantic classes and any same-named classes elsewhere (OpenAPI component-key rename risk: give the exact baseline keys/line numbers in tests/codemie/rest_api/router_guards/baselines/openapi.json), every importer and every `patch("codemie.rest_api.routers.analytics...")` / `routers.analytics` reference in src/ and tests/ (counts per file, including tests/unit/routers and tests/codemie/rest_api; separate it from `cli_analytics`), route-precedence-sensitive pairs involving analytics routes (static vs dynamic path conflicts, include order, the baseline route_precedence.json entries), `dependencies=` / auth on the router and on individual routes (to preserve, e.g. admin/permission dependencies), any unauthenticated or per-resource-check gaps that are pre-existing (to note, not fix), and any auth/access-check helpers that would move (for the /sanity note).

feature_area: analytics router package split
run_dir: /home/taras_spashchenko/EPAM/cm/codemie/docs/superpowers/tasks/2026-10-09-epmcdme-15651-step4-analytics-package

---

## 2. Codebase Findings

Tools were Read, Glob and codegraph (no grep, nothing executed). `analytics.py` and the 7 test files below were read in full, so their counts are exact; tree-wide counts are not grep-backed.

### Existing Implementations

`src/codemie/rest_api/routers/analytics.py`: 3,113 lines, 85 routes (84 GET, `POST /leaderboard/compute`), 15 helper functions, 12 public and 3 private module constants, 4 router-local Pydantic classes, no module-level state. `from __future__ import annotations` L21; `logger = logging.getLogger(__name__)` L58. `router = APIRouter(tags=["Dashboard Analytics"], prefix="/v1/analytics", dependencies=[Depends(authenticate)])` L136; decorator paths are relative (`/summaries`); no route sets `tags=`. Every handler also declares `user: User = Depends(authenticate)`. Most routes are 16-25 line wrappers: `@router.get(..., response_model, response_model_by_alias=True)` + `@handle_analytics_errors("...")` + `AnalyticsService(user).<method>(...)` + `_create_response(...)`.

Blocks (decorator to last line). All blocks are contiguous in source, so include order = source order reproduces today's `app.routes` sequence.

| Concern | Lines | Routes | Contents |
|---|---|---|---|
| usage (ticket name) | 470-866 (397) | 13 | summaries 470, assistants-chats, workflows, tools-usage, agents-usage, power-users, knowledge-sharing, top-agents-usage, top-workflow-usage, published-to-marketplace, webhooks-invocation, mcp-servers, mcp-servers-by-users 844 |
| activity (ticket name) | 869-1270 (402) | 11 | projects-spending 869, llms-usage, embeddings-usage, users-spending, budget-soft-limit, budget-hard-limit 1000, users-activity, users-unique-daily 1050, users 1102, projects-activity, projects-unique-daily 1213. The ticket does not say where usage ends and activity starts; the four spending/limit routes sit inside this range |
| cli | 1273-1524 (252) | 10 | cli-summary 1273 ... cli-tools 1502 |
| cli_insights | 1527-2005 (479) | 17 | 7 widgets 1527-1699 (weekday-pattern ... users); 8 `user_name`-keyed drilldowns 1702-1955 (user-detail ... user-repositories); 2 project widgets 1958-2005 |
| cli_enriched_users | 2008-2115 (108) | 5 | `_ENRICHMENT_DISABLED_MSG` 2008, `_ENRICHED_USER_ROUTE_PARAMS` 2010, `CLIEnrichedUserBaseParams` 2017, `CLIEnrichedUserParams` 2021, `_run_enriched_user_insight` 2027, routes 2053-2115 |
| spending | 2118-2507 (390) | 7 | `/spending` 2118, `_resolve_admin_budget_subject` 2212, `/budget_usage` 2249, `/user-project-spending` 2323, `/project-member-spending` 2373, `/engagement/weekly-histogram` 2417, `/spending/by-users/platform` 2452, `/cli` 2481 |
| leaderboard | 2510-2815 (306) | 11 | `_require_leaderboard_enabled` 2513; summary 2519, entries, user/{user_id} 2597, tiers, scores, dimensions, top-performers, snapshots, seasons, POST compute 2769, framework 2797 |
| routing | 2818-3113 (296) | 11 | summary 2818 ... classifier-overhead 3090, sessions/{session_id}/switches 3004 |

Header block, L58-467: `logger`; constants L61-73; `handle_analytics_errors` 76-123; `_ensure_routing_analytics_enabled` 126-133; `router` 136; spending helper cluster 139-429 (291 lines); `_create_response` 432-442; `AnalyticsFilterParams` 445, `AnalyticsQueryParams` 463. Group A in `test_analytics_capability_guards.py` (59 paths) = the 24 usage/activity + 10 cli + 17 cli_insights + 5 enriched + engagement + 2 by-users routes, a cross-check of the inventory.

Handlers over 60 lines: `get_summaries` 80, `get_users_list` 84, `get_user_spending` 92, `get_user_budget_usage` 72.

Helper sharing (readers by block):
- `_create_response`: every route except `/budget_usage`, `/user-project-spending`, `/project-member-spending`. `handle_analytics_errors`, `AnalyticsService`, `authenticate`, `User`: every block.
- `AnalyticsQueryParams` (usage, activity, cli, cli_insights, spending by-users, routing); `AnalyticsFilterParams` (routing summary and classifier-overhead; base of `CLIEnrichedUserBaseParams`). `CLIENT_SOURCE_FILTER_DESC` (usage, activity, cli-llms, spending); `USERS_FILTER_DESC` and `PROJECTS_FILTER_DESC` (usage, `get_users_list`, weekly-histogram).
- `ERROR_MSG_ACCESS_DENIED` / `ERROR_MSG_ADMIN_HELP`: `_authorize_admin_budget_view` (L388), `get_user_project_spending`, `get_leaderboard_user_detail`.
- `config`: `_create_response` (`config.is_local`), `AnalyticsQueryParams.per_page` default, spending (page defaults, `_resolve_admin_budget_subject` `ENABLE_USER_MANAGEMENT`), leaderboard (`_require_leaderboard_enabled`, entries default). `customer_config`: `_ensure_routing_analytics_enabled` and `_run_enriched_user_insight`. `logger`: decorator, `get_summaries`, `get_users_list`, spending code. `asyncio`, `get_async_session` (module level, plus function-local in `/budget_usage`), `member_spend_service`, `timezone` (module level, used by two spending routes and `_paginate_tabular`): spending only. `Path`: routing only.
- Spending-only: `_authorize_admin_budget_view`, `_paginate_tabular`, `_SPENDING_METRIC_DEFINITIONS`, `_build_spending_metric(s)`, `_calculate_time_until_reset`. No caller in the file or in codegraph (no covering tests): `_get_user_all_projects`, `_build_key_spending_tabular_data` and its callees `_get_key_spending_columns`, `_build_spending_row`; `service/analytics/handlers/budget_usage_service.py` L77/125/148 holds same-named copies the tests import. Unreferenced constants: `PROJECTS_FILTER_ADMIN_DESC` L61, `ERROR_MSG_PROJECT_EMPTY` L66.
- Router-local classes: `AnalyticsFilterParams`, `AnalyticsQueryParams`, `CLIEnrichedUserBaseParams`, `CLIEnrichedUserParams` (all `BaseModel` with `Query(...)` defaults, used only as `Depends()`).

### Architecture and Layers Affected

API router layer only, plus 7 test files and the guard baselines (expected unchanged). `main.py` unchanged.

### Integration Points

- `main.py`: L98 `analytics,` in the multi-line `from codemie.rest_api.routers import (...)`; L1068 `app.include_router(analytics.router)`, unconditional, between `metrics.router` L1067 and `cli_analytics.router` L1069. Only src importer found; codegraph shows every analytics.py symbol called only inside the file; `codemie-enterprise` returned no match. Both unprovable without grep.
- `routers/cli_analytics.py` (read in full): imports nothing from `analytics.py`; own `router` (`prefix="/v1/analytics/cli-analytics"`), `handle_errors`, `FilterParams`, `_respond` (`Cache-Control: private`), `_admin_gate`, `_ensure_enabled`. Out of scope. Its tests (`test_cli_analytics_ingest.py`, `test_cli_analytics_storage_wiring.py`, not opened) are named for it.
- Services and models: `AnalyticsService` (`service/analytics/analytics_service.py`), `member_spend_service`, `ResponseFormatter`, `EnrichedUserScope` (`handlers/cli_handler`), function-local `budget_usage_service` and `codemie.enterprise.litellm.dependencies`; response models `SummariesResponse`, `TabularResponse`, `UsersListResponse`, `AnalyticsDetailResponse` in `rest_api/models/analytics.py` (do not move).
- FastAPI 0.133.1 (both venvs): `get_typed_signature` evaluates string annotations in `inspect.unwrap(call).__globals__`, so under `from __future__ import annotations` each module holding a handler needs every annotation name (`User`, `datetime`, `JSONResponse`, `ClientSource`, the param classes) at module level; a missing name fails at import.
- Route precedence: `route_precedence.json` has 45 pairs, none under `/v1/analytics`. Scan of the 85 paths: only `/leaderboard/user/{user_id}` and `/routing/sessions/{session_id}/switches` are dynamic, with no literal route of the same shape; no first-segment dynamic route can shadow `/v1/analytics/cli-analytics/*`. Every path has one method. The guards' dump holds `{path, methods}` only (`app_probe.py` L79-84).

### Patterns and Conventions

- Auth to preserve: router-level `Depends(authenticate)`; route-level `dependencies=[Depends(admin_or_maintainer_or_auditor_access), Depends(_require_leaderboard_enabled)]` on 10 leaderboard GETs (L2526, 2548, 2605, 2635, 2657, 2679, 2701, 2724, 2752, 2806) and `[Depends(admin_access_only), Depends(_require_leaderboard_enabled)]` on `POST /leaderboard/compute` (L2776). Effective order: authenticate, access check, flag; `test_analytics_leaderboard.py` (CR-004) asserts 403 before 503. None of the three dependencies is defined here (`security/authentication.py`).
- Inline checks: `get_leaderboard_user_detail` L2616 (admin/maintainer/auditor, duplicates the route dependency); `_authorize_admin_budget_view` L388 (admin or auditor returns; else needs a shared admin project; else 403); `get_user_project_spending` (unknown target: 403 for non-admin, 404 for admin); feature gates: `_ensure_routing_analytics_enabled` (404), `_run_enriched_user_insight` (403, `userEnrichmentEnabled`), `_require_leaderboard_enabled` (503, plain `HTTPException`).
- `_create_response` sets `Cache-Control: public, max-age=300` plus ETag unless `config.is_local`; `/spending` (per-user) uses it.
- Wrapper `handle_analytics_errors` uses `functools.wraps` (endpoint `__module__`, `__name__`, signature follow the handler). `get_projects_unique_daily` raises a plain `HTTPException(400)` inside it; the test expects the resulting 500.

### OpenAPI component keys (`baselines/openapi.json`)

None of the four router-local classes has a component key: class dependencies expand to query parameters. The only `Analytics*` key is `"AnalyticsDetailResponse"` L1261-1281 (defined in `rest_api/models/analytics.py`; neighbors `AllowedModelsUpdateResponse` L1168-1259, `ApplicationInfo` L1282). Keys for `AnalyticsFilterParams`/`AnalyticsQueryParams` would sort between L1281 and L1282 and `CLIEnriched*` between `BulkWriteFileResponse` L4908-4936 and `Category` L4938: none exist. Codegraph finds one definition of each class name. No `codemie__rest_api__routers__analytics__*` key exists. Operation ids come from handler names and paths, which are unchanged.

---

## 3. Documentation Findings

### Guides and Architecture Docs

No guide covers the analytics router. Read: `project-structure.md`, `rest-api-patterns.md`, `endpoint-conventions.md`, `quality-gates.md`, head of `cli-analytics-events.md` (routes under `/v1/analytics/cli-analytics/`, the other router): no `routers/analytics.py` cite. `cli-analytics-storage.md` and `security-patterns.md` not opened.

### Architectural Decisions

No ADR. Binding: ticket (module list `_common, usage, activity, cli, cli_insights, cli_enriched_users, spending, leaderboard, routing`; criteria 1-8) and step-0 `spec.md` "Ticket criteria as resolved" (500-line cap binds, OpenAPI identical, export only `router`, `/sanity` for step 4, move-only). Step-3 additions: private modules, restated route-order criterion with a one-off dump script, stale-patch detector, byte-identical bodies, `COMPARE` (a)-(e).

### Derived Conventions

Step 2/3 patch rules: patch the module whose code resolves the name; whole-name patches bind to the using module; `<Name>.<attr>` keep their tail; imports take the defining submodule (tests may import underscore names); underscore names imported in `src/` only from private modules.

---

## 4. Testing Landscape

### Existing Coverage

Seven files reference `routers.analytics` (ticket: 8 files, 55 patches; the eighth file and 4 strings were not found without grep):

| File | Patch strings | Import statements |
|---|---|---|
| `tests/codemie/rest_api/routers/test_analytics.py` (1,697 lines) | 33: `AnalyticsService` 26, `logger` 3 (L164, 184 decorator; L434 `get_summaries`), `asyncio.to_thread` 4 (L1543-1622, `/budget_usage` with `user_id`) | 49 (L39 module level: `_create_response`, `handle_analytics_errors`; function-local: handlers, `AnalyticsQueryParams`, `_authorize_admin_budget_view` x6) |
| `.../test_analytics_leaderboard.py` | 8: `config` 5, `AnalyticsService` 3 | 1 (`router`) |
| `.../test_analytics_capability_guards.py` | 4: `config` L197, `get_async_session` L271 and L291, `_authorize_admin_budget_view` L285 | 1 (`router`) |
| `.../test_analytics_auditor.py` | 2: `AnalyticsService` L71, `_create_response` L79 | 3 |
| `.../test_analytics_member_spending.py` | 2: `asyncio.to_thread` L77 (helper reused by ~10 tests), `get_async_session` L337 | 18 |
| `.../test_analytics_budget_usage_labels.py` | 0 | 2 |
| `tests/unit/routers/test_analytics_enriched_user.py` | 2: `customer_config` L94, `AnalyticsService` L101 | 1 (5 names, `CLIEnrichedUserParams` included) |

Totals: 51 patch strings, 75 import statements. Under the ticket's module names the strings resolve to: `_common` 2; usage/activity handlers 22 (`get_summaries` 9, assistants-chats 2, workflows, tools, agents, llms, users list 2, projects-unique-daily 5); cli 2; cli_insights 3; spending 9; leaderboard 11; cli_enriched_users 2. Imports of `router` stay valid. Other patches in these files already target `codemie.clients.postgres.*`, `codemie.enterprise.litellm.dependencies.*` or service classes and are untouched by the move (`get_user_budget_usage` and `get_user_spending` import them function-locally). `cli_analytics` tests, `test_main_*` and `router_guards` do not reference the module by name (name-based, unverified).

### Testing Framework and Patterns

pytest, `pytest.mark.asyncio`/`anyio`, `patch` decorators, handlers called directly as functions (bypassing `Depends`), and `FastAPI()` + `include_router(router)` + `dependency_overrides[authenticate | admin_or_maintainer_or_auditor_access | admin_access_only]` with `httpx.ASGITransport` (works by callable identity). Guards: `poetry run pytest tests/codemie/rest_api/router_guards -n 0 --show-capture=no -q`; the guarded app pins `ENV=local`, `ENABLE_USER_MANAGEMENT=true`, `ANALYTICS_DEFAULT_PAGE_SIZE=20` and includes the analytics router unconditionally.

### Coverage Gaps

No test names the 11 routing routes or `_ensure_routing_analytics_enabled` (codegraph: "no covering tests found"). Group A (59 paths) is 200-only with ES unconfigured. Leaderboard routes are tested for 503/403 and one 200 (summary). About 22 handlers are called directly in tests (plus the 59 Group A paths over ASGI); enriched `job-title-group` only via Group A. Whole-name patches that miss their module stay silent when the mock is never asserted, e.g. L285 passes with any target because `_MOCK_USER.is_admin=True`.

---

## 5. Configuration and Environment

### Environment Variables

`LEADERBOARD_ENABLED` (read per request), `ANALYTICS_DEFAULT_PAGE_SIZE` (`Query` defaults, evaluated at import), `ENABLE_USER_MANAGEMENT` (`_resolve_admin_budget_subject`), `config.is_local` (cache headers). Customer-config features: `routingAnalytics`, `userEnrichmentEnabled`.

### Configuration Files

`pyproject.toml`: ruff E,F,B,N,C4,G,T20,RSE,SIM,C,W,PERF,ISC; mccabe 16; line length 120; no per-file ignore for analytics. `sonar-project.properties` and `.coveragerc` have no per-file entries. `main.py` registration unchanged. Baselines regenerate with `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`. Logging: the `codemie` logger owns the handler and the format has no `%(name)s`; children propagate.

### Feature Flags and Deployment Concerns

Flags above only. No pipeline; one MR after the whole ticket; the MR template requires a `/sanity` request for security-relevant moves. Untracked `opencode.json.lock` and `skills-lock.json` are never staged.

---

## 6. Risk Indicators

- Speculative: size. Usage/activity span about 800 lines (cut near L869: 397/402 plus a ~35-line header); `cli_insights` is 479 plus header; spending is 390 route lines plus 291 helper lines (a private support module).
- Speculative: whole-name patches bind per using module (`AnalyticsService`, `config`, `customer_config`, `logger`, `_create_response`, `_authorize_admin_budget_view`, `get_async_session`, `asyncio`); a string aimed at the wrong existing module passes silently (capability_guards L285 passes under any target).
- Leaderboard tests patch one `config` serving `_require_leaderboard_enabled` and `_create_response`. Speculative: a split mocks only one.
- Speculative: per-module `getLogger(__name__)` renames loggers.
- Missing annotation names fail at import.
- `/sanity` code: `authenticate`, 11 leaderboard dependencies and order (CR-004), `_authorize_admin_budget_view`, `_resolve_admin_budget_subject`, feature gates, `handle_analytics_errors`, `_create_response`.
- Pre-existing, not to fix: unknown `user_id` gets 404 before authorization in `_resolve_admin_budget_subject`; per-user `/spending` is `Cache-Control: public`; `/projects-unique-daily` 400 becomes 500; scoping delegated to `AnalyticsService` (unread).
- Speculative: guards dump lacks names and dependencies, so a one-off dump script is needed; no baseline change expected.
- Unverified: ticket's 55 patches/8 files versus 51/7 measured; no grep.

---

## 7. Summary for Complexity Assessment

One 3,113-line file with 85 near-identical wrappers; the ticket lists `_common` plus 8 route modules. Router layer only; `main.py` is unchanged. Surface: 7 test files (51 patch strings, 75 imports), no guide row, `cli_analytics` untouched. Third use of the step 2/3 pattern; concern blocks are contiguous, so include order equals source order. No router-local class is an OpenAPI component; no route pair involves analytics. New: prefix `/v1/analytics` with relative paths, 11 routes with extra access dependencies, a spending helper cluster beside 390 route lines. Routing has no tests. Step-0 estimate: M, 1-1.5 days.

---

## 8. External References

Read in full: ticket.md (plan item 4 module list; export only `router`; wrappers out of scope); step-0 spec.md (500-line cap binds; OpenAPI identical; `/sanity` for step 4); step-0 technical-analysis.md; step-2 spec.md (patch rules); step-3 spec.md, technical-analysis.md, plan.md head (dump script, stale-patch detector). All resolved.
