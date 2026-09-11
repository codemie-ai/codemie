# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

import json
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from codemie.core.exceptions import ExtendedHTTPException, MCPAuthenticationRequiredException
from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.guardrail import GuardrailEntity, GuardrailSource
from codemie.rest_api.routers.assistant import _ask_assistant, _ask_virtual_assistant
from codemie.rest_api.security.user import User


@pytest.fixture
def mock_user():
    """Create a mock user for testing."""
    user = MagicMock(spec=User)
    user.id = "user-123"
    user.username = "testuser"
    user.name = "Test User"
    user.is_admin = False
    user.admin_project_names = ["test-project"]
    return user


@pytest.fixture
def mock_assistant():
    """Create a mock assistant for testing."""
    assistant = MagicMock(spec=Assistant)
    assistant.id = "assistant-123"
    assistant.project = "test-project"
    assistant.name = "Test Assistant"
    assistant.description = "Test Description"
    assistant.system_prompt = "Test Prompt"
    assistant.toolkits = []
    return assistant


class TestAskAssistantWithGuardrails:
    """Tests for _ask_assistant method with guardrail functionality."""

    @patch("codemie.rest_api.routers.assistant.assistant_user_interaction_service.record_usage")
    @patch("codemie.rest_api.routers.assistant.request_summary_manager.create_request_summary")
    @patch("codemie.rest_api.routers.assistant.Ability")
    @patch("codemie.rest_api.routers.assistant.GuardrailService.apply_guardrails_for_entity")
    @patch("codemie.rest_api.routers.assistant.get_request_handler")
    def test_applies_guardrails_to_input_text(
        self,
        mock_get_handler,
        mock_apply_guardrails,
        mock_ability,
        mock_request_summary,
        mock_record_usage,
        mock_user,
        mock_assistant,
    ):
        """Test that guardrails are applied to user input before processing."""
        mock_assistant.id = "assistant-123"
        mock_assistant.project = "test-project"

        # Mock ability check
        mock_ability_instance = MagicMock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance

        # Mock guardrail service to return modified text
        mock_apply_guardrails.return_value = ("Modified input text", None)

        # Mock request handler
        mock_handler = MagicMock()
        mock_handler.process_request.return_value = {"response": "success"}
        mock_get_handler.return_value = mock_handler

        # Create request with text
        request = AssistantChatRequest(
            text="Original input text",
            workflow_execution_id=None,
            version=None,
            sub_assistants_versions={},
        )
        raw_request = MagicMock()
        raw_request.state.uuid = "test-uuid"
        background_tasks = MagicMock()

        _ask_assistant(mock_assistant, raw_request, request, mock_user, background_tasks)

        # Verify guardrail was called with correct parameters
        mock_apply_guardrails.assert_called_once_with(
            GuardrailEntity.ASSISTANT,
            "assistant-123",
            "test-project",
            "Original input text",
            GuardrailSource.INPUT,
        )

        # Verify request text was modified
        assert request.text == "Modified input text"

        # Verify processing continued with modified text
        mock_handler.process_request.assert_called_once()

    @patch("codemie.rest_api.routers.assistant.Ability")
    @patch("codemie.rest_api.routers.assistant.GuardrailService.apply_guardrails_for_entity")
    def test_raises_exception_when_guardrails_block_content(
        self, mock_apply_guardrails, mock_ability, mock_user, mock_assistant
    ):
        """Test that blocked content raises ExtendedHTTPException."""
        mock_assistant.id = "assistant-123"
        mock_assistant.project = "test-project"

        # Mock ability check
        mock_ability_instance = MagicMock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance

        # Mock guardrail service to return blocked content
        blocked_reasons = [
            {"policy": "contentPolicy", "type": "HATE", "reason": "BLOCKED"},
            {"policy": "topicPolicy", "type": "POLITICS", "reason": "BLOCKED"},
        ]
        mock_apply_guardrails.return_value = ("BLOCKED", blocked_reasons)

        request = AssistantChatRequest(text="Blocked input text")
        raw_request = MagicMock()
        raw_request.state.uuid = "test-uuid"
        background_tasks = MagicMock()

        with pytest.raises(ExtendedHTTPException) as exc_info:
            _ask_assistant(mock_assistant, raw_request, request, mock_user, background_tasks)

        assert exc_info.value.code == 422
        assert "Request blocked by guardrails" in exc_info.value.message
        assert "HATE" in exc_info.value.details
        assert "POLITICS" in exc_info.value.details

    @patch("codemie.rest_api.routers.assistant.assistant_user_interaction_service.record_usage")
    @patch("codemie.rest_api.routers.assistant.request_summary_manager.create_request_summary")
    @patch("codemie.rest_api.routers.assistant.Ability")
    @patch("codemie.rest_api.routers.assistant.GuardrailService.apply_guardrails_for_entity")
    @patch("codemie.rest_api.routers.assistant.get_request_handler")
    def test_skips_guardrails_when_no_text(
        self,
        mock_get_handler,
        mock_apply_guardrails,
        mock_ability,
        mock_request_summary,
        mock_record_usage,
        mock_user,
        mock_assistant,
    ):
        """Test that guardrails are skipped when request has no text."""
        mock_assistant.id = "assistant-123"

        # Mock ability check
        mock_ability_instance = MagicMock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance

        mock_handler = MagicMock()
        mock_handler.process_request.return_value = {"response": "success"}
        mock_get_handler.return_value = mock_handler

        # Request without text
        request = AssistantChatRequest(text=None)
        raw_request = MagicMock()
        raw_request.state.uuid = "test-uuid"
        background_tasks = MagicMock()

        _ask_assistant(mock_assistant, raw_request, request, mock_user, background_tasks)

        # Guardrails should not be called
        mock_apply_guardrails.assert_not_called()

    @patch("codemie.rest_api.routers.assistant.assistant_user_interaction_service.record_usage")
    @patch("codemie.rest_api.routers.assistant.request_summary_manager.create_request_summary")
    @patch("codemie.rest_api.routers.assistant.Ability")
    @patch("codemie.rest_api.routers.assistant.GuardrailService.apply_guardrails_for_entity")
    @patch("codemie.rest_api.routers.assistant.get_request_handler")
    def test_skips_guardrails_when_no_assistant_id(
        self,
        mock_get_handler,
        mock_apply_guardrails,
        mock_ability,
        mock_request_summary,
        mock_record_usage,
        mock_user,
        mock_assistant,
    ):
        """Test that guardrails are skipped when assistant has no ID."""
        mock_assistant.id = None

        # Mock ability check
        mock_ability_instance = MagicMock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance

        mock_handler = MagicMock()
        mock_handler.process_request.return_value = {"response": "success"}
        mock_get_handler.return_value = mock_handler

        request = AssistantChatRequest(text="Some text")
        raw_request = MagicMock()
        raw_request.state.uuid = "test-uuid"
        background_tasks = MagicMock()

        _ask_assistant(mock_assistant, raw_request, request, mock_user, background_tasks)

        # Guardrails should not be called
        mock_apply_guardrails.assert_not_called()

    @patch("codemie.rest_api.routers.assistant.Ability")
    @patch("codemie.rest_api.routers.assistant.GuardrailService.apply_guardrails_for_entity")
    @patch("codemie.rest_api.routers.assistant.get_request_handler")
    def test_deduplicates_blocked_reasons(
        self, mock_get_handler, mock_apply_guardrails, mock_ability, mock_user, mock_assistant
    ):
        """Test that duplicate blocked reasons are deduplicated."""
        mock_assistant.id = "assistant-123"
        mock_assistant.project = "test-project"

        # Mock ability check
        mock_ability_instance = MagicMock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance

        # Mock duplicate blocked reasons
        blocked_reasons = [
            {"policy": "contentPolicy", "type": "HATE"},
            {"policy": "contentPolicy", "type": "HATE"},  # Duplicate
            {"policy": "topicPolicy", "type": "POLITICS"},
        ]
        mock_apply_guardrails.return_value = ("BLOCKED", blocked_reasons)

        request = AssistantChatRequest(text="Blocked text")
        raw_request = MagicMock()
        raw_request.state.uuid = "test-uuid"
        background_tasks = MagicMock()

        with pytest.raises(ExtendedHTTPException) as exc_info:
            _ask_assistant(mock_assistant, raw_request, request, mock_user, background_tasks)

        # Check that details only contain unique reasons
        details_str = exc_info.value.details
        assert details_str.count("HATE") == 1  # Should appear only once despite duplicate
        assert "POLITICS" in details_str

    @patch("codemie.rest_api.routers.assistant._save_error")
    @patch("codemie.rest_api.routers.assistant.request_summary_manager.create_request_summary")
    @patch("codemie.rest_api.routers.assistant.assistant_user_interaction_service.record_usage")
    @patch("codemie.rest_api.routers.assistant.get_request_handler")
    def test_reraises_mcp_authentication_required_without_wrapping(
        self,
        mock_get_handler,
        mock_record_usage,
        mock_request_summary,
        mock_save_error,
        mock_user,
        mock_assistant,
    ):
        auth_payload = {
            "error": "authentication_required",
            "servers": [
                {
                    "mcp_config_id": "mcp-1",
                    "mcp_config_name": "GitHub",
                    "mcp_server_name": "GitHub",
                    "auth_config_id": "auth-1",
                    "auth_type": "oauth2",
                    "as_hostname": "login.example.com",
                    "status": "authentication_required",
                    "error_context": None,
                    "initiate_url": "/v1/mcp-auth/oauth2/initiate",
                }
            ],
        }
        auth_error = MCPAuthenticationRequiredException(auth_payload)
        mock_handler = MagicMock()
        mock_handler.process_request.side_effect = auth_error
        mock_get_handler.return_value = mock_handler

        request = AssistantChatRequest(text=None)
        raw_request = MagicMock()
        raw_request.state.uuid = "test-uuid"
        background_tasks = MagicMock()

        with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
            _ask_assistant(mock_assistant, raw_request, request, mock_user, background_tasks)

        assert exc_info.value.payload == auth_payload
        mock_save_error.assert_not_called()

    @patch("codemie.rest_api.routers.assistant._save_error")
    @patch("codemie.rest_api.routers.assistant.request_summary_manager.create_request_summary")
    @patch("codemie.rest_api.routers.assistant._validate_assistant_supports_model_change_and_raise")
    @patch("codemie.rest_api.routers.assistant._validate_assistant_supports_files_and_raise")
    @patch("codemie.rest_api.routers.assistant._validate_remote_entities_and_raise")
    @patch("codemie.rest_api.routers.assistant.get_request_handler")
    def test_virtual_assistant_reraises_mcp_authentication_required_without_wrapping(
        self,
        mock_get_handler,
        mock_validate_remote,
        mock_validate_files,
        mock_validate_model,
        mock_request_summary,
        mock_save_error,
        mock_user,
        mock_assistant,
    ):
        auth_payload = {
            "error": "authentication_required",
            "servers": [
                {
                    "mcp_config_id": "mcp-1",
                    "mcp_config_name": "OneHub",
                    "auth_type": "oauth2",
                    "status": "authentication_required",
                    "error": "insufficient_scope",
                    "recovery_flow_id": "rf-story-7-1",
                    "initiate_url": "/v1/mcp-auth/oauth2/initiate?recovery_flow_id=rf-story-7-1",
                }
            ],
        }
        auth_error = MCPAuthenticationRequiredException(auth_payload)
        mock_handler = MagicMock()
        mock_handler.process_request.side_effect = auth_error
        mock_get_handler.return_value = mock_handler
        mock_assistant.id = None

        request = AssistantChatRequest(text="run")
        raw_request = MagicMock()
        raw_request.state.uuid = "test-uuid"
        background_tasks = MagicMock()

        with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
            _ask_virtual_assistant(mock_assistant, raw_request, request, mock_user, background_tasks)

        assert exc_info.value.payload == auth_payload
        mock_validate_remote.assert_called_once_with(mock_assistant)
        mock_validate_files.assert_called_once_with(mock_assistant, request.file_names)
        mock_validate_model.assert_called_once_with(mock_assistant, request.llm_model)
        mock_save_error.assert_not_called()


class TestPrepareAssistantForExecutionWithSkills:
    """Tests for _prepare_assistant_for_execution with runtime skill_ids."""

    def test_runtime_skill_ids_do_not_modify_original_assistant(self):
        """
        Test that runtime skill_ids from request are not persisted to the original assistant object.

        This verifies the fix for the bug where skill_ids provided in the request body
        were being saved to the database when they should only be applied during runtime.
        """
        from codemie.rest_api.routers.assistant import _prepare_assistant_for_execution

        # Create an assistant with initial skill_ids
        original_assistant = MagicMock(spec=Assistant)
        original_assistant.id = "assistant-123"
        original_assistant.skill_ids = ["skill-1", "skill-2"]
        original_assistant.version_count = 1
        original_assistant.model_dump = MagicMock(
            return_value={
                "id": "assistant-123",
                "name": "Test Assistant",
                "description": "Test",
                "system_prompt": "Test prompt",
                "project": "test-project",
                "skill_ids": ["skill-1", "skill-2"],
                "toolkits": [],
                "context": [],
                "mcp_servers": [],
                "assistant_ids": [],
                "conversation_starters": [],
                "version_count": 1,
            }
        )

        # Create a request with additional runtime skill_ids
        request = AssistantChatRequest(
            text="Test request",
            skill_ids=["skill-3", "skill-4"],  # Runtime skills that should NOT be persisted
        )

        # Call the function
        execution_assistant = _prepare_assistant_for_execution(original_assistant, request)

        # Verify that the execution assistant has merged skill_ids
        assert "skill-1" in execution_assistant.skill_ids
        assert "skill-2" in execution_assistant.skill_ids
        assert "skill-3" in execution_assistant.skill_ids
        assert "skill-4" in execution_assistant.skill_ids

        # IMPORTANT: Verify that the original assistant object was NOT modified
        # This is the key check - the original object should still have only the original skills
        assert original_assistant.skill_ids == ["skill-1", "skill-2"]
        assert "skill-3" not in original_assistant.skill_ids
        assert "skill-4" not in original_assistant.skill_ids

        # Verify that execution_assistant is a different object
        assert execution_assistant is not original_assistant

    def test_no_skill_ids_in_request_preserves_original(self):
        """Test that when no skill_ids are in the request, the original assistant is used."""
        from codemie.rest_api.routers.assistant import _prepare_assistant_for_execution

        original_assistant = MagicMock(spec=Assistant)
        original_assistant.id = "assistant-123"
        original_assistant.skill_ids = ["skill-1", "skill-2"]
        original_assistant.version_count = 1

        # Create a request without skill_ids
        request = AssistantChatRequest(text="Test request", skill_ids=None)

        execution_assistant = _prepare_assistant_for_execution(original_assistant, request)

        # When no skill_ids are in the request, the original object should be returned
        assert execution_assistant is original_assistant
        assert execution_assistant.skill_ids == ["skill-1", "skill-2"]

    @patch("codemie.rest_api.routers.assistant.AssistantVersionService.apply_version_to_assistant")
    def test_version_request_with_skill_ids_creates_copy(self, mock_apply_version):
        """Test that when a version is requested AND skill_ids are provided, a copy is created."""
        from codemie.rest_api.routers.assistant import _prepare_assistant_for_execution

        original_assistant = MagicMock(spec=Assistant)
        original_assistant.id = "assistant-123"
        original_assistant.skill_ids = ["skill-1"]

        # Mock the version service to return a new assistant instance
        versioned_assistant = MagicMock(spec=Assistant)
        versioned_assistant.id = "assistant-123"
        versioned_assistant.skill_ids = ["skill-1"]
        versioned_assistant.version = 2
        versioned_assistant.model_dump = MagicMock(
            return_value={
                "id": "assistant-123",
                "name": "Test Assistant",
                "description": "Test",
                "system_prompt": "Test prompt",
                "project": "test-project",
                "skill_ids": ["skill-1"],
                "toolkits": [],
                "context": [],
                "mcp_servers": [],
                "assistant_ids": [],
                "conversation_starters": [],
                "version": 2,
            }
        )
        mock_apply_version.return_value = versioned_assistant

        # Create a request with version and skill_ids
        request = AssistantChatRequest(text="Test request", version=2, skill_ids=["skill-2", "skill-3"])

        execution_assistant = _prepare_assistant_for_execution(original_assistant, request)

        # Verify version service was called
        mock_apply_version.assert_called_once_with(original_assistant, 2)

        # Verify that execution_assistant has merged skill_ids
        assert "skill-1" in execution_assistant.skill_ids
        assert "skill-2" in execution_assistant.skill_ids
        assert "skill-3" in execution_assistant.skill_ids

        # Verify neither the original nor the versioned assistant were modified
        assert original_assistant.skill_ids == ["skill-1"]
        assert versioned_assistant.skill_ids == ["skill-1"]

        # Verify execution_assistant is a different object from both
        assert execution_assistant is not original_assistant
        assert execution_assistant is not versioned_assistant


class TestCreateAssistantSlug:
    """Tests for slug resolution in create_assistant.

    Rule: a client-provided slug is kept verbatim and validated (a per-project
    collision must surface a clear error instead of being silently suffixed); a
    slug the client did NOT provide is derived from the name and auto-resolved
    against collisions with a random suffix.
    """

    def _build_request(self, slug=None, name="Knowledge Companion"):
        from codemie.rest_api.models.assistant import AssistantRequest

        return AssistantRequest(
            name=name,
            description="Test Description",
            system_prompt="Test Prompt",
            project="demo",
            slug=slug,
            llm_model_type="gpt-4o",
            skip_integration_validation=True,
        )

    def _patches(self):
        """Patch the create_assistant collaborators around the slug branch."""
        return [
            patch("codemie.rest_api.routers.assistant.project_access_check"),
            patch("codemie.rest_api.routers.assistant.ensure_application_exists"),
            patch("codemie.service.assistant.assistant_version_service.AssistantVersionService.create_initial_version"),
            patch("codemie.rest_api.routers.assistant.GuardrailService.sync_guardrail_assignments_for_entity"),
            patch("codemie.rest_api.routers.assistant._track_mcp_usage_on_create"),
            patch("codemie.rest_api.routers.assistant._track_assistant_management_metric"),
        ]

    def _run_create(self, request, save_side_effect=None):
        """Invoke create_assistant with collaborators patched; capture the saved slug."""
        from codemie.rest_api.routers.assistant import create_assistant

        captured = {}

        def fake_save(self, *args, **kwargs):
            captured["slug"] = self.slug
            if save_side_effect:
                save_side_effect(self)
            return MagicMock()

        user = MagicMock(spec=User)
        user.id = "user-123"
        user.username = "testuser"
        user.name = "Test User"

        ctx_managers = self._patches()
        started = [cm.start() for cm in ctx_managers]
        try:
            with patch.object(Assistant, "save", new=fake_save):
                response = create_assistant(request, user=user)
            return response, captured, started
        finally:
            for cm in ctx_managers:
                cm.stop()

    @patch("codemie.rest_api.routers.assistant.AssistantService.ensure_unique_slug")
    def test_provided_slug_kept_and_not_suffixed(self, mock_ensure):
        # Client supplied a slug -> kept verbatim, ensure_unique_slug must NOT run.
        request = self._build_request(slug="my-custom-slug")

        _response, captured, _ = self._run_create(request)

        assert captured["slug"] == "my-custom-slug"
        mock_ensure.assert_not_called()

    def test_provided_slug_collision_surfaces_error(self):
        # A provided slug that collides must fail validation (clear 400) — never suffixed.
        request = self._build_request(slug="taken-slug")

        def raise_validation(_assistant):
            raise ValueError(
                "Slug 'taken-slug' is already used by another assistant in this project. "
                "Slugs must be unique within a project — please choose a different slug."
            )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            self._run_create(request, save_side_effect=raise_validation)

        assert exc_info.value.code == 400
        assert "taken-slug" in exc_info.value.details

    @patch("codemie.rest_api.routers.assistant.AssistantService.ensure_unique_slug")
    def test_derived_slug_auto_suffixed_on_collision(self, mock_ensure):
        # No slug provided -> derived from name and run through ensure_unique_slug.
        mock_ensure.return_value = "knowledge-companion_ab12"
        request = self._build_request(slug=None, name="Knowledge Companion")

        _response, captured, _ = self._run_create(request)

        mock_ensure.assert_called_once_with("knowledge-companion", "demo")
        assert captured["slug"] == "knowledge-companion_ab12"

    @patch("codemie.rest_api.routers.assistant.AssistantService.ensure_unique_slug")
    def test_no_slug_and_empty_derivation_yields_null(self, mock_ensure):
        # Name without slug-eligible characters -> slug stays NULL, ensure_unique_slug skipped.
        request = self._build_request(slug=None, name="???")

        _response, captured, _ = self._run_create(request)

        assert captured["slug"] is None
        mock_ensure.assert_not_called()

    def test_concurrent_create_integrity_error_maps_to_400(self):
        # Concurrency backstop: two simultaneous creates can both pass the ES uniqueness read and
        # collide only at COMMIT on uq_assistants_project_slug. That IntegrityError must surface as
        # a clean 400 (with the slug), not a raw psycopg2 constraint error.
        request = self._build_request(slug="taken-slug")

        def raise_integrity(_assistant):
            raise IntegrityError(
                "INSERT INTO assistants ...",
                {},
                Exception('duplicate key value violates unique constraint "uq_assistants_project_slug"'),
            )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            self._run_create(request, save_side_effect=raise_integrity)

        assert exc_info.value.code == 400
        assert "taken-slug" in exc_info.value.details

    def test_unrelated_integrity_error_is_not_swallowed_as_slug_conflict(self):
        # A different constraint violation must keep the generic save-error handling, not be
        # mislabeled as a slug conflict.
        request = self._build_request(slug="ok-slug")

        def raise_other_integrity(_assistant):
            raise IntegrityError(
                "INSERT INTO assistants ...",
                {},
                Exception('violates unique constraint "uq_assistants_bedrock_settings"'),
            )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            self._run_create(request, save_side_effect=raise_other_integrity)

        assert "Slug conflict" not in (exc_info.value.message or "")


class TestCreateAssistantCloneTracking:
    """Tests for clone_count tracking wired into create_assistant via source_assistant_id."""

    def _build_request(self, source_assistant_id=None, name="Cloned Assistant"):
        from codemie.rest_api.models.assistant import AssistantRequest

        return AssistantRequest(
            name=name,
            description="Test Description",
            system_prompt="Test Prompt",
            project="demo",
            llm_model_type="gpt-4o",
            skip_integration_validation=True,
            source_assistant_id=source_assistant_id,
        )

    def _patches(self):
        return [
            patch("codemie.rest_api.routers.assistant.project_access_check"),
            patch("codemie.rest_api.routers.assistant.ensure_application_exists"),
            patch("codemie.service.assistant.assistant_version_service.AssistantVersionService.create_initial_version"),
            patch("codemie.rest_api.routers.assistant.GuardrailService.sync_guardrail_assignments_for_entity"),
            patch("codemie.rest_api.routers.assistant._track_mcp_usage_on_create"),
            patch("codemie.rest_api.routers.assistant._track_assistant_management_metric"),
        ]

    def _run_create(self, request):
        from codemie.rest_api.routers.assistant import create_assistant

        def fake_save(self, *args, **kwargs):
            return MagicMock()

        user = MagicMock(spec=User)
        user.id = "user-123"
        user.username = "testuser"
        user.name = "Test User"

        ctx_managers = self._patches()
        for cm in ctx_managers:
            cm.start()
        try:
            with patch.object(Assistant, "save", new=fake_save):
                response = create_assistant(request, user=user)
            return response
        finally:
            for cm in ctx_managers:
                cm.stop()

    @patch("codemie.rest_api.routers.assistant.AssistantRepository")
    @patch("codemie.rest_api.routers.assistant.Assistant.find_by_id")
    @patch("codemie.rest_api.routers.assistant.AssistantCloneEventRepository")
    def test_source_assistant_id_logs_event_and_updates_clone_count(
        self, mock_clone_event_repo_class, mock_find_by_id, mock_assistant_repo_class
    ):
        mock_clone_event_repo = mock_clone_event_repo_class.return_value
        mock_source_assistant = MagicMock(spec=Assistant)
        mock_find_by_id.return_value = mock_source_assistant

        request = self._build_request(source_assistant_id="source-id-1")
        response = self._run_create(request)

        mock_clone_event_repo.log_clone_event.assert_called_once_with("source-id-1", "user-123")
        mock_assistant_repo_class.update_clone_count.assert_called_once_with("source-id-1")
        assert response.assistant_id is not None

    @patch("codemie.rest_api.routers.assistant.AssistantRepository")
    @patch("codemie.rest_api.routers.assistant.Assistant.find_by_id")
    @patch("codemie.rest_api.routers.assistant.AssistantCloneEventRepository")
    def test_repeat_clone_from_same_source_increments_each_call(
        self, mock_clone_event_repo_class, mock_find_by_id, mock_assistant_repo_class
    ):
        """CR-004: repeated clones of the same source must each log an event and
        trigger a recount, not be deduplicated in the router path."""
        mock_clone_event_repo = mock_clone_event_repo_class.return_value
        mock_find_by_id.return_value = MagicMock(spec=Assistant)

        request = self._build_request(source_assistant_id="source-id-1")
        self._run_create(request)
        self._run_create(request)

        assert mock_clone_event_repo.log_clone_event.call_count == 2
        mock_clone_event_repo.log_clone_event.assert_called_with("source-id-1", "user-123")
        assert mock_assistant_repo_class.update_clone_count.call_count == 2
        mock_assistant_repo_class.update_clone_count.assert_called_with("source-id-1")

    @patch("codemie.rest_api.routers.assistant.AssistantRepository")
    @patch("codemie.rest_api.routers.assistant.Assistant.find_by_id")
    @patch("codemie.rest_api.routers.assistant.AssistantCloneEventRepository")
    def test_nonexistent_source_assistant_id_skips_clone_tracking(
        self, mock_clone_event_repo_class, mock_find_by_id, mock_assistant_repo_class
    ):
        """CR-006: an unresolvable source_assistant_id must not log a clone event."""
        mock_clone_event_repo = mock_clone_event_repo_class.return_value
        mock_find_by_id.return_value = None

        request = self._build_request(source_assistant_id="nonexistent-id")
        response = self._run_create(request)

        mock_clone_event_repo.log_clone_event.assert_not_called()
        mock_assistant_repo_class.update_clone_count.assert_not_called()
        assert response.assistant_id is not None

    @patch("codemie.rest_api.routers.assistant.AssistantRepository")
    @patch("codemie.rest_api.routers.assistant.AssistantCloneEventRepository")
    def test_no_source_assistant_id_is_noop(self, mock_clone_event_repo_class, mock_assistant_repo_class):
        mock_clone_event_repo = mock_clone_event_repo_class.return_value

        request = self._build_request(source_assistant_id=None)
        self._run_create(request)

        mock_clone_event_repo.log_clone_event.assert_not_called()
        mock_assistant_repo_class.update_clone_count.assert_not_called()

    @patch("codemie.rest_api.routers.assistant.AssistantRepository")
    @patch("codemie.rest_api.routers.assistant.Assistant.find_by_id")
    @patch("codemie.rest_api.routers.assistant.AssistantCloneEventRepository")
    @patch("codemie.rest_api.routers.assistant.logger")
    def test_clone_tracking_exception_does_not_fail_create(
        self, mock_logger, mock_clone_event_repo_class, mock_find_by_id, mock_assistant_repo_class
    ):
        mock_clone_event_repo = mock_clone_event_repo_class.return_value
        mock_clone_event_repo.log_clone_event.side_effect = RuntimeError("db exploded")
        mock_find_by_id.return_value = MagicMock(spec=Assistant)

        request = self._build_request(source_assistant_id="source-id-1")
        response = self._run_create(request)

        assert response.assistant_id is not None
        mock_logger.error.assert_called_once()
        assert "source-id-1" in mock_logger.error.call_args.args[0]


class TestAssistantDetailEnrichment:
    """Guards that the by-id and by-slug lookup endpoints enrich the assistant identically.

    Regression for EPMCDME-13262: the by-slug endpoint used to skip `user_abilities`, so the UI
    could not show owner actions (Edit/Delete/Publish) on human-readable /{project}/{slug} URLs.
    Both endpoints now share `_build_assistant_detail_response`, so both must return the abilities.
    """

    def _build_assistant(self):
        return Assistant(
            id="assistant-123",
            name="Test Assistant",
            description="Test Description",
            project="demo",
            system_prompt="Test Prompt",
            slug="test-assistant",
        )

    def _enrichment_patches(self):
        """Patch the enrichment collaborators shared by both detail endpoints."""
        return [
            patch("codemie.rest_api.routers.assistant._check_user_can_access_assistant"),
            patch("codemie.rest_api.routers.assistant._validate_remote_entities_and_raise"),
            patch("codemie.rest_api.routers.assistant.ToolsInfoService.get_tools_info", return_value=[]),
            patch("codemie.rest_api.routers.assistant.Assistant.get_by_ids", return_value=[]),
            patch("codemie.rest_api.routers.assistant._get_categories_data"),
            patch("codemie.rest_api.routers.assistant._mask_sensitive_prompt_variables"),
            patch("codemie.rest_api.routers.assistant.AssistantRepository"),
            patch(
                "codemie.rest_api.routers.assistant.GuardrailService.get_entity_guardrail_assignments",
                return_value=[],
            ),
        ]

    @patch("codemie.rest_api.routers.assistant.Ability")
    @patch("codemie.rest_api.routers.assistant._get_assistant_by_slug_or_raise")
    def test_slug_response_includes_user_abilities(self, mock_get_by_slug, mock_ability):
        from codemie.core.ability import Action
        from codemie.rest_api.routers.assistant import get_assistant_by_slug

        assistant = self._build_assistant()
        mock_get_by_slug.return_value = assistant
        mock_ability.return_value.list.return_value = [Action.READ, Action.WRITE, Action.DELETE]

        user = MagicMock(spec=User)

        context_managers = self._enrichment_patches()
        for cm in context_managers:
            cm.start()
        try:
            response = get_assistant_by_slug(
                request=MagicMock(),
                assistant_slug="test-assistant",
                user=user,
                project="demo",
            )
        finally:
            for cm in context_managers:
                cm.stop()

        # Abilities are computed for the resolved assistant and serialized into the response,
        # so the UI can render owner-only menu actions on the human-readable URL.
        mock_ability.assert_called_once_with(user)
        mock_ability.return_value.list.assert_called_once_with(assistant)

        body = json.loads(response.body)
        assert body["user_abilities"] == ["read", "write", "delete"]


class TestAskAssistantSingleUserInvariant:
    @patch("codemie.rest_api.routers.assistant.assistant_user_interaction_service.record_usage")
    @patch("codemie.rest_api.routers.assistant.request_summary_manager.create_request_summary")
    @patch("codemie.rest_api.routers.assistant.Ability")
    @patch("codemie.rest_api.routers.assistant.get_request_handler")
    def test_single_user_drives_both_access_control_and_record_usage(
        self,
        mock_get_handler,
        mock_ability,
        mock_request_summary,
        mock_record_usage,
        mock_user,
        mock_assistant,
    ):
        from codemie.rest_api.routers.assistant import _ask_assistant
        from codemie.core.models import AssistantChatRequest
        from unittest.mock import MagicMock

        mock_ability_instance = MagicMock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance
        mock_handler = MagicMock()
        mock_handler.process_request.return_value = {"response": "ok"}
        mock_get_handler.return_value = mock_handler
        mock_user.as_user_model.return_value = MagicMock()

        request = AssistantChatRequest(text="hi")
        _ask_assistant(mock_assistant, MagicMock(state=MagicMock(uuid="req-1")), request, mock_user, MagicMock())

        mock_ability.assert_called_with(mock_user)
        mock_record_usage.assert_called_once_with(assistant=mock_assistant, user=mock_user)
        mock_get_handler.assert_called_once_with(mock_assistant, mock_user, "req-1")


class TestAskVirtualAssistantSingleUserInvariant:
    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.assistant.asyncio.to_thread")
    async def test_virtual_assistant_uses_single_user_throughout(
        self,
        mock_to_thread,
        mock_user,
    ):
        from codemie.rest_api.routers.assistant import ask_virtual_assistant, VirtualAssistantChatRequest
        from unittest.mock import MagicMock

        mock_user.current_project = "caller-project"
        mock_to_thread.return_value = {"response": "ok"}

        async def _noop():
            return None

        raw_request = MagicMock()
        raw_request.state.wait_for_disconnect = MagicMock(return_value=_noop())
        request = VirtualAssistantChatRequest()

        # The single user is resolved by authenticate() (Teams swap tested there); routes
        # just receive it and thread it through unchanged.
        await ask_virtual_assistant(raw_request, MagicMock(), request, mock_user)

        # to_thread's positional args: (_ask_virtual_assistant, assistant, raw_request, chat_request, user, ...)
        call_args = mock_to_thread.call_args.args
        passed_assistant = call_args[1]
        passed_user = call_args[4]
        assert passed_assistant.project == "caller-project"
        assert passed_user is mock_user


def test_resume_tool_call_never_had_and_still_has_no_billing_user_param():
    """Regression guard: resume_tool_call/_ask_assistant_resume never took a
    billing_user-shaped kwarg, before or after the authenticate() Teams swap
    (EPMCDME-10250) — the single authenticate()-resolved user flows through
    unmodified."""
    import inspect

    from codemie.rest_api.routers.assistant import resume_tool_call, _ask_assistant_resume

    resume_params = inspect.signature(resume_tool_call).parameters
    assert "billing_user" not in resume_params

    internal_params = inspect.signature(_ask_assistant_resume).parameters
    assert "billing_user" not in internal_params
