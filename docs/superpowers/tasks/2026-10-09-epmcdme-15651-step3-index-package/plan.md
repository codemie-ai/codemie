# EPMCDME-15651 Step 3: Index Router Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `routers/index.py` (2,884 lines, 45 routes) with the package `routers/index/` (`__init__`, 10 route modules, `_common`, `_repository_support`, each at most 500 lines); retarget six test files and one guide row.

**Architecture:** Move-only, block by block. Task 1 `git mv`s the file to `index/__init__.py` unchanged; Task 2 extracts `_common`; Tasks 3-12 extract route modules in reverse include order (providers first, queries last), each adding its `include_router` line above the earlier ones, so the final order is the spec's. Inline routes still in `__init__` register first, extracted ones after, so the package imports, `main.py`'s multi-line `from codemie.rest_api.routers import (..., index, ...)` resolves, and `GUARDS` is green at every commit: no atomic switch. Knowledge-base routes interleave types in source order, so route order changes by design and `COMPARE` (spec AC 2) replaces a literal diff. Commit per task using the repository's existing convention (`EPMCDME-15651: Capital description`).

**Tech Stack:** FastAPI `APIRouter`, pytest with `mock.patch`, ruff, Python `ast` for the scratch tools.

**Spec:** `docs/superpowers/tasks/2026-10-09-epmcdme-15651-step3-index-package/spec.md` (research: `technical-analysis.md`). `BASE` = HEAD before Task 1, saved to `$WORK/BASE`.

## Global Constraints

- Bodies, decorators, docstrings, function-local imports and the two classes byte-identical, source order kept per module; only imports, headers, `APIRouter()`, includes and `__all__` change. Text is moved by script, never retyped. New `.py` files carry the Apache header.
- Route modules use a bare `router = APIRouter()`; `__init__` keeps `router = APIRouter(tags=["Indexing"], prefix="/v1", dependencies=[Depends(authenticate)])` verbatim; the 5 `application_access_check` and 2 `admin_access_only` dependencies stay in their decorators. No re-exports.
- In `src/`, underscore names are imported only from `_common` / `_repository_support`; private modules never import route modules or `__init__`.
- No `main.py`, guard-code, baseline or behavior edits, no new tests, never `regenerate_baselines`. If `GUARDS` fails on `openapi.json` or `route_precedence.json`: stop, report the diff, ask. Whole-suite gates, `make ruff`, sonar and browser checks belong to Stage 7. Stage only the task's files (never `git add -A`, `opencode.json.lock`, `skills-lock.json`); no push, no MR.
- Before Task 3 (first `include_router`) query context7 for FastAPI `include_router` (tags, prefix, dependencies merge); `COMPARE` proves it.

## Module map

`BASE` line numbers guide. Interfaces: every route module produces `router: APIRouter` and consumes the `_common` / `_repository_support` names below; `__init__` consumes each `router`. A symbol read by one route module moves with it.

| Module | Routes | Other symbols |
|---|---|---|
| `_common` | | `_update_datasource_scheduler`, `_index_unique_check`, `_kb_demo_user_check`, `_validate_git_credentials`, `_validate_project_change`; shared constants (`INDEX_NOT_FOUND_*`, `APPLICATION_NOT_FOUND_*`, `INVALID_INPUT_PARAMETERS_HELP`, `INCORRECT_DATASOURCE_SETUP_MESSAGE`, `EDIT_SUCCESSFUL`), `INDEX_EXISTS_*`, `PROJECT_CHANGE_LOG_MESSAGE` (unreferenced, verbatim) |
| `_repository_support` | | `_REPO_FIELD_MAP`, `_INDEX_FIELD_MAP`, `_update_svn_index_and_repo_fields`, `_handle_index_and_repository_update`, `_get_repository_with_project_change`, `_handle_svn_reindex`, `SVN_REPOSITORY_NOT_FOUND_MESSAGE` |
| `queries` | 13: L195-454, `POST /index/health` 2621, `GET /index/jira/fields` 2637 | `_get_index_by_id_or_raise`, `_get_elasticsearch_stats`, `_validate_remote_entities_and_raise` |
| `code_repositories` | 5: L479-1018 | `CreateIndexRequest`, `CreateSVNIndexRequest` |
| `kb_atlassian` / `kb_azure_devops` | 6 / 4: confluence, jira, xray / wiki, work_item, POST + PUT | |
| `kb_google` / `kb_xwiki` / `kb_git_faq` | 3 / 2 / 3 | git_faq: `_GIT_FAQ_SOURCE_FIELDS`, `_build_git_faq_processor_from_index`, `_git_faq_update_params` |
| `kb_sharepoint` | 2 | `_parse_jwt_exp`, `_get_sharepoint_creds_for_reindex`, `_create_sharepoint_processor_for_reindex` |
| `kb_files` / `providers` | 2 (PUT 1688 before POST 2415) / 5: L2462-2618 | |

Final `__init__.py` (after the header):

```python
from fastapi import APIRouter, Depends

from codemie.rest_api.routers.index import (
    code_repositories, kb_atlassian, kb_azure_devops, kb_files, kb_git_faq, kb_google, kb_sharepoint, kb_xwiki, providers, queries,
)
from codemie.rest_api.security.authentication import authenticate

router = APIRouter(tags=["Indexing"], prefix="/v1", dependencies=[Depends(authenticate)])
router.include_router(queries.router)  # then code_repositories, kb_atlassian, kb_azure_devops, kb_google, kb_sharepoint, kb_xwiki, kb_git_faq, kb_files, providers

__all__ = ["router"]
```

## Review Focus

- A whole-name patch bound in the wrong module (`Ability`, `_index_unique_check`, `_kb_demo_user_check`, processors, `ensure_application_exists`) passes silently when its mock is never asserted: `PATCHUSE`, `MOVED`, and reading which handler the test drives.
- 19 handlers rely on the router-level `authenticate` alone: `COMPARE` (e) after every task.
- The `jira/fields` tests stay on `routers.index` through Task 11 and move in Task 12.
- `kb_atlassian` (about 470) and `queries` (about 440) are closest to the cap; over 500: one more route module named for the datasource (e.g. `kb_jira`), recorded in the handoff.

## Tools and checks

`WORK=local/router-split/step3` (gitignored via `local/*`, never staged). Run Python as `PYTHONPATH=. poetry run python`.

- `ROUTES <out>`: copy of `router_guards/app_probe.py` whose `main` dumps, in `app.routes` order, every route whose `endpoint.__module__` starts with `codemie.rest_api.routers.index` as `{methods, path, name, status_code, tags, deps: [d.call.__name__ ...]}`, plus all routes' `{path, methods}` and `index.router.dependencies` names. Launched as `guarded_app._run_probe` does. Re-capturing BASE later needs `git worktree add $WORK/base BASE` with `PYTHONPATH=$WORK/base/src` (confirm `codemie.__file__`).
- `COMPARE <after.json>` against `routes_before.json`: spec AC 2 (a)-(e), with (c) via `route_overlap.overlapping_pairs`.
- `move_symbols.py <module> <symbol>...`: with `ast`, cut the named top-level nodes (decorators, directly preceding comments, assignments) from `index/__init__.py` and append them in source order to the module.
- `PATCHUSE`: for each `codemie.rest_api.routers.index.<sub>.<name>` literal in `tests/`, fail unless `<sub>` is a module and `<name>` (`<Name>` of `<Name>.<attr>`) is bound there and loaded inside a function body; the f-string base at `test_xwiki_index_endpoints.py:108` is checked by hand.
- `ASTMOVE` (Task 13): each top-level def, class and assignment of `git show BASE:src/codemie/rest_api/routers/index.py` except `router` occurs byte-identical in exactly one new module and nowhere else.
- `GUARDS` = `poetry run pytest tests/codemie/rest_api/router_guards -n 0 --show-capture=no -q`. `IT` = `poetry run pytest -q` on `tests/codemie/rest_api/routers/test_index.py`, `test_index_git_validation.py`, `test_index_git_faq.py`, `tests/codemie/rest_api/test_xwiki_index_endpoints.py`, `test_main_retrieval_backend_disabled.py`, `tests/codemie/rest_api/models/test_index_info.py`. `IMPORT <module>` = `poetry run python -c "import importlib,sys; importlib.import_module(sys.argv[1])" <module>`.
- `MOVED <a|b|...>` = `rg -nP "routers[./]index\.(?:<a|b|...>)\b" src tests`, empty; names are the symbols the task moved plus imports it pruned from `__init__`.
- `LEFTOVER` = `rg -nP "routers[./]index\.(?!(?:router|queries|code_repositories|kb_atlassian|kb_azure_devops|kb_google|kb_sharepoint|kb_xwiki|kb_git_faq|kb_files|providers|_common|_repository_support)\b)\w+|routers\.index import (?!router\b)" src tests`, empty at the end.

**Patch rules:** retarget a string only when the code that resolves the name on the exercised path moved in this task (until then `routers.index` still binds it). Whole-name patches go to the module whose handler uses the name (a `_common` helper is patched on the using module, which imports it); `<Name>.<attr>` patches keep their tail and take the module that reads `<Name>`; imports take the defining submodule; the 14 `SettingsService.get_git_creds` / `GitBatchLoader.test_public_access` strings go to `_common`. Append each `old -> new` to `$WORK/patch-map.txt`.

**Procedure (Tasks 2-12):** (1) retarget the task's tests and run `IT` (or `IMPORT` when untested): red with ModuleNotFoundError. (2) Create the module: header, copy of the `__init__` import block, `move_symbols.py`, `router = APIRouter()` (route modules); add the import and the `include_router` line (bottom, above earlier ones) to `__init__`; prune with `ruff check --select F401 --fix --unsafe-fixes`, add undefined names with `--select F821` (shared ones from `_common`). (3) `MOVED`, `PATCHUSE`. (4) `IMPORT` the module, `wc -l` at most 500, `ruff check` and `ruff format --check` on touched files, `IT`, `ROUTES` + `COMPARE`, full `GUARDS` with `git status` showing no baseline change; `git add` the task's files and confirm `git diff --cached --color-moved=dimmed-zebra` shows only moved blocks, imports, headers, `APIRouter()`, includes. (5) Commit.

---

### Task 1: Tools, before-dump, package skeleton

**Files:** `git mv src/codemie/rest_api/routers/index.py src/codemie/rest_api/routers/index/__init__.py` (create the directory first); scratch tools in `$WORK`, uncommitted.

Test-first: no — rename only, bytes unchanged; proof: `git diff --cached -M --stat` shows a 100% rename, `IT`, `GUARDS` and `COMPARE` stay green.

- [ ] Save `BASE`, write the tools, run `ROUTES $WORK/routes_before.json` before the move (45 routes, router dependencies `[authenticate]`), `git mv`, then `IT`, `GUARDS`, `ROUTES` + `COMPARE`; commit the rename only.

### Task 2: `_common`

**Files:** Create `index/_common.py`; modify `__init__.py` (import the moved names from `._common`); tests: `test_index.py` (`_update_datasource_scheduler` imports x2), `test_xwiki_index_endpoints.py` (`INCORRECT_DATASOURCE_SETUP_MESSAGE`) to `_common`.

Test-first: yes — the retargeted imports fail with ModuleNotFoundError; after: `IMPORT ..._common` passes, `IT` green.

- [ ] Procedure, without `router` or `include_router`. Whole-name helper patches stay on `routers.index` (inline handlers still bind them).

### Task 3: `providers`

**Files:** Create `index/providers.py`; modify `__init__.py` (first `include_router` line, bottom). Read context7 first.

Test-first: yes — `IMPORT ...index.providers` fails (ModuleNotFoundError); no test covers these routes, so `COMPARE` (admin dependencies on `datasources/import*`) and `GUARDS` are the proof.

- [ ] Procedure.

### Task 4: `kb_files`

**Files:** Create `index/kb_files.py`; modify `__init__.py`; `test_index.py` (20 strings: `_kb_demo_user_check`, `_index_unique_check`, `FileRepositoryFactory`, `FileDatasourceProcessor`, `FileDatasourceService`, `UpdateFileDatasourceUseCase`).

Test-first: yes — retargeted strings fail with ModuleNotFoundError; after: `IT` green.

- [ ] Procedure; PUT stays before POST.

### Task 5: `kb_git_faq`

**Files:** Create `index/kb_git_faq.py`; modify `__init__.py`; `test_index_git_faq.py` (20 strings, `_build_git_faq_processor_from_index` import); `.ai-run/guides/integration/faq-git-integration.md:70` (cite `src/codemie/rest_api/routers/index/kb_git_faq.py`).

Test-first: yes — as Task 4; after: `rg -n 'routers/index\.py' .ai-run` empty.

- [ ] Procedure.

### Task 6: `kb_xwiki`

**Files:** Create `index/kb_xwiki.py`; modify `__init__.py`; `test_xwiki_index_endpoints.py` (f-string base L108, `index_knowledge_base_xwiki` and `update_knowledge_base_xwiki` imports; `router` stays); `test_index.py` (xwiki strings).

Test-first: yes — as Task 4; the `inspect.getsource` assertions pass only if bodies are verbatim.

- [ ] Procedure.

### Task 7: `kb_sharepoint`

**Files:** Create `index/kb_sharepoint.py`; modify `__init__.py`; `test_index.py` (`_parse_jwt_exp` imports x4).

Test-first: yes — as Task 4.

- [ ] Procedure.

### Task 8: `kb_google`

**Files:** Create `index/kb_google.py`; modify `__init__.py`.

Test-first: yes — `IMPORT ...index.kb_google` fails (ModuleNotFoundError); untested routes, so `COMPARE` and `GUARDS` are the proof.

- [ ] Procedure.

### Task 9: `kb_azure_devops`

**Files:** Create `index/kb_azure_devops.py`; modify `__init__.py`; `test_index.py` (15 strings).

Test-first: yes — as Task 4.

- [ ] Procedure.

### Task 10: `kb_atlassian`

**Files:** Create `index/kb_atlassian.py`; modify `__init__.py`; `test_index.py` (confluence, jira, xray strings; leave the two `jira/fields` tests, which drive `get_jira_datasource_fields`).

Test-first: yes — as Task 4; `wc -l` at most 500, else the Review Focus fallback.

- [ ] Procedure.

### Task 11: `code_repositories` and `_repository_support`

**Files:** Create both; modify `__init__.py`; `test_index_git_validation.py` (56 strings; `CreateIndexRequest`, `create_index_application`, `update_index_application` imports to `code_repositories`); `test_index.py` (12 strings; `_INDEX_FIELD_MAP` import to `_repository_support`); the 14 git-credential strings to `_common`.

Test-first: yes — as Task 4; `Ability` (x5), `ensure_application_exists` (x4) and both `*_in_background` patches bind to `code_repositories`.

- [ ] Procedure; `_repository_support` has no `router`.

### Task 12: `queries` and the final `__init__`

**Files:** Create `index/queries.py`; reduce `__init__.py` to the shape above (`rg -n '^(def|class|async def|[A-Za-z_]+ *=)'` on it shows only `router`); tests: `test_index.py` (queries strings, `_get_elasticsearch_stats` x2, the `jira/fields` pair), `models/test_index_info.py` (2 strings to `queries.IndexStatusService.get_index_info_list`).

Test-first: yes — as Task 4; after: `LEFTOVER` empty, `IT` green.

- [ ] Procedure; `queries` is now the first include (`COMPARE` (d)).

### Task 13: Final proof and handoff

**Files:** none; nothing to commit (a working-tree diff is a bug).

Test-first: no — proof only, no code change.

- [ ] **Step 1: Proof (AC 1-6).** 13 `.py` files, each at most 500 lines, all with `Licensed under the Apache License` (`rg -L` empty), `routers/index.py` gone; `ROUTES` + `COMPARE`; `ASTMOVE` and `git diff --color-moved=dimmed-zebra BASE -- src` show only moved blocks; `LEFTOVER`, `PATCHUSE` empty and every `git grep -n 'routers.index' BASE -- tests` line is on the left of `patch-map.txt`. AC 5: `rg -nUP "routers\.index\.(?:queries|code_repositories|kb_\w+|providers) import (?:[^\n(]*|\([^)]*)\b_[a-z]\w*" src`, `rg -n "routers\.index import|index\.(queries|code_repositories|kb_|providers)" src/codemie/rest_api/routers/index/_*.py` and `rg 'routers\.index' src/codemie/service src/codemie/rest_api/handlers` all empty. `rg -c '^class '` on the package totals 2; `rg -n PROJECT_CHANGE_LOG_MESSAGE src` has one hit. `IMPORT` for all 13 modules, one interpreter each. `git diff --exit-code BASE -- src/codemie/rest_api/main.py tests/codemie/rest_api/router_guards tests/codemie/rest_api/test_main_retrieval_backend_disabled.py` is empty: both baselines byte-identical, nothing regenerated.
- [ ] **Step 2: Suites.** `GUARDS`; `IT`; `poetry run pytest tests/codemie/rest_api/routers tests/unit/routers -q`.
- [ ] **Step 3: Handoff.** In `local/router-split/HANDOFF.md` (gitignored, never staged) record step 3 done, `patch-map.txt`, the scripts and the before/after `ROUTES` + `COMPARE` output, any fallback, the changed KB route order, and that the eventual MR must ask a reviewer to post `/sanity` (moved auth code: `_validate_project_change`, `_kb_demo_user_check`, `_validate_git_credentials`, `_parse_jwt_exp` with its unverified JWT decode, router-level `authenticate`, the 7 access dependencies, inline `Ability(user).can` checks in 10 handlers).

## Negative-constraint pass

- No re-exports, init holds only `router`: Task 12 shape; `LEFTOVER`.
- No underscore import from a router-defining module, private modules never import route modules: Task 13 Step 1 `rg` checks.
- Bare `APIRouter()` and decorator-level dependencies kept, no auth added to `get_assistants_using_index`: `COMPARE` (e) per task.
- No behavior change, rename, class moved to `models/`, `_parse_jwt_exp` merge or `PROJECT_CHANGE_LOG_MESSAGE` deletion: color-moved review per task; `ASTMOVE`; class count 2.
- No `main.py`, guard-code, baseline change or regeneration, no new tests, no test-file split: Task 13 `git diff --exit-code BASE`; `GUARDS` per task.
- No whole-suite gates, `make ruff`, sonar, manual or browser verification, push or MR: none planned.
