# EPMCDME-15651 Step 5: Projects Router Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `routers/projects.py` (1,253 lines, 14 routes R1-R14, prefix `/v1`, 46 top-level names, no classes) with the package `routers/projects/` (`__init__`, route modules `crud`, `spending`, `management`, `membership`, private `_common`, `_spending_support`: 7 files, each module at most 500 lines); retarget 313 patch strings and 27 import statements in 4 test files. No API change.

**Architecture:** Move-only, block by block. Task 1 `git mv`s the file to `projects/__init__.py` unchanged; Tasks 2-3 extract the private modules; Tasks 4-7 extract route modules from the tail upward (membership R8-R14, management R5-R7, spending R4, crud R1-R3), each adding its `include_router` line at the bottom of `__init__`, above the earlier ones. Inline routes register at import, before the includes, and the extracted block is always the tail of the inline run, so `app.routes` equals BASE order after every commit (extracting R4 first would append it after R14). The package imports and `main.py`'s `from codemie.rest_api.routers import (..., projects, ...)` resolves at every commit; there is no atomic switch. Commit per task using the repository's existing convention (`EPMCDME-15651: Capital description`).

**Tech Stack:** FastAPI `APIRouter`, pytest with `mock.patch`, ruff, Python `ast` for the scratch tools.

**Spec:** `docs/superpowers/tasks/2026-10-09-epmcdme-15651-step5-projects-package/spec.md` (research: `technical-analysis.md`). `BASE` = `23ac728c4b` (HEAD before Task 1).

## Global Constraints

- Bodies, decorators, docstrings, function-local imports (`project_assignment_service`, `csv_import_service`, `MAX_CSV_BYTES`), `_WIDGET_COLUMNS` and comments move byte-identical, in source order per module; only imports, headers, `APIRouter()`, includes, `__all__` and `logger` lines change. Text is moved by script, never retyped. Every new `.py` carries the verbatim 13-line Apache header (last line `# limitations under the License.`) and `from __future__ import annotations`.
- `__init__` keeps `router = APIRouter(tags=["Projects"], prefix="/v1", dependencies=[])` as it is today (no router-level `authenticate`); route modules use a bare `router = APIRouter()`; decorators stay unchanged. `__all__ = ["router"]`, no re-exports.
- A module whose moved code reads `logger` defines its own `logger = logging.getLogger(__name__)`: `_common` (`_raise_project_not_found`) and `crud` (`_attach_assigned_budget_summaries`) only. In `src/`, underscore names are imported only from `_common` / `_spending_support`; private modules never import route modules or `__init__`; `_spending_support` imports nothing from the package.
- No edits to `main.py`, guard code or baselines; no behavior change, no new tests, no test-file split, never `regenerate_baselines`. If `GUARDS` fails on `openapi.json` or `route_precedence.json`: stop, report the diff, ask. Pre-existing gaps (spec Non-goals) stay as they are. `make ruff`, whole-suite gates, sonar, browser checks and Jira/MR work belong to Stage 7 and later.
- Stage only the task's files by path (never `git add -A`, `.gitignore`, the task dir, `opencode.json.lock`, `skills-lock.json`, anything under `local/`); no push, no MR. No tree copy or archive of BASE under `local/`; BASE text only via `git show` into single small files.
- Before Task 4 (first `include_router`) query context7 for FastAPI `include_router` (prefix, tags, dependencies merge); `COMPARE` proves it.

## Module map

BASE line numbers, decorators included. Each route module produces `router: APIRouter`; `__init__` consumes each. A symbol read by one module moves with it.

| Module | BASE lines | Symbols | Imports |
|---|---|---|---|
| `_common` (4 symbols + own `logger`) | 119-125, 961-985, 1240-1253 | `_resolve_cost_center_name`, `_authorize_project_access`, `_raise_project_not_found`, `_ensure_user_management_enabled` | none from package |
| `_spending_support` (20) | 85-113, 180-362, 428-527, 717-815 | `_spend_repo`, `_WIDGET_COLUMNS`, 9 + 5 + 4 helpers | none from package |
| `crud` (R1-R3, 10 symbols) | 128-154, 163-177, 365-425, 530-714 | list/detail `_sync` helpers, 4 list-budget helpers, `_build_project_detail_response`; own `logger` | `_common`: gate, `_resolve_cost_center_name`; `_spending_support` |
| `spending` (R4, 2) | 157-160, 818-888 | `_get_project_by_name_sync`, `get_project_spends` | `_common`: gate, `_raise_project_not_found`; `_spending_support`: `_spend_repo` and what R4 reads |
| `management` (R5-R7, 3) | 891-958, 988-1014 | `update_project`, `update_allowed_models`, `delete_project` | `_common`: gate, `_resolve_cost_center_name`, `_authorize_project_access` |
| `membership` (R8-R14, 7) | 1017-1237 | the 7 assignment, bulk and CSV handlers | `_common`: gate (R12, R13), `_authorize_project_access` |

Final `__init__.py` (header and `from __future__ import annotations` kept; the existing `router = APIRouter(...)` statement kept as is; no other code):

```python
from fastapi import APIRouter

from codemie.rest_api.routers.projects import crud, management, membership, spending

router = APIRouter(tags=["Projects"], prefix="/v1", dependencies=[])
router.include_router(crud.router)  # then spending, management, membership

__all__ = ["router"]
```

## Review Focus

- R11, R13, R14 have no handler test; a dropped `Depends(authenticate)` or `Depends(_authorize_project_access)` changes the OpenAPI snapshot: `COMPARE` (a)-(c) and `GUARDS` after every task from Task 4.
- Whole-name patches bound in the wrong module (24+ already inert at BASE: `config`, `application_repository`, `get_session`) pass silently when the mock is never asserted: `PATCHUSE`, `MOVED`, `FLIP`.
- `.env.test` pins `ENABLE_USER_MANAGEMENT=false`: a `config` patch aimed at a module that does not run the gate gives 400 in gate-calling handlers. `config` has two readers (`_common` gate, `crud._is_budget_provider_active`).
- FastAPI resolves string annotations in the handler module's globals: every annotation name (`Request`, `Application`, `UploadFile`, `Optional`, `Literal`, `BudgetCategory`, `date`, `Path`, `Query`, `User`, each request model) must be module-level in the module holding the handler, `_authorize_project_access` in `_common` included; `F821`, `IMPORT` and the guarded app build catch a miss.
- Size: `_spending_support` (about 460) and `crud` (about 365) are closest to the cap. Over 500: cut at a function boundary (private) or route boundary (route module), record it in the handoff; a cut that would reorder a route: stop and ask.

## Tools and checks

`WORK=local/router-split/step5` (gitignored via `local/*`, never staged). `PKG=src/codemie/rest_api/routers/projects`. Run Python as `poetry run python`. Test log defaults to `$TASK_DIR/test.local.log` (gitignored by `*.local.*`).

- Origin: copy-and-adapt the top-level `*.py` / `*.sh` of `local/router-split/step4/` (its `README.md` explains each tool; never copy `*.json`, `BASE`, `base_*`, `patch-map.txt`, `flip-report.txt`, `baseline.md`), then adapt: `_lib.sh` (WORK, PKG, TASK_DIR); `at.sh` (file list below); `size.sh` (first 13 lines of each module byte-equal to `base_projects.py` lines 1-13); `moved.sh`, `leftover.sh`, `import.sh`, `move.sh`, `move_symbols.py`, `patchuse.py`, `flip.py`, `astmove.py` (package `projects`, BASE file `$WORK/base_projects.py`, `router`/`logger` exempt; `flip.py` defaults to the four edited test files); `routes_probe.py` and `compare.py` as below; `modulemap.py` ranges and counts from the Module map (`--check`: all 46 BASE symbols except `router`, `logger` in exactly one module, route counts crud 3, spending 1, management 3, membership 7, `_common` 4 symbols, `_spending_support` 20).
- `routes_probe.py`: `INDEX_MODULE` becomes `codemie.rest_api.routers.projects`; the endpoint predicate is `module == pkg or module.startswith(pkg + ".")` (a bare prefix would also match `project_budget_router`, `project_settings`); dump `projects_routes` in `app.routes` order as `{pos, methods, path, name, status_code, tags, deps, module}` plus `projects.router` prefix, tags and dependency names and all routes' `{path, methods}`.
- `ROUTES <out>` / `COMPARE [--final] <after.json>` against `routes_before.json`, spec AC 2: (a) the 14 rows (methods, path, name, status_code, tags, deps) equal element-wise in order, `module` ignored; (b) same `pos` offsets (one contiguous block), same routes right before and after, every other route's (path, methods) equal; (c) router prefix `/v1`, tags `['Projects']`, dependencies `[]` in both dumps, `authenticate` in the deps of R1-R6, `_authorize_project_access` in the deps of R7-R14; (d) `--final` only: no endpoint with `module == codemie.rest_api.routers.projects`. It prints routes per endpoint module: after Tasks 4-7 the remainder in `__init__` is 7, 4, 3, 0.
- `AT` = `poetry run pytest -n 0 --show-capture=no -q` on `tests/codemie/rest_api/routers/test_projects_router.py`, `test_projects_router_auditor.py`, `test_project_model_availability.py`, `test_project_model_availability_integration.py` and the untouched `test_project_model_availability_execution.py`. `GUARDS` = `poetry run pytest tests/codemie/rest_api/router_guards -n 0 --show-capture=no -q`. `IMPORT <module>` = fresh interpreter, `importlib.import_module`. `SIZE`: each module at most 500 lines (`wc -l`), header present.
- `MOVED <module>`: no hit of `routers[./]projects` followed by `.` or ` import ` and one of the module's symbols over `src tests` (`moved.sh 'a|b|c'`), except whole-name patch strings of helpers that inline code in `__init__` still resolves.
- `PATCHUSE`: for each `codemie.rest_api.routers.projects.<sub>.<name>` literal and `from ...projects.<sub> import <name>` in `tests/`, fail unless `<sub>` is a module and `<name>` (`<Name>` of `<Name>.<attr>`) is bound there and, for patch literals, loaded inside a function body.
- `LEFTOVER` = `rg -nP "routers[./]projects\.(?!(?:router|crud|spending|management|membership|_common|_spending_support)\b)\w+|routers\.projects import (?!router\b)" src tests .ai-run`, empty at the end (AC 4 stale-patch detector).
- `FLIP` (AC 4): for each whole-name literal `routers.projects.<mod>.<Name>` in the four edited files, point it at the first other module that binds `<Name>`, run the enclosing test (the file when the literal sits in a helper), restore the file in `finally`, write `PASS` (silent: listed in the handoff), `FAIL` (caught) or `NO-SIBLING` (safe) per string to `$WORK/flip-report.txt`. Run on a clean tree; `git diff --exit-code -- tests` afterwards.
- `ASTMOVE` (Task 8): each of the 46 top-level def, async def and assignment nodes of `$WORK/base_projects.py` except `router` and `logger` occurs byte-identical (decorators included) in exactly one new module and nowhere else; the package adds no other top-level definition besides `router`, `logger` and `__all__`.

**Patch rules** (spec "Test retarget rules" are authoritative): retarget a string only when the code that resolves the name on the exercised path moved in this task (until then `routers.projects` still binds it). Whole-name patches go to the module whose handler under test uses the name (`_resolve_cost_center_name`, `_ensure_user_management_enabled`, `project_service`, `project_visibility_service`); `<Name>.<attr>` keeps its tail. Imports take the defining submodule, one statement per module (a mixed import is split; `router as projects_router` stays its own single-line import from `routers.projects`). Inert strings (24+) go to the handler's module if it binds the name, else `_common`, and stay inert. A second patch is added only to the budgets test below; any other test that turns red for a missing second patch: stop and ask. Append each `file:line: old -> new` to `$WORK/patch-map.txt`.

**Procedure (Tasks 2-7):** (1) Retarget the task's tests and run `AT` (or `IMPORT` when untested): red with ModuleNotFoundError or AttributeError. (2) Create the module: header, `from __future__`, a copy of the `__init__` import block, `router = APIRouter()` (route modules), then `move.sh <module> $(poetry run python $WORK/modulemap.py <module>)`; for route modules add the import and the `include_router` line (bottom of `__init__`, above the earlier ones); prune with `ruff check --select F401 --fix --unsafe-fixes` on both files and add names reported by `--select F821` (shared ones from the private modules; `logger` defined locally). (3) `MOVED`, `PATCHUSE`. (4) `IMPORT` the module, `SIZE`, `ruff check` and `ruff format --check` on touched files, `AT`, `ROUTES` + `COMPARE`, full `GUARDS` with `git status` showing no baseline change; `git add` the task's files and confirm `git diff --cached --color-moved=dimmed-zebra` shows only moved blocks, imports, headers, `APIRouter()`, includes and `logger`. (5) Commit.

---

### Task 1: Tools, before-dump, package skeleton

**Files:** `git mv src/codemie/rest_api/routers/projects.py src/codemie/rest_api/routers/projects/__init__.py` (create the directory first); scratch tools in `$WORK`, uncommitted.

Test-first: no — rename only, bytes unchanged; proof: `git diff --cached -M --stat` shows a 100% rename, `AT`, `GUARDS` and `COMPARE` stay green.

- [ ] Setup: `git check-ignore $WORK`; `git rev-parse HEAD > $WORK/BASE` (it is `23ac728c4b` or a later planning-artifact commit; `git diff --exit-code 23ac728c4b HEAD -- src tests` must be empty); `git show 23ac728c4b:src/codemie/rest_api/routers/projects.py > $WORK/base_projects.py`; `git grep -n 'routers.projects' 23ac728c4b -- tests > $WORK/base_test_refs.txt` (expect 340 hits in 4 files).
- [ ] Tools: copy-and-adapt per Tools and checks; self-check with `modulemap.py --check` and, after the `git mv`, `move.sh --dry-run` for every module.
- [ ] Baseline: `ROUTES $WORK/routes_before.json` before the move (14 routes, prefix `/v1`, tags `['Projects']`, dependencies `[]`); keep `routes_before.orig.json` and its sha256; record `AT` and `GUARDS` pass counts in `$WORK/baseline.md`.
- [ ] `git mv`, then `AT`, `GUARDS`, `ROUTES` + `COMPARE` (all 14 endpoints still in `__init__`); commit the rename only.

### Task 2: `_common`

**Files:** Create `projects/_common.py`; modify `__init__.py` (import the moved names from `._common`); tests: all four edited files (`config`), `test_projects_router.py` (imports, `_authorize_project_access` and `_raise_project_not_found` tests).

Test-first: yes — retargeted imports and patches fail with ModuleNotFoundError; after: `IMPORT ..._common` passes, `AT` green.

Interfaces: Produces the four `_common` symbols; reads `config`, `get_session`, `application_repository`, `cost_center_repository`, `Ability`, `logger`.

- [ ] Procedure, without `router` or `include_router`. `logger` stays in `__init__` (`crud` code still reads it); `_common` defines its own. Retarget: imports `_authorize_project_access`, `_raise_project_not_found` (module-level block `test_projects_router.py:40-55` split); all `config` literals to `_common`; `test_list_projects_attaches_assigned_budgets_and_skips_invalid_categories` keeps its `projects.config` and gains `_common.config` (the only signature change); `get_session`, `application_repository`, `Ability`, `logger` in the `_authorize_project_access` / `_raise_project_not_found` tests (about lines 1691-1790) to `_common`; the inert `application_repository` strings (allowed-models and integration tests) to `_common`. `_resolve_cost_center_name` and `_ensure_user_management_enabled` literals stay: their handlers are still in `__init__`.

### Task 3: `_spending_support`

**Files:** Create `projects/_spending_support.py` (no `router`); modify `__init__.py` (inline code imports what it uses from it); tests: `test_projects_router_auditor.py`, `test_projects_router.py`.

Test-first: yes — as Task 2; after: `AT` green.

Interfaces: Produces the 20 names; `spending` later imports `_spend_repo`.

- [ ] Procedure. Retarget: imports `_manageable_project_names`, `_can_see_project_spending` (x2) in the auditor file and `_is_budget_period_open` in `test_projects_router.py`; literals read by the list/detail spending path (`_spend_repo`, `get_async_session` for `include_spending`, `budget_repository`, `project_budget_assignment_repository` where `_spending_support` reads it). `get_project_spends` literals stay (R4 is inline until Task 6), as does the one `include_budgets` `get_async_session` (Task 7).

### Task 4: `membership`

**Files:** Create `projects/membership.py`; modify `__init__.py` (first `include_router` line); tests: `test_projects_router.py`. Read context7 first.

Test-first: yes — retargeted strings fail with ModuleNotFoundError; R11, R13 and R14 have no handler test, so `IMPORT`, `COMPARE` (a)-(c) and `GUARDS` are their proof; after: `AT` green.

Interfaces: Produces `membership.router` (R8-R14); consumes `_common._authorize_project_access` and `_ensure_user_management_enabled`.

- [ ] Procedure. Retarget: imports `assign_user_to_project`, `update_user_project_assignment`, `remove_user_from_project`, `validate_users_from_csv` (7, from about line 1799); `get_session` literals of those tests to `membership`. `UploadFile` must be module-level for the R12/R13 annotations; the function-local service imports stay in the bodies.

### Task 5: `management`

**Files:** Create `projects/management.py`; modify `__init__.py` (include line above `membership`); tests: `test_projects_router.py`, `test_project_model_availability.py`, `test_project_model_availability_integration.py`.

Test-first: yes — as Task 4; after: `AT` green.

Interfaces: Produces `management.router` (R5-R7); consumes `_common` gate, `_resolve_cost_center_name`, `_authorize_project_access` (the `Depends` of `delete_project`).

- [ ] Procedure. Retarget: imports `update_project`, `update_allowed_models` (about 14 statements), `delete_project`; the mixed import at `test_project_model_availability_integration.py:404-407` is split (`_build_project_detail_response` stays on `routers.projects` until Task 7); `project_service`, `_resolve_cost_center_name` (update and allowed-models tests) and `_ensure_user_management_enabled` (8) to `management`; `get_session` for delete tests and the 3 inert allowed-models strings to `management`.

### Task 6: `spending`

**Files:** Create `projects/spending.py`; modify `__init__.py` (include line above `management`); tests: `test_projects_router.py`, `test_projects_router_auditor.py`.

Test-first: yes — as Task 4; after: `AT` green.

Interfaces: Produces `spending.router` (R4); consumes `_spending_support._spend_repo` and the helpers R4 reads, `_common._raise_project_not_found` and gate.

- [ ] Procedure. Retarget: imports `get_project_spends`; for `get_project_spends` tests the literals `get_session`, `get_async_session`, `_spend_repo` (4 dotted, tail kept) and `application_repository` to `spending`. The inline `can_access` check and the literal `action="GET spends"` move untouched.

### Task 7: `crud` and the final `__init__`

**Files:** Create `projects/crud.py`; reduce `__init__.py` to the final shape above (delete `logger` and unused imports by hand); tests: `test_projects_router.py`, `test_projects_router_auditor.py`, `test_project_model_availability.py`, `test_project_model_availability_integration.py`.

Test-first: yes — as Task 4; after: `LEFTOVER` empty, `rg -n '^(def|class|async def|[A-Za-z_]+ *=)' $PKG/__init__.py` shows only `router`, `__all__`, `COMPARE --final` green.

Interfaces: Produces `crud.router` (R1-R3) and `_build_project_detail_response`; consumes `_common` gate and `_resolve_cost_center_name`, `_spending_support` helpers.

- [ ] Procedure; `crud` is now the first include. Retarget: imports `create_project`, `list_projects`, `get_project_detail`, `_build_project_detail_response`; `project_service`, `project_visibility_service`, `_resolve_cost_center_name` (create tests), `_get_project_detail_sync`, `get_session` (create, list, detail), `get_async_session` (the `include_budgets` test), `project_budget_assignment_repository` (7 dotted, tail kept) where `_attach_assigned_budget_summaries` reads it, `logger`, `get_active_provider` and `SettingsService` (each to the module that reads it on the tested path) to `crud`; the budgets test's `projects.config` becomes `crud.config`.

### Task 8: Final proof and handoff

**Files:** none; nothing to commit (a working-tree diff is a bug).

Test-first: no — proof only, no code change.

- [ ] **Step 1: Proof (AC 1-6).** `projects.py` gone; 7 `.py` files, `SIZE` OK, `rg -L 'limitations under the License' $PKG` empty; `ROUTES` + `COMPARE --final`; `ASTMOVE` with a negative control (one body byte changed in a scratch copy of one module must fail); `git diff --color-moved=dimmed-zebra 23ac728c4b -- src` shows only moved blocks; `LEFTOVER` empty, `PATCHUSE` OK, every line of `base_test_refs.txt` is on the left of `patch-map.txt` or a `router` import; `FLIP` run and `flip-report.txt` written. AC 5: `rg -nUP "routers\.projects\.(?:crud|spending|management|membership) import (?:[^\n(]*|\([^)]*)\b_[a-z]\w*" src`, `rg -n "routers\.projects import|projects\.(crud|spending|management|membership)" $PKG/_common.py $PKG/_spending_support.py` and `rg 'routers\.projects' src/codemie/service src/codemie/rest_api/handlers` all empty; `rg -n 'routers/projects\.py' . -g '!docs/superpowers' -g '!local'` empty; read-only `rg 'rest_api\.routers\.projects' /home/taras_spashchenko/EPAM/cm/codemie-enterprise` has no hit (a hit: stop and ask); `rg -c '^class '` on the package totals 0. `IMPORT` for all 7 modules. `git diff --exit-code 23ac728c4b -- src/codemie/rest_api/main.py tests/codemie/rest_api/router_guards` is empty: `openapi.json` and `route_precedence.json` byte-identical, nothing regenerated.
- [ ] **Step 2: Suites.** `GUARDS`; `AT`; `poetry run pytest tests/codemie/rest_api/routers tests/unit/routers -n 0 --show-capture=no -q`.
- [ ] **Step 3: Handoff and cleanup.** In `local/router-split/HANDOFF.md` (gitignored, never staged) record step 5 done, `patch-map.txt`, the scripts, the before/after `ROUTES` + `COMPARE` output, the `PASS` and `NO-SIBLING` rows of `flip-report.txt` with the inert strings, any fallback module, the spec's non-goal MR notes, and that the eventual MR must ask a reviewer to post `/sanity` and carry a `## Test harness` block, listing the moved auth code of spec AC 8: `_authorize_project_access` (R7-R14), `_raise_project_not_found`, the 400 gate, the inline `can_access` in `get_project_spends`, `_can_see_project_spending`, `_manageable_project_names`, the `is_admin or is_auditor` flags in R2 and R3, 6 `Depends(authenticate)` and 8 `Depends(_authorize_project_access)`; R1, R5, R6 authorization stays in `project_service`. Then delete scratch artifacts (`routes_*.json`, `base_projects.py`, `base_test_refs.txt`, scratch copies, `__pycache__`, `<task dir>/test.local.log`); keep scripts, `README.md`, `patch-map.txt`, `flip-report.txt`, `baseline.md`. `find local/router-split/step5 -size +200k` is empty and `git status --porcelain` lists only the two lock files (plus `.gitignore` and the planning docs if their commit has not landed).

## Negative-constraint pass

negative-constraints: stated (spec Constraints, Non-goals, Open risks; caller notes).

- No route reordered or moved across modules; no cut that reorders (tail-upward extraction keeps R4 in place): Tasks 4-7 `COMPARE` (a)-(b); Review Focus fallback.
- No re-export; `__init__` holds only `router`, includes, `__all__`: Task 7 shape check, `LEFTOVER`, `ASTMOVE`.
- No underscore import from a router-defining module; private modules never import route modules; `_spending_support` imports nothing from the package; nothing under `service/` or `rest_api/handlers/` imports `routers/projects`: Task 8 Step 1 `rg` checks.
- No router-level `authenticate`, no changed path, tag, operationId or response model, bare `APIRouter()` in route modules: `COMPARE` (c) per task; `GUARDS` byte-exact snapshot.
- No behavior change, rename, helper moved to `service/` (step 7), dead-code deletion or fix of the listed pre-existing gaps: color-moved review per task; `ASTMOVE`.
- No second `config` patch except the budgets test, no new tests, no test-file split: Task 2 note, Patch rules; `git diff --stat -- tests` shows edits only.
- No `main.py`, guard-code or baseline change, no `regenerate_baselines`: Task 8 `git diff --exit-code`; `GUARDS` per task.
- No `.gitignore` or planning-doc edit in any task commit, no staged scratch or lock files, no whole-suite gates, `make ruff`, sonar, browser or Jira/MR work, no BASE tree copy, no push or MR: staging rule in Global Constraints; none planned; Task 8 `find` and `git status` checks.
