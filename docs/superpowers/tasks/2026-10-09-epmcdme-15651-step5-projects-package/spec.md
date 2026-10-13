# EPMCDME-15651, step 5: projects router package

## Purpose

Replace `routers/projects.py` (1,253 lines, 14 routes R1-R14, prefix `/v1`, 30 helpers, no classes) with a package, no API change. Move-only, reviewable with `git diff --color-moved`. Same branch; no MR until the ticket is done. Rules follow the step-2 spec as amended and the step-3 and step-4 specs.

## Scope

`routers/projects/` replaces the file (delete it). Bodies, decorators, docstrings, function-local imports and constants move byte-identical, in source order within each module; each module keeps `from __future__ import annotations`. Cap: 500 lines. Old lines in parentheses, estimates in brackets.

Route modules in include order (each block is contiguous in the source, so include order is source order and R4 is not reordered):

- `crud` R1-R3 (530-693) with `_list_projects_sync`, `_get_project_detail_sync`, the four list-budget helpers (365-425), `_build_project_detail_response` and its own `logger` [365].
- `spending` R4 (818-888) with `_get_project_by_name_sync`; imports `_spend_repo` from `_spending_support` [125].
- `management` R5-R7: update, allowed-models, delete (891-958, 988-1014) [150].
- `membership` R8-R14 (1017-1237) [280].

Private modules:

- `_common` [95]: `logger`, `_resolve_cost_center_name` (R1, R5, R6), `_authorize_project_access`, `_raise_project_not_found`, `_ensure_user_management_enabled`.
- `_spending_support` [460]: the 20 spending names verbatim (`_spend_repo`, `_WIDGET_COLUMNS`, 180-362, 428-527, 717-815); imports nothing from the package.

A module over 500 lines is cut at a function boundary (private) or route boundary (route module) and recorded. A cut that would reorder a route: stop and ask.

## Constraints

- `__init__.py` holds `router = APIRouter(tags=["Projects"], prefix="/v1", dependencies=[])` as today (no router-level `authenticate`), one `include_router` per route module in the order above, `__all__ = ["router"]`, nothing else. Route modules use a bare `APIRouter()`; decorators stay unchanged.
- Helpers keep their names; route modules import `_common` and `_spending_support` names by name, so bodies stay verbatim. In `src/`, underscore names are imported only from private modules; private modules never import route modules.
- `crud` and `_common` define their own `logger = logging.getLogger(__name__)`; `project_authorization_failed` is now logged under `...projects._common` (accepted as in steps 2-4).
- Tests stay in their files. Measured: **313 patch strings, 27 import lines, 4 files** (`test_projects_router.py` 267, `_auditor` 18, `test_project_model_availability.py` 15, `_integration` 13); `..._execution.py` has none. `main.py` is the only `src` importer and is unchanged.

## Test retarget rules

Patch the module whose code reads the name on the path the test exercises. Whole-name patches bind to the using module (`_ensure_user_management_enabled` (8), `_resolve_cost_center_name` (11): the handler's module); `<Name>.<attr>` keeps its tail; imports take the defining submodule (tests may import underscore names); `router` imports stay.

- `config` (83): `_common`, where the gate and `_authorize_project_access` read it; the other reader is `crud._is_budget_provider_active`. `test_list_projects_attaches_assigned_budgets_and_skips_invalid_categories` needs both, so it gets two patches (`crud.config`, `_common.config`), the only signature change. `.env.test` pins `ENABLE_USER_MANAGEMENT=false`: a gate-path patch aimed elsewhere gives 400.
- `get_session` (57): `_common` for `_authorize_project_access` tests; `crud` list/detail; `spending` `get_project_spends`; `management` delete; `membership` assignment, CSV, bulk.
- `get_async_session` (18): `spending` for `get_project_spends`; `_spending_support` for `include_spending` on list and detail; `crud` for `include_budgets` (one test).
- `_spend_repo` (20, 4 dotted): `spending` for `get_project_spends`; `_spending_support` for list and detail spending.
- Same path rule: `project_budget_assignment_repository` (`crud` or `_spending_support`), `budget_repository` (`_spending_support`), `application_repository` (`spending` or `_common`), `logger`, `Ability` (`_common`).
- Inert at BASE (24+: 16 `config`, 5 `application_repository`, 3 `get_session`): to the handler's module if it binds the name, else `_common`; they stay inert and go in the handoff.

## Route order and OpenAPI

Order is preserved, so the sequence itself is checked: a one-off dump script in the plan, not committed (gitignored `local/router-split/step5/`, adapted from step 4; BASE dump retaken on the pre-move HEAD). It lists, for every route whose endpoint module is `codemie.rest_api.routers.projects` or starts with it plus a dot, (methods, path, name, status code, tags, dependency call names) in `app.routes` order, plus the router's prefix, tags and dependencies.

`openapi.json` and `route_precedence.json` stay byte-identical (`git diff` empty), nothing regenerated. No class is defined here, so no component key changes; a dropped `authenticate` or `_authorize_project_access` fails the snapshot. If a guard fails: stop, report the diff, ask; never run `regenerate_baselines`.

## Acceptance criteria

1. `projects.py` is gone; the package holds `__init__.py`, the 4 route modules, `_common` and `_spending_support`, each at most 500 lines (`wc -l`). Every new file has the full 13-line Apache header; check its last line, `# limitations under the License.` (the license check matches markers only).
2. Dump before and after: (a) the 14 rows equal element-wise, in order, ignoring only the module field; (b) one contiguous block at the same `app.routes` offsets, same neighbors, rest of the app unchanged; (c) `router.prefix == "/v1"`, `.tags == ["Projects"]`, `.dependencies == []`, R1-R6 read `authenticate`, R7-R14 read `_authorize_project_access`; (d) no endpoint belongs to `__init__`.
3. `git diff --color-moved` shows only moved blocks, imports, headers, `APIRouter()`, `include_router`, `__all__` and `logger` lines. An AST check finds every top-level def, async def and assignment except `router` and `logger` (46 at BASE) byte-identical, decorators included, in exactly one new module.
4. No `routers.projects.<name>` in `src/` or `tests/` unless `<name>` is `router` or a real submodule; each `routers.projects.<module>.<name>` target imports and its module reads `<name>` in a function body. Flip check: each whole-name literal pointed once at a sibling module binding the name; literals whose test still passes go in the handoff with the old-to-new map and scripts.
5. No `src/` module imports an underscore name from a module that defines a `router`; nothing under `service/` or `rest_api/handlers/` imports `routers/projects`; `main.py` unchanged.
6. Each of the 7 modules imports alone in a fresh interpreter (FastAPI resolves string annotations in the handler module's globals); the guarded app builds hermetically; guards pass, both baselines unchanged.
7. `make ruff` passes (F401 in `__init__.py`, complexity 16, license check). Tests pass in the four edited files, the untouched `..._execution.py`, `tests/codemie/rest_api/routers`, `tests/unit/routers` and `router_guards`.
8. The handoff says the eventual MR must ask a reviewer for `/sanity` and carry a `## Test harness` block. Auth code that moves: `_authorize_project_access` (R7-R14), `_raise_project_not_found`, the 400 gate; the inline `can_access` in `get_project_spends`; `_can_see_project_spending`, `_manageable_project_names`; the `is_admin or is_auditor` flags in R2, R3; 6 `Depends(authenticate)` and 8 `Depends(_authorize_project_access)`. R1, R5, R6 authorization stays in `project_service`.

## Non-goals

- Moving helpers into `service/` (step 7), renaming, changing paths, tags, operationIds or response models, adding a router-level `authenticate`.
- Fixing pre-existing behavior, carried as MR notes: PATCH returns 403 where DELETE and assignment routes return 404 (existence disclosed; in `project_service`); R12 and R13 re-run the gate after the dependency; R7-R14 use `request.state.user` as actor; `get_project_spends` passes the literal `action="GET spends"` and its docstring omits auditors and personal owners; the create response omits `allowed_models` that update returns.
- New tests, splitting test files, `main.py`, steps 6 and 7, guard code, baselines; commits or MR beyond the flow's.

## Open risks

- R11, R13, R14 have no handler test: AC 2, 3 and 6 guard them.
- AC 4 is necessary, not sufficient: the 24+ inert patches, and any patch aimed at a module that holds the name off the exercised path, pass silently if the mock is never asserted; the flip check lists them.
- Line counts are estimates; `_spending_support` (about 460) is closest to the cap. Fallback in Scope.
- Counts come from reading plus grep (the analysis said 5 test files; grep finds 4): AC 4 and 5 are the net.
