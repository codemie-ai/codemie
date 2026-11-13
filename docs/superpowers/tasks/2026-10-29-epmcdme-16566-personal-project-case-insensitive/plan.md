# EPMCDME-16566 Case-insensitive personal-project matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. (sdlc-task runs this inline with superpowers:test-driven-development.)

**Goal:** Stop creating an orphan lowercase `user_projects` row for users whose personal project name differs from their stored email only by case, and make default-project and budget code treat such names as the same project.

**Architecture:** One shared comparison helper `same_project_name` in `security/user.py`. `resolve_default_project` uses it and prefers the real project over a bug-created orphan. `PersonalProjectService` resolves the application case-insensitively and uses its real `name` for the membership. The three budget comparison sites (`_unfunded_project`, `llm_factory`, `proxy_router`) use the helper. No DB change, no migration, no script.

**Tech Stack:** Python 3.12, SQLModel/SQLAlchemy (async), pytest + pytest-asyncio, ruff via poetry.

**Spec:** `docs/superpowers/tasks/2026-10-29-epmcdme-16566-personal-project-case-insensitive/spec.md`

## Global Constraints

- No migration, cleanup script, schema change or case-insensitive unique index (spec Non-goals).
- Never log email addresses: log user id and project type only.
- New files carry the Apache license header (this plan creates none).
- Commit prefix `EPMCDME-16566:`. Do not commit `.codemie/codemie-cli.config.json`, `docker-compose.yml`, `.ai-run/sdlc-factory/doctor.json` or other tasks' untracked docs.
- `make` is not on PATH in bash: run `poetry run pytest ...` and `poetry run ruff check/format` directly.
- Sync and async budget probe copies stay in lockstep (only comparison sites change here).
- MR description asks a reviewer to post `/sanity` (budget routing code is touched).

## Review Focus

- A user with both the real mixed-case row and a lowercase orphan resolves to the real project (Task 1), not the orphan.
- A case-variant application owned by another user, or non-personal, is never converted or joined (Task 2).
- A case-only email change (`Foo@x.com` to `foo@x.com`) never soft-deletes the personal project or its membership (Task 2).
- A personal project whose name differs from the email only by case never produces a "fallback from shared project" attribution (Task 3).
- Exact-case users and users with no email behave exactly as before (Tasks 1-3 keep the old tests green).

---

### Task 1: Shared comparison helper and default resolution

**Files:**
- Modify: `src/codemie/rest_api/security/user.py` (`resolve_default_project` at line 31; new helper above it)
- Test: `tests/codemie/rest_api/security/test_user.py` (`TestResolveDefaultProject`, line 257)

**Interfaces:**
- Produces: `same_project_name(a: str | None, b: str | None) -> bool`, True when both are non-empty and equal ignoring case. Used by Tasks 2 and 3.
- Produces: `resolve_default_project(project_names, stored_default, email) -> str | None` with the same signature, now case-insensitive.

Test-first: yes — `test_mixed_case_email_resolves_real_project_over_lowercase_orphan` fails because the current exact `email in names` returns the orphan (or `min(names)`).

- [ ] **Step 1: Write the failing tests** in `TestResolveDefaultProject`:
  - `test_same_project_name_ignores_case`: `same_project_name("Foo@x.com", "foo@x.com")` is True; `same_project_name(None, "a")`, `same_project_name("", "")` and `same_project_name("a", "b")` are False.
  - `test_mixed_case_member_matches_lowercase_email`: names `["Mixed.Case@epam.com"]`, stored None, email `"mixed.case@epam.com"` returns `"Mixed.Case@epam.com"`.
  - `test_mixed_case_email_resolves_real_project_over_lowercase_orphan`: names `["Mixed.Case@epam.com", "mixed.case@epam.com"]`, stored None, same email returns `"Mixed.Case@epam.com"`.
  - `test_stored_default_orphan_name_resolves_real_project`: same names, stored `"mixed.case@epam.com"` returns `"Mixed.Case@epam.com"`.
  - `test_stored_default_not_in_names_is_returned_unchanged`: names `["a"]`, stored `"zzz"` returns `"zzz"` (existing behaviour).
  - `test_exact_case_unchanged`: names `["foo@x.com", "alpha"]`, email `"foo@x.com"` returns `"foo@x.com"`.
  - `test_membership_default_prefers_real_row`: `resolve_membership_default` over `SimpleNamespace(project_name=..., is_default=...)` rows for the real project (`False`) and the orphan (`True`) returns the real name.
- [ ] **Step 2: Run** `poetry run pytest tests/codemie/rest_api/security/test_user.py -q -k "ResolveDefault or same_project"`. Expected: the new tests FAIL (helper missing / wrong pick).
- [ ] **Step 3: Implement** `same_project_name` and a private `_pick_member(names: set[str], candidate: str, email: str | None) -> str | None` in `security/user.py`. `_pick_member` collects `sorted(n for n in names if same_project_name(n, candidate))`: none returns None; one returns it; several returns the first that is not exactly equal to `email`, else the first. `resolve_default_project`: if `stored_default`, return `_pick_member(names, stored_default, email) or stored_default`; if no names, None; if `email`, return `_pick_member(names, email, email)` when not None; else `min(names)`.
- [ ] **Step 4: Run** the same command. Expected: PASS, including all pre-existing `TestResolveDefaultProject` and `test_current_project_*` tests.
- [ ] **Step 5: Commit** `git add src/codemie/rest_api/security/user.py tests/codemie/rest_api/security/test_user.py` then `git commit -m "EPMCDME-16566: Resolve default project case-insensitively, preferring the real project over an orphan"`.

### Task 2: Personal-project setup uses the application's real name

**Files:**
- Modify: `src/codemie/service/project/personal_project_service.py` (`ensure_personal_project_async` line 45, `reconcile_personal_project_on_email_change` line 111, `_has_personal_project_complete` line 152, `_create_personal_project` line 183)
- Test: `tests/codemie/service/project/test_personal_project_service.py`

**Interfaces:**
- Consumes: `same_project_name` (Task 1); `application_repository.aget_active_by_name_case_insensitive(session, name) -> Application | None` (exact-case first, non-deleted); `user_project_repository.aget_by_user_and_project(session, user_id, project_name)`, `aadd_project(session, user_id, project_name, is_project_admin=False)`, `aget_by_project_name`, `aremove_project`.
- Produces: `PersonalProjectService._create_personal_project(session, user_id, user_email) -> str`, now returning the real project name. `ensure_personal_project_async` uses it as the activity event `entity_id`.

Test-first: yes — `test_create_personal_project_does_not_add_orphan_for_mixed_case_application` fails because the current code inserts a membership named by the lowercase email.

- [ ] **Step 1: Write the failing tests** (patch `application_repository` and `user_project_repository` as the existing tests do):
  - `test_has_personal_project_complete_matches_mixed_case_application`: application `Mixed.Case@epam.com` (personal, `created_by=user_id`) returned by `aget_active_by_name_case_insensitive`; membership lookup is called with `"Mixed.Case@epam.com"` and returns a row; result True.
  - `test_create_personal_project_does_not_add_orphan_for_mixed_case_application`: existing mixed-case personal application by the same user and no membership; `aadd_project` is awaited once with `"Mixed.Case@epam.com"` (never the lowercase email); the method returns `"Mixed.Case@epam.com"`.
  - `test_create_personal_project_skips_membership_when_real_row_exists`: membership lookup by real name returns a row; `aadd_project` not called.
  - `test_create_personal_project_rejects_case_variant_owned_by_another_user`: case-variant application with `created_by="other"` raises `ValueError`; no `aadd_project`, application not modified.
  - `test_create_personal_project_rejects_case_variant_non_personal`: case-variant shared application raises `ValueError`.
  - `test_create_personal_project_uses_application_name_returned_by_get_or_create`: no existing application; `aget_or_create` returns an application named `"Mixed.Case@epam.com"` (race); membership uses that name.
  - `test_ensure_uses_canonical_name_for_activity_event`: `_create_personal_project` patched to return `"Mixed.Case@epam.com"`; the inserted `ActivityEventCreate.entity_id` equals it.
  - `test_reconcile_case_only_email_change_is_noop`: `reconcile_personal_project_on_email_change(user_id, "Foo@x.com", "foo@x.com", "regular")` returns True and touches neither repository.
  - `test_reconcile_finds_old_project_case_insensitively`: old application `Old@x.com`, `old_email="old@x.com"`, `new_email="new@x.com"`; `aremove_project` is called with `"Old@x.com"` and the soft-delete is applied.
- [ ] **Step 2: Run** `poetry run pytest tests/codemie/service/project/test_personal_project_service.py -q`. Expected: the new tests FAIL; note which existing tests assert exact `aget_by_name` and need updating in Step 4.
- [ ] **Step 3: Implement.**
  - Private `_find_application(session, user_email) -> Application | None` calls `aget_active_by_name_case_insensitive`.
  - Private `_is_reusable(application, user_id, user_email) -> bool`: True when `application.name == user_email`, or when it is personal and `created_by == user_id`.
  - `_has_personal_project_complete`: use `_find_application`; require personal type and `created_by == user_id`; look up the membership by `application.name`.
  - `_create_personal_project`: find the application; if found and not `_is_reusable`, raise `ValueError` (no email in the message); if found, run the existing other-users check and conversion against `application.name`; if not found, `aget_or_create(session, user_email)` and set personal fields as today; then ensure the membership by the real `name` and return it.
  - `ensure_personal_project_async`: `entity_id` is the name returned by `_create_personal_project`.
  - `reconcile_personal_project_on_email_change`: return True immediately when `same_project_name(old_email, new_email)`; otherwise find the old application with `_find_application`, soft-delete it and call `aremove_project` with its real `name`.
- [ ] **Step 4: Update existing tests** that patch `aget_by_name` to patch `aget_active_by_name_case_insensitive` with the same return value, and tests that patch `_create_personal_project` to give it `return_value=<name string>`. Keep their assertions otherwise.
- [ ] **Step 5: Run** the file again. Expected: all PASS.
- [ ] **Step 6: Commit** `git add src/codemie/service/project/personal_project_service.py tests/codemie/service/project/test_personal_project_service.py` then `git commit -m "EPMCDME-16566: Use the application's real name for personal-project membership"`.

### Task 3: Case-insensitive personal-project detection in budget code

**Files:**
- Modify: `src/codemie/service/llm_service/utils.py:80` (`_unfunded_project`)
- Modify: `src/codemie/enterprise/litellm/llm_factory.py:538` (`_record_direct_budget_attribution`) and `:624` (`_configure_direct_runtime_overrides`)
- Modify: `src/codemie/enterprise/litellm/proxy_router.py:633` (`_flag_personal_budget_fallback`)
- Test: `tests/codemie/service/llm_service/test_utils_litellm_context.py` (`TestUnfundedProjectAtContextCreation`, line 606), `tests/enterprise/litellm/test_llm_factory.py`, the existing proxy router test module (find with `grep -rl "_flag_personal_budget_fallback" tests`)

**Interfaces:**
- Consumes: `same_project_name(a, b) -> bool` from `codemie.rest_api.security.user`.

Test-first: yes — `test_mixed_case_personal_project_is_not_a_fallback` fails because the exact `project == user.email` guard lets `Mixed.Case@epam.com` reach the probe and returns it as the fallback project.

- [ ] **Step 1: Write the failing tests:**
  - `test_mixed_case_personal_project_is_not_a_fallback` (in `TestUnfundedProjectAtContextCreation`): user email `mixed.case@epam.com`, project `Mixed.Case@epam.com`, `_run` with empty scopes returns None and the probe is not called.
  - `test_record_attribution_mixed_case_personal_project_keeps_attribution` (in `test_llm_factory.py`): `LiteLLMContext(personal_project="mixed.case@epam.com")` and `project="Mixed.Case@epam.com"`, `project_budget_used=False`; afterwards `budget_fallback_from` is None and `current_project` is the project.
  - `test_runtime_override_treats_mixed_case_personal_project_as_no_project_context`: `_configure_direct_runtime_overrides` with `current_project="Mixed.Case@epam.com"`, `user_email="mixed.case@epam.com"` takes the personal/platform path (no project context passed to `_try_apply_premium_budget`).
  - `test_flag_personal_budget_fallback_ignores_mixed_case_personal_project`: `request_info[PROJECT]="Mixed.Case@epam.com"`, user email `mixed.case@epam.com` leaves `request_info` without `BUDGET_FALLBACK_FROM`.
- [ ] **Step 2: Run** `poetry run pytest tests/codemie/service/llm_service/test_utils_litellm_context.py tests/enterprise/litellm/test_llm_factory.py <proxy router test file> -q`. Expected: the four new tests FAIL.
- [ ] **Step 3: Implement** by replacing the four exact comparisons with `same_project_name(...)`: in `_unfunded_project` the `project == user.email` clause; in `_record_direct_budget_attribution` `project == personal_project`; in `_configure_direct_runtime_overrides` `current_project != user_email` becomes `not same_project_name(current_project, user_email)`; in `_flag_personal_budget_fallback` `project in {user.username, user.email}` becomes `any(same_project_name(project, name) for name in (user.username, user.email))`. Import the helper at module top (check for a circular import; use a function-level import only if one appears).
- [ ] **Step 4: Run** the same command. Expected: PASS, including all pre-existing tests in those modules.
- [ ] **Step 5: Commit** `git add src/codemie/service/llm_service/utils.py src/codemie/enterprise/litellm/llm_factory.py src/codemie/enterprise/litellm/proxy_router.py tests/...` (list the three test files) then `git commit -m "EPMCDME-16566: Recognise a mixed-case personal project as personal in budget routing"`.

### Task 4: Sweep, lint and regression run

**Files:**
- Modify: only what the sweep finds (expected: nothing).
- Test: no new tests unless the sweep changes code.

Test-first: no — verification task; any code it changes gets a test in its own commit.

- [ ] **Step 1: Sweep** `src/codemie` with Grep for `== user.email`, `== email`, `{user.username, user.email}` and `in user.project_names`. Apply the stop rule from the spec: change only checks that decide "is this the user's personal project" or "which membership is the default"; leave access and permission checks exact. Record the findings (kept and changed) in the commit message or the MR description. Also confirm by reading `usage_attribution` (`is_personal_project`) that personal spend is joined by customer id or application type, not by project-name equality with the email.
- [ ] **Step 2: Run** `poetry run ruff check src tests` and `poetry run ruff format --check src tests` on the touched files. Fix any finding.
- [ ] **Step 3: Run** `poetry run pytest tests/codemie/rest_api/security tests/codemie/service/project tests/codemie/service/llm_service tests/codemie/service/user tests/codemie/repository -q` and `poetry run pytest tests/enterprise/litellm -q`. Expected: PASS apart from pre-existing Windows-only failures, which are compared against `origin/main` before being dismissed.
- [ ] **Step 4: Re-run the local reproduction** (optional, Docker stack): seed the mixed-case user from the spec, call `ensure_personal_project_async` with the lowercase email and confirm `user_projects` still has one row and `GET /v1/admin/users/<id>` shows `is_default: true`. The backend image must be rebuilt first (no source mount).
- [ ] **Step 5: Commit** any sweep changes with `EPMCDME-16566:` prefix; otherwise nothing to commit.
