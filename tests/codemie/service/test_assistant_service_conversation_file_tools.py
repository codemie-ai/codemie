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
from codemie_tools.file_analysis.toolkit import FileAnalysisToolkit
from codemie_tools.vision.toolkit import VisionToolkit
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.file_service.file_service import FileService

from codemie.core.models import ChatRole
from codemie_tools.base.file_object import FileObject
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage
from codemie.rest_api.security.user import User
from codemie.service.tools import ToolkitService


@pytest.fixture
def mock_user():
    """Returns a mock User object."""
    user = MagicMock(spec=User)
    user.id = "test_user_id"
    user.name = "Test User"
    return user


@pytest.fixture
def mock_assistant():
    """Returns a mock Assistant object."""
    assistant = MagicMock(spec=Assistant)
    assistant.id = "assistant_id"
    assistant.name = "Test Assistant"
    assistant.project = "TestProject"
    assistant.llm_model_type = "gpt-4-turbo"
    return assistant


def test_add_conversation_file_tools():
    """
    Tests that add_file_tools correctly processes files mentioned in conversation history
    when they are also mentioned in the current request text.
    """
    # Setup fixtures
    assistant = MagicMock(spec=Assistant)
    assistant.id = "assistant_id"
    assistant.name = "Test Assistant"
    assistant.llm_model_type = "gpt-4-turbo"

    # Create request with conversation_id and a current file
    current_file_name = "current_file.csv"
    # No need to create request object since we directly pass file_objects

    request_uuid = "test-uuid-456"

    # Setup mock conversation with history
    mock_conversation = MagicMock(spec=Conversation)

    # Create history with different message types
    historical_file_name = "historical_file.pdf"
    unmentioned_file_name = "unmentioned_file.pptx"

    mock_conversation.history = [
        GeneratedMessage(file_names=[historical_file_name], role=ChatRole.ASSISTANT),
        GeneratedMessage(file_names=[unmentioned_file_name], role=ChatRole.ASSISTANT),
        GeneratedMessage(text="normal message", role=ChatRole.USER.value),
    ]

    # Setup mock file objects
    mock_current_file = MagicMock()
    mock_current_file.name = "current_file.csv"
    mock_current_file.is_image = lambda: False

    mock_historical_file = MagicMock()
    mock_historical_file.name = "historical_file.pdf"
    mock_historical_file.is_image = lambda: False

    # Mock file object creation from encoded URL
    mock_historical_file_object = MagicMock(spec=FileObject)
    mock_historical_file_object.name = "historical_file.pdf"
    mock_historical_file_object.is_image = lambda: False

    mock_unmentioned_file_object = MagicMock(spec=FileObject)
    mock_unmentioned_file_object.name = "unmentioned_file.pptx"
    mock_unmentioned_file_object.is_image = lambda: False

    # Define tools that will be returned by add_file_tools
    current_file_tools = ["file_analysis_tool_1"]
    # historical_file_tools is defined but unused

    # Apply patches
    with (
        patch('codemie.rest_api.models.conversation.Conversation.find_by_id', return_value=mock_conversation),
        patch('codemie.repository.base_file_repository.FileObject.from_encoded_url') as mock_from_encoded_url,
        patch.object(
            FileService, 'get_file_object', side_effect=lambda file_name: mock_current_file
        ),  # no need to capture this mock
        patch(
            'codemie.core.utils.build_unique_file_objects',
            return_value={
                current_file_name: mock_current_file,
                historical_file_name: mock_historical_file_object,
            },
        ),
        patch.object(VisionToolkit, 'get_toolkit') as mock_vision_toolkit,
        patch.object(FileAnalysisToolkit, 'get_toolkit') as mock_file_toolkit,
        patch.object(llm_service, 'get_llm_deployment_name', return_value='gpt-4.1'),  # no need to capture this mock
        patch.object(llm_service, 'get_multimodal_llms', return_value=[]),  # no need to capture this mock
    ):
        # Mock from_encoded_url to return appropriate objects
        mock_from_encoded_url.side_effect = lambda url: {
            current_file_name: mock_current_file,
            historical_file_name: mock_historical_file_object,
            unmentioned_file_name: mock_unmentioned_file_object,
        }.get(url)

        # Setup mock toolkits
        mock_file_toolkit_instance = MagicMock()
        mock_file_toolkit_instance.get_tools.return_value = current_file_tools
        mock_file_toolkit.return_value = mock_file_toolkit_instance

        mock_vision_toolkit_instance = MagicMock()
        mock_vision_toolkit_instance.get_tools.return_value = [
            "file_analysis_tool_2"
        ]  # Use explicit value instead of removed variable
        mock_vision_toolkit.return_value = mock_vision_toolkit_instance

        # Call method under test
        tools = ToolkitService.add_file_tools(
            assistant=assistant,
            file_objects=[mock_current_file, mock_historical_file_object],
            request_uuid=request_uuid,
        )

        # Verify results
        # In the new implementation, the unmentioned file might also get processed
        expected_tools = set(
            current_file_tools + ["file_analysis_tool_2"]
        )  # Use explicit value instead of removed variable
        assert set(tools).issubset(expected_tools)

        # We no longer need to verify _build_unique_file_objects as we're passing file_objects directly

        # Verify the toolkits were called
        assert mock_file_toolkit.call_count >= 1
        # For this specific test, vision toolkit might not be called because files aren't images


def test_add_conversation_file_tools_empty_history():
    """Tests add_file_tools with an empty conversation history."""
    assistant = MagicMock(spec=Assistant)
    current_file_name = "current_file.csv"
    # No need to create request object since we directly pass file_objects

    request_uuid = "test-uuid-456"

    mock_conversation = MagicMock(spec=Conversation)
    mock_conversation.history = []  # Empty history

    mock_current_file = MagicMock()
    mock_current_file.name = current_file_name
    mock_current_file.is_image = lambda: False

    current_file_tools = ["file_analysis_tool_1"]

    with (
        patch('codemie.rest_api.models.conversation.Conversation.find_by_id', return_value=mock_conversation),
        patch.object(
            FileService, 'get_file_object', side_effect=lambda file_name: mock_current_file
        ),  # no need to capture this mock
        patch('codemie.core.utils.build_unique_file_objects', return_value={current_file_name: mock_current_file}),
        patch.object(FileAnalysisToolkit, 'get_toolkit') as mock_file_toolkit,
        patch.object(llm_service, 'get_llm_deployment_name', return_value='gpt-4.1'),  # no need to capture this mock
        patch.object(llm_service, 'get_multimodal_llms', return_value=[]),  # no need to capture this mock
    ):
        # Setup mock toolkit
        mock_file_toolkit_instance = MagicMock()
        mock_file_toolkit_instance.get_tools.return_value = current_file_tools
        mock_file_toolkit.return_value = mock_file_toolkit_instance

        tools = ToolkitService.add_file_tools(
            assistant=assistant, file_objects=[mock_current_file], request_uuid=request_uuid
        )

        # Only the current file should be processed
        assert len(tools) == len(current_file_tools)
        assert tools == current_file_tools
        # No longer need to verify _build_unique_file_objects
        # Check that the file toolkit was called
        assert mock_file_toolkit.call_count >= 1


def test_add_conversation_file_tools_no_file_references():
    """Tests add_file_tools with a conversation history that contains no file references."""
    assistant = MagicMock(spec=Assistant)
    current_file_name = "current_file.csv"
    # No need to create request object since we directly pass file_objects

    request_uuid = "test-uuid-456"

    mock_conversation = MagicMock(spec=Conversation)
    mock_conversation.history = [
        GeneratedMessage(text="This is a message", role=ChatRole.USER),
        GeneratedMessage(text="This is another message", role=ChatRole.USER),
    ]

    mock_current_file = MagicMock()
    mock_current_file.name = current_file_name
    mock_current_file.is_image = lambda: False

    current_file_tools = ["file_analysis_tool_1"]

    with (
        patch('codemie.rest_api.models.conversation.Conversation.find_by_id', return_value=mock_conversation),
        patch.object(
            FileService, 'get_file_object', side_effect=lambda file_name: mock_current_file
        ),  # no need to capture this mock
        patch('codemie.core.utils.build_unique_file_objects', return_value={current_file_name: mock_current_file}),
        patch.object(FileAnalysisToolkit, 'get_toolkit') as mock_file_toolkit,
        patch.object(llm_service, 'get_llm_deployment_name', return_value='gpt-4.1'),  # no need to capture this mock
        patch.object(llm_service, 'get_multimodal_llms', return_value=[]),  # no need to capture this mock
    ):
        # Setup mock toolkit
        mock_file_toolkit_instance = MagicMock()
        mock_file_toolkit_instance.get_tools.return_value = current_file_tools
        mock_file_toolkit.return_value = mock_file_toolkit_instance

        tools = ToolkitService.add_file_tools(
            assistant=assistant, file_objects=[mock_current_file], request_uuid=request_uuid
        )

        # Only the current file should be processed
        assert len(tools) == len(current_file_tools)
        assert tools == current_file_tools
        # No longer need to verify _build_unique_file_objects
        # Check that the file toolkit was called
        assert mock_file_toolkit.call_count >= 1


def test_add_conversation_file_tools_no_mentions_in_request():
    """Tests add_file_tools when the request text doesn't mention historical files."""
    assistant = MagicMock(spec=Assistant)
    current_file_name = "current_file.csv"
    historical_file_name = "historical_file.pdf"
    # No need to create request object since we directly pass file_objects

    request_uuid = "test-uuid-456"

    mock_conversation = MagicMock(spec=Conversation)
    mock_conversation.history = [
        GeneratedMessage(file_names=[historical_file_name], role=ChatRole.ASSISTANT),
        GeneratedMessage(text="normal message", role=ChatRole.USER),
    ]

    mock_current_file = MagicMock()
    mock_current_file.name = current_file_name
    mock_current_file.is_image = lambda: False

    mock_historical_file = MagicMock()
    mock_historical_file.name = historical_file_name
    mock_historical_file.is_image = lambda: False

    mock_historical_file_object = MagicMock(spec=FileObject)
    mock_historical_file_object.name = historical_file_name
    mock_historical_file_object.is_image = lambda: False

    current_file_tools = ["file_analysis_tool_1"]
    # historical_file_tools is defined but unused

    with (
        patch('codemie.rest_api.models.conversation.Conversation.find_by_id', return_value=mock_conversation),
        patch.object(
            FileService, 'get_file_object', side_effect=lambda file_name: mock_current_file
        ),  # no need to capture this mock
        patch('codemie.core.utils.build_unique_file_objects', return_value={current_file_name: mock_current_file}),
        patch.object(FileAnalysisToolkit, 'get_toolkit') as mock_file_toolkit,
        patch.object(llm_service, 'get_llm_deployment_name', return_value='gpt-4.1'),  # no need to capture this mock
        patch.object(llm_service, 'get_multimodal_llms', return_value=[]),  # no need to capture this mock
    ):
        # Setup mock toolkit
        mock_file_toolkit_instance = MagicMock()
        mock_file_toolkit_instance.get_tools.return_value = current_file_tools
        mock_file_toolkit.return_value = mock_file_toolkit_instance

        tools = ToolkitService.add_file_tools(
            assistant=assistant,
            file_objects=[mock_current_file, mock_historical_file_object],
            request_uuid=request_uuid,
        )

        # With the new implementation, we expect the current file to be processed
        assert len(tools) == len(current_file_tools)
        assert tools == current_file_tools
        # No longer need to verify _build_unique_file_objects


def test_add_conversation_file_tools_duplicate_mentions():
    """Tests add_file_tools when a file is mentioned multiple times in history."""
    assistant = MagicMock(spec=Assistant)
    current_file_name = "current_file.csv"
    historical_file_name = "historical_file.pdf"
    # No need to create request object since we directly pass file_objects
    request_uuid = "test-uuid-456"

    # Create history with duplicate file references
    mock_conversation = MagicMock(spec=Conversation)
    mock_conversation.history = [
        GeneratedMessage(file_names=[historical_file_name], role=ChatRole.ASSISTANT),
        GeneratedMessage(file_names=[historical_file_name], role=ChatRole.ASSISTANT),  # Duplicate
        GeneratedMessage(text="normal message", role=ChatRole.USER.value),
    ]

    mock_current_file = MagicMock()
    mock_current_file.name = current_file_name
    mock_current_file.is_image = lambda: False

    mock_historical_file = MagicMock()
    mock_historical_file.name = historical_file_name
    mock_historical_file.is_image = lambda: False

    mock_historical_file_object = MagicMock(spec=FileObject)
    mock_historical_file_object.name = historical_file_name
    mock_historical_file_object.is_image = lambda: False

    current_file_tools = ["file_analysis_tool_1"]
    # historical_file_tools is defined but unused

    with (
        patch('codemie.rest_api.models.conversation.Conversation.find_by_id', return_value=mock_conversation),
        patch.object(
            FileService,
            'get_file_object',
            side_effect=lambda file_name: {
                historical_file_name: mock_historical_file_object,
                current_file_name: mock_current_file,
            }.get(file_name, mock_current_file),
        ),  # no need to capture this mock
        patch(
            'codemie.core.utils.build_unique_file_objects',
            return_value={
                current_file_name: mock_current_file,
                historical_file_name: mock_historical_file_object,
            },
        ),
        patch.object(FileAnalysisToolkit, 'get_toolkit') as mock_file_toolkit,
        patch.object(llm_service, 'get_llm_deployment_name', return_value='gpt-4.1'),  # no need to capture this mock
        patch.object(llm_service, 'get_multimodal_llms', return_value=[]),  # no need to capture this mock
    ):
        # Setup mock toolkit
        mock_file_toolkit_instance = MagicMock()
        mock_file_toolkit_instance.get_tools.return_value = current_file_tools
        mock_file_toolkit.return_value = mock_file_toolkit_instance

        tools = ToolkitService.add_file_tools(
            assistant=assistant,
            file_objects=[mock_current_file, mock_historical_file_object],
            request_uuid=request_uuid,
        )

        # With the updated implementation, file objects are uniquely identified by name
        # Each unique file will be processed only once, regardless of duplicates in history
        assert tools == current_file_tools

        # Verify that FileAnalysisToolkit was called
        assert mock_file_toolkit.call_count >= 1


def test_add_conversation_file_tools_missing_conversation_id():
    """Tests add_file_tools with a missing conversation ID."""
    assistant = MagicMock(spec=Assistant)
    current_file_name = "current_file.csv"
    # No need to create request object since we directly pass file_objects
    request_uuid = "test-uuid-456"

    mock_current_file = MagicMock()
    mock_current_file.name = current_file_name
    mock_current_file.is_image = lambda: False

    current_file_tools = ["file_analysis_tool_1"]

    with (
        patch('codemie.rest_api.models.conversation.Conversation.find_by_id') as mock_find_by_id,
        patch.object(
            FileService, 'get_file_object', side_effect=lambda file_name: mock_current_file
        ),  # no need to capture this mock
        patch('codemie.core.utils.build_unique_file_objects', return_value={current_file_name: mock_current_file}),
        patch.object(FileAnalysisToolkit, 'get_toolkit') as mock_file_toolkit,
        patch.object(llm_service, 'get_llm_deployment_name', return_value='gpt-4.1'),  # no need to capture this mock
        patch.object(llm_service, 'get_multimodal_llms', return_value=[]),  # no need to capture this mock
    ):
        # Setup mock toolkit
        mock_file_toolkit_instance = MagicMock()
        mock_file_toolkit_instance.get_tools.return_value = current_file_tools
        mock_file_toolkit.return_value = mock_file_toolkit_instance

        tools = ToolkitService.add_file_tools(
            assistant=assistant, file_objects=[mock_current_file], request_uuid=request_uuid
        )

        # Only the current file should be processed
        assert len(tools) == len(current_file_tools)
        assert tools == current_file_tools
        mock_find_by_id.assert_not_called()  # find_by_id should not be called
        # Check that the file toolkit was called
        assert mock_file_toolkit.call_count >= 1
