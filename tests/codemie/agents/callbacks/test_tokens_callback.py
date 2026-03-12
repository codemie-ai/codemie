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
    callback.on_llm_end(response=sample_llm_result, run_id=UUID('12345678-1234-5678-1234-567812345678'))

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
    assert call_args['llm_run'].llm_model == LLMService.BASE_NAME_GPT_41_MINI


@patch('codemie.agents.callbacks.tokens_callback.logger.error')
def test_on_llm_end_error_handling(mock_logger_error, callback):
    """Test error handling in on_llm_end"""
    # Create a malformed LLMResult that will cause an exception
    broken_result = LLMResult(generations=[[Generation(text="test")]])  # Missing usage_metadata

    callback.on_llm_end(response=broken_result, run_id=UUID('12345678-1234-5678-1234-567812345678'))

    # Verify that the error was logged
    mock_logger_error.assert_called_once()
    assert "Error while calculating tokens" in mock_logger_error.call_args[0][0]


def test_on_llm_end_with_empty_generations(callback):
    """Test on_llm_end with empty generations"""
    empty_result = LLMResult(generations=[])

    with (
        patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost') as mock_get_model_cost,
        patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run') as mock_update_llm_run,
        patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost') as mock_calculate_token_cost,
    ):
        mock_get_model_cost.return_value = CostConfig(input=0.001, output=0.002)
        mock_calculate_token_cost.return_value = (0.0, 0.0, 0.0)

        callback.on_llm_end(response=empty_result, run_id=UUID('12345678-1234-5678-1234-567812345678'))

        # Verify that update_llm_run was called
        mock_update_llm_run.assert_called_once()
        call_args = mock_update_llm_run.call_args[1]
        assert call_args['llm_run'].input_tokens == 0
        assert call_args['llm_run'].output_tokens == 0
        assert call_args['llm_run'].money_spent == 0.0
        assert call_args['llm_run'].cached_tokens_money_spent == 0.0


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

    callback.on_llm_end(response=result_with_cache, run_id=UUID('12345678-1234-5678-1234-567812345678'))

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

    callback.on_llm_end(response=result, run_id=UUID('12345678-1234-5678-1234-567812345678'))

    # Verify cached tokens and cost are 0
    call_args = mock_update_llm_run.call_args[1]
    assert call_args['llm_run'].cached_tokens == 0
    assert call_args['llm_run'].cached_tokens_money_spent == 0.0
