# User-Level Model Override Enforcement — Specification

> **Superseded.** This is the first attempt at EPMCDME-15082. The work continued in `../2026-10-07-epmcdme-15082-user-level-model-override/`, whose `spec.md` is the source of truth. This document describes a 4-tier chain with platform disablement and a different data model, which were dropped. It is kept as history only.

**Task**: Implement user-level model override enforcement for project admins to configure and enforce model access overrides for individual or bulk-selected users, with precedence over project settings, fallback logic, and platform disablement hard stops.

**Size**: L (4–5 days)

**Generated**: 2026-09-21

---

## 1. Overview

Project admins need granular control over LLM model access at the user level. The feature allows admins to override project-level model whitelists for individual users or bulk-selected users, with a secure 4-tier precedence chain that respects platform disablement hard stops and project constraints. User-level overrides take precedence over project settings, but cannot grant access to models disabled at the project or platform level.

---

## 2. Scope & Acceptance Criteria

### Acceptance Criteria

1. **UserModelOverride database table** — SQLModel table defined in `src/codemie/core/models.py` with fields: id (UUID PK), user_id (FK to UserDB), project_name (FK to Application), allowed_models (ARRAY), override_enabled (bool), created_at, updated_at. Unique constraint on (user_id, project_name). Check constraint: override_enabled=false if allowed_models is empty.

2. **UserModelOverrideService** — New service in `src/codemie/service/` orchestrating override CRUD and validation. Methods: set_override, get_override, delete_override, get_overrides_by_project, batch_set_overrides, resolve_model_for_user. Validation enforces override allowed_models ⊆ project allowed_models at set-time.

3. **UserModelOverride repository** — New repository in `src/codemie/data/repositories/` supporting queries by user_id, (user_id, project_name), and project_name for admin bulk operations.

4. **API endpoints** (all admin-only) — POST/GET/DELETE for single-user overrides; POST /batch for bulk operations. Batch endpoint supports grant/revoke operations with atomic transaction scoping.

5. **4-tier precedence chain** — At invocation time, model resolution follows: (1) platform disablement hard stop, (2) user override if enabled, (3) project whitelist, (4) default model. Platform disablement list sourced from new ApplicationEnrichment table (per-project) with global default in config.

6. **ModelAvailabilityService integration** — Extend resolve_model_for_execution to check user override before project whitelist. Check platform disablement at tier 1 (hard stop).

7. **Validation at set-time** — Override creation fails if allowed_models include: (a) models not in project whitelist, or (b) platform-disabled models. Error: ModelNotWhitelistedException with details.

8. **Graceful degradation at invocation** — If project whitelist changes after override creation, override is not re-validated. Invocation uses current 4-tier chain; user falls back to project whitelist → default if their override models are no longer project-allowed.

9. **Batch atomicity** — POST /batch rolls back entire transaction if any user fails validation. Returns 400 with failed user details (not 207 partial success).

10. **Admin gating** — All override endpoints require User.is_application_admin(project_name) check via security dependency.

11. **Backward compatibility** — Legacy projects (no ApplicationEnrichment row) default to no platform disablement. Legacy projects (no user overrides) continue using project-level whitelists only.

12. **Test coverage** — Unit tests for all service methods (set, get, delete, batch, resolve), API endpoint tests (admin gating, validation, batch atomicity), and integration tests for 4-tier precedence chain (all combinations: platform deny, user allow/deny, project allow/deny, with/without default).

---

## 3. Architecture & Data Model

### 3.1 Database — UserModelOverride Table

**Location**: `src/codemie/core/models.py` (alongside Application, UserProject, etc.)

**Definition**:
```python
class UserModelOverride(BaseModelWithSQLSupport):
    """
    User-level override for LLM model access within a project.
    
    Respects project whitelist constraints and platform disablement.
    One override per (user_id, project_name) pair.
    """
    __tablename__ = "user_model_overrides"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False)
    project_name: str = Field(foreign_key="applications.id", nullable=False)
    allowed_models: list[str] = Field(sa_column=Column(ARRAY(String)), nullable=False)
    override_enabled: bool = Field(default=True, nullable=False)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, nullable=False)
    
    # Unique constraint: one override per user per project
    __table_args__ = (
        UniqueConstraint("user_id", "project_name", name="uq_user_model_override_user_project"),
        CheckConstraint("NOT (override_enabled = true AND array_length(allowed_models, 1) IS NULL)", 
                        name="ck_override_enabled_has_models"),
    )
```

### 3.2 ApplicationEnrichment Table (Platform Disablement)

**Location**: `src/codemie/core/models.py`

**Definition**:
```python
class ApplicationEnrichment(BaseModelWithSQLSupport):
    """
    Per-project enrichment data, including platform-disabled model list.
    
    Extends Application model with project-specific platform constraints.
    """
    __tablename__ = "application_enrichment"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_name: str = Field(foreign_key="applications.id", nullable=False, unique=True)
    disabled_model_ids: list[str] = Field(sa_column=Column(ARRAY(String)), 
                                          default=[], nullable=False)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, nullable=False)
```

### 3.3 Service Layer — UserModelOverrideService

**Location**: `src/codemie/service/user/user_model_override_service.py` (new file)

**Key Methods**:
- `set_override(session, user_id: UUID, project_name: str, allowed_models: list[str]) -> UserModelOverride` — Create or update override; validate against project/platform constraints; raise ModelNotWhitelistedException on invalid models.
- `get_override(session, user_id: UUID, project_name: str) -> UserModelOverride | None` — Fetch override for user-project pair.
- `delete_override(session, user_id: UUID, project_name: str) -> None` — Remove override.
- `get_overrides_by_project(session, project_name: str, skip: int = 0, limit: int = 100) -> list[UserModelOverride]` — List all overrides for admin view.
- `batch_set_overrides(session, project_name: str, user_ids: list[UUID], operation: 'grant' | 'revoke', allowed_models: list[str] = None) -> list[UserModelOverride]` — Apply same override to multiple users; atomic transaction (all or nothing).
- `resolve_model_for_user(session, requested_model: str, user_id: UUID, project_name: str) -> str` — Integrate with ModelAvailabilityService to apply 4-tier precedence chain.

**Validation Helper**:
- `_validate_override_models(session, project_name: str, allowed_models: list[str]) -> None` — Check: (1) allowed_models ⊆ project whitelist, (2) no allowed_models in platform disablement list. Raise ModelNotWhitelistedException if violated.

### 3.4 Repository Layer — UserModelOverrideRepository

**Location**: `src/codemie/data/repositories/user_model_override_repository.py` (new file)

**Key Methods**:
- `get_by_user_and_project(session, user_id: UUID, project_name: str) -> UserModelOverride | None`
- `get_by_project(session, project_name: str, skip: int, limit: int) -> list[UserModelOverride]`
- `create(session, user_id: UUID, project_name: str, allowed_models: list[str]) -> UserModelOverride`
- `update(session, user_id: UUID, project_name: str, allowed_models: list[str]) -> UserModelOverride`
- `delete(session, user_id: UUID, project_name: str) -> None`

### 3.5 Model Resolution — 4-Tier Precedence Chain

**Integration point**: `src/codemie/service/llm/model_availability_service.py` (extend existing)

**Precedence (highest to lowest)**:
1. **Platform Disablement (hard stop)** — If requested_model is in ApplicationEnrichment.disabled_model_ids for this project (or global config default), deny immediately. Raise ModelNotWhitelistedException("Platform has disabled this model").
2. **User Override** — If UserModelOverride exists and override_enabled=true, check if requested_model in allowed_models. If yes, proceed to invocation. If no, move to tier 3.
3. **Project Whitelist** — Check if requested_model in Application.allowed_models. If yes, proceed. If no, move to tier 4.
4. **Default Model** — Use Application.default_model (or platform default from config if project has none).

**New method**: `resolve_model_for_execution_with_user_context(session, requested_model: str, user_id: UUID, project_name: str) -> str` — applies full 4-tier chain. Existing `resolve_model_for_execution(requested_model, project_name)` unchanged for backward compatibility.

---

## 4. API Design

### 4.1 Single-User Override Endpoints

**POST /projects/{project_name}/user-model-overrides** — Create or update override for one user.
- **Auth**: Requires is_application_admin(project_name)
- **Request body**: `{ user_id: UUID, allowed_models: list[str] }`
- **Response**: 200 OK, UserModelOverride JSON
- **Errors**: 403 Forbidden (not admin), 400 Bad Request (user not in project, models not in whitelist), 404 Not Found (project not found)

**GET /projects/{project_name}/user-model-overrides** — List all overrides for a project.
- **Auth**: Requires is_application_admin(project_name)
- **Query params**: skip=0, limit=100
- **Response**: 200 OK, list[UserModelOverride] JSON
- **Errors**: 403 Forbidden, 404 Not Found

**GET /projects/{project_name}/user-model-overrides/{user_id}** — Fetch override for a specific user.
- **Auth**: Requires is_application_admin(project_name)
- **Response**: 200 OK, UserModelOverride JSON
- **Errors**: 403 Forbidden, 404 Not Found (project or override not found)

**DELETE /projects/{project_name}/user-model-overrides/{user_id}** — Remove override.
- **Auth**: Requires is_application_admin(project_name)
- **Response**: 204 No Content
- **Errors**: 403 Forbidden, 404 Not Found

### 4.2 Batch Override Endpoint

**POST /projects/{project_name}/user-model-overrides/batch** — Apply/revoke override for multiple users.
- **Auth**: Requires is_application_admin(project_name)
- **Request body**:
  ```json
  {
    "operation": "grant" | "revoke",
    "user_ids": ["<uuid>", "<uuid>", ...],
    "allowed_models": ["model-a", "model-b"]  // required for grant, ignored for revoke
  }
  ```
- **Response**: 200 OK, list[UserModelOverride] JSON (created/updated/deleted records)
- **Errors**: 
  - 400 Bad Request: empty user_ids, empty allowed_models for grant, first user fails validation → entire batch rolled back
  - 403 Forbidden: not admin
  - 404 Not Found: project not found
- **Atomicity**: All users succeed or entire batch fails (no partial success). Transaction scope includes validation + all CRUD operations.

### 4.3 Response Models

**UserModelOverrideResponse** (JSON):
```json
{
  "id": "<uuid>",
  "user_id": "<uuid>",
  "project_name": "project-123",
  "allowed_models": ["gpt-4", "claude-opus"],
  "override_enabled": true,
  "created_at": "2026-09-21T10:00:00Z",
  "updated_at": "2026-09-21T10:00:00Z"
}
```

**Error Response** (following existing ExtendedHTTPException pattern):
```json
{
  "code": "MODEL_NOT_WHITELISTED",
  "message": "Override model not in project whitelist",
  "details": {
    "invalid_models": ["gpt-4-turbo"],
    "project_whitelist": ["gpt-4", "claude-opus"]
  },
  "help": "Ensure override models are subset of project allowed_models"
}
```

---

## 5. Validation & Error Handling

### 5.1 Set-Time Validation (Override Creation)

**Checks** (in order, fail-fast):
1. User exists and is member of project (via UserProject)
2. Project exists
3. allowed_models is non-empty
4. allowed_models ⊆ project.allowed_models (subset check)
5. No allowed_models in ApplicationEnrichment.disabled_model_ids

**Exceptions**:
- `NotFoundException("User not found" | "Project not found")` — 404
- `ModelNotWhitelistedException("Override models not in project whitelist", details=...)` — 400
- `ModelNotWhitelistedException("Platform has disabled these models", details=...)` — 400
- `ExtendedHTTPException(code="INVALID_INPUT", message="...", details=...)` — 400

### 5.2 Invocation-Time Validation

**At model resolution** (RouterChatModel._agenerate call site):
1. Check platform disablement → immediate deny (hard stop)
2. Check user override if exists → use if model in override list
3. Check project whitelist → use if model in project list
4. Use default model → no exception (safe fallback)

**No re-validation of override against current project whitelist.** If project whitelist narrows after override creation, invocation gracefully falls back through tiers 3 → 4 (project whitelist → default). This avoids per-call database queries and handles project config changes gracefully.

### 5.3 Batch Operation Error Handling

**Transaction scope**: Entire batch wrapped in SQLAlchemy session transaction.

**On validation failure of any user**:
- Catch exception during first failed validation
- Roll back entire transaction (no partial updates)
- Return 400 with failed user details and reason

**Example response**:
```json
{
  "code": "BATCH_VALIDATION_FAILED",
  "message": "Batch operation failed; no users updated",
  "details": {
    "failed_user_id": "<uuid>",
    "reason": "User not a member of this project",
    "operation": "grant",
    "user_ids": ["<uuid>", "<uuid>", "<uuid>"]
  }
}
```

---

## 6. Testing

### 6.1 Unit Tests (test_user_model_override_service.py)

- **set_override**: success, user not in project (NotFoundException), models not in project whitelist (ModelNotWhitelistedException), platform-disabled model in override (ModelNotWhitelistedException), empty allowed_models (400)
- **get_override**: override exists, override not found (None)
- **delete_override**: success, not found (no-op or exception — clarify implementation)
- **get_overrides_by_project**: list with pagination, empty project
- **batch_set_overrides (grant)**: success, first user fails validation (rollback all), partial conflict (rollback all)
- **batch_set_overrides (revoke)**: success, user override not found (no-op), rollback on first failure
- **resolve_model_for_user**: all 4-tier combinations:
  - Platform disables model → hard stop (exception)
  - User override exists, model in override → resolved
  - User override exists, model not in override → fall through to project
  - User override not exists → use project whitelist
  - Project disallows model → fall through to default
  - No default configured → exception (existing behavior)

### 6.2 API Endpoint Tests (test_user_model_override_endpoints.py)

- **POST single override**: success, admin-only gating (403 non-admin), validation errors (400), project not found (404)
- **GET list**: admin-only, pagination works, empty list for new project
- **GET single**: admin-only, found, not found (404)
- **DELETE**: admin-only, success (204), not found (404)
- **POST batch**: success, rollback on first failure, empty user_ids (400), empty allowed_models for grant (400), admin-only (403)

### 6.3 Integration Tests (extend test_model_availability_service.py)

- End-to-end: create override, invoke model through RouterChatModel → verify 4-tier chain applies
- Project whitelist change after override creation → override gracefully degrades at invocation
- Platform disablement → hard stop overrides user override

---

## 7. Backward Compatibility & Deployment

### 7.1 Backward Compatibility

- **Existing projects** (no ApplicationEnrichment row): Default to no platform disablement; model resolution skips tier 1 (or checks empty disabled list).
- **Existing projects** (no user overrides): Continue using project-level whitelist only; tier 2 (user override) skipped.
- **Legacy invocation code** (calls `resolve_model_for_execution` without user_id): Existing method unchanged; routers responsible for calling new `resolve_model_for_execution_with_user_context` when user context available.

### 7.2 Feature Flags (optional, for rollback strategy)

- `PLATFORM_MODEL_OVERRIDE_ENABLED` (env var, default=true) — gates new endpoints and service logic. If false, endpoints return 501 Not Implemented.
- Allows rapid disable if production issues discovered.

### 7.3 Alembic Migration

**New migration file**: `src/external/alembic/versions/<timestamp>_add_user_model_override_and_enrichment_tables.py`

**Changes**:
- Create `user_model_overrides` table (DDL from UserModelOverride)
- Create `application_enrichment` table (DDL from ApplicationEnrichment)
- Add unique constraint on (user_id, project_name)
- Add check constraint on override_enabled and allowed_models

---

## 8. Non-Goals

- **User-level platform disablement** — Platform disablement is always per-project (via ApplicationEnrichment), not per-user.
- **Role/group-based overrides** — This iteration applies overrides to explicit user_ids only. Role/group-based inheritance is a future feature.
- **Async or cached model resolution** — Model resolution remains synchronous; any future caching layer is out of scope.
- **Audit trail with immutable event log** — Override changes logged via activity events (existing pattern), but no separate immutable audit table is created. Regulatory audit requirements are out of scope.
- **UI for override management** — This spec covers API and backend only. Admin UI endpoints are out of scope.
- **Migration of existing user groups to overrides** — No bulk import tool or migration script for legacy user group data.
- **Per-invocation override metadata tracking** — Invocations do not record which tier of the precedence chain resolved the model; only the resolved model is persisted.

---

## 9. Open Risks

- **No FK constraint between UserModelOverride and UserProject** — Overrides can be orphaned if user-project relationship is deleted. Mitigation: add cascade delete or document admin cleanup procedure.
- **Invocation performance** — Each model resolution adds database query for user override. Mitigation: cache override data in-memory per session or accept query latency trade-off.
- **Concurrent override updates** — Batch operations are atomic per transaction, but concurrent individual updates can race on unique constraint. Mitigation: application-layer retry logic or accept constraint violation (rare in practice for admin operations).
- **Project whitelist narrowing after override creation** — Override models may no longer be in project whitelist; no automatic re-validation. Accepted: graceful degradation at invocation (fallback to project default). Admin must clean up overrides manually if project whitelist is intentionally narrowed.
- **Platform disablement integration** — ApplicationEnrichment table is new; no seeding script provided for existing projects. Mitigation: each project gets default ApplicationEnrichment row on first override create, or create script during migration.

---

## 10. Decisions Made

- **UserModelOverride placement**: `src/codemie/core/models.py` (centralized model definition, alongside Application, UserProject).
- **Platform disablement storage**: New ApplicationEnrichment table (per-project) with global default via config env vars. Allows future per-project granularity.
- **Bulk override operations**: POST /batch endpoint with explicit user_ids and atomic transaction. Simpler than role/group-based; future iteration for group support.
- **Override validation timing**: Set-time only (early fail-fast). Invocation-time re-check not implemented; graceful degradation via 4-tier chain if project whitelist changes.
- **Batch error semantics**: All-or-nothing (400 + rollback) rather than 207 partial success. Clearer semantics for admin UX.
- **User-project binding at override**: No explicit FK constraint (only logical check via UserProject table). Prevents cascade deletion issues but allows orphans.

---

## Summary

This spec defines a secure, layered user-level model override system that respects project and platform constraints. The 4-tier precedence chain (platform disable → user override → project whitelist → default) is validated at set-time to prevent invalid overrides, with graceful degradation at invocation time if project configuration changes. The implementation spans database models (UserModelOverride, ApplicationEnrichment), service orchestration (UserModelOverrideService), repository persistence, and API endpoints (single and batch CRUD). Backward compatibility is maintained for legacy projects and invocation patterns. Key risks (orphaned overrides, per-invocation query latency) are documented with mitigations.
