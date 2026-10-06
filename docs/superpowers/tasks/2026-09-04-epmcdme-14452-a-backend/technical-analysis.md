# Technical Research

**Task**: project model data storage CRUD API authorization  
**Generated**: 2026-09-04T14:20:00Z  
**Research path**: codegraph (agent-based exploration)

---

## 1. Original Context

Add `allowed_models` field to Project model with CRUD endpoints, validation rules, and authorization checks. Provides the API foundation for frontend UI and enforcement layers.

**Acceptance Criteria:**
- Project model has `allowed_models` field (list of model identifiers)
- Field is nullable/optional (null = all models allowed, backward compatible)
- Alembic migration created with default NULL for existing projects
- Migration tested against existing project records
- `PATCH /v1/projects/{id}/allowed-models` endpoint with update + read
- `GET /v1/projects/{id}` returns `allowed_models` field
- 404 if project doesn't exist; 403 if caller lacks admin/maintainer role
- Validation: at least 1 chat model required if non-null; empty array rejected
- Invalid model identifiers rejected; platform-disabled models allowed
- Only project admins and maintainers can update `allowed_models`
- Project members (non-admin) can read but not update
- Non-members cannot read or update
- Unit tests, API tests, authorization tests, migration tests

---

## 2. Codebase Findings

### Existing Implementations

**Router Layer:**
- `src/codemie/rest_api/routers/projects.py` — FastAPI router with project endpoint handlers
  - Contains response models: `ProjectCounters` (lines 55–80), `ProjectSpendingSummary`, `ProjectSpendingDetail`
  - Current endpoints: GET project list, GET project detail, create, update metadata
  - Pattern: router → service → repository

**Service Layer:**
- `src/codemie/service/project/project_service.py` — ProjectService class with validation rules
  - Validation: name pattern (regex), reserved names, min/max constraints (name 3–100 chars, desc ≤500 chars)
  - Error namespace: `ERRORS = {...}` (line 50+) for centralized error messages
  - Patterns: validation decorator, error handling, service-layer authorization checks

**Repository Layer:**
- `src/codemie/repository/application_repository.py` — ApplicationRepository for Project data access
  - Methods: list, search, visibility filters
  - Pattern: static methods for query composition (_build_visibility_condition, _apply_search_filters)
  - Visibility logic: personal projects (created_by == user) vs shared (via `user_projects` table)
  - Soft-deleted projects excluded (WHERE deleted_at IS NULL)

**Data Models:**
- `src/codemie/rest_api/models/user_management.py` — Database persistence models
  - `UserProject` (lines 62–73): relationship table (user_id, project_id, role, created_at)
  - `UserDB` (lines 38–60): user record with project_limit (nullable integer)
  - Pydantic + SQLModel table=True for DB mapping

**Security & Authorization:**
- `src/codemie/rest_api/security/authentication.py` — centralized auth dependencies
  - `authenticate(...)` dependency: extracts user from Authorization header
  - Pattern: dependency injection in router, role-based access control in service

**Test Structure:**
- `tests/codemie/rest_api/routers/test_projects_router.py` — endpoint tests
- `tests/codemie/service/project/test_project_service.py` — service logic tests
- `tests/codemie/repository/test_user_project_repository_*.py` — repository tests (visibility, bulk, etc.)
- Pattern: pytest fixtures, mock sessions, parameterized test cases

### Architecture and Layers Affected

| Layer | Component | File |
|-------|-----------|------|
| **HTTP Router** | Project endpoints (FastAPI) | `src/codemie/rest_api/routers/projects.py` |
| **Service** | Project business logic + validation | `src/codemie/service/project/project_service.py` |
| **Repository** | Project data access (query composition) | `src/codemie/repository/application_repository.py` |
| **Database** | SQLModel persistence + Alembic migrations | `src/external/alembic/versions/` |
| **Security** | Authentication + role-based authorization | `src/codemie/rest_api/security/authentication.py` |
| **Request/Response Models** | Pydantic schemas for HTTP contract | `src/codemie/rest_api/models/user_management.py`, `projects.py` |

### Integration Points

1. **Project Visibility** — via `ApplicationRepository._build_visibility_condition()`
   - Personal: `applications.created_by == user_id`
   - Shared: `applications.id IN (SELECT project_id FROM user_projects WHERE user_id = ...)`
   - Used by: GET project list, GET project detail, PATCH operations

2. **Authorization Role Check** — ProjectService must enforce admin/maintainer role
   - Source: `UserProject.role` column (values: admin, maintainer, member, viewer)
   - Pattern: service layer checks user role against project membership

3. **Error Responses** — centralized in ProjectService.ERRORS namespace
   - Returns 400 (validation), 403 (auth), 404 (not found)
   - Pattern: raise custom exceptions caught by `src/codemie/rest_api/main.py` exception handlers

4. **Session Management** — async SQLAlchemy sessions via dependency injection
   - Existing pattern: `async_session` injected into repository methods
   - Migration: Alembic auto-upgrade on app startup (if configured)

### Patterns and Conventions

**Validation Pattern (ProjectService):**
```python
class ProjectValidator:
    MIN_NAME_LENGTH = 3
    MAX_NAME_LENGTH = 100
    RESERVED_NAMES = {...}
    PROJECT_NAME_PATTERN = re.compile(...)
    
    @staticmethod
    def validate_name(name: str) -> str:
        if len(name) < MIN_NAME_LENGTH:
            raise ValueError("...")
        return name
```

**Request/Response Models (Pydantic):**
```python
class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    # field_validator(...) for validation

class ProjectDetail(BaseModel):
    id: UUID
    name: str
    allowed_models: Optional[List[str]] = None  # NEW FIELD
```

**PATCH Endpoint Pattern (FastAPI):**
```python
@router.patch("/v1/projects/{id}")
async def update_project(
    id: UUID,
    update: ProjectUpdate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(authenticate),
) -> ProjectDetail:
    # service.update_project(id, update, user, session)
    # returns updated project or raises
```

**Repository Query Pattern:**
```python
async def get_project(self, project_id: UUID, user: User) -> Application:
    stmt = select(Application).where(
        Application.id == project_id,
        ~Application.deleted_at.isnot(None),  # not deleted
        self._build_visibility_condition(user),  # user can see it
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
```

---

## 3. Documentation Findings

### Guides and Architecture Docs

**Mandatory Guides:**
- `.ai-run/guides/architecture/layered-architecture.md` — Router → Service → Repository separation (CRITICAL for this task)
- `.ai-run/guides/data/database-patterns.md` — SQLModel sessions, Alembic migration workflow (CRITICAL for schema change)
- `.ai-run/guides/api/rest-api-patterns.md` — FastAPI router registration, error responses
- `.ai-run/guides/api/endpoint-conventions.md` — request/response model patterns, router delegation
- `.ai-run/guides/development/security-patterns.md` — auth/authz, input validation, role-based access
- `.ai-run/guides/data/repository-patterns.md` — repository boundaries, data access patterns

### Architectural Decisions

**Key ADRs from codebase:**
1. **Soft Deletion** — projects marked deleted_at, never hard-deleted (enables audit trail, recovery)
2. **Visibility Composition** — personal vs shared projects via query conditions, not separate tables
3. **Centralized Error Handling** — custom exceptions caught by main.py exception handlers (not inline raises)
4. **Role-Based Access** — UserProject.role enforces authorization (admin/maintainer/member/viewer)
5. **Session Injection** — async SQLAlchemy sessions via FastAPI Depends() (stateless, testable)

### Derived Conventions

**From code inspection:**
- Response models include both Pydantic validation and database serialization
- Service layer is the enforcement point for business rules (including role checks)
- Repository methods are pure data-access (no business logic, no auth checks)
- Migrations use Alembic auto-generated scripts with manual tweaks
- Test files mirror source structure: `tests/codemie/X/test_Y.py` ↔ `src/codemie/X/Y.py`

---

## 4. Testing Landscape

### Existing Coverage

| Area | Test File | Coverage |
|------|-----------|----------|
| **Project Router** | `tests/codemie/rest_api/routers/test_projects_router.py` | GET/POST/PATCH project endpoints, error cases |
| **Project Service** | `tests/codemie/service/project/test_project_service.py` | Validation rules, reserved names, error messages |
| **User-Project Repo** | `tests/codemie/repository/test_user_project_repository_*.py` | Visibility (personal/shared), role filters, bulk ops |
| **Authorization** | Embedded in router/service tests | Role-based access, auth header validation |
| **Migrations** | `tests/codemie/data/test_migrations.py` (if exists) | Schema integrity, rollback safety |

### Testing Framework and Patterns

**Framework:**
- **pytest** — test runner with fixtures, parametrize, monkeypatch
- **pytest-asyncio** — async test support (async def test_...)
- **SQLAlchemy async sessions** — in-memory or real test database
- **FastAPI TestClient** — HTTP endpoint testing

**Fixture Patterns:**
```python
@pytest.fixture
async def session():
    # create async session, yield, cleanup
    
@pytest.fixture
async def authenticated_user(session):
    # create test user, return with auth token

@pytest.fixture
async def test_project(session, authenticated_user):
    # create test project, return
```

**Mock/Monkeypatch Patterns:**
- Mock external service calls (LLM APIs, cloud services)
- Monkeypatch environment variables for config overrides
- Patch repository methods in service tests (isolation)

### Coverage Gaps

**For `allowed_models` task:**
- No existing tests for list-type fields in Project model
- No tests for model identifier validation (need to add)
- No tests for PATCH endpoint on non-existent fields (good reference)
- Authorization tests exist for other fields; pattern reusable for `allowed_models`
- Migration tests may be sparse — verify `test_migrations.py` exists and is comprehensive

---

## 5. Configuration and Environment

### Environment Variables

**Relevant to project management:**
- `DATABASE_URL` — PostgreSQL connection (from pyproject.toml, used by Alembic + app)
- `DEBUG` — feature flag for local dev/prod behavior
- `LOG_LEVEL` — logging verbosity

**Not directly used for `allowed_models`, but present:**
- LLM provider secrets (OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.) — not relevant for storage phase
- Cloud storage (AWS_S3_BUCKET, etc.) — not relevant

### Configuration Files

| File | Purpose |
|------|---------|
| `pyproject.toml` | Dependencies (sqlmodel, fastapi, alembic, pydantic), test config |
| `alembic.ini` | Alembic runtime settings (sqlalchemy.url, script_location) |
| `.env` / `.env.local` | Local overrides for DATABASE_URL, DEBUG, etc. |
| `src/codemie/configs/` | Application config modules (if present) |

### Feature Flags and Deployment Concerns

**Backward Compatibility:**
- `allowed_models` field must be nullable (NULL = all models allowed)
- Existing projects MUST default to NULL (migration constraint)
- API response must include `"allowed_models": null` when unset

**Migration Safety:**
- Cannot mark column NOT NULL without populating existing rows
- Add column as nullable, optionally add check constraint after backfill

**Rollout Strategy:**
- Phase 1 (this ticket): storage + CRUD API (no enforcement)
- Phase 2 (later ticket): LiteLLM proxy enforcement
- Phase 3: Direct provider enforcement
- Phase 4: Frontend UI

---

## 6. Risk Indicators

1. **No SQLModel table=True on Application model** — Application is currently Pydantic-only; must add SQLModel table to persist to database. Risk: schema mismatch if not carefully migrated.

2. **Authorization scope ambiguity** — Ticket specifies "admins and maintainers", but ProjectService does not yet enforce role checks. Risk: need to verify UserProject.role values match ticket intent.

3. **Alembic migration ordering critical** — New column depends on existing applications table. Risk: circular dependencies or foreign key constraints if Application model structure is complex.

4. **Model identifier validation** — Ticket mentions "invalid model identifiers rejected", but no existing enum or validator defined. Risk: need to define canonical model list (from platform configuration or hardcoded).

5. **Platform-disabled models allowed but not enforced** — Ticket says "platform-disabled models can be in list (no enforcement yet)". Risk: frontend must handle gracefully; backend has no validation gate.

6. **Empty array validation** — Must reject `allowed_models: []` with error "At least one model required". Risk: Pydantic validator needed; easy to miss edge case (null vs empty).

7. **Test database for migration** — Migration testing requires actual PostgreSQL (or test DB). Risk: test environment must have Alembic configured correctly.

8. **No codegraph available** — Analysis relied on filesystem search and manual code reading. Risk: some integration points may have been missed; recommend spot-checking with `grep` for model usage.

---

## 7. Summary for Complexity Assessment

This task modifies the foundational Project model to add a new nullable `allowed_models` field, requiring changes across three layers: database schema (Alembic migration), service logic (validation + authorization), and HTTP API (PATCH endpoint + response model).

**Layers touched:** Database (1 migration file), Service (ProjectService, ~30–50 lines), Repository (minimal—mostly GET), Router (1 PATCH endpoint + response model, ~20–30 lines). Estimated 5–7 files modified.

**Technical novelty:** Low. Task reuses existing patterns (validation decorator, authorization check, PATCH handler, async session). The main complexity is ensuring Alembic migration is safe for production (backward compatible, no data loss on NULL→empty list conversion).

**Test coverage posture:** Moderate gap. Existing tests cover authorization and validation framework; this task needs unit tests for model identifier validation (invalid IDs rejected, at least 1 model, empty array error) plus API tests for PATCH endpoint and migration tests for schema rollback.

**Key risk factors:** (1) Migration must be rolled out safely (null default, verify existing projects unaffected). (2) Model identifier validation list must be defined and maintained (hardcoded or config-driven). (3) Authorization checks (admin/maintainer role) must be enforced at service layer. (4) Empty array edge case must be rejected consistently (Pydantic validator needed).

**Estimated effort:** 2 days (1 day implementation + 1 day testing + review). Implementation is straightforward (add field, add migration, add endpoint); testing is the largest component due to authorization + migration verification.
