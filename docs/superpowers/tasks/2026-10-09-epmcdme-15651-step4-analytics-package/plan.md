# EPMCDME-15651 Step 4: Analytics Router Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `routers/analytics.py` (3,113 lines, 85 routes, prefix `/v1/analytics`) with the package `routers/analytics/` (`__init__`, 9 route modules, `_common`, `_spending_support`: 12 files, each at most 500 lines); retarget eight test files (55 patch strings, 79 imports). No API change. (Spec AC 6 says "13 modules"; its Scope lists 12 files, which this plan follows.)

**Architecture:** Move-only, block by block. Task 1 `git mv`s the file to `analytics/__init__.py` unchanged; Tasks 2-3 extract the private modules; Tasks 4-12 extract route modules from the tail of the file upward (routing first, usage last), each adding its `include_router` line at the bottom of `__init__`, above the earlier ones. Inline routes register at import, before the includes, and the 85 blocks are contiguous, so `app.routes` equals BASE order after every commit: the package imports, `main.py`'s multi-line `from codemie.rest_api.routers import (..., analytics, ...)` resolves, element-wise `COMPARE` and `GUARDS` are green at every commit, and there is no atomic switch. Task 12 reduces `__init__` to its final shape. Commit per task using the repository's existing convention (`EPMCDME-15651: Capital description`).

**Tech Stack:** FastAPI `APIRouter`, pytest with `mock.patch`, ruff, Python `ast` for the scratch tools.

**Spec:** `docs/superpowers/tasks/2026-10-09-epmcdme-15651-step4-analytics-package/spec.md` (research: `technical-analysis.md`). `BASE` = `c810715933` (HEAD before Task 1).

## Global Constraints

- Bodies, decorators, docstrings, function-local imports, constants, banner comments, dead code (`_get_user_all_projects`, `_build_key_spending_tabular_data` and its callees, `PROJECTS_FILTER_ADMIN_DESC`, `ERROR_MSG_PROJECT_EMPTY`) and the four classes move byte-identical, in source order per module; only imports, headers, `APIRouter()`, includes, `__all__` and `logger` lines change. Text is moved by script, never retyped. New `.py` files carry the Apache header and `from __future__ import annotations`.
- Route modules use a bare `router = APIRouter()`; `__init__` keeps `router = APIRouter(tags=["Dashboard Analytics"], prefix="/v1/analytics", dependencies=[Depends(authenticate)])` verbatim. The route-level dependencies of the 10 leaderboard GETs `[admin_or_maintainer_or_auditor_access, _require_leaderboard_enabled]` and of `POST /leaderboard/compute` `[admin_access_only, _require_leaderboard_enabled]` stay in their decorators. No re-exports.
- A module whose moved code reads `logger` defines its own `logger = logging.getLogger(__name__)` (never imported from `_common`). In `src/`, underscore names are imported only from `_common` / `_spending_support`; private modules never import route modules or `__init__`; `_spending_support` imports `_common` only.
- No edits to `main.py`, `cli_analytics.py`, guard code or baselines; no behavior change, no new tests, never `regenerate_baselines`. If `GUARDS` fails on `openapi.json` or `route_precedence.json`: stop, report the diff, ask. Whole-suite gates, `make ruff`, sonar and browser checks belong to Stage 7.
- Stage only the task's files (never `git add -A`, `opencode.json.lock`, `skills-lock.json`, anything under `local/`); no push, no MR. Size hygiene: no tree copy or archive of BASE anywhere under `local/`; BASE text only via `git show` into single small files.
- Before Task 4 (first `include_router`) query context7 for FastAPI `include_router` (tags, prefix, dependencies merge); `COMPARE` proves it.

## Module map

BASE line numbers, decorators included. Every route module produces `router: APIRouter`; `__init__` consumes each. A symbol read by one module moves with it.

| Module | BASE lines | Other symbols |
|---|---|---|
| `_common` | 60-68, 76-123, 432-467, plus its own `logger` | `USERS_FILTER_DESC`, `PROJECTS_FILTER_DESC`, `CLIENT_SOURCE_FILTER_DESC`, `ERROR_MSG_ACCESS_DENIED`, `ERROR_MSG_ADMIN_HELP`, the two unreferenced constants, `handle_analytics_errors`, `_create_response`, `AnalyticsFilterParams`, `AnalyticsQueryParams` |
| `_spending_support` | 139-429 | `_SPENDING_METRIC_DEFINITIONS` ... `_authorize_admin_budget_view`, `_paginate_tabular` (10 symbols) |
| `usage` 13 / `activity` 11 | 470-866 / 869-1270 | |
| `cli` 10 / `cli_insights` 15 / `cli_insights_projects` 2 | 1273-1524 / 1527-1955 / 1958-2005 | |
| `cli_enriched_users` 5 | 2008-2115 | `_ENRICHMENT_DISABLED_MSG`, `_ENRICHED_USER_ROUTE_PARAMS`, `CLIEnrichedUserBaseParams`, `CLIEnrichedUserParams`, `_run_enriched_user_insight` |
| `spending` 7 | 2118-2507 | `_resolve_admin_budget_subject` (2212) |
| `leaderboard` 11 | 2510-2815 | `LEADERBOARD_*` (69-72), `_require_leaderboard_enabled` |
| `routing` 11 | 2818-3113 | `ROUTING_ANALYTICS_FEATURE` (73), `_ensure_routing_analytics_enabled` (126) |

Banner comments separated from their route by a blank line move with the block that follows them via `move.sh --lines` (current numbers via `rg -n`): BASE L552 (usage), L2410-2414 and L2447-2449 (spending), L2510 (leaderboard), L60 (`_common`).

Final `__init__.py` (Apache header, module docstring and `from __future__ import annotations` kept; no other code):

```python
from fastapi import APIRouter, Depends

from codemie.rest_api.routers.analytics import (
    activity, cli, cli_enriched_users, cli_insights, cli_insights_projects, leaderboard, routing, spending, usage,
)
from codemie.rest_api.security.authentication import authenticate

router = APIRouter(tags=["Dashboard Analytics"], prefix="/v1/analytics", dependencies=[Depends(authenticate)])
router.include_router(usage.router)  # then activity, cli, cli_insights, cli_insights_projects, cli_enriched_users, spending, leaderboard, routing

__all__ = ["router"]
```

## Review Focus

- Leaderboard dependency order (authenticate, access check, `_require_leaderboard_enabled`; CR-004 403 before 503): `COMPARE` (c) after every task from Task 5.
- A whole-name patch bound in the wrong module (`AnalyticsService`, `config`, `logger`, `_create_response`, `get_async_session`, `_authorize_admin_budget_view`) passes silently when its mock is never asserted: `PATCHUSE`, `MOVED`, `FLIP`.
- FastAPI 0.133.1 resolves string annotations in the handler module's globals: every annotation name (`User`, `datetime`, `ClientSource`, param classes, `JSONResponse`) must be module-level in each route module; `F821`, `IMPORT` and the guarded app build catch a miss.
- Routing (11 routes) has no test; the 59 data routes are tested for 200 only: `COMPARE` and `GUARDS` carry them.
- `cli_insights` (about 465), `spending` (455), `activity` (445), `usage` (440) are closest to the cap. Over 500: cut at a route boundary into one more route module named for the concern, included at the same position and recorded in the handoff. A cut that would reorder a route: stop and ask.

## Tools and checks

`WORK=local/router-split/step4` (gitignored via `local/*`, never staged). `PKG=src/codemie/rest_api/routers/analytics`. Run Python as `poetry run python`.

- Origin: copy-and-adapt the top-level `*.py` / `*.sh` of `local/router-split/step3/` (its `README.md` explains each; never copy `base_copy/`, `selftest*`, `*.json`, `base_index.py`), then adapt: `_lib.sh` (WORK, PKG, TASK_DIR); `routes_probe.py` (`INDEX_MODULE` to `codemie.rest_api.routers.analytics`; dump `analytics_routes` in `app.routes` order as `{pos, methods, path, name, status_code, tags, deps, module}` plus `analytics.router.prefix`, `.tags`, `.dependencies` names and all routes' `{path, methods}`); `compare.py` (rewritten, below); `modulemap.py` (the map above: route-module symbols derived with `ast` from the BASE line ranges in `$WORK/base_analytics.py` plus the listed extras; `--check`: every BASE top-level node except `router` and `logger` in exactly one module); `patchuse.py`, `astmove.py`, `moved.sh`, `leftover.sh`, `size.sh`, `import.sh`, `move.sh`, `move_symbols.py` (package name only; `astmove.py` also ignores `logger`, reads `$WORK/base_analytics.py` and checks every BASE comment-only line below the imports occurs in the package); `it.sh` becomes `at.sh`.
- `ROUTES <out>` / `COMPARE [--final] <after.json>` against `routes_before.json`, spec AC 2: (a) the 85 rows (methods, path, name, status_code, tags, deps) equal element-wise in order, `module` ignored; (b) same `pos` offsets (one contiguous block), same routes right before and after, every other route's (path, methods) equal; (c) router prefix, tags and dependencies (`['authenticate']`) equal, every row's `deps[0]` is `authenticate`, the 11 leaderboard rows read authenticate, access check (`admin_or_maintainer_or_auditor_access` x10, `admin_access_only` x1), `_require_leaderboard_enabled`, in that order; (d) `--final` only: no endpoint with `module == codemie.rest_api.routers.analytics`. It also prints routes per endpoint module: after Tasks 4-12 the remainder in `__init__` is 74, 63, 56, 51, 49, 34, 24, 13, 0.
- `AT` = `poetry run pytest -n 0 --show-capture=no -q` on the eight files: `tests/codemie/rest_api/routers/test_analytics.py`, `test_analytics_leaderboard.py`, `test_analytics_capability_guards.py`, `test_analytics_auditor.py`, `test_analytics_member_spending.py`, `test_analytics_budget_usage_labels.py`, `tests/unit/routers/test_analytics_enriched_user.py`, `tests/enterprise/litellm/test_premium_models_budget.py`. `GUARDS` = `poetry run pytest tests/codemie/rest_api/router_guards -n 0 --show-capture=no -q`. `IMPORT <module>` = fresh interpreter, `importlib.import_module`. `SIZE`: each module at most 500 lines (`wc -l`), Apache header present.
- `MOVED <module>`: no hit of `rg -nP "routers[./]analytics(?:\.|\s+import\s[^\n]*\b)(?:<symbols of module>)\b" src tests`, except the whole-name patch strings of helpers that inline code in `__init__` still resolves (`_create_response` until Task 5, `_authorize_admin_budget_view` until Task 6).
- `PATCHUSE`: for each `codemie.rest_api.routers.analytics.<sub>.<name>` literal and `from ...analytics.<sub> import <name>` in `tests/`, fail unless `<sub>` is a module and `<name>` (`<Name>` of `<Name>.<attr>`, so `asyncio` for `asyncio.to_thread`) is bound there and, for patch literals, loaded inside a function body.
- `LEFTOVER` = `rg -nP "routers[./]analytics\.(?!(?:router|usage|activity|cli|cli_insights|cli_insights_projects|cli_enriched_users|spending|leaderboard|routing|_common|_spending_support)\b)\w+|routers\.analytics import (?!router\b)" src tests .ai-run`, empty at the end (AC 4 stale-patch detector).
- `FLIP` (new `flip.py`, AC 4): for each whole-name literal `routers.analytics.<mod>.<Name>` in the eight files, point it at the first other module that binds `<Name>`, run the enclosing test (the file when the literal sits in a helper), restore the file in `finally`, and write `PASS` (silent: listed in the handoff), `FAIL` (caught) or `NO-SIBLING` (a wrong target raises AttributeError: safe) per string to `$WORK/flip-report.txt`. Run on a clean tree; `git diff --exit-code -- tests` afterwards.
- `ASTMOVE` (Task 13): each top-level def, async def, class and assignment of `$WORK/base_analytics.py` except `router` and `logger` occurs byte-identical (decorators included) in exactly one new module and nowhere else; the package adds no other top-level definition besides `router`, `logger` and `__all__`.

**Patch rules:** retarget a string only when the code that resolves the name on the exercised path moved in this task (until then `routers.analytics` still binds it). Whole-name patches go to the module whose handler uses the name (a private-module helper is patched on the using module, which imports it): `AnalyticsService` to the handler's module; `logger`: the two decorator tests to `_common`, the `get_summaries` test to `usage`; `_create_response` to `leaderboard`; `customer_config` to `cli_enriched_users`; `config` (6) to `leaderboard`; `asyncio.to_thread` (9, tail kept), `get_async_session` (3), `_authorize_admin_budget_view` (1) to `spending`. Imports take the defining submodule: `_create_response`, `handle_analytics_errors`, `AnalyticsQueryParams` from `_common`; `_authorize_admin_budget_view` from `_spending_support`; handlers from their module; `router` imports stay. Append each `file:line: old -> new` to `$WORK/patch-map.txt`.

**Procedure (Tasks 2-12):** (1) Retarget the task's tests (a mixed import is split into two lines) and run `AT` (or `IMPORT` when untested): red with ModuleNotFoundError. (2) Create the module: header, `from __future__`, a copy of the `__init__` import block, `router = APIRouter()` (route modules), then `move.sh <module> <symbols from modulemap.py>`; add the import and the `include_router` line (bottom of `__init__`, above the earlier ones) for route modules; prune with `ruff check --select F401 --fix --unsafe-fixes` on both files and add names reported by `--select F821` (shared ones from the private modules; `logger` defined locally). (3) `MOVED`, `PATCHUSE`. (4) `IMPORT` the module, `SIZE`, `ruff check` and `ruff format --check` on touched files, `AT`, `ROUTES` + `COMPARE`, full `GUARDS` with `git status` showing no baseline change; `git add` the task's files and confirm `git diff --cached --color-moved=dimmed-zebra` shows only moved blocks, imports, headers, `APIRouter()`, includes and `logger`. (5) Commit.

---

### Task 1: Tools, before-dump, package skeleton

**Files:** `git mv src/codemie/rest_api/routers/analytics.py src/codemie/rest_api/routers/analytics/__init__.py` (create the directory first); scratch tools in `$WORK`, uncommitted.

Test-first: no — rename only, bytes unchanged; proof: `git diff --cached -M --stat` shows a 100% rename, `AT`, `GUARDS` and `COMPARE` stay green.

- [ ] Setup: `git check-ignore $WORK`; `git rev-parse c810715933 > $WORK/BASE`; `git diff --exit-code c810715933 HEAD -- src tests` empty; `git show c810715933:src/codemie/rest_api/routers/analytics.py > $WORK/base_analytics.py`; `git grep -n 'routers.analytics' c810715933 -- tests > $WORK/base_test_refs.txt`.
- [ ] Tools: copy-and-adapt per Tools and checks, write `flip.py`; self-check with `modulemap.py --check` and `move.sh --dry-run` for every module.
- [ ] Baseline: `ROUTES $WORK/routes_before.json` before the move (85 routes, prefix `/v1/analytics`, tags `['Dashboard Analytics']`, dependencies `['authenticate']`); keep a `routes_before.orig.json` copy and its sha256; record `AT` and `GUARDS` pass counts in `$WORK/baseline.md`.
- [ ] `git mv`, then `AT`, `GUARDS`, `ROUTES` + `COMPARE` (all 85 endpoints still in `__init__`); commit the rename only.

### Task 2: `_common`

**Files:** Create `analytics/_common.py`; modify `__init__.py` (import the moved names from `._common`); tests: `test_analytics.py` (the `_create_response, handle_analytics_errors` import, the `AnalyticsQueryParams` imports split off the handler imports, the two decorator `logger` patches).

Test-first: yes — the retargeted imports and patches fail with ModuleNotFoundError; after: `IMPORT ..._common` passes, `AT` green.

- [ ] Procedure, without `router` or `include_router`. `logger` stays in `__init__` (inline code still reads it); `_common` defines its own.

### Task 3: `_spending_support`

**Files:** Create `analytics/_spending_support.py` (BASE 139-429, no `router`); modify `__init__.py` (inline spending code imports what it uses from it); tests: `_authorize_admin_budget_view` imports in `test_analytics.py` (6) and `test_analytics_auditor.py` (2). The `capability_guards` patch of the same name stays until Task 6.

Test-first: yes — as Task 2; after: `AT` green.

- [ ] Procedure.

### Task 4: `routing`

**Files:** Create `analytics/routing.py`; modify `__init__.py` (first `include_router` line). Read context7 first.

Test-first: yes — `IMPORT ...analytics.routing` fails (ModuleNotFoundError); the 11 routes have no test, so `COMPARE` (a)-(c) and `GUARDS` are the proof.

- [ ] Procedure; `routing` is the only reader of `fastapi.Path`; the feature constant and gate move with it.

### Task 5: `leaderboard`

**Files:** Create `analytics/leaderboard.py` (`--lines` for the L2510 banner); modify `__init__.py`; tests: `test_analytics_leaderboard.py` (3 `AnalyticsService`, 5 `config`), `test_analytics_auditor.py` (`AnalyticsService`, `_create_response`, `get_leaderboard_user_detail` import), `test_analytics_capability_guards.py` (`config`).

Test-first: yes — retargeted strings fail with ModuleNotFoundError; after: `AT` green, 403 before 503 holds.

- [ ] Procedure. `config` collapse: every test keeps its single `config` patch, now `leaderboard.config`; `_common.config` stays unpatched (it only picks cache headers; the one 200 test asserts status and the service call). Do not add a second patch or change a test signature; if a test asserts a `Cache-Control` header, stop and ask.

### Task 6: `spending`

**Files:** Create `analytics/spending.py` (`--lines` for the L2410-2414 and L2447-2449 banners); modify `__init__.py`; tests: `test_analytics.py` (`get_user_spending`, `get_user_budget_usage` imports, 4 `asyncio.to_thread`), `test_analytics_member_spending.py` (18 imports, `asyncio.to_thread`, `get_async_session`), `test_analytics_budget_usage_labels.py` (2 imports), `test_analytics_capability_guards.py` (`get_async_session` x2, `_authorize_admin_budget_view`), `tests/enterprise/litellm/test_premium_models_budget.py` (4 `asyncio.to_thread`, 4 `get_user_spending` imports).

Test-first: yes — as Task 5; `spending` imports its helpers from `_spending_support`; after: `AT` green.

- [ ] Procedure; `_resolve_admin_budget_subject` has no decorator and moves by name. Size near the cap: see Review Focus.

### Task 7: `cli_enriched_users`

**Files:** Create `analytics/cli_enriched_users.py`; modify `__init__.py`; tests: `tests/unit/routers/test_analytics_enriched_user.py` (5-name import block, `customer_config`, `AnalyticsService`).

Test-first: yes — as Task 5.

- [ ] Procedure; the two classes and `_run_enriched_user_insight` move with the block.

### Task 8: `cli_insights_projects`

**Files:** Create `analytics/cli_insights_projects.py`; modify `__init__.py`.

Test-first: yes — `IMPORT` fails (ModuleNotFoundError); untested routes, so `COMPARE` and `GUARDS` are the proof.

- [ ] Procedure.

### Task 9: `cli_insights`

**Files:** Create `analytics/cli_insights.py`; modify `__init__.py`; tests: `test_analytics.py` (`get_cli_insights_weekday_pattern`, `_top_spenders`, `_user_detail`: 3 `AnalyticsService`, 3 imports).

Test-first: yes — as Task 5; `SIZE` at most 500, else the Review Focus fallback.

- [ ] Procedure.

### Task 10: `cli`

**Files:** Create `analytics/cli.py`; modify `__init__.py`; tests: `test_analytics.py` (`get_cli_summary`, `get_cli_agents`: 2 `AnalyticsService`, 2 imports).

Test-first: yes — as Task 5.

- [ ] Procedure.

### Task 11: `activity`

**Files:** Create `analytics/activity.py`; modify `__init__.py`; tests: `test_analytics.py` (`get_llms_usage`, `get_users_list` x2, `get_projects_unique_daily`: 8 `AnalyticsService`, 9 imports).

Test-first: yes — as Task 5; the four spending/limit routes stay here, so no route is reordered.

- [ ] Procedure; `logger` is defined locally.

### Task 12: `usage` and the final `__init__`

**Files:** Create `analytics/usage.py` (`--lines` for the L552 banner); reduce `__init__.py` to the shape above (delete `logger` and unused imports by hand); tests: `test_analytics.py` (the remaining `AnalyticsService` strings and handler imports: `get_summaries`, assistants-chats, workflows, tools, agents; the `get_summaries` `logger` patch).

Test-first: yes — as Task 5; after: `LEFTOVER` empty, `rg -n '^(def|class|async def|[A-Za-z_]+ *=)' $PKG/__init__.py` shows only `router` and `__all__`, `COMPARE --final` green.

- [ ] Procedure; `usage` is now the first include.

### Task 13: Final proof and handoff

**Files:** none; nothing to commit (a working-tree diff is a bug).

Test-first: no — proof only, no code change.

- [ ] **Step 1: Proof (AC 1-6).** `analytics.py` gone; 12 `.py` files, `SIZE` OK, `rg -L 'Licensed under the Apache License' $PKG` empty; `ROUTES` + `COMPARE --final`; `ASTMOVE` with a negative control (one body byte changed in a scratch copy of one module must fail), `git diff --color-moved=dimmed-zebra c810715933 -- src` shows only moved blocks; `LEFTOVER` empty, `PATCHUSE` OK, every line of `base_test_refs.txt` is on the left of `patch-map.txt` or a `router` import; `FLIP` run and `flip-report.txt` written. AC 5: `rg -nUP "routers\.analytics\.(?:usage|activity|cli\w*|spending|leaderboard|routing) import (?:[^\n(]*|\([^)]*)\b_[a-z]\w*" src`, `rg -n "routers\.analytics import|analytics\.(usage|activity|cli|spending|leaderboard|routing)" $PKG/_common.py $PKG/_spending_support.py` and `rg 'routers\.analytics' src/codemie/service src/codemie/rest_api/handlers` all empty; `rg -n 'routers/analytics\.py' . -g '!docs/superpowers' -g '!local'` empty. `rg -c '^class '` on the package totals 4. `IMPORT` for all 12 modules, one interpreter each. `git diff --exit-code c810715933 -- src/codemie/rest_api/main.py src/codemie/rest_api/routers/cli_analytics.py tests/codemie/rest_api/router_guards` is empty: `openapi.json` and `route_precedence.json` byte-identical, nothing regenerated.
- [ ] **Step 2: Suites.** `GUARDS`; `AT`; `poetry run pytest tests/codemie/rest_api/routers tests/unit/routers tests/enterprise/litellm -q`.
- [ ] **Step 3: Handoff and cleanup.** In `local/router-split/HANDOFF.md` (gitignored, never staged) record step 4 done, `patch-map.txt`, the scripts, the before/after `ROUTES` + `COMPARE` output, the `PASS` and `NO-SIBLING` rows of `flip-report.txt` (expected `PASS`: the `_authorize_admin_budget_view` and `get_async_session` sites in `capability_guards`), any fallback module, the spec's non-goal MR notes (pre-existing behavior left as is), and that the eventual MR must ask a reviewer to post `/sanity` and carry a `## Test harness` block, listing the moved auth code of spec AC 8. Then delete scratch artifacts: `routes_*.json`, `base_analytics.py`, `base_test_refs.txt`, scratch module copies, `__pycache__`, `<task dir>/test.local.log`; keep scripts, `README.md`, `patch-map.txt`, `flip-report.txt`, `baseline.md`. `find local/router-split/step4 -size +200k` is empty and `git status --porcelain` lists only the two lock files (and untracked planning docs, if not yet committed).

## Negative-constraint pass

- No route moved across concerns or reordered (the four spending/limit routes stay in `activity`; no cut that reorders): Tasks 4-12 `COMPARE` (a)-(b); Review Focus fallback.
- No re-export, `__init__` holds only `router` and `__all__`: Task 12 shape check, `LEFTOVER`, `ASTMOVE`.
- No underscore import from a router-defining module, private modules never import route modules, `_spending_support` imports `_common` only: Task 13 Step 1 `rg` checks.
- Bare `APIRouter()` and decorator-level leaderboard dependencies kept, auth not touched: `COMPARE` (c) per task; color-moved review.
- No behavior change, rename, class moved to `models/`, dead-code deletion, handler shortening or fix of the listed pre-existing items: color-moved review per task; `ASTMOVE`; class count 4.
- No `main.py`, `cli_analytics.py`, guard-code or baseline change, no regeneration, no new tests, no test-file split: Task 13 `git diff --exit-code`; `GUARDS` per task.
- No second `config` patch for leaderboard tests, no gate moved into `_common`: Task 5 note.
- No whole-suite gates, `make ruff`, sonar, manual or browser verification, no BASE tree copy or archive, no staged scratch or lock files, no push or MR: none planned; Task 13 `find` and `git status` checks.
