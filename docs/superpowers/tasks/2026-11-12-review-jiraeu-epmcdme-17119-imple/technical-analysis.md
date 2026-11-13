# Technical Research

**Task**: project allowed-models model-availability
**Generated**: 2026-11-12
**Research path**: codegraph

---

## 1. Original Context

Review https://jiraeu.epam.com/browse/EPMCDME-17119 and implement the fix.

The requirements come from the ticket EPMCDME-17119 backend implementation brief: goal, stored states table for applications.allowed_models + default_model, 3 changes — (1) Alembic migration relaxing ck_model_whitelist_requires_default, (2) validating default_model in ProjectService.update_allowed_models when allowed_models is NULL, (3) project creation storing allowed_models=None when no routers are available — plus out-of-scope list and local evidence.

feature_area: project allowed-models model-availability
run_dir: /Users/bohdan_maliar/Projects/codemie-dev/codemie/docs/superpowers/tasks/2026-11-12-review-jiraeu-epmcdme-17119-imple/

---

## 2. Codebase Findings

### Existing Implementations

Purpose A: a project can be stored as "all models, including future ones" with its own default (`allowed_models` NULL + `default_model` set).
- DB check `ck_model_whitelist_requires_default` — `src/external/alembic/versions/a7f3c9e1b4d6_add_project_level_model_availability.py:74-80`. Expression: `(allowed_models IS NULL AND default_model IS NULL) OR (allowed_models IS NOT NULL AND default_model IS NOT NULL)`, schema `codemie`. The only definition in the repo (no model-level `CheckConstraint`). Only other mentions are in an old review diff under `docs/superpowers/reviews/`. `downgrade()` there drops the constraint then both columns. This file's `down_revision` is `d9e8f7a6b5c4`; it was later merged by `f88ad134467d` (merge revision).
- Columns: `Application.allowed_models` (`ARRAY(String)`, nullable) and `default_model` (`max_length=255`) — `src/codemie/core/models.py:400-401`.
- Policy reader: `ProjectModelPolicy.for_application` — `src/codemie/service/llm/model_availability_service.py:115-135`. `allowed is None` -> `UNRESTRICTED` carrying `default_model`; `[]` -> `DENY_ALL`; list without default -> `NoDefaultModelException` unless `require_default=False`; else `WHITELIST`. NULL + default is already read as UNRESTRICTED + default.
  - `choose` (`:137-148`): UNRESTRICTED returns first present candidate, else `catalog_base_name(default_model)`. `allows`/`check` are no-ops for UNRESTRICTED (`:176`, `:185`).
  - `resolve_model_for_execution` (`:257-300`): UNRESTRICTED candidates = `[requested, catalog_base_name(policy.default_model), fallback_model]`.
  - `validate_model_for_asset_creation` (`:221-244`) goes through `for_project(...).check`, so unrestricted passes.
- Model list filters: `LLMService._filter_models_by_project` (`llm_service.py:906-926`) and `_filter_router_options_by_project` (`:312-329`) both use `for_application(..., require_default=False)`; UNRESTRICTED lets everything through.
- Default selection: `LLMService._select_default_model` (`llm_service.py:928-978`) honours `project.default_model` (matches `base_name` or `deployment_name`) whenever it is set, regardless of allowed_models. Missing/removed stored default falls to platform default then first allowed model.
- `/v1/llm_models` — `src/codemie/rest_api/routers/llm_models.py:77-96`: appends chat models plus router options, then when `project.default_model` is set re-flags `default` on chat models (`base_name == default_name or deployment_name == default_name`); router entries are left unpatched; the platform default flag is not cleared elsewhere, so two models can carry `default` (matches the ticket's "found, not caused" item).
- Read-side exposure: `ProjectVisibilityService` dict `project_visibility_service.py:204-205` -> `crud.py:399-400` -> `ProjectDetailResponse.allowed_models/default_model` (`rest_api/models/project.py:206-207`). `AllowedModelsUpdateResponse` (`:289-299`) built by `management.py:117-128` (PATCH) and `:146-157` (DELETE).

Purpose B: set/reset the project's allowed models and default via API.
- `PATCH /projects/{projectName}/allowed-models` — `rest_api/routers/projects/management.py:95-128`; request `AllowedModelsUpdateRequest` (`rest_api/models/project.py:284-286`, both fields Optional, default None).
- `ProjectService.update_allowed_models` — `src/codemie/service/project/project_service.py:898-924`. Order: `validate_allowed_models` (`:797`, None passes through; `[]`/non-str/invalid chars -> 400) -> `ensure_chat_model_allowed` (`:844`, returns immediately for None or empty list; for a list requires intersection with chat ids | router ids, else 400 `ALLOWED_MODELS_EMPTY`) -> inside session: `_find_project` (row lock, 404), `_check_allowed_models_authorization` (403) -> `effective_default_model = default_model if default_model is not None else project.default_model` (`:913`) -> `validate_default_model` (`:914`) -> if list and effective default is None, 400 `DEFAULT_MODEL_REQUIRED_WITH_ALLOWED_MODELS` (`:916-920`) -> `_persist_allowed_models`.
- `validate_default_model` — `project_service.py:877-895`: returns when `default_model is None or allowed_models is None` (`:888`); else 400 with the inline literal message `"default_model must be in allowed_models list"` if not in list. So with `allowed_models=None` any string, including `""`, is accepted today. The `effective_default_model` used there is the request value, else the stored one — the stored (inherited) default is passed through `validate_default_model` as well; with a list that checks membership in the new list (this is what the existing "stale default" regression test covers).
- Available-id helpers: `_get_available_chat_model_ids` (`:935-938`) = `deployment_name` of every model in `get_all_llm_model_info()` that is not a declared LiteLLM router (no `enabled`/`forbidden_for_web` filter); `_get_available_router_model_ids` (`:940-957`) = `base_name` of enabled, non-`forbidden_for_web` Switchyard routers (`llm_service.get_llm_routers()`) and litellm_auto routers. So chat ids are deployment names, router ids are base names.
- `model_aliases(model)` — `model_availability_service.py:42-52`: returns `{model, entry.base_name, entry.deployment_name}` of the one catalog entry the name matches (base_name first, then deployment_name); unknown name -> `{model}`. `catalog_base_name` (`:55-64`). `_catalog_entry` uses `get_all_llm_model_info()` (all catalog models, router declarations included).
- `_persist_allowed_models` (`project_service.py:980-1027`): sets both fields, writes a `PROJECT_UPDATED` activity event with `build_change_attributes` diff on both fields, commits; on commit failure logs, rolls back and raises (docstring: 500). Shared by update and reset.
- `reset_allowed_models` (`:926-933`) persists `(None, None)`; DELETE route `management.py:131-157`.
- `ProjectService.ERRORS` `SimpleNamespace` is `project_service.py:108-156`; allowed-model messages at `:151-155` (not `:145` as the ticket says).

Purpose C: what a newly created project stores for models.
- `ProjectService.create_shared_project_in_session` — `project_service.py:214-273`; models block `:254-261`: comment at `:254-255`; `models = llm_service.get_allowed_chat_models(actor, include_all=False)` -> `deployment_names` -> `validate_allowed_models` -> `ensure_chat_model_allowed` -> `project.allowed_models = validated_models; project.default_model = validated_models[0]`. `validated_models[0]` is a `deployment_name` of the first non-router, web-visible model the actor can use. Note `ensure_chat_model_allowed` checks against all catalog chat models + enabled routers, not against the actor-filtered list.
- `LLMService.get_allowed_chat_models(user, include_all, project)` — `llm_service.py:980-1016`: per-user (LiteLLM integration for external users), excludes declared routers, `forbidden_for_web` filtered unless `include_all`.
- `LLMService.get_allowed_router_options(include_all=False, project=None)` — `llm_service.py:289-310`: not user-scoped; Switchyard routers (enabled, `forbidden_for_web` filtered) plus litellm_auto routers (enabled; `forbidden_for_web` filtered unless include_all); `project` argument filters via the policy. Returns `list[LlmRouterOption]`; empty list when no router available. Callers elsewhere: `rest_api/routers/llm_models.py`.
- Callers of `create_shared_project_in_session`: `ProjectService.create_shared_project` (`project_service.py:178`, route `crud.py:213`) and `ProvisioningService._create_project` (`src/codemie/service/provisioning_service.py:153`, `/v1/provisioning`). Both inherit any change.
- Create response: `ProjectCreateResponse` has `allowed_models` (`rest_api/models/project.py:233`) but no `default_model`; `crud.py` create route does not set it (only `:399-400` for detail). Update route sets `allowed_models=getattr(project, "allowed_models", None)` (`management.py:88`).

Other touched code: none beyond the above. Nothing else reads `allowed_models`/`default_model` in `src/` (grep of `.allowed_models`, `.default_model`, `allowed_models=` complete; `core/router_chat_model.py` `default_model` is unrelated).

### Decisions Already in the Code

- Convention: NULL `allowed_models` = unrestricted ("null = all models allowed": `validate_allowed_models` docstring `:797-801`, column comment in the migration `:54-56`, `_filter_models_by_project` docstring `:909-910`). NULL + default is already tolerated by every reader (see Purpose A).
- Chosen: the check couples the two columns strictly both-null / both-set (`a7f3c9e1b4d6:74-80`). Alternative: allow NULL list with a default. Recorded reason: none found in code; the migration docstring only says "if allowed_models is set, default_model must also be set" (`:73`) — the one-directional meaning of that comment is looser than the actual expression.
- Chosen: project settings store `deployment_name` for chat models and `base_name` for routers (`create_shared_project_in_session` `:256-261`; `LLMService._filter_router_options_by_project` docstring `:318-320`; `ProjectModelPolicy.allows` docstring `:170-175`; `catalog_base_name` docstring `:55-60`). Readers accept either name via `model_aliases`. The UI sends `base_name` for default (per ticket); stored defaults can therefore be either spelling. Data needed in a different form: validating an arbitrary default needs both spellings; `_get_available_chat_model_ids` yields deployment names only, `_get_available_router_model_ids` base names only, `model_aliases` expands one catalog entry to both.
- Chosen: list branch checks the inherited (stored) default only against the new list, never against the catalogue (`update_allowed_models` `:913-914` + `validate_default_model`). Reason: none found in code.
- Chosen: error text for default-model failures is inline literal (`validate_default_model` `:894`), while allowed-model errors live in `ERRORS` (`:151-155`); `ensure_chat_model_allowed` raises `ALLOWED_MODELS_EMPTY`.
- Convention: services raise `ExtendedHTTPException(code=400, message=...)` for validation; project_service has no code comments policy beyond docstrings; the repo owner's memory notes say no explanatory comments in code changes (user preference, not a code fact).
- Convention for Alembic: hand-written revisions with `revision`, `down_revision`, `op.*`; check constraints created via `op.create_check_constraint(name, 'applications', expr, schema='codemie')` and dropped via `op.drop_constraint(name, 'applications', schema='codemie', type_='check')` (this file). Data migrations in this repo use `op.execute`.
- Alembic head: not determined by a tool. Revisions with no later `down_revision` pointing at them seen in searches: `aad1456a4f17` (add_run_attributes, 2026-10-30, down `b5c6d7e8f901`), `e3b7c1a95d42` (add_hosted_applications, down `0ba28b31fa53`), `a4c9e1b7d2f3` (down `a9c4e7b2d1f3`). Ticket says use `alembic heads`; only the command output settles it.

### Task Statements Checked Against the Code

(Statements attributed to "the ticket EPMCDME-17119".)
- Check `ck_model_whitelist_requires_default` is defined in `a7f3c9e1b4d6_add_project_level_model_availability.py` — holds (`:74-80`).
- `upgrade()` should re-create it as `allowed_models IS NULL OR default_model IS NOT NULL` in schema `codemie` — consistent with the existing creation call's signature (`schema='codemie'`); the row set forbidden then is exactly list + NULL default.
- "List + NULL default stays forbidden" — the app-level guard exists independently: `update_allowed_models` `:916-920` returns 400 `DEFAULT_MODEL_REQUIRED_WITH_ALLOWED_MODELS`; creation always sets a default.
- "Rest of backend already supports NULL + default" — holds for readers in `src/` (Purpose A: `for_application` `:125-126`, `choose` `:148`, `resolve_model_for_execution` `:298`, `_select_default_model` `:949-962`, `llm_models.py:83-94`). Verified by reading; the ticket's local runtime evidence is unverified here (no running stack).
- `validate_default_model` returns early when `allowed_models is None` (ticket `:878`) — holds (`project_service.py:888`); any string accepted. `update_allowed_models` at `:898` — holds.
- "Reuse `_get_available_chat_model_ids` / `_get_available_router_model_ids` (`:936`, `:941`) or `model_aliases` (`model_availability_service.py:42`)" — line numbers hold. Fact: the two helpers cover deployment_name (chat) and base_name (router) only; `model_aliases` returns names for a catalog entry but returns `{model}` unchanged for unknown names (it does not itself reject unknown ones) and does not exclude routers/disabled/forbidden models.
- "Do not re-validate an inherited default against the catalogue; matches list branch" — holds for the list branch (`:913-914`: inherited default only compared to the new list). Keeps no existing mechanism in place beyond `update_allowed_models`'s `effective_default_model` flow, whose inherited-default path today is the `validate_default_model` call (consumers below).
- `ERRORS` at `:145` — drifted: now `:108-156`, allowed-model entries at `:151-155` (`:145` is `PERSONAL_DELETE`).
- "Do not reuse `ensure_chat_model_allowed`: its message is about allowed_models" — holds (`ALLOWED_MODELS_EMPTY`, and it returns immediately for `None`, `:857-858`).
- Creation "stores the creator's non-router chat models and default `validated_models[0]`" at `:255-261` — holds (`:256-261`; comment at `:254-255`, ticket says `:254`).
- "Models tab lists those models plus routers" — `/v1/llm_models` appends `get_allowed_router_options` to chat models (`llm_models.py:77-80`); Models-tab UI itself is outside this repository (unverified).
- `llm_service.get_allowed_router_options(include_all=False)` at `llm_service.py:289` — holds; keeps existing mechanism in place: the list-of-names store for creation remains in the router-present branch (consumers in Consumers section).
- "Keep `validate_allowed_models` / `ensure_chat_model_allowed` on the computed list in both branches" — both operate on the list returned by `get_allowed_chat_models`; `ensure_chat_model_allowed` is the existing check that raises when no chat model is available (note: against the full catalogue, not the actor-filtered list).
- `reset_allowed_models` unchanged — holds (`:926-933`).
- "Create-project response returns `allowed_models: null` while a list is stored" — holds (`ProjectCreateResponse.allowed_models` `:233` never set by `crud.py` create route).
- "`/v1/llm_models?project_id=` marks two models `default`" — holds by reading `llm_models.py:82-94` (only re-flags matches true; no clearing). Out of scope per ticket.
- "CLI not affected; LLM proxy (`enterprise/litellm/proxy_router.py`) never reads allowed_models" — no occurrence of `allowed_models` in `src/codemie/enterprise/` per the full-repo search of `.allowed_models`/`allowed_models=` (holds). CLI behaviour itself is outside this repository (unverified).
- "Existing list projects not migrated; on prod none need it (checked)" — unverified (prod data); no data migration exists in the repo for it.

### Consumers of Changed Contracts

1. DB check `ck_model_whitelist_requires_default` (dropped/re-created): writers of `applications.allowed_models/default_model`: `_persist_allowed_models` (`project_service.py:1005-1006`, used by PATCH and DELETE), `create_shared_project_in_session` (`:260-261`, used by `create_shared_project` and provisioning). Nothing else assigns these columns. Under the old check the NULL + default write raised `CheckViolation` -> `_persist_allowed_models` commit-failure path (logged, rolled back, re-raised).
2. `validate_default_model` (`:877`): single caller `update_allowed_models` (`:914`); tests call it directly (`test_project_model_availability.py:218` expecting rejection, `:984` and `:995` expecting no-raise for a list and for `None` default). A new check for `allowed_models is None` + non-None default changes the no-op outcome for `(default, None)` only if applied inside this function; the existing direct-call tests pass lists, so none exercise the None branch except `:995` (default None).
3. `update_allowed_models` (`:898`): single route caller (`management.py:110`). PATCH body fields are both optional; sending `allowed_models: null` and no default keeps the stored default (`:913`), including a stored default for a list project that is now being reset to NULL (stored default stays and is then the "NULL + default" state).
4. `ProjectService.ERRORS` (SimpleNamespace): used by `project_service.py` and by tests that read messages; adding an entry affects no reader.
5. `create_shared_project_in_session` models block (`:254-261`): callers `create_shared_project` (`:178`, route `crud.py:213`) and `ProvisioningService._create_project` (`provisioning_service.py:153`). Readers of the stored result: policy (`for_application`), `_filter_models_by_project`, `_filter_router_options_by_project`, `_select_default_model`, `llm_models.py:83`, `ProjectVisibilityService` (`:204-205`), `ProjectDetailResponse`. A stored NULL + `deployment_name` default is read by `catalog_base_name`/`_select_default_model` through either name; `llm_models.py:90` compares both names.
6. `get_allowed_router_options` (`llm_service.py:289`): is used by `llm_models.py` (14 callers per graph, router file) and tests `tests/codemie/service/test_llm_service.py`, `test_llm_service_litellm_integration.py`; not previously used from `project_service.py` (it imports `llm_service` at module level: `project_service.llm_service` is the patched name in tests).
7. Tests that pin current creation behaviour: `tests/codemie/service/project/test_project_service.py` `TestCreateSharedProjectInSession` (`:340-457`): patches `codemie.service.project.project_service.llm_service` as a `MagicMock`; `_prepare` sets only `get_allowed_chat_models.return_value`; `test_initializes_allowed_and_default_models` (`:441-457`) asserts `allowed_models == ["gpt-4o","claude-3"]`, `default_model == "gpt-4o"` and `get_allowed_chat_models.assert_called_once_with(service_account, include_all=False)`. With an unconfigured `MagicMock`, `get_allowed_router_options()` returns a (truthy) `MagicMock`, not a list. `_chat_model_check_passes` autouse fixture patches `ensure_chat_model_allowed`. `tests/codemie/service/test_provisioning_service.py` (7 mentions) and `test_provisioning_budget_service.py` (1) reference the create/update methods — whether they patch `ProjectService` or run the models block was not inspected.

### Architecture and Layers Affected

- Persistence/migration: Alembic revision in `src/external/alembic/versions/`, table `codemie.applications`.
- Service: `src/codemie/service/project/project_service.py` (`ProjectService`) — update path and creation path.
- Service collaborators read-only: `src/codemie/service/llm_service/llm_service.py` (`llm_service` singleton), `src/codemie/service/llm/model_availability_service.py`.
- REST: `src/codemie/rest_api/routers/projects/management.py` (PATCH/DELETE allowed-models), `crud.py` (create), `rest_api/models/project.py` (request/response models) — consumers; nothing in the ticket requires changes there.
- Provisioning: `src/codemie/service/provisioning_service.py` calls creation.

### Integration Points

- `project_service` -> `llm_service.get_allowed_chat_models`, `get_all_llm_model_info`, `get_llm_routers` (lazy import in `ensure_chat_model_allowed` and `_get_available_router_model_ids`; module-level `llm_service` is used in `create_shared_project_in_session`).
- `project_service` -> `application_repository`, `activity_event_repository` (PROJECT_UPDATED with `build_change_attributes`; both fields diffed in `_persist_allowed_models`), SQLModel `Application`.
- Feature flag `projectModelOverride` (`PROJECT_MODEL_OVERRIDE_FEATURE`, `customer_config`) gates all reading policy, not the write path.
- LiteLLM-sourced catalog: `get_all_llm_model_info()` is the catalogue ("platform") that grows when models are added; a NULL project is unrestricted at read time, a list project is fixed.

### Patterns and Conventions

- Static/class methods on `ProjectService` with `ExtendedHTTPException(code, message)`; constants in `ERRORS` `SimpleNamespace`.
- Change auditing via `build_change_attributes` for model fields.
- Tests: pytest, `unittest.mock` patching of `codemie.service.project.project_service.*` module attributes; `SimpleNamespace`/`MagicMock` stand-ins; no real Postgres in the project-service and model-availability tests (consistent with user note "tests match area style").
- Alembic migrations: plain Python revisions (see Decisions).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/` has no mention of `allowed_models`, `default_model` or `ck_model_whitelist_requires_default` (searched). Relevant general guides: `.ai-run/guides/data/database-patterns.md` (SQLModel/session; Alembic conventions are not confirmed here — not read in full), `.ai-run/guides/architecture/service-layer-patterns.md`, `.ai-run/guides/development/error-handling.md`, `.ai-run/guides/testing/testing-patterns.md`, `.ai-run/guides/testing/testing-service-patterns.md`, `.ai-run/guides/quality-gates.md`.

### Architectural Decisions
- Feature origin: EPMCDME-14349 "Project-Level Model Availability" (docstrings in the migration and `test_project_model_availability.py`). A review diff for it is under `docs/superpowers/reviews/2026-10-01-epmcdme-14349-project-level-model-availability/`; no ADR found.

### Derived Conventions
- See Section 2 "Decisions Already in the Code".

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/rest_api/routers/test_project_model_availability.py` (25 hits): update/reset/default (`TestProjectLevelModelRestriction` `:140`, `TestEdgeCases` `:801`, `TestDefaultModelHandling` `:972` calling `validate_default_model` directly), model filtering, assistant/workflow creation under restriction.
- `tests/codemie/rest_api/routers/test_project_model_availability_integration.py` (12 hits) and `..._execution.py`.
- `tests/codemie/rest_api/routers/test_projects_router.py` (12 hits): PATCH allowed-models route tests, including regression tests for empty-string default and stale default not in narrowed list (`:3734`, `:3780`).
- `tests/codemie/service/project/test_project_service.py` (`TestCreateSharedProjectInSession` `:340`, helper tests for `validate_allowed_models`/`ensure_chat_model_allowed` around `:758-800`, authorization `:935-959`).
- `tests/codemie/service/llm/test_model_availability_service.py`, `tests/codemie/service/test_llm_service.py`, `tests/codemie/test_model_narrowing_integration.py`.
- `tests/codemie/service/test_provisioning_service.py`.

### Testing Framework and Patterns
- pytest 8.4 (`.pyc` tags show 3.12/3.13), `unittest.mock.patch`/`MagicMock`, `SimpleNamespace`; class-level `@patch("codemie.service.project.project_service.<name>")`; fixtures `regular_user`, `service_account`.

### Coverage Gaps
- No test for `update_allowed_models` with `allowed_models=None` and a non-None `default_model` (neither accept nor reject), nor for inherited-default behaviour when `allowed_models` is NULL.
- No test of creation with routers present vs. absent (`get_allowed_router_options` is never configured in `TestCreateSharedProjectInSession`).
- No migration tests found for Alembic revisions (searched tests for `alembic`/revision ids; none for the project-model migration).
- No test pins the DB check itself (no real-DB tests in this area).

---

## 5. Configuration and Environment

### Environment Variables
- None specific. `config.ENABLE_USER_MANAGEMENT` gates the PATCH/DELETE routes via `_ensure_user_management_enabled`.

### Configuration Files
- Customer config feature flag `projectModelOverride` (`customer_config.is_feature_enabled`) — gates policy application on read paths.
- Alembic: `src/external/alembic/versions/`.

### Feature Flags and Deployment Concerns
- Migration applies to all installs; the ticket says no data change. Constraint relaxation is the only schema effect. Downgrade strategy per ticket: no-op (note the existing revision's own downgrade drops the constraint again by name, and `drop_constraint` on a missing/renamed name would fail only if the constraint is absent).

---

## 6. Risk Indicators

- Alembic head not determined from tools here (several unreferenced candidates listed in Section 2); a new revision must chain to the real head (`alembic heads`) or a multi-head state results (the repo has prior merge revisions: `f88ad134467d`, `8eb9522661cf`, `7d4e5f6a8c1c`-style merges).
- Speculative: id-spelling mismatch — chat ids from `_get_available_chat_model_ids` are `deployment_name`s while the UI sends `base_name` and routers are `base_name`s; validation by one set alone would reject valid input (Section 2, "Decisions": data in a different form).
- Speculative: `_get_available_chat_model_ids` has no `enabled`/`forbidden_for_web` filter, router ids do; "available" therefore means different things for chat models and routers if the helpers are reused as-is.
- `validate_default_model` receives the inherited stored default as well as the request value (`effective_default_model`, `:913-914`); a check keyed on "request sends default_model" versus on the effective value is a distinction the ticket draws (inherited default not re-validated).
- `TestCreateSharedProjectInSession` uses an unconfigured `MagicMock` for `llm_service`; `get_allowed_router_options` would return a truthy `MagicMock`, so the existing assertion at `:456-457` depends on how the truthiness is read. Existing tests also assert `get_allowed_chat_models.assert_called_once_with(...)` only.
- Creation is shared with provisioning (`provisioning_service.py:153`): router-present vs. router-absent behaviour changes for service-account-created projects too. `get_allowed_router_options` is not user-scoped, while `get_allowed_chat_models(actor)` is.
- Existing mechanism for the same purpose that a task statement keeps in place: creation's stored-list for router-present installs (`project_service.py:254-261`) and the strict list-to-default coupling in `update_allowed_models` (`:916-920`) — see "Task Statements Checked Against the Code" and "Consumers", item 5.
- Downgrade no-op: the existing revision `a7f3c9e1b4d6` still creates the strict check on upgrade and drops the check by name on downgrade; the ticket's no-op downgrade leaves rows NULL + default that the strict check would reject if it were ever re-created.
- Coverage gaps (Section 4): no tests for the NULL + default PATCH branch or router-aware creation; the changed behaviour has no safety net yet.
- Out-of-scope items the ticket records as found-not-caused (double `default` in `/v1/llm_models`, null `allowed_models` in create response) are confirmed present in code and remain.

---

## 7. Summary for Complexity Assessment

The change touches three layers: one Alembic revision in `src/external/alembic/versions/` (relax a single named CHECK on `codemie.applications`; no data change), and two edits inside one class, `ProjectService` in `src/codemie/service/project/project_service.py`: a default-model validation in the `allowed_models is None` path of `update_allowed_models`/`validate_default_model` (plus one `ERRORS` entry), and a router-aware branch in the models block of `create_shared_project_in_session`. The read side (policy, list filters, default selection, `/v1/llm_models`) already handles NULL + default by code reading, so no consumer files appear to need edits; the file change surface is about three source files plus tests.

Technical novelty is low: all patterns (Alembic check constraints, `ExtendedHTTPException` 400s, `ERRORS` namespace, existing helpers `_get_available_*`, `model_aliases`, `get_allowed_router_options`) already exist in these files. The one non-obvious fact is identifier spelling: stored chat defaults are `deployment_name`, router entries are `base_name`, and the UI sends `base_name`; the available-id helpers each cover one spelling. Creation is shared with `/v1/provisioning`.

Test posture: the area has mock-based tests of the right shape (`test_project_service.py`, `test_project_model_availability.py`, `test_projects_router.py`), but none for the NULL + default PATCH branch, router-aware creation, or the migration. Key risks: identifying the real Alembic head, id-spelling and "available" semantics in default validation, the existing creation test's `MagicMock` behaviour, and the provisioning entry point sharing the creation path.

---

## 8. External References

- Jira https://jiraeu.epam.com/browse/EPMCDME-17119 — unresolved: could not be fetched from this environment. Its backend brief was supplied locally and is treated as the ticket's content; facts used are attributed to "the ticket EPMCDME-17119" in Section 2 and are checked against the code there.
- Facts the ticket gives that the spec needs (quotable):
  - Stored states: (NULL, NULL) all models/platform default; (list, set) only these; (NULL, set) new state, all models including future ones with a project default; (list, NULL) invalid, stays forbidden.
  - Change 1: new Alembic revision on the current head; `upgrade()` drops `ck_model_whitelist_requires_default` and re-creates it as `allowed_models IS NULL OR default_model IS NOT NULL`, schema `codemie`; `downgrade()` a no-op with a comment; no data changes.
  - Change 2: in `update_allowed_models`, when `validated_models is None` and the request sends `default_model`, reject unless it is an available chat model or router (400); accept by `base_name` or `deployment_name`; do not re-validate an inherited default when the request omits `default_model`; new `ERRORS` message e.g. `DEFAULT_MODEL_NOT_AVAILABLE="default_model is not an available chat model: {model}"`; do not reuse `ensure_chat_model_allowed`; `PATCH {allowed_models: null, default_model: X}` stores NULL + X; `PATCH {allowed_models: null}` keeps the stored default; list branch and `reset_allowed_models` unchanged.
  - Change 3: in `create_shared_project_in_session`, default stays `validated_models[0]`; if `llm_service.get_allowed_router_options(include_all=False)` is empty store `allowed_models = None`, else store the list as today; keep `validate_allowed_models`/`ensure_chat_model_allowed` on the computed list in both branches; update the code comment at the models block.
  - Out of scope: migrating existing list projects; double `default` flag in `/v1/llm_models?project_id=`; create-project response returning `allowed_models: null`; CLI and LLM proxy unaffected.
  - Local evidence (unverified here): with the check relaxed by hand, PATCH NULL + `gpt-4.1-mini` -> 200, policy `unrestricted` with that default, execution resolution and missing-model fallback return it; before relaxing, 500 `CheckViolation`; `PATCH {allowed_models: null, default_model: "does-not-exist"}` -> 200 today.
