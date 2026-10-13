# EPMCDME-15651, step 4: analytics router package

## Purpose

Replace `routers/analytics.py` (3,113 lines, 85 routes, prefix `/v1/analytics`) with a package, no API change. Move-only, reviewable with `git diff --color-moved`. Same branch; no MR until the ticket is done. Rules follow the step-2 spec as amended and the step-3 spec. `routers/cli_analytics.py` is a separate router and stays out of scope.

## Scope

`routers/analytics/` replaces the file (delete it). Bodies, decorators, docstrings, function-local imports, constants and the four router-local classes move byte-identical, in source order within each module; each module keeps the source's `from __future__ import annotations`. Cap: 500 lines per module.

Route modules in include order (85 routes; every block is contiguous in the source, so include order is source order). Old lines in parentheses, size estimates in brackets:

- `usage` 13 routes (470-866) [440]; `activity` 11 (869-1270) [445], cut at the existing gap. Its four spending/limit routes stay: moving them to `spending` would reorder routes.
- `cli` 10 (1273-1524) [290]; `cli_insights` 15 (1527-1955) [465]; `cli_insights_projects` 2 (1958-2005) [85]. The 479-line block has no helpers, so a private module cannot shrink it; it is cut at a block boundary.
- `cli_enriched_users` 5 (2008-2115, with its constants, two classes and `_run_enriched_user_insight`) [150]; `spending` 7 (2118-2507, with `_resolve_admin_budget_subject`) [455]; `leaderboard` 11 (2510-2815, with `_require_leaderboard_enabled` and the `LEADERBOARD_*` constants) [355]; `routing` 11 (2818-3113, with `_ensure_routing_analytics_enabled` and `ROUTING_ANALYTICS_FEATURE`) [345].

Private modules:

- `_common` [150]: `handle_analytics_errors` with its `logger`, `_create_response`, `AnalyticsFilterParams`, `AnalyticsQueryParams`, the constants read by several modules (`USERS_FILTER_DESC`, `PROJECTS_FILTER_DESC`, `CLIENT_SOURCE_FILTER_DESC`, `ERROR_MSG_ACCESS_DENIED`, `ERROR_MSG_ADMIN_HELP`) and the two unreferenced ones (`PROJECTS_FILTER_ADMIN_DESC`, `ERROR_MSG_PROJECT_EMPTY`), verbatim.
- `_spending_support` [335]: lines 139-429 verbatim (`_authorize_admin_budget_view` included); imports `_common` only.

A module over 500 lines is cut at a route boundary into one more route module, included at the same position and recorded. A cut that would reorder a route is not allowed: stop and ask.

## Constraints

- `__init__.py` holds `router = APIRouter(tags=["Dashboard Analytics"], prefix="/v1/analytics", dependencies=[Depends(authenticate)])`, one `include_router` per route module in the order above, `__all__ = ["router"]`, nothing else. Route modules use a bare `APIRouter()`; decorators stay unchanged.
- Auth stays byte-identical. The 10 leaderboard GETs keep `dependencies=[Depends(admin_or_maintainer_or_auditor_access), Depends(_require_leaderboard_enabled)]` and `POST /leaderboard/compute` keeps `[Depends(admin_access_only), Depends(_require_leaderboard_enabled)]` in their decorators (effective order: authenticate, access check, flag; CR-004 403 before 503). The authorization helpers, inline checks, feature gates, `handle_analytics_errors` and `_create_response` move verbatim.
- Helpers keep their names. In `src/`, underscore names are imported only from private modules, never from a module that defines a `router`; private modules never import route modules.
- Each module whose moved code reads `logger` defines `logger = logging.getLogger(__name__)`. This renames the logger to `...routers.analytics.<module>`, accepted as in steps 2 and 3: the only configured logger is `codemie` (`configs/logger.py`), children propagate, no format uses `%(name)s`.
- `main.py` is unchanged: its multi-line `analytics` import resolves to the package. No other importer exists in `src/`, `tests/`, `.ai-run/` or the sibling repos (historical task docs only), and no guide row cites the file.
- Tests stay in their files. Measured: **55 patch strings, 79 import statements, 8 files**, matching the ticket. The analysis' 51 and 7 missed `tests/enterprise/litellm/test_premium_models_budget.py` (4 and 4). Patch the module whose code resolves the name on the exercised path:
  - `AnalyticsService` (31): the module of the handler under test. `logger` (3): two decorator tests to `_common`, `get_summaries` to `usage`. `_create_response` (1) to `leaderboard`; `customer_config` (1) to `cli_enriched_users`.
  - `asyncio.to_thread` (9, tail kept), `get_async_session` (3), `_authorize_admin_budget_view` (1): `spending`, the using module, not `_spending_support`.
  - `config` (6): `leaderboard`, one patch per test. `_require_leaderboard_enabled` reads the flag there; `_create_response` in `_common` reads `config.is_local` only to pick cache headers, and the one 200 test asserts status and the service call, which both branches meet. Two patches would change five test signatures; moving the gate into `_common` would misplace a one-user helper.
  - Imports take the defining submodule (tests may import underscore names): `_create_response`, `handle_analytics_errors`, `AnalyticsQueryParams` from `_common`; `_authorize_admin_budget_view` from `_spending_support`. `router` imports stay.

## Route order (restates step-0 criterion 4)

Order is preservable here, so the sequence itself is checked. Check: a one-off dump script in the plan, not committed (gitignored `local/router-split/step4/`; the guards' dump holds only path and methods). It copies `router_guards/app_probe.py` and, for every route whose endpoint module is `codemie.rest_api.routers.analytics` or starts with it plus a dot (this excludes `cli_analytics`), prints (methods, path, name, status code, tags, dependency call names from `route.dependant.dependencies` in order) in `app.routes` order, plus `analytics.router.prefix`, `.tags` and `.dependencies`. Run at the pre-move HEAD and after the last move.

## OpenAPI

Hard criterion: `openapi.json` and `route_precedence.json` byte-identical (`git diff` empty), nothing regenerated. The four classes are only `Depends()` class dependencies, which FastAPI expands to query parameters: the baseline has no key for them (zero hits for the four names or `routers__analytics`), so a changed `__module__` moves nothing. `AnalyticsDetailResponse` lives in `rest_api/models/analytics.py`, unmoved. `route_precedence.json` has no pair under `/v1/analytics`. If the snapshot fails: stop, report the diff, ask; never run `regenerate_baselines`.

## Acceptance criteria

1. `analytics.py` is gone; the package holds `__init__.py`, the 9 route modules, `_common` and `_spending_support`, each at most 500 lines (`wc -l`) with the Apache header.
2. Dump before and after: (a) the 85 rows are equal element-wise, in order, ignoring only the module field; (b) one contiguous block at the same `app.routes` offsets with the same neighbors, rest of the app unchanged; (c) `analytics.router.prefix`, `.tags` and `.dependencies == [authenticate]` unchanged, every route starts with `authenticate`, and the 11 leaderboard rows read authenticate, access check (`admin_or_maintainer_or_auditor_access` x10, `admin_access_only` x1), `_require_leaderboard_enabled`, in that order; (d) no endpoint belongs to the package `__init__`.
3. `git diff --color-moved` shows only moved blocks, imports, headers, `APIRouter()`, `include_router`, `__all__` and `logger` lines. An AST check finds every top-level def, async def, class and assignment except `router` and `logger` byte-identical (decorators included) in exactly one new module.
4. No `routers.analytics.<name>` in `src/` or `tests/` unless `<name>` is `router` or a real submodule; each `routers.analytics.<module>.<name>` target imports and its module reads `<name>` in a function body. Flip check: each whole-name string is pointed once at a sibling module holding the name; strings whose test still passes (expected: the `_authorize_admin_budget_view` and `get_async_session` sites in `capability_guards`) are listed in the handoff with the old-to-new map and scripts.
5. No `src/` module imports an underscore name from a module that defines a `router`; nothing under `service/` or `rest_api/handlers/` imports `routers/analytics`; `main.py` unchanged.
6. Each of the 13 modules imports alone in a fresh interpreter (a missing annotation name fails there: FastAPI resolves string annotations in the handler module's globals); the guarded app builds hermetically; guards pass with both baselines unchanged.
7. `make ruff` passes (F401 in `__init__.py`, complexity 16, license check). Tests pass in the eight files, `tests/codemie/rest_api/routers`, `tests/unit/routers`, `tests/enterprise/litellm` and `router_guards`.
8. The handoff says the eventual MR must ask a reviewer for `/sanity` and carry a `## Test harness` block (the compliance bot checks text only). Auth code that moves: router-level `authenticate`; the 11 leaderboard access dependencies and their order; `_authorize_admin_budget_view`, `_resolve_admin_budget_subject`; the inline role check in `get_leaderboard_user_detail` and the 403/404 branch in `get_user_project_spending`; the three feature gates (`_require_leaderboard_enabled`, `_ensure_routing_analytics_enabled`, `userEnrichmentEnabled`); `handle_analytics_errors`; `_create_response`.

## Non-goals

- Moving the four classes to `rest_api/models/`; renaming anything; changing paths, tags, operationIds or response models; the route factory the ticket defers.
- Moving the spending helpers into `service/analytics/` (step 7); deleting dead code (`_get_user_all_projects`, `_build_key_spending_tabular_data` and its two callees, the two unreferenced constants); shortening handlers.
- Fixing pre-existing behavior, carried as MR notes: an unknown `user_id` gets 404 before authorization in `_resolve_admin_budget_subject`; per-user `/spending` is `Cache-Control: public`; `/projects-unique-daily` 400 becomes 500; user and project scoping is delegated to `AnalyticsService` and not audited here; the user-detail role check repeats its route dependency.
- New tests, splitting test files, `main.py`, steps 5 to 7, guard code, baselines; commits or MR beyond the flow's.

## Open risks

- The 11 routing routes have no test and the 59 data routes are tested for 200 only: AC 2, 3 and 6 guard them.
- AC 4 is necessary, not sufficient: a whole-name patch aimed at a module that holds the name but is off the exercised path stays silent if its mock is never asserted; the flip check lists those sites.
- Line counts are estimates; `cli_insights` (465), `spending` (455) and `activity` (445) are closest to the cap. Fallback in Scope.
- Counts come from reading plus grep that already found an eighth test file: AC 4 and 5 are the net.
