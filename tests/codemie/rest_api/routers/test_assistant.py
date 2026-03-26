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

from unittest.mock import patch, MagicMock

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.guardrail import GuardrailEntity, GuardrailSource
from codemie.rest_api.routers.assistant import _ask_assistant, _resolve_billing_project
from codemie.rest_api.security.user import User


@pytest.fixture
def mock_user():
    """Create a mock user for testing."""
    user = MagicMock(spec=User)
    user.id = "user-123"
    user.username = "testuser"
    user.name = "Test User"
    user.is_admin = False
    user.project_names = ["test-project"]
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


class TestResolveBillingProject:
    """Unit tests for _resolve_billing_project — project attribution logic for marketplace assistants."""

    def _make_assistant(self, project: str, is_global: bool | None) -> MagicMock:
        a = MagicMock(spec=Assistant)
        a.project = project
        a.is_global = is_global
        return a

    def _make_user(
        self, email: str, project_names: list[str], admin_project_names: list[str] | None = None
    ) -> MagicMock:
        u = MagicMock(spec=User)
        u.id = "user-123"
        u.email = email
        u.project_names = project_names
        u.admin_project_names = admin_project_names or []
        return u

    def test_non_marketplace_assistant_returns_assistant_project(self):
        """Non-marketplace assistants always use their own project regardless of membership."""
        assistant = self._make_assistant("owner-project", is_global=False)
        user = self._make_user("user@example.com", project_names=["other-project"])

        assert _resolve_billing_project(assistant, user) == "owner-project"

    def test_non_marketplace_assistant_with_none_is_global(self):
        """is_global=None (falsy) is treated the same as is_global=False."""
        assistant = self._make_assistant("owner-project", is_global=None)
        user = self._make_user("user@example.com", project_names=[])

        assert _resolve_billing_project(assistant, user) == "owner-project"

    def test_marketplace_assistant_member_returns_assistant_project(self):
        """Project member using a marketplace assistant: no substitution needed."""
        assistant = self._make_assistant("owner-project", is_global=True)
        user = self._make_user("user@example.com", project_names=["owner-project", "another-project"])

        assert _resolve_billing_project(assistant, user) == "owner-project"

    def test_marketplace_assistant_non_member_returns_user_email(self):
        """Non-member using a marketplace assistant: billing redirected to user's personal project."""
        assistant = self._make_assistant("owner-project", is_global=True)
        user = self._make_user("user@example.com", project_names=["my-project"])

        assert _resolve_billing_project(assistant, user) == "user@example.com"

    def test_marketplace_assistant_non_member_no_projects_returns_user_email(self):
        """Non-member with no projects at all: fallback is still user email."""
        assistant = self._make_assistant("owner-project", is_global=True)
        user = self._make_user("user@example.com", project_names=[])

        assert _resolve_billing_project(assistant, user) == "user@example.com"

    def test_marketplace_assistant_admin_only_member_returns_assistant_project(self):
        """User in admin_project_names but not project_names is still treated as a member."""
        assistant = self._make_assistant("owner-project", is_global=True)
        user = self._make_user("admin@example.com", project_names=[], admin_project_names=["owner-project"])

        assert _resolve_billing_project(assistant, user) == "owner-project"

    def test_marketplace_assistant_project_names_none_treated_as_empty(self):
        """project_names=None does not raise TypeError; falls through to email fallback."""
        assistant = self._make_assistant("owner-project", is_global=True)
        user = self._make_user("user@example.com", project_names=None)

        assert _resolve_billing_project(assistant, user) == "user@example.com"

    def test_raises_when_assistant_project_is_none(self):
        """assistant.project=None raises ExtendedHTTPException (invariant violation)."""
        from codemie.core.exceptions import ExtendedHTTPException

        assistant = self._make_assistant(None, is_global=False)
        user = self._make_user("user@example.com", project_names=[])

        with pytest.raises(ExtendedHTTPException) as exc_info:
            _resolve_billing_project(assistant, user)
        assert exc_info.value.code == 500

    def test_raises_when_user_email_is_empty(self):
        """user.email='' raises ExtendedHTTPException when it would be used as billing project."""
        from codemie.core.exceptions import ExtendedHTTPException

        assistant = self._make_assistant("owner-project", is_global=True)
        user = self._make_user("", project_names=["other-project"])

        with pytest.raises(ExtendedHTTPException) as exc_info:
            _resolve_billing_project(assistant, user)
        assert exc_info.value.code == 500


class TestAskAssistantBillingProjectSubstitution:
    """Integration tests verifying that _ask_assistant applies billing-project substitution correctly."""

    def _make_request(self):
        raw = MagicMock()
        raw.state.uuid = "test-uuid"
        return raw

    @patch("codemie.rest_api.routers.assistant._validate_remote_entities_and_raise")
    @patch("codemie.rest_api.routers.assistant._validate_assistant_supports_model_change_and_raise")
    @patch("codemie.rest_api.routers.assistant.assistant_user_interaction_service.record_usage")
    @patch("codemie.rest_api.routers.assistant.request_summary_manager.create_request_summary")
    @patch("codemie.rest_api.routers.assistant.Ability")
    @patch("codemie.rest_api.routers.assistant.get_request_handler")
    def test_non_marketplace_assistant_uses_original_project(
        self, mock_get_handler, mock_ability, mock_create_summary, mock_record_usage, _mv, _vr
    ):
        """Non-marketplace assistant: project attribution unchanged."""
        assistant = MagicMock(spec=Assistant)
        assistant.id = "a-1"
        assistant.project = "owner-project"
        assistant.is_global = False

        user = MagicMock(spec=User)
        user.email = "user@example.com"
        user.project_names = ["my-project"]
        user.admin_project_names = []

        mock_ability.return_value.can.return_value = True
        mock_get_handler.return_value.process_request.return_value = {}

        _ask_assistant(assistant, self._make_request(), AssistantChatRequest(text=None), user, MagicMock())

        mock_create_summary.assert_called_once_with(
            request_id="test-uuid",
            project_name="owner-project",
            user=user.as_user_model(),
        )

    @patch("codemie.rest_api.routers.assistant._validate_remote_entities_and_raise")
    @patch("codemie.rest_api.routers.assistant._validate_assistant_supports_model_change_and_raise")
    @patch("codemie.rest_api.routers.assistant.assistant_user_interaction_service.record_usage")
    @patch("codemie.rest_api.routers.assistant.request_summary_manager.create_request_summary")
    @patch("codemie.rest_api.routers.assistant.Ability")
    @patch("codemie.rest_api.routers.assistant.get_request_handler")
    def test_marketplace_assistant_member_uses_original_project(
        self, mock_get_handler, mock_ability, mock_create_summary, mock_record_usage, _mv, _vr
    ):
        """Marketplace assistant accessed by a project member: no substitution."""
        assistant = MagicMock(spec=Assistant)
        assistant.id = "a-1"
        assistant.project = "owner-project"
        assistant.is_global = True

        user = MagicMock(spec=User)
        user.email = "user@example.com"
        user.project_names = ["owner-project", "my-project"]
        user.admin_project_names = ["owner-project"]

        mock_ability.return_value.can.return_value = True
        mock_get_handler.return_value.process_request.return_value = {}

        _ask_assistant(assistant, self._make_request(), AssistantChatRequest(text=None), user, MagicMock())

        mock_create_summary.assert_called_once_with(
            request_id="test-uuid",
            project_name="owner-project",
            user=user.as_user_model(),
        )

    @patch("codemie.rest_api.routers.assistant._validate_remote_entities_and_raise")
    @patch("codemie.rest_api.routers.assistant._validate_assistant_supports_model_change_and_raise")
    @patch("codemie.rest_api.routers.assistant.assistant_user_interaction_service.record_usage")
    @patch("codemie.rest_api.routers.assistant.request_summary_manager.create_request_summary")
    @patch("codemie.rest_api.routers.assistant.Ability")
    @patch("codemie.rest_api.routers.assistant.get_request_handler")
    def test_marketplace_assistant_non_member_uses_user_email(
        self, mock_get_handler, mock_ability, mock_create_summary, mock_record_usage, _mv, _vr
    ):
        """Marketplace assistant accessed by a non-member: billing redirected to user's personal project."""
        assistant = MagicMock(spec=Assistant)
        assistant.id = "a-1"
        assistant.project = "owner-project"
        assistant.is_global = True
        # model_copy must return a copy whose .project reflects the update dict
        copied = MagicMock(spec=Assistant)
        copied.project = "user@example.com"
        assistant.model_copy.return_value = copied

        user = MagicMock(spec=User)
        user.email = "user@example.com"
        user.project_names = ["my-project"]
        user.admin_project_names = []

        mock_ability.return_value.can.return_value = True
        mock_get_handler.return_value.process_request.return_value = {}

        _ask_assistant(assistant, self._make_request(), AssistantChatRequest(text=None), user, MagicMock())

        assistant.model_copy.assert_called_once_with(update={"project": "user@example.com"})
        mock_create_summary.assert_called_once_with(
            request_id="test-uuid",
            project_name="user@example.com",
            user=user.as_user_model(),
        )
        # get_request_handler must receive the substituted assistant copy, not the original
        handler_assistant_arg = mock_get_handler.call_args.args[0]
        assert handler_assistant_arg is copied

    @patch("codemie.rest_api.routers.assistant._validate_remote_entities_and_raise")
    @patch("codemie.rest_api.routers.assistant._validate_assistant_supports_model_change_and_raise")
    @patch("codemie.rest_api.routers.assistant.assistant_user_interaction_service.record_usage")
    @patch("codemie.rest_api.routers.assistant.request_summary_manager.create_request_summary")
    @patch("codemie.rest_api.routers.assistant.Ability")
    @patch("codemie.rest_api.routers.assistant.get_request_handler")
    def test_marketplace_assistant_admin_only_treated_as_member(
        self, mock_get_handler, mock_ability, mock_create_summary, mock_record_usage, _mv, _vr
    ):
        """User in admin_project_names but not project_names is still treated as a member."""
        assistant = MagicMock(spec=Assistant)
        assistant.id = "a-1"
        assistant.project = "owner-project"
        assistant.is_global = True

        user = MagicMock(spec=User)
        user.email = "admin@example.com"
        user.project_names = []
        user.admin_project_names = ["owner-project"]  # admin-only membership

        mock_ability.return_value.can.return_value = True
        mock_get_handler.return_value.process_request.return_value = {}

        _ask_assistant(assistant, self._make_request(), AssistantChatRequest(text=None), user, MagicMock())

        mock_create_summary.assert_called_once_with(
            request_id="test-uuid",
            project_name="owner-project",
            user=user.as_user_model(),
        )

    @patch("codemie.rest_api.routers.assistant._validate_remote_entities_and_raise")
    @patch("codemie.rest_api.routers.assistant._validate_assistant_supports_model_change_and_raise")
    @patch("codemie.rest_api.routers.assistant.assistant_user_interaction_service.record_usage")
    @patch("codemie.rest_api.routers.assistant.request_summary_manager.create_request_summary")
    @patch("codemie.rest_api.routers.assistant.Ability")
    @patch("codemie.rest_api.routers.assistant.get_request_handler")
    def test_record_usage_always_receives_original_assistant_project(
        self, mock_get_handler, mock_ability, mock_create_summary, mock_record_usage, _mv, _vr
    ):
        """record_usage must always see the original assistant project, not the substituted one."""
        assistant = MagicMock(spec=Assistant)
        assistant.id = "a-1"
        assistant.project = "owner-project"
        assistant.is_global = True

        user = MagicMock(spec=User)
        user.email = "user@example.com"
        user.project_names = ["my-project"]
        user.admin_project_names = []

        mock_ability.return_value.can.return_value = True
        mock_get_handler.return_value.process_request.return_value = {}

        _ask_assistant(assistant, self._make_request(), AssistantChatRequest(text=None), user, MagicMock())

        # record_usage must be called with the ORIGINAL assistant (original project intact)
        call_kwargs = mock_record_usage.call_args.kwargs
        assert call_kwargs["assistant"].project == "owner-project"
