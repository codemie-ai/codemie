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

"""Unit tests for HedgedAssistantHandler."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from time import time
from unittest.mock import Mock, patch

import pytest

from codemie.core.models import AssistantChatRequest, BaseModelResponse
from codemie.rest_api.handlers.assistant_handlers import (
    A2AAssistantHandler,
    ChatHistoryData,
    StandardAssistantHandler,
    get_request_handler,
)
from codemie.rest_api.handlers.hedged_handler import HedgedAssistantHandler
from codemie.rest_api.models.assistant import AssistantType
from codemie.rest_api.models.hedging import HedgingConfig, HedgingProviderToolDetails, HedgingToolDetails
from codemie.rest_api.security.user import User
from codemie_tools.base.codemie_hedge_tool import HedgeToolResult

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_HIT_RESULT = json.dumps(HedgeToolResult(empty=False, data="Fast response from KB").model_dump())
_MISS_RESULT = json.dumps(HedgeToolResult(empty=True).model_dump())

AGENT_CHUNK = json.dumps({"generated": "agent answer", "generated_chunk": None, "last": False, "thought": None})


@pytest.fixture
def mock_user():
    user = Mock(spec=User)
    user.id = "user-1"
    user.name = "Test User"
    user.username = "test_user"
    user.email = "test@example.com"
    user.auth_token = "test-token"
    return user


@pytest.fixture
def hedging_config():
    return HedgingConfig(tool=HedgingToolDetails(name="example_hedge_tool"), timeout_ms=200)


@pytest.fixture
def mock_assistant(hedging_config):
    assistant = Mock()
    assistant.id = "asst-1"
    assistant.name = "FAQ Bot"
    assistant.type = AssistantType.CODEMIE
    assistant.llm_model_type = "gpt-4"
    assistant.hedging_config = hedging_config
    return assistant


@pytest.fixture
def handler(mock_assistant, mock_user):
    return HedgedAssistantHandler(mock_assistant, mock_user, "req-uuid")


@pytest.fixture
def request_(mock_assistant):
    return AssistantChatRequest(text="What is X?", save_history=False)


@pytest.fixture
def raw_request():
    raw = Mock()
    raw.state.uuid = "req-uuid"
    raw.state.on_disconnect = Mock()
    return raw


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _consume(body_iterator) -> list[str]:
    """Drain a StreamingResponse body_iterator and return chunks as strings."""
    chunks = []
    async for chunk in body_iterator:
        decoded = chunk.decode() if isinstance(chunk, bytes) else chunk
        chunks.append(decoded)
    return chunks


def _make_fast_tool(invoke_return):
    """Create a mock tool whose invoke() returns invoke_return."""
    tool = Mock()
    tool.invoke = Mock(return_value=invoke_return)
    return tool


# ---------------------------------------------------------------------------
# TestRunFastPath
# ---------------------------------------------------------------------------


class TestRunFastPath:
    """Direct unit tests for HedgedAssistantHandler._run_fast_path."""

    def _run(self, handler, request_, raw_request, fast_tool, headers=None):
        with patch(
            "codemie.rest_api.handlers.hedged_handler.HedgingToolService.instantiate",
            return_value=fast_tool,
        ):
            attempt = handler._run_fast_path(request_, raw_request.state.uuid, headers or {})
        return attempt.result

    def test_hit_result_stored_in_holder(self, handler, request_, raw_request):
        tool = _make_fast_tool(_HIT_RESULT)
        result = self._run(handler, request_, raw_request, tool)

        assert result == _HIT_RESULT
        tool.invoke.assert_called_once_with({"query": "What is X?", "metadata": {}})

    def test_miss_result_holder_stays_set(self, handler, request_, raw_request):
        tool = _make_fast_tool(_MISS_RESULT)
        result = self._run(handler, request_, raw_request, tool)

        assert result == _MISS_RESULT

    def test_tool_none_return_holder_none(self, handler, request_, raw_request):
        tool = _make_fast_tool(None)
        result = self._run(handler, request_, raw_request, tool)

        assert result is None

    def test_instantiate_exception_logs_warning_done_still_set(self, handler, request_, raw_request):
        with (
            patch(
                "codemie.rest_api.handlers.hedged_handler.HedgingToolService.instantiate",
                side_effect=ValueError("tool not found"),
            ),
            patch("codemie.rest_api.handlers.hedged_handler.logger") as mock_logger,
        ):
            attempt = handler._run_fast_path(request_, raw_request.state.uuid, {})

        assert attempt.result is None
        mock_logger.warning.assert_called_once()

    def test_invoke_exception_logs_error_done_still_set(self, handler, request_, raw_request):
        tool = _make_fast_tool(None)
        tool.invoke.side_effect = RuntimeError("ES down")
        with patch("codemie.rest_api.handlers.hedged_handler.logger") as mock_logger:
            result = self._run(handler, request_, raw_request, tool)

        assert result is None
        mock_logger.error.assert_called_once()


# ---------------------------------------------------------------------------
# TestHandleStreamFastPathWins
# ---------------------------------------------------------------------------


class TestHandleStreamFastPathWins:
    """Coordinator yields the fast-path chunk and cancels the agent."""

    def _run_stream(self, handler, request_, raw_request):
        from codemie.core.thread import ThreadedGenerator  # lazy: avoids circular import at module level

        real_tg = ThreadedGenerator(request_uuid="req-uuid", user_id="user-1", conversation_id="")

        mock_agent = Mock()
        mock_agent.last_generation_result = None
        mock_agent.stream = Mock()

        with (
            patch(
                "codemie.rest_api.handlers.hedged_handler.HedgingToolService.instantiate",
                return_value=_make_fast_tool(_HIT_RESULT),
            ),
            patch("codemie.rest_api.handlers.hedged_handler.AssistantService.build_agent", return_value=mock_agent),
            patch("codemie.rest_api.handlers.hedged_handler.ThreadedGenerator", return_value=real_tg),
            patch("codemie.rest_api.handlers.hedged_handler.extract_custom_headers", return_value={}),
            patch("codemie.rest_api.handlers.hedged_handler.set_disable_prompt_cache"),
            patch.object(handler, "save_chat_history") as mock_save,
        ):
            response = handler._handle_stream(request_, raw_request, time())
            chunks = asyncio.run(_consume(response.body_iterator))

        return chunks, mock_save, real_tg, mock_agent

    def test_yields_exactly_one_chunk(self, handler, request_, raw_request):
        chunks, _, _, _ = self._run_stream(handler, request_, raw_request)

        # coordinator emits: TOOL_START thought, TOOL_END thought, generated chunk
        assert len(chunks) == 3

    def test_chunk_contains_fast_path_data(self, handler, request_, raw_request):
        chunks, _, _, _ = self._run_stream(handler, request_, raw_request)

        data = json.loads(chunks[2])
        assert data["generated"] == "Fast response from KB"
        assert data["last"] is True

    def test_save_chat_history_called_with_fast_path_text(self, handler, request_, raw_request):
        _, mock_save, _, _ = self._run_stream(handler, request_, raw_request)

        mock_save.assert_called_once()
        history_data: ChatHistoryData = mock_save.call_args[0][0]
        assert history_data.response == "Fast response from KB"

    def test_agent_queue_is_closed(self, handler, request_, raw_request):
        _, _, real_tg, _ = self._run_stream(handler, request_, raw_request)

        assert real_tg.is_closed()


# ---------------------------------------------------------------------------
# TestHandleStreamFastPathEmpty
# ---------------------------------------------------------------------------


class TestHandleStreamFastPathEmpty:
    """Coordinator falls through and drains the agent queue."""

    def _run_stream(self, handler, request_, raw_request, fast_tool_result=None):
        from codemie.core.thread import ThreadedGenerator  # lazy: avoids circular import at module level

        real_tg = ThreadedGenerator(request_uuid="req-uuid", user_id="user-1", conversation_id="")

        def agent_stream():
            real_tg.queue.put(AGENT_CHUNK)
            real_tg.queue.put(StopIteration)

        mock_agent = Mock()
        mock_agent.last_generation_result = None
        mock_agent.stream = agent_stream

        with (
            patch(
                "codemie.rest_api.handlers.hedged_handler.HedgingToolService.instantiate",
                return_value=_make_fast_tool(fast_tool_result),
            ),
            patch("codemie.rest_api.handlers.hedged_handler.AssistantService.build_agent", return_value=mock_agent),
            patch("codemie.rest_api.handlers.hedged_handler.ThreadedGenerator", return_value=real_tg),
            patch("codemie.rest_api.handlers.hedged_handler.extract_custom_headers", return_value={}),
            patch("codemie.rest_api.handlers.hedged_handler.set_disable_prompt_cache"),
            patch.object(handler, "save_chat_history") as mock_save,
        ):
            response = handler._handle_stream(request_, raw_request, time())
            chunks = asyncio.run(_consume(response.body_iterator))

        return chunks, mock_save, real_tg

    def test_yields_agent_chunk_on_miss(self, handler, request_, raw_request):
        chunks, _, _ = self._run_stream(handler, request_, raw_request, fast_tool_result=_MISS_RESULT)

        assert len(chunks) >= 1
        data = json.loads(chunks[0])
        assert data["generated"] == "agent answer"

    def test_yields_agent_chunk_on_none(self, handler, request_, raw_request):
        chunks, _, _ = self._run_stream(handler, request_, raw_request, fast_tool_result=None)

        assert len(chunks) >= 1
        data = json.loads(chunks[0])
        assert data["generated"] == "agent answer"

    def test_save_chat_history_called_with_agent_response(self, handler, request_, raw_request):
        _, mock_save, _ = self._run_stream(handler, request_, raw_request, fast_tool_result=_MISS_RESULT)

        mock_save.assert_called_once()
        history_data: ChatHistoryData = mock_save.call_args[0][0]
        assert history_data.response == "agent answer"

    def test_agent_queue_not_closed_by_coordinator(self, handler, request_, raw_request):
        _, _, real_tg = self._run_stream(handler, request_, raw_request, fast_tool_result=None)

        assert not real_tg.is_closed()


# ---------------------------------------------------------------------------
# TestHandleStreamInstantiateFails
# ---------------------------------------------------------------------------


class TestHandleStreamInstantiateFails:
    """When instantiate() raises, the agent path is used."""

    def test_missing_tool_uses_agent_output(self, handler, request_, raw_request):
        from codemie.core.thread import ThreadedGenerator  # lazy: avoids circular import at module level

        real_tg = ThreadedGenerator(request_uuid="req-uuid", user_id="user-1", conversation_id="")

        def agent_stream():
            real_tg.queue.put(AGENT_CHUNK)
            real_tg.queue.put(StopIteration)

        mock_agent = Mock()
        mock_agent.last_generation_result = None
        mock_agent.stream = agent_stream

        with (
            patch(
                "codemie.rest_api.handlers.hedged_handler.HedgingToolService.instantiate",
                side_effect=ValueError("not found"),
            ),
            patch("codemie.rest_api.handlers.hedged_handler.AssistantService.build_agent", return_value=mock_agent),
            patch("codemie.rest_api.handlers.hedged_handler.ThreadedGenerator", return_value=real_tg),
            patch("codemie.rest_api.handlers.hedged_handler.extract_custom_headers", return_value={}),
            patch("codemie.rest_api.handlers.hedged_handler.set_disable_prompt_cache"),
            patch.object(handler, "save_chat_history"),
            patch("codemie.rest_api.handlers.hedged_handler.logger") as mock_logger,
        ):
            response = handler._handle_stream(request_, raw_request, time())
            chunks = asyncio.run(_consume(response.body_iterator))

        assert len(chunks) >= 1
        data = json.loads(chunks[0])
        assert data["generated"] == "agent answer"
        mock_logger.warning.assert_called()


# ---------------------------------------------------------------------------
# TestHandleSync
# ---------------------------------------------------------------------------


class TestHandleSync:
    """Tests for the sequential sync path."""

    def test_fast_path_wins_returns_immediately_without_agent(self, handler, request_, raw_request):
        with (
            patch(
                "codemie.rest_api.handlers.hedged_handler.HedgingToolService.instantiate",
                return_value=_make_fast_tool(_HIT_RESULT),
            ),
            patch("codemie.rest_api.handlers.hedged_handler.AssistantService.build_agent") as mock_build,
            patch("codemie.rest_api.handlers.hedged_handler.extract_custom_headers", return_value={}),
            patch("codemie.rest_api.handlers.hedged_handler.set_disable_prompt_cache"),
            patch.object(handler, "save_chat_history") as mock_save,
        ):
            result = handler._handle_sync(request_, raw_request, time())

        assert isinstance(result, BaseModelResponse)
        assert result.generated == "Fast response from KB"
        mock_build.assert_not_called()
        mock_save.assert_called_once()

    def test_fast_path_miss_delegates_to_super(self, handler, request_, raw_request):
        super_response = BaseModelResponse(generated="super response", time_elapsed=1.0, thoughts=[])
        with (
            patch(
                "codemie.rest_api.handlers.hedged_handler.HedgingToolService.instantiate",
                return_value=_make_fast_tool(_MISS_RESULT),
            ),
            patch("codemie.rest_api.handlers.hedged_handler.extract_custom_headers", return_value={}),
            patch("codemie.rest_api.handlers.hedged_handler.set_disable_prompt_cache"),
            patch.object(StandardAssistantHandler, "_handle_sync", return_value=super_response) as mock_super,
        ):
            result = handler._handle_sync(request_, raw_request, time())

        assert result is super_response
        mock_super.assert_called_once()

    def test_save_chat_history_called_on_fast_path_win(self, handler, request_, raw_request):
        with (
            patch(
                "codemie.rest_api.handlers.hedged_handler.HedgingToolService.instantiate",
                return_value=_make_fast_tool(_HIT_RESULT),
            ),
            patch("codemie.rest_api.handlers.hedged_handler.AssistantService.build_agent"),
            patch("codemie.rest_api.handlers.hedged_handler.extract_custom_headers", return_value={}),
            patch("codemie.rest_api.handlers.hedged_handler.set_disable_prompt_cache"),
            patch.object(handler, "save_chat_history") as mock_save,
        ):
            handler._handle_sync(request_, raw_request, time())

        mock_save.assert_called_once()
        history_data: ChatHistoryData = mock_save.call_args[0][0]
        assert history_data.response == "Fast response from KB"


# ---------------------------------------------------------------------------
# TestHedgingMetricEmission
# ---------------------------------------------------------------------------


class TestHedgingMetricEmission:
    """Exactly one request_hedging metric is emitted per request with correct dimensions."""

    def _run_stream(self, handler, request_, raw_request, fast_tool, agent_serves=False):
        from codemie.core.thread import ThreadedGenerator  # lazy: avoids circular import at module level

        real_tg = ThreadedGenerator(request_uuid="req-uuid", user_id="user-1", conversation_id="")

        def agent_stream():
            real_tg.queue.put(AGENT_CHUNK)
            real_tg.queue.put(StopIteration)

        mock_agent = Mock()
        mock_agent.last_generation_result = None
        # When the fast path is expected to win, use a no-op agent (mirrors the fast-path-win
        # suite) so it cannot win the race; otherwise stream real chunks so the agent can serve.
        mock_agent.stream = agent_stream if agent_serves else Mock()

        # Use a passthrough observe provider so _lazy_observe adds no span overhead
        # to the fast-path thread, keeping the fast-path / agent race deterministic.
        noop_provider = Mock()
        noop_provider.make_observe_decorator.return_value = lambda **kw: lambda fn: fn

        with (
            patch(
                "codemie.rest_api.handlers.hedged_handler.get_observability_provider",
                return_value=noop_provider,
            ),
            patch(
                "codemie.rest_api.handlers.hedged_handler.HedgingToolService.instantiate",
                return_value=fast_tool,
            ),
            patch("codemie.rest_api.handlers.hedged_handler.AssistantService.build_agent", return_value=mock_agent),
            patch("codemie.rest_api.handlers.hedged_handler.ThreadedGenerator", return_value=real_tg),
            patch("codemie.rest_api.handlers.hedged_handler.extract_custom_headers", return_value={}),
            patch("codemie.rest_api.handlers.hedged_handler.set_disable_prompt_cache"),
            patch.object(handler, "save_chat_history"),
            patch(
                "codemie.rest_api.handlers.hedged_handler.HedgingMonitoringService.send_hedging_metric"
            ) as mock_metric,
        ):
            response = handler._handle_stream(request_, raw_request, time())
            asyncio.run(_consume(response.body_iterator))

        return mock_metric

    def test_stream_fast_path_hit_emits_one_metric(self, handler, request_, raw_request):
        mock_metric = self._run_stream(handler, request_, raw_request, _make_fast_tool(_HIT_RESULT))

        mock_metric.assert_called_once()
        payload = mock_metric.call_args.kwargs["payload"]
        assert payload.entry == "stream"
        assert payload.served_by == "fast_path"
        assert payload.fast_path_used is True
        assert payload.fast_path_outcome == "hit"
        assert payload.terminal_reason == "completed"

    def test_stream_miss_emits_agent_served_metric(self, handler, request_, raw_request):
        mock_metric = self._run_stream(handler, request_, raw_request, _make_fast_tool(None), agent_serves=True)

        mock_metric.assert_called_once()
        payload = mock_metric.call_args.kwargs["payload"]
        assert payload.served_by == "agent"
        assert payload.fast_path_used is False
        assert payload.fast_path_outcome == "miss"

    def test_stream_error_emits_agent_served_error_outcome(self, handler, request_, raw_request):
        tool = _make_fast_tool(None)
        tool.invoke.side_effect = RuntimeError("ES down")
        mock_metric = self._run_stream(handler, request_, raw_request, tool, agent_serves=True)

        mock_metric.assert_called_once()
        payload = mock_metric.call_args.kwargs["payload"]
        assert payload.served_by == "agent"
        assert payload.fast_path_outcome == "error"

    def test_sync_fast_path_hit_emits_one_metric(self, handler, request_, raw_request):
        with (
            patch(
                "codemie.rest_api.handlers.hedged_handler.HedgingToolService.instantiate",
                return_value=_make_fast_tool(_HIT_RESULT),
            ),
            patch("codemie.rest_api.handlers.hedged_handler.AssistantService.build_agent"),
            patch("codemie.rest_api.handlers.hedged_handler.extract_custom_headers", return_value={}),
            patch("codemie.rest_api.handlers.hedged_handler.set_disable_prompt_cache"),
            patch.object(handler, "save_chat_history"),
            patch(
                "codemie.rest_api.handlers.hedged_handler.HedgingMonitoringService.send_hedging_metric"
            ) as mock_metric,
        ):
            handler._handle_sync(request_, raw_request, time())

        mock_metric.assert_called_once()
        payload = mock_metric.call_args.kwargs["payload"]
        assert payload.entry == "sync"
        assert payload.served_by == "fast_path"
        assert payload.fast_path_outcome == "hit"
        assert payload.winner == "n/a"

    def test_sync_miss_emits_agent_served_metric(self, handler, request_, raw_request):
        super_response = BaseModelResponse(generated="super response", time_elapsed=1.0, thoughts=[])
        with (
            patch(
                "codemie.rest_api.handlers.hedged_handler.HedgingToolService.instantiate",
                return_value=_make_fast_tool(None),
            ),
            patch("codemie.rest_api.handlers.hedged_handler.extract_custom_headers", return_value={}),
            patch("codemie.rest_api.handlers.hedged_handler.set_disable_prompt_cache"),
            patch.object(StandardAssistantHandler, "_handle_sync", return_value=super_response),
            patch(
                "codemie.rest_api.handlers.hedged_handler.HedgingMonitoringService.send_hedging_metric"
            ) as mock_metric,
        ):
            handler._handle_sync(request_, raw_request, time())

        mock_metric.assert_called_once()
        payload = mock_metric.call_args.kwargs["payload"]
        assert payload.entry == "sync"
        assert payload.served_by == "agent"
        assert payload.fast_path_outcome == "miss"


# ---------------------------------------------------------------------------
# TestGetRequestHandler
# ---------------------------------------------------------------------------


class TestGetRequestHandler:
    """Tests for the get_request_handler() factory function."""

    def _make_user(self):
        user = Mock(spec=User)
        user.id = "user-1"
        return user

    def test_none_hedging_config_returns_standard_handler(self):
        assistant = Mock()
        assistant.type = AssistantType.CODEMIE
        assistant.hedging_config = None

        result = get_request_handler(assistant, self._make_user(), "uuid")

        assert isinstance(result, StandardAssistantHandler)
        assert not isinstance(result, HedgedAssistantHandler)

    def test_hedging_config_set_returns_hedged_handler(self, hedging_config):
        assistant = Mock()
        assistant.type = AssistantType.CODEMIE
        assistant.hedging_config = hedging_config

        with patch("codemie.rest_api.handlers.assistant_handlers.customer_config") as mock_cc:
            mock_cc.is_feature_enabled.return_value = True
            result = get_request_handler(assistant, self._make_user(), "uuid")

        assert isinstance(result, HedgedAssistantHandler)

    def test_a2a_type_returns_a2a_handler_regardless_of_hedging(self, hedging_config):
        assistant = Mock()
        assistant.type = AssistantType.A2A
        assistant.hedging_config = hedging_config

        result = get_request_handler(assistant, self._make_user(), "uuid")

        assert isinstance(result, A2AAssistantHandler)
        assert not isinstance(result, HedgedAssistantHandler)


# ---------------------------------------------------------------------------
# TestGetRequestHandlerFeatureFlag
# ---------------------------------------------------------------------------


class TestGetRequestHandlerFeatureFlag:
    """Tests for the REQUEST_HEDGING_ENABLED feature flag in get_request_handler()."""

    def _make_user(self):
        user = Mock(spec=User)
        user.id = "user-1"
        return user

    def test_flag_enabled_with_hedging_config_returns_hedged_handler(self, hedging_config):
        assistant = Mock()
        assistant.type = AssistantType.CODEMIE
        assistant.hedging_config = hedging_config

        with patch("codemie.rest_api.handlers.assistant_handlers.customer_config") as mock_cc:
            mock_cc.is_feature_enabled.return_value = True
            result = get_request_handler(assistant, self._make_user(), "uuid")

        assert isinstance(result, HedgedAssistantHandler)

    def test_flag_disabled_with_hedging_config_returns_standard_handler(self, hedging_config):
        assistant = Mock()
        assistant.type = AssistantType.CODEMIE
        assistant.hedging_config = hedging_config

        with patch("codemie.rest_api.handlers.assistant_handlers.customer_config") as mock_cc:
            mock_cc.is_feature_enabled.return_value = False
            result = get_request_handler(assistant, self._make_user(), "uuid")

        assert isinstance(result, StandardAssistantHandler)
        assert not isinstance(result, HedgedAssistantHandler)

    def test_flag_enabled_without_hedging_config_returns_standard_handler(self):
        assistant = Mock()
        assistant.type = AssistantType.CODEMIE
        assistant.hedging_config = None

        with patch("codemie.rest_api.handlers.assistant_handlers.customer_config") as mock_cc:
            mock_cc.is_feature_enabled.return_value = True
            result = get_request_handler(assistant, self._make_user(), "uuid")

        assert isinstance(result, StandardAssistantHandler)
        assert not isinstance(result, HedgedAssistantHandler)


# ---------------------------------------------------------------------------
# TestHedgingConfigValidation
# ---------------------------------------------------------------------------


class TestHedgingConfigValidation:
    """Pydantic model_validator tests for HedgingConfig."""

    def test_tool_only_is_valid(self):
        cfg = HedgingConfig(tool=HedgingToolDetails(name="my_tool"))
        assert cfg.tool.name == "my_tool"
        assert cfg.provider_tool is None

    def test_provider_tool_only_is_valid(self):
        cfg = HedgingConfig(
            provider_tool=HedgingProviderToolDetails(
                provider_name="my-provider",
                toolkit_name="search",
                tool_name="semantic_search",
            )
        )
        assert cfg.provider_tool.provider_name == "my-provider"
        assert cfg.tool is None

    def test_neither_tool_nor_provider_raises(self):
        with pytest.raises(Exception, match="Exactly one"):
            HedgingConfig()

    def test_both_tool_and_provider_raises(self):
        with pytest.raises(Exception, match="Only one"):
            HedgingConfig(
                tool=HedgingToolDetails(name="my_tool"),
                provider_tool=HedgingProviderToolDetails(
                    provider_name="p",
                    toolkit_name="t",
                    tool_name="tool",
                ),
            )

    def test_input_mapping_defaults_to_empty(self):
        cfg = HedgingConfig(tool=HedgingToolDetails(name="t"))
        assert cfg.input_mapping == {}

    def test_output_field_defaults_to_none(self):
        cfg = HedgingConfig(tool=HedgingToolDetails(name="t"))
        assert cfg.output_field is None

    def test_provider_config_with_input_mapping_and_output_field(self):
        cfg = HedgingConfig(
            provider_tool=HedgingProviderToolDetails(
                provider_name="p",
                toolkit_name="t",
                tool_name="search",
            ),
            input_mapping={"query": "{{query}}", "user_id": "{{user.id}}"},
            output_field="results.0.text",
            timeout_ms=300,
        )
        assert cfg.input_mapping["query"] == "{{query}}"
        assert cfg.output_field == "results.0.text"
        assert cfg.timeout_ms == 300

    def test_provider_tool_datasource_name_defaults_to_none(self):
        details = HedgingProviderToolDetails(
            provider_name="p",
            toolkit_name="t",
            tool_name="search",
        )
        assert details.datasource_name is None

    def test_provider_tool_datasource_name_can_be_set(self):
        details = HedgingProviderToolDetails(
            provider_name="p",
            toolkit_name="t",
            tool_name="search",
            datasource_name="my-datasource",
        )
        assert details.datasource_name == "my-datasource"

    def test_provider_tool_datasource_name_is_forwarded_in_config(self):
        cfg = HedgingConfig(
            provider_tool=HedgingProviderToolDetails(
                provider_name="p",
                toolkit_name="t",
                tool_name="search",
                datasource_name="ds",
            )
        )
        assert cfg.provider_tool.datasource_name == "ds"


# ---------------------------------------------------------------------------
# TestBuildTemplateContext
# ---------------------------------------------------------------------------


class TestBuildTemplateContext:
    """Unit tests for HedgingToolService.build_template_context."""

    def test_query_from_request_text(self):
        from codemie.service.tools.hedging_tool_service import HedgingToolService

        request = Mock()
        request.text = "hello world"
        request.conversation_id = "conv-1"
        request.metadata = None

        user = Mock()
        user.id = "u1"
        user.name = "Alice"
        user.username = "alice"
        user.email = "alice@example.com"
        user.auth_token = "tok"

        ctx = HedgingToolService.build_template_context(request, user, {"x-tenant": "acme"})

        assert ctx["query"] == "hello world"
        assert ctx["conversation_id"] == "conv-1"
        assert ctx["user"]["id"] == "u1"
        assert ctx["user"]["token"] == "tok"
        assert ctx["headers"] == {"x-tenant": "acme"}
        assert ctx["metadata"] == {}

    def test_none_text_becomes_empty_string(self):
        from codemie.service.tools.hedging_tool_service import HedgingToolService

        request = Mock()
        request.text = None
        request.conversation_id = None
        request.metadata = {"key": "val"}

        user = Mock()
        user.id = ""
        user.name = ""
        user.username = ""
        user.email = ""
        user.auth_token = None

        ctx = HedgingToolService.build_template_context(request, user, {})

        assert ctx["query"] == ""
        assert ctx["conversation_id"] == ""
        assert ctx["user"]["token"] == ""
        assert ctx["metadata"] == {"key": "val"}


# ---------------------------------------------------------------------------
# TestExtractProviderResult
# ---------------------------------------------------------------------------


class TestExtractProviderResult:
    """Unit tests for HedgingToolService._extract_provider_result."""

    def _response(self, status="Completed", result=None):
        from codemie.clients.provider.client.models.tool_invocation_response import ToolInvocationResponse

        return ToolInvocationResponse(status=status, result=result)

    def test_completed_with_result_returns_hit(self):
        from codemie.service.tools.hedging_tool_service import HedgingToolService

        r = HedgingToolService._extract_provider_result(self._response(result="answer"), None)
        assert r.empty is False
        assert r.data == "answer"

    def test_error_status_returns_miss(self):
        from codemie.service.tools.hedging_tool_service import HedgingToolService

        r = HedgingToolService._extract_provider_result(self._response(status="Error", result="x"), None)
        assert r.empty is True

    def test_none_result_returns_miss(self):
        from codemie.service.tools.hedging_tool_service import HedgingToolService

        r = HedgingToolService._extract_provider_result(self._response(result=None), None)
        assert r.empty is True

    def test_output_field_extracts_nested_dict(self):
        from codemie.service.tools.hedging_tool_service import HedgingToolService

        r = HedgingToolService._extract_provider_result(
            self._response(result={"data": {"answer": "42"}}),
            "data.answer",
        )
        assert r.empty is False
        assert r.data == "42"

    def test_output_field_extracts_list_element(self):
        from codemie.service.tools.hedging_tool_service import HedgingToolService

        r = HedgingToolService._extract_provider_result(
            self._response(result={"items": ["first", "second"]}),
            "items.0",
        )
        assert r.data == "first"

    def test_output_field_missing_key_returns_miss(self):
        from codemie.service.tools.hedging_tool_service import HedgingToolService

        r = HedgingToolService._extract_provider_result(
            self._response(result={"data": {}}),
            "data.answer",
        )
        assert r.empty is True

    def test_output_field_bad_list_index_returns_miss(self):
        from codemie.service.tools.hedging_tool_service import HedgingToolService

        r = HedgingToolService._extract_provider_result(
            self._response(result={"items": ["only"]}),
            "items.5",
        )
        assert r.empty is True


# ---------------------------------------------------------------------------
# TestRunFastPathProviderTool
# ---------------------------------------------------------------------------


class TestRunFastPathProviderTool:
    """Tests for _run_fast_path when cfg.provider_tool is set."""

    @pytest.fixture
    def provider_config(self):
        return HedgingConfig(
            provider_tool=HedgingProviderToolDetails(
                provider_name="my-provider",
                toolkit_name="search",
                tool_name="semantic_search",
            ),
            input_mapping={"query": "{{query}}", "user_id": "{{user.id}}"},
            timeout_ms=300,
        )

    @pytest.fixture
    def provider_handler(self, mock_assistant, mock_user, provider_config):
        mock_assistant.hedging_config = provider_config
        mock_assistant.project = "proj-1"
        mock_user.project_names = []
        mock_user.auth_token = "bearer-tok"
        mock_user.name = "Alice"
        mock_user.username = "alice"
        mock_user.email = "alice@example.com"
        return HedgedAssistantHandler(mock_assistant, mock_user, "req-uuid")

    def _run(self, handler, request_, raw_request, invoke_result, headers=None):
        with patch(
            "codemie.rest_api.handlers.hedged_handler.HedgingToolService.invoke_provider_tool",
            return_value=invoke_result,
        ):
            attempt = handler._run_fast_path(request_, raw_request.state.uuid, headers or {})
        return attempt.result

    def test_provider_hit_stored_in_holder(self, provider_handler, request_, raw_request):
        hit = HedgeToolResult(empty=False, data="fast answer")
        result = self._run(provider_handler, request_, raw_request, hit)

        parsed = json.loads(result)
        assert parsed["empty"] is False
        assert parsed["data"] == "fast answer"

    def test_provider_miss_stored_in_holder(self, provider_handler, request_, raw_request):
        miss = HedgeToolResult(empty=True)
        result = self._run(provider_handler, request_, raw_request, miss)

        parsed = json.loads(result)
        assert parsed["empty"] is True

    def test_provider_invoke_error_logs_and_done_set(self, provider_handler, request_, raw_request):
        with (
            patch(
                "codemie.rest_api.handlers.hedged_handler.HedgingToolService.invoke_provider_tool",
                side_effect=ValueError("provider not found"),
            ),
            patch("codemie.rest_api.handlers.hedged_handler.logger") as mock_logger,
        ):
            attempt = provider_handler._run_fast_path(request_, raw_request.state.uuid, {})

        assert attempt.result is None
        mock_logger.error.assert_called_once()

    def test_tool_display_name_uses_provider_path(self, provider_handler):
        name = provider_handler._tool_display_name()
        assert name == "my-provider/search/semantic_search"

    def test_input_mapping_context_passed_to_invoke(self, provider_handler, request_, raw_request):
        hit = HedgeToolResult(empty=False, data="ok")

        with patch(
            "codemie.rest_api.handlers.hedged_handler.HedgingToolService.invoke_provider_tool",
            return_value=hit,
        ) as mock_invoke:
            provider_handler._run_fast_path(request_, raw_request.state.uuid, {})

        mock_invoke.assert_called_once()
        call_kwargs = mock_invoke.call_args[1]
        assert call_kwargs["project_id"] == "proj-1"
        assert call_kwargs["request_uuid"] == "req-uuid"
        # template_context should contain the query
        assert call_kwargs["template_context"]["query"] == "What is X?"


# ---------------------------------------------------------------------------
# TestHandleStreamAgentWinsRace
# ---------------------------------------------------------------------------


class TestHandleStreamAgentWinsRace:
    """Coordinator uses the agent result when fast path times out before returning."""

    def _run_stream(self, handler, request_, raw_request):
        """
        Simulate the race where the fast path has not yet put anything in its queue
        by the time the coordinator's timeout expires (queue.Empty is raised on
        get_nowait()).  The coordinator must fall through to the agent path.
        """
        from codemie.core.thread import ThreadedGenerator  # lazy: avoids circular import at module level

        real_tg = ThreadedGenerator(request_uuid="req-uuid", user_id="user-1", conversation_id="")

        def slow_fast_path(*args, **kwargs):
            # Never puts anything in the queue within the coordinator timeout window.
            # Simulated by making instantiate raise so fast path exits without writing.
            pass

        def agent_stream():
            real_tg.queue.put(AGENT_CHUNK)
            real_tg.queue.put(StopIteration)

        mock_agent = Mock()
        mock_agent.last_generation_result = None
        mock_agent.stream = agent_stream

        # Patch the fast-path result queue's get_nowait to raise queue.Empty,
        # which is exactly what the coordinator handles as "timeout, agent wins".
        with (
            patch(
                "codemie.rest_api.handlers.hedged_handler.HedgingToolService.instantiate",
                side_effect=ValueError("timeout simulation — fast path never finished"),
            ),
            patch("codemie.rest_api.handlers.hedged_handler.AssistantService.build_agent", return_value=mock_agent),
            patch("codemie.rest_api.handlers.hedged_handler.ThreadedGenerator", return_value=real_tg),
            patch("codemie.rest_api.handlers.hedged_handler.extract_custom_headers", return_value={}),
            patch("codemie.rest_api.handlers.hedged_handler.set_disable_prompt_cache"),
            patch.object(handler, "save_chat_history") as mock_save,
            patch("codemie.rest_api.handlers.hedged_handler.logger"),
        ):
            response = handler._handle_stream(request_, raw_request, time())
            chunks = asyncio.run(_consume(response.body_iterator))

        return chunks, mock_save, real_tg

    def test_agent_chunk_is_yielded_when_fast_path_times_out(self, handler, request_, raw_request):
        """At least one chunk from the agent is forwarded to the client."""
        chunks, _, _ = self._run_stream(handler, request_, raw_request)

        assert len(chunks) >= 1
        data = json.loads(chunks[0])
        assert data["generated"] == "agent answer"

    def test_fast_path_win_chunk_not_present(self, handler, request_, raw_request):
        """The coordinator must NOT emit the fast-path 'last=True' generated chunk."""
        chunks, _, _ = self._run_stream(handler, request_, raw_request)

        for chunk in chunks:
            parsed = json.loads(chunk)
            # Fast-path win chunks have last=True; agent chunks have last=False or absent.
            assert not parsed.get("last", False), f"Unexpected fast-path win chunk: {chunk}"

    def test_save_chat_history_called_with_agent_response(self, handler, request_, raw_request):
        """History is saved with the agent's generated text, not fast-path data."""
        _, mock_save, _ = self._run_stream(handler, request_, raw_request)

        mock_save.assert_called_once()
        history_data: ChatHistoryData = mock_save.call_args[0][0]
        assert history_data.response == "agent answer"

    def test_agent_queue_not_closed_by_coordinator(self, handler, request_, raw_request):
        """The coordinator must NOT close the agent queue when it takes the agent path."""
        _, _, real_tg = self._run_stream(handler, request_, raw_request)

        assert not real_tg.is_closed()

    def test_warning_logged_for_failed_fast_path_instantiate(self, handler, request_, raw_request):
        """A warning must be logged when the fast-path tool cannot be instantiated."""
        from codemie.core.thread import ThreadedGenerator  # lazy: avoids circular import at module level

        real_tg = ThreadedGenerator(request_uuid="req-uuid", user_id="user-1", conversation_id="")

        def agent_stream():
            real_tg.queue.put(AGENT_CHUNK)
            real_tg.queue.put(StopIteration)

        mock_agent = Mock()
        mock_agent.last_generation_result = None
        mock_agent.stream = agent_stream

        with (
            patch(
                "codemie.rest_api.handlers.hedged_handler.HedgingToolService.instantiate",
                side_effect=ValueError("not found"),
            ),
            patch("codemie.rest_api.handlers.hedged_handler.AssistantService.build_agent", return_value=mock_agent),
            patch("codemie.rest_api.handlers.hedged_handler.ThreadedGenerator", return_value=real_tg),
            patch("codemie.rest_api.handlers.hedged_handler.extract_custom_headers", return_value={}),
            patch("codemie.rest_api.handlers.hedged_handler.set_disable_prompt_cache"),
            patch.object(handler, "save_chat_history"),
            patch("codemie.rest_api.handlers.hedged_handler.logger") as mock_logger,
        ):
            response = handler._handle_stream(request_, raw_request, time())
            asyncio.run(_consume(response.body_iterator))

        mock_logger.warning.assert_called()


# ---------------------------------------------------------------------------
# TestRunFastPathProviderToolDatasource
# ---------------------------------------------------------------------------


class TestRunFastPathProviderToolDatasource:
    """Tests for _run_fast_path when datasource_name is set on HedgingProviderToolDetails."""

    @pytest.fixture
    def provider_config_with_datasource(self):
        return HedgingConfig(
            provider_tool=HedgingProviderToolDetails(
                provider_name="my-provider",
                toolkit_name="search",
                tool_name="semantic_search",
                datasource_name="my-ds",
            ),
            timeout_ms=300,
        )

    @pytest.fixture
    def provider_config_without_datasource(self):
        return HedgingConfig(
            provider_tool=HedgingProviderToolDetails(
                provider_name="my-provider",
                toolkit_name="search",
                tool_name="semantic_search",
            ),
            timeout_ms=300,
        )

    @pytest.fixture
    def ds_handler(self, mock_assistant, mock_user, provider_config_with_datasource):
        mock_assistant.hedging_config = provider_config_with_datasource
        mock_assistant.project = "proj-1"
        mock_user.project_names = []
        mock_user.auth_token = "bearer-tok"
        mock_user.name = "Alice"
        mock_user.username = "alice"
        mock_user.email = "alice@example.com"
        return HedgedAssistantHandler(mock_assistant, mock_user, "req-uuid")

    @pytest.fixture
    def no_ds_handler(self, mock_assistant, mock_user, provider_config_without_datasource):
        mock_assistant.hedging_config = provider_config_without_datasource
        mock_assistant.project = "proj-1"
        mock_user.project_names = []
        mock_user.auth_token = "bearer-tok"
        mock_user.name = "Alice"
        mock_user.username = "alice"
        mock_user.email = "alice@example.com"
        return HedgedAssistantHandler(mock_assistant, mock_user, "req-uuid")

    def _run(self, handler, request_, raw_request, invoke_result, headers=None):
        with patch(
            "codemie.rest_api.handlers.hedged_handler.HedgingToolService.invoke_provider_tool",
            return_value=invoke_result,
        ) as mock_invoke:
            attempt = handler._run_fast_path(request_, raw_request.state.uuid, headers or {})
        return attempt.result, mock_invoke

    def test_datasource_name_forwarded_to_invoke_provider_tool(self, ds_handler, request_, raw_request):
        hit = HedgeToolResult(empty=False, data="fast answer")
        _, mock_invoke = self._run(ds_handler, request_, raw_request, hit)

        mock_invoke.assert_called_once()
        cfg = mock_invoke.call_args[1]["cfg"]
        assert cfg.provider_tool.datasource_name == "my-ds"

    def test_datasource_name_none_forwarded_to_invoke_provider_tool(self, no_ds_handler, request_, raw_request):
        hit = HedgeToolResult(empty=False, data="fast answer")
        _, mock_invoke = self._run(no_ds_handler, request_, raw_request, hit)

        mock_invoke.assert_called_once()
        cfg = mock_invoke.call_args[1]["cfg"]
        assert cfg.provider_tool.datasource_name is None

    def test_datasource_name_helper_returns_configured_value(self, ds_handler):
        assert ds_handler._datasource_name() == "my-ds"

    def test_datasource_name_helper_returns_none_when_unset(self, no_ds_handler):
        assert no_ds_handler._datasource_name() is None

    def _run_with_provider(self, handler, request_, raw_request, invoke_result):
        provider = Mock()
        # _lazy_observe resolves make_observe_decorator at call time; configure it to
        # pass through to fn so the actual _run_fast_path body (and its
        # _emit_fast_path_trace call) executes.
        provider.make_observe_decorator.return_value = lambda **kwargs: lambda fn: fn
        with (
            patch(
                "codemie.rest_api.handlers.hedged_handler.HedgingToolService.invoke_provider_tool",
                return_value=invoke_result,
            ),
            patch(
                "codemie.rest_api.handlers.hedged_handler.get_observability_provider",
                return_value=provider,
            ),
        ):
            handler._run_fast_path(request_, raw_request.state.uuid, {})
        return provider

    def test_datasource_name_in_trace_metadata(self, ds_handler, request_, raw_request):
        hit = HedgeToolResult(empty=False, data="fast answer")
        provider = self._run_with_provider(ds_handler, request_, raw_request, hit)

        obs_metadata = provider.update_current_observation.call_args[1]["metadata"]
        trace_metadata = provider.update_current_trace.call_args[1]["metadata"]
        assert obs_metadata["datasource"] == "my-ds"
        assert trace_metadata["datasource"] == "my-ds"

    def test_datasource_absent_from_metadata_when_unset(self, no_ds_handler, request_, raw_request):
        hit = HedgeToolResult(empty=False, data="fast answer")
        provider = self._run_with_provider(no_ds_handler, request_, raw_request, hit)

        obs_metadata = provider.update_current_observation.call_args[1]["metadata"]
        trace_metadata = provider.update_current_trace.call_args[1]["metadata"]
        assert "datasource" not in obs_metadata
        assert "datasource" not in trace_metadata


# ---------------------------------------------------------------------------
# TestFastPathOtelContextPropagation
# ---------------------------------------------------------------------------


class TestFastPathOtelContextPropagation:
    """Verifies that _handle_stream captures and explicitly attaches the OTEL context
    before spawning the fast-path thread, matching the project-wide thread propagation pattern."""

    def test_fast_path_thread_attaches_otel_context(self, handler, request_, raw_request):
        from codemie.core.thread import ThreadedGenerator

        real_tg = ThreadedGenerator(request_uuid="req-uuid", user_id="user-1", conversation_id="")

        mock_agent = Mock()
        mock_agent.last_generation_result = None
        mock_agent.stream = Mock()

        with (
            patch(
                "codemie.rest_api.handlers.hedged_handler.HedgingToolService.instantiate",
                return_value=_make_fast_tool(_HIT_RESULT),
            ),
            patch("codemie.rest_api.handlers.hedged_handler.AssistantService.build_agent", return_value=mock_agent),
            patch("codemie.rest_api.handlers.hedged_handler.ThreadedGenerator", return_value=real_tg),
            patch("codemie.rest_api.handlers.hedged_handler.extract_custom_headers", return_value={}),
            patch("codemie.rest_api.handlers.hedged_handler.set_disable_prompt_cache"),
            patch("codemie.rest_api.handlers.hedged_handler.get_otel_context_for_thread") as mock_get_ctx,
            patch("codemie.rest_api.handlers.hedged_handler.attach_otel_context") as mock_attach,
            patch("codemie.rest_api.handlers.hedged_handler.detach_otel_context") as mock_detach,
        ):
            response = handler._handle_stream(request_, raw_request, time())
            asyncio.run(_consume(response.body_iterator))

        mock_get_ctx.assert_called_once()
        mock_attach.assert_called_once_with(mock_get_ctx.return_value)
        mock_detach.assert_called_once()


# ---------------------------------------------------------------------------
# TestLoggingContextConversationId (MDDAAIAD-1205 / EPMCDME-13317)
# ---------------------------------------------------------------------------


class TestLoggingContextConversationId:
    """Hedged lifecycle logs must carry conversation_id from the originating request."""

    def _run_handle_stream(self, handler, request_, raw_request, extra_patches=()):
        from contextlib import ExitStack

        from codemie.core.thread import ThreadedGenerator

        real_tg = ThreadedGenerator(
            request_uuid="req-uuid",
            user_id="user-1",
            conversation_id=getattr(request_, "conversation_id", "") or "",
        )
        mock_agent = Mock()
        mock_agent.last_generation_result = None
        mock_agent.stream = Mock()

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "codemie.rest_api.handlers.hedged_handler.HedgingToolService.instantiate",
                    return_value=_make_fast_tool(_HIT_RESULT),
                )
            )
            stack.enter_context(
                patch("codemie.rest_api.handlers.hedged_handler.AssistantService.build_agent", return_value=mock_agent)
            )
            stack.enter_context(
                patch("codemie.rest_api.handlers.hedged_handler.ThreadedGenerator", return_value=real_tg)
            )
            stack.enter_context(
                patch("codemie.rest_api.handlers.hedged_handler.extract_custom_headers", return_value={})
            )
            stack.enter_context(patch("codemie.rest_api.handlers.hedged_handler.set_disable_prompt_cache"))
            stack.enter_context(patch("codemie.rest_api.handlers.hedged_handler.get_otel_context_for_thread"))
            stack.enter_context(patch("codemie.rest_api.handlers.hedged_handler.attach_otel_context"))
            stack.enter_context(patch("codemie.rest_api.handlers.hedged_handler.detach_otel_context"))
            for p in extra_patches:
                stack.enter_context(p)

            response = handler._handle_stream(request_, raw_request, time())
            asyncio.run(_consume(response.body_iterator))

    def test_fast_path_restore_includes_conversation_id(self, handler, request_, raw_request):
        request_.conversation_id = "conv-qa-1205"
        restored: dict[str, str] = {}

        def _capture_restore(snapshot):
            restored.update(snapshot)
            from codemie.configs.logger import set_logging_info as _set

            _set(**snapshot)

        self._run_handle_stream(
            handler,
            request_,
            raw_request,
            extra_patches=(
                patch(
                    "codemie.rest_api.handlers.hedged_handler.restore_logging_context",
                    side_effect=_capture_restore,
                ),
            ),
        )

        assert restored.get("conversation_id") == "conv-qa-1205"
        assert restored.get("user_id") == "user-1"
        assert restored.get("uuid") == "req-uuid"

    def test_none_conversation_id_preserves_existing_in_snapshot(self, handler, request_, raw_request):
        """None on the request means omit: an already-bound conversation_id is kept."""
        from codemie.configs.logger import set_logging_info as real_set

        real_set(uuid="pre", user_id="user-1", conversation_id="conv-keep", user_email="pre@example.com")
        request_.conversation_id = None
        restored: dict[str, str] = {}

        def _capture_restore(snapshot):
            restored.update(snapshot)
            from codemie.configs.logger import set_logging_info as _set

            _set(**snapshot)

        self._run_handle_stream(
            handler,
            request_,
            raw_request,
            extra_patches=(
                patch(
                    "codemie.rest_api.handlers.hedged_handler.restore_logging_context",
                    side_effect=_capture_restore,
                ),
            ),
        )

        assert restored.get("conversation_id") == "conv-keep"
        assert restored.get("user_id") == "user-1"

    def test_set_logging_info_called_before_copy_with_request_fields(self, handler, request_, raw_request):
        request_.conversation_id = "conv-before-copy"
        call_order: list[str] = []
        set_kwargs: dict = {}

        def _set_logging_info(**kwargs):
            call_order.append("set")
            set_kwargs.update(kwargs)
            from codemie.configs.logger import set_logging_info as real_set

            return real_set(**kwargs)

        def _copy_logging_context():
            call_order.append("copy")
            from codemie.configs.logger import copy_logging_context as real_copy

            return real_copy()

        self._run_handle_stream(
            handler,
            request_,
            raw_request,
            extra_patches=(
                patch(
                    "codemie.rest_api.handlers.hedged_handler.set_logging_info",
                    side_effect=_set_logging_info,
                ),
                patch(
                    "codemie.rest_api.handlers.hedged_handler.copy_logging_context",
                    side_effect=_copy_logging_context,
                ),
            ),
        )

        assert call_order[:2] == ["set", "copy"]
        assert set_kwargs.get("conversation_id") == "conv-before-copy"
        assert set_kwargs.get("user_id") == "user-1"
        assert set_kwargs.get("uuid") == "req-uuid"

    def test_hedged_won_log_keeps_conversation_id_after_context_cleared(self, handler, request_, raw_request):
        """Simulate StreamingResponse: request ContextVars cleared before generator runs."""
        import logging

        from codemie.configs.logger import set_logging_info as real_set

        request_.conversation_id = "conv-coordinator-1205"
        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        cap = _Capture()
        codemie_logger = logging.getLogger("codemie")
        codemie_logger.addHandler(cap)
        try:
            from contextlib import ExitStack

            from codemie.core.thread import ThreadedGenerator

            real_tg = ThreadedGenerator(
                request_uuid="req-uuid",
                user_id="user-1",
                conversation_id=request_.conversation_id,
            )
            mock_agent = Mock()
            mock_agent.last_generation_result = None
            mock_agent.stream = Mock()

            with ExitStack() as stack:
                stack.enter_context(
                    patch(
                        "codemie.rest_api.handlers.hedged_handler.HedgingToolService.instantiate",
                        return_value=_make_fast_tool(_HIT_RESULT),
                    )
                )
                stack.enter_context(
                    patch(
                        "codemie.rest_api.handlers.hedged_handler.AssistantService.build_agent",
                        return_value=mock_agent,
                    )
                )
                stack.enter_context(
                    patch("codemie.rest_api.handlers.hedged_handler.ThreadedGenerator", return_value=real_tg)
                )
                stack.enter_context(
                    patch("codemie.rest_api.handlers.hedged_handler.extract_custom_headers", return_value={})
                )
                stack.enter_context(patch("codemie.rest_api.handlers.hedged_handler.set_disable_prompt_cache"))
                stack.enter_context(patch("codemie.rest_api.handlers.hedged_handler.get_otel_context_for_thread"))
                stack.enter_context(patch("codemie.rest_api.handlers.hedged_handler.attach_otel_context"))
                stack.enter_context(patch("codemie.rest_api.handlers.hedged_handler.detach_otel_context"))
                stack.enter_context(patch.object(handler, "save_chat_history"))

                response = handler._handle_stream(request_, raw_request, time())
                # Middleware / request teardown clears ContextVars before body streams.
                real_set(uuid="-", user_id="-", conversation_id="-", user_email="-")
                asyncio.run(_consume(response.body_iterator))
        finally:
            codemie_logger.removeHandler(cap)

        hedged = [
            r
            for r in records
            if "[HEDGED] fast-path won" in str(r.getMessage()) or "[HEDGED] agent path won" in str(r.getMessage())
        ]
        assert hedged, "expected [HEDGED] fast-path/agent path won lifecycle log"
        for record in hedged:
            assert record.conversation_id == "conv-coordinator-1205"
            assert record.user_id == "user-1"
            assert record.uuid == "req-uuid"


# ---------------------------------------------------------------------------
# TestUserMessageReceivedAtPropagation
# ---------------------------------------------------------------------------


class TestUserMessageReceivedAtPropagation:
    """user_message_received_at must reach ChatHistoryData on all three save paths."""

    _TS = datetime(2026, 9, 4, 12, 0, 0)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _run_stream_fast_path_win(self, handler, request_, raw_request, ts):
        from codemie.core.thread import ThreadedGenerator

        real_tg = ThreadedGenerator(request_uuid="req-uuid", user_id="user-1", conversation_id="")
        mock_agent = Mock()
        mock_agent.last_generation_result = None
        mock_agent.stream = Mock()

        with (
            patch(
                "codemie.rest_api.handlers.hedged_handler.HedgingToolService.instantiate",
                return_value=_make_fast_tool(_HIT_RESULT),
            ),
            patch("codemie.rest_api.handlers.hedged_handler.AssistantService.build_agent", return_value=mock_agent),
            patch("codemie.rest_api.handlers.hedged_handler.ThreadedGenerator", return_value=real_tg),
            patch("codemie.rest_api.handlers.hedged_handler.extract_custom_headers", return_value={}),
            patch("codemie.rest_api.handlers.hedged_handler.set_disable_prompt_cache"),
            patch.object(handler, "save_chat_history") as mock_save,
        ):
            response = handler._handle_stream(request_, raw_request, time(), user_message_received_at=ts)
            asyncio.run(_consume(response.body_iterator))

        return mock_save

    def _run_stream_agent_wins(self, handler, request_, raw_request, ts):
        from codemie.core.thread import ThreadedGenerator

        real_tg = ThreadedGenerator(request_uuid="req-uuid", user_id="user-1", conversation_id="")

        def agent_stream():
            real_tg.queue.put(AGENT_CHUNK)
            real_tg.queue.put(StopIteration)

        mock_agent = Mock()
        mock_agent.last_generation_result = None
        mock_agent.stream = agent_stream

        with (
            patch(
                "codemie.rest_api.handlers.hedged_handler.HedgingToolService.instantiate",
                return_value=_make_fast_tool(_MISS_RESULT),
            ),
            patch("codemie.rest_api.handlers.hedged_handler.AssistantService.build_agent", return_value=mock_agent),
            patch("codemie.rest_api.handlers.hedged_handler.ThreadedGenerator", return_value=real_tg),
            patch("codemie.rest_api.handlers.hedged_handler.extract_custom_headers", return_value={}),
            patch("codemie.rest_api.handlers.hedged_handler.set_disable_prompt_cache"),
            patch.object(handler, "save_chat_history") as mock_save,
        ):
            response = handler._handle_stream(request_, raw_request, time(), user_message_received_at=ts)
            asyncio.run(_consume(response.body_iterator))

        return mock_save

    # ------------------------------------------------------------------
    # stream: fast-path win
    # ------------------------------------------------------------------

    def test_stream_fast_path_win_passes_timestamp(self, handler, request_, raw_request):
        mock_save = self._run_stream_fast_path_win(handler, request_, raw_request, self._TS)

        mock_save.assert_called_once()
        history_data: ChatHistoryData = mock_save.call_args[0][0]
        assert history_data.user_message_received_at == self._TS

    def test_stream_fast_path_win_none_timestamp_is_none(self, handler, request_, raw_request):
        mock_save = self._run_stream_fast_path_win(handler, request_, raw_request, None)

        history_data: ChatHistoryData = mock_save.call_args[0][0]
        assert history_data.user_message_received_at is None

    # ------------------------------------------------------------------
    # stream: agent wins
    # ------------------------------------------------------------------

    def test_stream_agent_wins_passes_timestamp(self, handler, request_, raw_request):
        mock_save = self._run_stream_agent_wins(handler, request_, raw_request, self._TS)

        mock_save.assert_called_once()
        history_data: ChatHistoryData = mock_save.call_args[0][0]
        assert history_data.user_message_received_at == self._TS

    def test_stream_agent_wins_none_timestamp_is_none(self, handler, request_, raw_request):
        mock_save = self._run_stream_agent_wins(handler, request_, raw_request, None)

        history_data: ChatHistoryData = mock_save.call_args[0][0]
        assert history_data.user_message_received_at is None

    # ------------------------------------------------------------------
    # sync: fast-path win
    # ------------------------------------------------------------------

    def test_sync_fast_path_win_passes_timestamp(self, handler, request_, raw_request):
        with (
            patch(
                "codemie.rest_api.handlers.hedged_handler.HedgingToolService.instantiate",
                return_value=_make_fast_tool(_HIT_RESULT),
            ),
            patch("codemie.rest_api.handlers.hedged_handler.AssistantService.build_agent"),
            patch("codemie.rest_api.handlers.hedged_handler.extract_custom_headers", return_value={}),
            patch("codemie.rest_api.handlers.hedged_handler.set_disable_prompt_cache"),
            patch.object(handler, "save_chat_history") as mock_save,
        ):
            handler._handle_sync(request_, raw_request, time(), user_message_received_at=self._TS)

        mock_save.assert_called_once()
        history_data: ChatHistoryData = mock_save.call_args[0][0]
        assert history_data.user_message_received_at == self._TS

    def test_sync_fast_path_win_none_timestamp_is_none(self, handler, request_, raw_request):
        with (
            patch(
                "codemie.rest_api.handlers.hedged_handler.HedgingToolService.instantiate",
                return_value=_make_fast_tool(_HIT_RESULT),
            ),
            patch("codemie.rest_api.handlers.hedged_handler.AssistantService.build_agent"),
            patch("codemie.rest_api.handlers.hedged_handler.extract_custom_headers", return_value={}),
            patch("codemie.rest_api.handlers.hedged_handler.set_disable_prompt_cache"),
            patch.object(handler, "save_chat_history") as mock_save,
        ):
            handler._handle_sync(request_, raw_request, time(), user_message_received_at=None)

        history_data: ChatHistoryData = mock_save.call_args[0][0]
        assert history_data.user_message_received_at is None
