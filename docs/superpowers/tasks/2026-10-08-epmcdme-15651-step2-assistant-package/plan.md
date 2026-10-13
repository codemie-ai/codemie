# EPMCDME-15651 Step 2: Assistant Router Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `routers/assistant.py` (3,290 lines, 36 routes) with the package `routers/assistant/` (`__init__` plus 12 modules, each at most 500 lines), move the two access helpers to `routers/utils.py` as public names, retarget importers and tests, regenerate the OpenAPI snapshot once.

**Architecture:** Move-only, block by block; the package imports and the guards pass at every commit (no atomic switch). Task 2 renames the file to `assistant/__init__.py` unchanged. Tasks 3-5 extract private modules, Tasks 6-12 route modules from the tail of `__init__.py` backwards (versions first, catalog last). Each adds its import at the top and `router.include_router(<mod>.router)` at the bottom in front of the already extracted ones, so include order always equals today's route order. Commit per task using the repository's existing convention (`EPMCDME-15651: Capital description`).

**Spec:** `docs/superpowers/tasks/2026-10-08-epmcdme-15651-step2-assistant-package/spec.md` (research: `technical-analysis.md`). `BASE=d36f314039`.

## Global Constraints

- Bodies, decorators, docstrings, function-local imports byte-identical; only imports and new-file headers change. Text is moved by script, never retyped. New `.py` files carry the Apache header.
- Route modules start with a bare `router = APIRouter()`; `__init__` keeps `router = APIRouter(tags=["Assistant"], prefix="/v1", dependencies=[])` verbatim. No auth added to `conversation_metrics`. No re-exports.
- In `src/`, underscore names are imported only from the private modules (`_shared`, `_ask`, `_sub_assistants`, `_inline_credentials`), `__init__` included; private modules never import route modules or `__init__`. Tests import route-module helpers from the defining submodule, as the spec's patch rules say.
- Spec typo: `execution` has 6 routes (6+1+6+3+6+5+5+4 = 36).
- No `main.py`, guard-code or behavior edits; no new tests. Whole-suite gates, `make ruff`, sonar, browser checks belong to Stage 7. Stage only the task's files (never `git add -A`, `opencode.json.lock`, `skills-lock.json`). Before Task 6 read FastAPI `include_router` (tag/prefix merge) in context7; the snapshot proves it.

## Module map

Base line numbers (`BASE` `assistant.py`) are a guide. Each route module gets the route handlers in its range plus the listed symbols; source order is kept. A helper lying inside a range goes where it is listed (`_validate_assistant_integrations`, L1308, sits in the execution range but belongs to `management`).

| Module | Routes (range) | Other symbols |
|---|---|---|
| `_shared` | | `_get_assistant_by_slug_or_raise`, `_validate_remote_entities_and_raise`, `_track_assistant_management_metric`, `_raise_assistant_operation_error`, `_index_marketplace_assistant` |
| `_inline_credentials` | | contiguous `_validate_assistant_inline_integrations` .. `_build_validation_result` (L2596-2787, incl. `_get_masked_prompt_variables`) |
| `_sub_assistants` | | `_validate_sub_assistant`, `_apply_sub_assistant_settings`, `_publish_single_sub_assistant`, `_publish_sub_assistants`, `_validate_publish_marketplace` |
| `_ask` | | `_ask_assistant`, `_ask_virtual_assistant`, `_apply_skill_ids_to_assistant`, `_apply_version_or_default`, `_resolve_chosen_execution_model`, `_prepare_assistant_for_execution`, `_create_assistant_error`, `_create_mcp_auth_error`, `_save_error`, `_try_save_error`, the three `_validate_*_and_raise` at L2550-2595 |
| `versions` | 4 (L2989-3127) | banner comment L2984-2986 |
| `generation` | 5 (L1860-2065) | `SystemPromptValidationRequest/Response` |
| `marketplace` | 5 (L1406-1859) | `ReactionRequest`, `PublishToMarketplaceRequest`, `_remove_marketplace_assistant`, `_llm_validate_on_marketplace_publish` |
| `execution` | 6 (L1004-1403) | `_resume_tool_call`, `_ask_assistant_resume`, `_calculate_workflow_metrics` |
| `management` | 3 (L837-1001) | `_update_source_clone_count`, `_validate_integrations_or_response`, `_build_assistant_from_create_request`, `_save_new_assistant`, `_validate_assistant_integrations`, `_filter_invalid_datasources`, `_encrypt_*` (2), `_restore_existing_encrypted_values`, `_track_mcp_usage_*` (3) |
| `toolkits` | 6 (L528-704) | `BuiltinSubagentListItem` |
| `health` | 1 (L442-525) | |
| `catalog` | 6 (L197-424) | `CategoriesRequest`, `_apply_template_filters`, `_enrich_nested_assistants`, `_get_skills_data`, `_resolve_effective_llm_model`, `_build_assistant_detail_response`, settings-config helpers (4), `_get_categories_data`, `_mask_sensitive_prompt_variables` |

Final `__init__.py` (after the header; ruff formats the import):

```python
from fastapi import APIRouter

from codemie.rest_api.routers.assistant import catalog, execution, generation, health, management, marketplace, toolkits, versions

router = APIRouter(tags=["Assistant"], prefix="/v1", dependencies=[])
router.include_router(catalog.router)  # then health, toolkits, management, execution, marketplace, generation, versions

__all__ = ["router"]
```

## Review Focus

- A patch string bound in the wrong module passes silently when nothing asserts on it, and names `__init__` still binds hide stale strings until Task 12: `PATCHUSE` plus the behavior suite.
- Route order and tags (versions keep `["Assistant", "Assistant Versioning"]`): `ROUTES` diff and `GUARDS` after every task.
- `routers/utils.py` (52 callers) now imports `Assistant`: `IMPORT` of the four AC 6 modules in Tasks 1 and 13.
- `management` is closest to 500 lines. If over, move its encrypt/restore group (84 lines) to `_prompt_variables.py` and record it.

## Preflight (no commit)

Scratchpad tools, outside the repo:

- `ROUTES <out>`: copy of `app_probe.main` (`tests/codemie/rest_api/router_guards/app_probe.py:87`) run the way `guarded_app._run_probe` runs it. Writes `(sorted(methods), path, name, [d.call.__name__ for d in dependant.dependencies])` for every route in `app.routes`, plus `assistant.router.dependencies`. Run now into `routes_before.json`; after each task `diff` must be empty (a superset of AC 2).
- `move_symbols.py <module> <symbol>...`: with `ast`, cut the named top-level nodes (decorators and directly preceding comments included) from `assistant/__init__.py`, append them in source order to the module, delete them from `__init__.py`.
- `PATCHUSE`: for each `codemie.rest_api.routers.assistant.<sub>.<name>` literal in `tests/`, fail unless `<name>` is bound in `<sub>` and used on a non-import line (`<Name>.<attr>` patches check `<Name>`).
- `GUARDS` = `poetry run pytest tests/codemie/rest_api/router_guards -n 0 --show-capture=no -q`. `AT` = `poetry run pytest -q` on `tests/codemie/rest_api/routers/test_assistant*.py`, `test_tool_call_resume.py`, `test_system_prompt_validation.py`, `test_inline_credentials.py`, `test_toolkit_settings_config.py`, `test_project_model_availability.py`, `tests/unit/routers/test_assistant_file_attachment.py`. `IMPORT <module>` = `poetry run python -c "import importlib,sys; importlib.import_module(sys.argv[1])" <module>`.
- `MOVED <a|b|...>` = `rg -nP "(routers[./]assistant\.|assistant_router\.)(?:<a|b|...>)\b" src tests`, empty. Names: symbols moved in the task plus imports pruned from `__init__`.
- `LEFTOVER` (final; flags any `routers.assistant` reference that is not `router` or a real submodule, and any non-`router` import):

```
rg -nUP "routers[./]assistant\.(?!(?:router|catalog|health|toolkits|management|execution|marketplace|generation|versions|_shared|_ask|_sub_assistants|_inline_credentials)\b)\w+|\bassistant_router\.(?!router\b)\w+|routers\.assistant import (?!router\b)" src tests
```

**Patch rules (Tasks 3-12):** retarget to the module whose code resolves the name on the exercised path (one old string may become several): `get_request_handler` to `_ask`, `asyncio` to `execution`, `logger` to the module that logs, `Ability` to `routers.utils` (access checks) or `catalog` (detail response). `<Name>.<attr>` patches keep their tail. Moved helpers are patched as `<using module>.<name>`.

**Procedure (Tasks 3-12):** (1) run the task's failing check; (2) create the module(s): header, copy of the `__init__` import block, `move_symbols.py`, `router = APIRouter()` (route modules), add the `__init__` import and `include_router` line, prune unused imports in the module and in `__init__` (`ruff check --select F401 --fix --unsafe-fixes`), add undefined names (`--select F821`); (3) retarget the task's tests, run `MOVED` and `PATCHUSE`; (4) verify: `IMPORT` per new module, `wc -l` at most 500, `ruff check` and `ruff format --check` on touched files, `AT`, `ROUTES` diff, `GUARDS` (from Task 8 deselect `test_openapi_snapshot.py::test_openapi_matches_baseline`, red until Task 13), then `git add` the task's files and confirm `git diff --cached --color-moved=dimmed-zebra` shows only moved blocks, import lines, headers; (5) commit.

---

### Task 1: Public access helpers in routers/utils.py

**Files:**
- Modify: `routers/utils.py` (add `get_assistant_by_id_or_raise`, `check_user_can_access_assistant`; bodies from `assistant.py:2068-2080`, `:2197-2202`; runtime imports `Assistant`, `Ability`, `Action`; `User` in the existing TYPE_CHECKING block)
- Modify: `routers/assistant.py` (delete both defs, import from utils at `:86`, rename all 36 occurrences)
- Modify: `routers/ide.py:30,152` (helper from utils; `get_request_handler` from `codemie.rest_api.handlers.assistant_handlers`; `ask_assistant_by_id` stays until Task 9), `routers/assistant_mapping.py:31`, `routers/assistant_prompt_variable_mapping.py:28` (plus their 4 handlers)
- Modify tests: `test_assistant_mapping.py` (13 strings), `test_assistant_prompt_variable_mapping.py` (6), `test_tool_call_resume.py` (1), `test_assistant.py` (3) to `<using module>.<public name>`; `routers.assistant.Ability` patches that drive an access check to `routers.utils.Ability`

Test-first: yes — importing both public names from `routers.utils` fails with ImportError and `rg -n '\b_get_assistant_by_id_or_raise|\b_check_user_can_access_assistant' src tests` lists 69 lines; after: import passes, rg is empty, the mapping tests pass.

- [ ] Run `AT`, both mapping test files, `ROUTES` diff, full `GUARDS`, `IMPORT` for `codemie.rest_api.routers.utils`, `codemie.service.scheduler_run_service`, `codemie.service.workflow_execution.thought_consumer`, `codemie.rest_api.handlers.assistant_handlers`. If `routers.utils` cannot import `Assistant` at module level, make it function-local in the lookup and note it for the handoff.

### Task 2: Package skeleton

**Files:** `git mv src/codemie/rest_api/routers/assistant.py src/codemie/rest_api/routers/assistant/__init__.py` (create the directory first).

Test-first: no — rename only, bytes unchanged; proof: `git diff --cached -M --stat` shows a 100% rename, `AT`, `ROUTES` diff and `GUARDS` stay green.

### Task 3: `_shared`

**Files:** Create `assistant/_shared.py`; modify `__init__.py` (import its five names from `._shared`); retarget strings for names the moved code resolves (monitoring, config, logger).

Test-first: yes — `IMPORT codemie.rest_api.routers.assistant._shared` fails (ModuleNotFoundError); after: passes, `MOVED`/`PATCHUSE` clean.

- [ ] Run the Procedure.

### Task 4: `_inline_credentials` and `_sub_assistants`

**Files:** Create both (they may import `_shared` and utils); modify `__init__.py`; `test_inline_credentials.py` (5 private names) to `_inline_credentials`.

Test-first: yes — `IMPORT` of each fails (ModuleNotFoundError); after: passes, `test_inline_credentials.py` green.

- [ ] Run the Procedure.

### Task 5: `_ask`

**Files:** Create `assistant/_ask.py`; modify `__init__.py`; tests: `test_assistant.py` imports of `_ask_assistant`, `_ask_virtual_assistant`, `_prepare_assistant_for_execution`, `_resolve_chosen_execution_model`; `tests/unit/routers/test_assistant_file_attachment.py` (`_validate_*`); patches of `get_request_handler`, `logger` and the like.

Test-first: yes — `IMPORT ...assistant._ask` fails (ModuleNotFoundError); after: passes, `MOVED`/`PATCHUSE` clean.

- [ ] Run the Procedure.

### Task 6: `versions`

**Files:** Create `assistant/versions.py`; modify `__init__.py` (first `include_router` line, at the bottom).

Test-first: yes — `IMPORT ...assistant.versions` fails (ModuleNotFoundError); no test covers these routes, so the `ROUTES` diff and the OpenAPI snapshot (tags) are the proof; after: both clean.

- [ ] Run the Procedure.

### Task 7: `generation`

**Files:** Create `assistant/generation.py`; modify `__init__.py` (include goes before `versions`); `test_system_prompt_validation.py` (`SystemPromptValidation*` imports, patches).

Test-first: yes — as Task 6; after: `test_system_prompt_validation.py` green.

- [ ] Run the Procedure.

### Task 8: `marketplace`

**Files:** Create `assistant/marketplace.py`; modify `__init__.py`; `test_assistant_marketplace.py` (28 strings), `test_assistant_reactions.py` (3).

Test-first: yes — as Task 6; after: both files green. The snapshot test now fails: run it once and confirm the diff names only the two `marketplace` component keys and their `$ref`s.

- [ ] Run the Procedure.

### Task 9: `execution`

**Files:** Create `assistant/execution.py`; modify `__init__.py`; `routers/ide.py:30` (`ask_assistant_by_id` from `codemie.rest_api.routers.assistant.execution`); tests: `test_assistant.py` (resume/ask imports, `asyncio` patches, `VirtualAssistantChatRequest` from `codemie.rest_api.models.assistant`), `test_tool_call_resume.py` (`_resume_tool_call`).

Test-first: yes — `IMPORT ...assistant.execution` fails (ModuleNotFoundError); after: passes, `AT` green.

- [ ] Run the Procedure; `ide.py` has no test, so `IMPORT codemie.rest_api.routers.ide` and the `ROUTES` diff cover it.

### Task 10: `management`

**Files:** Create `assistant/management.py`; modify `__init__.py`; tests: `test_assistant.py` (`create_assistant`, `update_assistant`), `test_assistant_filter_invalid_datasources.py`, docstring path at `test_project_model_availability.py:705`.

Test-first: yes — `IMPORT ...assistant.management` fails (ModuleNotFoundError); after: passes, `wc -l` at most 500 (else the fallback in Review Focus), `AT` green.

- [ ] Run the Procedure.

### Task 11: `toolkits` and `health`

**Files:** Create both (health include before toolkits); modify `__init__.py`; `test_assistant_plugin_tools.py:47` (`...assistant.toolkits.authenticate`).

Test-first: yes — `IMPORT` of each fails (ModuleNotFoundError); after: passes, `AT` green.

- [ ] Run the Procedure.

### Task 12: `catalog` and the final `__init__`

**Files:** Create `assistant/catalog.py`; reduce `__init__.py` to the shape above; tests: `test_assistant.py` (`get_assistant_by_slug`, `_resolve_effective_llm_model`, `Ability`), `test_toolkit_settings_config.py`, and the five `dependency_overrides[assistant_router.authenticate]` sites (`test_assistant_sort_params.py:30`, `test_assistant_marketplace.py:107`, `test_assistant_reactions.py:40`, `test_assistant_users.py:37`, `test_system_prompt_validation.py:42`), which import `authenticate` from `codemie.rest_api.security.authentication`.

Test-first: yes — `IMPORT ...assistant.catalog` fails (ModuleNotFoundError) and `LEFTOVER` lists the stale references; after: passes and `LEFTOVER` is empty.

- [ ] Run the Procedure; `__init__.py` holds only header, imports, `router`, includes, `__all__`.

### Task 13: Final proof and OpenAPI re-baseline

**Files:** Modify (generated, never hand-edited): `tests/codemie/rest_api/router_guards/baselines/openapi.json`

Test-first: yes — `test_openapi_matches_baseline` fails before regeneration and passes after; `route_precedence.json` must not change.

- [ ] **Step 1: Proof (AC 1-6, 9).** `LEFTOVER` and `PATCHUSE` clean; `rg -n '_get_assistant_by_id_or_raise|_check_user_can_access_assistant' src tests` empty; `rg -nUP "routers\.assistant\.(?:catalog|health|toolkits|management|execution|marketplace|generation|versions) import (?:[^\n(]*|\([^)]*)\b_[a-z]\w*" src` empty; `rg -n "assistant import|assistant\.(catalog|health|toolkits|management|execution|marketplace|generation|versions)" src/codemie/rest_api/routers/assistant/_*.py` empty; `rg 'rest_api\.routers' src/codemie/service src/codemie/rest_api/handlers` shows only `routers.utils`; the package has 13 `.py` files, each at most 500 lines, and `rg -L 'Licensed under the Apache License' src/codemie/rest_api/routers/assistant` is empty; `rg -c '^class '` totals 6; `git diff --stat BASE -- src/codemie/rest_api/main.py tests/codemie/rest_api/router_guards/*.py` is empty; `ROUTES` diff empty, `conversation_metrics` dependencies `[]`, router `dependencies == []`; `IMPORT` for all 13 modules (one interpreter each) and the four from Task 1; `git diff --color-moved=dimmed-zebra BASE -- src` shows only moved blocks, imports, headers.
- [ ] **Step 2: Approved-diff check, before writing anything.** Run `test_openapi_snapshot.py` (must fail). In a throwaway `poetry run python` snippet take `git show BASE:tests/codemie/rest_api/router_guards/baselines/openapi.json`, replace `codemie__rest_api__routers__assistant__PublishToMarketplaceRequest` and `...assistant__ReactionRequest` with `codemie__rest_api__routers__assistant__marketplace__PublishToMarketplaceRequest` and `...assistant__marketplace__ReactionRequest` (assert each is replaced exactly twice: key and `$ref`), and assert `json.loads(text) == load_guarded_app_dump()["openapi"]` (from `tests.codemie.rest_api.router_guards.guarded_app`). A mismatch means fixing the code, never regenerating.
- [ ] **Step 3: Regenerate once.** `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`. Then `git diff --exit-code -- tests/codemie/rest_api/router_guards/baselines/route_precedence.json` is empty, `git diff --stat` shows `openapi.json` with 4 insertions and 4 deletions (two keys, two `$ref` lines), `git status --short` shows only that file modified, full `GUARDS` is green, and `poetry run pytest tests/codemie/rest_api/test_xwiki_index_endpoints.py tests/codemie/rest_api/test_main_retrieval_backend_disabled.py tests/codemie/service/budget tests/unit/routers -q` passes.
- [ ] **Step 4: Handoff.** In `local/router-split/HANDOFF.md` (gitignored, never staged) record the old-to-new patch mapping, the `ROUTES` script with both outputs, any fallback taken (utils `Assistant` import, `_prompt_variables.py`), and that the eventual MR must ask a reviewer to post `/sanity` (access-check helpers moved; inline-credential and encryption code relocated).
- [ ] **Step 5:** Commit `openapi.json` only.

## Negative-constraint pass

- Init exports only `router`, nothing else, no re-exports: Task 12 shape; Task 13 Step 1 `LEFTOVER`.
- No underscore import from a router-defining module in `src/`, private modules never import route modules: Task 13 Step 1 `rg` checks.
- No auth on `conversation_metrics`, `dependencies=[]`: Global Constraints; `ROUTES` dump each task.
- No class moved to `models/`, `CategoriesRequest` kept, no helper renamed beyond the two, no behavior change: module map; color-moved review per task; class count 6.
- No `main.py`, guard-code or new-test changes; snapshot regenerated once after the approved-diff check: Task 13 Steps 1-3. No whole-suite gates, manual verification or MR: none planned.
