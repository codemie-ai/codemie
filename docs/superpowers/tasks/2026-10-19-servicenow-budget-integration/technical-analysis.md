# Technical Research

**Task**: servicenow budget-groups project-admin service-account auth
**Generated**: 2026-10-19
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-16127 — ServiceNow budget integration for the CodeMie backend (this repo). Two endpoints under /v1/integrations/servicenow: GET /projects lists shared, non-deleted projects that the given emails' users belong to; POST /budgets sets/adjusts/cancels a project's budget GROUP (ProjectBudgetService group methods, same model the UI uses), resolving or creating the project (and, when epam_project_code != codemie_project_name, a cost center named after the EPAM code), and assigning requester/approver emails as project admins via ProjectAssignmentService before the budget is applied. Called by a Keycloak client-credentials service account (user_type=service_account, no admin rights); authenticate() restricts it to exactly those two routes and a route guard lets only it call them. Toggled by SERVICENOW_INTEGRATION_ENABLED and SERVICENOW_SERVICE_ACCOUNT_ID env vars. An approved spec and plan already exist at docs/superpowers/tasks/2026-10-19-servicenow-budget-integration/spec.md and plan.md — read them so your analysis validates and complements them (confirm referenced files/signatures still match current source, flag anything stale or risky) rather than re-deriving the design.

---

## 2. Codebase Findings

Baseline: `main` at `d3acb1b2a`. No `servicenow` code exists in `src/codemie` apart from the unrelated ITSM tool credentials in `service/settings/settings.py:37` and `settings_tester.py:41`. All "New" files in the spec are missing, as expected.

### Existing Implementations (plan anchors verified)

| Plan reference | Current source | Status |
|---|---|---|
| `UserRepository.get_by_emails` "~350" | `repository/user_repository.py:370`. Case-insensitive, no active filter. `get_active_by_id:95-105` has the `is_active, deleted_at.is_(None)` filter | Matches (line drift) |
| `ApplicationRepository.get_project_types_bulk` ~514, `get_by_name_case_insensitive`, `create(..., cost_center_id, display_name)` | `:514`, `:167` (**includes soft-deleted**), `:188` | Matches |
| `CostCenterService.create` ~71-96, `validate_name`, `ensure_feature_enabled`, `ensure_exists_for_project` | `service/cost_center_service.py:72-96`, `:38`, `:62`, `:125`. `create` maps `IntegrityError` to 409 | Matches |
| `cost_center_repository.get_by_name_case_insensitive` / `create(session,*,name,description,created_by)` / `get_by_id` | `repository/cost_center_repository.py:39,43,31`. The lookup includes soft-deleted rows | Matches |
| `ProjectService.create_shared_project` 132-206; `_validate_shared_project_name` ~465 | `service/project/project_service.py:132-206`, `:465`. Pattern `^[a-z0-9][a-z0-9_-]*$`, length 3–100 (`:65-70`) | Matches |
| "all 4 references" + "one test reference" to `_validate_shared_project_name` | **3 occurrences** (def `:465`, callers `:141`, `:365`), **0 in tests** | **Stale, minor** |
| `ProjectAssignmentService.bulk_assign_users_to_project(session, project, users, project_name, actor, action)` | `service/project/project_assignment_service.py:519`, a `@staticmethod` | Matches |
| `user_project_repository.get_by_users_and_project(session, user_ids, project_name) -> dict[str, UserProject]` | `repository/user_project_repository.py:489` | Matches |
| `ProjectBudgetService` group methods | `create_project_budget_group(session, data, actor_id)` `:1828`; `get_project_budget_group(session, group_id)` `:1971`; `update_project_budget_group(session, group_id, data, actor_id)` `:2059`; `delete_project_budget_group(session, group_id, actor_id)` `:2330`; singleton `project_budget_service` `:2442` | Matches |
| `PCT_SUM_TOLERANCE`, `DEFAULT_SOFT_LIMIT_PCT` | `project_budget_service.py:69-70` (0.5, 80.0) | Matches |
| `ProjectBudgetGroupFullResult` (`group`, `categories[].budget/.assignment`, `total_amount` property) | `project_budget_service.py:80-97` | Matches |
| `project_budget_group_repository.get_active_by_project(session, project_name)` | `repository/project_budget_repository.py:871`, singleton `:932` | Matches |
| `CategoryBudgetSpec`, `ProjectBudgetGroupCreateRequest` (`name` max 100), `CategoryBudgetSpecUpdate`, `ProjectBudgetGroupUpdateRequest` | `rest_api/models/project_budget.py:121-176` | Matches |
| `BudgetCategory`; `SERVICE_ACCOUNT_USER_TYPE`; `invalidate_user_from_cache` | `service/budget/budget_enums.py:20` (a duplicate enum also exists at `enterprise/litellm/budget_categories.py:20`); `rest_api/security/user_type_validator.py:29`; `service/user/authentication_service.py:73` | Matches |
| `get_session` (sync cm) / `get_async_session` (async cm) | `clients/postgres.py:260,273` | Matches |
| `authenticate()` Teams block ~150; `maintainer_access_only` ~229; `ACCESS_DENIED_MESSAGE` | `rest_api/security/authentication.py:144-150`, `:229`, `:38` | Matches |
| config banner after user management ~252 | `configs/config.py:243-249`. `TEAMS_SERVICE_ACCOUNT_ID` is at `:240` | Matches |
| `main.py` gate before `get_mcp_auth_router()` ~1099 | `rest_api/main.py:1092-1099` | Matches |

### Architecture and Layers Affected
- **Router**: a new integration router, plus the existing group router `rest_api/routers/project_budget_router.py:510-592` as the reference for the commit pattern (`create` → `commit` → `get_project_budget_group`).
- **Security**: `authentication.py` (`authenticate`, guards) and `teams_authentication_resolver.py` (the lazy-import precedent).
- **Service**: `ProjectService`, `CostCenterService`, `ProjectAssignmentService` (sync `Session`), `ProjectBudgetService` (`AsyncSession`).
- **Repository**: user, application, cost-center, user-project and budget-group repositories.
- **Config/app wiring**: `config.py`, `main.py`.

### Integration Points
- `create_project_budget_group` and `_update_group_categories` call the LiteLLM provider (`get_active_provider()`, `_sync_created_project_budget`) **inside** the async transaction, before the caller commits.
- `bulk_assign_users_to_project` → `_sync_project_budget_member_added` (`:209`). For **new** members only, it creates allocations on active budget assignments in the same sync session. Promoted members get no new allocation.
- `create_project_budget_group` → `_get_active_member_user_ids` (`:131`) reads committed memberships with `UserDB.is_active`. The spec's commit-before-budget ordering is required for equal allocations to include the new admins.
- `create_project_budget_group` → `_ensure_project_budget_eligible` (`:288`). It matches **`Application.name == project_name` (case-sensitive)**, returns 404 if missing or deleted and 403 if the project is personal.

### Patterns and Conventions
- Classmethod services with a module singleton; `ExtendedHTTPException(code, message, details, help)`; `event: key=value, domain=...` logs.
- Group update semantics (`:2059-2143`): categories omitted from the payload stay unchanged, `pct<=0` deletes the category, and every sent category with pct>0 is rewritten. `_resolve_soft_amount` (`:2221`) falls back to 80% when `soft_budget` is None. This confirms that plan Review Focus 3 (resend the current soft limit) is necessary.
- `create_project_budget_group` skips categories with `pct<=0` (`:1872`), so sending all three categories on create is safe.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists. The relevant guides are `api/rest-api-patterns.md`, `api/endpoint-conventions.md`, `development/security-patterns.md` (1.4 KB, no mention of service accounts or scoping), `development/configuration-patterns.md`, `testing/testing-patterns.md`, `testing/testing-service-patterns.md`. None covers ServiceNow or scoped service accounts.

### Architectural Decisions
- `teams_authentication_resolver.py` docstring: a standalone module because `authenticate()` is its only caller. The spec copies this pattern.
- `authentication.py:123-124`: bind-key internal calls are never subject to the external-branch logic. The scope check placed after the Teams block inherits this.
- `ProjectBudgetGroup` (`service/budget/budget_models.py:196-240`) has a **partial unique index on `project_name WHERE deleted_at IS NULL`**.

### Derived Conventions
- `user_type` is persisted only when the user is first created (`authentication_service.create_user_from_idp`, `:183-215`). Later logins read it from the DB (`load_user_for_auth`, `:140-170`). This confirms spec setup note 2.
- Project membership routes are gated by `_ensure_user_management_enabled()` (`rest_api/routers/projects/_common.py:85-87`).

---

## 4. Testing Landscape

### Existing Coverage
All test files the plan names exist: `tests/codemie/repository/test_user_repository_crud.py` (`get_by_emails` tests at `:443`), `test_application_repository_extended.py`, `tests/codemie/service/test_cost_center_service.py`, `tests/codemie/service/project/test_project_service.py`, `tests/codemie/rest_api/security/test_authentication.py` (`TestAuthenticateTeamsSenderBranch._authenticate_as` at `:569-570`), `test_teams_authentication_resolver.py`, `tests/codemie/rest_api/routers/test_cost_centers_router.py`, `test_project_budget_router*.py`, and `tests/codemie/rest_api/router_guards/` (`guarded_app.py` `PROFILE_ENV` `:52-69`, `regenerate_baselines.py`, `baselines/openapi.json` 1.9 MB, `route_precedence.json`).

### Testing Framework and Patterns
pytest with anyio, `unittest.mock` patching of module-level singletons, and a mocked `db_session` with SQL compiled via `literal_binds`. The table names the plan's SQL assertions rely on are confirmed: `users`, `user_projects`, `applications`, `cost_centers`.

### Coverage Gaps
- No test exercises `scope["route"]` in `authenticate()`, and no source module reads `scope["route"]` today. FastAPI sets it in `APIRoute.matches` (`.venv/Lib/site-packages/fastapi/routing.py:573`) before dependencies run.
- No existing test covers a concurrent budget-group create (`IntegrityError` from the partial unique index).

---

## 5. Configuration and Environment

### Environment Variables
- Existing: `ENABLE_USER_MANAGEMENT` (`config.py:246`), `TEAMS_SERVICE_ACCOUNT_ID` (`:240`), `ADMIN_USER_ID`, `ADMIN_ROLE_NAME`, `COST_CENTER_NAME_PATTERN = ^[a-z0-9]+-[a-z0-9]+$` (`:249`, exactly one hyphen), `ENV` (`local` skips the UUID check on user ids in `_validate_user_id_format`, `project_assignment_service.py:341-353`).
- The new `SERVICENOW_*` settings do not exist yet.

### Configuration Files
`configs/config.py` (pydantic settings), `rest_api/main.py` router registration, and `tests/.../router_guards/guarded_app.py` `PROFILE_ENV`. The guard profile runs with `ENABLE_USER_MANAGEMENT=true` and `IDP_PROVIDER=local`.

### Feature Flags and Deployment Concerns
- `customer_config` feature `costCenters` is checked by both `CostCenterService.ensure_feature_enabled` and `ensure_exists_for_project`.
- `_require_budgeting_enabled()` in `project_budget_router.py:214` is a no-op, so the integration has no budgeting flag to honour.
- The Keycloak IdP implementation is not in this repo (`rest_api/security/idp/` holds only local/JWKS). It is presumably in `codemie-enterprise`.

---

## 6. Risk Indicators

- **Budget-group create race is not a 409.** The pre-check 409 (`project_budget_service.py:1843-1850`) only catches a group that is already committed. A truly concurrent insert violates the partial unique index on `project_budget_groups` and raises `IntegrityError` at insert or commit. Plan Task 8 catches only `ExtendedHTTPException(409)`. Speculative: the fallback should also handle `IntegrityError` with `await session.rollback()` before re-reading. Note also that LiteLLM provider sync runs before commit, so a rolled-back create can leave provider-side budgets behind.
- **Case-sensitive eligibility check.** `_ensure_project_budget_eligible` uses `Application.name == project_name`, but resolution uses `get_by_name_case_insensitive`. Speculative: `ProjectResolution.project_name` must be the stored `project.name`, not the lowercased request name. Otherwise a legacy mixed-case project returns 404 from the budget step.
- **Implicit `ENABLE_USER_MANAGEMENT` dependency.** DB users, `user_type` persistence and memberships exist only with `PersistentUserProvider`, and project routes 400 without the flag. Neither the spec nor the plan says the integration requires it. Speculative: gate it or warn at startup.
- **Privilege bypass is deliberate but broad.** The UI's group create and delete require `maintainer_access_only`, and update limits non-maintainers to categories only (`_ensure_allowed_budget_group_update_fields`, `project_budget_router.py:176-187`, so `total_amount` is maintainer-only). Calling the service layer directly gives a non-admin account maintainer-equivalent budget authority. The `authenticate()` scope check is the only containment, so Review Focus 5 is security-critical.
- **Cost-center naming.** `COST_CENTER_NAME_PATTERN` allows exactly one hyphen. EPAM codes with two hyphens or with underscores return 400 whenever the names differ.
- **Name length mismatch.** The request schema allows names of 1 character, but the project-name rules require at least 3. Short codes return 400 from the service, not 422. This is consistent with the spec but worth a router test.
- **UUID check on user ids.** `bulk_assign` validates user ids as UUIDs outside `ENV=local`. Keycloak `sub` values are UUIDs, so this is low risk. Users come from DB rows, so the not-found 404 path cannot fire.
- **Log noise.** A 403 raised inside `authenticate()`'s `try` is re-logged as `Authentication failed with HTTP 403` with `exc_info` (`authentication.py:170-172`), in addition to `access_denied_servicenow_scope`.
- **Stale plan detail.** Task 3 says "all 4 references" and "one test reference" to `_validate_shared_project_name`. There are 3 in source and none in tests. Other line anchors match or drift by fewer than 25 lines.
- **Baseline diff size.** Regenerating `baselines/openapi.json` (1.9 MB) needs careful review to confirm it contains additions only.

---

## 7. Summary for Complexity Assessment

The change spans router, security, service, repository and config layers. It adds 4 new source files (router, models, service, security module) and changes 7 existing ones (`config.py`, `main.py`, `authentication.py`, `project_service.py`, `cost_center_service.py`, `user_repository.py`, `application_repository.py`), plus router-guard baselines and about 8 test files. Every file, line anchor and signature the spec and plan reference exists in current source and matches, apart from one minor stale note about validator references. The work is composition: it reuses `ProjectBudgetService` group methods, `ProjectAssignmentService.bulk_assign_users_to_project` and the cost-center and project services, and adds two permission-free extractions and two repository queries.

Two pieces are new to this codebase. First, the per-route scope check inside `authenticate()` reads `request.scope["route"]`, which nothing in `src` uses today, though FastAPI sets it before dependencies run. Second, the orchestration spans two transactions (sync `Session`, then `AsyncSession`), with an `IntegrityError` retry on the sync side. The budget math is pure and well specified. Existing group-update semantics confirm the plan's need to resend current soft limits and to always send all three categories.

Test infrastructure is mature and every target test file exists. The gaps are the scope check and concurrency. The main risks: (1) the group-create race surfaces as `IntegrityError` from a partial unique index, not the 409 the plan catches; (2) the budget step's eligibility check is case-sensitive, so the stored project name must be passed through; (3) the integration silently depends on `ENABLE_USER_MANAGEMENT=true`; (4) the service account deliberately gets maintainer-level budget authority, contained only by the allowlist.

---

## 8. External References

None named by the task outside this repository. The in-repo `spec.md` and `plan.md` in this run directory were read in full and validated in Section 2.
