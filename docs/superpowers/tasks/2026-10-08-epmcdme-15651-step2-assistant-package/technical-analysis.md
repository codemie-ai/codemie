# Technical Research

**Task**: assistant router package split
**Generated**: 2026-10-08
**Research path**: codegraph

---

## 1. Original Context

Implement delivery step 2 of Jira EPMCDME-15651 (split oversized REST API router modules): convert src/codemie/rest_api/routers/assistant.py into an `assistant/` package whose __init__.py builds one combined `router` and exports only `router` (no re-exports); move `_get_assistant_by_id_or_raise` and `_check_user_can_access_assistant` to routers/utils.py as public names; re-point importers/callers/patch targets (incl. ide.py and tests). Move-only, no API change. The ticket body is in docs/superpowers/tasks/2026-10-08-epmcdme-15651-split-router-modules/ticket.md and the user-resolved criteria in .../spec.md in the same dir; step 1 (models move) is in docs/superpowers/tasks/2026-10-08-epmcdme-15651-step1-models-move/. Guard tests: tests/codemie/rest_api/router_guards. The spec must decide the OpenAPI snapshot treatment of the assistant ReactionRequest / PublishToMarketplaceRequest component-key renames if those classes move.

Coordinator notes (verbatim): src/codemie/rest_api/routers/assistant.py is ~3290 lines. Step 1 already moved ToolCallResumeRequest into models/assistant.py; step 0's research at docs/superpowers/tasks/2026-10-08-epmcdme-15651-split-router-modules/technical-analysis.md covers the five target routers (read its assistant-related sections instead of re-researching), and the step-0 spec.md lists the resolved criteria. Focus the new research on: the handler/route inventory of assistant.py grouped by concern (with line ranges), module-level helpers and which handlers share them, every importer / `patch("codemie.rest_api.routers.assistant...")` target in src/ and tests/ (counts per file), route-precedence-sensitive path pairs involving assistant routes (include order and static-vs-dynamic path conflicts), the OpenAPI component-name renames if the remaining router-local Pydantic classes (ReactionRequest, PublishToMarketplaceRequest, any other) are moved, and the auth/access-check code that moves.

---

## 2. Codebase Findings

Tool limits: Read, Glob, codegraph only (no grep, no shell, nothing executed). Counts below come from reading the files; they are exact for the files read and are NOT a tree-wide grep.

### Existing Implementations

`src/codemie/rest_api/routers/assistant.py`: 3,290 lines (ticket 3,214), 36 routes, 59 private helpers (ticket 52), 6 Pydantic classes, no module-level state. `router = APIRouter(tags=["Assistant"], prefix="/v1", dependencies=[])` at L116-120; decorator paths are `/assistants...` under the `/v1` prefix.

**Routes are already six contiguous blocks, in file order** (decorator line):

| Block | Routes | Lines |
|---|---|---|
| A catalog | GET `/assistants` L197, GET `/assistants/users` L259, GET `id/{assistant_id}` L368, GET `slug/{assistant_slug:path}` L382, GET `prebuilt` L405, GET `prebuilt/{slug}` L418, POST `{assistant_id}/health` L442 (84 lines) | L197-525 |
| A toolkits | GET `tools` L528, `hedgeable_tools` L542, `builtin_subagents` L553, `plugin_tools` L567; POST `mcp_tools` L612; GET `context` L684 | L528-704 |
| B management | POST `/assistants` L837, PUT `{id}` L884 (97 lines), DELETE `{id}` L983 | L707-1001 |
| C execution | POST `virtual/model` L1004, POST `{id}/model` L1090, POST `{id}/model/tool-call/resume` L1164, POST `slug/{path}/model` L1215, POST `{id}/evaluate` L1268, GET `metrics/{conversation_id}` L1370 | L1004-1403 |
| D marketplace | POST/DELETE `{id}/reactions` L1406/L1422, POST `marketplace/publish/validate` L1436, `marketplace/publish` L1514, `marketplace/unpublish` L1816 | L1406-1857 |
| E generation | POST `generate` L1860, `prompt/generate` L1896, `refine` L1936, `mcp/test` L1992, `system-prompt/validate` L2016 | L1860-2065 |
| F versions | GET `{id}/versions` L2989, `{id}/versions/{n}` L3018, `.../compare/{v2}` L3042, POST `.../rollback` L3080 (decorator `tags=["Assistant Versioning"]`) | L2984-3127 |

Including blocks in the order A, B, C, D, E, F reproduces the current 36-route order exactly (13+3+6+5+5+4).

**Router-local classes** (L123-171): `BuiltinSubagentListItem` (used by the L553 route only), `ReactionRequest` (L1406 route), `CategoriesRequest` (no reference found in assistant.py or category.py), `PublishToMarketplaceRequest` (L1519 handler), `SystemPromptValidationRequest/Response` (L2016 route).

**Helpers by use** (line counts approximate):
- Used by several blocks: `_get_assistant_by_id_or_raise` L2068 (18 call sites in A, B, C, D, F), `_check_user_can_access_assistant` L2197 (15 sites in A, B, C, D, F), `_validate_remote_entities_and_raise` L2205 (A, B, C, D, F), `_track_assistant_management_metric` L2226 (B, D, F), `_raise_assistant_operation_error` L2295 (B, D), `_get_assistant_by_slug_or_raise` L2083 (A, C), `_index_marketplace_assistant` L3162 (B update, D publish), `_get_masked_prompt_variables` L2621 (D inline-credential validation; A through `_mask_sensitive_prompt_variables`).
- A only: `_apply_template_filters`, `_enrich_nested_assistants`, `_get_skills_data`, `_resolve_effective_llm_model`, `_build_assistant_detail_response`, settings-config family (4, L2788-2871), `_get_categories_data`, `_mask_sensitive_prompt_variables`. About 370 lines plus health 84 plus toolkits 177, about 630 in total.
- B only: `_update_source_clone_count`, `_validate_integrations_or_response`, `_build_assistant_from_create_request`, `_save_new_assistant`, `_validate_assistant_integrations`, `_filter_invalid_datasources`, `_encrypt_*` (2), `_restore_existing_encrypted_values`, `_track_mcp_usage_*` (3). About 414 lines in total.
- C only: `_resume_tool_call`, `_ask_assistant_resume`, `_calculate_workflow_metrics`, `_apply_skill_ids_to_assistant`, `_apply_version_or_default`, `_resolve_chosen_execution_model`, `_prepare_assistant_for_execution`, `_create_assistant_error`, `_create_mcp_auth_error`, `_save_error`, `_try_save_error`, `_ask_virtual_assistant`, `_ask_assistant`, three `_validate_*_and_raise` validators. About 750 lines in total.
- D only: sub-assistant helpers L1615-1813 (5), `_remove_marketplace_assistant`, `_llm_validate_on_marketplace_publish`, inline-credential family L2596-2785 (5, about 150 lines). About 570 lines without, 725 with the family.
- E about 206, F about 144, cross-block core (lookup, access check, remote-entity check, metric, error) about 150.

### Architecture and Layers Affected

API router layer (`rest_api/routers/assistant.py`, `routers/utils.py`), sibling routers `ide.py`, `assistant_mapping.py`, `assistant_prompt_variable_mapping.py`, request models (`rest_api/models/assistant.py`), router tests, guard baselines.

### Integration Points

- `main.py` imports router modules as objects (L62-109) and includes them at L1030-1076 in this order: `a2a`, `assistant_mapping`, **`assistant`**, `assistant_prompt_variable_mapping`, `category`, ..., `ide` L1063, `skill_events`, `skill` L1076. `app.include_router(assistant.router)` works unchanged with a package.
- Importers in src (all that codegraph shows): `ide.py` L30 imports `ask_assistant_by_id`, `_get_assistant_by_id_or_raise`, `get_request_handler` (the last is re-exposed from `handlers.assistant_handlers`; used at L125, L152, L154; `inference` calls the handler as a plain function without `include_tool_errors`/`error_detail_level`, so those take `Query(...)` default objects, unchanged by a move); `assistant_mapping.py` L31 (`_get_assistant_by_id_or_raise`, 2 handlers); `assistant_prompt_variable_mapping.py` L28 (both helpers, 2 handlers). No service or handler imports the router (step 1 removed `ToolCallResumeRequest`). `skill.py` does not import it (it owns its `ReactionRequest`, and its `PublishToMarketplaceRequest` is in `models/skill.py`).
- `routers/utils.py` today: thread pools, `raise_access_denied` (401, 52 callers), `raise_forbidden` (403), streaming helpers; imports only fastapi, starlette, `codemie.configs`, `core.exceptions`; uses `from __future__ import annotations`. `handlers/assistant_handlers.py` imports `run_assistant_in_thread_pool` from it. `models/assistant.py` already imports `Owned, Ability, Action` and `User`.

**Route-precedence pairs involving assistant routes** (`baselines/route_precedence.json`: 45 pairs, 15 touch assistant routes). Intra-router (7): GET `id/{x}`, `slug/{path}`, `prebuilt/{slug}`, `metrics/{cid}` before GET `{id}/versions` (4); `slug/{path}` before `{id}/versions/{n}` and `.../compare/{v2}` (2); POST `virtual/model` before POST `{id}/model` (1). Cross-router (8): `assistant_mapping` `{id}/users/mapping` before `slug/{path}` (1); assistant `id/{x}`, `slug/{path}`, `prebuilt/{slug}`, `metrics/{cid}` before `skill` `{id}/skills` (4); `slug/{path}` before `assistant_prompt_variable_mapping` `{id}/users/prompt-variables` (1); assistant DELETE `{id}/reactions` and GET `{id}/versions` before `category` `categories/{id}` (2). The `main.py` order above satisfies every cross-router pair regardless of the package layout.

### Patterns and Conventions

- 35 of 36 routes declare `user: User = Depends(authenticate)`; **`GET /assistants/metrics/{conversation_id}` (`conversation_metrics`, L1375) has no auth dependency**, and the router-level `dependencies=[]` is empty.
- Four versioning routes get tags `["Assistant", "Assistant Versioning"]` (router tag plus decorator tag).
- Function-local imports guard cycles and some shadow module-level names: `AssistantVersionService` (L788, L3010, ...), `ModelAvailabilityService` (L848), and `get_request_handler` inside `_ask_assistant_resume` (L1208), so patching the module-level name does not reach that function.
- Only package-with-router precedent seen: `codemie/enterprise/mcp_auth/` (`router.py` plus underscore-named siblings `_initiate.py`, `_uri.py`).

**OpenAPI component keys of the router-local classes** (snapshot `openapi.json`, already post-step-1). Three `ReactionRequest` classes exist (`routers/ai_kata.py`, `routers/assistant.py`, `routers/skill.py`) and two `PublishToMarketplaceRequest` (`routers/assistant.py`, `models/skill.py`), so the keys are module-qualified: `codemie__rest_api__routers__assistant__PublishToMarketplaceRequest` (L34644-34684) and `codemie__rest_api__routers__assistant__ReactionRequest` (L34685-34702); each has one `$ref` (requestBody of POST `marketplace/publish` and POST `{id}/reactions`). `BuiltinSubagentListItem` and `SystemPromptValidationRequest/Response` have plain unique keys; `CategoriesRequest` is not in the schema. A key follows the class `__module__`: a move to `models/assistant.py` gives `codemie__rest_api__models__assistant__<Name>`, a move into a package submodule gives `codemie__rest_api__routers__assistant__<submodule>__<Name>`. `models/assistant.py` does not import `Literal` today (`ReactionRequest` uses it).

---

## 3. Documentation Findings

### Guides and Architecture Docs

`.ai-run/guides/architecture/project-structure.md` and `quality-gates.md` read: neither lists router modules by path. The step-0 analysis read the other architecture, API and testing guides (routers thin, schemas in `rest_api/models/`, helpers in `rest_api/utils/` or services). `local/router-split/HANDOFF.md` (gitignored notes) records state and gotchas.

### Architectural Decisions

No ADR. Decisions are in `ticket.md` and `spec.md` (Section 8).

### Derived Conventions

Patch where a name is used. Public helpers in `routers/utils.py` use verb-first names (`raise_*`, `run_*`). Criterion 5 removes underscore helpers imported across routers.

---

## 4. Testing Landscape

### Existing Coverage

135 patch strings `codemie.rest_api.routers.assistant.<name>` in 6 files (equals the ticket's 135; the ticket's "11 files" differs from the 13 files that reference the module):

| File | Patch strings | Other references |
|---|---|---|
| `routers/test_assistant.py` | 100 | 18 import statements (`_ask_assistant`, `_ask_virtual_assistant`, `_prepare_assistant_for_execution`, `create_assistant`, `update_assistant`, `get_assistant_by_slug`, `ask_virtual_assistant` plus `VirtualAssistantChatRequest` taken from the router namespace, `resume_tool_call`, `_ask_assistant_resume`, `_resolve_effective_llm_model`, `_resolve_chosen_execution_model`) |
| `routers/test_assistant_marketplace.py` | 28 | `assistant_router.authenticate` |
| `routers/test_assistant_reactions.py` | 3 (`Assistant.find_by_id`) | `assistant_router.authenticate` |
| `routers/test_assistant_sort_params.py` | 2 (`AssistantRepository`) | `assistant_router.authenticate` |
| `routers/test_assistant_plugin_tools.py` | 1 (`authenticate`, ineffective: routes captured it at definition) | `import router` + `app.include_router(router)` |
| `routers/test_tool_call_resume.py` | 1 (`_get_assistant_by_id_or_raise`) | `_resume_tool_call` |

Import-only files: `test_assistant_mcp_tools.py` (`router`), `test_assistant_users.py` and `test_system_prompt_validation.py` (`assistant_router.authenticate`), `test_assistant_filter_invalid_datasources.py`, `test_inline_credentials.py` (5 private names), `test_toolkit_settings_config.py`, `tests/unit/routers/test_assistant_file_attachment.py`. Two more files patch the moved helpers on sibling routers: `test_assistant_mapping.py` 13 strings `assistant_mapping._get_assistant_by_id_or_raise`; `test_assistant_prompt_variable_mapping.py` 6 (3 per helper). Not scanned: tests outside these two directories (no grep).

### Testing Framework and Patterns

pytest; real `app` from `main`, httpx `AsyncClient`/`TestClient`, `app.dependency_overrides[...authenticate]`, MagicMock users; `routers/conftest.py` disables the rate limiter. Marketplace tests patch `codemie.core.ability.Ability.can` at class level and assert 401 "Access denied", an end-to-end check of the access path. Many patches hit shared objects (`Assistant.find_by_id`, `GuardrailService.*`, `asyncio.to_thread`, `config.MARKETPLACE_...`) and resolve through any module path that has the name; whole-name patches (`Ability`, `get_request_handler`, `logger`, `AssistantRepository`, helper functions) bind to the module that uses them. Guards: `poetry run pytest tests/codemie/rest_api/router_guards -n 0 --show-capture=no -q` (42 tests, about 15 s, app built in a fresh subprocess).

### Coverage Gaps

In the files read, no test targets the routes for versions, health, evaluate, generate/refine/prompt-generate, `mcp/test`, delete, ask-by-id, ask-by-slug, metrics, tools/context, or `ide.py` (no `*ide*` test file exists). `routers/utils.py` has `test_routers_utils.py` but nothing for the two helpers, which do not live there yet.

---

## 5. Configuration and Environment

### Environment Variables

`MARKETPLACE_LLM_VALIDATION_ON_PUBLISH_ENABLED` (read in the publish handler). `routers/utils.py` reads `THREAD_POOL_MAX_WORKERS` and `ASSISTANT_THREAD_POOL_MAX_WORKERS` at import. The guard profile pins other env in `guarded_app.PROFILE_ENV`.

### Configuration Files

`pyproject.toml`: FastAPI ^0.133, ruff rules E,F,B,N,C4,G,T20,RSE,SIM,C,W,PERF,ISC (F401 applies to `__init__.py`), mccabe 16. `baselines/openapi.json` (sorted keys, version `0.0.0-router-guards`) and `baselines/route_precedence.json`; `regenerate_baselines` rewrites both: `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`.

### Feature Flags and Deployment Concerns

No flag gates assistant routes. New `.py` files need the Apache header (`make license-check`). Known unrelated failures per HANDOFF: two `test_filesystem_policy.py` tests, gitleaks on untracked local files.

---

## 6. Risk Indicators

- Speculative: moving `ReactionRequest`/`PublishToMarketplaceRequest` renames 2 component keys and 2 `$ref` lines for any destination; needs one approved re-baseline, `route_precedence.json` byte-identical.
- `conversation_metrics` has no auth dependency. Speculative: a combined-router `Depends(authenticate)` would change behavior and OpenAPI security; keep `dependencies=[]`.
- Speculative: patch retargeting is not 1:1 (`Ability` splits between utils and catalog; `get_request_handler`, `logger`, `asyncio` follow the using module). Six sites use `routers.assistant.authenticate` and fail once only `router` is exported.
- Speculative: execution (~750), marketplace (~570-725) and catalog plus toolkits (~630) exceed 500 as single modules.
- Speculative: `routers/utils.py` importing `Assistant`/`Ability` widens its import graph (52 callers); verify by fresh-interpreter import.
- Speculative: `/sanity` applies. `ide.py` has no test.
- Counts come from reading, not grep; unscanned test directories may hold more references.

---

## 7. Summary for Complexity Assessment

The move surface is one 3,290-line file (36 routes in six contiguous blocks, 59 helpers) plus three importers (`ide.py`, `assistant_mapping.py`, `assistant_prompt_variable_mapping.py`), `routers/utils.py`, optionally `models/assistant.py`, and 15 test files (135 `routers.assistant` patch strings). Layers: routers and tests only.

Novelty is low; the guards (OpenAPI snapshot, 45 route pairs, 15 touching assistant) make errors loud. The effort is retargeting patches by using module and holding the 500-line rule.

Spec decisions: snapshot treatment of the 2 renames, public helper names, sharing of underscore helpers inside the package.

---

## 8. External References

Resolved, read in full: `.../split-router-modules/ticket.md` (plan item 2: catalog, toolkits, management, execution, marketplace, generation, versions, `_shared`; init exports only `router`; criterion 5: no cross-router underscore imports), `.../split-router-modules/spec.md` (about 500-line modules bind; OpenAPI identical except a reviewed re-baseline; baseline is computed pairs, now 45; step 2 moves the two helpers to `routers/utils.py` as public names; `/sanity`), `.../step1-models-move/spec.md` (one regeneration, approved diff checked by applying renames to the old baseline).
