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
        request.llm_model = None
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
        )

        assert factory.assistant == mock_assistant
        assert factory.user == mock_user
        assert factory.request == mock_request
        assert factory.request_uuid == "test-uuid"
        assert factory.thread_generator is None

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
        )

        result = factory.build()

        # Assertions
        mock_build_agent.assert_called_once()
        mock_agent.set_thread_context.assert_called_once()
        assert result == mock_agent_executor

    @patch("codemie.service.assistant_service.AssistantService.build_agent")
    def test_factory_build_passes_is_subagent_true(self, mock_build_agent, mock_assistant, mock_user, mock_request):
        """factory.build() must always mark the built agent as a sub-assistant."""
        mock_agent = Mock()
        mock_agent.agent_executor = Mock()
        mock_agent.set_thread_context = Mock()
        mock_build_agent.return_value = mock_agent

        factory = AssistantFactory(
            assistant=mock_assistant,
            user=mock_user,
            request=mock_request,
            request_uuid="test-uuid",
            thread_generator=None,
        )

        factory.build()

        call_kwargs = mock_build_agent.call_args[1]
        assert call_kwargs["is_subagent"] is True

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
        )

        # Assertions
        assert result == []
        mock_assistant_class.get_by_ids.assert_called_once()


class _PolicyPassed(Exception):
    """Sentinel raised right after the model policy step of AssistantService.build_agent.

    ``args[0]`` is the model build_agent resolved (see ``_StopAtReactCheck``).
    """


class _StopAtReactCheck:
    """Stands in for get_react_llms(): the membership check raises _PolicyPassed with the resolved model."""

    def __contains__(self, item):
        raise _PolicyPassed(item)


class _RecordingAgent:
    """Stands in for AIToolsAgent / LangGraphAgent and keeps llm_model the way the real agents do."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.agent_name = kwargs["agent_name"]
        self.llm_model = kwargs["llm_model"]
        self.temperature = kwargs["temperature"]
        self.top_p = kwargs["top_p"]
        self.recursion_limit = None


class TestAssistantFactoryModelPolicy:
    """AssistantFactory.build delegates model policy to AssistantService.build_agent (resolve_assistant_model)."""

    @staticmethod
    def _assistant(is_global: bool = False):
        assistant = Mock(spec=Assistant)
        assistant.id = "sub-assistant-id"
        assistant.name = "Sub Assistant"
        assistant.type = "codemie"
        assistant.context = []
        assistant.toolkits = []
        assistant.project = "proj"
        assistant.is_global = is_global
        assistant.llm_model_type = "assistant-model"
        return assistant

    @staticmethod
    def _project(allowed_models, default_model=None):
        project = Mock()
        project.id = "proj"
        project.allowed_models = allowed_models
        project.default_model = default_model
        return project

    @staticmethod
    def _user():
        user = Mock(spec=User)
        user.id = "user-id"
        user.name = "User"
        return user

    def _build(self, assistant, project, flag_on: bool, config, llm_model: str):
        request = AssistantChatRequest(text="hi", llm_model=llm_model)
        with (
            patch("codemie.service.assistant_service.set_llm_context"),
            patch("codemie.service.assistant_service.build_unique_file_objects", return_value={}),
            patch(
                "codemie.service.assistant_service.BedrockOrchestratorService.is_bedrock_assistant",
                return_value=False,
            ),
            patch("codemie.service.assistant_service.AssistantService.drop_missing_context", return_value=[]),
            patch("codemie.service.assistant_service.Conversation.find_by_id", return_value=None),
            patch(
                "codemie.service.llm.model_availability_service.Application.get_by_id", return_value=project
            ) as get_by_id,
            patch(
                "codemie.service.assistant_service.llm_service.get_react_llms",
                return_value=_StopAtReactCheck(),
            ),
            patch("codemie.configs.customer_config.CustomerConfig.is_feature_enabled", return_value=flag_on),
            patch(
                "codemie.service.llm.model_availability_service.ModelAvailabilityService.get_project_models",
                return_value=config,
            ) as get_models,
        ):
            factory = AssistantFactory(assistant=assistant, user=self._user(), request=request, request_uuid="uuid")
            try:
                factory.build()
                outcome = None
            except Exception as e:  # noqa: BLE001 - the outcome is asserted by each test
                outcome = e
        return outcome, get_by_id, get_models

    def test_unconfigured_whitelist_delegates(self):
        from codemie.service.llm.model_availability_service import ProjectModelConfig

        assistant = self._assistant()
        outcome, _, get_models = self._build(
            assistant,
            self._project(allowed_models=None),
            flag_on=True,
            config=ProjectModelConfig(allowed_models=[], default_model=""),
            llm_model="gpt-4.1",
        )

        assert isinstance(outcome, _PolicyPassed)
        assert outcome.args[0] == "gpt-4.1"
        assert assistant.llm_model_type == "assistant-model"
        get_models.assert_not_called()

    def test_allowed_chat_model_wins_over_own_model(self):
        from codemie.service.llm.model_availability_service import ProjectModelConfig

        assistant = self._assistant()
        outcome, _, _ = self._build(
            assistant,
            self._project(allowed_models=["a", "assistant-model"], default_model="b"),
            flag_on=True,
            config=ProjectModelConfig(allowed_models=["a", "assistant-model"], default_model="b"),
            llm_model="a",
        )

        assert isinstance(outcome, _PolicyPassed)
        assert outcome.args[0] == "a"
        assert assistant.llm_model_type == "assistant-model"

    def test_disallowed_chat_model_falls_back_to_own_model(self):
        from codemie.service.llm.model_availability_service import ProjectModelConfig

        assistant = self._assistant()
        outcome, _, _ = self._build(
            assistant,
            self._project(allowed_models=["assistant-model", "b"], default_model="b"),
            flag_on=True,
            config=ProjectModelConfig(allowed_models=["assistant-model", "b"], default_model="b"),
            llm_model="x",
        )

        assert isinstance(outcome, _PolicyPassed)
        assert outcome.args[0] == "assistant-model"
        assert assistant.llm_model_type == "assistant-model"

    def test_empty_whitelist_raises_from_build_agent(self):
        from codemie.core.exceptions import ModelNotWhitelistedException
        from codemie.service.llm.model_availability_service import ProjectModelConfig

        outcome, _, _ = self._build(
            self._assistant(),
            self._project(allowed_models=[], default_model="b"),
            flag_on=True,
            config=ProjectModelConfig(allowed_models=[], default_model="b"),
            llm_model="x",
        )

        assert isinstance(outcome, ModelNotWhitelistedException)

    def test_populated_whitelist_falls_back(self):
        from codemie.service.llm.model_availability_service import ProjectModelConfig

        assistant = self._assistant()
        outcome, _, _ = self._build(
            assistant,
            self._project(allowed_models=["a", "b"], default_model="b"),
            flag_on=True,
            config=ProjectModelConfig(allowed_models=["a", "b"], default_model="b"),
            llm_model="x",
        )

        assert isinstance(outcome, _PolicyPassed)
        assert outcome.args[0] == "b"
        assert assistant.llm_model_type == "assistant-model"

    def test_global_assistant_skips_project(self):
        from codemie.service.llm.model_availability_service import ProjectModelConfig

        assistant = self._assistant(is_global=True)
        outcome, get_by_id, _ = self._build(
            assistant,
            self._project(allowed_models=[], default_model="b"),
            flag_on=True,
            config=ProjectModelConfig(allowed_models=[], default_model="b"),
            llm_model="x",
        )

        assert isinstance(outcome, _PolicyPassed)
        assert outcome.args[0] == "x"
        assert assistant.llm_model_type == "assistant-model"
        get_by_id.assert_not_called()

    def test_flag_off_unchanged(self):
        from codemie.service.llm.model_availability_service import ProjectModelConfig

        assistant = self._assistant()
        outcome, _, _ = self._build(
            assistant,
            self._project(allowed_models=[], default_model="b"),
            flag_on=False,
            config=ProjectModelConfig(allowed_models=[], default_model="b"),
            llm_model="x",
        )

        assert isinstance(outcome, _PolicyPassed)
        assert outcome.args[0] == "x"
        assert assistant.llm_model_type == "assistant-model"

    def test_create_assistant_executors_unconfigured_whitelist(self):
        from codemie.service.llm.model_availability_service import ProjectModelConfig

        agent = Mock()
        sub_assistant = self._assistant()
        with (
            patch("codemie.service.assistant_service.AssistantService.build_agent", return_value=agent),
            patch("codemie.service.tools.assistant_factory.Assistant.get_by_ids", return_value=[sub_assistant]),
            patch(
                "codemie.service.tools.assistant_factory._validate_remote_entity_exists_and_cleanup",
                return_value=None,
            ),
            patch(
                "codemie.service.llm.model_availability_service.Application.get_by_id",
                return_value=self._project(allowed_models=None),
            ),
            patch("codemie.configs.customer_config.CustomerConfig.is_feature_enabled", return_value=True),
            patch(
                "codemie.service.llm.model_availability_service.ModelAvailabilityService.get_project_models",
                return_value=ProjectModelConfig(allowed_models=[], default_model=""),
            ),
        ):
            result = create_assistant_executors(
                assistant_ids=["sub-assistant-id"],
                user=self._user(),
                request=AssistantChatRequest(text="hi", llm_model="gpt-4.1"),
                request_uuid="uuid",
            )

        assert result == [agent.agent_executor]


class TestBuildAgentResolvedModel:
    """Root build_agent resolves the model once, exposes it as agent.llm_model and mutates no input (AC6, AC8)."""

    @staticmethod
    def _assistant(own_model: str):
        assistant = Mock(spec=Assistant)
        assistant.id = "root-assistant-id"
        assistant.name = "Root Assistant"
        assistant.description = "Root"
        assistant.type = "codemie"
        assistant.context = []
        assistant.toolkits = []
        assistant.project = "proj"
        assistant.is_global = False
        assistant.smart_tool_selection_enabled = False
        assistant.temperature = None
        assistant.top_p = None
        assistant.llm_model_type = own_model
        return assistant

    @staticmethod
    def _user():
        user = Mock(spec=User)
        user.id = "user-id"
        user.name = "User"
        return user

    @staticmethod
    def _conversation(llm_model):
        conversation = Mock()
        conversation.llm_model = llm_model
        conversation.enable_image_generation = None
        conversation.image_generation_model = None
        conversation.tool_call_policy = None
        return conversation

    def _build_agent(self, assistant, request, conversation=None):
        from codemie.service.assistant_service import AssistantService

        project = Mock(id="proj", allowed_models=["D", "M"], default_model="D")
        with (
            patch("codemie.service.assistant_service.set_llm_context"),
            patch("codemie.service.assistant_service.build_unique_file_objects", return_value={}),
            patch(
                "codemie.service.assistant_service.BedrockOrchestratorService.is_bedrock_assistant",
                return_value=False,
            ),
            patch("codemie.service.assistant_service.AssistantService.drop_missing_context", return_value=[]),
            patch("codemie.service.assistant_service.Conversation.find_by_id", return_value=conversation),
            patch("codemie.service.llm.model_availability_service.Application.get_by_id", return_value=project),
            patch("codemie.configs.customer_config.CustomerConfig.is_feature_enabled", return_value=True),
            patch("codemie.service.assistant_service.llm_service.get_react_llms", return_value=[]),
            patch("codemie.service.assistant_service.AssistantService._apply_marketplace_tool_mappings"),
            patch("codemie.service.assistant_service.SkillContributionsResolver.resolve_for_assistant"),
            patch("codemie.service.assistant_service.prepare_skill_workspace", return_value=[]),
            patch("codemie.service.assistant_service.ToolkitService.get_tools", return_value=[]) as get_tools,
            patch("codemie.service.tools.oauth_connect_gate.enforce_oauth_connected"),
            patch("codemie.service.assistant_service.AssistantService._prepare_system_prompt", return_value=""),
            patch("codemie.service.assistant_service.AssistantService._configure_langgraph_agent"),
            patch("codemie.service.assistant_service.AIToolsAgent", _RecordingAgent),
            patch("codemie.service.assistant_service.LangGraphAgent", _RecordingAgent),
        ):
            agent = AssistantService.build_agent(
                assistant=assistant, request=request, user=self._user(), request_uuid="uuid"
            )
        return agent, get_tools

    def test_root_runs_on_own_allowed_model_without_mutating_inputs(self):
        """AC6 root: whitelist [D, M], default D, own M, pick X -> agent runs on M; inputs untouched."""
        assistant = self._assistant(own_model="M")
        request = AssistantChatRequest(text="hi", llm_model="X")
        fields_before = set(request.model_fields_set)

        agent, get_tools = self._build_agent(assistant, request)

        assert agent.llm_model == "M"
        assert get_tools.call_args.args[3] == "M"
        assert assistant.llm_model_type == "M"
        assert request.llm_model == "X"
        assert request.model_fields_set == fields_before

    def test_root_resolution_does_not_write_back_onto_assistant(self):
        """Pick allowed, own model disallowed: the agent runs on the pick; the assistant keeps its own model."""
        assistant = self._assistant(own_model="O")
        request = AssistantChatRequest(text="hi", llm_model="M")

        agent, _ = self._build_agent(assistant, request)

        assert agent.llm_model == "M"
        assert assistant.llm_model_type == "O"
        assert request.llm_model == "M"

    def test_root_uses_conversation_model_when_request_has_none(self):
        """AC8 root: no llm_model in the request, the conversation stores M -> the agent runs on M."""
        assistant = self._assistant(own_model="D")
        request = AssistantChatRequest(text="hi", conversation_id="conv-1")

        agent, _ = self._build_agent(assistant, request, conversation=self._conversation("M"))

        assert agent.llm_model == "M"
        assert request.llm_model is None
        assert "llm_model" not in request.model_fields_set
        assert agent.kwargs["request"].llm_model is None

    def test_root_explicit_none_ignores_conversation_model(self):
        """An explicit llm_model=None keeps the conversation model out of the pick (as on main)."""
        assistant = self._assistant(own_model="D")
        request = AssistantChatRequest(text="hi", conversation_id="conv-1", llm_model=None)

        agent, _ = self._build_agent(assistant, request, conversation=self._conversation("M"))

        assert agent.llm_model == "D"


class TestCreateAssistantExecutorsFailureLog:
    """Swallowed sub-assistant failures are logged with their exception details."""

    @patch("codemie.service.tools.assistant_factory.logger")
    @patch("codemie.service.tools.assistant_factory.Assistant")
    @patch("codemie.service.tools.assistant_factory.AssistantFactory")
    def test_create_assistant_executors_logs_exception_details(
        self, mock_factory_class, mock_assistant_class, mock_logger
    ):
        from codemie.core.exceptions import ModelNotWhitelistedException

        error = ModelNotWhitelistedException("x", "proj", details="d")
        mock_assistant_class.get_by_ids.return_value = [Mock(id="assistant-1")]
        mock_factory_class.return_value.build.side_effect = error

        with patch(
            "codemie.service.tools.assistant_factory._validate_remote_entity_exists_and_cleanup", return_value=None
        ):
            result = create_assistant_executors(
                assistant_ids=["assistant-1"], request=Mock(sub_assistants_versions=None)
            )

        assert result == []
        logged = " ".join(str(c.args[0]) for c in mock_logger.error.call_args_list)
        assert "ModelNotWhitelistedException" in logged
        assert error.message in logged
