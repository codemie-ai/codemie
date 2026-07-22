# Technical Research

**Task**: keycloak auth service-account budget project-member
**Generated**: 2026-07-21T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-13582: When a service account logs in for the first time through Keycloak, the code does not create a budget record for it. We need to add a call to _sync_project_budget_member_added right after the user is added to the project from Keycloak data. We do not need a migration, because removing and re-adding the user in the UI already fixes old accounts.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/service/user/authentication_service.py` — `AuthenticationService.create_user_from_idp` (line 178): bootstraps project membership ONE-TIME on first Keycloak login by calling `user_project_repository.aadd_project` for each project in `idp_user.project_names` (lines 225–227); **does not call `_sync_project_budget_member_added` — this is the exact bug location**
- `src/codemie/service/project/project_assignment_service.py` — defines `ProjectAssignmentService._sync_project_budget_member_added` as a static synchronous method (takes `sqlmodel.Session`); called at line 316 inside `assign_user_to_project` (sync) and at line 516 inside `bulk_assign_users_to_project` (sync); the singleton `project_assignment_service = ProjectAssignmentService()` is declared at the module bottom
- `src/codemie/repository/user_project_repository.py` — `UserProjectRepository.aadd_project` (async, line 542): inserts a `UserProject` row; called from `create_user_from_idp`; sync counterpart `add_project` (line 83) used by the UI path
- `src/codemie/service/budget/budget_models.py` — `Budget`, `ProjectBudgetAssignment`, `ProjectMemberBudgetAssignment` SQLModel ORM models; `build_shared_project_budget_id`; `ProjectMemberBudgetAssignment` is the row created by `_sync_project_budget_member_added`
- `src/codemie/enterprise/idp/dependencies.py` — `EnterpriseIdpWrapper.authenticate` (line 63): maps Keycloak `IdpUser` to core `User`, populating `project_names` and `admin_project_names` from Keycloak group memberships before `create_user_from_idp` is called
- `src/codemie/enterprise/loader.py` — lazy enterprise imports: `KeycloakIdpProvider`, `OidcIdpProvider`, migration helpers; guarded by `HAS_IDP` flag
- `src/codemie/enterprise/migration/coordinator.py` — `CodemieMigrationDeps.add_user_project` (line 92): bulk-migration path; calls `aadd_project` without budget sync — secondary gap not in scope of this task
- `src/codemie/service/user/user_access_service.py` — `grant_project_access` (line 94): legacy admin endpoint; also calls sync `add_project` without budget sync (pre-existing, out of scope)
- `src/codemie/rest_api/security/user_providers/persistent.py` — `PersistentUserProvider.authenticate_and_load_user`: top-level auth entry point that chains into `authentication_service.authenticate_persistent_user`
- `src/codemie/clients/postgres.py` — `get_session()` (sync `@contextmanager`) and `get_async_session()` (async `@asynccontextmanager`); both used in the affected code paths

### Architecture and Layers Affected

- **Service layer (primary)**: `AuthenticationService.create_user_from_idp` in `authentication_service.py` — the change happens here; `ProjectAssignmentService._sync_project_budget_member_added` in `project_assignment_service.py` — the function to be called
- **Repository layer**: `UserProjectRepository.aadd_project` — existing call already in place; no repository changes needed
- **External integration layer**: enterprise `KeycloakIdpProvider` → `EnterpriseIdpWrapper` → `BaseIdp.authenticate` → decodes token → populates `idp_user.project_names`; read-only from this task's perspective
- **API/security middleware**: `PersistentUserProvider.authenticate_and_load_user` → `authenticate_persistent_user` → `_create_first_login_user` → `create_user_from_idp`; no changes needed above the service layer

### Integration Points

- `authentication_service` imports from `user_project_repository` (`aadd_project`)
- `project_assignment_service` imports from `budget_models`, `budget_resolution_service`, and `user_project_repository`
- `enterprise/idp/dependencies` wraps `codemie_enterprise.idp` (external package); `IdpFactory.register()` connects it at startup
- `authentication_service` will need to import `project_assignment_service` (or the sync-session helper) after the fix
- **Session boundary challenge**: `create_user_from_idp` operates inside an `AsyncSession` context, but `_sync_project_budget_member_added` takes a synchronous `sqlmodel.Session`. Two codebase-consistent approaches exist:
  1. Open a new isolated sync session (`get_session()`) per project in the `for project_name` loop, matching the post-commit isolated-session pattern already used for personal-project creation
  2. Add an async variant `_async_sync_project_budget_member_added` to `ProjectAssignmentService` using `await session.execute(select(...))` and call it directly in the async context

### Patterns and Conventions

- `_sync_project_budget_member_added` is idempotent: it skips if a `ProjectMemberBudgetAssignment` row already exists (`existing is not None: continue`), so calling it on re-login is safe
- Static methods only on service classes — no instance state; singleton instances declared at module bottom
- Post-commit isolated-session pattern: a new session is opened after the main transaction commits for operations that cannot share the in-progress session (established in the personal-project creation flow)
- `_run_budget_provider_coro` helper: bridges a sync caller to an async budget-provider coroutine; relevant if an async overload is added
- `actor_id` for the budget sync during first-login bootstrap should use the new user's own ID (`db_user.id`) — no external actor is present during auto-first-login
- `# Bootstrap authorization from idp_user fields (ONE-TIME ONLY)` comment at line 224 of `authentication_service.py` marks the exact insertion point

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/architecture/layered-architecture.md` — confirms business logic belongs in service layer; budget sync is a service-layer concern, not a repository or router concern
- `.ai-run/guides/architecture/service-layer-patterns.md` — services coordinate repositories and providers; orchestration must not live in routers or repositories; supports placing the new call in `authentication_service.py`
- `.ai-run/guides/integration/external-services.md` — external API calls (including Keycloak) must go through service/toolkit adapters; the existing Keycloak integration satisfies this; no changes to integration layer needed
- `.ai-run/guides/development/security-patterns.md` — use central `authenticate` dependency; no inline auth logic in endpoints; the existing architecture already satisfies this
- `docs/jwks-configuration-reference.md` — confirms IDP_PROVIDER=keycloak enterprise flow and header convention; confirms `IdpFactory.register()` at startup connects the Keycloak provider
- `docs/superpowers/tasks/2026-06-22-epmcdme-12959-budget-stale-allocation/technical-analysis.md` — prior deep analysis of the same budget subsystem; confirms `_sync_project_budget_member_added` is the established pattern for creating `ProjectMemberBudgetAssignment` rows and that `SettingsService.get_enforce_member_spend_limits` is safe to call in async handlers
- `docs/superpowers/tasks/2026-06-22-epmcdme-12959-budget-stale-allocation/spec.md` — confirms allocation rows are created by `_sync_project_budget_member_added` exclusively; `project_member_runtime_sync.py` is not to be modified for this path

### Architectural Decisions

- `create_user_from_idp` calls `aadd_project` ONE-TIME ONLY (comment at line 224) — this is explicit and intentional; the bug is the omission of the budget sync in the same one-time block
- `_sync_project_budget_member_added` is synchronous by design (sync `Session`) — the async boundary is a known integration constraint
- Prior spec (EPMCDME-12959) confirmed `ProjectMemberBudgetAssignment` rows are the sole source of truth for per-member budget state; this task must create them via the same function
- Migration coordinator (`coordinator.py` line 92) has the same gap but is out of scope per ticket description

### Derived Conventions

- Budget sync must be called per project immediately when a user is added to a project, using the same session or a fresh isolated session
- All existing callers of `_sync_project_budget_member_added` pass a sync `Session`; any new caller from an async context must bridge the boundary
- `actor_id` on first-login bootstrap = the user's own `db_user.id` (no admin actor present)

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/project/test_project_assignment_service.py` — unit tests for `_sync_project_budget_member_added` (allocation copy, fallback, skip-if-exists, no-provider-sync); `assign_user_to_project`; `bulk_assign_users_to_project` — comprehensive coverage of the function to be called
- `tests/codemie/service/user/test_authentication_service.py` — tests `create_user_from_idp`, `_create_first_login_user`, `authenticate_persistent_user` (first-login, race-condition, deactivated flows); IDP provider mocked as "keycloak"; **covers project-name bootstrapping via `aadd_project` but contains no budget sync assertions — the gap is confirmed**
- `tests/codemie/rest_api/security/test_persistent_user_provider.py` — tests `PersistentUserProvider.authenticate_and_load_user` for keycloak/OIDC/local JWT/dev-header flows; delegates to `authentication_service` (mocked); budget sync invisible here
- `tests/enterprise/migration/test_coordinator.py` — tests `CodemieMigrationDeps.add_user_project` delegates to `user_project_repository.aadd_project`; no budget sync assertion (secondary gap, out of scope)
- `tests/codemie/service/budget/test_project_budget_service.py`, `test_project_budget_service_lifecycle.py` — budget service lifecycle; no keycloak/first-login path tested

### Testing Framework and Patterns

- pytest 8.3.x, pytest-asyncio 0.23.x, pytest-mock 3.14.x, pytest-env 1.1.x, pytest-cov 5.x
- `@pytest.mark.asyncio` required explicitly per test (no global asyncio_mode)
- `pytest.ini`: `testpaths=tests`, `pythonpath=src`, `addopts=--import-mode=importlib`
- `unittest.mock.AsyncMock` for async collaborators; `MagicMock` for sync
- Module-level `@patch(...)` decorators for repository singletons (`user_project_repository`, `user_repository`, etc.)
- `_make_async_session_cm` helper in auth tests wraps `AsyncMock` in async context manager protocol; `patch("codemie.clients.postgres.get_async_session")` for session injection
- `session.exec.side_effect = [...]` for multi-call SQL sequences (used heavily in budget sync tests)
- `conftest.py` global `mock_database_engine` autouse fixture prevents real DB connections; `_clear_auth_cache` autouse fixture per test in auth tests
- Class-scoped private helper factories (`_make_budget`, `_make_assignment`, `_make_member_alloc`) in budget tests — no shared conftest factories for budget objects

### Coverage Gaps

- No test verifies that `_sync_project_budget_member_added` is called during `create_user_from_idp` — this is the primary gap to address with the fix
- No test for a service-account user type (`user_type != "human"`) going through `create_user_from_idp`; all existing tests hardcode `user_type="human"`
- No test verifying that when a service account is added to multiple projects via Keycloak claims, each project gets a `ProjectMemberBudgetAssignment` record
- No end-to-end test covering: Keycloak token → `authenticate_persistent_user` → `_create_first_login_user` → `create_user_from_idp` → `_sync_project_budget_member_added`

---

## 5. Configuration and Environment

### Environment Variables

- `IDP_PROVIDER` — selects auth backend (`keycloak`, `oidc`, `entraid-oidc`, `local`); default in deploy is `keycloak`
- `ENABLE_USER_MANAGEMENT` — master switch for project-member and budget sync code paths; the first-login path is only reached when this is `True`
- `KEYCLOAK_MIGRATION_ENABLED` — gates bulk migration path (`coordinator.py`); secondary gap, out of scope
- `KEYCLOAK_ADMIN_URL`, `KEYCLOAK_ADMIN_REALM`, `KEYCLOAK_ADMIN_CLIENT_ID`, `KEYCLOAK_ADMIN_CLIENT_SECRET`, `KEYCLOAK_MIGRATION_BATCH_SIZE` — Keycloak migration coordinator settings; not affected by this fix
- `LLM_PROXY_BUDGET_CHECK_ENABLED` — activates per-request budget enforcement; must be `True` for the synced budget record to be enforced (defaults to `False` in Helm — must be explicitly set in environment-specific overrides)
- `LLM_PROXY_BUDGET_RECONCILIATION_ENABLED` — runs budget reconciliation at startup; no change needed
- `BUDGET_ASSIGNMENT_CACHE_TTL` / `BUDGET_ASSIGNMENT_CACHE_MAX_SIZE` — in-process cache; a newly synced record may be invisible for up to TTL seconds (default 60 s) — relevant for smoke testing
- `BUDGET_RESOLUTION_CACHE_TTL` / `BUDGET_RESOLUTION_CACHE_MAX_SIZE` — same concern for resolution cache
- `AUTH_TOKEN_CACHE_TTL` / `AUTH_TOKEN_CACHE_MAX_SIZE` — token validation cache; irrelevant to budget sync

### Configuration Files

- `config/budgets/budgets-config.yaml` — predefined platform budget (`default`, 100 USD max, 30-day cycle, `platform` category); loaded at startup; referenced by `_sync_project_budget_member_added` to determine default allocation amounts
- `config/customer/customer-config.yaml` — feature flags including `features:budgetManagement` (enabled: true); UI visibility flag, does not gate the server-side `_sync_project_budget_member_added` call
- `src/codemie/configs/config.py` — canonical env-var declarations for all keycloak, auth, and budget settings
- `deploy-templates/values.yaml` — sets `IDP_PROVIDER=keycloak`; `serviceAccount.create=true` refers to a Kubernetes pod service account (unrelated to Keycloak service accounts)

### Feature Flags and Deployment Concerns

- `features:budgetManagement` (customer-config.yaml) — UI flag only; does not gate server-side budget sync
- `ENABLE_USER_MANAGEMENT` — gates all project-member and budget paths; fix only takes effect when `True`
- `LLM_PROXY_BUDGET_CHECK_ENABLED` defaults to `False` in `deploy-templates/values.yaml`; must be explicitly enabled in environment overlays to enforce budget limits for newly-created service account allocations
- No DB migration required; `_sync_project_budget_member_added` is idempotent and skips existing rows
- `BUDGET_ASSIGNMENT_CACHE_TTL` (60 s default): note for smoke testing — new budget records may not be visible immediately after first login

---

## 6. Risk Indicators

- **Sync/async session mismatch**: `_sync_project_budget_member_added` takes a synchronous `sqlmodel.Session`; `create_user_from_idp` operates in an `AsyncSession` context. This is the primary implementation risk. Two patterns exist in the codebase to bridge this boundary (post-commit isolated sync session, or async overload), but neither is trivially identical to the existing callers.
- **No existing test for budget sync at first login**: `tests/codemie/service/user/test_authentication_service.py` covers `create_user_from_idp` with project bootstrapping but has zero assertions about `_sync_project_budget_member_added` — a new test case is needed and must be written carefully given the async/sync mock boundary
- **Service account user type not tested**: all `create_user_from_idp` tests use `user_type="human"`; service accounts may have different `user_type` values that could interact with budget sync logic
- **Budget assignment cache (60 s TTL)**: a newly created `ProjectMemberBudgetAssignment` may not be visible to budget enforcement within the first request after login — potential for confusing first-request behavior that does not indicate a bug
- **`LLM_PROXY_BUDGET_CHECK_ENABLED` defaults to False in Helm**: the fix creates the budget record but budget enforcement is off by default; QA must verify against an environment with budget check enabled
- **Secondary gap in migration coordinator (`coordinator.py` line 92)**: the same `aadd_project`-without-budget-sync pattern exists in the Keycloak bulk migration path; while out of scope for this ticket, it is a known gap and may generate a follow-up

---

## 7. Summary for Complexity Assessment

The task touches two service-layer files: `authentication_service.py` (where the call must be added, inside `create_user_from_idp` around line 225) and `project_assignment_service.py` (where `_sync_project_budget_member_added` is already defined and well-tested). No repository, router, model, or migration changes are required. The file change surface is narrow — likely one to three files total depending on whether an async overload of `_sync_project_budget_member_added` is introduced.

The primary technical novelty is the sync/async session boundary: all existing callers of `_sync_project_budget_member_added` use a synchronous `Session`, but the insertion point is inside an async method using `AsyncSession`. The codebase has two established bridging patterns (post-commit isolated sync session from personal-project creation; `_run_budget_provider_coro` from the budget provider path), so this is not greenfield — it requires careful selection of the right pattern rather than invention of a new one. The idempotent nature of `_sync_project_budget_member_added` reduces the risk of double-allocation on re-login.

Test coverage posture is mixed: `_sync_project_budget_member_added` itself is thoroughly unit-tested; `create_user_from_idp` has broad async test coverage; but there is no existing test asserting that the budget sync is invoked during the first-login flow. The fix must be accompanied by a new test case in `test_authentication_service.py` verifying that `project_assignment_service._sync_project_budget_member_added` (or its async equivalent) is called for each project in `idp_user.project_names` during `create_user_from_idp`. Key risk factors: async/sync boundary resolution, cache TTL behavior during smoke testing, and the need to validate against a deployment with `LLM_PROXY_BUDGET_CHECK_ENABLED=True`.
