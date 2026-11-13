# EPMCDME-17119: "all models incl. future" project state Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Acceptance criteria

- [ ] `applications` accepts (`allowed_models` NULL, `default_model` set); (list, NULL) is still rejected by the DB check; (NULL, NULL) and (list, set) are unchanged.
- [ ] `PATCH /projects/{p}/allowed-models` with `{allowed_models: null, default_model: X}` returns 200 and stores NULL + X when X is an available chat model or router (by `base_name` or `deployment_name`); otherwise 400 with `default_model is not an available chat model: X`.
- [ ] `PATCH {allowed_models: null}` (no default) keeps the stored default and does not re-validate it, even if it was removed from the platform.
- [ ] List branch of PATCH and `DELETE` (`reset_allowed_models`) behave exactly as before.
- [ ] A newly created project (also via `/v1/provisioning`) stores `allowed_models=None` (all models and routers, including ones added later) and `default_model=<first web-available chat model>`, whether or not routers exist; creation still fails when no chat model is available.
- [ ] No data is changed by the migration; `alembic heads` shows a single head.

**Goal:** Let a project be stored as "all models, including future ones, with a project default".

**Architecture:** Relax one DB check via a new Alembic revision; add an availability check for the request's `default_model` in the NULL-list branch of `ProjectService.update_allowed_models`; make project creation always store NULL. Readers already handle (NULL, set).

**Tech Stack:** Python, FastAPI, SQLModel, Alembic, pytest (mock-based).

**Spec:** none (requirements inline in the task); technical context in `technical-analysis.md` next to this file.

## Global Constraints

- Commit per task using the repository's existing convention. No other git operations.
- No explanatory code comments beyond the requested `downgrade()` comment and keeping the models-block comment accurate; no feature docs or `docs/<feature>.md`.
- Tests are mock-based, matching `tests/codemie/service/project/test_project_service.py`, `tests/codemie/rest_api/routers/test_project_model_availability.py`, `tests/codemie/rest_api/routers/test_projects_router.py`. No real-Postgres tests.
- Validation (`make ruff`, per `.ai-run/guides/quality-gates.md`) is run by the calling flow after the plan; do not add a task for it.

## Decisions

- **Identifier spelling.** The request default is accepted when `model_aliases(default_model)` intersects `_get_available_chat_model_ids(all) | _get_available_router_model_ids(all)`. Chat ids are `deployment_name`, router ids are `base_name`; `model_aliases` expands a catalogue name to both spellings, so the UI's `base_name` and creation's `deployment_name` both pass. Unknown names (and `""`) expand to themselves and fail.
- **"Available".** Exactly what the existing helpers mean: chat ids have no `enabled`/`forbidden_for_web` filter, router ids do. The helpers are reused unchanged; no new filtering is introduced.
- **Where the check lives.** A new `ProjectService.ensure_default_model_available(default_model: str) -> None` called from `update_allowed_models` only when `validated_models is None and default_model is not None` (the request value, not `effective_default_model`). `validate_default_model` is not changed, so inherited stored defaults are never checked against the catalogue.

## Task 1: Alembic revision relaxing `ck_model_whitelist_requires_default`

**Files:**
- Create: `src/external/alembic/versions/c8d2f6a1b9e3_relax_model_whitelist_default_check.py`

**Interfaces:** Produces revision `c8d2f6a1b9e3`, `down_revision = "aad1456a4f17"` (the only revision no other file lists as its `down_revision`, found by scanning all version files; re-confirm with `alembic heads` before finishing and use that id if it differs).

Test-first: no — constraint DDL has no mock-testable seam and the area has no migration tests; verified by `alembic heads` single-head check plus static review against `a7f3c9e1b4d6_add_project_level_model_availability.py:74-80`.

- [ ] **Step 1:** Create the revision file with the repo's license header and the typed `revision`/`down_revision` style of `aad1456a4f17_add_run_attributes.py`. `upgrade()`: `op.drop_constraint("ck_model_whitelist_requires_default", "applications", schema="codemie", type_="check")` then `op.create_check_constraint("ck_model_whitelist_requires_default", "applications", "allowed_models IS NULL OR default_model IS NOT NULL", schema="codemie")`. `downgrade()`: `pass` with a comment that the strict check cannot be restored without changing rows saved as NULL + default, and that code from before this change already reads that state. No `op.execute`.
- [ ] **Step 2:** Run `alembic heads` (from `src/external/alembic`'s configured entry; see `.ai-run/guides/development/setup-guide.md`) and confirm exactly one head, `c8d2f6a1b9e3`.

## Task 2: Validate the request default when `allowed_models` is NULL

**Files:**
- Modify: `src/codemie/service/project/project_service.py` (`ERRORS` at `:151-155`; new method near `validate_default_model` `:877-895`; call site in `update_allowed_models` `:909-914`)
- Test: `tests/codemie/service/project/test_project_service.py`

**Interfaces:** Produces `ERRORS.DEFAULT_MODEL_NOT_AVAILABLE = "default_model is not an available chat model: {model}"` and `ProjectService.ensure_default_model_available(default_model: str) -> None` (classmethod, raises `ExtendedHTTPException(code=400, message=ERRORS.DEFAULT_MODEL_NOT_AVAILABLE.format(model=default_model))`).

Test-first: yes — `update_allowed_models(user, "p", None, "does-not-exist")` currently stores it and returns; the new test expects a 400 with the new message and `_persist_allowed_models` not called.

- [ ] **Step 1: Write failing tests** in `test_project_service.py`, patching like the existing `ensure_chat_model_allowed` / authorization tests (around `:758-800`, `:935-959`): patch `get_session`, `_find_project`, `_check_allowed_models_authorization`, `_persist_allowed_models`, and the availability inputs (`llm_service.get_all_llm_model_info`, `_get_available_chat_model_ids`, `_get_available_router_model_ids`, `model_aliases`). Cases:
  - `test_null_list_rejects_unknown_default`: default `"does-not-exist"` -> 400, message `default_model is not an available chat model: does-not-exist`, persist not called.
  - `test_null_list_rejects_empty_string_default`: `""` -> 400.
  - `test_null_list_accepts_default_by_deployment_name`: chat id `gpt-4o-deployment` -> persisted with `(None, "gpt-4o-deployment")`.
  - `test_null_list_accepts_default_by_base_name`: `model_aliases("gpt-4o")` returns `{"gpt-4o", "gpt-4o-deployment"}` -> persisted with `(None, "gpt-4o")`.
  - `test_null_list_accepts_router_default`: router base_name in router ids -> persisted.
  - `test_null_list_omitted_default_keeps_stored_unchecked`: stored default `"removed-model"`, request default `None` -> persisted with `(None, "removed-model")`, availability helpers not called.
  - `test_list_branch_unchanged_does_not_call_catalogue_check`: list + default in list -> `ensure_default_model_available` not called.
  - `test_reset_allowed_models_still_clears_both`: persists `(None, None)`.
- [ ] **Step 2:** Run `pytest tests/codemie/service/project/test_project_service.py -k "null_list or reset_allowed" -v`; expect the rejection cases to FAIL.
- [ ] **Step 3:** Add the `ERRORS` entry and `ensure_default_model_available` (lazy-import `llm_service` and `model_aliases` like `ensure_chat_model_allowed` `:863`; build `all_models = list(llm_service.get_all_llm_model_info())`; reject unless `model_aliases(default_model) & (chat_ids | router_ids)`). In `update_allowed_models`, after `validate_default_model(...)` add the call guarded by `validated_models is None and default_model is not None`.
- [ ] **Step 4:** Re-run the command from Step 2 plus `pytest tests/codemie/rest_api/routers/test_project_model_availability.py tests/codemie/rest_api/routers/test_projects_router.py -k "allowed_models or default_model" -v`; expect PASS (existing list-branch regressions at `test_projects_router.py:3734`, `:3780` must stay green).

## Task 3: Store `allowed_models=None` at project creation

**Files:**
- Modify: `src/codemie/service/project/project_service.py` (`create_shared_project_in_session`, models block)
- Test: `tests/codemie/service/project/test_project_service.py` (`TestCreateSharedProjectInSession`)

**Interfaces:** None new. The router list is not consulted: `get_allowed_chat_models` never returns routers, so a list built from it would switch every router off.

Test-first: yes — the existing creation tests expect `allowed_models == ["gpt-4o", "claude-3"]`; they now expect `None` with `default_model == "gpt-4o"`.

- [ ] **Step 1: Update the existing tests** `test_initializes_allowed_and_default_models` and `test_new_project_allows_cli_only_models_but_defaults_to_a_web_one`: assert `project.allowed_models is None` and `default_model == "gpt-4o"`. No new tests: the validation calls are unchanged.
- [ ] **Step 2:** Run `pytest tests/codemie/service/project/test_project_service.py::TestCreateSharedProjectInSession -v`; expect the two updated tests to FAIL.
- [ ] **Step 3:** In the models block keep `validate_allowed_models` and `ensure_chat_model_allowed` on the computed list; set `project.allowed_models = None` and leave `project.default_model` as the first web-available model. Update the comment above the block: NULL means every model and router, including future ones, and only the default is stored.
- [ ] **Step 4:** Re-run Step 2's command plus `pytest tests/codemie/service/test_provisioning_service.py -v`; expect PASS (provisioning shares this path).

## Self-review notes

- negative-constraints: (list, NULL) stays forbidden -> Task 1 check keeps `default_model IS NOT NULL` for lists, app guard `update_allowed_models` untouched; "no data changes" -> Task 1 has no `op.execute`; "do not re-validate inherited default" -> Task 2 guard uses the request value and has an explicit test; "do not reuse `ensure_chat_model_allowed` for the default" -> Task 2 uses a new method and message; "no real-Postgres tests" -> Tasks 2-3 mock-only, Task 1 static; Out of scope (existing list projects, `/v1/llm_models` double `default`, create response `allowed_models`, CLI/proxy) -> no task touches them; no docs or scratch-file references anywhere.
- Spec coverage: change 1 -> Task 1, change 2 -> Task 2, change 3 -> Task 3; identifier-spelling and "available" semantics -> Decisions.
