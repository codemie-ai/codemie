# Project Requirement Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a real, caller-owned project a hard precondition on every member-driven request path (sign-in, assistant operations, proxy requests, CLI) to prevent governed requests from arriving with empty, username-derived, or silently substituted project values.

**Architecture:** Service layer changes (authentication, registration) to fail when personal project creation fails; API router changes (proxy) to reject requests without valid project headers; agent tools changes (platform tools, validation utils) to reject empty project values; and Elasticsearch reporting for empty-project assistants migration.

**Tech Stack:** FastAPI, SQLModel, PostgreSQL, Elasticsearch, pytest

## Risk Mitigation Strategy

This plan addresses 10 identified risk indicators through:

1. **Shared validation utilities** (Task 1) — Consistent validation logic across all paths
2. **Explicit exception raising** (Task 1, 2) — Replace silent failures with clear errors
3. **Integration marker** (Task 5) — Explicit background consumer identification via header
4. **User type exclusions** (Task 2) — Honor existing `is_personal_project_excluded()` logic
5. **Test-first implementation** (Tasks 1, 2, 5) — Close test coverage gaps proactively

## Task Mapping to Acceptance Criteria

| Acceptance Criterion | Delivered By |
|---|---|
| Sign-in refused when platform cannot create personal project | Task 2 |
| Assistant create/update attached to project caller belongs to, not fallback | Task 3 |
| Existing empty-project assistants reported to admins | Task 4 |
| Proxy requests without project refused | Task 5 |
| CLI tells user project is required and how to set one | Task 5 (backend enforcement, error surfaces to CLI) |
| Background consumers unaffected | Task 1 (identification), Task 5 (exemption) |

## Global Constraints

- Commit per task using the repository's existing convention
- Background consumers (datasource processors, skill/workflow generators, toolkit resolution) MUST remain unaffected — identified by `HEADER_CODEMIE_INTEGRATION`
- Proxy refusal uses HTTP 400 with clear error message
- Sign-in refusal surfaces through existing UI error rendering
- Preserve `User.current_project` property behavior (DEMO_PROJECT fallback) for backward compatibility

---

### Task 1: Create Shared Project Validation Utilities

**Files:**
- Create: `src/codemie/core/project_validator.py`
- Test: `tests/codemie/core/test_project_validator.py`

**Interfaces:**
- Produces: `require_valid_project(project: str | None) -> str` — Validates and returns project or raises
- Produces: `is_background_consumer(headers: dict) -> bool` — Identifies integration/background requests
- Produces: `ProjectRequiredException` — Custom exception for missing project

**Test-first: yes — Shared validation utilities enforce consistent project requirements**

**Risk Mitigations:**
- Addresses **Multiple fallback chains** by providing single source of truth
- Addresses **Empty string fallbacks** by replacing `or ""` pattern with validation
- Addresses **Background consumer identification** with explicit marker check

- [ ] **Step 1: Write failing test for project validation utilities**

Create `tests/codemie/core/test_project_validator.py`:

```python
"""Tests for project validation utilities."""
import pytest
from codemie.core.project_validator import (
    require_valid_project,
    is_background_consumer,
    ProjectRequiredException,
)
from codemie.core.constants import DEMO_PROJECT, HEADER_CODEMIE_INTEGRATION


class TestRequireValidProject:
    """Test project validation logic."""

    def test_require_valid_project_with_valid_project(self):
        """Test validation succeeds with valid project."""
        result = require_valid_project("valid-project")
        assert result == "valid-project"

    def test_require_valid_project_rejects_none(self):
        """Test validation fails when project is None."""
        with pytest.raises(ProjectRequiredException) as exc_info:
            require_valid_project(None)
        
        assert "project is required" in str(exc_info.value).lower()

    def test_require_valid_project_rejects_empty_string(self):
        """Test validation fails when project is empty string."""
        with pytest.raises(ProjectRequiredException) as exc_info:
            require_valid_project("")
        
        assert "project is required" in str(exc_info.value).lower()

    def test_require_valid_project_rejects_demo_project(self):
        """Test validation fails when project is DEMO_PROJECT."""
        with pytest.raises(ProjectRequiredException) as exc_info:
            require_valid_project(DEMO_PROJECT)
        
        assert "demo project" in str(exc_info.value).lower()

    def test_require_valid_project_accepts_whitespace_only_as_invalid(self):
        """Test validation fails when project is whitespace only."""
        with pytest.raises(ProjectRequiredException):
            require_valid_project("   ")


class TestIsBackgroundConsumer:
    """Test background consumer identification."""

    def test_is_background_consumer_with_integration_header(self):
        """Test identifies integration requests as background consumers."""
        headers = {HEADER_CODEMIE_INTEGRATION: "integration-123"}
        assert is_background_consumer(headers) is True

    def test_is_background_consumer_without_integration_header(self):
        """Test regular requests are not background consumers."""
        headers = {"some-other-header": "value"}
        assert is_background_consumer(headers) is False

    def test_is_background_consumer_with_empty_integration_header(self):
        """Test empty integration header is not a background consumer."""
        headers = {HEADER_CODEMIE_INTEGRATION: ""}
        assert is_background_consumer(headers) is False

    def test_is_background_consumer_with_none_integration_header(self):
        """Test None integration header is not a background consumer."""
        headers = {HEADER_CODEMIE_INTEGRATION: None}
        assert is_background_consumer(headers) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/codemie/core/test_project_validator.py -v`
Expected: FAIL with "module not found"

- [ ] **Step 3: Implement project validation utilities**

Create `src/codemie/core/project_validator.py`:

```python
"""Project validation utilities for enforcing project requirements.

Provides shared validation logic for all request paths (sign-in, assistant operations,
proxy requests) to ensure consistent project requirement enforcement.
"""
from codemie.core.constants import DEMO_PROJECT, HEADER_CODEMIE_INTEGRATION
from codemie.core.exceptions import ExtendedHTTPException


class ProjectRequiredException(ExtendedHTTPException):
    """Exception raised when a required project is missing or invalid."""

    def __init__(self, message: str = "A valid project is required", details: str | None = None):
        super().__init__(
            code=400,
            message=message,
            details=details or "Please provide a valid project identifier",
            help="Configure your project or contact your administrator for project access",
        )


def require_valid_project(project: str | None, context: str = "") -> str:
    """Validate project is present and not a fallback value.
    
    Args:
        project: Project identifier to validate
        context: Optional context for error message (e.g., "for assistant operations")
        
    Returns:
        The validated project string
        
    Raises:
        ProjectRequiredException: If project is None, empty, whitespace, or DEMO_PROJECT
    """
    if not project or not project.strip():
        ctx = f" {context}" if context else ""
        raise ProjectRequiredException(
            message=f"A valid project is required{ctx}",
            details="Project is None or empty. All member-driven requests must specify a real project.",
        )
    
    if project.strip() == DEMO_PROJECT:
        ctx = f" {context}" if context else ""
        raise ProjectRequiredException(
            message=f"Demo project cannot be used{ctx}",
            details=f"The demo project ({DEMO_PROJECT}) is not a valid project for governed requests. "
                   "Please specify a real project you belong to.",
        )
    
    return project.strip()


def is_background_consumer(headers: dict) -> bool:
    """Check if request is from a background consumer (exempt from project requirement).
    
    Background consumers include:
    - Datasource indexing processes
    - Skill/workflow generators
    - Toolkit resolution for platform-default models
    
    Args:
        headers: Request headers dictionary
        
    Returns:
        True if request has integration marker, False otherwise
    """
    integration_id = headers.get(HEADER_CODEMIE_INTEGRATION)
    return bool(integration_id and integration_id.strip())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/codemie/core/test_project_validator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codemie/core/project_validator.py tests/codemie/core/test_project_validator.py
git commit -m "feat(core): add shared project validation utilities

- ProjectRequiredException for consistent error handling
- require_valid_project validates non-empty, non-DEMO_PROJECT values
- is_background_consumer identifies integration requests
- Foundation for EPMCDME-14451 implementation"
```

---

### Task 2: Fail Authentication on Personal Project Creation Failure

**Files:**
- Modify: `src/codemie/service/project/personal_project_service.py:45-108`
- Modify: `src/codemie/service/user/authentication_service.py:388-405`, `680-746`
- Modify: `src/codemie/service/user/registration_service.py:152-278`
- Test: `tests/codemie/service/user/test_authentication_service.py`
- Test: `tests/codemie/service/user/test_registration_service.py`

**Interfaces:**
- Consumes: `is_personal_project_excluded()` from `user_type_validator.py`
- Consumes: `ProjectRequiredException` from Task 1
- Produces: `ensure_personal_project_async()` raises `ProjectRequiredException` on failure (except for excluded user types)

**Risk Mitigations:**
- Addresses **Silent failure propagation** by raising exception instead of returning False
- Addresses **External/service_account users** by honoring `is_personal_project_excluded()`
- Addresses **Test coverage debt** with authentication failure path tests

**Test-first: yes — Authentication fails with clear error when personal project creation fails for non-excluded user types**

- [ ] **Step 1: Write failing test for authentication failure when project creation fails**

In `tests/codemie/service/user/test_authentication_service.py`, add new test class:

```python
class TestAuthenticationPersonalProjectFailure:
    """Test authentication fails when personal project creation fails."""

    @pytest.mark.asyncio
    async def test_finalize_authentication_raises_when_personal_project_fails_for_regular_user(self):
        """Test _finalize_authentication raises when personal project creation fails for regular user."""
        from unittest.mock import AsyncMock, patch
        from codemie.service.user.authentication_service import AuthenticationService
        from codemie.rest_api.security.user import User
        from codemie.core.project_validator import ProjectRequiredException

        # Arrange: regular user with personal project creation failing
        user = User(
            id="user-123",
            email="test@example.com",
            username="testuser",
            user_type="regular",
            abilities=[],
            project_names=[],
            admin_project_names=[],
            knowledge_bases=[],
        )

        with patch("codemie.service.user.authentication_service.is_personal_project_excluded", return_value=False), \
             patch("codemie.service.user.authentication_service.personal_project_service.ensure_personal_project_async",
                   new_callable=AsyncMock, return_value=False) as mock_ensure:

            # Act & Assert: should raise ProjectRequiredException
            with pytest.raises(ProjectRequiredException) as exc_info:
                await AuthenticationService._finalize_authentication(user, "test")

            # Verify exception details
            assert exc_info.value.code == 400
            assert "project" in exc_info.value.message.lower()
            mock_ensure.assert_awaited_once_with(user.id, user.email)

    @pytest.mark.asyncio
    async def test_finalize_authentication_succeeds_for_excluded_user_type(self):
        """Test _finalize_authentication succeeds for excluded user types even when project creation fails."""
        from unittest.mock import AsyncMock, patch
        from codemie.service.user.authentication_service import AuthenticationService
        from codemie.rest_api.security.user import User

        # Arrange: external user (excluded from personal project)
        user = User(
            id="user-456",
            email="external@example.com",
            username="externaluser",
            user_type="external",
            abilities=[],
            project_names=[],
            admin_project_names=[],
            knowledge_bases=[],
        )

        with patch("codemie.service.user.authentication_service.is_personal_project_excluded", return_value=True), \
             patch("codemie.service.user.authentication_service.get_async_session") as mock_session, \
             patch("codemie.service.user.authentication_service.user_project_repository.aget_by_user_id",
                   new_callable=AsyncMock, return_value=[]), \
             patch("codemie.service.user.authentication_service.user_kb_repository.aget_by_user_id",
                   new_callable=AsyncMock, return_value=[]):

            # Act: should succeed without calling ensure_personal_project_async
            result = await AuthenticationService._finalize_authentication(user, "test")

            # Assert
            assert result.id == user.id
            assert result.project_names == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/codemie/service/user/test_authentication_service.py::TestAuthenticationPersonalProjectFailure -v`
Expected: FAIL with "ProjectRequiredException not raised"

- [ ] **Step 3: Modify ensure_personal_project_async to raise exception on failure**

In `src/codemie/service/project/personal_project_service.py:45-108`, change return False to raise exception:

```python
@staticmethod
async def ensure_personal_project_async(user_id: str, user_email: str) -> bool:
    """Ensure personal project exists for user (idempotent, blocking on failure, isolated transaction)
    
    Creates personal project automatically after authentication if missing.
    Uses SEPARATE SESSION to ensure failures do not affect parent authentication transaction.
    
    Follows FR-7.1 Personal Project Rules:
    - Project name = user's email address
    - project_type = 'personal'
    - created_by = user_id
    - User assigned as member with is_project_admin=false
    - BLOCKING: failures raise ExtendedHTTPException to prevent authentication
    
    Args:
        user_id: User UUID
        user_email: User email (used as project name)
    
    Returns:
        True if personal project exists or was created successfully
    
    Raises:
        ProjectRequiredException: If personal project creation fails
    
    Note:
        This method is idempotent - safe to call multiple times.
        ISOLATED TRANSACTION: Uses separate session to prevent rollback affecting auth.
    """
    from codemie.clients.postgres import get_async_session
    from codemie.core.project_validator import ProjectRequiredException

    try:
        async with get_async_session() as isolated_session:
            # Check if personal project already exists (both Application AND user_projects)
            if await PersonalProjectService._has_personal_project_complete(isolated_session, user_id, user_email):
                logger.debug(f"Personal project already exists: user_id={user_id}, project_type=personal")
                return True

            # Create personal project (transaction-safe)
            await PersonalProjectService._create_personal_project(isolated_session, user_id, user_email)

            await activity_event_repository.async_insert(
                ActivityEventCreate(
                    domain=ActivityDomain.PROJECT_MANAGEMENT,
                    event_type=ProjectManagementEvent.PROJECT_CREATED,
                    entity_type=ActivityEntityType.PROJECT,
                    entity_id=user_email,
                    actor_id=user_id,
                    attributes={"project_type": "personal"},
                ),
                isolated_session,
            )

            # Commit isolated transaction
            await isolated_session.commit()

            logger.info(f"Personal project created: user_id={user_id}, project_type=personal")
            return True

    except Exception as e:
        # BLOCKING: Raise exception to prevent authentication (FR-14451)
        # Security: Do not log email (PII leakage)
        logger.error(
            f"Personal project creation failed (blocking): user_id={user_id}, project_type=personal, error={e}",
            exc_info=True,
        )
        raise ProjectRequiredException(
            message="Failed to create personal project",
            details="Personal project creation is required for authentication but failed. Please contact support.",
        )
```

- [ ] **Step 4: Update authentication callers to handle exception**

In `src/codemie/service/user/authentication_service.py:388-405`, update `_finalize_authentication` to let exception propagate but only for non-excluded users:

```python
@staticmethod
async def _finalize_authentication(security_user_ins: User, auth_source: str) -> User:
    """Finalize user authentication (post-commit operations)
    
    Loads user's projects and knowledge bases after successful authentication.
    For non-excluded user types, ensures personal project exists (raises on failure).
    
    Args:
        security_user_ins: Authenticated user instance
        auth_source: Authentication source for logging
        
    Returns:
        User with populated project_names, admin_project_names, knowledge_bases
        
    Raises:
        ProjectRequiredException: If personal project creation fails for non-excluded user types
    """
    from codemie.clients.postgres import get_async_session
    from codemie.rest_api.security.user_type_validator import is_personal_project_excluded
    from codemie.service.project.personal_project_service import personal_project_service

    if not is_personal_project_excluded(security_user_ins.user_type):
        # Let exception propagate for non-excluded user types
        await personal_project_service.ensure_personal_project_async(security_user_ins.id, security_user_ins.email)

    async with get_async_session() as session:
        projects = await user_project_repository.aget_by_user_id(session, security_user_ins.id)
        kbs = await user_kb_repository.aget_by_user_id(session, security_user_ins.id)

        security_user_ins.project_names = [p.project_name for p in projects]
        security_user_ins.admin_project_names = [p.project_name for p in projects if p.is_project_admin]
        security_user_ins.knowledge_bases = [kb.kb_name for kb in kbs]

    logger.debug(f"User authenticated ({auth_source}): user_id={security_user_ins.id}")
    return security_user_ins
```

In `src/codemie/service/user/authentication_service.py:680-746`, the `authenticate_and_login` method already calls `_finalize_authentication`, so exception will propagate naturally.

- [ ] **Step 5: Update registration service to handle exception**

In `src/codemie/service/user/registration_service.py`, update the two call sites (lines 214, 251) to let exception propagate. The callers already have try-except blocks that will handle ExtendedHTTPException appropriately.

No code changes needed — verify exception propagates to router error handlers.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/codemie/service/user/test_authentication_service.py::TestAuthenticationPersonalProjectFailure -v`
Expected: PASS

Run: `pytest tests/codemie/service/user/ -v -k "authentication or registration"`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/codemie/service/project/personal_project_service.py src/codemie/service/user/authentication_service.py tests/codemie/service/user/test_authentication_service.py
git commit -m "feat(auth): fail authentication when personal project creation fails

- ensure_personal_project_async raises ExtendedHTTPException on failure
- _finalize_authentication lets exception propagate for non-excluded users
- External and service_account user types remain unaffected
- Closes EPMCDME-14451 acceptance criterion 1"
```

---

### Task 3: Reject Assistant Operations with Empty Project

**Files:**
- Modify: `src/codemie/agents/tools/platform/platform_tool.py:200-214`
- Modify: `src/codemie/workflows/assistant_generator/nodes/validation/utils.py:92-128`
- Test: `tests/agents/tools/platform/test_platform_tool.py` (create if not exists)
- Test: `tests/workflows/assistant_generator/nodes/validation/test_utils.py` (create if not exists)

**Interfaces:**
- Consumes: `Assistant.project`, `User.current_project` from authentication context
- Consumes: `require_valid_project()` from Task 1
- Produces: `_transform_assistant()` uses `require_valid_project()` to validate assistant.project; `get_validated_context_info()` validates resolved project

**Test-first: yes — Platform tool transformation and validation utils reject assistants with empty project**

**Risk Mitigations:**
- Addresses **Empty string fallbacks** by using `require_valid_project()` instead of `or ""`
- Addresses **User.current_project implicit fallback** by validating the resolved project value
- Addresses **No entry point validation** by checking project before operations proceed

- [ ] **Step 1: Write failing test for platform tool transformation with empty project**

Create `tests/agents/tools/platform/test_platform_tool.py`:

```python
"""Tests for platform tool assistant transformation."""
import pytest
from unittest.mock import MagicMock
from codemie.agents.tools.platform.platform_tool import _transform_assistant
from codemie.core.project_validator import ProjectRequiredException


class TestTransformAssistant:
    """Test assistant transformation with project validation."""

    def test_transform_assistant_with_valid_project(self):
        """Test transformation succeeds with valid project."""
        assistant = MagicMock()
        assistant.id = "asst-123"
        assistant.name = "Test Assistant"
        assistant.description = "Test description"
        assistant.system_prompt = "Test prompt"
        assistant.project = "valid-project"
        assistant.created_by = None
        assistant.created_date = None
        assistant.update_date = None
        assistant.llm_model_type = "gpt-4"

        result = _transform_assistant(assistant)

        assert result.project == "valid-project"
        assert result.name == "Test Assistant"

    def test_transform_assistant_rejects_none_project(self):
        """Test transformation fails when project is None."""
        assistant = MagicMock()
        assistant.id = "asst-456"
        assistant.name = "No Project Assistant"
        assistant.description = "Test description"
        assistant.system_prompt = "Test prompt"
        assistant.project = None
        assistant.created_by = None
        assistant.created_date = None
        assistant.update_date = None
        assistant.llm_model_type = "gpt-4"

        with pytest.raises(ProjectRequiredException) as exc_info:
            _transform_assistant(assistant)

        assert "project is required" in str(exc_info.value).lower()

    def test_transform_assistant_rejects_empty_project(self):
        """Test transformation fails when project is empty string."""
        assistant = MagicMock()
        assistant.id = "asst-789"
        assistant.name = "Empty Project Assistant"
        assistant.description = "Test description"
        assistant.system_prompt = "Test prompt"
        assistant.project = ""
        assistant.created_by = None
        assistant.created_date = None
        assistant.update_date = None
        assistant.llm_model_type = "gpt-4"

        with pytest.raises(ProjectRequiredException) as exc_info:
            _transform_assistant(assistant)

        assert "project is required" in str(exc_info.value).lower()
```

- [ ] **Step 2: Write failing test for validation utils with empty project**

Create `tests/workflows/assistant_generator/nodes/validation/test_utils.py`:

```python
"""Tests for assistant generator validation utilities."""
import pytest
from unittest.mock import MagicMock
from codemie.workflows.assistant_generator.nodes.validation.utils import get_validated_context_info


class TestGetValidatedContextInfo:
    """Test context validation with project requirements."""

    def test_get_validated_context_info_with_valid_project(self):
        """Test validation succeeds with valid assistant project."""
        assistant = MagicMock()
        assistant.project = "valid-project"

        user = MagicMock()
        user.current_project = "user-project"

        configured_context = ["repo1", "repo2"]

        # Mock IndexInfo.filter_for_user_repo_names to return empty (no validation errors)
        with pytest.mock.patch("codemie.workflows.assistant_generator.nodes.validation.utils.IndexInfo") as mock_index:
            mock_index.filter_for_user_repo_names.return_value = []

            result, validated_names = get_validated_context_info(assistant, user, configured_context)

            # Verify it used assistant.project, not fallback
            mock_index.filter_for_user_repo_names.assert_called_once()
            call_args = mock_index.filter_for_user_repo_names.call_args
            assert call_args[1]["project_name"] == "valid-project"

    def test_get_validated_context_info_rejects_empty_assistant_project(self):
        """Test validation fails when assistant.project is empty and user.current_project is DEMO_PROJECT."""
        from codemie.core.constants import DEMO_PROJECT

        assistant = MagicMock()
        assistant.project = ""

        user = MagicMock()
        user.current_project = DEMO_PROJECT

        configured_context = ["repo1"]

        with pytest.raises(ValueError) as exc_info:
            get_validated_context_info(assistant, user, configured_context)

        assert "valid project" in str(exc_info.value).lower()

    def test_get_validated_context_info_uses_user_project_as_fallback(self):
        """Test validation uses user.current_project when assistant.project is None but user has valid project."""
        assistant = MagicMock()
        assistant.project = None

        user = MagicMock()
        user.current_project = "user-valid-project"

        configured_context = ["repo1"]

        with pytest.mock.patch("codemie.workflows.assistant_generator.nodes.validation.utils.IndexInfo") as mock_index:
            mock_index.filter_for_user_repo_names.return_value = []

            result, validated_names = get_validated_context_info(assistant, user, configured_context)

            # Verify it used user.current_project as fallback
            call_args = mock_index.filter_for_user_repo_names.call_args
            assert call_args[1]["project_name"] == "user-valid-project"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/agents/tools/platform/test_platform_tool.py -v`
Expected: FAIL with "ProjectRequiredException not raised"

Run: `pytest tests/workflows/assistant_generator/nodes/validation/test_utils.py -v`
Expected: FAIL with "ProjectRequiredException not raised"

- [ ] **Step 4: Update platform_tool.py to reject empty project**

In `src/codemie/agents/tools/platform/platform_tool.py:200-214`, replace fallback with validation:

```python
def _transform_assistant(assistant) -> AssistantOutput:
    """Transform full assistant model to output model.
    
    Raises:
        ProjectRequiredException: If assistant.project is None, empty, or DEMO_PROJECT
    """
    from codemie.core.project_validator import require_valid_project
    
    # Validate project using shared validator
    try:
        validated_project = require_valid_project(assistant.project, context="for assistant operations")
    except ProjectRequiredException:
        # Re-raise with assistant context
        raise ProjectRequiredException(
            message=f"Assistant {assistant.id} ({assistant.name}) has no valid project",
            details="All assistants must be associated with a real project you belong to.",
        )

    return AssistantOutput(
        id=assistant.id,
        name=assistant.name,
        description=assistant.description,
        system_prompt=assistant.system_prompt or "",
        project=validated_project,
        created_by=_transform_created_by(assistant.created_by),
        created_date=assistant.created_date.isoformat() if assistant.created_date else "",
        updated_date=assistant.update_date.isoformat() if assistant.update_date else "",
        llm_model_type=assistant.llm_model_type,
        tools=_transform_tools(assistant.toolkits) if hasattr(assistant, 'toolkits') else [],
        context=_transform_context(assistant.context) if hasattr(assistant, 'context') else [],
        sub_assistants_ids=assistant.assistant_ids if hasattr(assistant, 'assistant_ids') else [],
    )
```

- [ ] **Step 5: Update validation utils to reject DEMO_PROJECT fallback**

In `src/codemie/workflows/assistant_generator/nodes/validation/utils.py:92-128`, add validation:

```python
def get_validated_context_info(
    assistant: Assistant, user: User, configured_context: list[str]
) -> tuple[list[dict], set[str]]:
    """Validate configured context and return info with descriptions.

    Validates that configured context exists in the database using the same logic
    as add_assistant_context. Returns ONLY context that exists in the database.

    Args:
        assistant: Assistant being validated
        user: User requesting validation
        configured_context: List of configured context names

    Returns:
        Tuple of (validated_context_info, validated_names_set)
        - validated_context_info: List of dicts with repo_name, index_type, description
        - validated_names_set: Set of validated context names
        
    Raises:
        ProjectRequiredException: If resolved project is invalid
    """
    from codemie.core.project_validator import require_valid_project

    if not configured_context:
        return [], set()

    project_name = assistant.project or user.current_project
    
    # Validate resolved project using shared validator
    try:
        validated_project = require_valid_project(project_name, context="for context validation")
    except ProjectRequiredException:
        # Re-raise with assistant context
        raise ProjectRequiredException(
            message=f"Assistant {assistant.id} has no valid project",
            details=f"assistant.project={assistant.project!r}, user.current_project={user.current_project!r}. "
                   "A real project is required for context validation.",
        )

    context_index_infos = IndexInfo.filter_for_user_repo_names(
        user=user, project_name=validated_project, repo_names=configured_context
    )

    validated_context = [
        {
            "repo_name": idx.repo_name,
            "index_type": idx.index_type,
            "description": idx.description,
        }
        for idx in context_index_infos
    ]
    validated_names = {idx.repo_name for idx in context_index_infos}

    return validated_context, validated_names
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/agents/tools/platform/test_platform_tool.py -v`
Expected: PASS

Run: `pytest tests/workflows/assistant_generator/nodes/validation/test_utils.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/codemie/agents/tools/platform/platform_tool.py src/codemie/workflows/assistant_generator/nodes/validation/utils.py tests/agents/tools/platform/test_platform_tool.py tests/workflows/assistant_generator/nodes/validation/test_utils.py
git commit -m "feat(assistants): reject assistant operations with empty project

- platform_tool _transform_assistant raises ValueError on empty project
- validation utils rejects DEMO_PROJECT fallback
- Preserves user.current_project fallback for legitimate cases
- Closes EPMCDME-14451 acceptance criterion 2"
```

---

### Task 4: Create Migration Report for Empty-Project Assistants

**Files:**
- Create: `scripts/report_empty_project_assistants.py`
- Test: Manual verification

**Interfaces:**
- Consumes: PostgreSQL `assistants` table via SQLModel
- Produces: Log output (INFO level) reporting empty-project assistants count and sample IDs

**Test-first: no — Script outputs a report, no assertions to write**

**Risk Mitigations:**
- Addresses **Migration complexity** with simple logging approach (no admin API complexity)

- [ ] **Step 1: Create migration report script**

Create `scripts/report_empty_project_assistants.py`:

```python
"""One-off migration report: Find assistants with empty or None project field.

Usage:
    python scripts/report_empty_project_assistants.py

Logs count and sample assistant IDs to stdout for admin review.
Does NOT modify any data.
"""
import asyncio
from sqlmodel import select, or_
from codemie.clients.postgres import get_session
from codemie.rest_api.models.assistant import Assistant
from codemie.configs.logger import logger


async def report_empty_project_assistants():
    """Query and report assistants with empty or None project field."""
    logger.info("Starting empty-project assistants migration report")

    with get_session() as session:
        # Query assistants where project is NULL or empty string
        statement = select(Assistant).where(
            or_(
                Assistant.project.is_(None),
                Assistant.project == "",
            )
        )
        results = session.exec(statement).all()

        assistant_count = len(results)
        logger.info(f"Found {assistant_count} assistants with empty or None project")

        if assistant_count == 0:
            logger.info("No empty-project assistants found. Migration report complete.")
            return

        # Log summary
        logger.info(f"Empty-project assistants require admin review for project assignment:")
        
        # Sample first 10 for detailed logging
        sample_size = min(10, assistant_count)
        for i, assistant in enumerate(results[:sample_size]):
            logger.info(
                f"  [{i+1}] id={assistant.id}, name={assistant.name!r}, "
                f"project={assistant.project!r}, created_by={assistant.created_by}"
            )

        if assistant_count > sample_size:
            logger.info(f"  ... and {assistant_count - sample_size} more")

        # Log all IDs for admin tooling
        all_ids = [assistant.id for assistant in results]
        logger.info(f"All empty-project assistant IDs: {all_ids}")

        logger.info("Migration report complete. Admins should assign valid projects to these assistants.")


def main():
    """Entry point."""
    asyncio.run(report_empty_project_assistants())


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test script against local database**

Run: `python scripts/report_empty_project_assistants.py`
Expected: Log output showing count and sample assistant IDs (may be 0 in clean dev environment)

- [ ] **Step 3: Document script in README or docs**

Add entry to repository documentation (if migration scripts section exists) noting this is a one-off report for EPMCDME-14451.

No code changes to repository docs — script is self-documenting with usage docstring.

- [ ] **Step 4: Commit**

```bash
git add scripts/report_empty_project_assistants.py
git commit -m "feat(migration): add empty-project assistants report script

- One-off script to identify assistants with empty/None project
- Logs count, samples, and full ID list for admin review
- Does not modify data (report only)
- Closes EPMCDME-14451 acceptance criterion 3"
```

---

### Task 5: Reject Proxy Requests Without Valid Project

**Files:**
- Modify: `src/codemie/enterprise/litellm/proxy_router.py:290-304`
- Modify: `src/codemie/enterprise/litellm/proxy_router.py` (add validation before model selection)
- Test: `tests/enterprise/litellm/test_proxy_router.py`

**Interfaces:**
- Consumes: `HEADER_CODEMIE_CLI_PROJECT` from request headers
- Consumes: `is_background_consumer()` from Task 1 for exemption detection
- Produces: HTTP 400 with clear error message when project header is missing or empty (except for integration/background requests)

**Test-first: yes — Proxy router rejects requests without valid project header, exempts integration requests**

**Risk Mitigations:**
- Addresses **Background consumer identification** using `is_background_consumer()` from Task 1
- Addresses **No entry point validation** by checking project header early in request flow
- Addresses **CLI project guard scope** via backend enforcement (rejection surfaces to CLI naturally)

- [ ] **Step 1: Write failing test for proxy rejection without project**

In `tests/enterprise/litellm/test_proxy_router.py`, add new test class:

```python
class TestProxyProjectRequirement:
    """Test proxy router project requirement enforcement."""

    @pytest.mark.asyncio
    async def test_proxy_rejects_request_without_project_header(self):
        """Test proxy returns 400 when HEADER_CODEMIE_CLI_PROJECT is missing."""
        from unittest.mock import MagicMock, AsyncMock, patch
        from fastapi import Request
        from starlette.datastructures import Headers
        from codemie.enterprise.litellm.proxy_router import _validate_project_header
        from codemie.core.constants import HEADER_CODEMIE_CLI_PROJECT
        from codemie.core.project_validator import ProjectRequiredException

        # Arrange: request without project header
        mock_request = MagicMock(spec=Request)
        mock_request.headers = Headers({})
        mock_user = MagicMock()
        mock_user.username = "testuser"

        # Act & Assert
        with pytest.raises(ProjectRequiredException) as exc_info:
            _validate_project_header(mock_request.headers, mock_user)

        assert exc_info.value.code == 400
        assert "project" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_proxy_rejects_request_with_empty_project_header(self):
        """Test proxy returns 400 when HEADER_CODEMIE_CLI_PROJECT is empty string."""
        from unittest.mock import MagicMock
        from starlette.datastructures import Headers
        from codemie.enterprise.litellm.proxy_router import _validate_project_header
        from codemie.core.constants import HEADER_CODEMIE_CLI_PROJECT
        from codemie.core.project_validator import ProjectRequiredException

        # Arrange: request with empty project header
        mock_request = MagicMock()
        mock_request.headers = Headers({HEADER_CODEMIE_CLI_PROJECT: ""})
        mock_user = MagicMock()
        mock_user.username = "testuser"

        # Act & Assert
        with pytest.raises(ProjectRequiredException) as exc_info:
            _validate_project_header(mock_request.headers, mock_user)

        assert exc_info.value.code == 400
        assert "project" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_proxy_accepts_request_with_valid_project_header(self):
        """Test proxy accepts request when HEADER_CODEMIE_CLI_PROJECT has valid value."""
        from unittest.mock import MagicMock
        from starlette.datastructures import Headers
        from codemie.enterprise.litellm.proxy_router import _validate_project_header
        from codemie.core.constants import HEADER_CODEMIE_CLI_PROJECT

        # Arrange: request with valid project header
        mock_request = MagicMock()
        mock_request.headers = Headers({HEADER_CODEMIE_CLI_PROJECT: "valid-project"})
        mock_user = MagicMock()

        # Act: should not raise
        _validate_project_header(mock_request.headers, mock_user)

        # Assert: no exception raised (implicit)

    @pytest.mark.asyncio
    async def test_proxy_exempts_integration_requests_from_project_requirement(self):
        """Test proxy allows integration requests without project header."""
        from unittest.mock import MagicMock
        from starlette.datastructures import Headers
        from codemie.enterprise.litellm.proxy_router import _validate_project_header
        from codemie.core.constants import HEADER_CODEMIE_INTEGRATION

        # Arrange: integration request without project header
        mock_request = MagicMock()
        mock_request.headers = Headers({HEADER_CODEMIE_INTEGRATION: "integration-123"})
        mock_user = MagicMock()

        # Act: should not raise
        _validate_project_header(mock_request.headers, mock_user)

        # Assert: no exception raised (implicit)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/enterprise/litellm/test_proxy_router.py::TestProxyProjectRequirement -v`
Expected: FAIL with "function not defined" or "ProjectRequiredException not raised"

- [ ] **Step 3: Add project header validation function**

In `src/codemie/enterprise/litellm/proxy_router.py`, add validation function after imports:

```python
def _validate_project_header(headers: Headers | httpx.Headers | dict, user: User | None = None) -> None:
    """Validate project header is present and non-empty.
    
    Background consumers (identified by HEADER_CODEMIE_INTEGRATION) are exempt.
    
    Args:
        headers: Request headers
        user: Authenticated user (optional)
        
    Raises:
        ProjectRequiredException: If project header is missing or empty for non-exempt requests
    """
    from codemie.core.project_validator import is_background_consumer, require_valid_project, ProjectRequiredException
    
    # Exempt integration/background requests
    if is_background_consumer(dict(headers)):
        return
    
    project = headers.get(HEADER_CODEMIE_CLI_PROJECT)
    
    # Validate using shared validator
    try:
        require_valid_project(project, context="for proxy requests")
    except ProjectRequiredException:
        # Re-raise with CLI-specific instructions
        raise ProjectRequiredException(
            message="Project is required for API requests",
            details=(
                "Please set the project header (X-CodeMie-Project) "
                "or configure your CLI with a valid project. "
                "Run 'codemie config set project <project-name>' to set your project."
            ),
        )
```

- [ ] **Step 4: Update _extract_request_info to remove fallback**

In `src/codemie/enterprise/litellm/proxy_router.py:290-304`, update to remove username fallback (validation ensures project exists):

```python
def _extract_request_info(headers: Headers | httpx.Headers | dict, user: User | None = None) -> dict:
    """Extract request metadata from headers (uses codemie constants).
    
    Note: Project header is validated by _validate_project_header before this function.
    """
    project = headers.get(HEADER_CODEMIE_CLI_PROJECT) or ""
    return {
        CLIENT_TYPE: headers.get(HEADER_CODEMIE_CLIENT, UNKNOWN),
        SESSION_ID: headers.get(HEADER_CODEMIE_SESSION_ID, str(uuid.uuid4())),
        REQUEST_ID: headers.get(HEADER_CODEMIE_REQUEST_ID, str(uuid.uuid4())),
        LLM_MODEL: headers.get(HEADER_CODEMIE_CLI_MODEL, UNKNOWN),
        USER_AGENT: headers.get("User-Agent", UNKNOWN),
        CODEMIE_CLI: headers.get(HEADER_CODEMIE_CLI, ""),
        BRANCH: headers.get(HEADER_CODEMIE_CLI_BRANCH, ""),
        REPOSITORY: headers.get(HEADER_CODEMIE_CLI_REPOSITORY, ""),
        PROJECT: project,
        INTEGRATION: headers.get(HEADER_CODEMIE_INTEGRATION) or None,
    }
```

- [ ] **Step 5: Call validation in proxy endpoint**

Find the main proxy endpoint function (likely `/chat/completions` or similar) and add validation call at the start, before model selection. This will be in the same file, likely around where `_extract_request_info` is called.

Add validation call after authentication but before processing:

```python
# In the main proxy handler function, after user authentication:
_validate_project_header(request.headers, user)
request_info = _extract_request_info(request.headers, user)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/enterprise/litellm/test_proxy_router.py::TestProxyProjectRequirement -v`
Expected: PASS

Run: `pytest tests/enterprise/litellm/test_proxy_router.py -v`
Expected: All PASS (existing tests should still work)

- [ ] **Step 7: Commit**

```bash
git add src/codemie/enterprise/litellm/proxy_router.py tests/enterprise/litellm/test_proxy_router.py
git commit -m "feat(proxy): require valid project header on proxy requests

- Add _validate_project_header to reject empty/missing project
- Exempt integration requests (HEADER_CODEMIE_INTEGRATION)
- Return HTTP 400 with CLI configuration instructions
- Remove username fallback from _extract_request_info
- Closes EPMCDME-14451 acceptance criteria 4, 5"
```

---

## Self-Review: Negative Constraints

Reviewing requirements for negative constraints:

- **"no governed request can arrive with an empty, username-derived, or silently substituted project"** — Task 1 provides shared validation; Task 2 removes silent failure on sign-in; Task 3 removes empty-string fallback in platform_tool and rejects DEMO_PROJECT; Task 5 removes username fallback in proxy_router and adds validation.
- **"Background consumers unaffected"** — Task 1 creates `is_background_consumer()` to identify integration requests; Task 5 exempts them from validation. Background consumers (datasource processors, skill/workflow generators) make requests with HEADER_CODEMIE_INTEGRATION and remain unaffected.

**negative-constraints: all constraints honored**

---

## Plan Complete

This plan enforces project requirements across all member-driven request paths through shared validation utilities (Task 1), eliminating multiple fallback chains and silent failures while preserving background consumer functionality. Each task is independently testable and commits working changes.

**Risk mitigation achieved:**
- Task 1 addresses 3 risks (multiple fallback chains, empty string fallbacks, background consumer identification)
- Task 2 addresses 3 risks (silent failure propagation, external/service_account users, test coverage debt)
- Task 3 addresses 3 risks (empty string fallbacks, implicit user.current_project fallback, no entry point validation)
- Task 4 addresses 1 risk (migration complexity)
- Task 5 addresses 3 risks (background consumer identification, no entry point validation, CLI project guard scope)

Total tasks: 5
Test-first tasks: 4
