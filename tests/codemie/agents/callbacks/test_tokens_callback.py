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

import pytest
from uuid import UUID, uuid4
from unittest.mock import MagicMock, patch
import asyncio
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import LLMResult, Generation, ChatGeneration

from codemie.agents.callbacks.tokens_callback import TokensCalculationCallback
from codemie.core.router import ClassifierCall, RoutingDecision
from codemie.core.routing_info import ClassifierUsage, RoutingInfo
from codemie.service.request_summary_manager import LLMRun
from codemie.service.llm_service.llm_service import LLMService
from codemie.configs.llm_config import CostConfig


def _llm_result(content: str = "hi") -> LLMResult:
    message = AIMessage(content=content, usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
    return LLMResult(generations=[[ChatGeneration(message=message)]])


@pytest.fixture
def callback():
    return TokensCalculationCallback(request_id="test_request_id", llm_model=LLMService.BASE_NAME_GPT_41_MINI)


@pytest.fixture
def sample_llm_result():
    message = BaseMessage(type="", content="Test response", usage_metadata={"input_tokens": 10, "output_tokens": 20})
    generation = ChatGeneration(text="Test response", message=message)
    return LLMResult(generations=[[generation]])


@pytest.fixture
def mock_model_costs():
    return CostConfig(
        input=0.001,
        output=0.002,
        input_cost_per_token_batches=0.0001,
        output_cost_per_token_batches=0.0002,
        cache_read_input_token_cost=0.0005,
    )


def test_initialization(callback):
    """Test the proper initialization of TokensCalculationCallback"""
    assert callback.request_id == "test_request_id"
    assert callback.llm_model == LLMService.BASE_NAME_GPT_41_MINI
    assert callback.input_tokens == 0
    assert callback.output_tokens == 0
    assert isinstance(callback.internal_run_id, str)


@patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
@patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost')
def test_on_llm_end_successful(
    mock_calculate_token_cost, mock_update_llm_run, mock_get_model_cost, callback, sample_llm_result, mock_model_costs
):
    """Test successful execution of on_llm_end with cached_tokens_money_spent"""
    # Setup
    mock_get_model_cost.return_value = mock_model_costs
    mock_calculate_token_cost.return_value = (
        0.051,
        0.0,
        0.0,
    )  # Returns tuple (total_cost, cached_cost, cache_creation_cost)

    # Execute
    asyncio.run(callback.on_llm_end(response=sample_llm_result, run_id=UUID('12345678-1234-5678-1234-567812345678')))

    # Verify
    # Full calculation would be:
    # (10 * 0.001) + (10 * 0.0001) + (20 * 0.002) + (20 * 0.0002) + (0 * 0.0005) = 0.051

    # Verify calculate_token_cost was called with correct parameters
    mock_calculate_token_cost.assert_called_once_with(
        llm_model=LLMService.BASE_NAME_GPT_41_MINI,
        cost_config=mock_model_costs,
        input_tokens=10,
        output_tokens=20,
        cached_tokens=0,
        cache_creation_tokens=0,
    )

    mock_update_llm_run.assert_called_once()
    call_args = mock_update_llm_run.call_args[1]

    assert call_args['request_id'] == "test_request_id"
    assert isinstance(call_args['llm_run'], LLMRun)
    assert call_args['llm_run'].input_tokens == 10
    assert call_args['llm_run'].output_tokens == 20
    assert call_args['llm_run'].cached_tokens == 0
    assert call_args['llm_run'].money_spent == 0.051
    assert call_args['llm_run'].cached_tokens_money_spent == 0.0
    assert call_args['llm_run'].cached_tokens_creation_cost == 0.0
    assert call_args['llm_run'].llm_model == LLMService.BASE_NAME_GPT_41_MINI


@patch('codemie.agents.callbacks.tokens_callback.logger.error')
def test_on_llm_end_error_handling(mock_logger_error, callback):
    """Test error handling in on_llm_end"""
    # Create a malformed LLMResult that will cause an exception
    broken_result = LLMResult(generations=[[Generation(text="test")]])  # Missing usage_metadata

    asyncio.run(callback.on_llm_end(response=broken_result, run_id=UUID('12345678-1234-5678-1234-567812345678')))

    # Verify that the error was logged
    mock_logger_error.assert_called_once()
    assert "Error while calculating tokens" in mock_logger_error.call_args[0][0]


def test_on_llm_end_with_empty_generations(callback):
    """Test on_llm_end with empty generations skips update (no tokens to track)."""
    empty_result = LLMResult(generations=[])

    with (
        patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost'),
        patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run') as mock_update_llm_run,
        patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost'),
    ):
        asyncio.run(callback.on_llm_end(response=empty_result, run_id=UUID('12345678-1234-5678-1234-567812345678')))

        mock_update_llm_run.assert_not_called()


@patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
@patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost')
def test_on_llm_end_with_cached_tokens(mock_calculate_token_cost, mock_update_llm_run, mock_get_model_cost, callback):
    """Test on_llm_end with cached tokens (Claude prompt caching)"""
    message = BaseMessage(
        type="",
        content="Test response",
        usage_metadata={
            "input_tokens": 5000,
            "output_tokens": 200,
            "input_token_details": {"cache_read": 4800},
        },
    )
    generation = ChatGeneration(text="Test response", message=message)
    result_with_cache = LLMResult(generations=[[generation]])

    # Setup
    mock_model_costs = CostConfig(
        input=0.000003,
        output=0.000015,
        cache_read_input_token_cost=0.0000003,
    )
    mock_get_model_cost.return_value = mock_model_costs
    # Total: (200 * 0.000003) + (4800 * 0.0000003) + (200 * 0.000015) = 0.0006 + 0.00144 + 0.003 = 0.00504
    mock_calculate_token_cost.return_value = (0.00504, 0.00144, 0.0)  # (total_cost, cached_cost, cache_creation_cost)

    asyncio.run(callback.on_llm_end(response=result_with_cache, run_id=UUID('12345678-1234-5678-1234-567812345678')))

    # Verify calculate_token_cost was called with cached tokens
    mock_calculate_token_cost.assert_called_once_with(
        llm_model=LLMService.BASE_NAME_GPT_41_MINI,
        cost_config=mock_model_costs,
        input_tokens=5000,
        output_tokens=200,
        cached_tokens=4800,
        cache_creation_tokens=0,
    )

    # Verify LLMRun includes cached tokens and cached cost
    mock_update_llm_run.assert_called_once()
    call_args = mock_update_llm_run.call_args[1]

    assert call_args['llm_run'].input_tokens == 5000
    assert call_args['llm_run'].output_tokens == 200
    assert call_args['llm_run'].cached_tokens == 4800
    assert call_args['llm_run'].money_spent == 0.00504
    assert call_args['llm_run'].cached_tokens_money_spent == 0.00144
    assert call_args['llm_run'].cached_tokens_creation_cost == 0.0


@patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
@patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost')
def test_on_llm_end_with_cache_creation_tokens(
    mock_calculate_token_cost, mock_update_llm_run, mock_get_model_cost, callback
):
    """Test on_llm_end with cache creation tokens (prompt caching - first request)"""
    message = BaseMessage(
        type="",
        content="Test response",
        usage_metadata={
            "input_tokens": 5000,
            "output_tokens": 200,
            "input_token_details": {
                "cache_creation": 4500,  # Tokens used to create cache
                "cache_read": 0,  # No cache read on first request
            },
        },
    )
    generation = ChatGeneration(text="Test response", message=message)
    result_with_cache_creation = LLMResult(generations=[[generation]])

    # Setup - Claude model with cache creation support
    mock_model_costs = CostConfig(
        input=0.000003,
        output=0.000015,
        cache_creation_input_token_cost=0.00000375,  # 1.25x input cost
        cache_read_input_token_cost=0.0000003,  # 0.1x input cost
    )
    mock_get_model_cost.return_value = mock_model_costs
    # Total: (500 prompt * 0.000003) + (4500 cache_creation * 0.00000375) + (200 output * 0.000015)
    # = 0.0015 + 0.016875 + 0.003 = 0.021375
    mock_calculate_token_cost.return_value = (0.021375, 0.0, 0.016875)  # (total_cost, cached_cost, cache_creation_cost)

    asyncio.run(
        callback.on_llm_end(response=result_with_cache_creation, run_id=UUID('12345678-1234-5678-1234-567812345678'))
    )

    # Verify calculate_token_cost was called with cache_creation_tokens
    mock_calculate_token_cost.assert_called_once_with(
        llm_model=LLMService.BASE_NAME_GPT_41_MINI,
        cost_config=mock_model_costs,
        input_tokens=5000,
        output_tokens=200,
        cached_tokens=0,
        cache_creation_tokens=4500,
    )

    # Verify LLMRun includes cache creation cost
    mock_update_llm_run.assert_called_once()
    call_args = mock_update_llm_run.call_args[1]

    assert call_args['llm_run'].input_tokens == 5000
    assert call_args['llm_run'].output_tokens == 200
    assert call_args['llm_run'].cached_tokens == 0
    assert call_args['llm_run'].money_spent == 0.021375
    assert call_args['llm_run'].cached_tokens_money_spent == 0.0
    assert call_args['llm_run'].cached_tokens_creation_cost == 0.016875


@patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
@patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost')
def test_on_llm_end_with_cache_creation_and_read(
    mock_calculate_token_cost, mock_update_llm_run, mock_get_model_cost, callback
):
    """Test on_llm_end with both cache creation and cache read tokens (mixed scenario)"""
    message = BaseMessage(
        type="",
        content="Test response",
        usage_metadata={
            "input_tokens": 6000,
            "output_tokens": 150,
            "input_token_details": {
                "cache_creation": 1000,  # Some new tokens added to cache
                "cache_read": 4500,  # Most tokens read from existing cache
            },
        },
    )
    generation = ChatGeneration(text="Test response", message=message)
    result_mixed = LLMResult(generations=[[generation]])

    # Setup
    mock_model_costs = CostConfig(
        input=0.000003,
        output=0.000015,
        cache_creation_input_token_cost=0.00000375,
        cache_read_input_token_cost=0.0000003,
    )
    mock_get_model_cost.return_value = mock_model_costs
    # Total: (500 prompt * 0.000003) + (1000 cache_creation * 0.00000375) + (4500 cache_read * 0.0000003) + (150 output * 0.000015)
    # = 0.0015 + 0.00375 + 0.00135 + 0.00225 = 0.00885
    mock_calculate_token_cost.return_value = (0.00885, 0.00135, 0.00375)  # (total, cached_cost, cache_creation_cost)

    asyncio.run(callback.on_llm_end(response=result_mixed, run_id=UUID('12345678-1234-5678-1234-567812345678')))

    # Verify calculate_token_cost was called with both cache_creation and cached_tokens
    mock_calculate_token_cost.assert_called_once_with(
        llm_model=LLMService.BASE_NAME_GPT_41_MINI,
        cost_config=mock_model_costs,
        input_tokens=6000,
        output_tokens=150,
        cached_tokens=4500,
        cache_creation_tokens=1000,
    )

    # Verify LLMRun includes both cache costs
    mock_update_llm_run.assert_called_once()
    call_args = mock_update_llm_run.call_args[1]

    assert call_args['llm_run'].input_tokens == 6000
    assert call_args['llm_run'].output_tokens == 150
    assert call_args['llm_run'].cached_tokens == 4500
    assert call_args['llm_run'].money_spent == 0.00885
    assert call_args['llm_run'].cached_tokens_money_spent == 0.00135
    assert call_args['llm_run'].cached_tokens_creation_cost == 0.00375


@patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
@patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost')
def test_on_llm_end_with_no_cache_cost_config(
    mock_calculate_token_cost, mock_update_llm_run, mock_get_model_cost, callback
):
    """Test on_llm_end with model that doesn't support caching (cache_read_input_token_cost=None)"""
    message = BaseMessage(type="", content="Test response", usage_metadata={"input_tokens": 1000, "output_tokens": 500})
    generation = ChatGeneration(text="Test response", message=message)
    result = LLMResult(generations=[[generation]])

    # Setup - model without cache cost (e.g., GPT-4o)
    mock_model_costs = CostConfig(
        input=0.0000025,
        output=0.00001,
        cache_read_input_token_cost=None,  # No caching support
    )
    mock_get_model_cost.return_value = mock_model_costs
    mock_calculate_token_cost.return_value = (0.0075, 0.0, 0.0)  # No cached cost, no cache creation cost

    asyncio.run(callback.on_llm_end(response=result, run_id=UUID('12345678-1234-5678-1234-567812345678')))

    # Verify cached tokens and cost are 0
    call_args = mock_update_llm_run.call_args[1]
    assert call_args['llm_run'].cached_tokens == 0
    assert call_args['llm_run'].cached_tokens_money_spent == 0.0
    assert call_args['llm_run'].cached_tokens_creation_cost == 0.0


@patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
@patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost')
def test_on_llm_error_records_partial_tokens(
    mock_calculate_token_cost, mock_update_llm_run, mock_get_model_cost, callback, mock_model_costs
):
    """on_llm_error records token usage when the provider returns a partial response."""
    message = BaseMessage(type="", content="", usage_metadata={"input_tokens": 100, "output_tokens": 5})
    generation = ChatGeneration(text="", message=message)
    partial_response = LLMResult(generations=[[generation]])

    mock_get_model_cost.return_value = mock_model_costs
    mock_calculate_token_cost.return_value = (0.0001, 0.0, 0.0)

    callback.on_llm_error(
        error=RuntimeError("stream interrupted"),
        run_id=UUID('12345678-1234-5678-1234-567812345678'),
        response=partial_response,
    )

    mock_calculate_token_cost.assert_called_once_with(
        llm_model=LLMService.BASE_NAME_GPT_41_MINI,
        cost_config=mock_model_costs,
        input_tokens=100,
        output_tokens=5,
        cached_tokens=0,
        cache_creation_tokens=0,
    )
    mock_update_llm_run.assert_called_once()
    call_args = mock_update_llm_run.call_args[1]
    assert call_args['llm_run'].input_tokens == 100
    assert call_args['llm_run'].output_tokens == 5
    assert call_args['llm_run'].money_spent == 0.0001


def test_on_llm_error_skips_update_when_no_response(callback):
    """on_llm_error does nothing when the provider sends no response (no kwargs response)."""
    with patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run') as mock_update:
        callback.on_llm_error(
            error=RuntimeError("no response"),
            run_id=UUID('12345678-1234-5678-1234-567812345678'),
        )
        mock_update.assert_not_called()


def test_on_llm_error_skips_update_when_zero_tokens(callback):
    """on_llm_error does nothing when the partial response has zero token counts."""
    message = BaseMessage(type="", content="", usage_metadata={"input_tokens": 0, "output_tokens": 0})
    generation = ChatGeneration(text="", message=message)
    zero_response = LLMResult(generations=[[generation]])

    with patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run') as mock_update:
        callback.on_llm_error(
            error=RuntimeError("zero tokens"),
            run_id=UUID('12345678-1234-5678-1234-567812345678'),
            response=zero_response,
        )
        mock_update.assert_not_called()


@patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost')
@patch('codemie.agents.callbacks.tokens_callback.config')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
@patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost')
def test_on_llm_end_uses_proxy_cost_header(
    mock_calculate_token_cost, mock_update_llm_run, mock_config, mock_get_model_cost, callback
):
    """When x-litellm-response-cost header is present and proxy tracking is on, use it directly."""
    mock_config.LLM_PROXY_ENABLED = True
    mock_config.LLM_PROXY_TRACK_USAGE = True
    message = BaseMessage(
        type="",
        content="Test response",
        usage_metadata={"input_tokens": 10, "output_tokens": 20},
    )
    generation = ChatGeneration(
        text="Test response",
        message=message,
        generation_info={"headers": {"x-litellm-response-cost": "0.0042"}},
    )
    result = LLMResult(generations=[[generation]])

    mock_get_model_cost.return_value = CostConfig(input=0.001, output=0.002)
    mock_calculate_token_cost.return_value = (0.012, 0.0, 0.0)

    asyncio.run(callback.on_llm_end(response=result, run_id=UUID('12345678-1234-5678-1234-567812345678')))

    # Proxy cost is authoritative: calculate_token_cost is not consulted at all in this case.
    mock_calculate_token_cost.assert_not_called()
    mock_update_llm_run.assert_called_once()
    call_args = mock_update_llm_run.call_args[1]
    assert call_args['llm_run'].money_spent == 0.0042
    assert call_args['llm_run'].cached_tokens_money_spent == 0.0
    assert call_args['llm_run'].cached_tokens_creation_cost == 0.0
    assert call_args['llm_run'].input_tokens == 10
    assert call_args['llm_run'].output_tokens == 20


@patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
@patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost')
def test_on_llm_end_falls_back_to_calculate_when_header_absent(
    mock_calculate_token_cost, mock_update_llm_run, mock_get_model_cost, callback
):
    """When x-litellm-response-cost header is absent, fall back to calculate_token_cost."""
    message = BaseMessage(
        type="",
        content="Test response",
        usage_metadata={"input_tokens": 10, "output_tokens": 20},
    )
    generation = ChatGeneration(text="Test response", message=message)
    result = LLMResult(generations=[[generation]])

    mock_get_model_cost.return_value = CostConfig(input=0.001, output=0.002)
    mock_calculate_token_cost.return_value = (0.05, 0.0, 0.0)

    asyncio.run(callback.on_llm_end(response=result, run_id=UUID('12345678-1234-5678-1234-567812345678')))

    mock_calculate_token_cost.assert_called_once()
    call_args = mock_update_llm_run.call_args[1]
    assert call_args['llm_run'].money_spent == 0.05


@patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
@patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost')
def test_on_llm_end_uses_response_metadata_model_name_when_generation_info_has_no_model(
    mock_calculate_token_cost, mock_update_llm_run, mock_get_model_cost, callback
):
    """Plain (non-proxied) providers embed the actually-served model in response_metadata
    (e.g. ChatOpenAI's `model_name`), not generation_info — this must still resolve
    billed_model for the cost-model lookup."""
    message = AIMessage(
        content="Test response",
        usage_metadata={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        response_metadata={"model_name": "gpt-4o-2024-08-06"},
    )
    generation = ChatGeneration(text="Test response", message=message)
    result = LLMResult(generations=[[generation]])

    mock_get_model_cost.return_value = CostConfig(input=0.001, output=0.002)
    mock_calculate_token_cost.return_value = (0.05, 0.0, 0.0)

    asyncio.run(callback.on_llm_end(response=result, run_id=UUID('12345678-1234-5678-1234-567812345678')))

    mock_get_model_cost.assert_called_once_with("gpt-4o-2024-08-06")
    mock_calculate_token_cost.assert_called_once()
    assert mock_calculate_token_cost.call_args[1]["llm_model"] == "gpt-4o-2024-08-06"
    mock_update_llm_run.assert_called_once()


@patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost')
@patch('codemie.agents.callbacks.tokens_callback.config')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
@patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost')
def test_on_llm_end_uses_litellm_cost_from_generation_info(
    mock_calculate_token_cost, mock_update_llm_run, mock_config, mock_get_model_cost, callback
):
    """When litellm_cost is in generation_info and proxy tracking is on, use it directly."""
    mock_config.LLM_PROXY_ENABLED = True
    mock_config.LLM_PROXY_TRACK_USAGE = True
    message = BaseMessage(
        type="",
        content="Test response",
        usage_metadata={"input_tokens": 10, "output_tokens": 20},
    )
    generation = ChatGeneration(
        text="Test response",
        message=message,
        generation_info={"litellm_cost": 0.0099},
    )
    result = LLMResult(generations=[[generation]])

    mock_get_model_cost.return_value = CostConfig(input=0.001, output=0.002)
    mock_calculate_token_cost.return_value = (0.012, 0.0, 0.0)

    asyncio.run(callback.on_llm_end(response=result, run_id=UUID('12345678-1234-5678-1234-567812345678')))

    # Proxy cost is authoritative: calculate_token_cost is not consulted at all in this case.
    mock_calculate_token_cost.assert_not_called()
    call_args = mock_update_llm_run.call_args[1]
    assert call_args['llm_run'].money_spent == 0.0099
    assert call_args['llm_run'].cached_tokens_money_spent == 0.0
    assert call_args['llm_run'].cached_tokens_creation_cost == 0.0


@patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost')
@patch('codemie.agents.callbacks.tokens_callback.config')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
@patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost')
def test_on_llm_end_falls_back_when_cost_header_invalid(
    mock_calculate_token_cost, mock_update_llm_run, mock_config, mock_get_model_cost, callback
):
    """When x-litellm-response-cost is a non-numeric string, fall back to calculate_token_cost."""
    mock_config.LLM_PROXY_ENABLED = True
    mock_config.LLM_PROXY_TRACK_USAGE = True
    message = BaseMessage(
        type="",
        content="Test response",
        usage_metadata={"input_tokens": 10, "output_tokens": 20},
    )
    for bad_value in ("None", "", "error"):
        mock_calculate_token_cost.reset_mock()
        mock_update_llm_run.reset_mock()
        generation = ChatGeneration(
            text="Test response",
            message=message,
            generation_info={"headers": {"x-litellm-response-cost": bad_value}},
        )
        result = LLMResult(generations=[[generation]])
        mock_get_model_cost.return_value = CostConfig(input=0.001, output=0.002)
        mock_calculate_token_cost.return_value = (0.05, 0.0, 0.0)

        asyncio.run(callback.on_llm_end(response=result, run_id=UUID('12345678-1234-5678-1234-567812345678')))

        mock_calculate_token_cost.assert_called_once()


@pytest.mark.parametrize(
    "proxy_enabled,track_usage",
    [
        (False, True),
        (True, False),
        (False, False),
    ],
)
@patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost')
@patch('codemie.agents.callbacks.tokens_callback.config')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
@patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost')
def test_on_llm_end_ignores_proxy_cost_when_gate_disabled(
    mock_calculate_token_cost,
    mock_update_llm_run,
    mock_config,
    mock_get_model_cost,
    proxy_enabled,
    track_usage,
    callback,
):
    """Proxy cost is ignored when LLM_PROXY_ENABLED or LLM_PROXY_TRACK_USAGE is False."""
    mock_config.LLM_PROXY_ENABLED = proxy_enabled
    mock_config.LLM_PROXY_TRACK_USAGE = track_usage
    message = BaseMessage(
        type="",
        content="Test response",
        usage_metadata={"input_tokens": 5, "output_tokens": 10},
    )
    generation = ChatGeneration(
        text="Test response",
        message=message,
        generation_info={
            "litellm_cost": 0.9999,
            "headers": {"x-litellm-response-cost": "0.9999"},
        },
    )
    result = LLMResult(generations=[[generation]])
    mock_get_model_cost.return_value = CostConfig(input=0.001, output=0.002)
    mock_calculate_token_cost.return_value = (0.01, 0.0, 0.0)

    asyncio.run(callback.on_llm_end(response=result, run_id=UUID('12345678-1234-5678-1234-567812345678')))

    mock_calculate_token_cost.assert_called_once()
    call_args = mock_update_llm_run.call_args[1]
    assert call_args['llm_run'].money_spent == 0.01


@patch('codemie.agents.callbacks.tokens_callback.logger.debug')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
def test_on_llm_end_skips_litellm_whole_response_cache_hit(mock_update_llm_run, mock_logger_debug, callback):
    """LiteLLM whole-response cache hits must not create usage summaries."""
    message = BaseMessage(
        type="",
        content="Cached response",
        usage_metadata={"input_tokens": 10, "output_tokens": 20},
        response_metadata={"cache_hit": True},
    )
    result = LLMResult(generations=[[ChatGeneration(text="Cached response", message=message)]])

    asyncio.run(callback.on_llm_end(response=result, run_id=UUID('12345678-1234-5678-1234-567812345678')))

    mock_update_llm_run.assert_not_called()
    mock_logger_debug.assert_any_call(
        "Skipping LangGraph usage tracking for LiteLLM cache hit: "
        "request_id=test_request_id model=gpt-4.1-mini estimated_spend_skipped=unknown"
    )


@patch('codemie.agents.callbacks.tokens_callback.logger.debug')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
def test_on_llm_end_skips_litellm_proxy_lru_cache_hit(mock_update_llm_run, mock_logger_debug, callback):
    """LiteLLM proxy x-litellm-cache-hit header must suppress usage tracking."""
    message = BaseMessage(
        type="",
        content="Cached response",
        usage_metadata={"input_tokens": 10, "output_tokens": 20},
    )
    generation = ChatGeneration(
        text="Cached response",
        message=message,
        generation_info={"headers": {"x-litellm-cache-key": "some-cache-key-hash"}},
    )
    result = LLMResult(generations=[[generation]])

    asyncio.run(callback.on_llm_end(response=result, run_id=UUID('12345678-1234-5678-1234-567812345678')))

    mock_update_llm_run.assert_not_called()
    mock_logger_debug.assert_any_call(
        "Skipping LangGraph usage tracking for LiteLLM proxy cache hit (x-litellm-cache-key): "
        "request_id=test_request_id model=gpt-4.1-mini"
    )


def _make_switchyard_decision(**overrides: object):
    """Build a minimal RoutingDecision for tests without depending on pick_model()'s internals."""
    import dataclasses

    base = RoutingDecision(
        model="claude-haiku-4-5-20251001",
        tier="efficient",
    )
    return dataclasses.replace(base, **overrides)


def _make_router(
    *,
    routing_info: RoutingInfo | None = None,
    classifier_usage: ClassifierUsage | None = None,
) -> MagicMock:
    """A duck-typed Router double exposing the methods on_llm_end relies on. Both
    routing_info() and extract() are stubbed identically: on_llm_end picks whichever one
    applies (routing_info() when a decision was stashed, extract() otherwise — see
    TokensCalculationCallback.on_llm_end), and these tests don't care which, only what
    display_routing ends up being."""
    router = MagicMock()
    resolved = routing_info if routing_info is not None else RoutingInfo()
    router.extract.return_value = resolved
    router.routing_info.return_value = resolved
    router.extract_classifier_usage.return_value = classifier_usage
    return router


@patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
@patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost')
def test_on_llm_end_registers_router_classifier_usage_as_separate_run(
    mock_calculate_token_cost, mock_update_llm_run, mock_get_model_cost, callback
):
    """A router's classifier sub-call usage gets its own billable LLMRun.

    The (router, decision) pair travels through on_chat_model_start's config metadata (see
    core/router_chat_model.py::RouterChatModel._agenerate), not response_metadata —
    response_metadata for this call doesn't exist yet at the time on_chat_model_start/
    on_llm_end fire on this callback.
    """
    from codemie.core.router_chat_model import _ROUTING_CTX_KEY

    run_id = UUID('12345678-1234-5678-1234-567812345678')
    decision = _make_switchyard_decision(classifier=ClassifierCall())
    router = _make_router(
        classifier_usage=ClassifierUsage(
            provider="switchyard",
            input_tokens=200,
            output_tokens=15,
            cost_usd=0.0021,
            model="gpt-5.6-luna-2026-07-09",
        )
    )
    callback.on_chat_model_start({}, [[]], run_id=run_id, metadata={_ROUTING_CTX_KEY: (router, decision)})

    message = BaseMessage(
        type="",
        content="Test response",
        usage_metadata={"input_tokens": 10, "output_tokens": 20},
    )
    generation = ChatGeneration(text="Test response", message=message)
    result = LLMResult(generations=[[generation]])

    mock_get_model_cost.return_value = CostConfig(input=0.001, output=0.002)
    mock_calculate_token_cost.return_value = (0.05, 0.0, 0.0)

    asyncio.run(callback.on_llm_end(response=result, run_id=run_id))

    assert mock_update_llm_run.call_count == 2
    main_run = mock_update_llm_run.call_args_list[0][1]['llm_run']
    classifier_run = mock_update_llm_run.call_args_list[1][1]['llm_run']

    assert main_run.run_id == str(run_id)
    assert classifier_run.run_id == f"{run_id}-classifier"
    assert classifier_run.input_tokens == 200
    assert classifier_run.output_tokens == 15
    assert classifier_run.llm_model == "gpt-5.6-luna-2026-07-09"
    router.extract_classifier_usage.assert_called_once()
    # The pending (router, decision) pair must be consumed exactly once.
    assert run_id not in callback._pending


@patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
@patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost')
def test_on_llm_end_skips_classifier_run_when_router_reports_no_usage(
    mock_calculate_token_cost, mock_update_llm_run, mock_get_model_cost, callback
):
    """Signal-mode routing (no classifier call) must not fabricate a classifier LLMRun."""
    from codemie.core.router_chat_model import _ROUTING_CTX_KEY

    run_id = UUID('12345678-1234-5678-1234-567812345678')
    decision = _make_switchyard_decision()  # classifier defaults to None (no classifier sub-call)
    router = _make_router(classifier_usage=None)
    callback.on_chat_model_start({}, [[]], run_id=run_id, metadata={_ROUTING_CTX_KEY: (router, decision)})

    message = BaseMessage(
        type="",
        content="Test response",
        usage_metadata={"input_tokens": 10, "output_tokens": 20},
    )
    generation = ChatGeneration(text="Test response", message=message)
    result = LLMResult(generations=[[generation]])

    mock_get_model_cost.return_value = CostConfig(input=0.001, output=0.002)
    mock_calculate_token_cost.return_value = (0.05, 0.0, 0.0)

    asyncio.run(callback.on_llm_end(response=result, run_id=run_id))

    mock_update_llm_run.assert_called_once()


def test_on_llm_error_clears_pending_routing_ctx():
    """A pending (router, decision) pair must not leak in self._pending when the call errors."""
    from codemie.core.router_chat_model import _ROUTING_CTX_KEY

    callback = TokensCalculationCallback(request_id="test_request_id", llm_model="gpt-4.1-mini")
    run_id = UUID('12345678-1234-5678-1234-567812345678')
    decision = _make_switchyard_decision()
    router = _make_router()
    callback.on_chat_model_start({}, [[]], run_id=run_id, metadata={_ROUTING_CTX_KEY: (router, decision)})
    assert run_id in callback._pending

    callback.on_llm_error(RuntimeError("boom"), run_id=run_id)

    assert run_id not in callback._pending


@pytest.mark.asyncio
async def test_on_llm_end_uses_stashed_router_when_present():
    """When a decision was stashed pre-call, display_routing must come from
    router.routing_info(decision), NOT router.extract(response) — extract() reads a stamp
    that RouterChatModel._agenerate only applies *after* this callback's own on_llm_end fires
    (see Router.extract()'s docstring on core/router.py), so for a SwitchyardRouter-style
    decision-bearing call, extract() would see this unstamped _llm_result() and return empty.
    routing_info(decision) has no such ordering dependency — this is the regression test for
    that fix."""
    callback = TokensCalculationCallback(request_id="req-1", llm_model="claude-4-5-haiku")
    router = MagicMock()
    router.routing_info.return_value = RoutingInfo(routed_model="claude-4-5-haiku")
    router.extract_classifier_usage.return_value = None
    decision = RoutingDecision(
        model="claude-4-5-haiku",
        tier="efficient",
    )
    run_id = uuid4()
    callback.on_chat_model_start({}, [[]], run_id=run_id, metadata={"_routing_ctx": (router, decision)})

    with patch("codemie.service.request_summary_manager.request_summary_manager.update_llm_run") as mock_update:
        await callback.on_llm_end(_llm_result(), run_id=run_id)

    router.routing_info.assert_called_once_with(decision)
    router.extract.assert_not_called()
    router.extract_classifier_usage.assert_called_once()
    mock_update.assert_called_once()
    llm_run = mock_update.call_args.kwargs["llm_run"]
    assert llm_run.routing == RoutingInfo(routed_model="claude-4-5-haiku")


@pytest.mark.asyncio
async def test_on_llm_end_falls_back_to_create_router_when_nothing_stashed():
    callback = TokensCalculationCallback(request_id="req-1", llm_model="gpt-4.1")
    fallback_router = MagicMock()
    fallback_router.extract.return_value = RoutingInfo()
    fallback_router.extract_classifier_usage.return_value = None
    run_id = uuid4()
    # No on_chat_model_start call at all — nothing stashed for this run_id.

    with (
        patch("codemie.service.llm_service.router_factory.create_router", return_value=fallback_router) as mock_create,
        patch("codemie.service.request_summary_manager.request_summary_manager.update_llm_run"),
    ):
        await callback.on_llm_end(_llm_result(), run_id=run_id)

    mock_create.assert_called_once_with("gpt-4.1")
    fallback_router.extract.assert_called_once()
