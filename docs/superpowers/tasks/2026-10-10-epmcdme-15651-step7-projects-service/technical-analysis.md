# Technical Research

**Task**: projects spending summary widget service
**Generated**: 2026-10-10
**Research path**: codegraph

---

## 1. Original Context

Implement delivery step 7, package `projects` (run 7c, the last package of ticket step 7), of Jira story EPMCDME-15651 ("Split oversized REST API router modules into smaller per-concern modules"). Steps 0-6 and 7a (assistant -> service/assistant/) and 7b (analytics -> service/analytics/spending_formatter.py) are done on branch EPMCDME-15651_split-router-modules. Ticket step 7: "Move domain helpers into services, one merge request per package." For `projects` the ticket says: "Move the classes to rest_api/models/project.py and the spending helpers to service/project/project_spending_service.py" (the classes moved in step 1; this run is the spending helpers). They live in src/codemie/rest_api/routers/projects/_spending_support.py (458 lines, about 20 symbols: _WIDGET_COLUMNS, _format_time_until_reset, _parse_budget_reset_at, _is_budget_period_open, _budget_info, _select_category_rows, _aggregate_budget_window, _build_widget_rows, _build_category_widget_rows, _key_row_to_widget, _manageable_project_names, _latest_key_rows_by_project, _budget_rows_grouped_by_project, _project_spending_summary, _attach_project_spending_summaries (async), _can_see_project_spending, _attach_project_detail_spending (async), _populate_project_spending_summary, _populate_project_spending_widget) and are used by routers/projects/{crud,spending,...}.py; find the exact helper set, every importer and the access-control/visibility logic among them (`_can_see_project_spending`, `_manageable_project_names`, auditor/maintainer/admin checks are security-relevant: if such logic moves the eventual MR needs /sanity; say clearly which helpers are access control, which are formatting/aggregation, which do DB/async IO).
Not a pure file move: handlers call the new service and test patch targets move (step 5 retargeted 314 patch literals in 4 test files, so expect many patch literals naming this module; FLIP showed 144 of 314 literals pass when mis-aimed: report which of them target `_spending_support` names and which would go dark if mis-aimed). The precedent is run 7a and 7b: read docs/superpowers/tasks/2026-10-10-epmcdme-15651-step7-assistant-service/{spec.md,plan.md,technical-analysis.md}, docs/superpowers/tasks/2026-10-10-epmcdme-15651-step7-analytics-service/{spec.md,plan.md} and the "Step 7: assistant helpers to service/assistant" and "Step 7b" sections at the end of local/router-split/HANDOFF.md: module functions (not classes), bodies unchanged, function-local imports kept, characterization tests first (green at BASE), body-AST check under a rename map, retargeted patch literals plus flip check, guards byte-identical, parameterized generics on public signatures from the start (the review flagged bare list/dict annotations twice), word-boundary greps, scoped underscore-import rule. Constraints: no API behaviour change (OpenAPI snapshot and route-precedence baselines in tests/codemie/rest_api/router_guards/ stay byte-identical, nothing regenerated); modules under service/ and rest_api/handlers/ must NOT import from rest_api/routers/ (except routers/utils.py); no src module imports an underscore name from a module that defines a `router`; ruff max-complexity 16; pure relocation, no redesign, no fixing of pre-existing quirks (step 5 recorded: PATCH 403 vs DELETE/assignment 404, R12/R13 re-run the gate, request.state.user as actor, literal action="GET spends", create response omits allowed_models, maintainer-vs-admin spend visibility, sync SettingsService call in the async detail handler, OverflowError at period_to=9999-12-31, no wiring tests for 4 write routes). Note the three divergent time-until-reset functions (this module's _format_time_until_reset at line 73, service/analytics/spending_formatter.calculate_time_until_reset, service/analytics/handlers/budget_usage_service.py:125): do not merge. Follow .ai-run/guides (architecture/layered-architecture.md, service-layer-patterns.md, project-structure.md, security-patterns.md, data/repository-patterns.md for async/DB). Ticket body and criteria: docs/superpowers/tasks/2026-10-08-epmcdme-15651-split-router-modules/{ticket,spec}.md; step 5 (projects package) records: docs/superpowers/tasks/2026-10-09-epmcdme-15651-step5-projects-package/.
Research must report, with path:line evidence: (1) the exact helper set, its classification (access control / aggregation / formatting / DB-IO) and every caller (routers, services, handlers, tests, sibling repos /home/taras_spashchenko/EPAM/cm/codemie-enterprise etc.); (2) existing service/project/ contents and naming conventions (is there an existing project spending service? how do services expose functions vs classes?) and whether the ticket's target file service/project/project_spending_service.py exists; (3) every test file and mock.patch literal that targets `routers.projects._spending_support` names (and `routers.projects.<module>.<name>` names for helpers used from other route modules), with which would go dark if mis-aimed; (4) router-only dependencies among the helpers (HTTPException, Request, Depends, router constants, ERROR_MSG constants in _common) that would drag HTTP concerns into service/ or create import cycles, and what the helpers import from routers/projects/_common; (5) circular-import risks, including service/project/__init__.py contents; (6) behaviour to preserve byte-for-byte and its current test coverage; (7) whether acceptance criterion 5 (no service/ or handlers/ import of routers/ except routers/utils.py) holds today; (8) a recommendation on the minimal coherent set to move (pure/aggregation/formatting vs the access-control and IO ones) with the reason.

---

## 2. Codebase Findings

Tools: Read, Glob, codegraph; no grep or shell, nothing run. Absence claims are unproven unless a source is named. Paths are under `src/codemie/` unless prefixed `tests/`, `docs/`, `local/`. `R` = `rest_api/routers/projects`. HEAD `8a209018b9`, tree clean.

### Existing Implementations

`R/_spending_support.py`: 458 lines, 20 module-level names (18 functions, 2 assignments; the brief's 19 omit `_spend_repo`), `from __future__ import annotations` :15. It imports no `logging`, `config`, `fastapi` or `_common` and has no function-local import.

| Name | Lines | Class | Used by (same file unless noted) |
|---|---|---|---|
| `_spend_repo = ProjectSpendTrackingRepository()` | 42 | DB-IO handle | :324-383; `spending.py:36` import, `:97` use |
| `_WIDGET_COLUMNS` | 45-70 | formatting const | :457 |
| `_format_time_until_reset` | 73-87 | formatting, clock :77 | :200, 233, 252, 440 |
| `_parse_budget_reset_at` | 90-94 | parsing | :105, 120, 166, 175, 219 |
| `_is_budget_period_open` | 97-110 | aggregation | :142; test `test_projects_router.py:42` |
| `_budget_info` | 113-120 | aggregation | :192, 224, 245 |
| `_select_category_rows` | 123-148 | aggregation | :348, 393 |
| `_aggregate_budget_window` | 151-179 | aggregation | :304, 432 |
| `_build_widget_rows` | 182-205 | formatting; `project_name` never read | :397 |
| `_build_category_widget_rows` | 208-238 | formatting | :395 |
| `_key_row_to_widget` | 241-255 | formatting | :454 |
| `_manageable_project_names` | 258-266 | ACCESS CONTROL | :319; test `test_projects_router_auditor.py:49` |
| `_latest_key_rows_by_project` | 269-275 | aggregation | :339 |
| `_budget_rows_grouped_by_project` | 278-289 | aggregation | :340 |
| `_project_spending_summary` | 292-311 | aggregation | :352 |
| `_attach_project_spending_summaries` (async) | 314-357 | DB-IO, applies access filter :319-321, clock :338 | `crud.py:276` |
| `_can_see_project_spending` | 360-363 | ACCESS CONTROL | `crud.py:326`; auditor tests `:62`, `:70` |
| `_attach_project_detail_spending` (async) | 366-414 | DB-IO, clock :393 | `crud.py:327-331` |
| `_populate_project_spending_summary` | 417-443 | formatting, mutates `response.spending` | :401 |
| `_populate_project_spending_widget` | 446-458 | formatting, mutates `response.spending_widget` | :409 |

Totals: access control 2; DB-IO 2 functions plus `_spend_repo`; aggregation 7; formatting 8. Both predicates are `user.is_admin` or `is_auditor` or project admin or personal project (:262-265, :363); `rest_api/security/user.py:74-75` makes maintainers `is_admin`. `_attach_project_spending_summaries` returns early when no project is manageable, leaving `item.spending` unset.

Importers (all five route modules and `__init__` read): `crud.py:41-45` (3 functions) and `spending.py:36` (`_spend_repo`). `management.py`, `membership.py`, `_common.py` import none. No `service/` or `rest_api/handlers/` module imports `rest_api.models.project` (codegraph callers of `SpendingWidgetRow`, `ProjectDetailResponse`). codemie-enterprise: name query found no consumer; other siblings not queried.

Imports: runtime `datetime`, `get_async_session`, `Application` (:265, 361), `budget_repository`, `project_budget_assignment_repository`, `ProjectSpendTrackingRepository`, 7 response models; annotation-only: `ProjectAssignedBudgetSummaryRow`, `ProjectListItem`, `ProjectDetailResponse`, `User`, `Budget`, `ProjectSpendTracking`. Bare `list`/`dict` or untyped parameters on 9 signatures: :152-153, 183, 241, 258, 296, 316, 360, 420-424, 450-451.

### Architecture and Layers Affected

Router (`R/crud.py`, `R/spending.py`, `R/_spending_support.py`), service (`service/project/`), tests. `rest_api/models/project.py`, `R/_common.py`, `main.py` and the guards are not involved.

### Integration Points

- `crud.py` keeps its own `get_async_session` :23 and `project_budget_assignment_repository` :26 (used by `_attach_assigned_budget_summaries` :125-167).
- `ProjectSpendTrackingRepository.__init__` reads `config.DB_INSERT_BATCH_SIZE`/`DB_IN_CLAUSE_BATCH_SIZE` (`repository/project_spend_tracking_repository.py:36-39`). Singleton `project_spend_tracking_repository` exists (:1098; used at `service/project/project_assignment_service.py:36`); `_spend_repo` is a second instance.
- Three time-until-reset functions differ: `_spending_support.py:73-87` (datetime in; "N day(s)"/"N hour(s)"; past gives "0 days"; naive = UTC), `service/analytics/spending_formatter.py:122-155` (str; "X days Y hours Z mins"; past "0 days 0 hours 0 mins"; naive raises TypeError), `service/analytics/handlers/budget_usage_service.py:125-145` (str; "Expired"; naive = UTC).
- Other access checks stay in handlers: `crud.py:259,320` (`is_admin or is_auditor`), `spending.py:84-89` (inline `can_access`, `is_admin_or_maintainer`).

### Patterns and Conventions

- `service/project/`: `__init__.py`, `csv_import_service.py`, `personal_project_service.py`, `project_assignment_service.py`, `project_service.py`, `project_visibility_service.py`. `project_spending_service.py` does not exist. Each service is a class (static/class methods) plus a module singleton; `__init__.py` eagerly imports and exports four of them (not `csv_import_service`). `get_async_session` is module-level at `project_assignment_service.py:30`, function-local at `personal_project_service.py:70`.
- Precedent 7a/7b (`HANDOFF.md:414-461`): module functions, underscore dropped on cross-module names, 13-line header, `from __future__ import annotations`, annotation-only imports under `TYPE_CHECKING`, no `__init__` re-export, bodies copied by script, `bodycheck.py` under a rename map, flip check.

---

## 3. Documentation Findings

### Guides and Architecture Docs

Read: `layered-architecture.md:5-9` (no database code in routers; `_spending_support.py:323,372` open `get_async_session()`), `service-layer-patterns.md:5` (response formatting outside services; the helpers fill `ProjectDetailResponse`), `project-structure.md`, `security-patterns.md:5-12` (central role helpers over inline checks; both predicates are inline), `repository-patterns.md`.

### Architectural Decisions

`ticket.md:41` target file; `:67` AC 5; `:70` AC 8 (`/sanity`). 7b kept its gate in the router and moved no security code (`HANDOFF.md:436,454`); 7a moved security code, so the single MR already needs `/sanity` and `## Test harness` (`:403,454`). AC 5 today: `HANDOFF.md:409,458` record `routers` imports in `service/` = 3 and `handlers/` = 1, all `routers.utils`; not rescanned (no grep). Import blocks of the four `service/project/*_service.py` modules hold no `routers` import. The helpers import nothing from `R/_common.py` (its names: `_resolve_cost_center_name`, `_authorize_project_access`, `_raise_project_not_found`, `_ensure_user_management_enabled`, `logger`; no `ERROR_MSG_*`).

### Derived Conventions

Patch the module that reads the name; dotted `<singleton>.<attr>` patches reach the shared object whatever the module path; whole-name patches bind per module (7a `spec.md` AC 5).

---

## 4. Testing Landscape

### Existing Coverage

Only `tests/codemie/rest_api/routers/test_projects_router.py` and `test_projects_router_auditor.py` name `_spending_support`. Source `local/router-split/step5/flip-report.txt` (HEAD `a78e8d1051`), spot-checked against the files. 35 literals and 4 imports:

| Literal target | N | Lines (`aud` = auditor file) | Step-5 FLIP at nearest sibling |
|---|---|---|---|
| `_spending_support.get_async_session` | 8 | 395, 741, 2112, 2195, 2265, 3658, 3820, aud:192 | PASS, inert (all repositories mocked) |
| `...project_budget_assignment_repository.get_assigned_budget_summaries_for_projects` (dotted) | 7 | 373, 735, 2106, 2189, 2259, 3662, 3824 | PASS (shared singleton) |
| `...project_budget_assignment_repository` (whole) | 1 | aud:188 | FAIL |
| `..._spend_repo.<method>` (dotted) | 4 | 382, 384, 388, 392 | PASS (shared instance) |
| `..._spend_repo` (whole) | 7 | 744, 2115, 2198, 2268, 3659, 3821, aud:191 | FAIL |
| `...budget_repository` (7 whole; :379 dotted `.get_all_keyed_by_id`) | 8 | 379, 740, 2111, 2194, 2264, 3660, 3822, aud:190 | NO-SIBLING (name exists nowhere else in the package) |

PASS 19 (go dark if mis-aimed), FAIL 8, NO-SIBLING 8. Imports (ImportError if stale): `test_projects_router.py:42` `_is_budget_period_open`; auditor `:49` `_manageable_project_names`, `:62`, `:70` `_can_see_project_spending`. Related: 9 whole-name `spending._spend_repo` literals (`test_projects_router.py` 2371, 2426, 2467, 2615, 2699, 2740, 2781, 2822; aud:126) FAIL when aimed at `_spending_support._spend_repo`; `crud.get_async_session`/`crud.project_budget_assignment_repository` literals (e.g. :647-648) serve `crud`'s own budget attach.

Direct tests: `_is_budget_period_open` 3 (`test_projects_router.py:3605-3616`); `_manageable_project_names` 1 (auditor `:47-58`); `_can_see_project_spending` 2 (auditor `:60-82`: auditor true, regular false). Through handlers: detail for project admin (`:372-449`), personal owner (`:2188-2331`), auditor (aud `:188-242`); list for admin (`:734-827`), personal owner (`:2105-2186`); `TestUnassignedCategorySpend` (`:3648-3868`) pins category selection, `is_assigned`, null limit, dropped and re-added budgets.

### Testing Framework and Patterns

pytest with `anyio`, `unittest.mock.patch` decorators, handlers awaited directly with `user=`, local `_mock_session_ctx`. `tests/.env.test` pins `ENABLE_USER_MANAGEMENT=false`, so tests patch `routers.projects._common.config`. Run: `poetry run pytest <path> -n 0 --show-capture=no -q`; guards in `tests/codemie/rest_api/router_guards/`. `tests/codemie/service/project/` exists (flat `test_<module>.py`), no spending test.

### Coverage Gaps

No assertion on `time_until_reset`, `budget_reset_at`, `_WIDGET_COLUMNS`, widget `total`, `_key_row_to_widget`, hour/plural branches, `_parse_budget_reset_at` failure; none (in the regions read) where a non-privileged user sends `include_spending=true`.

---

## 5. Configuration and Environment

### Environment Variables

The helpers read none. Adjacent: `ENABLE_USER_MANAGEMENT` (`R/_common.py:85-87`, checked by handlers first), `LLM_PROXY_BUDGET_CHECK_ENABLED` (`crud.py:109`), `DB_INSERT_BATCH_SIZE`/`DB_IN_CLAUSE_BATCH_SIZE` (read when `_spend_repo` is built at import).

### Configuration Files

`pyproject.toml` ruff (E,F,B,N,C4,G,T20,RSE,SIM,C,W,PERF,ISC; line 120; mccabe 16; per step-5 TA, not re-read). Baselines regenerate only via `regenerate_baselines`.

### Feature Flags and Deployment Concerns

Query params `include_spending`, `spending_rows_limit` (`crud.py:218-221`, `288-296`) gate the helpers; no flag, manifest or secret. No pipeline config: the MR needs `/sanity` and `## Test harness`.

---

## 6. Risk Indicators

- Access control (fact): `_manageable_project_names` :258 and `_can_see_project_spending` :360 are one admin/auditor/project-admin/personal disjunction, no HTTP import; the list filter also sits inside IO function `_attach_project_spending_summaries` :319-321. Moving them is security-relevant (AC 8: `/sanity`, `## Test harness`).
- Speculative: move all 20 names, delete the file. `_manageable_project_names` cannot stay in the router without a signature change; 7b's reason for keeping its gate (HTTP exception, router constants) does not apply.
- Speculative: `_spend_repo` is shared with `spending.py:36,97` and 9 `spending._spend_repo` literals; a rename or second instance changes what dotted patches reach.
- Patch targets (fact): 19 of 35 `_spending_support` literals are inert if mis-aimed (8 `get_async_session`, 7 dotted `project_budget_assignment_repository`, 4 dotted `_spend_repo.*`); 8 FAIL, 8 NO-SIBLING.
- Three divergent time-until-reset functions: merging changes output. No test asserts `time_until_reset`, `_WIDGET_COLUMNS`, widget `total`.
- Nine signatures use bare `list`/`dict`; Speculative: annotation-only edits need a bodycheck relaxation (7b CR-001).
- Speculative: first `service/` module importing `rest_api.models.project`; `service/project/__init__.py` imports four services eagerly, so check a cold `python -I` import. AC 5 not rescanned.
- Non-goal quirks (by inspection): malformed `budget_reset_at` raises ValueError (:94); key row dropped when budget rows exist (:398); `project_name` unused (:184).

---

## 7. Summary for Complexity Assessment

`routers/projects/_spending_support.py` (458 lines, 20 names: 18 functions, 2 assignments) has two importers, `crud.py:41-45` and `spending.py:36`, and no HTTP, `_common` or logger dependency. The target `service/project/project_spending_service.py` does not exist. Layers: router, service, tests; models, guards and `main.py` are untouched. Classes: 2 access control, 2 async DB functions plus `_spend_repo`, 7 aggregation, 8 formatting.

Tests: 35 patch literals and 4 imports in two files name the module, 19 of them inert if mis-aimed. Most helper behavior is covered only through handlers. The method is the 7a/7b template.

---

## 8. External References

All resolved and read: `ticket.md` (`:41` target file, `:67` AC 5, `:70` AC 8); 7a `spec.md`; 7b `spec.md`, `technical-analysis.md`, `plan.md` (1-70); step-5 `technical-analysis.md` and `flip-report.txt` (314 literals: PASS 144, FAIL 140, NO-SIBLING 30); `HANDOFF.md` 395-461. Not opened: 7a `plan.md`, `technical-analysis.md`, umbrella `spec.md`.
