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

from inspect import signature

import pytest
from unittest.mock import patch, Mock, MagicMock
from typing import Dict, Any, List, Generator
from langchain_core.tools import ToolException

from codemie.agents.tools.code.tools_models import SearchInput
from codemie.agents.utils import parse_tool_input, to_snake_case, render_text_description_and_args
from codemie.agents.utils import handle_agent_exception, _extract_budget_message, _emit_llm_error_log
from codemie.core.errors import ErrorCode
from codemie.core.litellm_error_constants import LITELLM_ERROR_FRIENDLY_MESSAGES
from codemie.core.models import CodeFields
from codemie.agents.utils import (
    OPEN_AI_TOOL_NAME_LIMIT,
    adapt_tool_name,
    generate_tool_hash,
    get_repo_files_by_search_phrase_path,
    get_repo_tree,
)
from codemie.core.constants import CodeIndexType
from codemie.configs import config


@pytest.fixture
def code_fields():
    return CodeFields(
        repo_name='codemie',
        branch_name='main',
        app_name='codemie',
        index_type=CodeIndexType.CODE,
        llm_credentials={'api_key': 'test-key'},
    )


class TestUtils:
    def test_parse_tool_input_str(self, code_fields):
        input_str = "{\"keywords_list\": [\"test\"], \"file_path\": [\"test\"], \"query\": \"example query\"}"
        result = parse_tool_input(SearchInput, input_str)
        expected_result = SearchInput(query='example query', file_path=['test'], keywords_list=['test'])
        assert result == expected_result.dict(), 'Failed to parse input string to dict'

    def test_parse_tool_input_dict(self, code_fields):
        input_dict = {'keywords_list': ['test'], 'file_path': ['test'], 'query': 'example query'}
        result = parse_tool_input(SearchInput, input_dict)
        expected_result = SearchInput(query='example query', file_path=['test'], keywords_list=['test'])
        assert result == expected_result.dict(), 'Failed to parse input dict correctly'

    def test_parse_tool_input_invalid(self, code_fields):
        with pytest.raises(ToolException):
            parse_tool_input(SearchInput, 'invalid input')

    def test_get_repo_tree(self, code_fields):
        with (
            patch('codemie.agents.utils.get_indexed_repo') as mocked_get_indexed_repo,
            patch('codemie.agents.utils.ElasticSearchClient.get_client') as mocked_get_client,
        ):
            mocked_get_indexed_repo.return_value.get_identifier.return_value = 'test-index'
            mocked_es_instance = mocked_get_client.return_value
            mocked_es_instance.search.return_value = {
                'hits': {
                    'hits': [
                        {'_source': {'metadata': {'file_path': 'src/main.py'}}},
                        {'_source': {'metadata': {'file_path': 'src/base_tools.py'}}},
                    ]
                }
            }
            result = get_repo_tree(code_fields)
            assert sorted(result) == sorted(['src/main.py', 'src/base_tools.py']), 'Failed to get repo tree'

    def test_generate_tool_hash_with_long_string(self):
        input_string = "This is a very long string that should generate a different hash value"
        result = generate_tool_hash(input_string)
        assert result != "0"

    def test_generate_tool_hash_with_special_characters(self):
        input_string = "!@#$%^&*()_+{}|:<>?[]\\;',./"
        result = generate_tool_hash(input_string)
        assert isinstance(result, str)

    def test_generate_tool_hash_with_numbers(self):
        input_string = "12345678901234567890"
        result = generate_tool_hash(input_string)
        assert len(result) <= 8

    def test_generate_tool_hash_with_unicode_characters(self):
        input_string = "こんにちは世界"
        result = generate_tool_hash(input_string)
        assert isinstance(result, str)

    def test_adapt_tool_name_with_short_input(self):
        template = "tool_{}"
        alias = "short_alias"
        tool_name = adapt_tool_name(template, alias)
        assert tool_name == "tool_short_alias"

    def test_adapt_tool_name_with_long_input(self):
        template = "tool_{}"
        alias = "a" * 100
        tool_name = adapt_tool_name(template, alias)
        assert len(tool_name) <= OPEN_AI_TOOL_NAME_LIMIT

    def test_generate_tool_hash_with_valid_input(self):
        input_string = "test_repo"
        repo_name = generate_tool_hash(input_string)
        assert isinstance(repo_name, str)

    def test_generate_tool_hash_with_valid_input_hash(self):
        input_string = "test_tool"
        tool_hash = generate_tool_hash(input_string)
        assert isinstance(tool_hash, str)
        assert len(tool_hash) <= 8

    def test_to_snake_case(self):
        assert to_snake_case('test string') == 'test_string', 'Failed to convert space separated string to snake case'
        assert to_snake_case('test-string') == 'test_string', 'Failed to convert hyphen separated string to snake case'

    def test_single_tool_with_execute(self):
        # Mock a tool with execute method
        tool = Mock()
        tool.name = "ToolA"
        tool.description = "Description of ToolA"
        tool.args = {"param1": "value1"}

        def mock_execute(param1):
            pass

        tool.execute = mock_execute

        expected_signature = str(signature(mock_execute))
        expected_output = (
            f"Tool Name: ToolA{expected_signature}\n"
            f"Tool Description: Description of ToolA\n"
            f"Tool Arguments: {{'param1': 'value1'}}\n"
        )

        result = render_text_description_and_args([tool])
        assert result == expected_output

    def test_single_tool_without_execute(self):
        # Mock a tool without execute method
        tool = Mock()
        tool.name = "ToolB"
        tool.description = "Description of ToolB"
        tool.args = {"param2": "value2"}
        tool.execute = None

        expected_output = (
            "Tool Name: ToolB\nTool Description: Description of ToolB\nTool Arguments: {'param2': 'value2'}\n"
        )

        result = render_text_description_and_args([tool])
        assert result == expected_output

    def test_multiple_tools(self):
        # Mock multiple tools
        tool1 = Mock()
        tool1.name = "ToolA"
        tool1.description = "Description of ToolA"
        tool1.args = {"param1": "value1"}

        def mock_execute1(param1):
            pass

        tool1.execute = mock_execute1

        tool2 = Mock()
        tool2.name = "ToolB"
        tool2.description = "Description of ToolB"
        tool2.args = {"param2": "value2"}
        tool2.execute = None

        expected_signature1 = str(signature(mock_execute1))

        expected_output = (
            f"Tool Name: ToolA{expected_signature1}\n"
            f"Tool Description: Description of ToolA\n"
            f"Tool Arguments: {{'param1': 'value1'}}\n\n"
            f"Tool Name: ToolB\n"
            f"Tool Description: Description of ToolB\n"
            f"Tool Arguments: {{'param2': 'value2'}}\n"
        )

        result = render_text_description_and_args([tool1, tool2])
        assert result == expected_output

    def test_empty_tools_list(self):
        # Test with an empty list
        expected_output = ""
        result = render_text_description_and_args([])
        assert result == expected_output


@pytest.fixture
def mock_es_search():
    with patch('codemie.clients.elasticsearch.ElasticSearchClient.get_client') as mock:
        yield mock


@pytest.fixture
def mock_get_indexed_repo() -> Generator[MagicMock, None, None]:
    with patch('codemie.agents.utils.get_indexed_repo') as mock:
        mock.return_value.get_identifier.return_value = "mock_index_name"
        yield mock


@pytest.fixture
def default_hits() -> List[Dict[str, Any]]:
    return [
        {
            '_source': {
                'text': 'example text 1',
                'metadata': {
                    'source': 'path1 #1',
                    'file_path': 'path1',
                    'file_name': 'file1',
                },
            }
        },
        {  # DUPLICATE 1
            '_source': {
                'text': 'example text 1',
                'metadata': {
                    'source': 'path1 #1',
                    'file_path': 'path1',
                    'file_name': 'file1',
                },
            }
        },
        {
            '_source': {
                'text': 'example text 2',
                'metadata': {
                    'source': 'path1 #2',
                    'file_path': 'path1',
                    'file_name': 'file1',
                },
            }
        },
        {  # DUPLICATE 2
            '_source': {
                'text': 'example text 2',
                'metadata': {
                    'source': 'path1 #2',
                    'file_path': 'path1',
                    'file_name': 'file1',
                },
            }
        },
        {
            '_source': {
                'text': 'example text',
                'metadata': {
                    'source': 'path2',
                    'file_path': 'path2',
                    'file_name': 'file2',
                },
            }
        },
        {
            '_source': {
                'text': 'example text 5',
                'metadata': {'source': 'path3', 'file_path': 'path3', 'file_name': 'file3', "chunk_num": 1},
            }
        },
        {
            '_source': {
                'text': 'example text 6',
                'metadata': {'source': 'path3', 'file_path': 'path3', 'file_name': 'file3', "chunk_num": 2},
            }
        },
        {  # DUPLICATE #3
            '_source': {
                'text': 'example text 6',
                'metadata': {'source': 'path3', 'file_path': 'path3', 'file_name': 'file3', "chunk_num": 2},
            }
        },
    ]


@pytest.fixture
def es_search_response():
    def _es_search_response(hits):
        return {'hits': {'hits': hits}}

    return _es_search_response


def test_get_repo_files_by_search_phrase_path_reduces_duplications(
    request, code_fields, mock_get_indexed_repo, mock_es_search, es_search_response, default_hits
) -> None:
    mock_es_client = mock_es_search.return_value
    mock_es_client.search.return_value = es_search_response(default_hits)
    expected_result = [
        {
            "text": "example text 1",
            "source": "path1 #1",
            "file_path": "path1",
            "file_name": "file1",
            "unique_key": "path1 #1",
        },
        {
            "text": "example text 2",
            "source": "path1 #2",
            "file_path": "path1",
            "file_name": "file1",
            "unique_key": "path1 #2",
        },
        {"text": "example text", "source": "path2", "file_path": "path2", "file_name": "file2", "unique_key": "path2"},
        {
            "text": "example text 5",
            "source": "path3",
            "file_path": "path3",
            "file_name": "file3",
            "unique_key": "path31",
        },
        {
            "text": "example text 6",
            "source": "path3",
            "file_path": "path3",
            "file_name": "file3",
            "unique_key": "path32",
        },
    ]

    result = get_repo_files_by_search_phrase_path(code_fields, "dummy_path")
    assert len(result) == len(default_hits) - 3
    assert result == expected_result


# ---------------------------------------------------------------------------
# handle_agent_exception / _extract_budget_message / _emit_llm_error_log
#
# These tests validate the core error-handling flow from the Jira ticket:
#   exception → classification → (user_message, error_code) + structured log
# ---------------------------------------------------------------------------
class TestHandleAgentExceptionEndToEnd:
    """End-to-end tests: real litellm exceptions flow through the full chain
    (is_litellm_exception → classify → friendly message + error code + structured log).

    Each test corresponds to a Jira ticket scenario.
    """

    @pytest.fixture(autouse=True)
    def _setup(self):
        try:
            import litellm.exceptions  # noqa: F401
        except ImportError:
            pytest.skip("litellm not installed")
        with patch.object(config, "HIDE_AGENT_STREAMING_EXCEPTIONS", False):
            yield

    @patch("codemie.agents.utils.send_log_metric")
    @patch("codemie.agents.utils.logging_user_id")
    def test_budget_exceeded_returns_code_and_friendly_message(self, mock_uid, mock_metric):
        from litellm.exceptions import BudgetExceededError

        mock_uid.get.return_value = "u1"
        exc = BudgetExceededError(current_cost=15.0, max_budget=10.0)

        user_message, error_code = handle_agent_exception(exc)

        assert error_code == "llm_budget_exceeded"
        # Verify base message is present
        assert LITELLM_ERROR_FRIENDLY_MESSAGES[ErrorCode.LLM_BUDGET_EXCEEDED] in user_message
        # Verify enriched budget details are present
        assert "Budget has been exceeded: Your current spending: 15.0, available budget: 10.0" in user_message
        mock_metric.assert_called_once()
        attrs = mock_metric.call_args[0][1]
        assert attrs["llm_error_code"] == "llm_budget_exceeded"

    @patch("codemie.agents.utils.send_log_metric")
    @patch("codemie.agents.utils.logging_user_id")
    def test_budget_exceeded_returns_universal_message_when_hide_flag_on(self, mock_uid, mock_metric):
        from litellm.exceptions import BudgetExceededError

        mock_uid.get.return_value = "u1"
        exc = BudgetExceededError(current_cost=15.0, max_budget=10.0)

        with patch.object(config, "HIDE_AGENT_STREAMING_EXCEPTIONS", True):
            user_message, error_code = handle_agent_exception(exc)

        assert error_code == "llm_budget_exceeded"
        assert user_message == config.CODEMIE_SUPPORT_MSG

    @patch("codemie.agents.utils.send_log_metric")
    @patch("codemie.agents.utils.logging_user_id")
    def test_rate_limit_tpm(self, mock_uid, mock_metric):
        from litellm.exceptions import RateLimitError

        mock_uid.get.return_value = "u1"
        exc = RateLimitError(
            "tokens per minute limit exceeded", model="gpt-4", llm_provider="openai", response=MagicMock()
        )

        user_message, error_code = handle_agent_exception(exc)

        assert error_code == "llm_tpm_limit"
        assert user_message == LITELLM_ERROR_FRIENDLY_MESSAGES[ErrorCode.LLM_TPM_LIMIT]
        attrs = mock_metric.call_args[0][1]
        assert attrs["llm_error_code"] == "llm_tpm_limit"

    @patch("codemie.agents.utils.send_log_metric")
    @patch("codemie.agents.utils.logging_user_id")
    def test_rate_limit_rpm(self, mock_uid, mock_metric):
        from litellm.exceptions import RateLimitError

        mock_uid.get.return_value = "u1"
        exc = RateLimitError("requests per minute limit", model="gpt-4", llm_provider="openai", response=MagicMock())

        user_message, error_code = handle_agent_exception(exc)

        assert error_code == "llm_rpm_limit"
        assert user_message == LITELLM_ERROR_FRIENDLY_MESSAGES[ErrorCode.LLM_RPM_LIMIT]

    @patch("codemie.agents.utils.send_log_metric")
    @patch("codemie.agents.utils.logging_user_id")
    def test_service_unavailable(self, mock_uid, mock_metric):
        from litellm.exceptions import ServiceUnavailableError

        mock_uid.get.return_value = "u1"
        exc = ServiceUnavailableError("down", model="gpt-4", llm_provider="openai", response=MagicMock())

        user_message, error_code = handle_agent_exception(exc)

        assert error_code == "llm_unavailable"
        assert user_message == LITELLM_ERROR_FRIENDLY_MESSAGES[ErrorCode.LLM_UNAVAILABLE]

    @patch("codemie.agents.utils.send_log_metric")
    @patch("codemie.agents.utils.logging_user_id")
    def test_internal_server_error(self, mock_uid, mock_metric):
        from litellm.exceptions import InternalServerError

        mock_uid.get.return_value = "u1"
        exc = InternalServerError("oops", model="gpt-4", llm_provider="openai", response=MagicMock())

        user_message, error_code = handle_agent_exception(exc)

        assert error_code == "llm_internal_error"
        assert user_message == LITELLM_ERROR_FRIENDLY_MESSAGES[ErrorCode.LLM_INTERNAL_ERROR]

    @patch("codemie.agents.utils.send_log_metric")
    @patch("codemie.agents.utils.logging_user_id")
    def test_transitive_error(self, mock_uid, mock_metric):
        # Classified by status_code (502); exception must be recognized as LLM (module or message)
        _exc = type("_Exc", (Exception,), {"__module__": "litellm.exceptions"})
        exc = _exc("connection refused")
        exc.status_code = 502  # type: ignore[attr-defined]

        mock_uid.get.return_value = "u1"
        user_message, error_code = handle_agent_exception(exc)

        assert error_code == "llm_transitive_error"
        assert user_message == LITELLM_ERROR_FRIENDLY_MESSAGES[ErrorCode.LLM_TRANSITIVE_ERROR]

    @patch("codemie.agents.utils.send_log_metric")
    @patch("codemie.agents.utils.logging_user_id")
    def test_model_and_provider_included_in_structured_log(self, mock_uid, mock_metric):
        from litellm.exceptions import Timeout

        mock_uid.get.return_value = "u1"
        exc = Timeout("timed out", model="claude-3-opus", llm_provider="anthropic")

        handle_agent_exception(exc)

        attrs = mock_metric.call_args[0][1]
        assert attrs.get("llm_model") == "claude-3-opus"
        assert attrs.get("llm_provider") == "anthropic"

    @patch("codemie.agents.utils.send_log_metric")
    @patch("codemie.agents.utils.logging_user_id")
    def test_hide_flag_returns_universal_message_for_any_litellm_error(self, mock_uid, mock_metric):
        from litellm.exceptions import ServiceUnavailableError

        mock_uid.get.return_value = "u1"
        exc = ServiceUnavailableError("down", model="gpt-4", llm_provider="openai", response=MagicMock())

        with patch.object(config, "HIDE_AGENT_STREAMING_EXCEPTIONS", True):
            user_message, error_code = handle_agent_exception(exc)

        assert error_code == "llm_unavailable"
        assert user_message == config.CODEMIE_SUPPORT_MSG


class TestHandleAgentExceptionLegacyAndGeneral:
    """Tests for non-LiteLLM paths: legacy budget_exceeded string match
    and general unrecognised exceptions."""

    @pytest.fixture(autouse=True)
    def _hide_off(self):
        with patch.object(config, "HIDE_AGENT_STREAMING_EXCEPTIONS", False):
            yield

    @patch("codemie.agents.utils._emit_llm_error_log")
    @patch("codemie.agents.utils.is_litellm_exception", return_value=False)
    def test_legacy_budget_exceeded_with_dict_message(self, mock_is, mock_emit):
        error_str = "budget_exceeded: {'error': {'message': 'User budget exceeded for user abc'}}"
        exc = Exception(error_str)

        user_message, error_code = handle_agent_exception(exc)

        assert error_code == "llm_budget_exceeded"
        assert user_message == "User budget exceeded for user abc"
        mock_emit.assert_called_once()

    @patch("codemie.agents.utils._emit_llm_error_log")
    @patch("codemie.agents.utils.is_litellm_exception", return_value=False)
    def test_legacy_budget_exceeded_no_parseable_dict(self, mock_is, mock_emit):
        exc = Exception("budget_exceeded — no details")

        user_message, error_code = handle_agent_exception(exc)

        assert error_code == "llm_budget_exceeded"
        assert user_message == "Budget limit has been reached."

    @patch("codemie.agents.utils._emit_llm_error_log")
    @patch("codemie.agents.utils.is_litellm_exception", return_value=False)
    def test_legacy_budget_exceeded_returns_universal_when_hide_flag_on(self, mock_is, mock_emit):
        exc = Exception("budget_exceeded — no details")

        with patch.object(config, "HIDE_AGENT_STREAMING_EXCEPTIONS", True):
            user_message, error_code = handle_agent_exception(exc)

        assert error_code == "llm_budget_exceeded"
        assert user_message == config.CODEMIE_SUPPORT_MSG

    @patch("codemie.agents.utils.is_litellm_exception", return_value=False)
    def test_general_exception_returns_none_code_and_includes_type(self, mock_is):
        exc = ValueError("something broke")

        user_message, error_code = handle_agent_exception(exc)

        assert error_code is None
        assert "ValueError" in user_message
        assert "something broke" in user_message


class TestExtractBudgetMessage:
    """Tests for _extract_budget_message — parses error dicts from LiteLLM proxy."""

    def test_extract_nested_error_message(self):
        msg = "Error: {'error': {'message': 'Budget exceeded for user X'}}"
        assert _extract_budget_message(msg) == "Budget exceeded for user X"

    def test_extract_flat_message(self):
        msg = "Error: {'message': 'You ran out of budget'}"
        assert _extract_budget_message(msg) == "You ran out of budget"

    def test_no_dict_returns_none(self):
        assert _extract_budget_message("plain error text") is None

    def test_malformed_dict_returns_none(self):
        assert _extract_budget_message("Error: {invalid dict") is None

    def test_dict_without_message_key_returns_none(self):
        assert _extract_budget_message("Error: {'status': 'failed'}") is None


class TestEmitLlmErrorLog:
    """Tests for _emit_llm_error_log — validates ELK-alertable structured log."""

    @patch("codemie.agents.utils.send_log_metric")
    @patch("codemie.agents.utils.logging_uuid")
    @patch("codemie.agents.utils.logging_conversation_id")
    @patch("codemie.agents.utils.current_user_email")
    @patch("codemie.agents.utils.logging_user_id")
    def test_emits_correct_metric_name_and_context_attributes(
        self, mock_uid, mock_email, mock_conv, mock_uuid, mock_send
    ):
        mock_uid.get.return_value = "user-123"
        mock_email.get.return_value = "alice@example.com"
        mock_conv.get.return_value = "conv-456"
        mock_uuid.get.return_value = "req-789"

        _emit_llm_error_log("llm_rate_limit", "rate limit hit")

        mock_send.assert_called_once()
        metric_name = mock_send.call_args[0][0]
        attrs = mock_send.call_args[0][1]
        assert metric_name == "codemie_llm_error_total"
        assert attrs["llm_error_code"] == "llm_rate_limit"
        assert attrs["user_id"] == "user-123"
        assert attrs["user_email"] == "alice@example.com"
        assert attrs["conversation_id"] == "conv-456"
        assert attrs["request_uuid"] == "req-789"

    @patch("codemie.agents.utils.send_log_metric")
    @patch("codemie.agents.utils.logging_uuid")
    @patch("codemie.agents.utils.logging_conversation_id")
    @patch("codemie.agents.utils.current_user_email")
    @patch("codemie.agents.utils.logging_user_id")
    def test_extracts_model_and_provider_from_exception(self, mock_uid, mock_email, mock_conv, mock_uuid, mock_send):
        mock_uid.get.return_value = "-"
        mock_email.get.return_value = "-"
        mock_conv.get.return_value = "-"
        mock_uuid.get.return_value = "-"

        exc = MagicMock()
        exc.model = "claude-3"
        exc.llm_provider = "anthropic"
        exc.status_code = 429

        _emit_llm_error_log("llm_timeout", "timed out", exc=exc)

        attrs = mock_send.call_args[0][1]
        assert attrs["llm_model"] == "claude-3"
        assert attrs["llm_provider"] == "anthropic"
        assert attrs["status_code"] == 429

    @patch("codemie.agents.utils.send_log_metric")
    @patch("codemie.agents.utils.logging_uuid")
    @patch("codemie.agents.utils.logging_conversation_id")
    @patch("codemie.agents.utils.current_user_email")
    @patch("codemie.agents.utils.logging_user_id")
    def test_omits_exc_fields_when_no_exception(self, mock_uid, mock_email, mock_conv, mock_uuid, mock_send):
        mock_uid.get.return_value = "-"
        mock_email.get.return_value = "-"
        mock_conv.get.return_value = "-"
        mock_uuid.get.return_value = "-"

        _emit_llm_error_log("llm_rate_limit", "error")

        attrs = mock_send.call_args[0][1]
        assert "llm_model" not in attrs
        assert "llm_provider" not in attrs
        assert "status_code" not in attrs

    @patch("codemie.agents.utils.send_log_metric", side_effect=RuntimeError("send failed"))
    @patch("codemie.agents.utils.logging_uuid")
    @patch("codemie.agents.utils.logging_conversation_id")
    @patch("codemie.agents.utils.current_user_email")
    @patch("codemie.agents.utils.logging_user_id")
    def test_swallows_exceptions_to_avoid_masking_original_error(
        self, mock_uid, mock_email, mock_conv, mock_uuid, mock_send
    ):
        mock_uid.get.return_value = "-"
        mock_email.get.return_value = "-"
        mock_conv.get.return_value = "-"
        mock_uuid.get.return_value = "-"
        _emit_llm_error_log("llm_unknown_error", "something")
