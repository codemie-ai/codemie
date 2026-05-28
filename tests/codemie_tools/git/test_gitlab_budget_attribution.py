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

from unittest.mock import Mock, patch
import pytest

from codemie_tools.git.gitlab.gitlab_openai_tools import OpenAIUpdateFileDiffTool, OpenAIUpdateFileWholeTool
from codemie_tools.git.gitlab.gitlab_toolkit import CustomGitLabToolkit
from codemie_tools.git.utils import GitCredentials
from codemie.core.constants import REQUEST_ID


def test_update_file_diff_tool_creates_llm_at_execution_time():
    """Test that OpenAIUpdateFileDiffTool creates LLM at execution time using get_llm_by_credentials."""
    # Arrange
    credentials = GitCredentials(
        repo_type="gitlab", base_branch="main", repo_link="https://gitlab.com/test/repo", token="token"
    )

    tool = OpenAIUpdateFileDiffTool(
        api_wrapper=None,
        credentials=credentials,
        llm_model="gpt-4",  # String model name
    )
    tool.metadata = {REQUEST_ID: "test-request-123"}

    mock_llm = Mock()

    # Act & Assert
    with patch('codemie_tools.git.gitlab.gitlab_openai_tools.get_llm_by_credentials') as mock_get_llm:
        with patch('codemie_tools.git.gitlab.gitlab_openai_tools.update_content_by_task') as mock_update:
            mock_get_llm.return_value = mock_llm
            mock_update.return_value = ("new content", "edits")

            tool.update_content("old content", "task details")

            # Verify get_llm_by_credentials was called with correct params
            mock_get_llm.assert_called_once_with(llm_model="gpt-4", request_id="test-request-123", streaming=False)

            # Verify update_content_by_task received the freshly created LLM
            mock_update.assert_called_once_with("old content", "task details", mock_llm)


def test_update_file_diff_tool_handles_missing_request_id():
    """Test that tool handles missing request_id gracefully."""
    credentials = GitCredentials(
        repo_type="gitlab", base_branch="main", repo_link="https://gitlab.com/test/repo", token="token"
    )

    tool = OpenAIUpdateFileDiffTool(api_wrapper=None, credentials=credentials, llm_model="gpt-4")
    tool.metadata = None  # No metadata

    mock_llm = Mock()

    with patch('codemie_tools.git.gitlab.gitlab_openai_tools.get_llm_by_credentials') as mock_get_llm:
        with patch('codemie_tools.git.gitlab.gitlab_openai_tools.update_content_by_task') as mock_update:
            mock_get_llm.return_value = mock_llm
            mock_update.return_value = ("new content", "edits")

            tool.update_content("old content", "task details")

            # Should pass None for request_id
            mock_get_llm.assert_called_once_with(llm_model="gpt-4", request_id=None, streaming=False)


def test_update_file_whole_tool_creates_llm_at_execution_time():
    """Test that OpenAIUpdateFileWholeTool creates LLM in _chain property."""
    # Arrange
    credentials = GitCredentials(
        repo_type="gitlab", base_branch="main", repo_link="https://gitlab.com/test/repo", token="token"
    )

    tool = OpenAIUpdateFileWholeTool(
        api_wrapper=None,
        credentials=credentials,
        llm_model="gpt-4",  # String model name
    )
    tool.metadata = {REQUEST_ID: "test-request-456"}

    mock_llm = Mock()
    mock_llm.invoke = Mock(return_value="new content")

    # Act & Assert
    with patch('codemie_tools.git.gitlab.gitlab_openai_tools.get_llm_by_credentials') as mock_get_llm:
        mock_get_llm.return_value = mock_llm

        # Access _chain property which should create LLM
        chain = tool._chain

        # Verify get_llm_by_credentials was called
        mock_get_llm.assert_called_once_with(llm_model="gpt-4", request_id="test-request-456", streaming=False)

        # Verify chain was created with the LLM
        assert chain is not None


def test_update_file_diff_tool_raises_when_llm_model_is_none():
    """OpenAIUpdateFileDiffTool must raise ValueError on update_content when llm_model is None."""
    credentials = GitCredentials(
        repo_type="gitlab",
        base_branch="main",
        repo_link="https://gitlab.com/test/repo",
        token="token",
    )

    tool = OpenAIUpdateFileDiffTool(
        api_wrapper=None,
        credentials=credentials,
        # llm_model omitted → defaults to None
    )

    with pytest.raises(ValueError, match="LLM model is required for this tool but was not configured"):
        tool.update_content("old content", "task details")


def test_update_file_whole_tool_raises_when_llm_model_is_none():
    """OpenAIUpdateFileWholeTool must raise ValueError in _chain property when llm_model is None."""
    credentials = GitCredentials(
        repo_type="gitlab",
        base_branch="main",
        repo_link="https://gitlab.com/test/repo",
        token="token",
    )

    tool = OpenAIUpdateFileWholeTool(
        api_wrapper=None,
        credentials=credentials,
        # llm_model omitted → defaults to None
    )

    with pytest.raises(ValueError, match="LLM model is required for this tool but was not configured"):
        _ = tool._chain


def test_gitlab_toolkit_extracts_model_name_from_string():
    """Test toolkit handles string model name and stores it as a string."""
    configs = {
        "repo_type": "gitlab",
        "base_branch": "main",
        "repo_link": "https://gitlab.com/test/repo",
        "token": "token",
    }

    toolkit = CustomGitLabToolkit.get_toolkit(configs=configs, llm_model="gpt-4")

    assert toolkit.llm_model == "gpt-4"

    # Verify the diff tool receives the string model name
    diff_tool = OpenAIUpdateFileDiffTool(
        api_wrapper=None,
        credentials=toolkit.git_creds,
        llm_model=toolkit.llm_model,
    )
    assert diff_tool.llm_model == "gpt-4"


def test_gitlab_toolkit_extracts_model_name_from_llm_instance():
    """Test toolkit extracts model name from LLM instance."""
    configs = {
        "repo_type": "gitlab",
        "base_branch": "main",
        "repo_link": "https://gitlab.com/test/repo",
        "token": "token",
    }

    mock_llm = Mock()
    mock_llm.model_name = "gpt-4-turbo"

    toolkit = CustomGitLabToolkit.get_toolkit(configs=configs, llm_model=mock_llm)

    assert toolkit.llm_model == "gpt-4-turbo"

    # Verify the diff tool receives the extracted string model name
    diff_tool = OpenAIUpdateFileDiffTool(
        api_wrapper=None,
        credentials=toolkit.git_creds,
        llm_model=toolkit.llm_model,
    )
    assert diff_tool.llm_model == "gpt-4-turbo"


def test_gitlab_toolkit_handles_llm_with_deployment_name():
    """Test toolkit extracts deployment_name if model_name not available."""
    configs = {
        "repo_type": "gitlab",
        "base_branch": "main",
        "repo_link": "https://gitlab.com/test/repo",
        "token": "token",
    }

    mock_llm = Mock()
    mock_llm.model_name = None
    mock_llm.deployment_name = "gpt-4-deployment"

    toolkit = CustomGitLabToolkit.get_toolkit(configs=configs, llm_model=mock_llm)

    assert toolkit.llm_model == "gpt-4-deployment"
