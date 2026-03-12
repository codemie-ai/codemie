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

"""Unit tests for AssistantFactory."""

import pytest
from unittest.mock import Mock, patch

from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.tools.assistant_factory import AssistantFactory, create_assistant_executors


class TestAssistantFactory:
    """Test suite for AssistantFactory."""

    @pytest.fixture
    def mock_assistant(self):
        """Fixture for mocking Assistant."""
        assistant = Mock(spec=Assistant)
        assistant.id = "test-assistant-id"
        assistant.name = "Test Assistant"
        assistant.project = "test-project"
        return assistant

    @pytest.fixture
    def mock_user(self):
        """Fixture for mocking User."""
        user = Mock(spec=User)
        user.id = "test-user-id"
        user.name = "Test User"
        user.full_name = "Test User Full Name"
        return user

    @pytest.fixture
    def mock_request(self):
        """Fixture for mocking AssistantChatRequest."""
        request = Mock(spec=AssistantChatRequest)
        request.text = "Test request"
        request.history = []
        request.sub_assistants_versions = None
        return request

    @pytest.fixture
    def mock_thread_generator(self):
        """Fixture for mocking MessageQueue/ThreadGenerator."""
        return Mock()

    def test_assistant_factory_initialization(self, mock_assistant, mock_user, mock_request):
        """Test AssistantFactory initialization."""
        factory = AssistantFactory(
            assistant=mock_assistant,
            user=mock_user,
            request=mock_request,
            request_uuid="test-uuid",
            thread_generator=None,
            llm_model="gpt-4",
        )

        assert factory.assistant == mock_assistant
        assert factory.user == mock_user
        assert factory.request == mock_request
        assert factory.request_uuid == "test-uuid"
        assert factory.thread_generator is None
        assert factory.llm_model == "gpt-4"

    @patch("codemie.service.assistant_service.AssistantService.build_agent")
    def test_factory_build_creates_agent_executor(self, mock_build_agent, mock_assistant, mock_user, mock_request):
        """Test that factory.build() creates and returns an agent executor."""
        # Setup mocks
        mock_agent = Mock()
        mock_agent_executor = Mock()
        mock_agent.agent_executor = mock_agent_executor
        mock_agent.set_thread_context = Mock()

        mock_build_agent.return_value = mock_agent

        # Create factory and build
        factory = AssistantFactory(
            assistant=mock_assistant,
            user=mock_user,
            request=mock_request,
            request_uuid="test-uuid",
            thread_generator=None,
            llm_model="gpt-4",
        )

        result = factory.build()

        # Assertions
        mock_build_agent.assert_called_once()
        mock_agent.set_thread_context.assert_called_once()
        assert result == mock_agent_executor

    @patch("codemie.service.assistant_service.AssistantService.build_agent")
    def test_factory_build_with_thread_generator(
        self, mock_build_agent, mock_assistant, mock_user, mock_request, mock_thread_generator
    ):
        """Test factory.build() with thread generator for streaming."""
        # Setup mocks
        mock_agent = Mock()
        mock_agent_executor = Mock()
        mock_agent.agent_executor = mock_agent_executor
        mock_agent.set_thread_context = Mock()

        mock_build_agent.return_value = mock_agent

        factory = AssistantFactory(
            assistant=mock_assistant,
            user=mock_user,
            request=mock_request,
            request_uuid="test-uuid",
            thread_generator=mock_thread_generator,
            llm_model="gpt-4",
        )

        result = factory.build()

        # Assertions
        assert result == mock_agent_executor
        # Verify thread_generator was passed to build_agent
        call_kwargs = mock_build_agent.call_args[1]
        assert call_kwargs["thread_generator"] == mock_thread_generator

    @patch("codemie.service.assistant_service.AssistantService.build_agent")
    def test_factory_build_handles_exception(self, mock_build_agent, mock_assistant, mock_user, mock_request):
        """Test that factory.build() handles exceptions properly."""
        # Setup mock to raise exception
        mock_build_agent.side_effect = Exception("Build failed")

        # Create factory
        factory = AssistantFactory(
            assistant=mock_assistant,
            user=mock_user,
            request=mock_request,
            request_uuid="test-uuid",
            thread_generator=None,
            llm_model="gpt-4",
        )

        # Should raise the exception
        with pytest.raises(Exception, match="Build failed"):
            factory.build()

    @patch("codemie.service.tools.assistant_factory.Assistant")
    @patch("codemie.service.tools.assistant_factory.AssistantFactory")
    def test_create_assistant_executors_basic(self, mock_factory_class, mock_assistant_class, mock_user, mock_request):
        """Test create_assistant_executors with basic parameters."""
        # Setup mocks
        assistant_ids = ["assistant-1", "assistant-2"]
        mock_assistants = [Mock(id="assistant-1"), Mock(id="assistant-2")]
        mock_assistant_class.get_by_ids.return_value = mock_assistants

        mock_executor1 = Mock()
        mock_executor2 = Mock()
        mock_factory_instance1 = Mock()
        mock_factory_instance1.build.return_value = mock_executor1
        mock_factory_instance2 = Mock()
        mock_factory_instance2.build.return_value = mock_executor2

        mock_factory_class.side_effect = [mock_factory_instance1, mock_factory_instance2]

        # Mock validation to return None (no deleted assistants)
        with patch(
            "codemie.service.tools.assistant_factory._validate_remote_entity_exists_and_cleanup", return_value=None
        ):
            # Call function
            result = create_assistant_executors(
                assistant_ids=assistant_ids,
                user=mock_user,
                request=mock_request,
                request_uuid="test-uuid",
                thread_generator=None,
                llm_model="gpt-4",
            )

        # Assertions
        assert len(result) == 2
        assert result[0] == mock_executor1
        assert result[1] == mock_executor2
        mock_assistant_class.get_by_ids.assert_called_once_with(mock_user, assistant_ids, parent_assistant=None)

    @patch("codemie.service.tools.assistant_factory.Assistant")
    @patch("codemie.service.tools.assistant_factory.AssistantFactory")
    def test_create_assistant_executors_with_parent(
        self, mock_factory_class, mock_assistant_class, mock_user, mock_request
    ):
        """Test create_assistant_executors with parent assistant."""
        # Setup mocks
        assistant_ids = ["assistant-1"]
        mock_assistants = [Mock(id="assistant-1")]
        mock_assistant_class.get_by_ids.return_value = mock_assistants
        parent_assistant = Mock(id="parent-id")

        mock_executor = Mock()
        mock_factory_instance = Mock()
        mock_factory_instance.build.return_value = mock_executor
        mock_factory_class.return_value = mock_factory_instance

        # Mock validation
        with patch(
            "codemie.service.tools.assistant_factory._validate_remote_entity_exists_and_cleanup", return_value=None
        ):
            # Call function with parent_assistant
            result = create_assistant_executors(
                assistant_ids=assistant_ids,
                user=mock_user,
                request=mock_request,
                request_uuid="test-uuid",
                thread_generator=None,
                llm_model="gpt-4",
                parent_assistant=parent_assistant,
            )

        # Assertions
        assert len(result) == 1
        mock_assistant_class.get_by_ids.assert_called_once_with(
            mock_user, assistant_ids, parent_assistant=parent_assistant
        )

    @patch("codemie.service.tools.assistant_factory.Assistant")
    @patch("codemie.service.assistant.assistant_version_service.AssistantVersionService.apply_version_to_assistant")
    @patch("codemie.service.tools.assistant_factory.AssistantFactory")
    def test_create_assistant_executors_with_version(
        self, mock_factory_class, mock_apply_version, mock_assistant_class, mock_user, mock_request
    ):
        """Test create_assistant_executors with version pinning."""
        # Setup mocks
        assistant_ids = ["assistant-1"]
        mock_assistant = Mock(id="assistant-1")
        mock_assistant_class.get_by_ids.return_value = [mock_assistant]

        # Setup request with version override
        mock_request.sub_assistants_versions = {"assistant-1": 2}

        mock_versioned_assistant = Mock(id="assistant-1", version=2)
        mock_apply_version.return_value = mock_versioned_assistant

        mock_executor = Mock()
        mock_factory_instance = Mock()
        mock_factory_instance.build.return_value = mock_executor
        mock_factory_class.return_value = mock_factory_instance

        # Mock validation
        with patch(
            "codemie.service.tools.assistant_factory._validate_remote_entity_exists_and_cleanup", return_value=None
        ):
            # Call function
            result = create_assistant_executors(
                assistant_ids=assistant_ids,
                user=mock_user,
                request=mock_request,
                request_uuid="test-uuid",
                thread_generator=None,
                llm_model="gpt-4",
            )

        # Assertions
        assert len(result) == 1
        mock_apply_version.assert_called_once_with(mock_assistant, 2)

    @patch("codemie.service.tools.assistant_factory.Assistant")
    def test_create_assistant_executors_skips_deleted_assistants(self, mock_assistant_class, mock_user, mock_request):
        """Test that deleted assistants are skipped."""
        # Setup mocks
        assistant_ids = ["assistant-1", "assistant-2"]
        mock_assistants = [Mock(id="assistant-1"), Mock(id="assistant-2")]
        mock_assistant_class.get_by_ids.return_value = mock_assistants

        # Mock validation - first returns "Deleted", second returns None
        with patch(
            "codemie.service.tools.assistant_factory._validate_remote_entity_exists_and_cleanup",
            side_effect=["DeletedAssistant", None],
        ):
            with patch("codemie.service.tools.assistant_factory.AssistantFactory") as mock_factory_class:
                mock_executor = Mock()
                mock_factory_instance = Mock()
                mock_factory_instance.build.return_value = mock_executor
                mock_factory_class.return_value = mock_factory_instance

                # Call function
                result = create_assistant_executors(
                    assistant_ids=assistant_ids,
                    user=mock_user,
                    request=mock_request,
                    request_uuid="test-uuid",
                    thread_generator=None,
                    llm_model="gpt-4",
                )

        # Assertions - only one assistant should be created (the second one)
        assert len(result) == 1

    @patch("codemie.service.tools.assistant_factory.Assistant")
    @patch("codemie.service.tools.assistant_factory.AssistantFactory")
    def test_create_assistant_executors_handles_factory_exception(
        self, mock_factory_class, mock_assistant_class, mock_user, mock_request
    ):
        """Test that exceptions during factory.build() are handled gracefully."""
        # Setup mocks
        assistant_ids = ["assistant-1", "assistant-2"]
        mock_assistants = [Mock(id="assistant-1"), Mock(id="assistant-2")]
        mock_assistant_class.get_by_ids.return_value = mock_assistants

        # First factory succeeds, second fails
        mock_executor1 = Mock()
        mock_factory_instance1 = Mock()
        mock_factory_instance1.build.return_value = mock_executor1

        mock_factory_instance2 = Mock()
        mock_factory_instance2.build.side_effect = Exception("Build failed")

        mock_factory_class.side_effect = [mock_factory_instance1, mock_factory_instance2]

        # Mock validation
        with patch(
            "codemie.service.tools.assistant_factory._validate_remote_entity_exists_and_cleanup", return_value=None
        ):
            # Call function - should not raise exception
            result = create_assistant_executors(
                assistant_ids=assistant_ids,
                user=mock_user,
                request=mock_request,
                request_uuid="test-uuid",
                thread_generator=None,
                llm_model="gpt-4",
            )

        # Assertions - only the first executor should be in result
        assert len(result) == 1
        assert result[0] == mock_executor1

    @patch("codemie.service.tools.assistant_factory.Assistant")
    def test_create_assistant_executors_handles_get_by_ids_exception(
        self, mock_assistant_class, mock_user, mock_request
    ):
        """Test that exceptions when fetching assistants are handled."""
        # Setup mock to raise exception
        assistant_ids = ["assistant-1"]
        mock_assistant_class.get_by_ids.side_effect = Exception("Database error")

        # Call function - should not raise exception
        result = create_assistant_executors(
            assistant_ids=assistant_ids,
            user=mock_user,
            request=mock_request,
            request_uuid="test-uuid",
            thread_generator=None,
            llm_model="gpt-4",
        )

        # Assertions - should return empty list
        assert result == []

    @patch("codemie.service.tools.assistant_factory.Assistant")
    @patch("codemie.service.tools.assistant_factory.AssistantFactory")
    def test_create_assistant_executors_empty_list(
        self, mock_factory_class, mock_assistant_class, mock_user, mock_request
    ):
        """Test create_assistant_executors with empty assistant_ids list."""
        # Setup mocks
        assistant_ids = []
        mock_assistant_class.get_by_ids.return_value = []

        # Call function
        result = create_assistant_executors(
            assistant_ids=assistant_ids,
            user=mock_user,
            request=mock_request,
            request_uuid="test-uuid",
            thread_generator=None,
            llm_model="gpt-4",
        )

        # Assertions
        assert result == []
        mock_assistant_class.get_by_ids.assert_called_once()
