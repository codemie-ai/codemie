# EPMCDME-15651, step 3: index router package

## Purpose

Replace `routers/index.py` (2,884 lines, 45 routes) with a package, no API change. Move-only, reviewable with `git diff --color-moved`. Same branch; no MR until the ticket is done. Rules follow the step-2 spec as amended.

## Scope

`routers/index/` replaces the file (delete it). Bodies, decorators, docstrings, function-local imports and the two router-local classes move byte-identical, in source order within each module. Cap: 500 lines per module.

Route modules, in include order (routes): `queries` (the 11 `/index` routes, then `POST /index/health`, `GET /index/jira/fields`: 13), `code_repositories` (5), `kb_atlassian` (6), `kb_azure_devops` (4), `kb_google` (3), `kb_sharepoint` (2), `kb_xwiki` (2), `kb_git_faq` (3), `kb_files` (2, PUT before POST as now), `providers` (5). A helper with one user moves with it. `health` and `jira/fields` go to `queries` because `kb_atlassian` with them would reach about 500 lines.

Private modules: `_common` (the 5 cross-module helpers `_update_datasource_scheduler`, `_index_unique_check`, `_kb_demo_user_check`, `_validate_git_credentials`, `_validate_project_change`; the 7 shared message constants and those only they read; the unreferenced `PROJECT_CHANGE_LOG_MESSAGE`, verbatim); `_repository_support` (the two field maps, the four code-repository helpers, `SVN_REPOSITORY_NOT_FOUND_MESSAGE`), because the code-repository block alone is 562 lines.

## Constraints

- `__init__.py` holds `router = APIRouter(tags=["Indexing"], prefix="/v1", dependencies=[Depends(authenticate)])`, one `include_router` per route module, `__all__ = ["router"]`, nothing else. Route modules use a bare `APIRouter()`; the 5 `application_access_check` and 2 `admin_access_only` route dependencies stay in their decorators.
- Helpers keep their names. In `src/`, underscore names are imported only from private modules, never from a module that defines a `router`; private modules never import route modules (a class they need is defined there; OpenAPI keys do not change).
- `main.py` and `test_main_retrieval_backend_disabled.py` are unchanged: the multi-line `index` import resolves to the package and `index.router` is the combined router. `.ai-run/guides/integration/faq-git-integration.md:70` cites `routers/index/kb_git_faq.py`.
- Tests stay in their files. Six reference `routers.index` (171 patch strings, 25 imports): the five in the analysis plus `models/test_index_info.py` (2 strings). Patch the module whose code resolves the name on the exercised path; one old string may become several (the 14 `SettingsService.get_git_creds` / `GitBatchLoader.test_public_access` strings go to `_common`, where `_validate_git_credentials` resolves them, although the handler is in `code_repositories`). Whole-name patches bind to the using module; `<Name>.<attr>` patches keep their tail. Imports take the defining submodule (tests may import underscore names from it). `test_xwiki_index_endpoints.py` uses `inspect.getsource` on three handlers, so bodies stay verbatim.

## Route order (restates step-0 criterion 4 for this package)

The 22 knowledge-base routes interleave datasource types in source order and the ticket prescribes per-type modules, so today's sequence cannot be kept (no module could hold the 1,439-line block). Checked instead is everything order can change (AC 2). Routes keep source order inside a module, so same-path method order, which the 405 `Allow` header shows, is preserved.

Check: a one-off dump script in the plan, not committed (the guards' dump holds only path and methods). It copies `router_guards/app_probe.py` and, for every route whose endpoint module is under `codemie.rest_api.routers.index`, prints (methods, path, name, status code, tags, call names from `route.dependant.dependencies`) in `app.routes` order, plus `index.router.dependencies`. Run at the pre-move HEAD and after the last move.

## OpenAPI

Hard criterion: `openapi.json` and `route_precedence.json` byte-identical (`git diff` empty), nothing regenerated. `CreateIndexRequest` (L7403) and `CreateSVNIndexRequest` (L7658) have plain unique keys; a key follows `__module__` only on a collision. If the snapshot fails: stop, report the diff, ask; never run `regenerate_baselines`.

## Acceptance criteria

1. `index.py` is gone; the package holds `__init__.py`, the 10 route modules, `_common` and `_repository_support`, each at most 500 lines (`wc -l`) with the Apache header.
2. Dump before and after: (a) equal as multisets (45 routes, dependency call names in order); (b) equal method sequence for every path; (c) `overlapping_pairs` (guards' matcher) over the index routes gives the same pairs in the same order, including the 2 baseline pairs; (d) one contiguous block with the same neighbors; (e) `index.router.dependencies == [authenticate]` and every route's list starts with it.
3. `git diff --color-moved` shows only moved blocks, imports, headers, `APIRouter()`, `include_router` and `__all__`; an AST check finds every top-level def and class except `router` byte-identical in one new module.
4. No `routers.index.<name>` in `src/` or `tests/` unless `<name>` is `router` or a real submodule (catches stale `Ability`, `_index_unique_check`); each `routers.index.<module>.<name>` target imports and its module reads `<name>` in a function body. Script and old-to-new map go in the handoff.
5. No `src/` module imports an underscore name from a module that defines a `router`; nothing under `service/` or `rest_api/handlers/` imports `routers/index` (none today).
6. Each of the 13 modules imports alone in a fresh interpreter; the guarded app builds hermetically; guards pass with both baselines unchanged.
7. `make ruff` passes (F401 in `__init__.py`, complexity 16, license check). Tests pass in the six files, `tests/codemie/rest_api/routers`, `tests/unit/routers` and `router_guards`.
8. The handoff says the eventual MR must ask a reviewer for `/sanity`. Auth code that moves: `_validate_project_change`, `_kb_demo_user_check`, `_validate_git_credentials`, `_parse_jwt_exp` (unverified JWT decode), the router-level `authenticate`, the 7 access dependencies, inline `Ability(user).can` checks in 10 handlers.

## Non-goals

- Moving the two classes to `rest_api/models/`; renaming anything; changing paths, tags, operationIds or response models.
- Merging `_parse_jwt_exp` with `jwt_utils.parse_jwt_exp`, deleting `PROJECT_CHANGE_LOG_MESSAGE`, shortening handlers, moving helpers into `service/` (step 7).
- Adding a per-resource check to `get_assistants_using_index` (none today) or any behavior change; new tests; splitting test files.
- `main.py`, steps 4 to 6, guard code, baselines; commits or MR beyond the flow's.

## Open risks

- 25 of 45 handlers have no direct test; only AC 2, 3 and 6 guard them.
- AC 4 is necessary, not sufficient: a whole-name patch aimed at a module that holds the name but is off the exercised path stays silent if its mock is never asserted.
- Line counts are estimates; `kb_atlassian` (about 470) and `queries` (about 440) are closest to the cap. Fallback: one more route module named for the datasource, recorded.
- Counts come from reading plus targeted grep, which already found a sixth test file: AC 4 and 5 are the net.
