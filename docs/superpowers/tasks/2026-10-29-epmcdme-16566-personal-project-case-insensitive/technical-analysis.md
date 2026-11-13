# Technical Research

**Task**: personal project case-insensitive membership default project user_projects
**Generated**: 2026-10-29
**Research path**: filesystem (codegraph unavailable)

---

## 1. Original Context

EPMCDME-16566 - User Management shows no default project for users whose IdP email contains uppercase letters, because personal-project setup creates an orphan membership row.

Description: For a user whose IdP-provided email contains uppercase letters, the User Details popup shows no Default project selected. It lists one project (the personal project, mixed-case name) with an empty Default radio. Root cause is in `personal_project_service.ensure_personal_project_async` / `_create_personal_project`, which run on login and mix case-sensitive and case-insensitive lookups:

1. A personal application exists under a mixed-case name `Mixed.Case@epam.com`; the user's stored email is lowercase `mixed.case@epam.com`.
2. `_create_personal_project` checks the application with `aget_by_name` (case-sensitive), misses, and takes the create branch.
3. `aget_or_create` is case-insensitive: it returns the existing mixed-case application and rewrites it as personal with description "Personal Project for mixed.case@epam.com". The name stays mixed-case.
4. The membership check `aget_by_user_and_project` is case-sensitive and misses; `aadd_project` inserts a second `user_projects` row with the lowercase name. No application has that name and there is no FK from `user_projects.project_name` to `applications`, so the insert succeeds.

The user ends with two memberships: the real mixed-case project and an orphan lowercase one.

Why the Default radio is empty: `resolve_default_project` computes the default from all memberships. The orphan lowercase name equals the user's email, so it wins. The admin detail response drops rows with no matching application (`if not info: continue`), so every returned row has `is_default: false`.

Impact: admins see no default; the effective default points at a membership with no budget allocations, requests fall back to the personal email budget and usage attribution can report a project that is not the one charged; any user with an uppercase email in IdP data can be affected; prod count unknown.

Steps to reproduce: create user `mixed.case@epam.com`; create application `Mixed.Case@epam.com` (personal, created_by the user) with one `user_projects` row; call `ensure_personal_project_async(user_id, "mixed.case@epam.com")`; a second lowercase row now exists; `GET /v1/admin/users/<id>` returns one project with `is_default: false` (before the call it returned true). Prod check not yet run: `select project_name, is_default from user_projects where user_id = '<id>'`.

Expected: no duplicate or orphan membership, personal project matched case-insensitively; user details show exactly one default, pointing at the real mixed-case personal project.

Affected areas: `personal_project_service.py`; `aget_by_name` / `aget_or_create`; `aget_by_user_and_project` / `aadd_project`; `resolve_default_project`, `_unfunded_project`; admin API `GET /v1/admin/users/<id>` visibility filter; frontend User Details Default radio; budget attribution and usage analytics.

Acceptance criteria:
1. A user whose email has uppercase letters gets exactly one personal-project membership, with the project's real name, and no orphan row is created.
2. Admin user details show exactly one default project for such users.
3. Unit tests cover mixed-case emails in `ensure_personal_project_async`, `resolve_default_project`, and `_unfunded_project`.
4. Existing orphan rows on prod are found and removed, with a count reported.
5. Budget attribution for these users names the project actually charged.

Related: EPMCDME-15738, EPMCDME-16466, EPMCDME-16520. Reproduced locally on DB `codemie_13933` (seeded user id 6f8a7504-a0ca-4b59-8733-9ef8da433281).

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/service/project/personal_project_service.py`: the bug site.
  - `ensure_personal_project_async(user_id, user_email)` (45): isolated session, `_has_personal_project_complete` else `_create_personal_project`, then an activity event with `entity_id=user_email`; non-blocking, returns False on any exception.
  - `_has_personal_project_complete` (152): case-sensitive `aget_by_name`, requires PERSONAL and `created_by == user_id`, then case-sensitive `aget_by_user_and_project`.
  - `_create_personal_project` (183): case-sensitive `aget_by_name`; existing app path checks other users via `aget_by_project_name(user_email)` and converts to personal; missing app path calls case-insensitive `aget_or_create` (returns the mixed-case app), then case-sensitive `aget_by_user_and_project(user_email)` misses and `aadd_project(user_email)` inserts the orphan.
  - `reconcile_personal_project_on_email_change` (111): `aget_by_name(old_email)` and `aremove_project(old_email)` exact; same case issue; should also clean orphans.
- `src/codemie/repository/application_repository.py`: `aget_by_name` (812, exact), `aget_by_name_case_insensitive` (818), `aget_active_by_name_case_insensitive` (824, filters soft-deleted, prefers exact case), `aget_or_create` (899, case-insensitive), sync twins `get_by_name_case_insensitive` (167), `get_project_types_bulk` (516, exact-name keys).
- `src/codemie/repository/user_project_repository.py`: async `aget_by_user_and_project`, `aget_by_project_name`, `aadd_project`, `aremove_project` all exact; no case-insensitive variant. `_filter_projects_by_visibility` (440) does `project_info.get(project.project_name)` and skips rows with no application, which hides the orphan. `get_by_user_id` (30) has no join to applications.
- `src/codemie/rest_api/security/user.py`: `resolve_default_project` (31) uses `email in names` (exact), else `min(names)`; `resolve_membership_default` (49); `User.current_project` (140).
- `src/codemie/service/llm_service/utils.py`: `_unfunded_project` (71) guard `project == user.email` (80, exact); `set_llm_context` sets `personal_project=user.email`.
- `src/codemie/enterprise/litellm/llm_factory.py`: `_record_direct_budget_attribution` (531) compares `project == personal_project` (538); line 624 `runtime_context.current_project != user_email`.
- `src/codemie/service/user/user_management_service.py` (195) derives the default from all memberships but lists only visible ones: the display mismatch.
- Callers of `ensure_personal_project_async`: `authentication_service.py` (396, 788), `registration_service.py` (215, 252), `enterprise/migration/coordinator.py` (104), and reconcile. Callers of reconcile: `authentication_service.py:586`, `user_profile_service.py:202`.
- Existing pattern to follow: `user_repository.py` (77, 599) already does `func.lower(UserDB.email) == email.lower()`.

### Architecture and Layers Affected
- Service: `personal_project_service`, `authentication_service`, `registration_service`.
- Repository: `application_repository`, `user_project_repository` (singletons with sync and `a*` async twins).
- Pure-function layer: `security/user.py` default resolution.
- Budget attribution layer: `llm_service/utils.py`, `enterprise/litellm/llm_factory.py`, `LiteLLMContext.personal_project` (`rest_api/models/settings.py:198`).
- Data: `UserProject` (`rest_api/models/user_management.py`) has `UniqueConstraint('user_id','project_name')`, case-sensitive, plus a partial unique index for one default per user. Alembic migrations in `src/external/alembic/versions/`.

### Integration Points
- Login flows (IdP, local, registration, migration coordinator) all call `ensure_personal_project_async`.
- Admin user detail, user list, profile and access routers consume `resolve_membership_default`.
- LiteLLM budget routing consumes `user.email` as the personal project name.

### Patterns and Conventions
- Repository singletons with sync and `a*`-prefixed async twins; isolated `get_async_session()` for personal-project creation; non-blocking try/except returning bool.
- Case-insensitive lookup via `func.lower(col) == name.lower()`.
- Never log email (PII): log user_id and project_type only.
- Alembic data migrations use raw `sa` / `op.get_bind()` and log counts.
- Apache license header on new files; commit prefix `EPMCDME-NNNNN:`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/`: data/repository-patterns.md, data/database-patterns.md, architecture/service-layer-patterns.md, testing/testing-patterns.md, testing/testing-service-patterns.md, development/logging-patterns.md, development/error-handling.md, quality-gates.md, standards/git-workflow.md. Not read in detail yet.

### Architectural Decisions
- `resolve_default_project` docstring (EPMCDME-15738): the fallback is never persisted; every reader derives it, so the UI shows exactly one default and runtime follows what it shows.
- `_unfunded_project` (EPMCDME-15111 AC6/AC10): attribution decided at context creation.
- Personal project name is the user's email (FR-7.1, Story 9).

### Derived Conventions
- A project's canonical name is `Application.name`; membership rows should carry exactly that string.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/project/test_personal_project_service.py`: ensure, complete-check, create, concurrent creation, reconcile. No mixed-case tests.
- `tests/codemie/rest_api/security/test_user.py`: `TestResolveDefaultProject` (257), `TestUserProperties.test_current_project_*` (151-190).
- `tests/codemie/service/llm_service/test_utils_litellm_context.py`: `TestUnfundedProjectAtContextCreation` (606), `_user()` and `_run(scopes)` helpers.
- `tests/codemie/repository/test_user_project_repository_*.py` (basic, bulk_filter, default, extended, rename_cascade, story18, visibility).
- Callers: `test_authentication_service.py`, `test_registration_service.py`, `test_user_profile_service.py`, `tests/enterprise/migration/test_coordinator.py`, `test_user_router.py`, `test_user_profile_router.py`, `test_local_idp.py`.

### Testing Framework and Patterns
- pytest 8 with pytest-asyncio. Personal-project tests use `_make_async_session_cm`, patch `codemie.clients.postgres.get_async_session`, and patch module-level `application_repository` / `user_project_repository` with AsyncMock. Repository tests use MagicMock sessions with `exec.return_value.first.return_value`. Some tests assert exact call args such as `aget_by_name.assert_called_once_with(session, user_email)`; they will need updating if lookups change.

### Coverage Gaps
- No mixed-case email test anywhere in these modules.
- No orphan-scenario test; no test that `_filter_projects_by_visibility` drops a row with no application.
- No `_unfunded_project` test with differing case between project and `user.email`.
- No test for an orphan cleanup migration or script.

---

## 5. Configuration and Environment

### Environment Variables
- `LLM_PROXY_MODE` (must be `lite_llm` for `_unfunded_project`), `LLM_PROXY_SHARED_ASSET_PROJECT_BUDGET_ROUTING_ENABLED`.

### Configuration Files
- `pytest.ini`, `pyproject.toml`, `Makefile`; gates in `.ai-run/guides/quality-gates.md`.

### Feature Flags and Deployment Concerns
- `is_personal_project_excluded(user_type)` skips personal projects for some user types.
- Orphan cleanup on prod is a data migration (`src/external/alembic/versions/`; run `alembic heads` before setting `down_revision`) or a one-off script. Model migrations: `e5f9a1c3b7d2_repair_ms_teams_migrated_settings.py` (data repair with count log), `a1b3e5d7f9c2_add_project_slug_unique_index_to_assistants.py` (lower() SQL).

---

## 6. Risk Indicators

- Data migration on prod: orphan rows must be found by joining `user_projects` to `applications` on `lower(name)` where `project_name <> name`. If the user also has the correct row, delete the orphan; otherwise rename it to the real name, taking care of the `is_default` flag and the unique constraint. Needs a dry-run count first (acceptance criterion 4).
- The prod orphan row has not been confirmed; the diagnosis rests on code reading and a local reproduction.
- Changing the unique constraint to case-insensitive would need the cleanup to run first; not planned.
- Existing tests assert exact repository call args and will break when lookups switch to case-insensitive variants.
- Attribution semantics: `LiteLLMContext.personal_project`, `_record_direct_budget_attribution` (538) and `llm_factory` 624 use `user.email` as the personal project name; changing them must keep `current_project` and `budget_fallback_from` consistent with the real charge.
- Which row wins when both a correct and an orphan row exist, and whether `is_default` is stored on the orphan.
- Soft-deleted applications (`deleted_at`) and `reconcile_personal_project_on_email_change`, which has the same case-sensitivity gap.
- `resolve_default_project` must return the member's actual project name, not the email string, when matching case-insensitively.
- Scope creep: frontend needs no change if the API returns a correct `is_default`.

---

## 7. Summary for Complexity Assessment

The change surface is the backend only: `personal_project_service.py` (canonical-name handling in ensure, complete-check, create and reconcile), `application_repository` / `user_project_repository` (case-insensitive lookup helpers), `security/user.py` (`resolve_default_project`), `llm_service/utils.py` (`_unfunded_project`, `set_llm_context`) and possibly `llm_factory.py` attribution comparisons, plus one Alembic data migration (or script) to remove or repair existing orphan rows. No API or frontend contract change is expected.

Technical novelty is low; the repository already has case-insensitive lookups and the `func.lower` pattern. The uncertainty is data: an irreversible-looking cleanup on prod needs a careful dry-run, and the orphan on prod is unconfirmed.

Test posture is reasonable: every touched module has a test file with reusable mocks and fixtures, but none covers mixed-case emails or the orphan scenario, and some existing assertions on exact lookup calls must be updated. Main concerns are the prod data migration, attribution consistency for these users, and updating existing mock-based tests.
