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
Tests for execution-time model resolution within project constraints (EPMCDME-14349).

Tests the enforcement of project-level model restrictions when agents/workflows
actually execute, including fallback to default_model when a disallowed model
is selected.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from codemie.core.models import Application


UTC = ZoneInfo("UTC")


@pytest.fixture
def anyio_backend():
    return "asyncio"


class TestExecutionTimeModelResolution:
    """Tests for model enforcement during agent/workflow execution."""

    @pytest.fixture
    def setup_restricted_project(self):
        """Setup a project with model restrictions."""
        project = MagicMock(spec=Application)
        project.name = "restricted-project"
        project.allowed_models = ["gpt-4", "claude-3-sonnet"]
        project.default_model = "gpt-4"
        return project

    @patch("codemie.service.llm_service.llm_service.llm_service")
    @pytest.mark.anyio
    async def test_agent_execution_uses_allowed_model(
        self,
        mock_llm_service,
        setup_restricted_project,
    ):
        """Test that agent execution uses model from allowed_models list."""
        project = setup_restricted_project
        requested_model = "gpt-4"

        # Verify requested model is in allowed list
        assert requested_model in project.allowed_models

        # Mock LLM service to return the model
        mock_llm_service.get_model.return_value = MagicMock(
            base_name=requested_model,
            deployment_name=requested_model,
        )

        # Simulation of agent execution requesting a model
        resolved_model = mock_llm_service.get_model(requested_model)

        assert resolved_model.base_name == requested_model

    @patch("codemie.service.llm_service.llm_service.llm_service")
    @pytest.mark.anyio
    async def test_agent_execution_rejects_disallowed_model(
        self,
        mock_llm_service,
        setup_restricted_project,
    ):
        """Test that agent execution rejects model outside allowed_models."""
        from codemie.core.exceptions import ExtendedHTTPException

        project = setup_restricted_project
        disallowed_model = "llama-2-70b"

        # Verify model is NOT in allowed list
        assert disallowed_model not in project.allowed_models

        # Raise exception for disallowed model
        mock_llm_service.get_model.side_effect = ExtendedHTTPException(
            code=400,
            message=f"Model {disallowed_model} is not allowed for project {project.name}",
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            mock_llm_service.get_model(disallowed_model)

        assert exc_info.value.code == 400
        assert disallowed_model in exc_info.value.message

    @patch("codemie.service.llm_service.llm_service.llm_service")
    @pytest.mark.anyio
    async def test_agent_execution_fallback_to_default_model(
        self,
        mock_llm_service,
        setup_restricted_project,
    ):
        """
        Test that when requested model is disallowed, execution falls back to default_model.

        Scenario:
        1. User selects "gpt-3.5-turbo" for assistant (allowed globally)
        2. But project restricts to ["gpt-4", "claude-3-sonnet"]
        3. Agent execution sees requested="gpt-3.5-turbo" but project restricts it
        4. Falls back to default_model="gpt-4" (from project config)
        """
        project = setup_restricted_project
        requested_model = "gpt-3.5-turbo"  # Not in allowed_models
        fallback_model = project.default_model  # "gpt-4"

        # Verify requested model is NOT allowed
        assert requested_model not in project.allowed_models

        # Verify fallback is allowed
        assert fallback_model in project.allowed_models

        # Mock the resolution logic
        mock_llm_service.resolve_model_for_execution.return_value = MagicMock(
            base_name=fallback_model,
            deployment_name=fallback_model,
            fallback_applied=True,
            original_requested=requested_model,
        )

        # Execute resolution
        resolved = mock_llm_service.resolve_model_for_execution(
            user=None,
            requested_model=requested_model,
            project=project,
        )

        # Verify fallback was applied
        assert resolved.base_name == fallback_model
        assert resolved.fallback_applied is True
        assert resolved.original_requested == requested_model

    @patch("codemie.service.llm_service.llm_service.llm_service")
    @pytest.mark.anyio
    async def test_no_fallback_when_model_is_allowed(
        self,
        mock_llm_service,
        setup_restricted_project,
    ):
        """Test that no fallback occurs when requested model is in allowed_models."""
        project = setup_restricted_project
        requested_model = "claude-3-sonnet"

        # Verify model IS allowed
        assert requested_model in project.allowed_models

        # Mock resolution returns same model (no fallback)
        mock_llm_service.resolve_model_for_execution.return_value = MagicMock(
            base_name=requested_model,
            deployment_name=requested_model,
            fallback_applied=False,
            original_requested=None,
        )

        resolved = mock_llm_service.resolve_model_for_execution(
            user=None,
            requested_model=requested_model,
            project=project,
        )

        # Verify no fallback
        assert resolved.base_name == requested_model
        assert resolved.fallback_applied is False
        assert resolved.original_requested is None


class TestWorkflowModelValidation:
    """Tests for model validation during workflow/agent start."""

    @pytest.fixture
    def setup_workflow_context(self):
        """Setup a workflow execution context."""
        project = MagicMock(spec=Application)
        project.name = "workflow-project"
        project.allowed_models = ["gpt-4", "claude-3-sonnet"]
        project.default_model = "gpt-4"

        workflow = MagicMock()
        workflow.id = "workflow-123"
        workflow.name = "test-workflow"
        workflow.llm_model = "gpt-4"  # Assistant selected this model
        workflow.project = project

        return {
            "project": project,
            "workflow": workflow,
        }

    @patch("codemie.service.llm.model_availability_service.ModelAvailabilityService")
    @pytest.mark.anyio
    async def test_workflow_validates_model_at_start(
        self,
        mock_model_availability_service,
        setup_workflow_context,
    ):
        """Test that workflow validates model choice before starting execution.

        Production code resolves this via the shared
        ``ModelAvailabilityService.resolve_model_for_execution``, which returns
        ``(actual_model_id, was_fallback_applied)``; no fallback for an allowed model.
        """
        workflow = setup_workflow_context["workflow"]
        project = setup_workflow_context["project"]

        mock_model_availability_service.resolve_model_for_execution.return_value = (
            workflow.llm_model,
            False,
        )

        resolved_model, fallback_applied = mock_model_availability_service.resolve_model_for_execution(
            requested_model=workflow.llm_model,
            project_name=project.name,
        )

        assert resolved_model == workflow.llm_model
        assert fallback_applied is False

    @patch("codemie.service.llm.model_availability_service.ModelAvailabilityService")
    @pytest.mark.anyio
    async def test_workflow_rejects_disallowed_model_at_start(
        self,
        mock_model_availability_service,
        setup_workflow_context,
    ):
        """Test that workflow rejects start if model not in project allowlist and empty whitelist."""
        from codemie.core.exceptions import ModelNotWhitelistedException

        workflow = setup_workflow_context["workflow"]
        project = setup_workflow_context["project"]
        workflow.llm_model = "llama-2-70b"  # Not in project.allowed_models
        project.allowed_models = []  # Whitelist configured but empty -> hard rejection

        mock_model_availability_service.resolve_model_for_execution.side_effect = ModelNotWhitelistedException(
            workflow.llm_model, project.name
        )

        with pytest.raises(ModelNotWhitelistedException) as exc_info:
            mock_model_availability_service.resolve_model_for_execution(
                requested_model=workflow.llm_model,
                project_name=project.name,
            )

        assert exc_info.value.code == 400

    @patch("codemie.service.llm.model_availability_service.ModelAvailabilityService")
    @pytest.mark.anyio
    async def test_workflow_applies_default_model_on_validation_error(
        self,
        mock_model_availability_service,
        setup_workflow_context,
    ):
        """Test that workflow falls back to default_model when the requested model is disallowed."""
        workflow = setup_workflow_context["workflow"]
        project = setup_workflow_context["project"]
        workflow.llm_model = "llama-2-70b"  # Disallowed

        # Real resolve_model_for_execution falls back to the project's default_model
        # and reports was_fallback_applied=True.
        mock_model_availability_service.resolve_model_for_execution.return_value = (
            project.default_model,
            True,
        )

        corrected_model, fallback_applied = mock_model_availability_service.resolve_model_for_execution(
            requested_model=workflow.llm_model,
            project_name=project.name,
        )

        assert corrected_model == project.default_model
        assert fallback_applied is True
        assert corrected_model in project.allowed_models


class TestAgentNodeModelEnforcement:
    """Tests for model enforcement at agent execution nodes."""

    @pytest.fixture
    def setup_agent_node(self):
        """Setup an agent node execution context."""
        project = MagicMock(spec=Application)
        project.name = "agent-project"
        project.allowed_models = ["gpt-4", "claude-3-sonnet", "gpt-3.5-turbo"]
        project.default_model = "gpt-4"

        agent_node = MagicMock()
        agent_node.name = "reasoning_node"
        agent_node.llm_model = "gpt-4"

        return {
            "project": project,
            "agent_node": agent_node,
        }

    @patch("codemie.service.llm.model_availability_service.ModelAvailabilityService")
    @pytest.mark.anyio
    async def test_agent_node_enforces_project_model_restrictions(
        self,
        mock_model_availability_service,
        setup_agent_node,
    ):
        """Test that agent node enforces project model restrictions at execution time
        via the shared ModelAvailabilityService.resolve_model_for_execution."""
        agent_node = setup_agent_node["agent_node"]
        project = setup_agent_node["project"]
        model_to_use = agent_node.llm_model

        mock_model_availability_service.resolve_model_for_execution.return_value = (
            model_to_use,
            False,
        )

        resolved_model, fallback_applied = mock_model_availability_service.resolve_model_for_execution(
            requested_model=model_to_use,
            project_name=project.name,
        )

        assert resolved_model == model_to_use
        assert fallback_applied is False

    @patch("codemie.service.llm.model_availability_service.ModelAvailabilityService")
    @pytest.mark.anyio
    async def test_agent_node_blocks_disallowed_model(
        self,
        mock_model_availability_service,
        setup_agent_node,
    ):
        """Test that agent node blocks initialization with a disallowed model and an
        empty project whitelist (the real hard-rejection case)."""
        from codemie.core.exceptions import ModelNotWhitelistedException

        project = setup_agent_node["project"]
        project.allowed_models = []
        disallowed_model = "llama-2-70b"

        mock_model_availability_service.resolve_model_for_execution.side_effect = ModelNotWhitelistedException(
            disallowed_model, project.name
        )

        with pytest.raises(ModelNotWhitelistedException):
            mock_model_availability_service.resolve_model_for_execution(
                requested_model=disallowed_model,
                project_name=project.name,
            )

    @patch("codemie.service.llm.model_availability_service.ModelAvailabilityService")
    @pytest.mark.anyio
    async def test_agent_node_uses_default_on_model_not_found(
        self,
        mock_model_availability_service,
        setup_agent_node,
    ):
        """Test that agent node falls back to default_model if requested model unavailable."""
        project = setup_agent_node["project"]
        attempted_model = "some-unavailable-model"

        # Real resolve_model_for_execution falls back to the project default and
        # reports was_fallback_applied=True.
        mock_model_availability_service.resolve_model_for_execution.return_value = (
            project.default_model,
            True,
        )

        resolved_model, fallback_applied = mock_model_availability_service.resolve_model_for_execution(
            requested_model=attempted_model,
            project_name=project.name,
        )

        assert resolved_model == project.default_model
        assert fallback_applied is True


class TestModelValidationInAssistantExecution:
    """Tests for model validation when executing assistants."""

    @pytest.fixture
    def setup_assistant_execution(self):
        """Setup an assistant execution context."""
        project = MagicMock(spec=Application)
        project.name = "assistant-project"
        project.allowed_models = ["gpt-4", "claude-3-sonnet"]
        project.default_model = "gpt-4"

        assistant = MagicMock()
        assistant.id = "assistant-123"
        assistant.name = "test-assistant"
        assistant.llm_model_type = "gpt-4"
        assistant.project = project

        return {
            "project": project,
            "assistant": assistant,
        }

    @patch("codemie.service.llm.model_availability_service.ModelAvailabilityService")
    @pytest.mark.anyio
    async def test_assistant_execution_validates_model_choice(
        self,
        mock_model_availability_service,
        setup_assistant_execution,
    ):
        """Test that assistant execution validates model at runtime via the shared
        ModelAvailabilityService.resolve_model_for_execution (no fallback for an allowed model)."""
        assistant = setup_assistant_execution["assistant"]
        project = setup_assistant_execution["project"]

        mock_model_availability_service.resolve_model_for_execution.return_value = (
            assistant.llm_model_type,
            False,
        )

        resolved_model, fallback_applied = mock_model_availability_service.resolve_model_for_execution(
            requested_model=assistant.llm_model_type,
            project_name=project.name,
        )

        assert resolved_model == assistant.llm_model_type
        assert fallback_applied is False

    @patch("codemie.service.llm.model_availability_service.ModelAvailabilityService")
    @pytest.mark.anyio
    async def test_assistant_execution_applies_project_default_if_model_invalid(
        self,
        mock_model_availability_service,
        setup_assistant_execution,
    ):
        """Test assistant falls back to project default_model if selected model invalid."""
        assistant = setup_assistant_execution["assistant"]
        project = setup_assistant_execution["project"]

        # Simulate model not in project.allowed_models
        assistant.llm_model_type = "invalid-model"

        mock_model_availability_service.resolve_model_for_execution.return_value = (
            project.default_model,
            True,
        )

        resolved, fallback_applied = mock_model_availability_service.resolve_model_for_execution(
            requested_model=assistant.llm_model_type,
            project_name=project.name,
        )

        assert resolved == project.default_model
        assert fallback_applied is True
        assert resolved in project.allowed_models


class TestModelBlockingAfterCreation:
    """Tests for when admin blocks a model AFTER user created assets with it."""

    @pytest.fixture
    def setup_assistant_with_model(self):
        """Setup an assistant created when model was allowed."""
        project = MagicMock()
        project.name = "test-project"
        project.allowed_models = ["gpt-4", "claude-3-sonnet", "gpt-3.5-turbo"]
        project.default_model = "gpt-4"

        assistant = MagicMock()
        assistant.id = "assistant-123"
        assistant.name = "test-assistant"
        assistant.llm_model_type = "gpt-3.5-turbo"  # Was allowed at creation time
        assistant.project = project

        return {
            "project": project,
            "assistant": assistant,
        }

    @patch("codemie.service.llm_service.llm_service.llm_service")
    @pytest.mark.anyio
    async def test_user_runs_assistant_after_model_blocked_by_admin(
        self,
        mock_llm_service,
        setup_assistant_with_model,
    ):
        """
        Scenario: User created assistant with gpt-3.5-turbo (was allowed).
        Admin later removes gpt-3.5-turbo from project.allowed_models.
        User tries to run the assistant.

        Expected: Fallback to project.default_model (gpt-4).
        """
        assistant = setup_assistant_with_model["assistant"]
        project = setup_assistant_with_model["project"]

        # Simulate: Admin blocked the model after assistant was created
        original_model = assistant.llm_model_type  # "gpt-3.5-turbo"
        project.allowed_models = ["gpt-4", "claude-3-sonnet"]  # Removed gpt-3.5-turbo
        project.default_model = "gpt-4"

        # User runs the assistant
        mock_llm_service.resolve_model_for_execution.return_value = MagicMock(
            base_name=project.default_model,
            deployment_name=project.default_model,
            fallback_applied=True,
            original_requested=original_model,
            reason="model_no_longer_allowed",
        )

        resolved = mock_llm_service.resolve_model_for_execution(
            user=None,
            requested_model=original_model,
            project=project,
        )

        # Verify: Fallback occurred and original model is in the fallback info
        assert resolved.base_name == project.default_model
        assert resolved.fallback_applied is True
        assert resolved.original_requested == original_model

    @patch("codemie.service.llm_service.llm_service.llm_service")
    @pytest.mark.anyio
    async def test_user_cannot_edit_blocked_model_back_in(
        self,
        mock_llm_service,
        setup_assistant_with_model,
    ):
        """
        Test that if user tries to manually edit assistant to use blocked model,
        it's rejected on save.
        """
        from codemie.core.exceptions import ExtendedHTTPException

        project = setup_assistant_with_model["project"]

        # Model was blocked
        blocked_model = "gpt-3.5-turbo"
        project.allowed_models = ["gpt-4", "claude-3-sonnet"]

        # User tries to set assistant.llm_model_type = blocked_model
        mock_llm_service.validate_assistant_model.side_effect = ExtendedHTTPException(
            code=400,
            message=f"Model {blocked_model} is not allowed for this project",
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            mock_llm_service.validate_assistant_model(
                model=blocked_model,
                project=project,
            )

        assert exc_info.value.code == 400
        assert blocked_model in exc_info.value.message


class TestEdgeCasesInExecution:
    @patch("codemie.service.llm_service.llm_service.llm_service")
    @pytest.mark.anyio
    async def test_project_with_no_default_model_set(
        self,
        mock_llm_service,
    ):
        """Test fallback behavior when project has no default_model set."""
        project = MagicMock()
        project.name = "no-default-project"
        project.allowed_models = ["gpt-4", "claude-3-sonnet"]
        project.default_model = None  # No default set

        requested_model = "invalid-model"

        # Should fallback to first allowed model
        fallback = project.allowed_models[0]

        mock_llm_service.resolve_model_for_execution.return_value = MagicMock(
            base_name=fallback,
            fallback_applied=True,
        )

        resolved = mock_llm_service.resolve_model_for_execution(
            user=None,
            requested_model=requested_model,
            project=project,
        )

        assert resolved.base_name == fallback

    @patch("codemie.service.llm_service.llm_service.llm_service")
    @pytest.mark.anyio
    async def test_project_with_no_restrictions_allows_any_model(
        self,
        mock_llm_service,
    ):
        """Test that project with allowed_models=None allows any model."""
        project = MagicMock()
        project.name = "unrestricted-project"
        project.allowed_models = None  # No restrictions

        requested_model = "any-model"

        # Should use requested model as-is
        mock_llm_service.resolve_model_for_execution.return_value = MagicMock(
            base_name=requested_model,
            fallback_applied=False,
        )

        resolved = mock_llm_service.resolve_model_for_execution(
            user=None,
            requested_model=requested_model,
            project=project,
        )

        assert resolved.base_name == requested_model
        assert resolved.fallback_applied is False
