# EPMCDME-15651, step 2: assistant router package

## Purpose

Replace `routers/assistant.py` (3,290 lines, 36 routes, 59 helpers) with a package, no API change. Move `_get_assistant_by_id_or_raise` and `_check_user_can_access_assistant` to `routers/utils.py` as `get_assistant_by_id_or_raise` and `check_user_can_access_assistant` (the only renames), ending the underscore imports in `ide.py` and the two mapping routers. Move-only, reviewable with `git diff --color-moved`. Same branch; no MR until the ticket is done.

## Scope

`routers/assistant/` replaces the file (delete it). Bodies move verbatim, in source order. Cap: 500 lines per module (estimates in brackets).

Route modules, in include order (6+1+6+3+4+5+5+4 = 36 routes, the current order): `catalog` (list, users, id, slug, prebuilt x2, detail/settings-config helpers, unreferenced `CategoriesRequest`) [450]; `health` [120]; `toolkits` (`BuiltinSubagentListItem`) [220]; `management` (create, update, delete) [480]; `execution` (ask x3, resume, evaluate, metrics) [430]; `marketplace` (reactions, validate, publish, unpublish, `ReactionRequest`, `PublishToMarketplaceRequest`) [450]; `generation` (`SystemPromptValidation*`) [260]; `versions` [170].

Private modules: `_shared` (slug lookup, remote-entity check, management metric, operation error, marketplace index) [200]; `_ask` (`_ask_assistant`, `_ask_virtual_assistant`, error/save, prepare-for-execution and request-validation helpers) [420]; `_sub_assistants` [220]; `_inline_credentials` (the contiguous inline-credential block with `_get_masked_prompt_variables`) [225].

## Constraints

- Bodies, decorators, docstrings and function-local imports are byte-identical. Only imports and new-file headers change.
- `__init__.py` holds `router = APIRouter(tags=["Assistant"], prefix="/v1", dependencies=[])`, one `include_router` per route module, `__all__ = ["router"]`, nothing else. Route modules use a bare `APIRouter()`, so decorators stay unchanged and versions keep tags `["Assistant", "Assistant Versioning"]`.
- Helpers keep their names. Underscore names are imported only from private modules, never from a module that defines a `router`; private modules never import route modules. Dropping underscores everywhere was rejected: `_ask_virtual_assistant` would collide with the route `ask_virtual_assistant`, and the move would become a rename diff.
- `routers/utils.py`: bodies and messages unchanged; `Ability` at module level; `Assistant` at module level if AC 6 passes, else a function-local import in the lookup (recorded).
- `ide.py` takes `ask_assistant_by_id` from `assistant.execution`, `get_request_handler` from `handlers.assistant_handlers`, the helpers from utils. The mapping routers take the helpers from utils.
- `conversation_metrics` keeps no auth dependency; the combined router keeps `dependencies=[]`.
- Test patch rules (tests stay in their files):
  - Patch the module whose code resolves the name on the exercised path; one old string may become several. `get_request_handler` goes to `_ask`, `asyncio` to `execution`, `logger` to the module that logs, `Ability` to `routers.utils` (access checks) and `catalog` (detail response). `<Name>.<attr>` patches of shared objects keep their tail.
  - Moved helpers are patched as `<using module>.<public name>`, including `assistant_mapping` (13 strings) and `assistant_prompt_variable_mapping` (6).
  - The five `dependency_overrides[assistant_router.authenticate]` sites import `authenticate` from `codemie.rest_api.security.authentication`; the ineffective patch in `test_assistant_plugin_tools.py` becomes `...assistant.toolkits.authenticate`.
  - Imports take the defining submodule; `VirtualAssistantChatRequest` comes from `models.assistant`; the docstring path at `test_project_model_availability.py:705` is updated.

## OpenAPI (second amendment of step-0 criterion 2)

Move-only produces exactly this change:

- Key `codemie__rest_api__routers__assistant__PublishToMarketplaceRequest` (baseline L34644) becomes `codemie__rest_api__routers__assistant__marketplace__PublishToMarketplaceRequest`; `...assistant__ReactionRequest` (L34685) becomes `...assistant__marketplace__ReactionRequest`.
- Their two `$ref` lines change: L54677 (POST `marketplace/publish`), L55037 (POST `{assistant_id}/reactions`).
- Sorted position is unchanged (`ai_kata` < `assistant` < `skill`; `P` < `R` < `m`): four lines edited in place, no moved blocks.
- The key follows the class `__module__`, so every home but `__init__.py` renames it (and `__init__.py` must define nothing). `models/assistant.py` was rejected: same renames, but the blocks sort earlier and about 59 lines move; step 1 left these classes in the router, and a later move costs another rename.
- `BuiltinSubagentListItem` and `SystemPromptValidation*` have plain unique keys: unchanged.
- Regenerate once, after all moves (`poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`). Run the snapshot test first; its failure must show only these four lines, otherwise fix the code.

## Acceptance criteria

1. `assistant.py` is gone; the package holds `__init__.py` and the 12 modules, each at most 500 lines (`wc -l`) with the Apache header. `main.py` is unchanged.
2. The (method, path, name) sequence of the 36 assistant routes in `app.routes` equals the pre-move one (one-off script on both trees, in the handoff). `conversation_metrics` has no `authenticate` dependency on either tree; the combined router has empty `dependencies`.
3. `git diff --color-moved` shows only moved blocks, import lines, new-file headers and the two renames.
4. Grep over `src/` and `tests/` finds no `_get_assistant_by_id_or_raise` or `_check_user_can_access_assistant` (69 occurrences in 8 files before). The public helpers are defined only in `routers/utils.py`.
5. No module under `src/` imports an underscore name from a module that defines a `router`; tests are exempt and import underscore helpers from their defining route module, per the test rule (decision CR-001). Nothing outside the package imports from it except `router` and public handlers. No file under `service/` or `rest_api/handlers/` imports from `rest_api/routers/` except `routers/utils.py`. Every import, patch string and `assistant_router.<attr>` in `src/` and `tests/` that names `routers.assistant` resolves to `router` or a real submodule; the old-to-new patch mapping is in the handoff, because `mock.patch` does not fail on a wrong module that happens to have the name.
6. Each of the 13 package modules, `routers.utils`, `service.scheduler_run_service`, `service.workflow_execution.thought_consumer` and `handlers.assistant_handlers` imports cleanly alone in a fresh interpreter. The guarded app builds hermetically.
7. Applying the two key renames and two `$ref` renames to the old baseline gives a document deeply equal to the new `openapi.json`. `route_precedence.json` is byte-identical (`git diff` empty). Guards pass.
8. `make ruff` passes (F401 in `__init__.py`, complexity 16, license check). Tests pass in `tests/codemie/rest_api/routers`, `tests/unit/routers` (including `test_xwiki_index_endpoints.py`, `test_main_retrieval_backend_disabled.py`), `tests/codemie/service/budget/` and `tests/codemie/rest_api/router_guards`.
9. The handoff says the eventual MR must ask a reviewer for `/sanity` (access-check helpers move; inline-credential and encryption code is relocated).

## Non-goals

- Moving any class to `rest_api/models/`, or deleting `CategoriesRequest`.
- Renaming any helper other than the two; changing endpoint names, paths, tags, operationIds or response models.
- Moving helpers into `service/` (step 7); shortening long handlers.
- Adding auth to `conversation_metrics` or any other behavior change. No new tests, no splitting of test files.
- `main.py` changes, steps 3 to 6, guard test code (only the regenerated `openapi.json`).
- Commits, push or MR beyond the flow's own.

## Open risks

- `routers/utils.py` (many callers, services included) now imports `Assistant`: AC 6, with the function-local fallback.
- Versions, health, evaluate, generation, delete and `ide.py` have no tests; only AC 2, 6 and 7 guard them. `conversation_metrics` is unauthenticated and untested: AC 2 preserves it.
- Line counts are estimates; `management` is closest to the cap. If it exceeds 500, move its encrypt/restore group (84 lines) to a private `_prompt_variables.py` and record it.
- Reference counts come from reading, not full grep: AC 4 and 5.
