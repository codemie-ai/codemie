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
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.tools.base import ToolException

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.errors import TruncatedOutputError

EXECUTION_RESULT = "Execution Result"


class MockCodeMieTool(CodeMieTool):
    """Mock implementation of CodeMieTool for testing."""

    name: str = "ConcreteTool"
    description: str = "A concrete implementation of CodeMieTool for testing"

    def execute(self, *args, **kwargs):
        return EXECUTION_RESULT


@pytest.fixture
def mock_tool():
    """Fixture to create a mock CodeMieTool instance."""
    tool = MockCodeMieTool()
    tool.name = "TestTool"
    tool.base_llm_model_name = "gpt-4.1-mini"
    tool.tokens_size_limit = 100
    tool.throw_truncated_error = True
    tool.truncate_message = "Tool output is truncated."
    return tool


def test_run_success(mock_tool):
    with patch.object(mock_tool.__class__, 'execute', return_value=EXECUTION_RESULT) as mock_execute:
        result = mock_tool._run('arg1', kwarg1='value1')
        mock_execute.assert_called_once_with('arg1', kwarg1='value1')
        assert result == EXECUTION_RESULT


def test_run_exception(mock_tool):
    with patch.object(mock_tool.__class__, 'execute', side_effect=Exception("Test Exception")) as mock_execute:
        with pytest.raises(ToolException) as excinfo:
            mock_tool._run('arg1', kwarg1='value1')
        assert mock_execute.call_count == 1
        assert (
            str(excinfo.value)
            == "Error calling tool: TestTool with: \nArguments: {'kwarg1': 'value1'}. \nThe root cause is: 'Test Exception'"
        )
        mock_execute.assert_any_call('arg1', kwarg1='value1')


def test_run_exception_sanitized(mock_tool):
    with patch.object(mock_tool.__class__, 'execute', side_effect=Exception("Test Exception: PASSWORD: Secret123")):
        result = mock_tool.run('arg1', kwarg1='value1')
        assert (
            "Error calling tool: TestTool with: \nArguments: {}. \nThe root cause is: 'Test Exception: PASSWORD: ***'"
            in result
        )


@patch("codemie_tools.base.codemie_tool.get_encoding")
def test_output_within_token_limit(mock_encoding, mock_tool):
    """Test case where output is within the token limit."""
    mock_encoding.return_value.encode = MagicMock(return_value=["token"] * 50)
    mock_encoding.return_value.decode = MagicMock()

    output = "a" * 50
    result, token_count = mock_tool._limit_output_content(output)

    assert result == output
    assert token_count == 50
    mock_encoding.return_value.encode.assert_called_once_with(str(output))


@patch("codemie_tools.base.codemie_tool.get_encoding")
def test_output_exceeds_token_limit_no_error(mock_encoding, mock_tool):
    """Test case where output exceeds token limit but no error is raised."""
    mock_tool.throw_truncated_error = False
    mock_encoding.return_value.encode = MagicMock(return_value=["token"] * 150)
    mock_encoding.return_value.decode = MagicMock(return_value="truncated_data")

    output = "a" * 150
    result, token_count = mock_tool._limit_output_content(output)

    assert "Tool output is truncated." in result
    assert "Ratio limit/used_tokens: 0.666" in result
    assert token_count == 150
    mock_encoding.return_value.encode.assert_called_once_with(str(output))
    mock_encoding.return_value.decode.assert_called_once_with(["token"] * 100)


@patch("codemie_tools.base.codemie_tool.get_encoding")
def test_output_exceeds_token_limit_with_error(mock_encoding, mock_tool):
    """Test case where output exceeds token limit and an error is raised."""
    mock_encoding.return_value.encode = MagicMock(return_value=["token"] * 150)
    mock_encoding.return_value.decode = MagicMock(return_value="truncated_data")

    output = "a" * 150

    with pytest.raises(TruncatedOutputError) as exc_info:
        mock_tool._limit_output_content(output)

    assert "Tool output is truncated." in str(exc_info.value)
    assert "Ratio limit/used_tokens: 0.666" in str(exc_info.value)
    mock_encoding.return_value.encode.assert_called_once_with(str(output))
    mock_encoding.return_value.decode.assert_called_once_with(["token"] * 100)


@patch("codemie_tools.base.codemie_tool.get_encoding")
def test_empty_output(mock_encoding, mock_tool):
    """Test case with empty output."""
    mock_encoding.return_value.encode = MagicMock(return_value=[])
    mock_encoding.return_value.decode = MagicMock()

    output = ""
    result, token_count = mock_tool._limit_output_content(output)

    assert result == output
    assert token_count == 0
    mock_encoding.return_value.encode.assert_called_once_with(str(output))


@patch("codemie_tools.base.codemie_tool.get_encoding")
def test_large_output_truncation(mock_encoding, mock_tool):
    """Test case with large output that triggers truncation."""
    mock_encoding.return_value.encode = MagicMock(return_value=["token"] * 1000)
    mock_encoding.return_value.decode = MagicMock(return_value="truncated_large_output")

    output = "a" * 1000
    mock_tool.throw_truncated_error = False
    result, token_count = mock_tool._limit_output_content(output)

    assert "Tool output is truncated." in result
    assert "truncated_large_output" in result
    assert token_count == 1000
    mock_encoding.return_value.encode.assert_called_once_with(str(output))
    mock_encoding.return_value.decode.assert_called_once_with(["token"] * 100)


@patch("codemie_tools.base.codemie_tool.logger")
@patch("codemie_tools.base.codemie_tool.get_encoding")
def test_logging_when_output_exceeds_limit(mock_encoding, mock_logger, mock_tool):
    """
    Test case to ensure a logging statement appears when output exceeds token limit.
    """
    # Mock tiktoken encoding behavior
    mock_encoding.return_value.encode = MagicMock(return_value=["token"] * 150)
    mock_encoding.return_value.decode = MagicMock(return_value="truncated_data")

    output = "a" * 150
    mock_tool.throw_truncated_error = False

    mock_tool._limit_output_content(output)

    # Check that the error message was logged
    mock_logger.error.assert_called_once()
    error_call_args = mock_logger.error.call_args[0][0]
    assert "TestTool output is too long: 150 tokens." in error_call_args
    assert "Ratio limit/used_tokens:" in error_call_args


@patch("codemie_tools.base.codemie_tool.get_encoding")
def test_truncated_output_error_raised(mock_encoding, mock_tool):
    """
    Test case to ensure TruncatedOutputError is raised when throw_truncated_error is True.
    """
    # Mock tiktoken encoding behavior
    mock_encoding.return_value.encode = MagicMock(return_value=["token"] * 150)
    mock_encoding.return_value.decode = MagicMock(return_value="truncated_data")

    output = "a" * 150

    with pytest.raises(TruncatedOutputError) as exc_info:
        mock_tool._limit_output_content(output)

    # Check the error message
    assert "Tool output is truncated." in str(exc_info.value)
    assert "Ratio limit/used_tokens: 0.666" in str(exc_info.value)


class TestPostProcessOutputContent:
    """Tests for _post_process_output_content ensuring tool outputs are always strings.

    Background: LangChain >= 0.3.77 added "file" to TOOL_MESSAGE_BLOCK_TYPES.
    When a tool returns a list of dicts with "type": "file" (e.g. GitHub Contents API
    directory listing), LangChain/LangGraph treat it as valid content blocks and pass
    it through to the LLM without JSON-serialization. Azure OpenAI rejects "file"
    content blocks, causing a 400 error.
    """

    @pytest.fixture
    def tool(self):
        return MockCodeMieTool()

    def test_string_output_returned_as_is(self, tool):
        """String output should pass through unchanged."""
        output = "some text result"
        result = tool._post_process_output_content(output)
        assert result == "some text result"
        assert isinstance(result, str)

    def test_dict_output_serialized_to_json(self, tool):
        """Dict output (e.g. single GitHub file response) must be JSON-serialized."""
        output = {"type": "file", "name": "README.md", "size": 100}
        result = tool._post_process_output_content(output)
        assert isinstance(result, str)
        assert json.loads(result) == output

    def test_list_of_file_dicts_serialized_to_json(self, tool):
        """List of dicts with type=file (GitHub directory listing) must be JSON-serialized.

        This is the exact scenario that causes the Azure OpenAI 400 error:
        LangChain treats [{"type": "file", ...}] as valid content blocks
        and sends them to the API without serialization.
        """
        output = [
            {"type": "file", "name": "README.md", "path": "README.md", "size": 100},
            {"type": "file", "name": "main.py", "path": "main.py", "size": 200},
        ]
        result = tool._post_process_output_content(output)
        assert isinstance(result, str)
        assert json.loads(result) == output

    def test_list_of_mixed_dicts_serialized_to_json(self, tool):
        """Mixed list of dicts (files + dirs) must also be JSON-serialized."""
        output = [
            {"type": "file", "name": "README.md"},
            {"type": "dir", "name": "src"},
        ]
        result = tool._post_process_output_content(output)
        assert isinstance(result, str)
        assert json.loads(result) == output

    def test_list_of_strings_serialized_to_json(self, tool):
        """List of strings (e.g. file tree) must be JSON-serialized."""
        output = ["src/main.py", "src/utils.py", "README.md"]
        result = tool._post_process_output_content(output)
        assert isinstance(result, str)
        assert json.loads(result) == output

    def test_nested_dict_serialized_to_json(self, tool):
        """Nested dict structures must be JSON-serialized."""
        output = {"data": {"items": [1, 2, 3]}, "total": 3}
        result = tool._post_process_output_content(output)
        assert isinstance(result, str)
        assert json.loads(result) == output

    def test_empty_string_returned_as_is(self, tool):
        """Empty string should pass through unchanged."""
        result = tool._post_process_output_content("")
        assert result == ""
        assert isinstance(result, str)


class TestRunOutputAlwaysString:
    """Tests that _run() always returns a string regardless of execute() return type.

    This ensures tool outputs are never misinterpreted as LLM content blocks.
    """

    @pytest.fixture
    def tool(self):
        tool = MockCodeMieTool()
        tool.tokens_size_limit = 100_000
        tool.throw_truncated_error = False
        return tool

    @patch("codemie_tools.base.codemie_tool.get_encoding")
    def test_run_with_dict_execute_returns_string(self, mock_encoding, tool):
        """When execute() returns a dict, _run() must return a JSON string."""
        mock_encoding.return_value.encode = MagicMock(return_value=["t"] * 50)

        github_response = {"type": "file", "name": "README.md", "content": "decoded"}
        with patch.object(tool.__class__, "execute", return_value=github_response):
            result = tool._run()
        assert isinstance(result, str)
        assert json.loads(result) == github_response

    @patch("codemie_tools.base.codemie_tool.get_encoding")
    def test_run_with_file_list_execute_returns_string(self, mock_encoding, tool):
        """When execute() returns a list of file dicts, _run() must return a JSON string.

        This is the root cause of the Azure OpenAI 400 error.
        """
        mock_encoding.return_value.encode = MagicMock(return_value=["t"] * 50)

        github_dir_listing = [
            {"type": "file", "name": "README.md", "size": 100},
            {"type": "file", "name": "setup.py", "size": 200},
        ]
        with patch.object(tool.__class__, "execute", return_value=github_dir_listing):
            result = tool._run()
        assert isinstance(result, str)
        assert json.loads(result) == github_dir_listing
