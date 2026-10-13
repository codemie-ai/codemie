# EPMCDME-15651 Step 1: Models Move Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move 61 request/response Pydantic classes from five routers into `rest_api/models/`, re-point every importer, regenerate the OpenAPI snapshot once.

**Architecture:** Verbatim, move-only. Tasks 1-5 each move one router's classes and re-point its importers (services, handler, tests). Task 6 regenerates `openapi.json`. Commit per task using the repository's existing convention (`EPMCDME-15651: Capital description`).

**Tech Stack:** Python, Pydantic v2, FastAPI, pytest, ruff, poetry.

**Spec:** `docs/superpowers/tasks/2026-10-08-epmcdme-15651-step1-models-move/spec.md` (research: `technical-analysis.md`, same dir)

## Global Constraints

- Class bodies, fields, validators, docstrings byte-identical; only imports and the new-file header change. Source order kept; a section-banner comment heading only moved classes moves with them.
- Constants used only by moved classes travel with them; all else stays in the router (`_WIDGET_COLUMNS`, `_ensure_allowed_budget_group_update_fields`, `_build_*` helpers).
- New models modules never import from `rest_api/routers`; referenced types are imported at runtime, not under TYPE_CHECKING. `BudgetCategory`/`AllocationMode` come from `codemie.service.budget.budget_enums`, never `enterprise/litellm/budget_categories`.
- Routers import moved classes at runtime, only those they use (ruff F401); no re-exports or aliases. Keep `from __future__ import annotations` in a new models file when its source router has it.
- Service/handler imports keep their TYPE_CHECKING or function-local form (no hoisting).
- New `.py` files carry the Apache header. No guard test code edits, no `main.py` change. Whole-suite gates and manual verification belong to Stage 7; checks here are targeted.

## Review Focus

- A test import or patch of a moved class still resolves through the router's own re-import: only `CHECK` catches it (Tasks 1-5, final run Task 6).
- A forward reference not importable at runtime fails on first validation, not on import: `SMOKE` calls `model_json_schema()` on every moved class.
- OpenAPI drift beyond the three approved `PaginationInfo` key renames (the guard's diff is cut at 200 lines): Task 6 deep-equality check before regenerating.
- Import cycle models <-> service layer (`service.budget.__init__` imports `budget_models`): `SMOKE` runs in a fresh interpreter per module.
- Wrong `BudgetCategory` enum accepted silently: `rg -n budget_categories src/codemie/rest_api/models` must stay empty (Tasks 3-5).

## Preflight (no commit)

Save this detector outside the repo (session scratchpad) as `check_moved_names.py`. It implements spec AC4 against the pre-move class lists at base commit `ddce2f7c51` and is the only detector of stale test imports and patches.

```python
import ast, re, subprocess, sys
from pathlib import Path

PKG, BASE = "codemie.rest_api.routers", "ddce2f7c51"
root, mods = sys.argv[1], sys.argv[2:]

def class_names(mod):
    if mod == "assistant":  # the other six assistant.py classes stay put
        return ["ToolCallResumeRequest"]
    path = f"src/{PKG.replace('.', '/')}/{mod}.py"
    source = subprocess.check_output(["git", "-C", root, "show", f"{BASE}:{path}"], text=True)
    return re.findall(r"^class (\w+)", source, re.M)

names = {m: class_names(m) for m in mods}
hits = []
for path in sorted(p for top in ("src", "tests") for p in Path(root, top).rglob("*.py")):
    text, alias = path.read_text(encoding="utf-8"), {m: {m} for m in mods}
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ImportFrom):
            for m in mods:
                if node.module == f"{PKG}.{m}":
                    hits += [f"{path}:{node.lineno}: imports {a.name}" for a in node.names if a.name in names[m]]
                elif node.module == PKG:
                    alias[m] |= {a.asname or a.name for a in node.names if a.name == m}
        elif isinstance(node, ast.Import):
            for a in node.names:
                for m in mods:
                    if a.name == f"{PKG}.{m}" and a.asname:
                        alias[m].add(a.asname)
    for m in mods:
        pattern = rf"\b(?:{'|'.join(map(re.escape, alias[m]))})\.(?:{'|'.join(names[m])})\b"
        for hit in re.finditer(pattern, text):
            hits.append(f"{path}:{text.count(chr(10), 0, hit.start()) + 1}: {hit.group(0)}")
print("\n".join(hits))
sys.exit(bool(hits))
```

- `CHECK <modules>` = `python3 <scratchpad>/check_moved_names.py . <modules>`; no output and exit 0 means clean.
- `SMOKE <module>` = `poetry run python -c "import importlib,sys; from pydantic import BaseModel; m=importlib.import_module(sys.argv[1]); [c.model_json_schema() for c in vars(m).values() if isinstance(c,type) and issubclass(c,BaseModel) and c.__module__==m.__name__]" <module>`
- `GUARDS` = `poetry run pytest tests/codemie/rest_api/router_guards -q`. In Tasks 2-5 add `--deselect tests/codemie/rest_api/router_guards/test_openapi_snapshot.py::test_openapi_matches_baseline` (red until Task 6); run that test once anyway and confirm it fails only on the task's `PaginationInfo` key.

**Procedure (Tasks 1-5):** (1) run the task's failing checks; (2) create or extend the models module and re-point importers per the Files list, splitting mixed imports (classes to models, handlers/helpers stay on the router); the test-file lists are a start, `CHECK` decides which lines; (3) verify: the task's `SMOKE`/`CHECK`, `rg -c '^class '` on the new file, `poetry run ruff check` and `ruff format --check` on touched files, the task's pytest line, `GUARDS`, then `git add` and confirm `git diff --cached --color-moved=dimmed-zebra` shows only moved blocks, import lines and new-file headers; (4) commit.

---

### Task 1: ToolCallResumeRequest to models/assistant.py

**Files:**
- Modify: `src/codemie/rest_api/models/assistant.py:36-43` (add `ToolCallAction` to the `codemie.core.models` import); append `ToolCallResumeRequest` after the last class
- Modify: `src/codemie/rest_api/routers/assistant.py:1143-1146` (delete class), `:46-66` (add the name to the `models.assistant` import), `:41` (drop `ToolCallAction` only if ruff flags it)
- Modify: `src/codemie/rest_api/handlers/assistant_handlers.py:26-27` (TYPE_CHECKING import from `codemie.rest_api.models.assistant`)
- Modify: `tests/codemie/rest_api/routers/test_tool_call_resume.py`

**Interfaces:** Produces `codemie.rest_api.models.assistant.ToolCallResumeRequest(conversation_id: str, action: ToolCallAction, stream: bool = True)`.

Test-first: yes — `poetry run python -c "from codemie.rest_api.models.assistant import ToolCallResumeRequest"` fails with ImportError and `CHECK assistant` lists the handler and the test imports; both flip after the move.

- [ ] Run the Procedure. In (3) use the import above plus `ToolCallResumeRequest.model_json_schema()` instead of `SMOKE`. Tests: `poetry run pytest tests/codemie/rest_api/routers/test_tool_call_resume.py tests/codemie/rest_api/routers/test_assistant.py -q`. Run `GUARDS` in full, snapshot included; it stays green (component key unchanged).

### Task 2: Cost-center models

**Files:**
- Create: `src/codemie/rest_api/models/cost_center.py` — 7 classes from `routers/cost_centers.py:40-83`, PaginationInfo through PaginatedCostCenterListResponse; needs `datetime`, `Optional`, `UUID`, `BaseModel`, `Field`
- Modify: `src/codemie/rest_api/routers/cost_centers.py` (delete L40-83)
- Modify: `tests/codemie/rest_api/routers/test_cost_centers_router.py`

Test-first: yes — `SMOKE codemie.rest_api.models.cost_center` fails with ModuleNotFoundError and `CHECK cost_centers` lists the test import; after: `SMOKE` passes, `CHECK` clean, class count 7.

- [ ] Run the Procedure. Tests: `poetry run pytest tests/codemie/rest_api/routers/test_cost_centers_router.py -q`.

### Task 3: Budget models and budget_service

**Files:**
- Create: `src/codemie/rest_api/models/budget.py` — 7 classes from `routers/budget_router.py:42-146` (BudgetCreateRequest through BudgetAssignmentBackfillResult); `_build_budget_response` (L106-111) stays in the router; needs `datetime`, `Optional`, `BaseModel`, `EmailStr`, `Field`, `BudgetCategory` from `budget_enums`
- Modify: `src/codemie/rest_api/routers/budget_router.py` (delete the classes)
- Modify: `src/codemie/service/budget/budget_service.py:51-56` (TYPE_CHECKING), `:664`, `:850` (function-local) to `codemie.rest_api.models.budget`
- Modify tests: `tests/codemie/rest_api/routers/test_budget_router.py`, `test_budget_router_additional.py`, `tests/codemie/service/budget/test_budget_service_activity.py`, `test_budget_service.py`

Test-first: yes — `SMOKE codemie.rest_api.models.budget` fails with ModuleNotFoundError and `CHECK budget_router` lists the service and test imports; after: `SMOKE` passes, `CHECK` clean, class count 7.

- [ ] Run the Procedure; also `rg -n budget_categories src/codemie/rest_api/models` is empty. Tests: `poetry run pytest tests/codemie/rest_api/routers/test_budget_router.py tests/codemie/rest_api/routers/test_budget_router_additional.py tests/codemie/rest_api/routers/test_budget_router_auditor.py tests/codemie/service/budget -q`.

### Task 4: Project-budget models and project_budget_service

**Files:**
- Create: `src/codemie/rest_api/models/project_budget.py` — `_DURATION_PATTERN` (`routers/project_budget_router.py:46`) and 15 classes from L57-146 and L522-612 (ProjectBudgetCreateRequest through ProjectBudgetMembersResponse, then CategoryBudgetSpec through PaginatedProjectBudgetGroupListResponse); needs `datetime`, `Optional`, `BaseModel`, `EmailStr`, `Field`, `AllocationMode`, `BudgetCategory` from `budget_enums`
- Modify: `src/codemie/rest_api/routers/project_budget_router.py` (delete them; keep a runtime import of `ProjectBudgetGroupUpdateRequest`, whose `model_fields` `_ensure_allowed_budget_group_update_fields` reads)
- Modify: `src/codemie/service/budget/project_budget_service.py:59-65` (TYPE_CHECKING) to `codemie.rest_api.models.project_budget`
- Modify tests: `tests/codemie/rest_api/routers/test_project_budget_router.py`, `test_project_budget_router_additional.py`, `test_project_budget_router_schema.py`, `tests/codemie/service/budget/test_budget_service_activity.py` (L413)

Test-first: yes — `SMOKE codemie.rest_api.models.project_budget` fails with ModuleNotFoundError and `CHECK project_budget_router` lists the service and test imports; after: `SMOKE` passes, `CHECK` clean, class count 15.

- [ ] Run the Procedure; also `rg -n budget_categories src/codemie/rest_api/models` is empty. Tests: `poetry run pytest tests/codemie/rest_api/routers/test_project_budget_router.py tests/codemie/rest_api/routers/test_project_budget_router_additional.py tests/codemie/rest_api/routers/test_project_budget_router_schema.py tests/codemie/service/budget -q`.

### Task 5: Project models

**Files:**
- Create: `src/codemie/rest_api/models/project.py` — 31 classes from `routers/projects.py:59-398` (ProjectCounters through CsvImportValidationResponse); `_WIDGET_COLUMNS` (L132-157) stays in the router, which then imports `SpendingWidgetColumn`; needs `datetime`, `Literal`, `Optional`, `UUID`, `BaseModel`, `Field`, `model_validator`, `BudgetCategory` from `budget_enums`
- Modify: `src/codemie/rest_api/routers/projects.py` (delete them; import every moved class it still references)
- Modify tests (only lines naming moved classes): `tests/codemie/rest_api/routers/test_projects_router.py` (incl. function-local imports near L3296-3387), `test_project_model_availability.py`, `test_project_model_availability_integration.py`

Test-first: yes — `SMOKE codemie.rest_api.models.project` fails with ModuleNotFoundError and `CHECK projects` lists the test imports; after: `SMOKE` passes, `CHECK` clean, class count 31.

- [ ] Run the Procedure; also `rg -n budget_categories src/codemie/rest_api/models` is empty. Tests: `poetry run pytest tests/codemie/rest_api/routers/test_projects_router.py tests/codemie/rest_api/routers/test_projects_router_auditor.py tests/codemie/rest_api/routers/test_project_model_availability.py tests/codemie/rest_api/routers/test_project_model_availability_integration.py -q`.

### Task 6: Regenerate the OpenAPI snapshot once

**Files:**
- Modify (generated, never hand-edited): `tests/codemie/rest_api/router_guards/baselines/openapi.json`

Test-first: yes — the existing `test_openapi_matches_baseline` fails before regeneration and passes after; `route_precedence.json` must not change.

- [ ] **Step 1: Final proof (AC1, AC3, AC4).** `CHECK budget_router project_budget_router projects cost_centers assistant` is clean (paste its output into your report as handoff evidence); `rg -n 'rest_api\.routers' src/codemie/rest_api/models` has no hits; `rg -c '^class '` prints 7, 15, 31, 7 for `models/budget.py`, `project_budget.py`, `project.py`, `cost_center.py` and 6 for `routers/assistant.py`.
- [ ] **Step 2: Approved-diff check, before writing anything.** Run `test_openapi_snapshot.py` (must fail). Then, in a throwaway `poetry run python` snippet from the repo root, take the old baseline via `git show ddce2f7c51:tests/codemie/rest_api/router_guards/baselines/openapi.json`, text-replace the three keys `codemie__rest_api__routers__{budget_router,cost_centers,projects}__PaginationInfo` with `codemie__rest_api__models__{budget,cost_center,project}__PaginationInfo` (this also renames the `$ref` lines), and assert `json.loads(text) == load_guarded_app_dump()["openapi"]` (from `tests.codemie.rest_api.router_guards.guarded_app`). Any mismatch means fixing the code in Tasks 2-5, never regenerating.
- [ ] **Step 3: Regenerate once.** `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`. Then `git diff --exit-code -- tests/codemie/rest_api/router_guards/baselines/route_precedence.json` is empty, `git status --short` shows only `openapi.json` modified, full `GUARDS` is green, and `poetry run pytest tests/codemie/rest_api/test_xwiki_index_endpoints.py tests/codemie/rest_api/test_main_retrieval_backend_disabled.py -q` passes.
- [ ] **Step 4:** Commit.

## Negative-constraint pass

- No router imports in models, no TYPE_CHECKING for model types: Global Constraints; Task 6 Step 1 and `SMOKE`. Not the enterprise `BudgetCategory`: Tasks 3-5 `rg` check.
- No re-exports/aliases, no hoisting of service imports: Tasks 1, 3, 4 keep each import's form; F401 in the ruff checks.
- No class beyond the 61, no `PaginationInfo` merge, no field or validator edits: color-moved review per task; Task 6 Step 2.
- Handlers, helpers, `_WIDGET_COLUMNS` stay put; no `routers/utils.py`, `main.py` or guard-test edits: Tasks 4-5 file lists; Task 6 `git status`.
- Snapshot regenerated exactly once, only after the approved-diff check: Task 6 Steps 2-3. No whole-suite gates, manual verification, MR or `/sanity`: none planned.
