# Technical Research

**Task**: project settings models disabled default_model
**Generated**: 2026-11-18
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-17444, "Backend: Disabled Models Rejected in Project Settings" (subtask of EPMCDME-17321).

Context: A model's Disabled or Enabled state is applied on the next request, with no catalog reload. Project-scoped model lists and run-time use already skip disabled models. A new project is stored as "all models" (EPMCDME-17119), but its default model is picked from the list that includes disabled models, so the stored default can be a disabled model. The checks behind a project's model list and default model do not look at the model's status. Choosing a new disabled default is already refused, but a disabled default that is already stored is kept on save. A project stored as "all models" gets a re-enabled model without any write. A project with an explicit list never gets it.

Story: As a platform administrator, I want the platform to refuse a disabled model as a project's default or as a newly added model, and never pick one for a new project, so that no project is set up with a model the platform has turned off.

AC1: Given a model is Disabled, when a new project is created, then its default model is an enabled model and the project is still stored as "all models".
AC2: Given a model is Disabled, when a project's default model is set to it through the project settings, then the change is rejected with a message that names the model and asks for an available one.
AC3: Given a model is Disabled, when it is newly added to a project's model list through the project settings, then the change is rejected with a clear message.
AC4: Given a project's stored list already contains a model that is now Disabled, when other changes to the list are saved, then the save succeeds and the disabled name stays in the stored list (D2).
AC5: Given a project's stored default model is now Disabled, when the project's model settings are saved without choosing a new default, then the save is rejected with a message asking for an available default (D4).
AC6: Given a project is stored as "all models", when a Disabled model is Enabled again, then the project's available models include it with no change to the project; a project with an explicit list does not gain it.
AC7: Given models that are not Disabled, when any model is disabled or enabled, then creating projects and saving their settings behave as before.

Out of scope: NG1 any screen change (codemie-ui covers the Models tab); NG2 converting old projects holding a full copy of the list (D5); NG3 run-time behaviour of a disabled model already used by an assistant/workflow/chat; NG4 the model list used by the read-only models catalog page and workflow model-id handling (D7).

Decisions: D1 a project uses all models when stored as unrestricted. D2 a disabled name already stored on a project is kept and ignored while disabled; saving other changes never fails because of it. D4 a disabled default is not a valid default; the next save needs an available default. D6 enabled state is checked live; no catalog reload. D10 project settings checks reuse the platform's existing "selectable model" check instead of adding a second status rule.

Constraints: Either repository can land first: this child rejects only newly chosen or newly added disabled names and keeps stored ones. Calls from outside the screen, such as provisioning, create projects through the same path and get the same default rule.

Open Questions: None.

---

## 2. Codebase Findings

### Existing Implementations

Purpose A: a new project gets a default model (AC1, AC7, provisioning constraint).
- `src/codemie/service/project/project_service.py:220-269` `create_shared_project_in_session`: calls `llm_service.get_allowed_chat_models(actor, include_all=True)` (:264), then `project.allowed_models = None` (:268) and `project.default_model = next(m.deployment_name for m in models if m.forbidden_for_web is not True)` (:269). The default filter is web-forbidden only; no status check.
- `include_all=True` skips `_filter_models_by_visibility`, which is where `status != DISABLED` is dropped (`llm_service.py:1228-1238`). So disabled models are in this list. Lines 266-267 also run `validate_allowed_models`/`ensure_chat_model_allowed` over the full list; the latter returns at once for None but here receives a list, and uses `_get_available_chat_model_ids` (web-forbidden and router filter only, no status).
- Callers of the creation path: `ProjectService.create_shared_project` (:165) from `rest_api/routers/projects/crud.py:206`; `service/provisioning_service.py:153` calls `create_shared_project_in_session` directly. Both share :264-269.
- `next(...)` without a default raises `StopIteration` when no model qualifies (today a web-available model must exist).

Purpose B: saving a project's model settings (AC2-AC5).
- `project_service.py:933-969` `update_allowed_models`; route `rest_api/routers/projects/management.py:95-115` (PATCH `/projects/{projectName}/allowed-models`). Flow: `validate_allowed_models` (:805, format only) -> `ensure_chat_model_allowed` (:852, web-available chat model or router in list; no status) -> `_find_project` + authorization -> `effective_default_model = default_model or project.default_model` (:948) -> `validate_default_model` (:887, in-list and CLI-only check) -> if list is None and a default is passed, `ensure_default_model_available` (:914, uses `_get_available_chat_model_ids` / `_get_available_router_model_ids`; no status for plain models; routers check `enabled` only) -> `llm_service.assert_model_selectable(effective_default_model, previous_model_name=project.default_model)` (:953-959), mapped to HTTP 400 "Model not selectable" with `details=str(exc)`.
- `llm_service.py:1442-1477` `assert_model_selectable`: returns early if `model_name == previous_model_name` (:1455) or the model is the previous one under its other catalog name (:1461). Rejects deprecated (unless `allow_deprecated`) and `not enabled or status == DISABLED` with `ModelNotSelectableError(name, "model is not available")` -> message `Model '<name>' is not selectable: model is not available`. Unknown names pass silently (:1472-1473). Routers are resolved via `get_allowed_router_options(include_all=True)`.
  - Consequence today: a new disabled default is refused (AC2 path exists, message names the model, does not ask for an available one); a stored disabled default equals `previous_model_name`, so it is kept on save (the AC5 gap).
- No code in the update path looks at statuses of names in `validated_models` (AC3 gap). No diff against the stored `project.allowed_models` is computed anywhere (`_persist_allowed_models` :1044 diffs only for activity/audit via `value_fields`).
- `reset_allowed_models` (:972) writes None/None; no validation.

Purpose C: "selectable model" rule (D10).
- `llm_service.py:960` `_is_selectable(model)` = `model.enabled and model.status == ENABLED` (staticmethod, private; also excludes DEPRECATED). Used by `set_admin_global_default` (:986, raises `ModelNotSelectableError`), `resolve_global_default` (:1010), `_execution_fallback_choice` (:660), category defaults (:949, :909).
- Public rule: `assert_model_selectable` (above), used by `conversation_service.py:1471,1478`, `routers/assistant/management.py:232,234,448,450`, `routers/assistant/_model_selection.py:32`, `routers/workflow.py:211-233` (`_assert_workflow_model_selections_selectable`), and `project_service.py:955`. Each maps `ModelNotSelectableError` to an HTTP error in its own router/service.
- `llm_service.py:361` / `:1237` / `:1397` / `:637` also test `ModelStatus.DISABLED` directly (visibility filter, image models, `_is_disabled_for_execution`). Status is merged live on every `get_all_llm_model_info()` call via `_merge_persisted_status` (:1099, :104-113), no reload.

Purpose D: project's available model list (AC6).
- `llm_service.py:1247` `_filter_models_by_project` -> `ProjectModelPolicy.for_application(project, require_default=False)`; `allowed_models is None` -> `UNRESTRICTED` (`allows()` always True) (`service/llm/model_availability_service.py:115-135, 176`). Chat list is computed per request (`get_allowed_chat_models` :1321), disabled removed by `_filter_models_by_visibility` when `include_all=False`. So AC6 for "all models" holds by construction; explicit list stores names and `allows()` is membership, so it does not gain new names. Run time: `ModelAvailabilityService.resolve_model_for_execution` (:257) then `llm_service.resolve_disabled_model` (:689) replaces disabled models using `_execution_fallback_choice` (project default if selectable among candidates, else global default).
- `project_visibility_service.py:204-205` reads `allowed_models`/`default_model` for display only.

Other touched code: `core/exceptions.py:292` `ModelNotSelectableError(base_name, reason)`; tests listed in Section 4.

### Decisions Already in the Code
- Convention: status is enforced by `LLMService.assert_model_selectable`, which maps to HTTP 400 in the caller (`project_service.py:958-959`: code 400, message "Model not selectable", details = exception text). The project path reuses it already (aligns with D10).
- Decision: an unchanged selection is exempt from the status check (`llm_service.py:1455, 1461`; "An existing selection may be retained on edit", docstring :1450). Alternative: always check. Reason recorded for assistants/conversations edits; none found for projects. This is the direction AC5 reverses for the project default only (D4).
- Decision: unknown names pass `assert_model_selectable` (:1472); unknown-name rejection lives in `ensure_default_model_available` only when `allowed_models is None` (:950).
- Decision: project settings store deployment names (`model_aliases`, `model_availability_service.py:42-52`, docstring :58), while `assert_model_selectable` matches `base_name` or `deployment_name` (:1458), so one check covers both forms.
- Decision: `include_all=True` means "include web-forbidden and disabled" in `get_allowed_chat_models` (:1228-1230). New-project default relies on it (comment `project_service.py:260-263`).
- Stored in one direction, needed in another: allowed list is stored as deployment names, with no per-name "added at" record. AC3/AC4 need "newly added" = names not in the stored list; derivable from `project.allowed_models` (already loaded at :945) compared with the request, with alias matching via `model_aliases`.
- Decision D2/NG: disabled names stored in a list are ignored by `resolve_disabled_model` at run time (no stored-list repair exists).

### Task Statements Checked Against the Code
- "Project-scoped model lists and run-time use already skip disabled models": holds for `include_all=False` lists (`llm_service.py:1237`) and run time (`:689`); contradicted for `include_all=True` callers, which keep disabled models (`:1228`), including the create path.
- "Default model is picked from the list that includes disabled models": holds (`project_service.py:264,269`).
- "Checks behind a project's model list and default model do not look at status": holds for `ensure_chat_model_allowed` (:852), `validate_default_model` (:887), `ensure_default_model_available` (:914), `_get_available_chat_model_ids` (:981). Partly contradicted: `assert_model_selectable` at :955 does look at status, but only for a changed default; routers in `_get_available_router_model_ids` check `enabled` only.
- "Choosing a new disabled default is already refused": holds (:953-959, `llm_service.py:1476`); the message does not ask for an available model, only "is not selectable: model is not available".
- "A disabled default already stored is kept on save": holds via `previous_model_name` early return (`llm_service.py:1455`) with `effective_default_model` falling back to `project.default_model` (:948).
- "'All models' project gets a re-enabled model without any write; explicit list never does": holds (`model_availability_service.py:125-126, 176`; list filter :1260).
- D10 "existing selectable model check": keeps `assert_model_selectable` and `_is_selectable` in place; consumers listed in Existing Implementations, Purpose C. `_is_selectable` is private and also rejects deprecated.
- Constraint "provisioning creates projects through the same path": holds (`provisioning_service.py:153` -> `create_shared_project_in_session`).
- NG4 "model list used by the read-only models catalog page and workflow model-id handling": unverified for exact functions; `routers/llm_models.py` and `routers/workflow.py:211` exist and share `get_allowed_chat_models`/`assert_model_selectable`.

### Consumers of Changed Contracts
- `assert_model_selectable` (if its exemption or message changes): consumers at `conversation_service.py:1471,1478`, `assistant/management.py:232-234,448-450`, `assistant/_model_selection.py:32`, `workflow.py:230`, `project_service.py:955`, `admin.py` (via `set_admin_global_default`, separate path). Each expects the previous-name exemption; AC7 and NG3 require them unchanged.
- `get_allowed_chat_models(..., include_all=True)`: other callers not enumerated here beyond `project_service.py:264`; grep `include_all=True` before changing semantics. Test `test_project_service.py:457` asserts `assert_called_once_with(service_account, include_all=True)`.
- `create_shared_project_in_session`: `create_shared_project` (REST `projects/crud.py:206`), `provisioning_service.py:153`.
- `update_allowed_models` / `ProjectService.ERRORS`: single REST consumer `projects/management.py:110`; message constants at `project_service.py:151-161`.
- `ensure_chat_model_allowed`, `validate_default_model`, `ensure_default_model_available`: used by the update path (and :266-267 for create); `validate_default_model`/`ensure_default_model_available` have direct tests (`test_project_service.py:979-1002, 1037-1074`).

### Architecture and Layers Affected
- Service layer: `ProjectService` (project create, settings update), `LLMService` (selectability rule). Router layer: only exception mapping already in place (`projects/management.py`). No model/migration (`allowed_models`, `default_model` columns exist, Alembic `a7f3c9e1b4d6`, `c8d2f6a1b9e3`).

### Integration Points
- `ProjectService` -> `llm_service` (lazy imports at :920, :992 to avoid cycles; top-level `llm_service` also used at :264, :955). `llm_service` -> `model_availability_service` (lazy). Provisioning service -> `ProjectService`.

### Patterns and Conventions
- `ExtendedHTTPException(code=400, message=..., details=...)`; messages in `ProjectService.ERRORS` constants (:151-161). Classmethods/staticmethods on `ProjectService`; `ModelNotSelectableError` converted at the service/router boundary. Guides: `.ai-run/guides/architecture/service-layer-patterns.md`, `development/error-handling.md`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/` exists (listed in AGENTS.md); relevant: service-layer-patterns, error-handling, api/provisioning-api.md (create path), testing guides. Not re-read in full; no guide dedicated to project model availability found by name.

### Architectural Decisions
- Inline docstrings in `assert_model_selectable` and `resolve_disabled_model` record the retain-existing selection rule. No ADR found.

### Derived Conventions
- Status is evaluated live via `_merge_persisted_status`; callers do not cache.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/project/test_project_service.py`: creation default (:443-481), `validate_default_model` (:979-1002), `update_allowed_models` (:1011-1074), router-count tests (:874, :894).
- `tests/codemie/service/test_llm_service.py`, `tests/codemie/service/llm/test_disabled_model_fallback.py`: selectability and fallback.
- `tests/codemie/rest_api/routers/test_projects_router.py`, `test_project_model_availability.py`, `test_project_model_availability_integration.py`.

### Testing Framework and Patterns
- pytest with `MagicMock`/`patch.object(ProjectService, ...)`, `SimpleNamespace` projects, `mock_llm_service` fixture and `_prepare(...)` helper for creation tests. Tests are written only when requested (AGENTS.md).

### Coverage Gaps
- No found test that a disabled model is skipped as the new-project default, that a disabled model in a new list is rejected, or that a stored disabled default is rejected on save.

---

## 5. Configuration and Environment

### Environment Variables
- `LLM_PROXY_ENABLED` selects the catalog source (`llm_service.py:105, 1165`); `LLM_PROXY_MODE` elsewhere.

### Configuration Files
- Feature flag `projectModelOverride` (`PROJECT_MODEL_OVERRIDE_FEATURE`, `model_availability_service.py:33`) via `customer_config`: when off, list/policy filters bypass the project; the update/create code in `project_service.py` does not consult it.

### Feature Flags and Deployment Concerns
- No migration or deployment change evident; persisted status comes from the model-status store merged live.

---

## 6. Risk Indicators

- Existing exemption keeps a mechanism in place: `assert_model_selectable`'s `previous_model_name` early return (`llm_service.py:1455,1461`) is what keeps a stored disabled default (AC5); it is shared with conversation, assistant and workflow edits (see Consumers). 
- AC4 vs AC3 need a "newly added" notion: `update_allowed_models` has no diff against `project.allowed_models`; aliases (base vs deployment name) complicate comparison (`model_aliases`).
- AC5 vs AC4 conflict surface: default is rejected when stored-and-disabled while list names are tolerated; the default must also be in the list (`validate_default_model` :900), so a list save with an unchanged disabled default and unchanged list is rejected per D4.
- Speculative: the new-project default path (:269) has the `next(...)` without a fallback; if every web model is disabled it raises `StopIteration` (500). Today the same exists when no web model exists.
- Speculative: `ensure_chat_model_allowed` at create (:267) counts disabled models toward "has a web chat model"; not an AC.
- Router names: `_get_available_router_model_ids` ignores router `status` (only `enabled`); `assert_model_selectable` resolves routers with computed status, so router handling differs between the two checks.
- Unknown names pass `assert_model_selectable` silently (:1472); AC3 for list entries relies on a name being found in the catalog.
- Message for AC2 ("asks for an available one") differs from existing `ModelNotSelectableError` text; changing shared text affects other routers/tests (`test_llm_service.py`, router tests).
- Cross-repo: UI lands independently (constraint); only newly chosen names may be rejected.
- Low coverage on the new-project default with disabled models.

---

## 7. Summary for Complexity Assessment

The change sits in the service layer: `ProjectService.create_shared_project_in_session` (one expression picking the default, shared by REST and provisioning) and `ProjectService.update_allowed_models` (validation order and new-vs-stored comparison), plus possibly the shared `LLMService.assert_model_selectable`. No schema, router, or config change appears in the existing code; the router already maps `ModelNotSelectableError` to 400. AC6 and AC7 describe current behaviour (unrestricted project filter is computed per request; no write).

Technical novelty is low: the platform already has a selectable-model check and an HTTP mapping convention; the novel part is distinguishing newly added names from stored ones, and rejecting a stored disabled default despite the shared "unchanged selection" exemption, which other features rely on. Name forms (deployment vs base) and routers are the main subtlety.

Test posture: `test_project_service.py` has mock-based coverage for create and update, but none for disabled models in these paths. Key risk is the shared exemption and message text in `assert_model_selectable` used by conversation, assistant and workflow flows (AC7, NG3).

---

## 8. External References

None named by the task.
