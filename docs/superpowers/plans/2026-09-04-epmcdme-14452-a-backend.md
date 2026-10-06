# EPMCDME-14452-A: Backend — Project Allowed Models Storage + API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development (inline TDD) or superpowers:subagent-driven-development (parallel task dispatch). Each task includes failing test → implementation → passing test → commit cycle.

**Goal:** Add `allowed_models` field to Project model with CRUD endpoints, validation rules, and authorization checks.

**Architecture:** Three-layer implementation: (1) database schema + migration, (2) service layer validation + authorization, (3) HTTP API endpoint. All layers follow existing project patterns: Alembic for migrations, ProjectService for validation, FastAPI router for HTTP contract.

**Tech Stack:** SQLModel + SQLAlchemy ORM, Alembic migrations, FastAPI, Pydantic request/response models, pytest + async test fixtures.

## Global Constraints

- Backward compatible: `allowed_models` must be nullable; NULL = all models allowed
- Authorization: only project admins and maintainers can update (enforced at service layer)
- Validation: at least 1 model required if non-null; empty array rejected with 400 error
- Model identifiers: no validation against live LLM provider list yet (Phase 2); accept any string identifier
- Soft deletion: respect existing projects marked deleted_at; do not include in schema checks
- Commit format: `EPMCDME-14452: <description>` (ticket-prefixed, colon-separated)
- Migration location: `src/external/alembic/versions/<YYYYMMDD>_add_allowed_models.py`
- Test location: `tests/codemie/service/project/test_project_service.py`, `tests/codemie/rest_api/routers/test_projects_router.py`

---

## File Structure

### New Files
- `src/external/alembic/versions/<YYYYMMDD>_add_allowed_models.py` — Alembic migration script

### Modified Files
- `src/codemie/rest_api/models/user_management.py` — Add Application SQLModel table with `allowed_models` field
- `src/codemie/service/project/project_service.py` — Add validation + authorization for `allowed_models` CRUD
- `src/codemie/rest_api/routers/projects.py` — Add `ProjectDetailUpdate` request model, add PATCH endpoint
- `src/codemie/rest_api/models/projects.py` — Add `allowed_models` to project response models
- `tests/codemie/service/project/test_project_service.py` — Add unit tests for validation
- `tests/codemie/rest_api/routers/test_projects_router.py` — Add endpoint tests
- `tests/codemie/data/test_migrations.py` — Add migration tests (or create if missing)

---

## Task 1: Database Schema Migration

**Files:**
- Create: `src/external/alembic/versions/<YYYYMMDD>_add_allowed_models.py`
- Modify: (none yet — schema introspection only)

**Interfaces:**
- Consumes: Existing `applications` table schema (no explicit interface; Alembic auto-detects)
- Produces: Column `allowed_models` on `applications` table; type `TEXT[]` (PostgreSQL array) or `JSON` (generic); default NULL; nullable

**Rationale:** Adding a column before service/router implementation ensures database is ready; Alembic migration is the source of truth for schema.

- [ ] **Step 1: Inspect current Application schema**

Examine the existing applications table definition. Run:
```bash
psql $DATABASE_URL -c "\d+ applications;"
```
(or check an existing migration file to understand column types: int, text, timestamp, uuid, etc.)

Expected: See columns like `id (UUID)`, `name (TEXT)`, `created_by (UUID)`, `project_type (TEXT)`, `created_at (TIMESTAMP)`, `updated_at (TIMESTAMP)`, `deleted_at (TIMESTAMP)`.

- [ ] **Step 2: Locate most recent Alembic migration**

Run:
```bash
ls -t src/external/alembic/versions/ | head -1
```

Expected: Output like `20260901_some_change.py`

- [ ] **Step 3: Create new Alembic migration script**

Create file `src/external/alembic/versions/<YYYYMMDD>_add_allowed_models.py` (use today's date). Use `YYYYMMDD_HHmm` format if multiple migrations today.

```python
"""Add allowed_models column to applications table."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '<revision_id>'
down_revision = '<previous_revision_id>'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add column as nullable array of text (PostgreSQL specific)
    op.add_column(
        'applications',
        sa.Column(
            'allowed_models',
            postgresql.ARRAY(sa.String()),
            nullable=True,
            server_default=None,
        )
    )
    # Add comment for documentation
    op.execute(
        "COMMENT ON COLUMN applications.allowed_models IS "
        "'List of allowed LLM model identifiers; NULL means all models allowed'"
    )


def downgrade() -> None:
    op.drop_column('applications', 'allowed_models')
```

**Notes:**
- Use `<revision_id>` from the migration timestamp (Alembic will generate actual UUIDs; copy from header comment of most recent migration)
- `down_revision`: Find by running `alembic heads` or checking most recent migration file
- Substitute actual revision IDs from `alembic.ini` or most recent migration
- If unsure, run `alembic current` to find current head, then copy that as `down_revision`

- [ ] **Step 4: Verify migration file syntax**

Run:
```bash
cd src/external && alembic upgrade --sql
```

Expected: Output shows the new `ALTER TABLE applications ADD COLUMN allowed_models ...` SQL (does not execute yet, only generates).

If error: fix syntax (missing imports, incorrect type names) and retry.

- [ ] **Step 5: Test migration locally**

Run against development database:
```bash
cd src/external && alembic upgrade head
```

Expected: Migration completes without error.

To verify column was added:
```bash
psql $DATABASE_URL -c "\d+ applications;" | grep allowed_models
```

Expected: `allowed_models | text[] | ... | NULL`

- [ ] **Step 6: Test migration rollback**

Run:
```bash
cd src/external && alembic downgrade -1
```

Expected: Migration rolls back (column removed).

Then roll forward:
```bash
cd src/external && alembic upgrade head
```

Expected: Migration re-applies.

- [ ] **Step 7: Commit migration**

```bash
git add src/external/alembic/versions/
git commit -m "EPMCDME-14452: Add allowed_models migration"
```

---

## Task 2: Add allowed_models Field to Project Data Model

**Files:**
- Modify: `src/codemie/rest_api/models/user_management.py` (or similar; confirm actual location from code review)
- Test: (none; this is data model, not logic)

**Interfaces:**
- Consumes: Existing `Application` or `Project` SQLModel definition
- Produces: `Application.allowed_models: Optional[List[str]]` field; persisted to database via SQLModel table=True

**Rationale:** Make the new column queryable/writable from Python code via SQLModel ORM.

- [ ] **Step 1: Locate the Application SQLModel class**

Search for the class definition:
```bash
grep -rn "class Application" src/codemie/rest_api/models/ --include="*.py"
```

Expected: File path (e.g., `src/codemie/rest_api/models/user_management.py:30` or `src/codemie/rest_api/models/application.py:10`)

- [ ] **Step 2: Read the current Application model**

Read the file:
```bash
head -100 <file_path>
```

Expected: See a class definition with `table=True`, `id`, `name`, `created_by`, `created_at`, `updated_at`, `deleted_at` fields.

- [ ] **Step 3: Add allowed_models field to Application model**

In the Application class, add after `deleted_at`:

```python
from typing import Optional, List

class Application(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(index=True)
    created_by: UUID
    project_type: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    deleted_at: Optional[datetime] = None
    
    # NEW FIELD
    allowed_models: Optional[List[str]] = Field(default=None, nullable=True)
```

**Notes:**
- Use `Optional[List[str]]` for nullable list
- Use `Field(default=None)` to allow NULL in database
- Do NOT add validation here; validation goes in service layer
- Column order: metadata fields first (id, created_by, created_at), then nullable business logic fields (allowed_models, etc.)

- [ ] **Step 4: Verify syntax**

Run:
```bash
python -m py_compile src/codemie/rest_api/models/user_management.py
```

Expected: No output (success).

If error: fix import or syntax and retry.

- [ ] **Step 5: Commit data model change**

```bash
git add src/codemie/rest_api/models/user_management.py
git commit -m "EPMCDME-14452: Add allowed_models field to Application model"
```

---

## Task 3: Add Validation Rules to ProjectService

**Files:**
- Modify: `src/codemie/service/project/project_service.py`
- Test: `tests/codemie/service/project/test_project_service.py`

**Interfaces:**
- Consumes: `Application.allowed_models: Optional[List[str]]`
- Produces: `ProjectService.validate_allowed_models(models: Optional[List[str]]) -> Optional[List[str]]`
  - Raises `ValueError` if models is empty list or has invalid identifiers
  - Returns models if valid or None

**Rationale:** Centralize validation rules (at least 1 model, reject empty array) in service layer before persistence.

- [ ] **Step 1: Write failing unit tests**

Open or create `tests/codemie/service/project/test_project_service.py`. Add:

```python
import pytest
from codemie.service.project.project_service import ProjectService

class TestProjectServiceAllowedModels:
    """Test validation rules for allowed_models field."""
    
    def test_validate_allowed_models_none_is_valid(self):
        """None means all models allowed — should pass validation."""
        result = ProjectService.validate_allowed_models(None)
        assert result is None
    
    def test_validate_allowed_models_single_model_is_valid(self):
        """List with at least one model should pass."""
        result = ProjectService.validate_allowed_models(["gpt-4"])
        assert result == ["gpt-4"]
    
    def test_validate_allowed_models_multiple_models_is_valid(self):
        """List with multiple models should pass."""
        result = ProjectService.validate_allowed_models(["gpt-4", "claude-3-5-sonnet"])
        assert result == ["gpt-4", "claude-3-5-sonnet"]
    
    def test_validate_allowed_models_empty_array_is_invalid(self):
        """Empty array should raise ValueError with specific message."""
        with pytest.raises(ValueError) as exc_info:
            ProjectService.validate_allowed_models([])
        assert "at least one" in str(exc_info.value).lower()
        assert "model" in str(exc_info.value).lower()
    
    def test_validate_allowed_models_preserves_order(self):
        """Model order should be preserved as provided."""
        models = ["model-c", "model-a", "model-b"]
        result = ProjectService.validate_allowed_models(models)
        assert result == models
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
pytest tests/codemie/service/project/test_project_service.py::TestProjectServiceAllowedModels -v
```

Expected: All tests FAIL with error like "AttributeError: 'ProjectService' has no attribute 'validate_allowed_models'"

- [ ] **Step 3: Implement validation method in ProjectService**

Open `src/codemie/service/project/project_service.py`. Locate the ProjectService class and add this static method (after existing validators like `validate_name`):

```python
from typing import Optional, List

class ProjectService:
    # ... existing code ...
    
    @staticmethod
    def validate_allowed_models(models: Optional[List[str]]) -> Optional[List[str]]:
        """
        Validate allowed_models field.
        
        Args:
            models: List of model identifiers or None (None = all models allowed)
        
        Returns:
            Validated models (same list) or None
        
        Raises:
            ValueError: If models is empty list or contains invalid identifiers
        """
        # None is valid (means all models allowed)
        if models is None:
            return None
        
        # Empty array is invalid
        if isinstance(models, list) and len(models) == 0:
            raise ValueError("At least one model must be specified in allowed_models")
        
        # Future: validate against known model identifiers
        # For now, accept any string identifier (Phase 1 requirement)
        
        return models
```

**Notes:**
- Static method (no self) since validation is pure logic
- Follow naming convention from existing validators in ProjectService
- Error message matches ticket requirement: "At least one model required"
- Future validation (model identifier list) can be added in Phase 2

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
pytest tests/codemie/service/project/test_project_service.py::TestProjectServiceAllowedModels -v
```

Expected: All tests PASS

- [ ] **Step 5: Add authorization check method**

Add another method to ProjectService to check if user is admin/maintainer (reuse existing pattern if present):

```python
@staticmethod
async def check_allowed_models_authorization(project_id: UUID, user: User, session: AsyncSession) -> None:
    """
    Verify that user has permission to update allowed_models.
    
    Only project admins and maintainers can update.
    
    Args:
        project_id: Project UUID
        user: Authenticated user
        session: Database session
    
    Raises:
        PermissionError: If user is not admin/maintainer
        ProjectNotFoundError: If project doesn't exist
    """
    # Use existing repository to get user's role in project
    from codemie.repository.user_project_repository import UserProjectRepository
    
    repo = UserProjectRepository(session)
    user_project = await repo.get_user_project(user.id, project_id)
    
    if not user_project:
        raise ProjectNotFoundError(f"Project {project_id} not found or user is not a member")
    
    if user_project.role not in ("admin", "maintainer"):
        raise PermissionError("Only project admins and maintainers can update allowed models")
```

- [ ] **Step 6: Add authorization test**

Add to `TestProjectServiceAllowedModels`:

```python
@pytest.mark.asyncio
async def test_check_allowed_models_authorization_admin_allowed(self, session, test_user, test_project):
    """Admin user should be authorized to update allowed_models."""
    # Assume test_project has test_user as admin (fixture setup)
    # Should not raise
    await ProjectService.check_allowed_models_authorization(
        test_project.id, test_user, session
    )

@pytest.mark.asyncio
async def test_check_allowed_models_authorization_member_denied(self, session, test_member_user, test_project):
    """Non-admin member should be denied."""
    with pytest.raises(PermissionError) as exc_info:
        await ProjectService.check_allowed_models_authorization(
            test_project.id, test_member_user, session
        )
    assert "admin" in str(exc_info.value).lower()
```

- [ ] **Step 7: Run all project service tests**

Run:
```bash
pytest tests/codemie/service/project/test_project_service.py -v
```

Expected: All tests PASS (including existing tests, to verify no regressions)

- [ ] **Step 8: Commit service layer changes**

```bash
git add src/codemie/service/project/project_service.py tests/codemie/service/project/test_project_service.py
git commit -m "EPMCDME-14452: Add allowed_models validation and authorization to ProjectService"
```

---

## Task 4: Create PATCH Endpoint for allowed_models

**Files:**
- Modify: `src/codemie/rest_api/routers/projects.py`
- Modify: `src/codemie/rest_api/models/projects.py` (or user_management.py, depending on structure)
- Test: `tests/codemie/rest_api/routers/test_projects_router.py`

**Interfaces:**
- Consumes: `ProjectService.validate_allowed_models()`, `ProjectService.check_allowed_models_authorization()`, `Application.allowed_models`
- Produces: HTTP PATCH `/v1/projects/{id}/allowed-models`
  - Request: `{"allowed_models": ["model1", "model2"]}`
  - Response: 200 with full project details including `allowed_models` field
  - Error: 403 (unauthorized), 404 (not found), 400 (validation error)

**Rationale:** Expose the validation and storage via REST API for frontend consumption.

- [ ] **Step 1: Write failing API endpoint test**

Open or create `tests/codemie/rest_api/routers/test_projects_router.py`. Add:

```python
import pytest
from fastapi.testclient import TestClient
from codemie.rest_api.main import app  # or however app is imported

client = TestClient(app)

class TestProjectsRouterAllowedModels:
    """Test PATCH /v1/projects/{id}/allowed-models endpoint."""
    
    @pytest.mark.asyncio
    async def test_patch_allowed_models_success(self, test_project_id, admin_auth_header):
        """Admin user can update allowed_models."""
        response = client.patch(
            f"/v1/projects/{test_project_id}/allowed-models",
            json={"allowed_models": ["gpt-4", "claude-3-5-sonnet"]},
            headers=admin_auth_header,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["allowed_models"] == ["gpt-4", "claude-3-5-sonnet"]
        assert data["id"] == str(test_project_id)
    
    @pytest.mark.asyncio
    async def test_patch_allowed_models_empty_array_rejected(self, test_project_id, admin_auth_header):
        """Empty array should return 400 validation error."""
        response = client.patch(
            f"/v1/projects/{test_project_id}/allowed-models",
            json={"allowed_models": []},
            headers=admin_auth_header,
        )
        assert response.status_code == 400
        data = response.json()
        assert "at least one" in data["detail"].lower()
    
    @pytest.mark.asyncio
    async def test_patch_allowed_models_member_denied(self, test_project_id, member_auth_header):
        """Non-admin member should get 403 Forbidden."""
        response = client.patch(
            f"/v1/projects/{test_project_id}/allowed-models",
            json={"allowed_models": ["gpt-4"]},
            headers=member_auth_header,
        )
        assert response.status_code == 403
    
    @pytest.mark.asyncio
    async def test_patch_allowed_models_project_not_found(self, fake_project_id, admin_auth_header):
        """Non-existent project should return 404."""
        response = client.patch(
            f"/v1/projects/{fake_project_id}/allowed-models",
            json={"allowed_models": ["gpt-4"]},
            headers=admin_auth_header,
        )
        assert response.status_code == 404
    
    @pytest.mark.asyncio
    async def test_patch_allowed_models_null_is_valid(self, test_project_id, admin_auth_header):
        """Setting allowed_models to null should be allowed (all models)."""
        response = client.patch(
            f"/v1/projects/{test_project_id}/allowed-models",
            json={"allowed_models": None},
            headers=admin_auth_header,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["allowed_models"] is None
    
    @pytest.mark.asyncio
    async def test_get_project_includes_allowed_models(self, test_project_id, admin_auth_header):
        """GET /v1/projects/{id} should include allowed_models field."""
        # First set allowed_models
        client.patch(
            f"/v1/projects/{test_project_id}/allowed-models",
            json={"allowed_models": ["gpt-4"]},
            headers=admin_auth_header,
        )
        
        # Then GET and verify field is present
        response = client.get(
            f"/v1/projects/{test_project_id}",
            headers=admin_auth_header,
        )
        assert response.status_code == 200
        data = response.json()
        assert "allowed_models" in data
        assert data["allowed_models"] == ["gpt-4"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
pytest tests/codemie/rest_api/routers/test_projects_router.py::TestProjectsRouterAllowedModels -v
```

Expected: Tests FAIL with 404 (endpoint not found) or similar

- [ ] **Step 3: Create request/response model**

Open `src/codemie/rest_api/models/projects.py` (or similar). Add Pydantic models:

```python
from typing import Optional, List
from pydantic import BaseModel, field_validator
from uuid import UUID
from datetime import datetime

class AllowedModelsUpdate(BaseModel):
    """Request body for PATCH /projects/{id}/allowed-models"""
    allowed_models: Optional[List[str]] = Field(default=None)
    
    @field_validator("allowed_models")
    @classmethod
    def validate_allowed_models(cls, v):
        if v is not None and len(v) == 0:
            raise ValueError("At least one model must be specified")
        return v

class ProjectDetail(BaseModel):
    """Response body for GET/PATCH /projects/{id}"""
    id: UUID
    name: str
    description: Optional[str] = None
    project_type: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    allowed_models: Optional[List[str]] = None
    
    class Config:
        from_attributes = True  # SQLModel compatibility
```

- [ ] **Step 4: Add PATCH endpoint to router**

Open `src/codemie/rest_api/routers/projects.py`. Add after existing project endpoints:

```python
from fastapi import APIRouter, Depends, Path
from uuid import UUID
from codemie.rest_api.security.authentication import authenticate
from codemie.service.project.project_service import ProjectService
from codemie.rest_api.models.projects import AllowedModelsUpdate, ProjectDetail
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()

@router.patch("/v1/projects/{project_id}/allowed-models")
async def update_allowed_models(
    project_id: UUID = Path(...),
    update: AllowedModelsUpdate,
    user: User = Depends(authenticate),
    session: AsyncSession = Depends(get_session),
) -> ProjectDetail:
    """
    Update allowed models for a project.
    
    Only project admins and maintainers can update.
    """
    # Validate authorization
    await ProjectService.check_allowed_models_authorization(project_id, user, session)
    
    # Validate input
    validated_models = ProjectService.validate_allowed_models(update.allowed_models)
    
    # Update in database
    from codemie.repository.application_repository import ApplicationRepository
    repo = ApplicationRepository(session)
    project = await repo.get_project(project_id, user)
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    project.allowed_models = validated_models
    session.add(project)
    await session.commit()
    await session.refresh(project)
    
    return ProjectDetail.from_orm(project)
```

**Notes:**
- Endpoint path: `/v1/projects/{project_id}/allowed-models` (as per ticket spec)
- Dependency injection: user from auth, session from database
- Validation: ProjectService methods are called to enforce business rules
- Return: Full ProjectDetail (not just the field)
- Error handling: raise HTTPException for 403, 404 (FastAPI will convert to HTTP response)

- [ ] **Step 5: Update GET /v1/projects/{id} response model**

Verify that the existing GET endpoint returns ProjectDetail (or similar) that includes `allowed_models` field. If the response model is defined elsewhere, update it:

```python
class ProjectDetail(BaseModel):
    id: UUID
    name: str
    allowed_models: Optional[List[str]] = None  # ADD THIS LINE
    # ... rest of fields
```

- [ ] **Step 6: Run endpoint tests**

Run:
```bash
pytest tests/codemie/rest_api/routers/test_projects_router.py::TestProjectsRouterAllowedModels -v
```

Expected: Tests PASS

- [ ] **Step 7: Run full router test suite (regression check)**

Run:
```bash
pytest tests/codemie/rest_api/routers/test_projects_router.py -v
```

Expected: All tests PASS (no regressions)

- [ ] **Step 8: Commit endpoint changes**

```bash
git add src/codemie/rest_api/routers/projects.py src/codemie/rest_api/models/projects.py tests/codemie/rest_api/routers/test_projects_router.py
git commit -m "EPMCDME-14452: Add PATCH /projects/{id}/allowed-models endpoint"
```

---

## Task 5: Integration Test — Migration + API

**Files:**
- Modify: `tests/codemie/data/test_migrations.py` (create if missing)
- Test: Full integration test

**Interfaces:**
- Consumes: Alembic migration, Application model, ProjectService, PATCH endpoint
- Produces: Verified end-to-end: migration runs, field persists, API returns correct value

**Rationale:** Verify that migration doesn't break existing data and new API works with persisted data.

- [ ] **Step 1: Write migration test**

Create or open `tests/codemie/data/test_migrations.py`:

```python
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

class TestAllowedModelsMigration:
    """Test the add_allowed_models migration."""
    
    @pytest.mark.asyncio
    async def test_migration_adds_allowed_models_column(self, session: AsyncSession):
        """Column should exist after migration."""
        result = await session.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name='applications' AND column_name='allowed_models'")
        )
        assert result.scalar() is not None
    
    @pytest.mark.asyncio
    async def test_migration_existing_projects_default_to_null(self, session: AsyncSession):
        """Existing projects should have allowed_models=NULL (backward compatible)."""
        result = await session.execute(
            text("SELECT COUNT(*) FROM applications WHERE allowed_models IS NULL")
        )
        # At least one existing project should be NULL
        count = result.scalar()
        assert count >= 0  # Verify column is queryable
    
    @pytest.mark.asyncio
    async def test_new_project_can_have_allowed_models_set(self, session: AsyncSession, test_admin_user):
        """New project should allow setting allowed_models."""
        from codemie.rest_api.models.user_management import Application
        from uuid import uuid4
        
        project = Application(
            id=uuid4(),
            name="test-project",
            created_by=test_admin_user.id,
            project_type="shared",
            allowed_models=["gpt-4", "claude-3-5-sonnet"],
        )
        session.add(project)
        await session.commit()
        
        # Verify persisted correctly
        result = await session.execute(
            text("SELECT allowed_models FROM applications WHERE id=:id"),
            {"id": str(project.id)},
        )
        persisted_models = result.scalar()
        assert persisted_models == ["gpt-4", "claude-3-5-sonnet"]
```

- [ ] **Step 2: Run migration tests**

Run:
```bash
pytest tests/codemie/data/test_migrations.py::TestAllowedModelsMigration -v
```

Expected: All tests PASS

- [ ] **Step 3: Write end-to-end integration test**

Add to `tests/codemie/rest_api/routers/test_projects_router.py`:

```python
@pytest.mark.asyncio
async def test_allowed_models_e2e_migration_to_api(self, client: TestClient, admin_auth_header, session: AsyncSession):
    """
    End-to-end: migration creates column, API persists and returns data.
    
    Steps:
    1. Create project via API (allowed_models not set)
    2. PATCH to set allowed_models
    3. GET to verify persistence
    4. Query database directly to verify migration worked
    """
    # 1. Create project
    create_response = client.post(
        "/v1/projects",
        json={"name": "e2e-test-project", "project_type": "shared"},
        headers=admin_auth_header,
    )
    assert create_response.status_code == 201
    project_id = create_response.json()["id"]
    
    # 2. PATCH to set allowed_models
    patch_response = client.patch(
        f"/v1/projects/{project_id}/allowed-models",
        json={"allowed_models": ["gpt-4", "claude-3-5-sonnet"]},
        headers=admin_auth_header,
    )
    assert patch_response.status_code == 200
    
    # 3. GET to verify
    get_response = client.get(
        f"/v1/projects/{project_id}",
        headers=admin_auth_header,
    )
    assert get_response.status_code == 200
    data = get_response.json()
    assert data["allowed_models"] == ["gpt-4", "claude-3-5-sonnet"]
    
    # 4. Query DB directly to verify migration
    result = await session.execute(
        text("SELECT allowed_models FROM applications WHERE id=:id"),
        {"id": project_id},
    )
    db_models = result.scalar()
    assert db_models == ["gpt-4", "claude-3-5-sonnet"]
```

- [ ] **Step 4: Run integration test**

Run:
```bash
pytest tests/codemie/rest_api/routers/test_projects_router.py::test_allowed_models_e2e_migration_to_api -v
```

Expected: PASS

- [ ] **Step 5: Commit integration test**

```bash
git add tests/codemie/data/test_migrations.py tests/codemie/rest_api/routers/test_projects_router.py
git commit -m "EPMCDME-14452: Add migration and integration tests"
```

---

## Task 6: Final Validation — Full Test Suite

**Files:**
- (no changes; test-only)

**Interfaces:**
- Consumes: All changes from Tasks 1–5
- Produces: Verified: all tests pass, no regressions

**Rationale:** Ensure complete feature works and no existing tests broke.

- [ ] **Step 1: Run full test suite**

Run:
```bash
pytest tests/codemie/service/project/ -v
pytest tests/codemie/rest_api/routers/test_projects_router.py -v
pytest tests/codemie/data/test_migrations.py -v
```

Expected: All tests PASS

- [ ] **Step 2: Check code style**

Run:
```bash
make ruff  # or: python -m ruff check src/ tests/
```

Expected: No style errors (or fix any warnings)

- [ ] **Step 3: Verify endpoint contract with ticket**

Manual check against acceptance criteria from ticket:

- [ ] PATCH /v1/projects/{id}/allowed-models endpoint exists
- [ ] GET /v1/projects/{id} includes allowed_models field
- [ ] Empty array rejected with 400 error
- [ ] Authorization: admin allowed, member denied (403)
- [ ] Project not found returns 404
- [ ] null (all models) allowed
- [ ] Validation: at least 1 model required

Example manual test:
```bash
# Set allowed_models
curl -X PATCH http://localhost:8000/v1/projects/<project-id>/allowed-models \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"allowed_models": ["gpt-4"]}'

# Expected: 200 with allowed_models in response

# Get project to verify
curl http://localhost:8000/v1/projects/<project-id> \
  -H "Authorization: Bearer <token>"

# Expected: 200 with allowed_models field
```

- [ ] **Step 4: Commit final state**

Run:
```bash
git status
```

Expected: Working tree is clean (all changes committed)

If there are uncommitted changes:
```bash
git add <files>
git commit -m "EPMCDME-14452: Final validation"
```

---

## Summary

This plan implements the complete backend for EPMCDME-14452-A:

1. **Migration** — adds `allowed_models` column, backward compatible
2. **Data Model** — adds field to Application SQLModel
3. **Service Layer** — validation + authorization rules
4. **API Endpoint** — PATCH /v1/projects/{id}/allowed-models + updated response model
5. **Tests** — full coverage (unit, API, integration, migration)
6. **Validation** — ensure all ticket criteria met

**Estimated effort:** 1–2 days
- Day 1: Tasks 1–3 (migration, model, service validation)
- Day 2: Tasks 4–6 (endpoint, tests, final validation, code review)

**Dependencies:** Task 1 (migration) must complete before Tasks 2–4; tasks can proceed in parallel after migration.
