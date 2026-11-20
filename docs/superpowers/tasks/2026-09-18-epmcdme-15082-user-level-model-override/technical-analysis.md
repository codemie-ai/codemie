# Technical Research

**Task**: model-access user-override access-control invocation-resolution fallback-logic
**Generated**: 2026-09-18
**Research path**: local codebase exploration

---

## 1. Original Context

Implement user-level model override enforcement for project admins to configure and enforce model access overrides for individual or bulk-selected users, with proper precedence over project settings, fallback logic, and platform disablement hard stops.

---

## 2. Codebase Findings

### Existing Implementations

**Model Availability Service** (`src/codemie/service/llm/model_availability_service.py`):
- `ModelAvailabilityService` — central orchestrator for model validation and resolution
- `validate_model_for_asset_creation(model_id, project_name)` — validates models at asset (assistant/workflow) creation time
- `resolve_model_for_execution(requested_model, project_name)` — resolves actual model at invocation with fallback to default
- `get_project_models(project_name)` — fetches project's allowed_models and default_model
- `ProjectModelConfig` dataclass — holds (allowed_models: list[str], default_model: str)
- Backward-compatibility logic: silent pass if whitelist/default not configured (allows legacy projects to continue)

**Project-Level Model Configuration** (`src/codemie/core/models.py`, lines 381-415):
- `Application` model extends BaseModelWithSQLSupport; table name "applications"
- `allowed_models: Optional[list[str]]` — PostgreSQL ARRAY(String), nullable (NULL = all models allowed for backward compatibility)
- `default_model: Optional[str]` — admin-selected fallback model, nullable

**User and Access Control** (`src/codemie/rest_api/models/user_management.py`):
- `UserDB` table — persistent user storage (id, username, email, is_active, is_admin, is_maintainer, is_auditor, auth_source, user_type, project_limit, deleted_at)
- `UserProject` table — user-to-project access mapping (user_id, project_name, is_project_admin, date)
  - Unique constraint on (user_id, project_name)
  - Foreign key to users.id
- User security model: admin, maintainer, or project_admin per project role
- No existing per-user model override table or columns

**User Authentication and Context** (`src/codemie/rest_api/security/user.py`):
- `User` model represents runtime authentication context
- Properties: is_admin, is_maintainer, is_auditor, admin_project_names, project_names, project_limit
- `is_application_admin(app_name: str)` method checks project-level admin membership
- Populated from JWT claims (ENABLE_USER_MANAGEMENT=False) or database tables (ENABLE_USER_MANAGEMENT=True)

**User Management Service** (`src/codemie/service/user/user_management_service.py`):
- Handles user CRUD operations, admin operations, project access grant/revoke
- Methods: create_local_user, get_user_by_id, update user roles, manage project access, list users with filters
- Activity event logging for user management operations via ActivityEventRepository

**Model Routing and Invocation** (`src/codemie/core/router_chat_model.py`):
- `RouterChatModel` — LangChain wrapper that routes each call through Router.decide()
- `_agenerate()` method calls router.decide() → receives RoutingDecision.model or falls back to default_model
- Candidate models pre-built and held for session lifetime
- No user-specific model selection logic; uses only project/router configuration

**Project Settings Router** (`src/codemie/rest_api/routers/project_settings.py`):
- GET/POST/PUT /settings/project endpoints for project-level settings
- Handles credentials, scheduler, LiteLLM, Git, webhook, MS Teams configs
- `_check_permission(user, project_name)` validates access
- LiteLLM credential creation gated to admin/maintainer only

### Architecture and Layers Affected

**API Layer**:
- REST routers for user-level model override endpoints (admin-only: POST, PUT, DELETE, list)
- Model selection endpoints likely routed from project_settings or user_management routers
- Depends on security.authentication.authenticate() dependency, User context

**Service Layer**:
- New service: UserModelOverrideService — orchestrates override CRUD, validation against project/platform constraints
- Extends ModelAvailabilityService — adds user-level lookup before project-level fallback
- Depends on UserManagementService for user validation and project_admin checks

**Repository Layer**:
- New UserModelOverride repository — CRUD for user override records
- Queries: by user_id, by (user_id, project_name), by project_name for bulk operations
- Should follow existing repository patterns (user_repository.py, application_repository.py)

**Database Layer**:
- New SQLModel table: UserModelOverride
  - Fields: id (UUID, PK), user_id (FK to users.id), project_name (FK to applications.id), allowed_models (ARRAY), override_enabled (bool), created_at, updated_at
  - Unique constraint on (user_id, project_name)
  - Alembic migration required

**Invocation Resolution Layer**:
- Model resolution call stack: RouterChatModel._agenerate → Router.decide() → candidates selected
- **New precedence chain** (lowest to highest):
  1. Platform disablement (hard stop — user_model_disabled flag in config or ApplicationEnrichment)
  2. User-level model override (if configured and user is in override set)
  3. Project-level allowed_models whitelist
  4. Default model (project-level or platform-level fallback)

### Integration Points

**Internal Module Dependencies**:
- ModelAvailabilityService → Application.get_by_id() (model retrieval)
- UserManagementService → user_repository (user CRUD)
- UserModelOverrideService (new) → user_repository, application_repository, UserModelOverride repository
- Invocation resolution → RouterChatModel._agenerate() call site (new precedence check)
- Project admin endpoints → User.is_application_admin(project_name) check

**External Service Connections**:
- No direct external services identified; platform disablement would come from config or internal enrichment table

**Data Flow**:
- Asset creation: validate model → ModelAvailabilityService.validate_model_for_asset_creation(model_id, project)
- Execution/invocation: resolve model → (1) check user override, (2) check project whitelist, (3) return default
- Admin config: set user override → UserModelOverrideService.set_override(user_id, project_name, allowed_models)

### Patterns and Conventions

**Error Handling** (from error-handling guide):
- ModelNotWhitelistedException (400) — model not in whitelist
- NoDefaultModelException (400) — whitelist configured without default
- NotFoundException (404) — project or user not found
- ExtendedHTTPException with code, message, details, help fields

**Access Control Patterns** (from security-patterns guide):
- Use authenticate() dependency for bearer/bind-key parsing
- Use admin/maintainer/project-admin helper dependencies for role checks
- User context passed explicitly (not Request object)
- Strict parsing of untrusted input (reject malformed overrides)

**Service Layer Patterns** (from service-layer-patterns guide):
- Feature-scoped service (UserModelOverrideService) for override orchestration
- Repository delegation from service (no direct SQL in service)
- Request parsing and response formatting in routers, business logic in service

**Database Patterns** (from database-patterns guide):
- SQLModel with scoped sessions through existing helpers
- Alembic migrations in src/external/alembic/versions/
- No raw SQL; use SQLAlchemy expressions

**Backward Compatibility**:
- ModelAvailabilityService checks: if allowed_models is None AND default_model is None, skip validation (existing behavior)
- User override layer added without breaking existing project-level logic
- Platform disablement config optional (defaults to enabled if not present)

---

## 3. Documentation Findings

### Guides and Architecture Docs

Relevant guides loaded:
- `.ai-run/guides/architecture/layered-architecture.md` — API to Service to Repository pattern; Startup and Router Ownership; Shared Core
- `.ai-run/guides/development/security-patterns.md` — Authentication/Authorization, Secret Handling, Untrusted Input Handling
- `.ai-run/guides/data/database-patterns.md` — SQLModel/Sessions, Migrations via Alembic
- `.ai-run/guides/architecture/service-layer-patterns.md` — Orchestration Boundary, Feature Services, Provider Registries
- `.ai-run/guides/development/configuration-patterns.md` — Config and environment patterns (for platform disablement flag)

**Key Conventions from Guides**:
- HTTP concerns in routers (src/codemie/rest_api/routers/), orchestration in services (src/codemie/service/), persistence in repositories
- Central authentication dependency and role helpers (not inline checks)
- Shared exceptions from codemie.core
- Config read from environment or config modules, not inline
- Alembic for schema changes under src/external/alembic/versions/

### Architectural Decisions

**Implicit ADRs**:
1. Project-level model whitelist is a backward-compatible opt-in feature — silent pass if not configured
2. Model resolution happens at invocation time, not asset creation time (fallback applied during execution)
3. User context flows through routers → services → repositories (explicit, not hidden in Request object)
4. Router selection happens once at construction, not re-resolved per call (RouterChatModel holds concrete instance)

**Inferred ADR for User-Level Overrides**:
- User-level override must respect project-level constraints (override cannot grant access to project-disallowed models)
- Platform disablement is a hard stop (user override cannot bypass platform-level model disable)
- Bulk configuration supported for admins to apply same override to multiple users or user groups

### Derived Conventions

**Exception Strategy**: Modeled after existing ModelAvailabilityService
- Custom exception types for domain errors (ModelNotWhitelistedException, NoDefaultModelException)
- ExtendedHTTPException for HTTP-level errors with code, message, details, help
- NotFoundException for missing entities

**Logging**: From logging-patterns guide
- Use sanitized contextual logging (avoid logging tokens, keys, sensitive user data)
- Log domain events (e.g., model_override_set, model_resolution_applied)

**Repository Pattern**: Existing repositories follow unique constraint + get_all_by_fields pattern
- user_repository.get_by_id(session, user_id)
- application_repository.get_by_id(session, project_name)
- User overrides repository should follow same pattern: get_by_user_and_project(), get_by_project(), etc.

---

## 4. Testing Landscape

### Existing Coverage

**Model Availability Service Tests** (`tests/codemie/service/llm/test_model_availability_service.py`):
- Unit tests for validate_model_for_asset_creation: success, not in whitelist, empty whitelist, no default, project not found
- Unit tests for resolve_model_for_execution: no fallback needed, with fallback, default is requested, empty whitelist, no default, project not found
- Unit tests for get_project_models: success, no default, project not found
- Edge cases: special characters in model names, multiple fallback scenarios
- Mocks Application.get_by_id() for isolation
- Test coverage: 100% of ModelAvailabilityService public methods

**User Management Tests**:
- No dedicated test file visible in glob; tests likely embedded in integration test suites or router tests
- User CRUD operations and project access mapping tested via API contract tests

**Coverage Gaps**:
- No tests for user-level overrides (feature not yet implemented)
- No integration tests for model resolution flow (RouterChatModel._agenerate)
- No tests for precedence/fallback chain with multi-level constraints

### Testing Framework and Patterns

**Framework**: pytest (from pyproject.toml, Makefile)
- Fixtures for mock Application objects (mock_app, mock_empty_whitelist_app, mock_no_default_app)
- patch() from unittest.mock for dependency mocking
- MagicMock() for mock objects

**Fixture Patterns**:
- Mock objects for Application, User, UserProject
- Mock repositories with side_effect for error cases

**Mock Strategies**:
- Mock Application.get_by_id() at module level to avoid database calls
- Parametrized tests for multiple scenarios (not currently used; could improve test readability)

**Test Utilities**:
- Standard pytest patterns (assert, raises context manager)
- No custom test utilities visible for model availability tests

### Coverage Gaps

**Areas Needing Tests**:
- UserModelOverrideService CRUD operations (set override, get override, delete override, bulk operations)
- Invocation resolution call chain: user override → project whitelist → default (with all combinations)
- Admin-only access control for override endpoints
- Platform disablement hard stop enforcement
- Bulk user override operations (apply to group, remove from group)
- Data validation: override must not grant access to project-disallowed models
- Backward compatibility: legacy projects (no override table) continue to work
- Edge cases: user override removal, project whitelist update with existing overrides

---

## 5. Configuration and Environment

### Environment Variables

From codebase (inferred):
- `ENV` — deployment environment (local, dev, staging, prod) — affects is_admin resolution
- `ADMIN_USER_ID` — legacy IDP mode: user ID with admin status
- `ADMIN_ROLE_NAME` — legacy IDP mode: role name for admins
- `ENABLE_USER_MANAGEMENT` — flag to enable database-backed user management (vs. legacy JWT)
- `USER_PROJECT_LIMIT` — default project limit for non-admin users (from config object)
- `PASSWORD_MIN_LENGTH` — minimum password length for local auth
- (New) `PLATFORM_MODEL_OVERRIDE_ENABLED` — feature flag for user-level overrides (if platform-wide disable needed)
- (New) `PLATFORM_DISABLED_MODELS` — comma-separated list of globally disabled model IDs (hard stop on user selection)

### Configuration Files

**Project Settings** (`src/codemie/rest_api/routers/project_settings.py`):
- GET/POST/PUT /settings/project — project-level configuration persistence
- Settings service handles credential storage and retrieval

**Application/Project Model** (`src/codemie/core/models.py`):
- allowed_models: list[str] — whitelist of allowed models per project
- default_model: str — admin-selected fallback model per project

**Config Module** (`src/codemie/configs/`):
- centralized environment variable reads
- config.ENABLE_USER_MANAGEMENT, config.PASSWORD_MIN_LENGTH, config.USER_PROJECT_LIMIT referenced throughout
- (New) Config entries for PLATFORM_MODEL_OVERRIDE_ENABLED, PLATFORM_DISABLED_MODELS

### Feature Flags and Deployment Concerns

**Feature Flags**:
- ENABLE_USER_MANAGEMENT — gates database user table vs. legacy IDP
- (Inferred) PLATFORM_MODEL_OVERRIDE_ENABLED — gates user override endpoints (deprecation/rollback strategy)

**Deployment Manifests**:
- No manifests visible in codebase search; config via environment variables and Makefile

**Secrets Management**:
- Credentials stored in settings table (encrypted by application; see settings validation in project_settings.py)
- User model override configs are not secrets (data, not credentials)

**Model Availability Lifecycle**:
- Project creation: admin sets allowed_models and default_model
- Asset creation: validate model against project whitelist
- Execution: resolve model with fallback to default
- (New) User override creation: admin sets per-user allowed_models; overrides applied at invocation
- (New) Platform disablement: global disable of specific model IDs blocks all access (even if project/user allows)

---

## 6. Risk Indicators

- **User override precedence complexity**: User override must not contradict project settings; validation logic needs to check project whitelist at override set-time AND at invocation time to catch project config changes after override creation
- **Bulk operation atomicity**: Bulk-assign user overrides to multiple users — need transaction boundary and error handling for partial failures (commit all or rollback all)
- **Lack of async invalidation**: User override removal or project whitelist update does not cascade or invalidate in-flight invocations; model resolution is synchronous, stale config could be read if caching is added later
- **Platform disablement integration point unclear**: No existing ApplicationEnrichment or global config table for platform-disabled models; need to clarify where this flag lives (config file, database table, or separate feature)
- **No migration path documented**: Existing project admins will need UI/API instructions to create user overrides; no backward-compat import for bulk user groups
- **User-project binding not enforced at override table**: UserModelOverride references user_id and project_name; no FK constraint exists to prevent orphaned overrides if user-project relationship is deleted
- **Testing surface area**: No existing integration tests for model resolution call chain; new feature adds precedence chain (platform disable → user override → project whitelist → default) with 4 levels to test in all combinations
- **Audit and logging**: No existing audit trail for model override changes; regulatory requirements may mandate tracking who changed what override, when
- **API design ambiguity**: Endpoint structure not yet defined (POST /projects/{project_name}/users/{user_id}/model-overrides? or /settings/project/user-overrides?); inconsistent placement would break discoverability
- **No constraint on override model list**: Override allowed_models can include models not in project whitelist; validation logic must reject this at set-time, not at invocation time

---

## 7. Summary for Complexity Assessment

The user-level model override feature sits at the intersection of access control, model routing, and invocation resolution. Existing project-level model availability (ModelAvailabilityService) operates at a single level: per-project whitelist and default. The new feature adds a user-scoped override layer that must respect project constraints and enforce platform hard stops, creating a 4-tier precedence chain (platform disable → user override → project whitelist → default). The implementation surface spans API routers (new endpoints for override CRUD, admin-only gating), service orchestration (new UserModelOverrideService to coordinate with ModelAvailabilityService), repository persistence (new UserModelOverride SQLModel table with foreign keys and unique constraints), and invocation resolution (new logic in ModelAvailabilityService.resolve_model_for_execution or a new wrapper that calls it). Complexity is amplified by bulk user operations (atomicity, partial failure handling), the need to validate override lists against project constraints at two points in time (creation and invocation), and the introduction of a platform-level disablement mechanism whose integration point is not yet defined. Testing surface area is large: the precedence chain has 4 levels, each with allow/deny states, and bulk operations require transaction-scoped test fixtures.

---

## 8. External References

None named by the task.

