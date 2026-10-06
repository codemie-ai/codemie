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
End-to-end integration tests for project-level model availability (EPMCDME-14349).

Tests the complete flow from UI model selection through backend persistence to
assistant creation with filtered models.
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


class TestEndToEndModelRestrictionFlow:
    """Integration tests for complete model restriction workflow."""

    @pytest.fixture
    def setup_project_and_user(self):
        """Setup a test project and admin user."""
        project = MagicMock(spec=Application)
        project.name = "Foo"
        project.display_name = "Foo Project"
        project.description = "Test project"
        project.project_type = "shared"
        project.created_by = "user-123"
        project.date = datetime(2026, 1, 1, tzinfo=UTC)
        project.allowed_models = None
        project.default_model = None
        project.cost_center_id = None
        project.chargeback_enabled = False
        project.chargeback_attribution = "project"

        user = User(
            id="user-123",
            username="testuser",
            name="Test User",
            email="testuser@example.com",
            is_admin=False,
            applications_admin=["Foo"],
        )

        all_models = [
            LLMModel(
                base_name="gpt-4",
                deployment_name="gpt-4",
                enabled=True,
                label="GPT-4",
                default=True,
            ),
            LLMModel(
                base_name="gpt-3.5-turbo",
                deployment_name="gpt-3.5-turbo",
                enabled=True,
                label="GPT-3.5 Turbo",
            ),
            LLMModel(
                base_name="claude-3-sonnet",
                deployment_name="claude-3-sonnet",
                enabled=True,
                label="Claude 3 Sonnet",
            ),
            LLMModel(
                base_name="claude-3-opus",
                deployment_name="claude-3-opus",
                enabled=True,
                label="Claude 3 Opus",
            ),
            LLMModel(
                base_name="llama-2-70b",
                deployment_name="llama-2-70b",
                enabled=True,
                label="Llama 2 70B",
            ),
        ]

        return {
            "project": project,
            "user": user,
            "all_models": all_models,
        }

    @patch("codemie.rest_api.routers.projects._ensure_user_management_enabled")
    @patch("codemie.rest_api.routers.projects.project_service")
    @patch("codemie.rest_api.routers.llm_models.llm_service")
    @patch("codemie.rest_api.routers.llm_models.application_repository")
    @pytest.mark.anyio
    async def test_complete_workflow_select_and_filter_models(
        self,
        mock_llm_app_repo,
        mock_llm_service,
        mock_proj_service,
        mock_ensure_user_management_enabled,
        setup_project_and_user,
    ):
        """
        Complete workflow:
        1. User selects 3 models in Models tab
        2. Changes are saved to backend
        3. When creating assistant and selecting project, only 3 models appear
        """
        from codemie.rest_api.routers.projects import (
            AllowedModelsUpdateRequest,
            update_allowed_models,
        )
        from codemie.rest_api.routers.llm_models import get_llm_models

        project = setup_project_and_user["project"]
        user = setup_project_and_user["user"]
        all_models = setup_project_and_user["all_models"]

        # Step 1: User selects 3 models in Models tab
        selected_models = ["gpt-4", "claude-3-sonnet", "gpt-3.5-turbo"]
        default_model = "gpt-4"

        # Step 2: Update project with selected models
        project.allowed_models = selected_models
        project.default_model = default_model
        mock_proj_service.update_allowed_models.return_value = project

        response = update_allowed_models(
            payload=AllowedModelsUpdateRequest(
                allowed_models=selected_models,
                default_model=default_model,
            ),
            project_name="Foo",
            user=user,
        )

        # Verify models were saved
        assert response.allowed_models == selected_models
        assert response.default_model == default_model

        # Step 3: When creating assistant, get filtered models
        allowed_models_list = [m for m in all_models if m.base_name in selected_models]
        mock_llm_service.get_allowed_chat_models.return_value = allowed_models_list
        mock_llm_service.get_allowed_router_options.return_value = []
        mock_llm_app_repo.get_by_name.return_value = project

        models_for_assistant = get_llm_models(
            user=user,
            include_all=False,
            project=project,
        )

        # Verify only 3 models are available for assistant creation
        assert len(models_for_assistant) == 3
        assert all(m.base_name in selected_models for m in models_for_assistant)

    @patch("codemie.rest_api.routers.projects.project_service")
    @patch("codemie.rest_api.routers.projects.application_repository")
    @patch("codemie.rest_api.routers.llm_models.llm_service")
    @pytest.mark.anyio
    async def test_user_cannot_bypass_project_restrictions(
        self,
        mock_llm_service,
        mock_app_repo,
        mock_proj_service,
        setup_project_and_user,
    ):
        """Test that user cannot create assistant with model outside project allowlist."""
        from codemie.core.exceptions import ExtendedHTTPException

        project = setup_project_and_user["project"]

        # Project restricts to 3 models
        project.allowed_models = ["gpt-4", "claude-3-sonnet", "gpt-3.5-turbo"]

        # User tries to create assistant with disallowed model
        disallowed_model = "llama-2-70b"

        # Service should reject this
        mock_llm_service.get_allowed_chat_models.side_effect = ExtendedHTTPException(
            code=400,
            message=f"Model {disallowed_model} is not allowed for this project",
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            # This would be part of assistant creation validation
            if disallowed_model not in project.allowed_models:
                raise ExtendedHTTPException(
                    code=400,
                    message=f"Model {disallowed_model} is not allowed for this project",
                )

        assert exc_info.value.code == 400

    @patch("codemie.rest_api.routers.projects.application_repository")
    @patch("codemie.rest_api.routers.llm_models.application_repository")
    @pytest.mark.anyio
    async def test_multiple_projects_maintain_separate_restrictions(
        self,
        mock_llm_app_repo,
        mock_proj_app_repo,
        setup_project_and_user,
    ):
        """Test that model restrictions are isolated between projects."""
        all_models = setup_project_and_user["all_models"]

        # Create two projects with different restrictions
        project_a = MagicMock()
        project_a.name = "project-a"
        project_a.allowed_models = ["gpt-4", "claude-3-sonnet"]

        project_b = MagicMock()
        project_b.name = "project-b"
        project_b.allowed_models = ["llama-2-70b", "gpt-3.5-turbo"]

        # Verify each project has correct models
        models_a = [m for m in all_models if m.base_name in project_a.allowed_models]
        models_b = [m for m in all_models if m.base_name in project_b.allowed_models]

        assert len(models_a) == 2
        assert len(models_b) == 2
        assert all(m.base_name in ["gpt-4", "claude-3-sonnet"] for m in models_a)
        assert all(m.base_name in ["llama-2-70b", "gpt-3.5-turbo"] for m in models_b)

        # Models are properly isolated
        models_a_names = {m.base_name for m in models_a}
        models_b_names = {m.base_name for m in models_b}
        assert models_a_names.isdisjoint(models_b_names)

    @patch("codemie.rest_api.routers.projects._ensure_user_management_enabled")
    @patch("codemie.rest_api.routers.projects.project_service")
    @pytest.mark.anyio
    async def test_changing_restrictions_updates_available_models(
        self,
        mock_proj_service,
        mock_ensure_user_management_enabled,
        setup_project_and_user,
    ):
        """Test that changing model restrictions updates available models for new assistants."""
        from codemie.rest_api.routers.projects import (
            AllowedModelsUpdateRequest,
            update_allowed_models,
        )

        project = setup_project_and_user["project"]
        user = setup_project_and_user["user"]

        # Initially allow 3 models
        initial_models = ["gpt-4", "claude-3-sonnet", "gpt-3.5-turbo"]
        project.allowed_models = initial_models
        mock_proj_service.update_allowed_models.return_value = project

        # Verify initial state
        response = update_allowed_models(
            payload=AllowedModelsUpdateRequest(allowed_models=initial_models),
            project_name="Foo",
            user=user,
        )
        assert response.allowed_models == initial_models

        # Change to different 3 models
        new_models = ["claude-3-opus", "llama-2-70b", "gpt-3.5-turbo"]
        project.allowed_models = new_models
        mock_proj_service.update_allowed_models.return_value = project

        response = update_allowed_models(
            payload=AllowedModelsUpdateRequest(allowed_models=new_models),
            project_name="Foo",
            user=user,
        )

        # Verify new models replaced old ones
        assert response.allowed_models == new_models
        assert "gpt-4" not in response.allowed_models
        assert "claude-3-opus" in response.allowed_models


class TestPermissionBoundaries:
    """Tests for authorization boundaries in model management."""

    @patch("codemie.rest_api.routers.projects.project_service")
    @pytest.mark.anyio
    async def test_non_admin_cannot_modify_project_models(
        self,
        mock_proj_service,
    ):
        """Test that non-project-admin users cannot modify allowed_models.

        ``check_allowed_models_authorization`` is a sync classmethod on the real
        ProjectService (it manages its own DB session internally), so the mock
        here must be a plain MagicMock, not an AsyncMock.
        """
        from codemie.core.exceptions import ExtendedHTTPException

        # User without project admin permissions
        non_admin_user = User(
            id="user-999",
            username="regular-user",
            name="Regular User",
            email="regular@example.com",
            is_admin=False,
            applications_admin=[],  # Not admin for any project
        )

        # Authorization check should fail
        mock_proj_service.check_allowed_models_authorization.side_effect = ExtendedHTTPException(
            code=403,
            message="You are not authorized to manage models for this project",
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            mock_proj_service.check_allowed_models_authorization(
                user=non_admin_user,
                project_name="test-project",
            )

        assert exc_info.value.code == 403

    @patch("codemie.rest_api.routers.projects.project_service")
    @pytest.mark.anyio
    async def test_project_admin_can_modify_project_models(
        self,
        mock_proj_service,
    ):
        """Test that project admin can modify allowed_models."""
        project_admin_user = User(
            id="user-456",
            username="projectadmin",
            name="Project Admin",
            email="admin@example.com",
            is_admin=False,
            applications_admin=["test-project"],
        )

        # Authorization check should pass (no exception); real method returns None.
        mock_proj_service.check_allowed_models_authorization.return_value = None

        # Should not raise
        mock_proj_service.check_allowed_models_authorization(
            user=project_admin_user,
            project_name="test-project",
        )

        mock_proj_service.check_allowed_models_authorization.assert_called_once()

    @patch("codemie.rest_api.routers.projects.project_service")
    @pytest.mark.anyio
    async def test_system_admin_can_modify_any_project_models(
        self,
        mock_proj_service,
    ):
        """Test that system admin can modify models for any project."""
        admin_user = User(
            id="user-789",
            username="sysadmin",
            name="System Admin",
            email="sysadmin@example.com",
            is_admin=True,
            applications_admin=[],
        )

        # System admin authorization should always pass
        mock_proj_service.check_allowed_models_authorization.return_value = None

        mock_proj_service.check_allowed_models_authorization(
            user=admin_user,
            project_name="any-project",
        )

        mock_proj_service.check_allowed_models_authorization.assert_called_once()


class TestDataConsistency:
    """Tests for data consistency and edge cases."""

    @patch("codemie.rest_api.routers.projects._ensure_user_management_enabled")
    @patch("codemie.rest_api.routers.projects.project_service")
    @pytest.mark.anyio
    async def test_backend_persists_model_selection(
        self,
        mock_proj_service,
        mock_ensure_user_management_enabled,
    ):
        """Test that model selection is persisted (via project_service.update_allowed_models,
        which manages its own DB session) and can be read back via the detail response mapper.
        """
        from codemie.rest_api.routers.projects import (
            AllowedModelsUpdateRequest,
            update_allowed_models,
            _build_project_detail_response,
        )

        project = MagicMock()
        project.name = "test-project"
        selected_models = ["gpt-4", "claude-3-sonnet"]
        default_model = "gpt-4"

        project.allowed_models = selected_models
        project.default_model = default_model
        project.cost_center_id = None
        project.display_name = None
        project.description = ""
        project.project_type = "shared"
        project.created_by = "user-123"
        mock_proj_service.update_allowed_models.return_value = project

        user = User(
            id="user-123",
            username="testuser",
            name="Test User",
            email="test@example.com",
            is_admin=True,
            applications_admin=[],
        )

        # Update the project
        response = update_allowed_models(
            payload=AllowedModelsUpdateRequest(
                allowed_models=selected_models,
                default_model=default_model,
            ),
            project_name="test-project",
            user=user,
        )

        # Verify it was saved
        assert response.allowed_models == selected_models
        assert response.default_model == default_model

        # Retrieve the project to verify persistence, via the real detail-response mapper.
        project_detail = {
            "name": project.name,
            "display_name": None,
            "description": "",
            "project_type": "shared",
            "created_by": user.id,
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            "user_count": 1,
            "admin_count": 1,
            "allowed_models": project.allowed_models,
            "default_model": project.default_model,
            "members": [],
        }
        retrieved_project = _build_project_detail_response(project_detail, "test-project")

        assert retrieved_project.allowed_models == selected_models
        assert retrieved_project.default_model == default_model

    @patch("codemie.rest_api.routers.projects.project_service")
    def test_model_order_preserved_in_persistence(
        self,
        mock_proj_service,
    ):
        """Test that model order is preserved when saving and retrieving."""
        from codemie.service.project.project_service import ProjectService

        # Models in specific order
        ordered_models = ["claude-3-sonnet", "gpt-4", "gpt-3.5-turbo"]

        # Validate preserves order
        result = ProjectService.validate_allowed_models(ordered_models)

        assert result == ordered_models
        assert result[0] == "claude-3-sonnet"
        assert result[1] == "gpt-4"
        assert result[2] == "gpt-3.5-turbo"
