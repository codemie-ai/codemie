# User-Level Model Override Enforcement — Implementation Plan

> **Superseded.** This is the first attempt at EPMCDME-15082. The work continued in `../2026-10-07-epmcdme-15082-user-level-model-override/`, whose `spec.md` is the source of truth. This document describes a 4-tier chain with platform disablement and a different data model, which were dropped. It is kept as history only.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement user-level model override enforcement allowing project admins to configure granular LLM model access for individual or bulk-selected users, with a 4-tier precedence chain (platform disablement → user override → project whitelist → default) and full backward compatibility.

**Architecture:** The feature adds three new database models (UserModelOverride, ApplicationEnrichment for platform disablement) and extends ModelAvailabilityService with user-scoped resolution logic. API endpoints (admin-only, POST/GET/DELETE single + batch) orchestrate via new UserModelOverrideService. Model resolution integrates into existing RouterChatModel invocation flow without breaking legacy code paths. Validation occurs at override set-time (early fail-fast) and gracefully degrades at invocation time if project whitelist changes.

**Tech Stack:** SQLModel (ORM), SQLAlchemy (session/constraints), FastAPI (routers), pytest (TDD), Alembic (migrations).

**Spec:** `docs/superpowers/tasks/2026-09-18-user-level-model-override/spec.md`

## Global Constraints

- Unique constraint on UserModelOverride (user_id, project_name): one override per user per project.
- Check constraint on UserModelOverride: override_enabled=true requires non-empty allowed_models.
- All override endpoints require is_application_admin(project_name) check.
- Override validation at set-time: allowed_models ⊆ project.allowed_models AND no platform-disabled models.
- Batch operations: atomic (all-or-nothing); first validation failure rolls back entire transaction.
- Backward compatibility: legacy projects without ApplicationEnrichment default to no platform disablement; legacy invocations without user_id use existing resolve_model_for_execution.
- Error responses follow ExtendedHTTPException pattern (code, message, details, help).

---

## File Structure

**New Files (6):**
1. `src/codemie/core/models.py` — Add UserModelOverride, ApplicationEnrichment SQLModel tables
2. `src/codemie/data/repositories/user_model_override_repository.py` — CRUD operations for overrides
3. `src/codemie/service/user/user_model_override_service.py` — Orchestration: set, get, delete, batch, resolve
4. `src/codemie/rest_api/routers/user_model_override_router.py` — FastAPI endpoints (admin-only CRUD + batch)
5. `src/external/alembic/versions/<timestamp>_add_user_model_override_and_enrichment_tables.py` — Schema migration
6. `tests/codemie/service/user/test_user_model_override_service.py` — Comprehensive unit tests

**Modified Files (4):**
1. `src/codemie/service/llm/model_availability_service.py` — Add resolve_model_for_execution_with_user_context (4-tier precedence)
2. `src/codemie/core/router_chat_model.py` — Integrate user-scoped model resolution at invocation point
3. `src/codemie/configs/config.py` — Add PLATFORM_MODEL_OVERRIDE_ENABLED, PLATFORM_DISABLED_MODELS env vars
4. `tests/codemie/service/llm/test_model_availability_service.py` — Add 4-tier precedence chain tests

**Test Files (2):**
1. `tests/codemie/rest_api/routers/test_user_model_override_endpoints.py` — API endpoint tests (admin gating, validation, batch)
2. (Extend existing `test_model_availability_service.py` for integration tests)

---

## Task Breakdown

### Task 1: Add UserModelOverride and ApplicationEnrichment SQLModel Tables

**Files:**
- Modify: `src/codemie/core/models.py` — Add two new table classes

**Interfaces:**
- Produces: `UserModelOverride` class (UUID id, UUID user_id FK, str project_name FK, list[str] allowed_models, bool override_enabled, datetime created_at, datetime updated_at; unique constraint on (user_id, project_name); check constraint on override_enabled)
- Produces: `ApplicationEnrichment` class (UUID id, str project_name FK unique, list[str] disabled_model_ids, datetime created_at, datetime updated_at)

**Test-first: yes — Tests will verify table definitions can be instantiated and serialized**

- [ ] **Step 1: Write failing test for UserModelOverride model**

Create `tests/codemie/core/test_user_model_override_model.py`:

```python
from uuid import uuid4
from datetime import datetime
from codemie.core.models import UserModelOverride, BaseModelWithSQLSupport

def test_user_model_override_instantiation():
    """Verify UserModelOverride can be created with required fields."""
    user_id = uuid4()
    project_name = "test-project"
    allowed_models = ["gpt-4", "claude-opus"]
    
    override = UserModelOverride(
        user_id=user_id,
        project_name=project_name,
        allowed_models=allowed_models,
        override_enabled=True
    )
    
    assert override.user_id == user_id
    assert override.project_name == project_name
    assert override.allowed_models == allowed_models
    assert override.override_enabled is True
    assert isinstance(override.created_at, datetime)
    assert isinstance(override.updated_at, datetime)

def test_application_enrichment_instantiation():
    """Verify ApplicationEnrichment can be created with disabled models list."""
    project_name = "test-project"
    disabled_models = ["old-model-v1"]
    
    enrichment = ApplicationEnrichment(
        project_name=project_name,
        disabled_model_ids=disabled_models
    )
    
    assert enrichment.project_name == project_name
    assert enrichment.disabled_model_ids == disabled_models
    assert isinstance(enrichment.created_at, datetime)
    assert isinstance(enrichment.updated_at, datetime)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/codemie/core/test_user_model_override_model.py -v
```

Expected: FAIL with ImportError (UserModelOverride, ApplicationEnrichment not defined)

- [ ] **Step 3: Implement UserModelOverride and ApplicationEnrichment in models.py**

In `src/codemie/core/models.py`, add after existing tables (e.g., after UserProject):

```python
from sqlalchemy import Column, ARRAY, String, UniqueConstraint, CheckConstraint

class UserModelOverride(BaseModelWithSQLSupport):
    """
    User-level override for LLM model access within a project.
    
    Respects project whitelist constraints and platform disablement.
    One override per (user_id, project_name) pair.
    """
    __tablename__ = "user_model_overrides"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    project_name: str = Field(foreign_key="applications.id", nullable=False, index=True)
    allowed_models: list[str] = Field(sa_column=Column(ARRAY(String)), nullable=False)
    override_enabled: bool = Field(default=True, nullable=False)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, nullable=False)
    
    __table_args__ = (
        UniqueConstraint("user_id", "project_name", name="uq_user_model_override_user_project"),
        CheckConstraint(
            "NOT (override_enabled = true AND array_length(allowed_models, 1) IS NULL)",
            name="ck_override_enabled_has_models"
        ),
    )


class ApplicationEnrichment(BaseModelWithSQLSupport):
    """
    Per-project enrichment data, including platform-disabled model list.
    
    Extends Application model with project-specific platform constraints.
    """
    __tablename__ = "application_enrichment"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_name: str = Field(foreign_key="applications.id", nullable=False, unique=True, index=True)
    disabled_model_ids: list[str] = Field(
        sa_column=Column(ARRAY(String)),
        default_factory=list,
        nullable=False
    )
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, nullable=False)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/codemie/core/test_user_model_override_model.py -v
```

Expected: PASS (both tests pass)

- [ ] **Step 5: Commit**

```bash
git add src/codemie/core/models.py tests/codemie/core/test_user_model_override_model.py
git commit -m "feat: add UserModelOverride and ApplicationEnrichment tables"
```

---

### Task 2: Create UserModelOverrideRepository

**Files:**
- Create: `src/codemie/data/repositories/user_model_override_repository.py`
- Test: `tests/codemie/data/repositories/test_user_model_override_repository.py`

**Interfaces:**
- Consumes: UserModelOverride, Application (from models.py), SQLAlchemy Session
- Produces: UserModelOverrideRepository class with methods:
  - `get_by_user_and_project(session, user_id: UUID, project_name: str) -> UserModelOverride | None`
  - `get_by_project(session, project_name: str, skip: int, limit: int) -> list[UserModelOverride]`
  - `create(session, user_id: UUID, project_name: str, allowed_models: list[str]) -> UserModelOverride`
  - `update(session, user_id: UUID, project_name: str, allowed_models: list[str]) -> UserModelOverride`
  - `delete(session, user_id: UUID, project_name: str) -> None`
  - `delete_all_by_project(session, project_name: str) -> None`

**Test-first: yes — Tests verify CRUD operations with mocked Session**

- [ ] **Step 1: Write failing test for repository CRUD**

Create `tests/codemie/data/repositories/test_user_model_override_repository.py`:

```python
from uuid import uuid4
from unittest.mock import MagicMock, patch
from codemie.data.repositories.user_model_override_repository import UserModelOverrideRepository
from codemie.core.models import UserModelOverride

def test_get_by_user_and_project_found():
    """Verify get_by_user_and_project returns override when found."""
    session = MagicMock()
    user_id = uuid4()
    project_name = "test-project"
    mock_override = MagicMock(spec=UserModelOverride)
    mock_override.id = uuid4()
    
    session.query().filter().filter().first.return_value = mock_override
    
    repo = UserModelOverrideRepository()
    result = repo.get_by_user_and_project(session, user_id, project_name)
    
    assert result == mock_override

def test_get_by_user_and_project_not_found():
    """Verify get_by_user_and_project returns None when not found."""
    session = MagicMock()
    user_id = uuid4()
    project_name = "test-project"
    
    session.query().filter().filter().first.return_value = None
    
    repo = UserModelOverrideRepository()
    result = repo.get_by_user_and_project(session, user_id, project_name)
    
    assert result is None

def test_create_new_override():
    """Verify create adds and returns new override."""
    session = MagicMock()
    user_id = uuid4()
    project_name = "test-project"
    allowed_models = ["gpt-4", "claude-opus"]
    
    session.add = MagicMock()
    session.flush = MagicMock()
    
    repo = UserModelOverrideRepository()
    result = repo.create(session, user_id, project_name, allowed_models)
    
    assert result.user_id == user_id
    assert result.project_name == project_name
    assert result.allowed_models == allowed_models
    session.add.assert_called_once()

def test_delete_removes_override():
    """Verify delete removes override."""
    session = MagicMock()
    user_id = uuid4()
    project_name = "test-project"
    
    mock_override = MagicMock(spec=UserModelOverride)
    session.query().filter().filter().first.return_value = mock_override
    
    repo = UserModelOverrideRepository()
    repo.delete(session, user_id, project_name)
    
    session.delete.assert_called_once()
    session.flush.assert_called_once()

def test_get_by_project_pagination():
    """Verify get_by_project respects skip and limit."""
    session = MagicMock()
    project_name = "test-project"
    mock_overrides = [MagicMock(spec=UserModelOverride) for _ in range(3)]
    
    session.query().filter().offset().limit().all.return_value = mock_overrides
    
    repo = UserModelOverrideRepository()
    result = repo.get_by_project(session, project_name, skip=10, limit=20)
    
    assert len(result) == 3
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/codemie/data/repositories/test_user_model_override_repository.py -v
```

Expected: FAIL with ImportError (UserModelOverrideRepository not defined)

- [ ] **Step 3: Implement repository**

Create `src/codemie/data/repositories/user_model_override_repository.py`:

```python
from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy import and_

from codemie.core.models import UserModelOverride


class UserModelOverrideRepository:
    """Repository for UserModelOverride CRUD operations."""
    
    @staticmethod
    def get_by_user_and_project(
        session: Session, user_id: UUID, project_name: str
    ) -> UserModelOverride | None:
        """Fetch override for a specific user in a project."""
        return (
            session.query(UserModelOverride)
            .filter(UserModelOverride.user_id == user_id)
            .filter(UserModelOverride.project_name == project_name)
            .first()
        )
    
    @staticmethod
    def get_by_project(
        session: Session, project_name: str, skip: int = 0, limit: int = 100
    ) -> list[UserModelOverride]:
        """Fetch all overrides for a project with pagination."""
        return (
            session.query(UserModelOverride)
            .filter(UserModelOverride.project_name == project_name)
            .offset(skip)
            .limit(limit)
            .all()
        )
    
    @staticmethod
    def create(
        session: Session, user_id: UUID, project_name: str, allowed_models: list[str]
    ) -> UserModelOverride:
        """Create a new override."""
        override = UserModelOverride(
            user_id=user_id,
            project_name=project_name,
            allowed_models=allowed_models,
            override_enabled=True
        )
        session.add(override)
        session.flush()
        return override
    
    @staticmethod
    def update(
        session: Session, user_id: UUID, project_name: str, allowed_models: list[str]
    ) -> UserModelOverride:
        """Update existing override's allowed_models."""
        override = UserModelOverrideRepository.get_by_user_and_project(
            session, user_id, project_name
        )
        if override:
            override.allowed_models = allowed_models
            session.flush()
        return override
    
    @staticmethod
    def delete(session: Session, user_id: UUID, project_name: str) -> None:
        """Delete override for a user in a project."""
        override = UserModelOverrideRepository.get_by_user_and_project(
            session, user_id, project_name
        )
        if override:
            session.delete(override)
            session.flush()
    
    @staticmethod
    def delete_all_by_project(session: Session, project_name: str) -> None:
        """Delete all overrides for a project (admin cleanup)."""
        session.query(UserModelOverride).filter(
            UserModelOverride.project_name == project_name
        ).delete()
        session.flush()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/codemie/data/repositories/test_user_model_override_repository.py -v
```

Expected: PASS (all tests pass)

- [ ] **Step 5: Commit**

```bash
git add src/codemie/data/repositories/user_model_override_repository.py tests/codemie/data/repositories/test_user_model_override_repository.py
git commit -m "feat: add UserModelOverrideRepository with CRUD operations"
```

---

### Task 3: Create UserModelOverrideService with Validation

**Files:**
- Create: `src/codemie/service/user/user_model_override_service.py`
- Test: `tests/codemie/service/user/test_user_model_override_service.py`

**Interfaces:**
- Consumes: UserModelOverrideRepository, Application (repository), UserProject (repository), UserModelOverride, ApplicationEnrichment, ModelAvailabilityService
- Produces: UserModelOverrideService class with methods:
  - `set_override(session, user_id: UUID, project_name: str, allowed_models: list[str]) -> UserModelOverride` — create/update override with validation
  - `get_override(session, user_id: UUID, project_name: str) -> UserModelOverride | None`
  - `delete_override(session, user_id: UUID, project_name: str) -> None`
  - `get_overrides_by_project(session, project_name: str, skip: int, limit: int) -> list[UserModelOverride]`
  - `batch_set_overrides(session, project_name: str, user_ids: list[UUID], operation: str, allowed_models: list[str] | None) -> list[UserModelOverride]` — atomic batch grant/revoke
  - `_validate_override_models(session, project_name: str, allowed_models: list[str]) -> None` — private validation helper

**Test-first: yes — Tests cover set/get/delete with validation, batch operations, and error cases**

- [ ] **Step 1: Write failing test for service validation and CRUD**

Create `tests/codemie/service/user/test_user_model_override_service.py`:

```python
from uuid import uuid4
from unittest.mock import MagicMock, patch, PropertyMock
import pytest
from codemie.service.user.user_model_override_service import UserModelOverrideService
from codemie.core.models import UserModelOverride, Application, UserProject, ApplicationEnrichment
from codemie.core.exceptions import ModelNotWhitelistedException, NotFoundException

def test_set_override_success():
    """Verify set_override creates override when validation passes."""
    session = MagicMock()
    user_id = uuid4()
    project_name = "test-project"
    allowed_models = ["gpt-4", "claude-opus"]
    
    # Mock User exists and is in project
    mock_user_project = MagicMock(spec=UserProject)
    session.query(UserProject).filter().filter().first.return_value = mock_user_project
    
    # Mock Project exists with whitelist containing requested models
    mock_app = MagicMock(spec=Application)
    mock_app.allowed_models = ["gpt-4", "claude-opus", "gpt-3.5"]
    session.query(Application).filter().first.return_value = mock_app
    
    # Mock ApplicationEnrichment (no disabled models)
    mock_enrichment = MagicMock(spec=ApplicationEnrichment)
    mock_enrichment.disabled_model_ids = []
    session.query(ApplicationEnrichment).filter().first.return_value = mock_enrichment
    
    # Mock repository returns new override
    mock_override = MagicMock(spec=UserModelOverride)
    with patch('codemie.service.user.user_model_override_service.UserModelOverrideRepository.get_by_user_and_project', return_value=None):
        with patch('codemie.service.user.user_model_override_service.UserModelOverrideRepository.create', return_value=mock_override):
            service = UserModelOverrideService()
            result = service.set_override(session, user_id, project_name, allowed_models)
    
    assert result == mock_override

def test_set_override_user_not_in_project():
    """Verify set_override raises NotFoundException if user not in project."""
    session = MagicMock()
    user_id = uuid4()
    project_name = "test-project"
    allowed_models = ["gpt-4"]
    
    # Mock user not found in project
    session.query(UserProject).filter().filter().first.return_value = None
    
    service = UserModelOverrideService()
    with pytest.raises(NotFoundException):
        service.set_override(session, user_id, project_name, allowed_models)

def test_set_override_models_not_in_whitelist():
    """Verify set_override raises ModelNotWhitelistedException if models not in project whitelist."""
    session = MagicMock()
    user_id = uuid4()
    project_name = "test-project"
    allowed_models = ["gpt-4-turbo"]  # NOT in whitelist
    
    # Mock User exists and is in project
    mock_user_project = MagicMock(spec=UserProject)
    session.query(UserProject).filter().filter().first.return_value = mock_user_project
    
    # Mock Project exists with whitelist NOT containing requested models
    mock_app = MagicMock(spec=Application)
    mock_app.allowed_models = ["gpt-4", "claude-opus"]  # turbo not here
    session.query(Application).filter().first.return_value = mock_app
    
    service = UserModelOverrideService()
    with pytest.raises(ModelNotWhitelistedException):
        service.set_override(session, user_id, project_name, allowed_models)

def test_set_override_platform_disabled_model():
    """Verify set_override raises ModelNotWhitelistedException if any model is platform-disabled."""
    session = MagicMock()
    user_id = uuid4()
    project_name = "test-project"
    allowed_models = ["gpt-4", "old-model"]  # old-model is disabled
    
    # Mock User exists and is in project
    mock_user_project = MagicMock(spec=UserProject)
    session.query(UserProject).filter().filter().first.return_value = mock_user_project
    
    # Mock Project exists with whitelist containing both
    mock_app = MagicMock(spec=Application)
    mock_app.allowed_models = ["gpt-4", "old-model", "claude-opus"]
    session.query(Application).filter().first.return_value = mock_app
    
    # Mock ApplicationEnrichment with old-model disabled
    mock_enrichment = MagicMock(spec=ApplicationEnrichment)
    mock_enrichment.disabled_model_ids = ["old-model"]
    session.query(ApplicationEnrichment).filter().first.return_value = mock_enrichment
    
    service = UserModelOverrideService()
    with pytest.raises(ModelNotWhitelistedException):
        service.set_override(session, user_id, project_name, allowed_models)

def test_get_override():
    """Verify get_override returns existing override or None."""
    session = MagicMock()
    user_id = uuid4()
    project_name = "test-project"
    
    mock_override = MagicMock(spec=UserModelOverride)
    with patch('codemie.service.user.user_model_override_service.UserModelOverrideRepository.get_by_user_and_project', return_value=mock_override):
        service = UserModelOverrideService()
        result = service.get_override(session, user_id, project_name)
    
    assert result == mock_override

def test_delete_override():
    """Verify delete_override calls repository delete."""
    session = MagicMock()
    user_id = uuid4()
    project_name = "test-project"
    
    with patch('codemie.service.user.user_model_override_service.UserModelOverrideRepository.delete') as mock_delete:
        service = UserModelOverrideService()
        service.delete_override(session, user_id, project_name)
    
    mock_delete.assert_called_once_with(session, user_id, project_name)

def test_batch_set_overrides_grant():
    """Verify batch_set_overrides with grant operation adds overrides for all users."""
    session = MagicMock()
    project_name = "test-project"
    user_ids = [uuid4(), uuid4(), uuid4()]
    allowed_models = ["gpt-4"]
    
    # Mock project exists with whitelist
    mock_app = MagicMock(spec=Application)
    mock_app.allowed_models = ["gpt-4", "claude-opus"]
    session.query(Application).filter().first.return_value = mock_app
    
    # Mock ApplicationEnrichment (no disabled)
    mock_enrichment = MagicMock(spec=ApplicationEnrichment)
    mock_enrichment.disabled_model_ids = []
    session.query(ApplicationEnrichment).filter().first.return_value = mock_enrichment
    
    # Mock all users in project
    mock_user_project = MagicMock(spec=UserProject)
    session.query(UserProject).filter().filter().first.return_value = mock_user_project
    
    # Mock repository returns overrides
    mock_overrides = [MagicMock(spec=UserModelOverride) for _ in user_ids]
    with patch('codemie.service.user.user_model_override_service.UserModelOverrideRepository.get_by_user_and_project', return_value=None):
        with patch('codemie.service.user.user_model_override_service.UserModelOverrideRepository.create', side_effect=mock_overrides):
            service = UserModelOverrideService()
            result = service.batch_set_overrides(session, project_name, user_ids, "grant", allowed_models)
    
    assert len(result) == 3

def test_batch_set_overrides_revoke():
    """Verify batch_set_overrides with revoke operation removes overrides."""
    session = MagicMock()
    project_name = "test-project"
    user_ids = [uuid4(), uuid4()]
    
    with patch('codemie.service.user.user_model_override_service.UserModelOverrideRepository.delete') as mock_delete:
        service = UserModelOverrideService()
        result = service.batch_set_overrides(session, project_name, user_ids, "revoke", None)
    
    assert mock_delete.call_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/codemie/service/user/test_user_model_override_service.py -v
```

Expected: FAIL with ImportError (UserModelOverrideService not defined)

- [ ] **Step 3: Implement UserModelOverrideService**

Create `src/codemie/service/user/user_model_override_service.py`:

```python
from uuid import UUID
from sqlalchemy.orm import Session

from codemie.core.models import (
    UserModelOverride, Application, UserProject, ApplicationEnrichment
)
from codemie.data.repositories.user_model_override_repository import UserModelOverrideRepository
from codemie.core.exceptions import ModelNotWhitelistedException, NotFoundException


class UserModelOverrideService:
    """Service for managing user-level model overrides."""
    
    @staticmethod
    def set_override(
        session: Session, user_id: UUID, project_name: str, allowed_models: list[str]
    ) -> UserModelOverride:
        """
        Create or update override for a user in a project.
        
        Validates that:
        1. User is a member of the project
        2. Project exists
        3. allowed_models is non-empty
        4. allowed_models ⊆ project.allowed_models
        5. No allowed_models in platform disablement list
        """
        # Check user exists and is in project
        user_project = (
            session.query(UserProject)
            .filter(UserProject.user_id == user_id)
            .filter(UserProject.project_name == project_name)
            .first()
        )
        if not user_project:
            raise NotFoundException(f"User {user_id} not found in project {project_name}")
        
        # Check project exists
        project = (
            session.query(Application)
            .filter(Application.id == project_name)
            .first()
        )
        if not project:
            raise NotFoundException(f"Project {project_name} not found")
        
        # Validate models
        UserModelOverrideService._validate_override_models(
            session, project_name, allowed_models
        )
        
        # Check if override already exists; if so, update; else create
        existing = UserModelOverrideRepository.get_by_user_and_project(
            session, user_id, project_name
        )
        if existing:
            return UserModelOverrideRepository.update(
                session, user_id, project_name, allowed_models
            )
        else:
            return UserModelOverrideRepository.create(
                session, user_id, project_name, allowed_models
            )
    
    @staticmethod
    def get_override(
        session: Session, user_id: UUID, project_name: str
    ) -> UserModelOverride | None:
        """Fetch override for a user in a project."""
        return UserModelOverrideRepository.get_by_user_and_project(
            session, user_id, project_name
        )
    
    @staticmethod
    def delete_override(
        session: Session, user_id: UUID, project_name: str
    ) -> None:
        """Delete override for a user in a project."""
        UserModelOverrideRepository.delete(session, user_id, project_name)
    
    @staticmethod
    def get_overrides_by_project(
        session: Session, project_name: str, skip: int = 0, limit: int = 100
    ) -> list[UserModelOverride]:
        """List all overrides for a project."""
        return UserModelOverrideRepository.get_by_project(
            session, project_name, skip, limit
        )
    
    @staticmethod
    def batch_set_overrides(
        session: Session,
        project_name: str,
        user_ids: list[UUID],
        operation: str,
        allowed_models: list[str] | None = None
    ) -> list[UserModelOverride]:
        """
        Apply or revoke override for multiple users (atomic transaction).
        
        Args:
            operation: 'grant' (create/update) or 'revoke' (delete)
            allowed_models: Required for 'grant', ignored for 'revoke'
        
        Returns: List of modified overrides (created/updated/deleted)
        Raises: ModelNotWhitelistedException on first validation failure (entire batch rolled back)
        """
        results = []
        
        if operation == "grant":
            if not allowed_models:
                raise ValueError("allowed_models required for grant operation")
            
            # Validate models once (same for all users)
            UserModelOverrideService._validate_override_models(
                session, project_name, allowed_models
            )
            
            # Apply override to each user
            for user_id in user_ids:
                # Check user in project
                user_project = (
                    session.query(UserProject)
                    .filter(UserProject.user_id == user_id)
                    .filter(UserProject.project_name == project_name)
                    .first()
                )
                if not user_project:
                    raise NotFoundException(
                        f"User {user_id} not found in project {project_name}"
                    )
                
                # Create or update override
                existing = UserModelOverrideRepository.get_by_user_and_project(
                    session, user_id, project_name
                )
                if existing:
                    override = UserModelOverrideRepository.update(
                        session, user_id, project_name, allowed_models
                    )
                else:
                    override = UserModelOverrideRepository.create(
                        session, user_id, project_name, allowed_models
                    )
                results.append(override)
        
        elif operation == "revoke":
            # Remove override from each user
            for user_id in user_ids:
                UserModelOverrideRepository.delete(session, user_id, project_name)
                # For revoke, we don't track deleted overrides in results
                # This is consistent with DELETE semantics
        
        return results
    
    @staticmethod
    def _validate_override_models(
        session: Session, project_name: str, allowed_models: list[str]
    ) -> None:
        """
        Validate override models against project whitelist and platform disablement.
        
        Raises: ModelNotWhitelistedException if validation fails
        """
        if not allowed_models:
            raise ValueError("allowed_models cannot be empty")
        
        # Get project
        project = (
            session.query(Application)
            .filter(Application.id == project_name)
            .first()
        )
        if not project:
            raise NotFoundException(f"Project {project_name} not found")
        
        # Check allowed_models ⊆ project.allowed_models
        # If project has no whitelist (None), allow all (backward compat)
        if project.allowed_models:
            invalid_models = [m for m in allowed_models if m not in project.allowed_models]
            if invalid_models:
                raise ModelNotWhitelistedException(
                    f"Override models not in project whitelist",
                    details={
                        "invalid_models": invalid_models,
                        "project_whitelist": project.allowed_models
                    }
                )
        
        # Check platform disablement
        enrichment = (
            session.query(ApplicationEnrichment)
            .filter(ApplicationEnrichment.project_name == project_name)
            .first()
        )
        if enrichment and enrichment.disabled_model_ids:
            disabled_in_override = [
                m for m in allowed_models if m in enrichment.disabled_model_ids
            ]
            if disabled_in_override:
                raise ModelNotWhitelistedException(
                    f"Platform has disabled these models",
                    details={
                        "disabled_models": disabled_in_override,
                        "platform_disablement": enrichment.disabled_model_ids
                    }
                )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/codemie/service/user/test_user_model_override_service.py -v
```

Expected: PASS (all tests pass)

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/user/user_model_override_service.py tests/codemie/service/user/test_user_model_override_service.py
git commit -m "feat: add UserModelOverrideService with validation"
```

---

### Task 4: Extend ModelAvailabilityService with 4-Tier Precedence Chain

**Files:**
- Modify: `src/codemie/service/llm/model_availability_service.py` — Add resolve_model_for_execution_with_user_context method
- Modify: `tests/codemie/service/llm/test_model_availability_service.py` — Add comprehensive precedence chain tests

**Interfaces:**
- Consumes: UserModelOverrideRepository, ApplicationEnrichment repository/model, existing resolve_model_for_execution
- Produces: `resolve_model_for_execution_with_user_context(session, requested_model: str, user_id: UUID, project_name: str) -> str` — new method applying 4-tier precedence

**Test-first: yes — Tests verify all 16 combinations of 4-tier precedence chain**

- [ ] **Step 1: Write failing tests for 4-tier precedence chain**

Add to `tests/codemie/service/llm/test_model_availability_service.py`:

```python
from uuid import uuid4
from unittest.mock import MagicMock, patch
import pytest
from codemie.service.llm.model_availability_service import ModelAvailabilityService
from codemie.core.models import (
    Application, ApplicationEnrichment, UserModelOverride
)
from codemie.core.exceptions import ModelNotWhitelistedException

class TestModelAvailabilityServiceWithUserContext:
    """Test 4-tier precedence chain for user-scoped model resolution."""
    
    def test_tier1_platform_disablement_hard_stop(self):
        """Tier 1: Platform disablement blocks model even if user/project allow."""
        session = MagicMock()
        user_id = uuid4()
        project_name = "test-project"
        requested_model = "old-model"
        
        # Mock Application with no whitelist (legacy, all allowed)
        mock_app = MagicMock(spec=Application)
        mock_app.allowed_models = None  # No whitelist
        mock_app.default_model = "gpt-4"
        
        # Mock ApplicationEnrichment with disabled models
        mock_enrichment = MagicMock(spec=ApplicationEnrichment)
        mock_enrichment.disabled_model_ids = ["old-model"]
        
        # Mock user override allows it
        mock_override = MagicMock(spec=UserModelOverride)
        mock_override.allowed_models = ["old-model"]
        mock_override.override_enabled = True
        
        with patch('codemie.service.llm.model_availability_service.Application.get_by_id', return_value=mock_app):
            with patch('codemie.service.llm.model_availability_service.ApplicationEnrichment'):
                session.query().filter().first.return_value = mock_enrichment
                with patch('codemie.service.llm.model_availability_service.UserModelOverrideRepository.get_by_user_and_project', return_value=mock_override):
                    service = ModelAvailabilityService()
                    with pytest.raises(ModelNotWhitelistedException):
                        service.resolve_model_for_execution_with_user_context(
                            session, requested_model, user_id, project_name
                        )
    
    def test_tier2_user_override_allowed(self):
        """Tier 2: User override allows model → use it."""
        session = MagicMock()
        user_id = uuid4()
        project_name = "test-project"
        requested_model = "gpt-4"
        
        # Mock Application with different whitelist
        mock_app = MagicMock(spec=Application)
        mock_app.allowed_models = ["claude-opus"]  # gpt-4 NOT in whitelist
        mock_app.default_model = "claude-opus"
        
        # Mock ApplicationEnrichment (no disablement)
        mock_enrichment = MagicMock(spec=ApplicationEnrichment)
        mock_enrichment.disabled_model_ids = []
        
        # Mock user override allows gpt-4
        mock_override = MagicMock(spec=UserModelOverride)
        mock_override.allowed_models = ["gpt-4"]
        mock_override.override_enabled = True
        
        with patch('codemie.service.llm.model_availability_service.Application.get_by_id', return_value=mock_app):
            session.query().filter().first.return_value = mock_enrichment
            with patch('codemie.service.llm.model_availability_service.UserModelOverrideRepository.get_by_user_and_project', return_value=mock_override):
                service = ModelAvailabilityService()
                result = service.resolve_model_for_execution_with_user_context(
                    session, requested_model, user_id, project_name
                )
        
        assert result == "gpt-4"
    
    def test_tier3_project_whitelist_allowed(self):
        """Tier 3: No user override; project whitelist allows → use it."""
        session = MagicMock()
        user_id = uuid4()
        project_name = "test-project"
        requested_model = "gpt-4"
        
        # Mock Application whitelist allows gpt-4
        mock_app = MagicMock(spec=Application)
        mock_app.allowed_models = ["gpt-4", "claude-opus"]
        mock_app.default_model = "claude-opus"
        
        # Mock ApplicationEnrichment (no disablement)
        mock_enrichment = MagicMock(spec=ApplicationEnrichment)
        mock_enrichment.disabled_model_ids = []
        
        # Mock no user override
        with patch('codemie.service.llm.model_availability_service.Application.get_by_id', return_value=mock_app):
            session.query().filter().first.return_value = mock_enrichment
            with patch('codemie.service.llm.model_availability_service.UserModelOverrideRepository.get_by_user_and_project', return_value=None):
                service = ModelAvailabilityService()
                result = service.resolve_model_for_execution_with_user_context(
                    session, requested_model, user_id, project_name
                )
        
        assert result == "gpt-4"
    
    def test_tier4_default_model_fallback(self):
        """Tier 4: Model not in override/whitelist → use default."""
        session = MagicMock()
        user_id = uuid4()
        project_name = "test-project"
        requested_model = "unknown-model"
        
        # Mock Application
        mock_app = MagicMock(spec=Application)
        mock_app.allowed_models = ["gpt-4", "claude-opus"]
        mock_app.default_model = "gpt-4"
        
        # Mock ApplicationEnrichment (no disablement)
        mock_enrichment = MagicMock(spec=ApplicationEnrichment)
        mock_enrichment.disabled_model_ids = []
        
        # Mock no user override
        with patch('codemie.service.llm.model_availability_service.Application.get_by_id', return_value=mock_app):
            session.query().filter().first.return_value = mock_enrichment
            with patch('codemie.service.llm.model_availability_service.UserModelOverrideRepository.get_by_user_and_project', return_value=None):
                service = ModelAvailabilityService()
                result = service.resolve_model_for_execution_with_user_context(
                    session, requested_model, user_id, project_name
                )
        
        assert result == "gpt-4"
    
    def test_precedence_user_override_blocks_project_whitelist(self):
        """User override does NOT have model → fall back to project whitelist."""
        session = MagicMock()
        user_id = uuid4()
        project_name = "test-project"
        requested_model = "gpt-4"
        
        # Mock Application
        mock_app = MagicMock(spec=Application)
        mock_app.allowed_models = ["gpt-4", "claude-opus"]
        mock_app.default_model = "claude-opus"
        
        # Mock ApplicationEnrichment (no disablement)
        mock_enrichment = MagicMock(spec=ApplicationEnrichment)
        mock_enrichment.disabled_model_ids = []
        
        # Mock user override does NOT include gpt-4
        mock_override = MagicMock(spec=UserModelOverride)
        mock_override.allowed_models = ["claude-opus"]
        mock_override.override_enabled = True
        
        with patch('codemie.service.llm.model_availability_service.Application.get_by_id', return_value=mock_app):
            session.query().filter().first.return_value = mock_enrichment
            with patch('codemie.service.llm.model_availability_service.UserModelOverrideRepository.get_by_user_and_project', return_value=mock_override):
                service = ModelAvailabilityService()
                result = service.resolve_model_for_execution_with_user_context(
                    session, requested_model, user_id, project_name
                )
        
        # Should use default since gpt-4 not in override and project allows it
        # (Actually, project allows it, so should return gpt-4)
        assert result == "gpt-4"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/codemie/service/llm/test_model_availability_service.py::TestModelAvailabilityServiceWithUserContext -v
```

Expected: FAIL with AttributeError (resolve_model_for_execution_with_user_context not defined)

- [ ] **Step 3: Implement resolve_model_for_execution_with_user_context in ModelAvailabilityService**

In `src/codemie/service/llm/model_availability_service.py`, add:

```python
from uuid import UUID
from codemie.core.models import ApplicationEnrichment, UserModelOverride
from codemie.data.repositories.user_model_override_repository import UserModelOverrideRepository
from codemie.core.exceptions import ModelNotWhitelistedException


class ModelAvailabilityService:
    """Existing class — add new method below."""
    
    # ... (existing methods: validate_model_for_asset_creation, resolve_model_for_execution, etc.)
    
    @staticmethod
    def resolve_model_for_execution_with_user_context(
        session,
        requested_model: str,
        user_id: UUID,
        project_name: str
    ) -> str:
        """
        Resolve model at invocation time with user-scoped override.
        
        4-tier precedence chain:
        1. Platform disablement (hard stop) — requested_model in ApplicationEnrichment.disabled_model_ids
        2. User override (if exists and enabled) — requested_model in override.allowed_models
        3. Project whitelist (if exists) — requested_model in project.allowed_models
        4. Default model — project.default_model or platform default
        
        Returns resolved model ID or raises ModelNotWhitelistedException if no valid model found.
        """
        # Get project
        project = Application.get_by_id(session, project_name)
        if not project:
            raise NotFoundException(f"Project {project_name} not found")
        
        # Tier 1: Check platform disablement
        enrichment = (
            session.query(ApplicationEnrichment)
            .filter(ApplicationEnrichment.project_name == project_name)
            .first()
        )
        if enrichment and enrichment.disabled_model_ids:
            if requested_model in enrichment.disabled_model_ids:
                raise ModelNotWhitelistedException(
                    f"Platform has disabled model {requested_model}",
                    details={"disabled_model": requested_model}
                )
        
        # Tier 2: Check user override
        override = UserModelOverrideRepository.get_by_user_and_project(
            session, user_id, project_name
        )
        if override and override.override_enabled:
            if requested_model in override.allowed_models:
                return requested_model
        
        # Tier 3: Check project whitelist
        if project.allowed_models:
            if requested_model in project.allowed_models:
                return requested_model
        
        # Tier 4: Return default or raise
        if project.default_model:
            return project.default_model
        
        # If no default and model not found in any tier
        raise ModelNotWhitelistedException(
            f"Model {requested_model} not allowed for this user/project combination"
        )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/codemie/service/llm/test_model_availability_service.py::TestModelAvailabilityServiceWithUserContext -v
```

Expected: PASS (all precedence tests pass)

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/llm/model_availability_service.py tests/codemie/service/llm/test_model_availability_service.py
git commit -m "feat: add 4-tier precedence chain to ModelAvailabilityService"
```

---

### Task 5: Create UserModelOverride API Router

**Files:**
- Create: `src/codemie/rest_api/routers/user_model_override_router.py`
- Test: `tests/codemie/rest_api/routers/test_user_model_override_endpoints.py`

**Interfaces:**
- Consumes: User (from security context), UserModelOverrideService, FastAPI (routing), database session
- Produces: FastAPI router with endpoints:
  - POST /projects/{project_name}/user-model-overrides — single user override
  - GET /projects/{project_name}/user-model-overrides — list overrides for project
  - GET /projects/{project_name}/user-model-overrides/{user_id} — fetch one override
  - DELETE /projects/{project_name}/user-model-overrides/{user_id} — remove override
  - POST /projects/{project_name}/user-model-overrides/batch — bulk grant/revoke (atomic)

**Test-first: yes — Tests verify admin gating, validation, and batch atomicity**

- [ ] **Step 1: Write failing tests for API endpoints**

Create `tests/codemie/rest_api/routers/test_user_model_override_endpoints.py`:

```python
from uuid import uuid4
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from codemie.rest_api.main import app
from codemie.core.models import UserModelOverride
from codemie.core.exceptions import ModelNotWhitelistedException, NotFoundException

client = TestClient(app)

def test_post_user_model_override_success():
    """Verify POST /projects/{project}/user-model-overrides creates override."""
    project_name = "test-project"
    user_id = str(uuid4())
    
    payload = {
        "user_id": user_id,
        "allowed_models": ["gpt-4", "claude-opus"]
    }
    
    with patch('codemie.rest_api.routers.user_model_override_router.authenticate') as mock_auth:
        with patch('codemie.rest_api.routers.user_model_override_router.get_session') as mock_session:
            mock_user = MagicMock()
            mock_user.is_application_admin.return_value = True
            mock_auth.return_value = mock_user
            
            mock_session_obj = MagicMock()
            mock_session.return_value = mock_session_obj
            
            mock_override = MagicMock(spec=UserModelOverride)
            mock_override.id = uuid4()
            mock_override.user_id = uuid4()
            mock_override.project_name = project_name
            mock_override.allowed_models = ["gpt-4", "claude-opus"]
            
            with patch('codemie.rest_api.routers.user_model_override_router.UserModelOverrideService.set_override', return_value=mock_override):
                response = client.post(
                    f"/projects/{project_name}/user-model-overrides",
                    json=payload,
                    headers={"Authorization": "Bearer token"}
                )
    
    assert response.status_code == 200

def test_post_user_model_override_non_admin():
    """Verify POST requires admin role."""
    project_name = "test-project"
    user_id = str(uuid4())
    
    payload = {
        "user_id": user_id,
        "allowed_models": ["gpt-4"]
    }
    
    with patch('codemie.rest_api.routers.user_model_override_router.authenticate') as mock_auth:
        mock_user = MagicMock()
        mock_user.is_application_admin.return_value = False
        mock_auth.return_value = mock_user
        
        response = client.post(
            f"/projects/{project_name}/user-model-overrides",
            json=payload,
            headers={"Authorization": "Bearer token"}
        )
    
    assert response.status_code == 403

def test_get_user_model_overrides_list():
    """Verify GET /projects/{project}/user-model-overrides lists overrides."""
    project_name = "test-project"
    
    with patch('codemie.rest_api.routers.user_model_override_router.authenticate') as mock_auth:
        with patch('codemie.rest_api.routers.user_model_override_router.get_session') as mock_session:
            mock_user = MagicMock()
            mock_user.is_application_admin.return_value = True
            mock_auth.return_value = mock_user
            
            mock_session_obj = MagicMock()
            mock_session.return_value = mock_session_obj
            
            mock_overrides = [MagicMock(spec=UserModelOverride) for _ in range(3)]
            
            with patch('codemie.rest_api.routers.user_model_override_router.UserModelOverrideService.get_overrides_by_project', return_value=mock_overrides):
                response = client.get(
                    f"/projects/{project_name}/user-model-overrides",
                    headers={"Authorization": "Bearer token"}
                )
    
    assert response.status_code == 200

def test_delete_user_model_override():
    """Verify DELETE /projects/{project}/user-model-overrides/{user_id} removes override."""
    project_name = "test-project"
    user_id = str(uuid4())
    
    with patch('codemie.rest_api.routers.user_model_override_router.authenticate') as mock_auth:
        with patch('codemie.rest_api.routers.user_model_override_router.get_session') as mock_session:
            mock_user = MagicMock()
            mock_user.is_application_admin.return_value = True
            mock_auth.return_value = mock_user
            
            mock_session_obj = MagicMock()
            mock_session.return_value = mock_session_obj
            
            with patch('codemie.rest_api.routers.user_model_override_router.UserModelOverrideService.delete_override'):
                response = client.delete(
                    f"/projects/{project_name}/user-model-overrides/{user_id}",
                    headers={"Authorization": "Bearer token"}
                )
    
    assert response.status_code == 204

def test_post_batch_grant():
    """Verify POST /projects/{project}/user-model-overrides/batch grants overrides atomically."""
    project_name = "test-project"
    user_ids = [str(uuid4()), str(uuid4())]
    
    payload = {
        "operation": "grant",
        "user_ids": user_ids,
        "allowed_models": ["gpt-4"]
    }
    
    with patch('codemie.rest_api.routers.user_model_override_router.authenticate') as mock_auth:
        with patch('codemie.rest_api.routers.user_model_override_router.get_session') as mock_session:
            mock_user = MagicMock()
            mock_user.is_application_admin.return_value = True
            mock_auth.return_value = mock_user
            
            mock_session_obj = MagicMock()
            mock_session.return_value = mock_session_obj
            
            mock_overrides = [MagicMock(spec=UserModelOverride) for _ in user_ids]
            
            with patch('codemie.rest_api.routers.user_model_override_router.UserModelOverrideService.batch_set_overrides', return_value=mock_overrides):
                response = client.post(
                    f"/projects/{project_name}/user-model-overrides/batch",
                    json=payload,
                    headers={"Authorization": "Bearer token"}
                )
    
    assert response.status_code == 200

def test_post_batch_validation_failure_rolls_back():
    """Verify batch operation rolls back on validation failure."""
    project_name = "test-project"
    user_ids = [str(uuid4())]
    
    payload = {
        "operation": "grant",
        "user_ids": user_ids,
        "allowed_models": ["invalid-model"]
    }
    
    with patch('codemie.rest_api.routers.user_model_override_router.authenticate') as mock_auth:
        with patch('codemie.rest_api.routers.user_model_override_router.get_session') as mock_session:
            mock_user = MagicMock()
            mock_user.is_application_admin.return_value = True
            mock_auth.return_value = mock_user
            
            mock_session_obj = MagicMock()
            mock_session.return_value = mock_session_obj
            
            with patch('codemie.rest_api.routers.user_model_override_router.UserModelOverrideService.batch_set_overrides', side_effect=ModelNotWhitelistedException("Invalid model")):
                response = client.post(
                    f"/projects/{project_name}/user-model-overrides/batch",
                    json=payload,
                    headers={"Authorization": "Bearer token"}
                )
    
    assert response.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/codemie/rest_api/routers/test_user_model_override_endpoints.py -v
```

Expected: FAIL with ImportError (user_model_override_router module not found)

- [ ] **Step 3: Implement UserModelOverride API router**

Create `src/codemie/rest_api/routers/user_model_override_router.py`:

```python
from uuid import UUID
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.rest_api.models.base_models import get_session
from codemie.service.user.user_model_override_service import UserModelOverrideService
from codemie.core.models import UserModelOverride
from codemie.core.exceptions import ModelNotWhitelistedException, NotFoundException, ExtendedHTTPException


# Response Models
class UserModelOverrideResponse(BaseModel):
    """Response model for UserModelOverride."""
    id: UUID
    user_id: UUID
    project_name: str
    allowed_models: List[str]
    override_enabled: bool
    created_at: str
    updated_at: str
    
    class Config:
        from_attributes = True


# Request Models
class CreateUserModelOverrideRequest(BaseModel):
    """Request body for creating user model override."""
    user_id: UUID
    allowed_models: List[str] = Field(..., min_items=1)


class BatchUserModelOverrideRequest(BaseModel):
    """Request body for batch override operations."""
    operation: str = Field(..., pattern="^(grant|revoke)$")
    user_ids: List[UUID] = Field(..., min_items=1)
    allowed_models: List[str] | None = None


# Router
router = APIRouter(prefix="/projects", tags=["user-model-overrides"])


@router.post("/{project_name}/user-model-overrides", response_model=UserModelOverrideResponse)
def create_user_model_override(
    project_name: str,
    request: CreateUserModelOverrideRequest,
    user: User = Depends(authenticate),
    session: Session = Depends(get_session)
) -> UserModelOverrideResponse:
    """Create or update model override for a user in a project (admin only)."""
    
    # Admin check
    if not user.is_application_admin(project_name):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    try:
        override = UserModelOverrideService.set_override(
            session, request.user_id, project_name, request.allowed_models
        )
        session.commit()
        return UserModelOverrideResponse.from_orm(override)
    except (ModelNotWhitelistedException, NotFoundException) as e:
        session.rollback()
        raise HTTPException(status_code=e.status_code if hasattr(e, 'status_code') else 400, detail=str(e))
    except Exception as e:
        session.rollback()
        raise


@router.get("/{project_name}/user-model-overrides", response_model=List[UserModelOverrideResponse])
def list_user_model_overrides(
    project_name: str,
    skip: int = 0,
    limit: int = 100,
    user: User = Depends(authenticate),
    session: Session = Depends(get_session)
) -> List[UserModelOverrideResponse]:
    """List all model overrides for a project (admin only)."""
    
    # Admin check
    if not user.is_application_admin(project_name):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    overrides = UserModelOverrideService.get_overrides_by_project(
        session, project_name, skip, limit
    )
    return [UserModelOverrideResponse.from_orm(o) for o in overrides]


@router.get("/{project_name}/user-model-overrides/{user_id}", response_model=UserModelOverrideResponse)
def get_user_model_override(
    project_name: str,
    user_id: UUID,
    user: User = Depends(authenticate),
    session: Session = Depends(get_session)
) -> UserModelOverrideResponse:
    """Fetch model override for a specific user (admin only)."""
    
    # Admin check
    if not user.is_application_admin(project_name):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    override = UserModelOverrideService.get_override(session, user_id, project_name)
    if not override:
        raise HTTPException(status_code=404, detail="Override not found")
    
    return UserModelOverrideResponse.from_orm(override)


@router.delete("/{project_name}/user-model-overrides/{user_id}", status_code=204)
def delete_user_model_override(
    project_name: str,
    user_id: UUID,
    user: User = Depends(authenticate),
    session: Session = Depends(get_session)
) -> None:
    """Delete model override for a user (admin only)."""
    
    # Admin check
    if not user.is_application_admin(project_name):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    try:
        UserModelOverrideService.delete_override(session, user_id, project_name)
        session.commit()
    except Exception as e:
        session.rollback()
        raise


@router.post("/{project_name}/user-model-overrides/batch", response_model=List[UserModelOverrideResponse])
def batch_user_model_overrides(
    project_name: str,
    request: BatchUserModelOverrideRequest,
    user: User = Depends(authenticate),
    session: Session = Depends(get_session)
) -> List[UserModelOverrideResponse]:
    """Batch grant/revoke model overrides (admin only, atomic)."""
    
    # Admin check
    if not user.is_application_admin(project_name):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Validate request
    if request.operation == "grant" and not request.allowed_models:
        raise HTTPException(status_code=400, detail="allowed_models required for grant operation")
    
    try:
        overrides = UserModelOverrideService.batch_set_overrides(
            session, project_name, request.user_ids, request.operation, request.allowed_models
        )
        session.commit()
        return [UserModelOverrideResponse.from_orm(o) for o in overrides] if overrides else []
    except (ModelNotWhitelistedException, NotFoundException) as e:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        session.rollback()
        raise
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/codemie/rest_api/routers/test_user_model_override_endpoints.py -v
```

Expected: PASS (all endpoint tests pass)

- [ ] **Step 5: Register router in FastAPI main app**

In `src/codemie/rest_api/main.py`, add:

```python
from codemie.rest_api.routers.user_model_override_router import router as user_model_override_router

app.include_router(user_model_override_router)
```

- [ ] **Step 6: Commit**

```bash
git add src/codemie/rest_api/routers/user_model_override_router.py tests/codemie/rest_api/routers/test_user_model_override_endpoints.py src/codemie/rest_api/main.py
git commit -m "feat: add API endpoints for user model override management"
```

---

### Task 6: Integrate Model Resolution at RouterChatModel Invocation Point

**Files:**
- Modify: `src/codemie/core/router_chat_model.py` — Update _agenerate to call resolve_model_for_execution_with_user_context
- Test: `tests/codemie/core/test_router_chat_model.py` — Verify user context flows through

**Interfaces:**
- Consumes: ModelAvailabilityService.resolve_model_for_execution_with_user_context
- Produces: Updated RouterChatModel._agenerate that passes user_id through resolution chain

**Test-first: yes — Test verifies user override is applied at invocation time**

- [ ] **Step 1: Write failing test for RouterChatModel with user context**

Create or update `tests/codemie/core/test_router_chat_model.py`:

```python
from unittest.mock import MagicMock, patch, AsyncMock
from uuid import uuid4
import pytest
from codemie.core.router_chat_model import RouterChatModel

@pytest.mark.asyncio
async def test_agenerate_with_user_context():
    """Verify _agenerate passes user_id through model resolution."""
    user_id = uuid4()
    project_name = "test-project"
    requested_model = "gpt-4"
    resolved_model = "gpt-4"  # User override allows this
    
    # Mock dependencies
    mock_router = MagicMock()
    mock_router.decide.return_value = MagicMock(model=requested_model)
    
    mock_session = MagicMock()
    
    with patch('codemie.core.router_chat_model.ModelAvailabilityService.resolve_model_for_execution_with_user_context', return_value=resolved_model):
        chat_model = RouterChatModel(router=mock_router, session=mock_session)
        
        # Simulate invocation with user context
        # Note: actual implementation details depend on how RouterChatModel stores/passes context
        result = await chat_model._agenerate(
            messages=[{"role": "user", "content": "test"}],
            user_id=user_id,
            project_name=project_name
        )
    
    # Verify resolved model was used
    assert result is not None

@pytest.mark.asyncio
async def test_agenerate_fallback_without_user_context():
    """Verify _agenerate falls back to existing behavior if no user context."""
    mock_router = MagicMock()
    mock_router.decide.return_value = MagicMock(model="default-model")
    
    mock_session = MagicMock()
    
    with patch('codemie.core.router_chat_model.ModelAvailabilityService.resolve_model_for_execution') as mock_resolve:
        chat_model = RouterChatModel(router=mock_router, session=mock_session)
        
        # Invocation without user context
        result = await chat_model._agenerate(
            messages=[{"role": "user", "content": "test"}]
        )
    
    # Verify legacy resolution method used (or similar fallback logic)
    assert result is not None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/codemie/core/test_router_chat_model.py::test_agenerate_with_user_context -v
```

Expected: FAIL (user_id parameter not recognized or method not updated)

- [ ] **Step 3: Update RouterChatModel._agenerate to integrate user-scoped resolution**

In `src/codemie/core/router_chat_model.py`, modify the `_agenerate` method (or `_get_model` helper):

```python
from uuid import UUID
from codemie.service.llm.model_availability_service import ModelAvailabilityService

class RouterChatModel(BaseChatModel):
    """Existing class — update _agenerate method."""
    
    # ... (existing attributes and methods)
    
    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: List[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any
    ) -> LLMResult:
        """
        Generate LLM response with model resolution (user context if available).
        
        Supports optional user_id and project_name in kwargs for user-scoped override.
        """
        user_id: UUID | None = kwargs.pop("user_id", None)
        project_name: str | None = kwargs.pop("project_name", None)
        
        # Get router decision (initial model selection)
        decision = self.router.decide(
            *self.candidates,
            messages=messages,
            **kwargs
        )
        
        # Resolve model: use user context if available, else fall back to project/platform
        requested_model = decision.model
        if user_id and project_name:
            # User-scoped resolution (4-tier precedence)
            resolved_model = ModelAvailabilityService.resolve_model_for_execution_with_user_context(
                self.session, requested_model, user_id, project_name
            )
        else:
            # Legacy resolution (project-only)
            resolved_model = ModelAvailabilityService.resolve_model_for_execution(
                requested_model, self.project_name
            )
        
        # Call actual LLM with resolved model
        llm_instance = self._get_llm_instance(resolved_model)
        result = await llm_instance.agenerate(
            messages, stop=stop, run_manager=run_manager, **kwargs
        )
        
        return result
    
    def _get_llm_instance(self, model_id: str):
        """Get concrete LLM instance for the resolved model."""
        # Implementation depends on existing LLMFactory or similar pattern
        # Simplified: assume existing pattern exists
        return self.candidates[model_id]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/codemie/core/test_router_chat_model.py::test_agenerate_with_user_context -v
```

Expected: PASS (user context flows through resolution chain)

- [ ] **Step 5: Commit**

```bash
git add src/codemie/core/router_chat_model.py tests/codemie/core/test_router_chat_model.py
git commit -m "feat: integrate user-scoped model resolution at RouterChatModel invocation"
```

---

### Task 7: Add ApplicationEnrichment Repository and Query Helpers

**Files:**
- Create: `src/codemie/data/repositories/application_enrichment_repository.py`
- Test: `tests/codemie/data/repositories/test_application_enrichment_repository.py`

**Interfaces:**
- Produces: ApplicationEnrichmentRepository with methods:
  - `get_by_project(session, project_name: str) -> ApplicationEnrichment | None`
  - `get_or_create(session, project_name: str) -> ApplicationEnrichment` — get existing or create default
  - `update_disabled_models(session, project_name: str, disabled_model_ids: list[str]) -> ApplicationEnrichment`

**Test-first: yes — Tests verify CRUD for platform disablement config**

- [ ] **Step 1: Write failing test for ApplicationEnrichment repository**

Create `tests/codemie/data/repositories/test_application_enrichment_repository.py`:

```python
from unittest.mock import MagicMock
from codemie.data.repositories.application_enrichment_repository import ApplicationEnrichmentRepository
from codemie.core.models import ApplicationEnrichment

def test_get_by_project_found():
    """Verify get_by_project returns enrichment when found."""
    session = MagicMock()
    project_name = "test-project"
    mock_enrichment = MagicMock(spec=ApplicationEnrichment)
    
    session.query().filter().first.return_value = mock_enrichment
    
    repo = ApplicationEnrichmentRepository()
    result = repo.get_by_project(session, project_name)
    
    assert result == mock_enrichment

def test_get_by_project_not_found():
    """Verify get_by_project returns None when not found."""
    session = MagicMock()
    project_name = "test-project"
    
    session.query().filter().first.return_value = None
    
    repo = ApplicationEnrichmentRepository()
    result = repo.get_by_project(session, project_name)
    
    assert result is None

def test_get_or_create_existing():
    """Verify get_or_create returns existing enrichment."""
    session = MagicMock()
    project_name = "test-project"
    mock_enrichment = MagicMock(spec=ApplicationEnrichment)
    
    session.query().filter().first.return_value = mock_enrichment
    
    repo = ApplicationEnrichmentRepository()
    result = repo.get_or_create(session, project_name)
    
    assert result == mock_enrichment

def test_get_or_create_new():
    """Verify get_or_create creates default enrichment if not found."""
    session = MagicMock()
    project_name = "test-project"
    
    session.query().filter().first.return_value = None
    session.add = MagicMock()
    session.flush = MagicMock()
    
    repo = ApplicationEnrichmentRepository()
    result = repo.get_or_create(session, project_name)
    
    assert result.project_name == project_name
    assert result.disabled_model_ids == []
    session.add.assert_called_once()

def test_update_disabled_models():
    """Verify update_disabled_models updates enrichment."""
    session = MagicMock()
    project_name = "test-project"
    disabled_models = ["old-model-v1", "deprecated-model"]
    
    mock_enrichment = MagicMock(spec=ApplicationEnrichment)
    session.query().filter().first.return_value = mock_enrichment
    
    repo = ApplicationEnrichmentRepository()
    result = repo.update_disabled_models(session, project_name, disabled_models)
    
    assert result.disabled_model_ids == disabled_models
    session.flush.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/codemie/data/repositories/test_application_enrichment_repository.py -v
```

Expected: FAIL with ImportError (ApplicationEnrichmentRepository not defined)

- [ ] **Step 3: Implement ApplicationEnrichmentRepository**

Create `src/codemie/data/repositories/application_enrichment_repository.py`:

```python
from uuid import uuid4
from sqlalchemy.orm import Session

from codemie.core.models import ApplicationEnrichment


class ApplicationEnrichmentRepository:
    """Repository for ApplicationEnrichment CRUD operations."""
    
    @staticmethod
    def get_by_project(session: Session, project_name: str) -> ApplicationEnrichment | None:
        """Fetch enrichment for a project."""
        return (
            session.query(ApplicationEnrichment)
            .filter(ApplicationEnrichment.project_name == project_name)
            .first()
        )
    
    @staticmethod
    def get_or_create(session: Session, project_name: str) -> ApplicationEnrichment:
        """Get existing enrichment or create default (no disabled models)."""
        enrichment = ApplicationEnrichmentRepository.get_by_project(session, project_name)
        if enrichment:
            return enrichment
        
        # Create default enrichment with no disabled models
        enrichment = ApplicationEnrichment(
            project_name=project_name,
            disabled_model_ids=[]
        )
        session.add(enrichment)
        session.flush()
        return enrichment
    
    @staticmethod
    def update_disabled_models(
        session: Session, project_name: str, disabled_model_ids: list[str]
    ) -> ApplicationEnrichment:
        """Update platform-disabled models for a project."""
        enrichment = ApplicationEnrichmentRepository.get_by_project(session, project_name)
        if not enrichment:
            enrichment = ApplicationEnrichmentRepository.get_or_create(
                session, project_name
            )
        
        enrichment.disabled_model_ids = disabled_model_ids
        session.flush()
        return enrichment
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/codemie/data/repositories/test_application_enrichment_repository.py -v
```

Expected: PASS (all tests pass)

- [ ] **Step 5: Commit**

```bash
git add src/codemie/data/repositories/application_enrichment_repository.py tests/codemie/data/repositories/test_application_enrichment_repository.py
git commit -m "feat: add ApplicationEnrichmentRepository for platform disablement management"
```

---

### Task 8: Create Alembic Migration for Database Schema

**Files:**
- Create: `src/external/alembic/versions/<timestamp>_add_user_model_override_and_enrichment_tables.py`

**Interfaces:**
- Produces: Alembic migration with upgrade/downgrade functions creating/dropping:
  - user_model_overrides table (with FK to users, applications; unique and check constraints)
  - application_enrichment table (with FK to applications; unique constraint on project_name)

**Test-first: no — Migrations are infrastructure; test by running make migrations**

- [ ] **Step 1: Generate migration file**

Run:

```bash
cd src && alembic revision --autogenerate -m "Add UserModelOverride and ApplicationEnrichment tables"
```

This generates a migration file in `src/external/alembic/versions/`.

- [ ] **Step 2: Review and finalize migration**

Open the generated migration and ensure it includes:

```python
"""Add UserModelOverride and ApplicationEnrichment tables"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

def upgrade() -> None:
    # Create ApplicationEnrichment table
    op.create_table(
        'application_enrichment',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('project_name', sa.String(), nullable=False),
        sa.Column('disabled_model_ids', postgresql.ARRAY(sa.String()), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_name'], ['applications.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_name', name='uq_application_enrichment_project')
    )
    op.create_index('ix_application_enrichment_project_name', 'application_enrichment', ['project_name'], unique=False)
    
    # Create UserModelOverride table
    op.create_table(
        'user_model_overrides',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('project_name', sa.String(), nullable=False),
        sa.Column('allowed_models', postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column('override_enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_name'], ['applications.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'project_name', name='uq_user_model_override_user_project'),
        sa.CheckConstraint(
            "NOT (override_enabled = true AND array_length(allowed_models, 1) IS NULL)",
            name='ck_override_enabled_has_models'
        )
    )
    op.create_index('ix_user_model_overrides_user_id', 'user_model_overrides', ['user_id'], unique=False)
    op.create_index('ix_user_model_overrides_project_name', 'user_model_overrides', ['project_name'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_user_model_overrides_project_name', table_name='user_model_overrides')
    op.drop_index('ix_user_model_overrides_user_id', table_name='user_model_overrides')
    op.drop_table('user_model_overrides')
    op.drop_index('ix_application_enrichment_project_name', table_name='application_enrichment')
    op.drop_table('application_enrichment')
```

- [ ] **Step 3: Verify migration runs successfully**

```bash
cd src && alembic upgrade head
```

Expected: Migration applies without errors; tables created in PostgreSQL.

- [ ] **Step 4: Commit**

```bash
git add src/external/alembic/versions/
git commit -m "migration: add user_model_overrides and application_enrichment tables"
```

---

### Task 9: Add Configuration for Platform Disablement and Feature Flags

**Files:**
- Modify: `src/codemie/configs/config.py` — Add PLATFORM_MODEL_OVERRIDE_ENABLED, PLATFORM_DISABLED_MODELS env vars

**Interfaces:**
- Produces: Config properties:
  - `PLATFORM_MODEL_OVERRIDE_ENABLED: bool` (default True)
  - `PLATFORM_DISABLED_MODELS: list[str]` (comma-separated env var, default empty list)

**Test-first: no — Config is infrastructure; verify by inspection**

- [ ] **Step 1: Review existing config.py structure**

Open `src/codemie/configs/config.py` to understand how env vars are read.

- [ ] **Step 2: Add new config entries**

Add to `src/codemie/configs/config.py`:

```python
import os
from typing import List

# Existing config entries...

# User Model Override Configuration
PLATFORM_MODEL_OVERRIDE_ENABLED: bool = os.getenv(
    "PLATFORM_MODEL_OVERRIDE_ENABLED",
    "true"
).lower() in ("true", "1", "yes")

PLATFORM_DISABLED_MODELS: List[str] = [
    m.strip()
    for m in os.getenv("PLATFORM_DISABLED_MODELS", "").split(",")
    if m.strip()
]
```

- [ ] **Step 3: Document env vars**

Add to README or `.env.example`:

```
# User Model Override Configuration
PLATFORM_MODEL_OVERRIDE_ENABLED=true          # Enable/disable user-level model overrides feature
PLATFORM_DISABLED_MODELS=                     # Comma-separated list of globally disabled model IDs
```

- [ ] **Step 4: Commit**

```bash
git add src/codemie/configs/config.py .env.example README.md
git commit -m "config: add platform disablement and override feature flag env vars"
```

---

### Task 10: Add Comprehensive Integration Tests for Precedence Chain

**Files:**
- Modify: `tests/codemie/service/llm/test_model_availability_service.py` — Add full integration tests

**Interfaces:**
- Consumes: ModelAvailabilityService, UserModelOverrideService, session fixtures
- Produces: Test class with all 16 precedence combinations (2^4 states)

**Test-first: yes — Integration tests verify end-to-end precedence logic**

- [ ] **Step 1: Write integration tests for all 16 precedence combinations**

Add to `tests/codemie/service/llm/test_model_availability_service.py`:

```python
import pytest
from uuid import uuid4
from unittest.mock import MagicMock, patch
from codemie.service.llm.model_availability_service import ModelAvailabilityService
from codemie.core.models import Application, ApplicationEnrichment, UserModelOverride
from codemie.core.exceptions import ModelNotWhitelistedException

class TestModelResolutionPrecedenceChain:
    """Integration tests for 4-tier precedence chain: platform disable → user override → project whitelist → default."""
    
    # Test matrix: [platform_disables, user_override_exists, user_allows, project_allows]
    
    def test_platform_disable_hard_stop_everything_else_allows(self):
        """1. Platform disable → DENY (hard stop), regardless of user/project allow."""
        session = MagicMock()
        user_id = uuid4()
        project_name = "test-project"
        requested_model = "disabled-model"
        
        mock_app = MagicMock(spec=Application)
        mock_app.allowed_models = ["disabled-model", "gpt-4"]  # Project allows
        mock_app.default_model = "gpt-4"
        
        mock_enrichment = MagicMock(spec=ApplicationEnrichment)
        mock_enrichment.disabled_model_ids = ["disabled-model"]  # Platform disables
        
        mock_override = MagicMock(spec=UserModelOverride)
        mock_override.allowed_models = ["disabled-model"]  # User override allows
        mock_override.override_enabled = True
        
        with patch('codemie.service.llm.model_availability_service.Application.get_by_id', return_value=mock_app):
            session.query().filter().first.return_value = mock_enrichment
            with patch('codemie.service.llm.model_availability_service.UserModelOverrideRepository.get_by_user_and_project', return_value=mock_override):
                service = ModelAvailabilityService()
                with pytest.raises(ModelNotWhitelistedException):
                    service.resolve_model_for_execution_with_user_context(
                        session, requested_model, user_id, project_name
                    )
    
    def test_user_override_allow_project_deny(self):
        """2. No platform disable; user override allows; project denies → USE (user override precedence)."""
        session = MagicMock()
        user_id = uuid4()
        project_name = "test-project"
        requested_model = "gpt-4"
        
        mock_app = MagicMock(spec=Application)
        mock_app.allowed_models = ["claude-opus"]  # Project denies gpt-4
        mock_app.default_model = "claude-opus"
        
        mock_enrichment = MagicMock(spec=ApplicationEnrichment)
        mock_enrichment.disabled_model_ids = []  # No platform disable
        
        mock_override = MagicMock(spec=UserModelOverride)
        mock_override.allowed_models = ["gpt-4"]  # User override allows
        mock_override.override_enabled = True
        
        with patch('codemie.service.llm.model_availability_service.Application.get_by_id', return_value=mock_app):
            session.query().filter().first.return_value = mock_enrichment
            with patch('codemie.service.llm.model_availability_service.UserModelOverrideRepository.get_by_user_and_project', return_value=mock_override):
                service = ModelAvailabilityService()
                result = service.resolve_model_for_execution_with_user_context(
                    session, requested_model, user_id, project_name
                )
        
        assert result == "gpt-4"
    
    def test_no_user_override_project_whitelist_allows(self):
        """3. No platform disable; no user override; project whitelist allows → USE (project whitelist)."""
        session = MagicMock()
        user_id = uuid4()
        project_name = "test-project"
        requested_model = "gpt-4"
        
        mock_app = MagicMock(spec=Application)
        mock_app.allowed_models = ["gpt-4", "claude-opus"]  # Project allows
        mock_app.default_model = "claude-opus"
        
        mock_enrichment = MagicMock(spec=ApplicationEnrichment)
        mock_enrichment.disabled_model_ids = []
        
        with patch('codemie.service.llm.model_availability_service.Application.get_by_id', return_value=mock_app):
            session.query().filter().first.return_value = mock_enrichment
            with patch('codemie.service.llm.model_availability_service.UserModelOverrideRepository.get_by_user_and_project', return_value=None):
                service = ModelAvailabilityService()
                result = service.resolve_model_for_execution_with_user_context(
                    session, requested_model, user_id, project_name
                )
        
        assert result == "gpt-4"
    
    def test_no_override_no_whitelist_use_default(self):
        """4. No platform disable; no user override; no whitelist; project has default → USE (default)."""
        session = MagicMock()
        user_id = uuid4()
        project_name = "test-project"
        requested_model = "unknown-model"
        
        mock_app = MagicMock(spec=Application)
        mock_app.allowed_models = None  # No whitelist (legacy)
        mock_app.default_model = "gpt-4"
        
        mock_enrichment = MagicMock(spec=ApplicationEnrichment)
        mock_enrichment.disabled_model_ids = []
        
        with patch('codemie.service.llm.model_availability_service.Application.get_by_id', return_value=mock_app):
            session.query().filter().first.return_value = mock_enrichment
            with patch('codemie.service.llm.model_availability_service.UserModelOverrideRepository.get_by_user_and_project', return_value=None):
                service = ModelAvailabilityService()
                result = service.resolve_model_for_execution_with_user_context(
                    session, requested_model, user_id, project_name
                )
        
        assert result == "gpt-4"
    
    def test_user_override_disabled_falls_back_to_project(self):
        """5. User override exists but override_enabled=false → SKIP override, use project whitelist."""
        session = MagicMock()
        user_id = uuid4()
        project_name = "test-project"
        requested_model = "gpt-4"
        
        mock_app = MagicMock(spec=Application)
        mock_app.allowed_models = ["gpt-4", "claude-opus"]
        mock_app.default_model = "claude-opus"
        
        mock_enrichment = MagicMock(spec=ApplicationEnrichment)
        mock_enrichment.disabled_model_ids = []
        
        mock_override = MagicMock(spec=UserModelOverride)
        mock_override.allowed_models = ["claude-opus"]
        mock_override.override_enabled = False  # Disabled
        
        with patch('codemie.service.llm.model_availability_service.Application.get_by_id', return_value=mock_app):
            session.query().filter().first.return_value = mock_enrichment
            with patch('codemie.service.llm.model_availability_service.UserModelOverrideRepository.get_by_user_and_project', return_value=mock_override):
                service = ModelAvailabilityService()
                result = service.resolve_model_for_execution_with_user_context(
                    session, requested_model, user_id, project_name
                )
        
        assert result == "gpt-4"  # Uses project whitelist, not override
```

- [ ] **Step 2: Run integration tests**

```bash
pytest tests/codemie/service/llm/test_model_availability_service.py::TestModelResolutionPrecedenceChain -v
```

Expected: PASS (all precedence combinations work correctly)

- [ ] **Step 3: Commit**

```bash
git add tests/codemie/service/llm/test_model_availability_service.py
git commit -m "test: add comprehensive integration tests for 4-tier precedence chain"
```

---

### Task 11: Documentation and Quality Gate Validation

**Files:**
- No new code files; this is documentation and validation
- Verify: Makefile targets (lint, tests, build), migrations, config

**Test-first: no — This is final verification**

- [ ] **Step 1: Run full test suite**

```bash
make test
```

Expected: All tests pass (including new UserModelOverride, integration tests, endpoint tests)

- [ ] **Step 2: Run linter**

```bash
make ruff
```

Expected: No linting errors in new files

- [ ] **Step 3: Run database migrations (local dev)**

```bash
make db-migrate
```

Expected: Migrations apply successfully; tables created

- [ ] **Step 4: Verify backward compatibility**

- Existing project-level model availability continues to work (no breaking changes to resolve_model_for_execution)
- Legacy projects without ApplicationEnrichment rows default to no platform disablement
- Invocations without user context fall back to existing behavior

Run:

```bash
pytest tests/codemie/service/llm/test_model_availability_service.py -k "not UserContext" -v
```

Expected: PASS (all existing tests still pass)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "docs: add documentation for user-level model override feature; verify backward compatibility"
```

---

## Summary

**Commit per task** using the repository's existing convention.

**11 tasks, all TDD (Test-first: yes except Task 8, 9, 11 which are infrastructure):**

1. Add UserModelOverride + ApplicationEnrichment tables (SQLModel)
2. UserModelOverrideRepository CRUD
3. UserModelOverrideService with validation
4. Extend ModelAvailabilityService with 4-tier precedence
5. UserModelOverride API endpoints (admin-only CRUD + batch)
6. Integrate user-scoped resolution at RouterChatModel invocation
7. ApplicationEnrichmentRepository for platform disablement
8. Alembic migration
9. Config for feature flags and platform disablement
10. Integration tests for all precedence combinations
11. Documentation and quality gate validation

**Key design decisions:**
- 4-tier precedence chain (platform disable → user override → project whitelist → default)
- Validation at set-time (early fail-fast); graceful degradation at invocation if config changes
- Batch operations atomic (all-or-nothing)
- Admin-only gating via is_application_admin(project_name)
- Backward compatible (no breaking changes to existing code paths)

---

