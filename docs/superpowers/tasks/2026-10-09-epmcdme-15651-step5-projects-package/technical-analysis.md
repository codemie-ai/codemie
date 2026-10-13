# Technical Research

**Task**: projects router package split
**Generated**: 2026-10-09
**Research path**: codegraph

---

## 1. Original Context

Implement delivery step 5 of Jira EPMCDME-15651 ("Split oversized REST API router modules into smaller per-concern modules") on branch EPMCDME-15651_split-router-modules. Split src/codemie/rest_api/routers/projects.py (1,253 lines) into the same-named package src/codemie/rest_api/routers/projects/. Move-only: handler bodies, decorators, signatures and dependencies stay byte-identical; no API change. The package __init__.py builds one combined `router` (same prefix/tags/dependencies as today) from bare `APIRouter()` sub-routers and exports only `router` (`__all__ = ["router"]`), no re-exports. Route modules are per concern (target <=500 lines each); private support modules have a leading underscore. src importers (main.py etc.) and tests that import or patch `codemie.rest_api.routers.projects.*` must be retargeted to the defining submodule (~265 patch refs in tests/codemie/rest_api/routers/test_projects_router*.py, test_project_model_availability*.py). Guard tests in tests/codemie/rest_api/router_guards/ (byte-exact app.openapi() snapshot, route-precedence baseline, hermetic app probe) must stay byte-identical; a component-name change from moving a class with a colliding name would force a snapshot re-baseline, so find any such classes (steps 1 already moved project models to rest_api/models/project.py). Logic moves into services are NOT in this step (step 7). Auth/access-check code moves here, so the MR will need /sanity.
Ticket body and user-resolved criteria: docs/superpowers/tasks/2026-10-08-epmcdme-15651-split-router-modules/{ticket.md,spec.md}. Lessons and proven method from steps 2-4 (read the step 4 sections of local/router-split/HANDOFF.md and the step 4 technical-analysis for the expected shape): docs/superpowers/tasks/2026-10-09-epmcdme-15651-step4-analytics-package/technical-analysis.md. Research needs: route inventory (count, paths, methods, decorator-level dependencies, router prefix/tags), proposed module grouping with line counts, shared helpers/constants/classes and which module each belongs in, every src importer and every test import/patch of routers.projects (count per test file and per target name), name collisions that could change OpenAPI component names, route-precedence overlaps, auth/access-check sites, pre-existing gaps to preserve (do not fix), risk indicators.

---

## 2. Codebase Findings

Tools: Read, Glob, codegraph; no grep, nothing executed. `projects.py` and the five test files below were read in full, so their counts are hand tallies (about plus or minus 3); tree-wide absence claims are unproven.

### Existing Implementations

`src/codemie/rest_api/routers/projects.py`: 1,253 lines, 14 routes, 30 helpers (3 async), **0 classes**, 4 other top-level names: `router` L79, `_spend_repo` L85 (a `ProjectSpendTrackingRepository()` instance), `_WIDGET_COLUMNS` L88-113, `logger` L116. `from __future__ import annotations` L15. `router = APIRouter(tags=["Projects"], prefix="/v1", dependencies=[])`; decorators carry full `/projects...` paths and none sets `tags`, `dependencies` or `operation_id`. There is no router-level `authenticate`: R1-R6 declare `user: User = Depends(authenticate)`, R7-R14 get it through `Depends(_authorize_project_access)`. The ticket's size (1,483 lines, 13 routes, 29 classes) is stale: step 1 moved the classes, `update_allowed_models` was added.

Routes in source order (= registration order), spans from the decorator:

| # | Route | Handler, lines | Access |
|---|---|---|---|
| R1 | POST /v1/projects (201) | create_project 530-560 | authenticate, gate |
| R2 | GET /v1/projects | list_projects 563-641 (79) | authenticate, gate |
| R3 | GET /v1/projects/{projectName} | get_project_detail 644-693 | authenticate, gate |
| R4 | GET .../spends | get_project_spends 818-888 (71) | authenticate, gate, inline `can_access` |
| R5 | PATCH /v1/projects/{projectName} | update_project 891-927 | authenticate, gate |
| R6 | PATCH .../allowed-models | update_allowed_models 930-958 | authenticate, gate |
| R7 | DELETE /v1/projects/{projectName} | delete_project 988-1014 | `_authorize_project_access` |
| R8 | POST .../assignment | assign_user_to_project 1017-1039 | same |
| R9 | PUT .../assignment/{userId} | update_user_project_assignment 1042-1065 | same |
| R10 | DELETE .../assignment/{userId} | remove_user_from_project 1068-1089 | same |
| R11 | POST .../assignments | bulk_assign_users_to_project 1092-1122 | same |
| R12 | POST .../import-users/validate | validate_users_from_csv 1125-1152 | same, gate again |
| R13 | POST .../import-users | import_users_from_csv 1155-1199 | same, gate again |
| R14 | DELETE .../assignments | bulk_remove_users_from_project 1202-1237 | same |

Blocks are not grouped by concern: R4 (spends) sits between R3 and R5; R7 follows `_authorize_project_access` (961-985); R8-R14 are contiguous.

Helper readers (gate = `_ensure_user_management_enabled` L1251-1253, reads `config`):
- `_authorize_project_access` L961-985: dependency of R7-R14; calls the gate and `_raise_project_not_found` (L1240-1248, reads `logger`, `UTC`; also called by R4). Reads `get_session`, `application_repository`, `Ability`.
- Gate callers: R1-R6, R12, R13, `_authorize_project_access`.
- `_resolve_cost_center_name` L119-125 (reads `get_session`, `cost_center_repository`): R1, R5, R6. `_list_projects_sync` L128-154: R2. `_get_project_detail_sync` L163-177: R3. `_get_project_by_name_sync` L157-160 (reads `get_session`, `application_repository`): R4. `_build_project_detail_response` L696-714: R3 (tests import it).
- List-budget helpers, R2 only: `_is_budget_provider_active` L365-367 (reads `config`, `get_active_provider`), `_ensure_project_budget_list_supported` L370-376, `_budget_category_value` L379-380, `_attach_assigned_budget_summaries` L383-425 (reads `get_async_session`, `project_budget_assignment_repository`, `logger`).
- Spending cluster, 20 names, 379 code lines (about 417 with blanks): `_spend_repo`, `_WIDGET_COLUMNS` (L85-113), L180-362 (9 functions), L428-527 (5), `_can_see_project_spending` L717-720, `_attach_project_detail_spending` L723-771, `_populate_project_spending_summary` L774-800, `_populate_project_spending_widget` L803-815. R2 uses `_manageable_project_names`, `_attach_project_spending_summaries` and their callees; R3 uses `_can_see_project_spending`, `_attach_project_detail_spending` and callees; R4 also reads `_spend_repo` (L864).
- `get_session` has 13 readers in four groups (R1-R3 helpers, R4 helper, `_authorize_project_access`, R7-R14). `get_async_session` has 4 (L391, 493, 729, 863). `config` 2 readers (L367, L1252), `logger` 2 (L405, L1247). All imports are used; no dead code found.

### Architecture and Layers Affected

REST router layer only, plus 4 test files that reference the module and the guard baselines (expected unchanged). Services (`project_service`, `project_visibility_service`, `SettingsService`) stay put.

### Integration Points

- `main.py`: L81 `projects,` in the multi-line `from codemie.rest_api.routers import (...)`; L1050 `app.include_router(projects.router)`, unconditional, between `settings.router` L1049 and `cost_centers.router` L1051. Only src importer found: codegraph shows the handlers called only inside the file and tests; `codemie-enterprise` returned no match (name-based, unproven). `cost_centers.py` L46-48 has its own identical `_ensure_user_management_enabled`; it does not import from projects.
- Function-local imports stay local in the bodies: `project_assignment_service` (R8-R11, R14), `csv_import_service` + `MAX_CSV_BYTES` (R12, R13). Tests patch the defining service module, so the split does not affect them.
- OpenAPI baseline (`baselines/openapi.json`, 75,073 lines): `/v1/projects` L65886 (get 65887, post 66076); `/{projectName}` L66125 (delete 66126, get 66175, patch 66250); `/allowed-models` L66309; `/assignment` L66370; `/assignment/{userId}` L66431 (delete 66432, put 66490); `/assignments` L66559 (delete 66560, post 66625); `/import-users` L66685; `/import-users/validate` L66746; `/spends` L66807. Every operation has tag `Projects` and security `user-id` plus `X-Bind-Key`, R7-R14 included, so a dropped dependency changes the snapshot. Operation ids come from handler names and paths. Multipart bodies are components `Body_import_users_from_csv_v1_projects__projectName__import_users_post` (L66704) and `Body_validate_users_from_csv_..._import_users_validate_post` (L66765), named from route name and path.
- Component-name collisions: none. `projects.py` defines no class, so no key can gain a `codemie__rest_api__routers__projects__` prefix; `PaginationInfo` (four definitions) lives in `models/project.py` since step 1.
- Route precedence: `route_precedence.json` (45 pairs) has no `/v1/projects` entry. All 14 routes use single-segment `{projectName}`/`{userId}`; same-method pairs differ by literal or segment count (`assignment` vs `assignments`, `import-users` vs `.../validate`, 3 vs 4 vs 5 segments), so none overlaps. Relative order affects no match; it only changes the `app.routes` sequence.

### Patterns and Conventions

Inherited from steps 2-4 (not re-derived): bare `APIRouter()` per route module, combined router in `__init__` with `include_router` and `__all__ = ["router"]`; each module keeps `from __future__ import annotations`, so FastAPI needs every annotation name (`Request`, `Application`, `UploadFile`, `Optional`, `Literal`, `BudgetCategory`, `date`, `Path`, `Query`, `User`, each request model) at module level of the module holding the handler, `_authorize_project_access` included; per-module `logger = logging.getLogger(__name__)` where logger is read; private modules never import route modules; underscore names are imported in `src/` only from private modules; header copied verbatim (Apache, 13 lines). Dumps and checkers are in `local/router-split/step4/` (Glob confirmed; `routes_before.json` was deleted, so retake it on the pre-move tree). The dump predicate must match module `== pkg` or `startswith(pkg + ".")`, because `project_budget_router` and `project_settings` share the prefix letters.

Access-check sites that move: `_authorize_project_access` (404 instead of 403, deleted project treated as missing, `Ability(user).can(Action.WRITE, project)`); `_raise_project_not_found` (log line omits the project name); the gate (400); R4 inline `can_access = is_admin_or_maintainer or is_auditor or is_application_admin or (personal and has_access_to_application)` with 404 otherwise; `_can_see_project_spending` and `_manageable_project_names` (admin, auditor, project admin, personal); `is_admin or is_auditor` passed to the visibility service in R2, R3 (L619, L680). Authorization for R1, R5, R6 is in `project_service` (not moved): project limit on create; `_get_project_for_update` gives 403 to non-admins of an existing project.

Pre-existing, to preserve: PATCH returns 403 where DELETE and assignment routes return 404 (project existence disclosed); R12/R13 re-run the gate after the dependency; handlers use `request.state.user` as actor; R4 passes the literal `action="GET spends"`; R4 docstring omits auditors and personal owners; the create response omits `allowed_models`, which the update response returns.

---

## 3. Documentation Findings

### Guides and Architecture Docs

No guide cites `routers/projects.py`. Read: project-structure, endpoint-conventions, rest-api-patterns, layered-architecture, security-patterns, quality-gates; not opened: the other guides.

### Architectural Decisions

Ticket target split: `projects/`: crud, spending, membership; moving the spending helpers to `service/project/project_spending_service.py` is step 7 (out of scope). Step-0 analysis: a fourth module is needed for the gate, `_raise_project_not_found` plus `logger`, `_authorize_project_access`; crud needs the spending helpers; spending about 0.55k lines before step 7. Step-0 spec criteria 1-8 apply (500-line cap binds, OpenAPI identical, export only `router`, `/sanity` for step 5, move-only).

### Derived Conventions

Patch the module whose code resolves the name on the exercised path; whole-name patches bind per using module; `<Name>.<attr>` keeps its tail; imports take the defining submodule; tests may import underscore names; stale-patch detector, FLIP check and byte-identical AST move proof as in step 4.

---

## 4. Testing Landscape

### Existing Coverage

Five files reference `routers.projects` (found by name; others unproven):

| File | Lines | Patch strings | Import statements |
|---|---|---|---|
| tests/codemie/rest_api/routers/test_projects_router.py | 3,868 | 267 | 11 (24 names; L40-55 module level, 7 `validate_users_from_csv` from L1799, 3 `update_allowed_models`) |
| .../test_projects_router_auditor.py | 243 | 18 | 7 |
| .../test_project_model_availability.py | 972 | 15 | 6 |
| .../test_project_model_availability_integration.py | 483 | 13 | 3 |
| .../test_project_model_availability_execution.py | 623 | 0 | 0 |

Totals: 313 patch strings, 27 import statements (ticket: 265 in 2 files). Imports of `router` stay valid.

Strings by name (all files): `config` 83, `get_session` 57, `project_service` 50 (6 dotted), `project_visibility_service` 17, `_spend_repo` 20 (4 dotted), `application_repository` 20, `get_async_session` 18, `_resolve_cost_center_name` 11, `project_budget_assignment_repository` 9 (7 dotted `.get_assigned_budget_summaries_for_projects`), `budget_repository` 8, `_ensure_user_management_enabled` 8, `SettingsService` 4, `_get_project_detail_sync` 3, `logger` 3, `get_active_provider` 1, `Ability` 1. Imported names: `_authorize_project_access`, `_raise_project_not_found`, `_is_budget_period_open`, `_manageable_project_names`, `_can_see_project_spending` (x2), `_build_project_detail_response` (x3), and handlers `create_project`, `list_projects` (x2), `get_project_detail` (x3), `get_project_spends` (x2), `update_project`, `update_allowed_models` (x11), `delete_project`, `assign_user_to_project`, `update_user_project_assignment`, `remove_user_from_project`, `validate_users_from_csv` (x7).

### Testing Framework and Patterns

pytest with `anyio` (file-local `anyio_backend`), `@patch` decorators, handlers called as plain functions with `user=`/`authorized_project=` (dependencies bypassed), `FastAPI()` + `include_router(projects_router)` to read `route.dependant.dependencies[*].call.__name__` (test_projects_router.py L1006-1025, L1987-2008). `tests/.env.test` pins `ENABLE_USER_MANAGEMENT=false`: a `config` patch aimed at a module that does not run the gate gives 400 in gate-calling handlers. Guards: `poetry run pytest tests/codemie/rest_api/router_guards -n 0 --show-capture=no -q`.

### Coverage Gaps

`bulk_assign_users_to_project` (R11), `import_users_from_csv` (R13) and `bulk_remove_users_from_project` (R14) have no handler test; only the guards cover them. `_attach_assigned_budget_summaries` has one test. At least 24 patch strings are inert at BASE: 16 `config` (13 assignment tests, 3 delete tests: those handlers never run the gate), 5 `application_repository` (allowed-models and integration tests), 3 `get_session` (allowed-models).

---

## 5. Configuration and Environment

### Environment Variables

`ENABLE_USER_MANAGEMENT` (read per request through `config`), `LLM_PROXY_BUDGET_CHECK_ENABLED` (in `_is_budget_provider_active`), `LLM_PROXY_ENABLED=false` in `.env.test`. `MAX_CSV_BYTES` (5 MB) comes from `csv_import_service`.

### Configuration Files

`pyproject.toml`: ruff E,F,B,N,C4,G,T20,RSE,SIM,C,W,PERF,ISC, line length 120, mccabe 16, no per-file ignore for projects (the `*_tool*`, `*prompt*`, `*template*`, `*_vars*` E501 globs match no proposed name). `sonar-project.properties` (`sonar.sources=src/codemie`) and `.coveragerc` (`include src/codemie/*`) have no per-file entries. Baselines regenerate only with `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`, never by a failing run.

### Feature Flags and Deployment Concerns

The router is included unconditionally; the flag is a per-request 400. No pipeline config: the MR needs `/sanity` and a `## Test harness` block. Untracked `opencode.json.lock` and `skills-lock.json` are never staged.

---

## 6. Risk Indicators

- Speculative: grouping A (ticket's crud, spending, membership plus private `_common` about 80, `_spending_support` about 460): `crud` R1-R3, R5-R7 about 475, `spending` R4 about 115, `membership` about 270, `__init__` about 45 (plus or minus 40); `crud` and `_spending_support` sit near the 500 cap. Helpers plus R4 in one `spending` would be about 570.
- Speculative: R4 sits between R3 and R5, so grouping A moves it after R7 (no overlapping pair, guards unaffected, order check needs a restated criterion). Strict order needs a fourth route module (B: crud R1-R3, spending, management R5-R7, membership; `_resolve_cost_center_name` to `_common`).
- Speculative: under A the 313 strings split `_common` 96, `crud` 119, `spending` 44, `_spending_support` 35, `membership` 19. `config` has two readers: all 83 go to `_common`; test L635 also needs `crud.config`.
- Inert patches (24+) can pass silently when mis-aimed.
- `/sanity`: `_authorize_project_access` (8 routes), `_raise_project_not_found`, gate, R4 `can_access`, spending visibility helpers, auditor flags.
- `project_authorization_failed` logger renames to the new module.
- Pre-existing gaps (Section 2) preserved, not fixed. Three handlers untested.
- Unverified: other importers (no grep); ticket counts are stale.

---

## 7. Summary for Complexity Assessment

One 1,253-line router (14 routes, 30 helpers, no classes) becomes a package of about three route modules and two private modules. Router layer only; `main.py` is untouched. No class means no OpenAPI component rename, and no route pair overlaps, so both baselines should stay byte-identical.

Surface is five test files, 313 patch strings and 27 imports (hand-counted), retargeted across five modules, with `config`, `get_session`, `get_async_session` and `_spend_repo` splitting by caller. Access-check code moves, so `/sanity` applies. Fourth use of the step 2-4 method with tooling on disk; three handlers are untested. Step-0 estimate: M, 1.5 days.

---

## 8. External References

All resolved and read: `ticket.md` (item 5: `projects/` crud, spending, membership; spending helpers to services is step 7), step-0 `spec.md` (criteria 1-8; `/sanity` for step 5), step-0 and step-4 `technical-analysis.md` (fourth module; shape), step-4 `spec.md`, `local/router-split/HANDOFF.md` steps 2-4 (patch rules; scripts in `local/router-split/step4/`).
