# EPMCDME-15651, step 1: models move

## Purpose

Move request/response Pydantic classes out of four routers (and one class out of `assistant.py`) into `rest_api/models/`. This removes the services' imports from `budget_router` / `project_budget_router` and the handler's import of `ToolCallResumeRequest` from `routers/assistant.py`. It also clears the ground for the router splits in steps 2 to 5. Move-only: reviewable with `git diff --color-moved`. Same branch `EPMCDME-15651_split-router-modules`; no MR until the whole ticket is done.

## Scope (user-resolved)

61 classes, every module-level `class` in these files, moved verbatim in source order:

| Source | Classes | Target (all new unless noted) |
|---|---|---|
| `routers/budget_router.py` | 7 | `models/budget.py` |
| `routers/project_budget_router.py` | 15, plus `_DURATION_PATTERN` (used only by 4 of their fields) | `models/project_budget.py` |
| `routers/projects.py` | 31 | `models/project.py` |
| `routers/cost_centers.py` | 7 | `models/cost_center.py` |
| `routers/assistant.py` | `ToolCallResumeRequest` only | existing `models/assistant.py` |

Constants and validators used only by the moved classes travel with them. Everything else stays in the router, including `_WIDGET_COLUMNS` in `projects.py` (it instantiates `SpendingWidgetColumn`, so the router imports that class) and `_ensure_allowed_budget_group_update_fields`. The other router modules and the other six `assistant.py` classes stay put.

Routers import the moved classes they use from the new models modules. Service and handler imports are re-pointed to the same modules, keeping their existing TYPE_CHECKING or function-local form (no hoisting). Every test import or `mock.patch` target that names a moved class is re-pointed. Patches on handlers and helpers stay on the routers.

## Constraints

- Class bodies, fields, validators and docstrings are byte-identical. Only imports and the new-file header change.
- New models modules never import from `rest_api/routers`. Types a model references are imported at runtime, not under TYPE_CHECKING, so pydantic can resolve them. `BudgetCategory` and `AllocationMode` come from `service/budget/budget_enums.py`, not the enterprise `BudgetCategory`.
- The four routers with `from __future__ import annotations` (budget, project_budget, projects, cost_centers) import moved classes at runtime, so FastAPI can resolve annotations.
- No compatibility re-exports or aliases. Routers import only what they use.

## OpenAPI (amended step-0 criterion 2)

Move-only produces exactly this change, and nothing else is allowed:

- Three `PaginationInfo` component keys are renamed:
  - `codemie__rest_api__routers__budget_router__PaginationInfo` becomes `codemie__rest_api__models__budget__PaginationInfo`
  - `codemie__rest_api__routers__cost_centers__PaginationInfo` becomes `codemie__rest_api__models__cost_center__PaginationInfo`
  - `codemie__rest_api__routers__projects__PaginationInfo` becomes `codemie__rest_api__models__project__PaginationInfo`
- The four `$ref` lines pointing at them change (baseline L18491, L18511, L18704, L18718).
- The `models__user_management__PaginationInfo` key is unchanged. The budget and cost_centers classes stay separate (no merge).
- Keys are sorted, so the diff shows the renamed components as moved blocks.
- The snapshot is regenerated exactly once, after the moves, by `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`. Run the snapshot test first; its failure must show only the approved set. Any other diff means fixing the code, not regenerating again.

## Acceptance criteria

1. The 61 classes exist in the target files (per-file counts 7, 15, 31, 7, +1). None is defined in `budget_router`, `project_budget_router`, `projects`, `cost_centers`, and `assistant.py` keeps only its other six classes.
2. `git diff --color-moved` shows only moved blocks, import lines and new-file headers. New `.py` files carry the Apache header.
3. No file under `src/codemie/service/` or `rest_api/handlers/` imports any of the 61 classes from `rest_api/routers/`. `budget_service.py` (L51-56, L664, L850), `project_budget_service.py` (L59-65) and `assistant_handlers.py` (L26-27) import from the new models modules. No `rest_api/models/` file imports from `rest_api/routers/`.
4. Grep proof, over the whole `src/` and `tests/` trees, with the 61 names taken from the pre-move `^class` listing of the five files. The checks are:
   - no `from codemie.rest_api.routers.<module> import` statement that names one of the 61 classes (multi-line import blocks included);
   - no `routers.<module>.<ClassName>` string (patch target) and no `<module>.<ClassName>` attribute access, for the five modules.
   Routers legitimately keep importing moved names, so stale test imports would resolve silently. This grep is the only detector, and its result is recorded in the handoff.
5. Existing guards, snapshot and route precedence, pass with the amended snapshot. The OpenAPI change matches the approved set: applying the three key renames and the `$ref` renames to the old baseline gives a document deeply equal to the new one. Paths, operationIds, tags and all other components are unchanged.
6. `route_precedence.json` is byte-identical after regeneration (`git diff` empty). `regenerate_baselines` rewrites both files, so this is checked explicitly.
7. Each new models module imports cleanly in a fresh interpreter on its own, which exposes cycles that import order would otherwise mask. The guarded app still builds hermetically.
8. `make ruff` passes (F401 on stale imports, max-complexity 16). Tests pass in `tests/codemie/rest_api/routers`, `tests/unit/routers` (including `test_xwiki_index_endpoints.py` and `test_main_retrieval_backend_disabled.py`), `tests/codemie/service/budget/`, and `test_tool_call_resume.py`.

Step-0 criteria that still apply: guards run unchanged except for the one approved baseline regeneration; building the app contacts no network, database or Elasticsearch; the hermetic guard profile is untouched.

## Non-goals

- Moving any class outside the 61: the other 11 router modules, the other six `assistant.py` classes, `index.py` and `analytics.py` classes.
- Merging, renaming or altering any class, field or validator; deduplicating the identical `PaginationInfo` pair.
- Moving handlers, helper functions or constants beyond those used only by the moved classes. No router splitting and no `routers/utils.py` changes (step 2).
- Changing endpoint names, paths, response models, operationIds or tags. No `main.py` or registration change. No edits to the guard test code, only the regenerated `openapi.json`.
- Touching `enterprise/litellm/budget_categories.BudgetCategory`.
- Commits, push or MR beyond the flow's own, and a `/sanity` request (no auth or security code moves).

## Open risks (each covered by an acceptance check)

- The importer list in the technical analysis is not exhaustive: covered by AC 4.
- Un-migrated test imports and patches resolve silently because routers re-import the names: covered by AC 4.
- `from __future__ import annotations` routers need runtime imports of moved classes: covered by AC 5 and AC 7.
