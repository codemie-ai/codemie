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


def _partial_error_response() -> LLMResult:
    """A response carrying tokens from generations that completed before the call failed."""
    message = BaseMessage(type="", content="", usage_metadata={"input_tokens": 100, "output_tokens": 5})
    return LLMResult(generations=[[ChatGeneration(text="", message=message)]])


@patch('codemie.agents.callbacks.tokens_callback.uses_litellm', return_value=True)
@patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
@patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost')
def test_on_llm_error_on_the_litellm_path_records_a_measured_zero(
    mock_calculate_token_cost, mock_update_llm_run, mock_get_model_cost, _mock_uses_litellm, callback, mock_model_costs
):
    """A failed LiteLLM call carries a real, measured zero - AD-20 - and nothing re-prices it.

    The token counts survive because they were consumed; only the invented charge goes. This was
    AD-4's one live violation: the branch called ``calculate_token_cost`` unconditionally, with no
    proxy-cost check even possible on an error path, so a failure was billed an estimate no ledger
    would ever agree with. The zero here is a **measured** one and must not be confused with
    ``_calculate_cost``'s unsettled ``None``.
    """
    mock_get_model_cost.return_value = mock_model_costs
    mock_calculate_token_cost.return_value = (0.0001, 0.0, 0.0)

    callback.on_llm_error(
        error=RuntimeError("stream interrupted"),
        run_id=UUID('12345678-1234-5678-1234-567812345678'),
        response=_partial_error_response(),
    )

    mock_calculate_token_cost.assert_not_called()
    mock_get_model_cost.assert_not_called()
    mock_update_llm_run.assert_called_once()
    call_args = mock_update_llm_run.call_args[1]
    assert call_args['llm_run'].input_tokens == 100
    assert call_args['llm_run'].output_tokens == 5
    assert call_args['llm_run'].money_spent == 0.0


@patch('codemie.agents.callbacks.tokens_callback.uses_litellm', return_value=False)
@patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
@patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost')
def test_on_llm_error_off_the_litellm_path_keeps_the_local_estimate(
    mock_calculate_token_cost, mock_update_llm_run, mock_get_model_cost, _mock_uses_litellm, callback, mock_model_costs
):
    """The mirror case, and it is why the zero is gated rather than unconditional.

    AD-20 scopes itself to the **ledger** - ``status = 'failure'`` rows, *"counts come from the
    ledger, never from the callback"* - and where no ledger serves this call class the local price
    table **is** the authority. These tokens were processed before the call failed and the provider
    may well have billed them, so an unconditional zero would silently drop real cost from every
    non-LiteLLM deployment. Driven from ``uses_litellm`` rather than a config flag so the assertion
    pins AD-2's single routing seam.
    """
    mock_get_model_cost.return_value = mock_model_costs
    mock_calculate_token_cost.return_value = (0.0001, 0.0, 0.0)

    callback.on_llm_error(
        error=RuntimeError("stream interrupted"),
        run_id=UUID('12345678-1234-5678-1234-567812345678'),
        response=_partial_error_response(),
    )

    mock_calculate_token_cost.assert_called_once()
    assert mock_update_llm_run.call_args[1]['llm_run'].money_spent == 0.0001


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


def _chat_result_without_proxy_cost():
    """A completed chat generation carrying token counts and no cost of any kind."""
    message = BaseMessage(
        type="",
        content="Test response",
        usage_metadata={"input_tokens": 10, "output_tokens": 20},
    )
    return LLMResult(generations=[[ChatGeneration(text="Test response", message=message)]])


@patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
@patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost')
@patch('codemie.agents.callbacks.tokens_callback.uses_litellm', return_value=True)
def test_on_llm_end_on_litellm_path_without_a_cost_is_unsettled_not_zero(
    mock_uses_litellm, mock_calculate_token_cost, mock_update_llm_run, mock_get_model_cost, callback
):
    """AD-28: a LiteLLM call whose cost has not arrived carries an absence, never a zero.

    This is the normal state for streamed Claude chat, where neither immediate-cost source
    fires (AD-22). The ledger settles it later; a zero would freeze "this call was free" into
    the record, and a local estimate would invent a figure LiteLLM already computed (AD-4).
    """
    asyncio.run(
        callback.on_llm_end(
            response=_chat_result_without_proxy_cost(), run_id=UUID('12345678-1234-5678-1234-567812345678')
        )
    )

    mock_calculate_token_cost.assert_not_called()
    mock_get_model_cost.assert_not_called()
    llm_run = mock_update_llm_run.call_args[1]['llm_run']
    assert llm_run.money_spent is None
    # No float at all was emitted for the money, so in particular no 0.0 was: `None == 0.0` is
    # False, but both are falsy, and every way the two get collapsed goes through a truthiness
    # test on this field.
    assert not isinstance(llm_run.money_spent, float)
    # The tokens are real and stay.
    assert llm_run.input_tokens == 10
    assert llm_run.output_tokens == 20


@patch('codemie.agents.callbacks.tokens_callback.config')
@patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
@patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost')
@patch('codemie.agents.callbacks.tokens_callback.uses_litellm', return_value=True)
def test_on_llm_end_on_litellm_path_is_unsettled_even_with_usage_tracking_off(
    mock_uses_litellm, mock_calculate_token_cost, mock_update_llm_run, mock_get_model_cost, mock_config, callback
):
    """The routing gate is strictly wider than the proxy-cost extraction gate.

    With ``LLM_PROXY_TRACK_USAGE`` off the caller never even looks for the authoritative
    number, but LiteLLM still billed the call, so re-pricing it locally is still an invention.
    """
    mock_config.LLM_PROXY_ENABLED = True
    mock_config.LLM_PROXY_TRACK_USAGE = False
    message = BaseMessage(type="", content="x", usage_metadata={"input_tokens": 5, "output_tokens": 10})
    generation = ChatGeneration(text="x", message=message, generation_info={"litellm_cost": 0.9999})
    result = LLMResult(generations=[[generation]])

    asyncio.run(callback.on_llm_end(response=result, run_id=UUID('12345678-1234-5678-1234-567812345678')))

    mock_calculate_token_cost.assert_not_called()
    assert mock_update_llm_run.call_args[1]['llm_run'].money_spent is None


@patch('codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
@patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost')
@patch('codemie.agents.callbacks.tokens_callback.uses_litellm', return_value=False)
def test_on_llm_end_off_the_litellm_path_still_gets_the_local_estimate(
    mock_uses_litellm, mock_calculate_token_cost, mock_update_llm_run, mock_get_model_cost, callback
):
    """The mirror case: in a non-LiteLLM deployment the local price table *is* the ledger.

    Driven from ``uses_litellm`` rather than from a config flag, so the test pins AD-2's single
    routing seam and not one of the several narrower predicates this repo has had.
    """
    mock_get_model_cost.return_value = CostConfig(input=0.001, output=0.002)
    mock_calculate_token_cost.return_value = (0.05, 0.0, 0.0)

    asyncio.run(
        callback.on_llm_end(
            response=_chat_result_without_proxy_cost(), run_id=UUID('12345678-1234-5678-1234-567812345678')
        )
    )

    mock_calculate_token_cost.assert_called_once()
    assert mock_update_llm_run.call_args[1]['llm_run'].money_spent == 0.05


@patch('codemie.agents.callbacks.tokens_callback.config')
@patch('codemie.agents.callbacks.tokens_callback.request_summary_manager.update_llm_run')
@patch('codemie.agents.callbacks.tokens_callback.calculate_token_cost')
@patch('codemie.agents.callbacks.tokens_callback.uses_litellm', return_value=True)
def test_on_llm_end_settled_proxy_cost_survives_the_routing_gate(
    mock_uses_litellm, mock_calculate_token_cost, mock_update_llm_run, mock_config, callback
):
    """A settled cost is used exactly as recorded - the gate must not shadow it into `None`."""
    mock_config.LLM_PROXY_ENABLED = True
    mock_config.LLM_PROXY_TRACK_USAGE = True
    message = BaseMessage(type="", content="x", usage_metadata={"input_tokens": 10, "output_tokens": 20})
    generation = ChatGeneration(
        text="x", message=message, generation_info={"headers": {"x-litellm-response-cost": "0.0042"}}
    )
    result = LLMResult(generations=[[generation]])

    asyncio.run(callback.on_llm_end(response=result, run_id=UUID('12345678-1234-5678-1234-567812345678')))

    mock_calculate_token_cost.assert_not_called()
    assert mock_update_llm_run.call_args[1]['llm_run'].money_spent == 0.0042


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
        decision_source="llm_classifier",
        routing_family="switchyard",
    )
    return dataclasses.replace(base, **overrides)


def _make_router(
    *,
    routing_info: RoutingInfo | None = None,
    classifier_usage: ClassifierUsage | None = None,
) -> MagicMock:
    """A duck-typed Router double exposing the methods on_llm_end relies on.
    router.routing_info(decision, header_maps) is stubbed to return the same value regardless
    of its two arguments — these tests don't care which of decision/header_maps a real router
    would read, only what display_routing ends up being."""
    router = MagicMock()
    router.routing_info.return_value = routing_info if routing_info is not None else RoutingInfo()
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
async def test_on_llm_end_uses_stashed_router_and_decision():
    """When a decision was stashed pre-call, display_routing must come from
    router.routing_info(decision, header_maps) with the stashed decision, not from anything
    stamped on the response — this callback fires strictly before RouterChatModel._agenerate's
    own post-call stamp (it's attached directly to the candidate model, running inside
    selected.ainvoke()), so for a SwitchyardRouter-style decision-bearing call, a header/stamp
    based read would see this unstamped _llm_result() and find nothing. Passing the decision
    straight through has no such ordering dependency — this is the regression test for that."""
    callback = TokensCalculationCallback(request_id="req-1", llm_model="claude-4-5-haiku")
    router = MagicMock()
    router.routing_info.return_value = RoutingInfo(routed_model="claude-4-5-haiku")
    router.extract_classifier_usage.return_value = None
    decision = RoutingDecision(
        model="claude-4-5-haiku",
        tier="efficient",
        decision_source="llm_classifier",
        routing_family="switchyard",
    )
    run_id = uuid4()
    callback.on_chat_model_start({}, [[]], run_id=run_id, metadata={"_routing_ctx": (router, decision)})

    with patch("codemie.service.request_summary_manager.request_summary_manager.update_llm_run") as mock_update:
        await callback.on_llm_end(_llm_result(), run_id=run_id)

    router.routing_info.assert_called_once()
    assert len(router.routing_info.call_args.args) == 2
    assert router.routing_info.call_args.args[0] is decision
    router.extract_classifier_usage.assert_called_once()
    mock_update.assert_called_once()
    llm_run = mock_update.call_args.kwargs["llm_run"]
    assert llm_run.routing == RoutingInfo(
        routed_model="claude-4-5-haiku",
        routed_input_tokens=10,
        routed_output_tokens=5,
        routed_cached_tokens=0,
        routed_cache_creation_tokens=0,
        routed_total_tokens=15,
        routed_cache_hit=False,
    )


@pytest.mark.asyncio
async def test_on_llm_end_falls_back_to_create_router_when_nothing_stashed():
    callback = TokensCalculationCallback(request_id="req-1", llm_model="gpt-4.1")
    fallback_router = MagicMock()
    fallback_router.routing_info.return_value = RoutingInfo()
    fallback_router.extract_classifier_usage.return_value = None
    run_id = uuid4()
    # No on_chat_model_start call at all — nothing stashed for this run_id.

    with (
        patch("codemie.service.llm_service.router_factory.create_router", return_value=fallback_router) as mock_create,
        patch("codemie.service.request_summary_manager.request_summary_manager.update_llm_run"),
    ):
        await callback.on_llm_end(_llm_result(), run_id=run_id)

    mock_create.assert_called_once_with("gpt-4.1")
    fallback_router.routing_info.assert_called_once()
    assert fallback_router.routing_info.call_args.args[0] is None


@pytest.mark.asyncio
async def test_on_llm_end_applies_counterfactual_costs_from_router_reported_baseline():
    """Agent usage tracking re-prices against whatever counterfactual_model the router's own
    routing_info() already reported — router-owned state (set at construction, see
    core/router.py), not a separately resolved value."""
    callback = TokensCalculationCallback(request_id="req-1", llm_model="gpt-smart-router")
    router = MagicMock()
    router.routing_info.return_value = RoutingInfo(
        routed_model="gpt-5.6-luna-2026-07-09",
        requested_model="gpt-smart-router",
        counterfactual_model="gpt-5.6-terra-2026-07-09",
        tier="SIMPLE",
        decision_source="llm_classifier",
        classifier_cost_usd=0.0005,
    )
    router.extract_classifier_usage.return_value = None
    run_id = uuid4()
    callback.on_chat_model_start({}, [[]], run_id=run_id, metadata={"_routing_ctx": (router, None)})

    with (
        patch("codemie.agents.callbacks.tokens_callback.calculate_token_cost", return_value=(0.002, 0.0, 0.0)),
        patch("codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost"),
        patch("codemie.service.request_summary_manager.request_summary_manager.update_llm_run") as mock_update,
    ):
        await callback.on_llm_end(_llm_result(), run_id=run_id)

    routing = mock_update.call_args.kwargs["llm_run"].routing
    assert routing.requested_model == "gpt-smart-router"
    assert routing.counterfactual_model == "gpt-5.6-terra-2026-07-09"
    assert routing.routed_input_tokens == 10
    assert routing.routed_output_tokens == 5
    assert routing.routed_cached_tokens == 0
    assert routing.routed_cache_creation_tokens == 0
    assert routing.routed_total_tokens == 15
    assert routing.original_cost_usd == 0.0025
    assert routing.estimated_max_cost_usd == 0.002
    assert routing.potential_savings_usd == -0.0005


@pytest.mark.asyncio
async def test_on_llm_end_prices_the_counterfactual_on_the_litellm_path():
    """The counterfactual is a "what it would have cost" analytics figure, not spend, so the
    LiteLLM gate that withholds a local price from the call's own money must not withhold it
    here - otherwise no routed agent call on a LiteLLM deployment ever reports savings."""
    callback = TokensCalculationCallback(request_id="req-1", llm_model="gpt-smart-router")
    router = _make_router(
        routing_info=RoutingInfo(
            routed_model="gpt-5.6-luna-2026-07-09",
            requested_model="gpt-smart-router",
            counterfactual_model="gpt-5.6-terra-2026-07-09",
            tier="SIMPLE",
            classifier_cost_usd=0.0005,
        )
    )
    run_id = uuid4()
    callback.on_chat_model_start({}, [[]], run_id=run_id, metadata={"_routing_ctx": (router, None)})
    message = AIMessage(content="hi", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
    settled = ChatGeneration(message=message, generation_info={"litellm_cost": 0.001})

    with (
        patch("codemie.agents.callbacks.tokens_callback.uses_litellm", return_value=True),
        patch("codemie.agents.callbacks.tokens_callback.config") as mock_config,
        patch(
            "codemie.agents.callbacks.tokens_callback.calculate_token_cost", return_value=(0.004, 0.0, 0.0)
        ) as mock_calculate_token_cost,
        patch("codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost"),
        patch("codemie.service.request_summary_manager.request_summary_manager.update_llm_run") as mock_update,
    ):
        mock_config.LLM_PROXY_ENABLED = True
        mock_config.LLM_PROXY_TRACK_USAGE = True
        await callback.on_llm_end(LLMResult(generations=[[settled]]), run_id=run_id)

    llm_run = mock_update.call_args.kwargs["llm_run"]
    assert llm_run.money_spent == 0.001
    assert mock_calculate_token_cost.call_args.kwargs["llm_model"] == "gpt-5.6-terra-2026-07-09"
    assert llm_run.routing.original_cost_usd == pytest.approx(0.0015)
    assert llm_run.routing.estimated_max_cost_usd == 0.004
    assert llm_run.routing.potential_savings_usd == pytest.approx(0.0025)


_LUNA = "gpt-5.6-luna-2026-07-09"
_TERRA = "gpt-5.6-terra-2026-07-09"


def _price_by_model(prices: dict[str, float]):
    """A `calculate_token_cost` stand-in answering per model, so each side of the savings is told apart."""

    def _price(*, llm_model, **_):
        return prices[llm_model], 0.0, 0.0

    return _price


def test_counterfactual_prices_the_actual_locally_on_an_unsettled_cost(callback):
    """The call's own money is still pending for the ledger, but the routing event is emitted now and
    Elasticsearch has no later pass - so the routing figures price the actual from the same table
    as the counterfactual, and the savings compare like with like."""
    routing = RoutingInfo(routed_model=_LUNA, counterfactual_model=_TERRA, tier="SIMPLE", classifier_cost_usd=0.0005)

    with (
        patch(
            "codemie.agents.callbacks.tokens_callback.calculate_token_cost",
            side_effect=_price_by_model({_LUNA: 0.001, _TERRA: 0.004}),
        ),
        patch("codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost"),
    ):
        result = callback._apply_counterfactual_costs(routing, None, 10, 5, 0, 0)

    assert result.original_cost_usd == pytest.approx(0.0015)
    assert result.estimated_max_cost_usd == 0.004
    assert result.potential_savings_usd == pytest.approx(0.0025)


def test_counterfactual_on_the_top_tier_uses_the_locally_priced_actual(callback):
    """Already at the top tier there is nothing to save, so the estimate is the actual itself."""
    routing = RoutingInfo(routed_model=_TERRA, counterfactual_model=_TERRA, tier="complex", classifier_cost_usd=0.0005)

    with (
        patch(
            "codemie.agents.callbacks.tokens_callback.calculate_token_cost",
            side_effect=_price_by_model({_TERRA: 0.004}),
        ),
        patch("codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost"),
    ):
        result = callback._apply_counterfactual_costs(routing, None, 10, 5, 0, 0)

    assert result.estimated_max_cost_usd == 0.004
    assert result.original_cost_usd == pytest.approx(0.0045)
    assert result.potential_savings_usd == pytest.approx(-0.0005)


def test_counterfactual_uses_the_settled_cost_without_pricing_the_actual(callback):
    routing = RoutingInfo(routed_model=_LUNA, counterfactual_model=_TERRA, tier="SIMPLE", classifier_cost_usd=0.0005)

    with (
        patch(
            "codemie.agents.callbacks.tokens_callback.calculate_token_cost",
            side_effect=_price_by_model({_LUNA: 99.0, _TERRA: 0.004}),
        ) as mock_price,
        patch("codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost"),
    ):
        result = callback._apply_counterfactual_costs(routing, 0.001, 10, 5, 0, 0)

    assert [call.kwargs["llm_model"] for call in mock_price.call_args_list] == [_TERRA]
    assert result.original_cost_usd == pytest.approx(0.0015)


def test_counterfactual_is_left_off_when_the_actual_cannot_be_priced(callback):
    """A model missing from the price table must not fail the call or invent a figure."""
    routing = RoutingInfo(routed_model=_LUNA, counterfactual_model=_TERRA, tier="SIMPLE")

    with (
        patch("codemie.agents.callbacks.tokens_callback.calculate_token_cost", side_effect=KeyError(_LUNA)),
        patch("codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost"),
    ):
        result = callback._apply_counterfactual_costs(routing, None, 10, 5, 0, 0)

    assert result is routing


@pytest.mark.asyncio
async def test_on_llm_end_stamps_the_routing_costs_while_the_calls_own_money_stays_unsettled():
    """On a LiteLLM deployment with no cost in hand: the ledger still owns `money_spent`, and the routing
    event still carries the original cost, the estimated max and the savings."""
    callback = TokensCalculationCallback(request_id="req-1", llm_model="gpt-smart-router")
    router = _make_router(
        routing_info=RoutingInfo(
            routed_model=_LUNA,
            requested_model="gpt-smart-router",
            counterfactual_model=_TERRA,
            tier="SIMPLE",
            classifier_cost_usd=0.0005,
        )
    )
    run_id = uuid4()
    callback.on_chat_model_start({}, [[]], run_id=run_id, metadata={"_routing_ctx": (router, None)})
    message = AIMessage(content="hi", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
    unsettled = ChatGeneration(message=message)

    with (
        patch("codemie.agents.callbacks.tokens_callback.uses_litellm", return_value=True),
        patch("codemie.agents.callbacks.tokens_callback.config") as mock_config,
        patch(
            "codemie.agents.callbacks.tokens_callback.calculate_token_cost",
            side_effect=_price_by_model({_LUNA: 0.001, _TERRA: 0.004}),
        ),
        patch("codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost"),
        patch("codemie.service.request_summary_manager.request_summary_manager.update_llm_run") as mock_update,
    ):
        mock_config.LLM_PROXY_ENABLED = True
        mock_config.LLM_PROXY_TRACK_USAGE = True
        await callback.on_llm_end(LLMResult(generations=[[unsettled]]), run_id=run_id)

    llm_run = mock_update.call_args.kwargs["llm_run"]
    assert llm_run.money_spent is None
    assert llm_run.routing.original_cost_usd == pytest.approx(0.0015)
    assert llm_run.routing.estimated_max_cost_usd == 0.004
    assert llm_run.routing.potential_savings_usd == pytest.approx(0.0025)


@pytest.mark.asyncio
async def test_on_llm_end_records_routed_cache_hit_without_model_cost():
    """A routed whole-response cache hit remains observable but costs no model spend."""
    callback = TokensCalculationCallback(request_id="req-1", llm_model="gpt-smart-router")
    router = MagicMock()
    router.routing_info.return_value = RoutingInfo(
        routed_model="gpt-5.6-luna-2026-07-09",
        requested_model="gpt-smart-router",
        tier="SIMPLE",
    )
    router.extract_classifier_usage.return_value = None
    run_id = uuid4()
    callback.on_chat_model_start({}, [[]], run_id=run_id, metadata={"_routing_ctx": (router, None)})
    message = AIMessage(
        content="Cached response",
        usage_metadata={"input_tokens": 11, "output_tokens": 5, "total_tokens": 16},
        response_metadata={"cache_hit": True},
    )

    with (
        patch("codemie.agents.callbacks.tokens_callback.calculate_token_cost", return_value=(0.00902, 0.0, 0.0)),
        patch("codemie.agents.callbacks.tokens_callback.llm_service.get_model_cost"),
        patch("codemie.service.request_summary_manager.request_summary_manager.update_llm_run") as mock_update,
    ):
        await callback.on_llm_end(
            response=LLMResult(generations=[[ChatGeneration(message=message)]]),
            run_id=run_id,
        )

    llm_run = mock_update.call_args.kwargs["llm_run"]
    assert llm_run.money_spent == 0.0
    assert llm_run.routing.routed_cache_hit is True
    assert llm_run.routing.routed_input_tokens == 11
    assert llm_run.routing.routed_output_tokens == 5


class TestOnChatModelStartPublishesTheRunInFlight:
    """The pre-flight callback is what lets an outbound header name the individual call.

    The run id cannot instead ride in the model's fixed headers: one model object serves every
    iteration of an agent loop, so a header set at construction would send one id for many calls and
    a per-call cost lookup would sum the wrong rows.
    """

    def test_the_run_id_is_readable_by_the_request_that_owns_it(self):
        from uuid import uuid4

        from codemie.agents.callbacks.tokens_callback import TokensCalculationCallback
        from codemie.service.spend.run_context import current_run_id

        run_id = uuid4()
        callback = TokensCalculationCallback(request_id="req-77", llm_model="gpt-4.1")
        callback.on_chat_model_start({}, [[]], run_id=run_id)

        assert current_run_id("req-77") == str(run_id)

    @pytest.mark.asyncio
    async def test_an_async_dispatch_publishes_into_the_awaiting_task(self):
        """``ainvoke`` goes through the async manager, which runs a non-inline sync handler in an
        executor on a copied context; the publish would then never reach the request that follows."""
        from langchain_core.callbacks import AsyncCallbackManager

        from codemie.service.spend.run_context import current_run_id

        run_id = uuid4()
        callback = TokensCalculationCallback(request_id="req-async", llm_model="gpt-4.1")
        await AsyncCallbackManager(handlers=[callback]).on_chat_model_start({}, [[]], run_id=run_id)

        assert current_run_id("req-async") == str(run_id)

    def test_another_requests_call_does_not_read_it(self):
        from uuid import uuid4

        from codemie.agents.callbacks.tokens_callback import TokensCalculationCallback
        from codemie.service.spend.run_context import current_run_id

        TokensCalculationCallback(request_id="req-77", llm_model="gpt-4.1").on_chat_model_start(
            {}, [[]], run_id=uuid4()
        )

        assert current_run_id("req-88") is None
