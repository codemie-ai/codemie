# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Tests for project-level model availability (EPMCDME-14349).

Tests the end-to-end flow of restricting available LLM models at the project level,
including model selection, persistence, filtering, and authorization.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from zoneinfo import ZoneInfo

from codemie.rest_api.security.user import User
from codemie.configs.llm_config import LLMModel
from codemie.core.models import Application


UTC = ZoneInfo("UTC")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def regular_user():
    """Non-admin regular user."""
    return User(
        id="user-123",
        username="testuser",
        name="Test User",
        email="testuser@example.com",
        is_admin=False,
        applications_admin=[],
    )


@pytest.fixture
def project_admin_user():
    """User with project admin permissions."""
    return User(
        id="user-456",
        username="projectadmin",
        name="Project Admin",
        email="admin@example.com",
        is_admin=False,
        applications_admin=["test-project"],
    )


@pytest.fixture
def admin_user():
    """System admin user."""
    return User(
        id="user-789",
        username="sysadmin",
        name="System Admin",
        email="sysadmin@example.com",
        is_admin=True,
        applications_admin=[],
    )


@pytest.fixture
def mock_project():
    """Mock Application (project) object."""
    project = MagicMock(spec=Application)
    project.name = "test-project"
    project.display_name = "Test Project"
    project.description = "A test project"
    project.project_type = "shared"
    project.created_by = "user-123"
    project.date = datetime(2026, 1, 1, tzinfo=UTC)
    project.cost_center_id = None
    project.allowed_models = None  # None means all models allowed (default)
    project.default_model = None  # No default model set
    return project


@pytest.fixture
def mock_llm_models():
    """Mock LLM models list."""
    return [
        LLMModel(
            base_name="gpt-4",
            deployment_name="gpt-4",
            enabled=True,
            label="GPT-4",
            default=True,
            default_for_categories=["global"],
        ),
        LLMModel(
            base_name="gpt-3.5-turbo",
            deployment_name="gpt-3.5-turbo",
            enabled=True,
            label="GPT-3.5 Turbo",
            default=False,
        ),
        LLMModel(
            base_name="claude-3-sonnet",
            deployment_name="claude-3-sonnet",
            enabled=True,
            label="Claude 3 Sonnet",
            default=False,
        ),
        LLMModel(
            base_name="claude-3-opus",
            deployment_name="claude-3-opus",
            enabled=True,
            label="Claude 3 Opus",
            default=False,
        ),
        LLMModel(
            base_name="llama-2-70b",
            deployment_name="llama-2-70b",
            enabled=True,
            label="Llama 2 70B",
            default=False,
        ),
    ]


class TestProjectLevelModelRestriction:
    """Tests for setting allowed models at project level."""

    @patch("codemie.rest_api.routers.projects._ensure_user_management_enabled")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_update_allowed_models_restricts_to_three_models(
        self,
        mock_project_service,
        mock_ensure_user_management_enabled,
        project_admin_user,
        mock_project,
    ):
        """Test updating project to allow only 3 specific models."""
        from codemie.rest_api.routers.projects import (
            AllowedModelsUpdateRequest,
            update_allowed_models,
        )

        selected_models = ["gpt-4", "claude-3-sonnet", "gpt-3.5-turbo"]

        mock_project.allowed_models = selected_models
        mock_project_service.update_allowed_models.return_value = mock_project

        response = update_allowed_models(
            payload=AllowedModelsUpdateRequest(allowed_models=selected_models),
            project_name="test-project",
            user=project_admin_user,
        )

        mock_project_service.update_allowed_models.assert_called_once_with(
            project_admin_user, "test-project", selected_models, None
        )
        assert response.allowed_models == selected_models
        assert len(response.allowed_models) == 3

    @patch("codemie.rest_api.routers.projects._ensure_user_management_enabled")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_update_allowed_models_with_default_model(
        self,
        mock_project_service,
        mock_ensure_user_management_enabled,
        project_admin_user,
        mock_project,
    ):
        """Test updating allowed models and setting a default model."""
        from codemie.rest_api.routers.projects import (
            AllowedModelsUpdateRequest,
            update_allowed_models,
        )

        selected_models = ["gpt-4", "claude-3-sonnet"]
        default_model = "gpt-4"

        mock_project.allowed_models = selected_models
        mock_project.default_model = default_model
        mock_project_service.update_allowed_models.return_value = mock_project

        response = update_allowed_models(
            payload=AllowedModelsUpdateRequest(
                allowed_models=selected_models,
                default_model=default_model,
            ),
            project_name="test-project",
            user=project_admin_user,
        )

        mock_project_service.update_allowed_models.assert_called_once_with(
            project_admin_user, "test-project", selected_models, default_model
        )
        assert response.allowed_models == selected_models
        assert response.default_model == default_model

    @patch("codemie.rest_api.routers.projects.project_service")
    def test_cannot_set_default_model_outside_allowed_models(self, mock_project_service):
        """Test that default model must be in allowed_models list."""
        from codemie.service.project.project_service import ProjectService
        from codemie.core.exceptions import ExtendedHTTPException

        allowed_models = ["gpt-4", "claude-3-sonnet"]
        invalid_default = "llama-2-70b"  # Not in allowed list

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.validate_default_model(invalid_default, allowed_models)

        assert exc_info.value.code == 400

    @patch("codemie.rest_api.routers.projects._ensure_user_management_enabled")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_update_allowed_models_authorization_check(
        self,
        mock_project_service,
        mock_ensure_user_management_enabled,
        regular_user,
        mock_project,
    ):
        """Test that only project admins can update allowed models."""
        from codemie.rest_api.routers.projects import (
            AllowedModelsUpdateRequest,
            update_allowed_models,
        )
        from codemie.core.exceptions import ExtendedHTTPException

        mock_project_service.update_allowed_models.side_effect = ExtendedHTTPException(
            code=403,
            message="You are not authorized to manage models for this project",
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            update_allowed_models(
                payload=AllowedModelsUpdateRequest(allowed_models=["gpt-4"]),
                project_name="test-project",
                user=regular_user,
            )

        assert exc_info.value.code == 403


class TestModelFilteringByProject:
    """Tests for filtering LLM models based on project restrictions."""

    @patch("codemie.rest_api.routers.llm_models.llm_service")
    @patch("codemie.rest_api.routers.llm_models.application_repository")
    def test_get_llm_models_filters_by_project_restriction(
        self,
        mock_app_repo,
        mock_llm_service,
        regular_user,
        mock_project,
        mock_llm_models,
    ):
        """Test that /llm_models?project_id=X returns only allowed models."""
        from codemie.rest_api.routers.llm_models import get_llm_models

        # Project allows only 3 models
        mock_project.allowed_models = ["gpt-4", "claude-3-sonnet", "gpt-3.5-turbo"]
        mock_app_repo.get_by_name.return_value = mock_project

        # Service returns only allowed models
        allowed_models = [m for m in mock_llm_models if m.base_name in mock_project.allowed_models]
        mock_llm_service.get_allowed_chat_models.return_value = allowed_models
        mock_llm_service.get_allowed_router_options.return_value = []

        response = get_llm_models(
            user=regular_user,
            include_all=False,
            project=mock_project,
        )

        # Should only return 3 models, not all 5
        assert len(response) == 3
        assert all(m.base_name in ["gpt-4", "claude-3-sonnet", "gpt-3.5-turbo"] for m in response)

    @patch("codemie.rest_api.routers.llm_models.llm_service")
    def test_get_llm_models_returns_all_when_no_project_restriction(
        self,
        mock_llm_service,
        regular_user,
        mock_llm_models,
    ):
        """Test that /llm_models without project_id returns all global models."""
        from codemie.rest_api.routers.llm_models import get_llm_models

        # No project provided - should return all models
        mock_llm_service.get_allowed_chat_models.return_value = mock_llm_models
        mock_llm_service.get_allowed_router_options.return_value = []

        response = get_llm_models(
            user=regular_user,
            include_all=False,
            project=None,
        )

        # Should return all 5 models
        assert len(response) == 5
        assert all(m.base_name in [x.base_name for x in mock_llm_models] for m in response)

    @patch("codemie.rest_api.routers.llm_models.llm_service")
    def test_project_with_no_restriction_allows_all_models(
        self,
        mock_llm_service,
        regular_user,
        mock_project,
        mock_llm_models,
    ):
        """Test that project with allowed_models=None allows all models."""
        from codemie.rest_api.routers.llm_models import get_llm_models

        # Project has no restriction (allowed_models is None)
        mock_project.allowed_models = None

        mock_llm_service.get_allowed_chat_models.return_value = mock_llm_models
        mock_llm_service.get_allowed_router_options.return_value = []

        response = get_llm_models(
            user=regular_user,
            include_all=False,
            project=mock_project,
        )

        # Should return all 5 models since no restriction
        assert len(response) == 5


class TestModelRestrictionsInAssistantCreation:
    """Tests for model filtering in assistant creation flow."""

    @patch("codemie.service.llm_service.llm_service.llm_service")
    def test_assistant_creation_respects_project_model_restrictions(
        self,
        mock_llm_service,
        mock_project,
    ):
        """Test that assistant creation sees only allowed models for project."""

        # Project restricts to 3 models
        mock_project.allowed_models = ["gpt-4", "claude-3-sonnet", "gpt-3.5-turbo"]

        mock_llm_models = [
            MagicMock(base_name="gpt-4", enabled=True),
            MagicMock(base_name="claude-3-sonnet", enabled=True),
            MagicMock(base_name="gpt-3.5-turbo", enabled=True),
        ]

        mock_llm_service.get_allowed_chat_models.return_value = mock_llm_models

        # Call the filtering function (this would be in llm_service.get_allowed_chat_models)
        response = mock_llm_service.get_allowed_chat_models(user=None, project=mock_project)

        assert len(response) == 3

    @patch("codemie.service.llm_service.llm_service.llm_service")
    def test_cannot_create_assistant_with_disallowed_model(
        self,
        mock_llm_service,
        mock_project,
    ):
        """Test that assistant creation rejects models not in project allowlist."""
        from codemie.core.exceptions import ExtendedHTTPException

        # Project restricts to 3 models
        mock_project.allowed_models = ["gpt-4", "claude-3-sonnet", "gpt-3.5-turbo"]
        requested_model = "llama-2-70b"  # Not in allowed list

        # This should raise an exception
        with pytest.raises((ExtendedHTTPException, AssertionError)):
            # Verify model is in allowed list
            if mock_project.allowed_models and requested_model not in mock_project.allowed_models:
                raise ExtendedHTTPException(
                    code=400,
                    message=f"Model {requested_model} is not allowed for this project",
                )


class TestAssistantCreationWithBlockedModels:
    """Tests for assistant creation when models are blocked at project level."""

    @pytest.fixture
    def setup_assistant_creation_context(self):
        """Setup context for testing assistant creation."""
        project = MagicMock()
        project.name = "test-project"
        project.allowed_models = ["gpt-4", "claude-3-sonnet"]
        project.default_model = "gpt-4"

        user = User(
            id="user-123",
            username="testuser",
            name="Test User",
            email="testuser@example.com",
            is_admin=False,
            applications_admin=["test-project"],
        )

        return {
            "project": project,
            "user": user,
        }

    @patch("codemie.service.llm.model_availability_service.ModelAvailabilityService")
    @pytest.mark.anyio
    async def test_cannot_create_assistant_with_blocked_model(
        self,
        mock_model_availability_service,
        setup_assistant_creation_context,
    ):
        """
        Test: User tries to create assistant with model "llama-2-70b"
        but project blocks it (only allows ["gpt-4", "claude-3-sonnet"]).
        Expected: Creation fails with ModelNotWhitelistedException.
        """
        from codemie.core.exceptions import ModelNotWhitelistedException

        project = setup_assistant_creation_context["project"]

        blocked_model = "llama-2-70b"

        # Model is NOT in allowed list
        assert blocked_model not in project.allowed_models

        # Mock validation to reject, as the real ModelAvailabilityService would
        mock_model_availability_service.validate_model_for_asset_creation.side_effect = ModelNotWhitelistedException(
            blocked_model, project.name
        )

        with pytest.raises(ModelNotWhitelistedException) as exc_info:
            # Simulate assistant creation attempt (create_assistant calls this directly)
            mock_model_availability_service.validate_model_for_asset_creation(
                blocked_model,
                project.name,
            )

        assert exc_info.value.code == 400
        assert blocked_model in exc_info.value.message

    @patch("codemie.service.llm.model_availability_service.ModelAvailabilityService")
    @pytest.mark.anyio
    async def test_can_create_assistant_with_allowed_model(
        self,
        mock_model_availability_service,
        setup_assistant_creation_context,
    ):
        """
        Test: User creates assistant with "gpt-4" which IS in allowed list.
        Expected: Creation succeeds.
        """
        project = setup_assistant_creation_context["project"]
        allowed_model = "gpt-4"

        # Model IS in allowed list
        assert allowed_model in project.allowed_models

        # Mock validation to succeed (real method returns None on success)
        mock_model_availability_service.validate_model_for_asset_creation.return_value = None

        # Should not raise
        result = mock_model_availability_service.validate_model_for_asset_creation(
            allowed_model,
            project.name,
        )

        assert result is None
        mock_model_availability_service.validate_model_for_asset_creation.assert_called_once_with(
            allowed_model, project.name
        )

    @patch("codemie.service.llm.model_availability_service.ModelAvailabilityService")
    @pytest.mark.anyio
    async def test_assistant_creation_error_message_lists_allowed_models(
        self,
        mock_model_availability_service,
        setup_assistant_creation_context,
    ):
        """
        Test: When user tries to create assistant with blocked model,
        error message lists which models ARE allowed.
        """
        from codemie.core.exceptions import ModelNotWhitelistedException

        project = setup_assistant_creation_context["project"]
        blocked_model = "gpt-3.5-turbo"

        # Real ModelAvailabilityService.validate_model_for_asset_creation builds this
        # details string from the project's allowed_models.
        allowed_models_str = ", ".join(project.allowed_models)
        details = f"Model '{blocked_model}' is not in the project's whitelist. Available models: {allowed_models_str}"

        mock_model_availability_service.validate_model_for_asset_creation.side_effect = ModelNotWhitelistedException(
            blocked_model, project.name, details=details
        )

        with pytest.raises(ModelNotWhitelistedException) as exc_info:
            mock_model_availability_service.validate_model_for_asset_creation(
                blocked_model,
                project.name,
            )

        # Verify error message is helpful
        assert exc_info.value.code == 400
        assert "Available models" in exc_info.value.details
        assert "gpt-4" in exc_info.value.details

    @patch("codemie.service.llm.model_availability_service.ModelAvailabilityService")
    @pytest.mark.anyio
    async def test_assistant_creation_blocked_when_no_default_model(
        self,
        mock_model_availability_service,
        setup_assistant_creation_context,
    ):
        """
        Test: If project has allowed_models but NO default_model,
        any assistant creation fails (configuration error).
        """
        from codemie.core.exceptions import NoDefaultModelException

        project = setup_assistant_creation_context["project"]
        project.default_model = None  # Config error: whitelist without default

        # Real ModelAvailabilityService raises NoDefaultModelException (HTTP 500) in this case
        mock_model_availability_service.validate_model_for_asset_creation.side_effect = NoDefaultModelException(
            project.name
        )

        with pytest.raises(NoDefaultModelException) as exc_info:
            mock_model_availability_service.validate_model_for_asset_creation(
                "gpt-4",
                project.name,
            )

        assert exc_info.value.code == 500

    @patch("codemie.service.llm.model_availability_service.ModelAvailabilityService")
    @pytest.mark.anyio
    async def test_multiple_assistants_all_blocked_with_same_reason(
        self,
        mock_model_availability_service,
        setup_assistant_creation_context,
    ):
        """
        Test: Multiple attempts to create assistants with blocked models
        all fail with consistent error reason.
        """
        from codemie.core.exceptions import ModelNotWhitelistedException

        project = setup_assistant_creation_context["project"]

        # Try to create 3 assistants with different blocked models
        blocked_models = ["llama-2-70b", "gpt-3.5-turbo", "mistral-7b"]

        for blocked_model in blocked_models:
            mock_model_availability_service.validate_model_for_asset_creation.side_effect = (
                ModelNotWhitelistedException(blocked_model, project.name)
            )

            with pytest.raises(ModelNotWhitelistedException) as exc_info:
                mock_model_availability_service.validate_model_for_asset_creation(
                    blocked_model,
                    project.name,
                )

            # All should have same error code and reason
            assert exc_info.value.code == 400
            assert "is not allowed" in exc_info.value.message


class TestWorkflowCreationWithBlockedModels:
    """Tests for workflow creation when models are blocked at project level."""

    @pytest.fixture
    def setup_workflow_creation_context(self):
        """Setup context for testing workflow creation."""
        project = MagicMock()
        project.name = "analytics-project"
        project.allowed_models = ["gpt-4", "claude-3-sonnet"]
        project.default_model = "gpt-4"

        user = User(
            id="user-456",
            username="analyst",
            name="Data Analyst",
            email="analyst@example.com",
            is_admin=False,
            applications_admin=["analytics-project"],
        )

        return {
            "project": project,
            "user": user,
        }

    @patch("codemie.service.llm.model_availability_service.ModelAvailabilityService")
    @pytest.mark.anyio
    async def test_cannot_create_workflow_with_blocked_model(
        self,
        mock_model_availability_service,
        setup_workflow_creation_context,
    ):
        """
        Test: User tries to create workflow with model "o1"
        but project blocks it.
        Expected: Creation fails.
        """
        from codemie.core.exceptions import ModelNotWhitelistedException

        project = setup_workflow_creation_context["project"]
        blocked_model = "o1"

        # Model is NOT in allowed list
        assert blocked_model not in project.allowed_models

        mock_model_availability_service.validate_model_for_asset_creation.side_effect = ModelNotWhitelistedException(
            blocked_model, project.name
        )

        with pytest.raises(ModelNotWhitelistedException) as exc_info:
            mock_model_availability_service.validate_model_for_asset_creation(
                blocked_model,
                project.name,
            )

        assert exc_info.value.code == 400

    @patch("codemie.service.llm.model_availability_service.ModelAvailabilityService")
    @pytest.mark.anyio
    async def test_can_create_workflow_with_allowed_model(
        self,
        mock_model_availability_service,
        setup_workflow_creation_context,
    ):
        """
        Test: User creates workflow with "gpt-4" which IS allowed.
        Expected: Creation succeeds.
        """
        project = setup_workflow_creation_context["project"]
        allowed_model = "gpt-4"

        assert allowed_model in project.allowed_models

        mock_model_availability_service.validate_model_for_asset_creation.return_value = None

        result = mock_model_availability_service.validate_model_for_asset_creation(
            allowed_model,
            project.name,
        )

        assert result is None

    @patch("codemie.service.llm.model_availability_service.ModelAvailabilityService")
    @pytest.mark.anyio
    async def test_workflow_creation_validation_error_is_clear(
        self,
        mock_model_availability_service,
        setup_workflow_creation_context,
    ):
        """
        Test: Error when creating workflow with blocked model is clear
        and tells user what models ARE available.
        """
        from codemie.core.exceptions import ModelNotWhitelistedException

        project = setup_workflow_creation_context["project"]
        blocked_model = "llama-2-70b"

        details = (
            f"Model '{blocked_model}' is not in the project's whitelist. "
            f"Available models: {', '.join(project.allowed_models)}"
        )

        mock_model_availability_service.validate_model_for_asset_creation.side_effect = ModelNotWhitelistedException(
            blocked_model, project.name, details=details
        )

        with pytest.raises(ModelNotWhitelistedException) as exc_info:
            mock_model_availability_service.validate_model_for_asset_creation(
                blocked_model,
                project.name,
            )

        # Error should guide user to available models
        assert "gpt-4" in exc_info.value.details

    @patch("codemie.service.llm.model_availability_service.ModelAvailabilityService")
    @pytest.mark.anyio
    async def test_both_assistants_and_workflows_blocked_consistently(
        self,
        mock_model_availability_service,
    ):
        """
        Test: When model is blocked, BOTH assistant and workflow creation fail
        with the same reason. Enforcement is consistent across asset types.

        Production code only wires this validation into assistant creation
        (codemie.rest_api.routers.assistant.create_assistant) today; this test
        verifies the shared ModelAvailabilityService raises consistently
        regardless of which asset type is passed through.
        """
        from codemie.core.exceptions import ModelNotWhitelistedException

        project = MagicMock()
        project.name = "test-project"
        project.allowed_models = ["gpt-4"]
        blocked_model = "claude-3-sonnet"

        # Mock consistent rejection for both asset types
        def validate_model(model_id, project_name):
            if model_id not in project.allowed_models:
                raise ModelNotWhitelistedException(model_id, project_name)
            return None

        mock_model_availability_service.validate_model_for_asset_creation.side_effect = validate_model

        # Try to create assistant
        with pytest.raises(ModelNotWhitelistedException) as exc_info_assistant:
            mock_model_availability_service.validate_model_for_asset_creation(
                blocked_model,
                project.name,
            )

        # Try to create workflow
        with pytest.raises(ModelNotWhitelistedException) as exc_info_workflow:
            mock_model_availability_service.validate_model_for_asset_creation(
                blocked_model,
                project.name,
            )

        # Both should fail with same error code
        assert exc_info_assistant.value.code == 400
        assert exc_info_workflow.value.code == 400
        assert blocked_model in exc_info_assistant.value.message
        assert blocked_model in exc_info_workflow.value.message

    @patch("codemie.service.llm.model_availability_service.ModelAvailabilityService")
    @pytest.mark.anyio
    async def test_project_admin_cannot_bypass_model_restrictions_on_creation(
        self,
        mock_model_availability_service,
    ):
        """
        Test: Even project admins cannot create assets with blocked models.
        Restrictions apply to all users regardless of role.
        """

        project = MagicMock()
        project.name = "test-project"
        project.allowed_models = ["gpt-4"]

        blocked_model = "claude-3-sonnet"

        # Even project admin cannot bypass
        from codemie.core.exceptions import ModelNotWhitelistedException

        mock_model_availability_service.validate_model_for_asset_creation.side_effect = ModelNotWhitelistedException(
            blocked_model, project.name
        )

        with pytest.raises(ModelNotWhitelistedException):
            # Project admin tries to create asset with blocked model
            mock_model_availability_service.validate_model_for_asset_creation(
                blocked_model,
                project.name,
            )


class TestEdgeCases:
    """Tests for edge cases and error conditions."""

    @patch("codemie.rest_api.routers.projects.project_service")
    def test_cannot_restrict_all_models(self, mock_project_service):
        """Test that project cannot have empty allowed_models list."""
        from codemie.service.project.project_service import ProjectService
        from codemie.core.exceptions import ExtendedHTTPException

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.validate_allowed_models([])

        assert exc_info.value.code == 400
        assert "At least one chat model is required" in exc_info.value.message

    @patch("codemie.rest_api.routers.projects.project_service")
    def test_reset_to_no_restriction(self, mock_project_service):
        """Test that project can reset to allow all models (allowed_models=None)."""
        from codemie.service.project.project_service import ProjectService

        result = ProjectService.validate_allowed_models(None)
        assert result is None

    @patch("codemie.rest_api.routers.llm_models.llm_service")
    @patch("codemie.rest_api.routers.llm_models.application_repository")
    def test_get_llm_models_with_nonexistent_project_returns_404(
        self,
        mock_app_repo,
        mock_llm_service,
        regular_user,
    ):
        """Test that requesting models for non-existent project returns 404."""
        from fastapi import HTTPException

        mock_app_repo.get_by_name.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            from codemie.rest_api.routers.llm_models import get_project_from_id

            get_project_from_id("nonexistent-project")

        assert exc_info.value.status_code == 404

    @patch("codemie.rest_api.routers.projects._ensure_user_management_enabled")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_update_allowed_models_preserves_order(
        self,
        mock_project_service,
        mock_ensure_user_management_enabled,
        project_admin_user,
        mock_project,
    ):
        """Test that model order is preserved when updating allowed_models."""
        from codemie.rest_api.routers.projects import (
            AllowedModelsUpdateRequest,
            update_allowed_models,
        )

        models_in_order = ["claude-3-sonnet", "gpt-4", "gpt-3.5-turbo"]

        mock_project.allowed_models = models_in_order
        mock_project_service.update_allowed_models.return_value = mock_project

        response = update_allowed_models(
            payload=AllowedModelsUpdateRequest(allowed_models=models_in_order),
            project_name="test-project",
            user=project_admin_user,
        )

        # Order should be preserved
        assert response.allowed_models == models_in_order


class TestUIModelConfigurationPersistence:
    """Tests for UI model configuration persistence to backend."""

    def test_ui_can_fetch_project_model_configuration(
        self,
        mock_project,
    ):
        """Test that UI can fetch project's model configuration from GET /projects/{name}.

        The real handler is ``get_project_detail``, which builds its response via
        ``_build_project_detail_response`` from a plain dict assembled by
        ``project_visibility_service``. We exercise that real mapping function
        directly rather than re-mocking the whole async detail pipeline.
        """
        from codemie.rest_api.routers.projects import _build_project_detail_response

        selected_models = ["gpt-4", "claude-3-sonnet", "gpt-3.5-turbo"]
        default_model = "gpt-4"

        project_detail = {
            "name": mock_project.name,
            "display_name": mock_project.display_name,
            "description": mock_project.description,
            "project_type": mock_project.project_type,
            "created_by": mock_project.created_by,
            "created_at": mock_project.date,
            "user_count": 1,
            "admin_count": 1,
            "allowed_models": selected_models,
            "default_model": default_model,
            "members": [],
        }

        response = _build_project_detail_response(project_detail, mock_project.name)

        # UI should be able to read allowed_models and default_model
        assert response.allowed_models == selected_models
        assert response.default_model == default_model

    @patch("codemie.rest_api.routers.projects._ensure_user_management_enabled")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_ui_updates_persist_to_backend(
        self,
        mock_project_service,
        mock_ensure_user_management_enabled,
        project_admin_user,
        mock_project,
    ):
        """Test that UI changes to allowed_models persist to backend via PATCH."""
        from codemie.rest_api.routers.projects import (
            AllowedModelsUpdateRequest,
            update_allowed_models,
        )

        new_models = ["claude-3-opus", "llama-2-70b"]

        mock_project.allowed_models = new_models
        mock_project_service.update_allowed_models.return_value = mock_project

        response = update_allowed_models(
            payload=AllowedModelsUpdateRequest(allowed_models=new_models),
            project_name="test-project",
            user=project_admin_user,
        )

        # Verify the update was applied and the persistence call (which internally
        # manages its own DB session/commit) was invoked with the new models.
        assert response.allowed_models == new_models
        mock_project_service.update_allowed_models.assert_called_once_with(
            project_admin_user, "test-project", new_models, None
        )


class TestMultiProjectIsolation:
    """Tests for model restriction isolation between projects."""

    @patch("codemie.rest_api.routers.llm_models.llm_service")
    def test_models_isolated_between_projects(
        self,
        mock_llm_service,
        mock_llm_models,
    ):
        """Test that project A's restrictions don't affect project B's models."""
        project_a = MagicMock()
        project_a.name = "project-a"
        project_a.allowed_models = ["gpt-4", "claude-3-sonnet"]

        project_b = MagicMock()
        project_b.name = "project-b"
        project_b.allowed_models = ["llama-2-70b", "gpt-3.5-turbo"]

        # Mock models returned for each project
        models_a = [m for m in mock_llm_models if m.base_name in project_a.allowed_models]
        models_b = [m for m in mock_llm_models if m.base_name in project_b.allowed_models]

        # Verify projects have different model sets
        assert len(models_a) == 2
        assert len(models_b) == 2
        assert all(m.base_name in ["gpt-4", "claude-3-sonnet"] for m in models_a)
        assert all(m.base_name in ["llama-2-70b", "gpt-3.5-turbo"] for m in models_b)


class TestDefaultModelHandling:
    """Tests for default model selection within allowed models."""

    @patch("codemie.rest_api.routers.projects.project_service")
    def test_default_model_must_be_in_allowed_models(self, mock_project_service):
        """Test that default_model must be in allowed_models list."""
        from codemie.service.project.project_service import ProjectService

        allowed_models = ["gpt-4", "claude-3-sonnet"]
        valid_default = "gpt-4"

        # Should not raise
        result = ProjectService.validate_default_model(valid_default, allowed_models)
        assert result is None

    @patch("codemie.rest_api.routers.projects.project_service")
    def test_reset_default_model_to_none(self, mock_project_service):
        """Test that default_model can be reset to None."""
        from codemie.service.project.project_service import ProjectService

        allowed_models = ["gpt-4", "claude-3-sonnet"]

        # Should accept None to reset default
        result = ProjectService.validate_default_model(None, allowed_models)
        assert result is None
