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
from uuid import UUID
from unittest.mock import patch
import asyncio
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult, Generation, ChatGeneration

from codemie.agents.callbacks.tokens_callback import TokensCalculationCallback
from codemie.service.request_summary_manager import LLMRun
from codemie.service.llm_service.llm_service import LLMService
from codemie.configs.llm_config import CostConfig


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


class TestExtractRoutedModelFromResponseMetadata:
    """Unit tests for _extract_routed_model_from_response_metadata."""

    @staticmethod
    def _call(metadata: dict) -> str | None:
        from codemie.agents.callbacks.tokens_callback import TokensCalculationCallback

        return TokensCalculationCallback._extract_routed_model_from_response_metadata(metadata)

    def test_proxy_model_fallback(self):
        result = self._call({"model": "gpt-4o"})
        assert result == "gpt-4o"

    def test_returns_none_when_empty(self):
        assert self._call({}) is None


def _make_switchyard_decision(**overrides: object):
    """Build a minimal RoutingDecision for tests without depending on pick_model()'s internals."""
    import dataclasses

    from codemie.enterprise.switchyard.decision import RoutingDecision

    base = RoutingDecision(
        model="claude-haiku-4-5-20251001",
        tier="efficient",
        capable_model="claude-sonnet-5",
        confidence=0.3,
    )
    return dataclasses.replace(base, **overrides)


@patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
@patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost')
def test_on_llm_end_registers_switchyard_classifier_as_separate_run(
    mock_calculate_token_cost, mock_update_llm_run, mock_get_model_cost, callback
):
    """Switchyard's classifier sub-call gets its own billable LLMRun, mirroring the LiteLLM-router path.

    The decision travels through on_chat_model_start's config metadata (see
    switchyard/agent.py::_agenerate), not response_metadata — response_metadata for this
    call doesn't exist yet at the time on_chat_model_start/on_llm_end fire on this callback.
    """
    from codemie.enterprise.switchyard.routing_meta import _SWITCHYARD_DECISION_METADATA_KEY

    run_id = UUID('12345678-1234-5678-1234-567812345678')
    decision = _make_switchyard_decision(
        classifier_used=True,
        classifier_model="gpt-5.6-luna-2026-07-09",
        classifier_input_tokens=200,
        classifier_output_tokens=15,
        classifier_cost_usd=0.0021,
    )
    callback.on_chat_model_start({}, [[]], run_id=run_id, metadata={_SWITCHYARD_DECISION_METADATA_KEY: decision})

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
    # The pending decision must be consumed exactly once.
    assert run_id not in callback._pending_decisions


@patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
@patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost')
def test_on_llm_end_skips_switchyard_classifier_run_when_classifier_not_used(
    mock_calculate_token_cost, mock_update_llm_run, mock_get_model_cost, callback
):
    """Signal-mode routing (no classifier call) must not fabricate a classifier LLMRun."""
    from codemie.enterprise.switchyard.routing_meta import _SWITCHYARD_DECISION_METADATA_KEY

    run_id = UUID('12345678-1234-5678-1234-567812345678')
    decision = _make_switchyard_decision()  # classifier_used defaults to False
    callback.on_chat_model_start({}, [[]], run_id=run_id, metadata={_SWITCHYARD_DECISION_METADATA_KEY: decision})

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


def test_on_llm_error_clears_pending_decision():
    """A pending decision must not leak in self._pending_decisions when the call errors."""
    from codemie.agents.callbacks.tokens_callback import TokensCalculationCallback
    from codemie.enterprise.switchyard.routing_meta import _SWITCHYARD_DECISION_METADATA_KEY

    callback = TokensCalculationCallback(request_id="test_request_id", llm_model="gpt-4.1-mini")
    run_id = UUID('12345678-1234-5678-1234-567812345678')
    decision = _make_switchyard_decision()
    callback.on_chat_model_start({}, [[]], run_id=run_id, metadata={_SWITCHYARD_DECISION_METADATA_KEY: decision})
    assert run_id in callback._pending_decisions

    callback.on_llm_error(RuntimeError("boom"), run_id=run_id)

    assert run_id not in callback._pending_decisions
