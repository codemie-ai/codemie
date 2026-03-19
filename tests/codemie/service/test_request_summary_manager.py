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

"""
Tests for RequestSummaryManager to ensure cached_tokens_money_spent is properly aggregated.
"""

import pytest

from codemie.service.request_summary_manager import (
    LLMRun,
    RequestSummary,
    request_summary_manager,
)


@pytest.fixture
def sample_request_id():
    """Create a sample request ID."""
    return "test-request-123"


@pytest.fixture
def sample_llm_runs():
    """Create sample LLM runs with cached tokens."""
    return [
        LLMRun(
            run_id="run-1",
            input_tokens=1000,
            output_tokens=500,
            cached_tokens=800,
            money_spent=0.012,
            cached_tokens_money_spent=0.00024,
            cached_tokens_creation_cost=0.0001,
            llm_model="claude-3-7",
        ),
        LLMRun(
            run_id="run-2",
            input_tokens=2000,
            output_tokens=1000,
            cached_tokens=1500,
            money_spent=0.025,
            cached_tokens_money_spent=0.00045,
            cached_tokens_creation_cost=0.00015,
            llm_model="claude-3-7",
        ),
        LLMRun(
            run_id="run-3",
            input_tokens=500,
            output_tokens=300,
            cached_tokens=0,
            money_spent=0.006,
            cached_tokens_money_spent=0.0,
            cached_tokens_creation_cost=0.0,
            llm_model="gpt-4.1",
        ),
    ]


def test_llm_run_includes_cached_tokens_money_spent():
    """Test that LLMRun model includes cached_tokens_money_spent and cached_tokens_creation_cost fields."""
    llm_run = LLMRun(
        run_id="test-run",
        input_tokens=1000,
        output_tokens=500,
        cached_tokens=800,
        money_spent=0.012,
        cached_tokens_money_spent=0.00024,
        cached_tokens_creation_cost=0.0001,
        llm_model="claude-3-7",
    )

    assert llm_run.cached_tokens_money_spent == 0.00024
    assert isinstance(llm_run.cached_tokens_money_spent, float)
    assert llm_run.cached_tokens_creation_cost == 0.0001
    assert isinstance(llm_run.cached_tokens_creation_cost, float)


def test_llm_run_default_cached_tokens_money_spent():
    """Test that LLMRun has default values for cached_tokens_money_spent and cached_tokens_creation_cost."""
    llm_run = LLMRun(
        run_id="test-run",
        input_tokens=1000,
        output_tokens=500,
        cached_tokens=0,
        money_spent=0.012,
        llm_model="gpt-4o",
        # cached_tokens_money_spent and cached_tokens_creation_cost not provided
    )

    assert llm_run.cached_tokens_money_spent == 0.0
    assert llm_run.cached_tokens_creation_cost == 0.0


def test_request_summary_calculate_aggregates_cached_tokens_money_spent(sample_request_id, sample_llm_runs):
    """Test that RequestSummary.calculate() aggregates cached_tokens_money_spent and cache creation costs from all runs."""
    request_summary = RequestSummary(request_id=sample_request_id, llm_runs=sample_llm_runs)

    request_summary.calculate()

    assert request_summary.tokens_usage is not None
    assert request_summary.tokens_usage.input_tokens == 3500  # 1000 + 2000 + 500
    assert request_summary.tokens_usage.output_tokens == 1800  # 500 + 1000 + 300
    assert request_summary.tokens_usage.cached_tokens == 2300  # 800 + 1500 + 0
    assert abs(request_summary.tokens_usage.money_spent - 0.043) < 0.001  # 0.012 + 0.025 + 0.006
    assert abs(request_summary.tokens_usage.cached_tokens_money_spent - 0.00069) < 0.00001  # 0.00024 + 0.00045 + 0.0
    assert (
        abs(request_summary.tokens_usage.cached_tokens_creation_money_spent - 0.00025) < 0.00001
    )  # 0.0001 + 0.00015 + 0.0


def test_request_summary_calculate_with_no_cached_tokens(sample_request_id):
    """Test RequestSummary.calculate() when no LLM runs have cached tokens."""
    llm_runs = [
        LLMRun(
            run_id="run-1",
            input_tokens=1000,
            output_tokens=500,
            cached_tokens=0,
            money_spent=0.015,
            cached_tokens_money_spent=0.0,
            cached_tokens_creation_cost=0.0,
            llm_model="gpt-4o",
        ),
    ]
    request_summary = RequestSummary(request_id=sample_request_id, llm_runs=llm_runs)

    request_summary.calculate()

    assert request_summary.tokens_usage.cached_tokens == 0
    assert request_summary.tokens_usage.cached_tokens_money_spent == 0.0
    assert request_summary.tokens_usage.cached_tokens_creation_money_spent == 0.0


def test_request_summary_calculate_with_empty_runs(sample_request_id):
    """Test RequestSummary.calculate() with no LLM runs."""
    request_summary = RequestSummary(request_id=sample_request_id, llm_runs=[])

    request_summary.calculate()

    assert request_summary.tokens_usage is not None
    assert request_summary.tokens_usage.input_tokens == 0
    assert request_summary.tokens_usage.output_tokens == 0
    assert request_summary.tokens_usage.cached_tokens == 0
    assert request_summary.tokens_usage.money_spent == 0.0
    assert request_summary.tokens_usage.cached_tokens_money_spent == 0.0
    assert request_summary.tokens_usage.cached_tokens_creation_money_spent == 0.0


def test_request_summary_manager_update_llm_run():
    """Test RequestSummaryManager.update_llm_run() with cached_tokens_money_spent and cache creation cost."""
    request_id = "test-request-456"
    llm_run = LLMRun(
        run_id="run-1",
        input_tokens=5000,
        output_tokens=200,
        cached_tokens=4800,
        money_spent=0.00504,
        cached_tokens_money_spent=0.00144,
        cached_tokens_creation_cost=0.0002,
        llm_model="claude-3-7",
    )

    request_summary_manager.update_llm_run(request_id=request_id, llm_run=llm_run)

    summary = request_summary_manager.get_summary(request_id=request_id)
    assert summary is not None
    assert len(summary.llm_runs) == 1
    assert summary.llm_runs[0].cached_tokens_money_spent == 0.00144
    assert summary.llm_runs[0].cached_tokens_creation_cost == 0.0002

    request_summary_manager.clear_summary(request_id=request_id)


def test_request_summary_manager_multiple_runs_aggregation():
    """Test RequestSummaryManager aggregates cached_tokens_money_spent and cache creation costs across multiple runs."""
    request_id = "test-request-789"

    run1 = LLMRun(
        run_id="run-1",
        input_tokens=1000,
        output_tokens=500,
        cached_tokens=800,
        money_spent=0.012,
        cached_tokens_money_spent=0.00024,
        cached_tokens_creation_cost=0.0001,
        llm_model="claude-3-7",
    )
    request_summary_manager.update_llm_run(request_id=request_id, llm_run=run1)

    run2 = LLMRun(
        run_id="run-2",
        input_tokens=2000,
        output_tokens=1000,
        cached_tokens=1500,
        money_spent=0.025,
        cached_tokens_money_spent=0.00045,
        cached_tokens_creation_cost=0.00015,
        llm_model="claude-3-7",
    )
    request_summary_manager.update_llm_run(request_id=request_id, llm_run=run2)

    summary = request_summary_manager.get_summary(request_id=request_id)
    summary.calculate()

    assert summary.tokens_usage.cached_tokens == 2300  # 800 + 1500
    assert abs(summary.tokens_usage.cached_tokens_money_spent - 0.00069) < 0.00001  # 0.00024 + 0.00045
    assert abs(summary.tokens_usage.cached_tokens_creation_money_spent - 0.00025) < 0.00001  # 0.0001 + 0.00015

    request_summary_manager.clear_summary(request_id=request_id)


def test_request_summary_manager_mixed_models():
    """Test aggregation across different models (some with caching, some without)."""
    request_id = "test-request-mixed"

    runs = [
        LLMRun(
            run_id="run-claude",
            input_tokens=5000,
            output_tokens=200,
            cached_tokens=4800,
            money_spent=0.00504,
            cached_tokens_money_spent=0.00144,
            cached_tokens_creation_cost=0.0003,
            llm_model="claude-3-7",
        ),
        LLMRun(
            run_id="run-gpt",
            input_tokens=1000,
            output_tokens=500,
            cached_tokens=0,
            money_spent=0.015,
            cached_tokens_money_spent=0.0,
            cached_tokens_creation_cost=0.0,
            llm_model="gpt-4o",
        ),
    ]

    for run in runs:
        request_summary_manager.update_llm_run(request_id=request_id, llm_run=run)

    summary = request_summary_manager.get_summary(request_id=request_id)
    summary.calculate()

    assert summary.tokens_usage.input_tokens == 6000
    assert summary.tokens_usage.cached_tokens == 4800
    assert abs(summary.tokens_usage.money_spent - 0.02004) < 0.00001
    assert abs(summary.tokens_usage.cached_tokens_money_spent - 0.00144) < 0.00001
    assert abs(summary.tokens_usage.cached_tokens_creation_money_spent - 0.0003) < 0.00001

    request_summary_manager.clear_summary(request_id=request_id)
