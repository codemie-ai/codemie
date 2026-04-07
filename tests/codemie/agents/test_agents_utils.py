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
from codemie.agents.utils import (
    LangfuseLiteLLMErrorOutputCallback,
    OPEN_AI_TOOL_NAME_LIMIT,
    adapt_tool_name,
    error_output_callback,
    generate_tool_hash,
    get_repo_files_by_search_phrase_path,
    get_repo_tree,
    get_run_config,
    parse_tool_input,
    render_text_description_and_args,
    to_snake_case,
)
from codemie.core.errors import ErrorResponse, ErrorCategory, InternalError, LiteLLMErrorClassifier
from codemie.core.models import CodeFields
from codemie.core.constants import CodeIndexType
from codemie.configs import config
from codemie.enterprise.litellm.proxy_router import emit_llm_error_log, handle_agent_exception
from codemie.service.monitoring.metrics_constants import LLM_ERROR_TOTAL_METRIC


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
# handle_agent_exception / emit_llm_error_log (codemie.enterprise.litellm.proxy_router)
#
# These tests validate the core error-handling flow:
#   exception → classification → ErrorResponse (message, error_code) + structured log
# ---------------------------------------------------------------------------
class TestHandleAgentExceptionEndToEnd:
    """End-to-end tests: exception → classification → ErrorResponse (message, error_code) + structured log."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        with patch.object(config, "HIDE_AGENT_STREAMING_EXCEPTIONS", False):
            yield

    @patch("codemie.enterprise.litellm.proxy_router.send_log_metric")
    @patch("codemie.enterprise.litellm.proxy_router.logging_user_id")
    def test_budget_exceeded_returns_code_and_friendly_message(self, mock_uid, mock_metric):
        mock_uid.get.return_value = "u1"
        exc = Exception("budget_exceeded: current cost 15, max 10")

        response = handle_agent_exception(exc)
        user_message = response.get_error().message
        error_code = response.get_error().error_code.value

        assert response.category == ErrorCategory.AGENT
        assert error_code == "agent_budget_exceeded"
        assert user_message == config.AGENT_MSG_BUDGET_EXCEEDED
        mock_metric.assert_called_once()
        attrs = mock_metric.call_args[0][1]
        assert attrs["llm_error_code"] == "agent_budget_exceeded"

    @patch("codemie.enterprise.litellm.proxy_router.send_log_metric")
    @patch("codemie.enterprise.litellm.proxy_router.logging_user_id")
    def test_service_unavailable(self, mock_uid, mock_metric):
        mock_uid.get.return_value = "u1"
        exc = Exception("service unavailable")

        response = handle_agent_exception(exc)
        user_message = response.get_error().message
        error_code = response.get_error().error_code.value

        assert response.category == ErrorCategory.INTERNAL
        assert error_code == "platform_error"
        assert user_message == config.GLOBAL_FALLBACK_MSG

    @patch("codemie.enterprise.litellm.proxy_router.send_log_metric")
    @patch("codemie.enterprise.litellm.proxy_router.logging_user_id")
    def test_internal_server_error(self, mock_uid, mock_metric):
        mock_uid.get.return_value = "u1"
        exc = Exception("internal server error")

        response = handle_agent_exception(exc)
        user_message = response.get_error().message
        error_code = response.get_error().error_code.value

        assert response.category == ErrorCategory.INTERNAL
        assert error_code == "platform_error"
        assert user_message == config.GLOBAL_FALLBACK_MSG

    @patch("codemie.enterprise.litellm.proxy_router.send_log_metric")
    @patch("codemie.enterprise.litellm.proxy_router.logging_user_id")
    def test_transitive_error(self, mock_uid, mock_metric):
        mock_uid.get.return_value = "u1"
        exc = Exception("connection refused")

        response = handle_agent_exception(exc)
        user_message = response.get_error().message
        error_code = response.get_error().error_code.value

        assert response.category == ErrorCategory.AGENT
        assert error_code == "agent_network_error"
        assert user_message == config.AGENT_MSG_NETWORK_ERROR

    @patch("codemie.enterprise.litellm.proxy_router.send_log_metric")
    @patch("codemie.enterprise.litellm.proxy_router.logging_user_id")
    def test_model_and_provider_included_in_structured_log(self, mock_uid, mock_metric):
        mock_uid.get.return_value = "u1"
        exc = Exception("timed out")
        exc.model = "claude-3-opus"  # type: ignore[attr-defined]
        exc.llm_provider = "anthropic"  # type: ignore[attr-defined]

        handle_agent_exception(exc)

        attrs = mock_metric.call_args[0][1]
        assert attrs.get("llm_model") == "claude-3-opus"
        assert attrs.get("llm_provider") == "anthropic"


class TestHandleAgentExceptionLegacyAndGeneral:
    """Tests for non-LiteLLM paths: legacy budget_exceeded string match
    and general unrecognised exceptions."""

    @pytest.fixture(autouse=True)
    def _hide_off(self):
        with patch.object(config, "HIDE_AGENT_STREAMING_EXCEPTIONS", False):
            yield

    @patch("codemie.enterprise.litellm.proxy_router.emit_llm_error_log")
    def test_legacy_budget_exceeded_with_dict_message(self, mock_emit):
        error_str = "budget_exceeded: {'error': {'message': 'User budget exceeded for user abc'}}"
        exc = Exception(error_str)

        response = handle_agent_exception(exc)
        user_message = response.get_error().message
        error_code = response.get_error().error_code.value

        assert response.category == ErrorCategory.AGENT
        assert error_code == "agent_budget_exceeded"
        assert "User budget exceeded" in user_message or "budget" in user_message.lower()
        mock_emit.assert_called_once()


class TestEmitLlmErrorLog:
    """Tests for emit_llm_error_log — validates ELK-alertable structured log."""

    @patch("codemie.enterprise.litellm.proxy_router.send_log_metric")
    @patch("codemie.enterprise.litellm.proxy_router.logging_uuid")
    @patch("codemie.enterprise.litellm.proxy_router.logging_conversation_id")
    @patch("codemie.enterprise.litellm.proxy_router.current_user_email")
    @patch("codemie.enterprise.litellm.proxy_router.logging_user_id")
    def test_emits_correct_metric_name_and_context_attributes(
        self, mock_uid, mock_email, mock_conv, mock_uuid, mock_send
    ):
        mock_uid.get.return_value = "user-123"
        mock_email.get.return_value = "alice@example.com"
        mock_conv.get.return_value = "conv-456"
        mock_uuid.get.return_value = "req-789"

        internal_error = InternalError.from_exception(ValueError("rate limit hit"))
        response = ErrorResponse(
            category=ErrorCategory.INTERNAL,
            internal=internal_error,
        )
        emit_llm_error_log(response)

        mock_send.assert_called_once()
        metric_name = mock_send.call_args[0][0]
        attrs = mock_send.call_args[0][1]
        assert metric_name == LLM_ERROR_TOTAL_METRIC
        assert attrs["llm_error_code"] == "platform_error"
        assert attrs["user_id"] == "user-123"
        assert attrs["user_email"] == "alice@example.com"
        assert attrs["conversation_id"] == "conv-456"
        assert attrs["request_uuid"] == "req-789"

    @patch("codemie.enterprise.litellm.proxy_router.send_log_metric")
    @patch("codemie.enterprise.litellm.proxy_router.logging_uuid")
    @patch("codemie.enterprise.litellm.proxy_router.logging_conversation_id")
    @patch("codemie.enterprise.litellm.proxy_router.current_user_email")
    @patch("codemie.enterprise.litellm.proxy_router.logging_user_id")
    def test_extracts_model_and_provider_from_exception(self, mock_uid, mock_email, mock_conv, mock_uuid, mock_send):
        mock_uid.get.return_value = "-"
        mock_email.get.return_value = "-"
        mock_conv.get.return_value = "-"
        mock_uuid.get.return_value = "-"

        internal_error = InternalError.from_exception(ValueError("timed out"))
        response = ErrorResponse(
            category=ErrorCategory.INTERNAL,
            internal=internal_error,
        )
        exc = MagicMock()
        exc.model = "claude-3"
        exc.llm_provider = "anthropic"
        exc.status_code = 429

        emit_llm_error_log(response, exc=exc)

        attrs = mock_send.call_args[0][1]
        assert attrs["llm_model"] == "claude-3"
        assert attrs["llm_provider"] == "anthropic"
        assert attrs["status_code"] == 429

    @patch("codemie.enterprise.litellm.proxy_router.send_log_metric")
    @patch("codemie.enterprise.litellm.proxy_router.logging_uuid")
    @patch("codemie.enterprise.litellm.proxy_router.logging_conversation_id")
    @patch("codemie.enterprise.litellm.proxy_router.current_user_email")
    @patch("codemie.enterprise.litellm.proxy_router.logging_user_id")
    def test_omits_exc_fields_when_no_exception(self, mock_uid, mock_email, mock_conv, mock_uuid, mock_send):
        mock_uid.get.return_value = "-"
        mock_email.get.return_value = "-"
        mock_conv.get.return_value = "-"
        mock_uuid.get.return_value = "-"

        internal_error = InternalError.from_exception(ValueError("error"))
        response = ErrorResponse(
            category=ErrorCategory.INTERNAL,
            internal=internal_error,
        )
        emit_llm_error_log(response)

        attrs = mock_send.call_args[0][1]
        assert "llm_model" not in attrs
        assert "llm_provider" not in attrs
        assert "status_code" not in attrs

    @patch("codemie.enterprise.litellm.proxy_router.send_log_metric", side_effect=RuntimeError("send failed"))
    @patch("codemie.enterprise.litellm.proxy_router.logging_uuid")
    @patch("codemie.enterprise.litellm.proxy_router.logging_conversation_id")
    @patch("codemie.enterprise.litellm.proxy_router.current_user_email")
    @patch("codemie.enterprise.litellm.proxy_router.logging_user_id")
    def test_swallows_exceptions_to_avoid_masking_original_error(
        self, mock_uid, mock_email, mock_conv, mock_uuid, mock_send
    ):
        mock_uid.get.return_value = "-"
        mock_email.get.return_value = "-"
        mock_conv.get.return_value = "-"
        mock_uuid.get.return_value = "-"
        internal_error = InternalError.from_exception(ValueError("something"))
        response = ErrorResponse(
            category=ErrorCategory.INTERNAL,
            internal=internal_error,
        )
        emit_llm_error_log(response)


# ---------------------------------------------------------------------------
# LangfuseLiteLLMErrorOutputCallback + get_run_config Langfuse wiring
# ---------------------------------------------------------------------------
@pytest.fixture
def classified_lite_llm_rate_limit_exception() -> Exception:
    """Exception body that LiteLLMErrorClassifier maps to a rate-limit ErrorResponse (see test_errors)."""
    return Exception(
        '{"error": {"message": "rate limit exceeded for model gpt-4", '
        '"type": "None", "param": "None", "code": "429"}}'
    )


class TestLangfuseLiteLLMErrorOutputCallback:
    """LangChain callback: classified LiteLLM errors → Langfuse generation + trace output."""

    def _expected_classified_message(self, exc: Exception) -> str:
        r = LiteLLMErrorClassifier().classify(exc)
        assert r is not None
        payload = r.get_error()
        assert payload is not None
        return payload.message

    @patch("codemie.agents.utils.get_langfuse_client_or_none")
    def test_on_llm_error_updates_generation_and_trace_io(
        self, mock_get_client, classified_lite_llm_rate_limit_exception
    ):
        gen = MagicMock()
        trace_io = MagicMock()

        class _Client:
            update_current_generation = gen
            set_current_trace_io = trace_io

        mock_get_client.return_value = _Client()
        handler = LangfuseLiteLLMErrorOutputCallback()
        exc = classified_lite_llm_rate_limit_exception
        expected = self._expected_classified_message(exc)

        handler.on_llm_error(exc)

        gen.assert_called_once_with(output=expected)
        trace_io.assert_called_once_with(output=expected)

    @patch("codemie.agents.utils.get_langfuse_client_or_none")
    def test_on_llm_error_skips_client_when_classify_returns_none(self, mock_get_client):
        handler = LangfuseLiteLLMErrorOutputCallback()
        handler.on_llm_error(Exception("Connection refused"))
        mock_get_client.assert_not_called()

    @patch("codemie.agents.utils.get_langfuse_client_or_none", return_value=None)
    def test_on_llm_error_no_op_when_langfuse_client_unavailable(
        self, _mock_get_client, classified_lite_llm_rate_limit_exception
    ):
        handler = LangfuseLiteLLMErrorOutputCallback()
        handler.on_llm_error(classified_lite_llm_rate_limit_exception)

    @patch("codemie.agents.utils.get_langfuse_client_or_none")
    def test_on_llm_error_falls_back_to_update_current_trace(
        self, mock_get_client, classified_lite_llm_rate_limit_exception
    ):
        gen = MagicMock()
        trace = MagicMock()

        class _Client:
            update_current_generation = gen
            update_current_trace = trace

        mock_get_client.return_value = _Client()
        handler = LangfuseLiteLLMErrorOutputCallback()
        exc = classified_lite_llm_rate_limit_exception
        expected = self._expected_classified_message(exc)

        handler.on_llm_error(exc)

        gen.assert_called_once_with(output=expected)
        trace.assert_called_once_with(output=expected)

    @patch("codemie.agents.utils.logger.warning")
    @patch("codemie.agents.utils.get_langfuse_client_or_none")
    def test_on_llm_error_logs_warning_when_langfuse_raises(
        self, mock_get_client, mock_warning, classified_lite_llm_rate_limit_exception
    ):
        gen = MagicMock(side_effect=RuntimeError("otel failed"))
        trace_io = MagicMock()

        class _Client:
            update_current_generation = gen
            set_current_trace_io = trace_io

        mock_get_client.return_value = _Client()
        handler = LangfuseLiteLLMErrorOutputCallback()
        handler.on_llm_error(classified_lite_llm_rate_limit_exception)

        gen.assert_called_once()
        trace_io.assert_not_called()
        mock_warning.assert_called_once()
        assert "Failed to set Langfuse output" in mock_warning.call_args[0][0]


class TestGetRunConfigLangfuseCallbacks:
    @patch("codemie.agents.utils.build_agent_metadata_with_workflow_context", return_value={"k": "v"})
    @patch("codemie.agents.utils.get_langfuse_callback_handler")
    @patch("codemie.agents.utils._should_enable_langfuse_tracing", return_value=True)
    def test_includes_error_output_callback_after_langfuse_handler(self, _mock_tracing, mock_handler, _mock_meta):
        handler = MagicMock()
        mock_handler.return_value = handler
        cfg = get_run_config(
            request=None,
            llm_model="gpt-test",
            agent_name="TestAgent",
            conversation_id="conv-1",
        )
        assert cfg["callbacks"] == [handler, error_output_callback]
        assert cfg["run_name"] == "TestAgent"
        assert cfg["metadata"] == {"k": "v"}
