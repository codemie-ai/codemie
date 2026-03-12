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

from unittest.mock import patch

import pytest

from codemie.rest_api.models.index import IndexInfo
from codemie.core.models import CreatedByUser, TokensUsage
from codemie.rest_api.security.user import User
from codemie.service.monitoring.datasource_monitoring_service import DatasourceMonitoringService
from codemie.service.monitoring.metrics_constants import MetricsAttributes
from codemie.service.request_summary_manager import LLMRun


@pytest.fixture
def mock_index_info():
    return IndexInfo(
        project_name="project",
        repo_name="repo",
        embeddings_model="model",
        current_state=10,
        complete_state=10,
        index_type="code",
        created_by=CreatedByUser(username="test_user", id="123"),
    )


@pytest.fixture
def mock_user():
    return User(id='test_id', name='test_name', username='test_user_name')


@pytest.fixture
def mock_tokens_usage():
    return TokensUsage(
        input_tokens=100,
        output_tokens=50,
        cached_tokens=0,
        money_spent=0.0001,
        cached_tokens_money_spent=0.0,
    )


@patch.object(DatasourceMonitoringService, "send_count_metric")
def test_send_indexing_metrics(mock_send_count_metric, mock_index_info):
    base_metric_name = DatasourceMonitoringService.DATASOURCE_INDEX_BASE_METRIC
    completed = True
    additional_attributes = {"extra_attribute": "extra_value"}

    expected_attributes = {
        "datasource_type": mock_index_info.index_type,
        "project": mock_index_info.project_name,
        "repo_name": mock_index_info.repo_name,
        "embeddings_model": mock_index_info.embeddings_model,
        "user_name": mock_index_info.created_by.username,
        "user_id": mock_index_info.created_by.id,
        **additional_attributes,
    }

    DatasourceMonitoringService.send_indexing_metrics(
        base_metric_name, mock_index_info, completed, additional_attributes=additional_attributes
    )

    mock_send_count_metric.assert_any_call(
        name=f"{base_metric_name}_total",
        attributes=expected_attributes,
    )

    mock_send_count_metric.assert_any_call(
        name=f"{base_metric_name}_documents",
        attributes=expected_attributes,
        count=mock_index_info.current_state,
    )


@patch.object(DatasourceMonitoringService, "send_count_metric")
def test_send_indexing_metrics_with_user(mock_send_count_metric, mock_index_info, mock_user):
    base_metric_name = DatasourceMonitoringService.DATASOURCE_INDEX_BASE_METRIC
    completed = True
    additional_attributes = {"extra_attribute": "extra_value"}

    expected_attributes = {
        "datasource_type": mock_index_info.index_type,
        "project": mock_index_info.project_name,
        "repo_name": mock_index_info.repo_name,
        "embeddings_model": mock_index_info.embeddings_model,
        "user_name": mock_user.name,
        "user_id": mock_user.id,
        **additional_attributes,
    }

    DatasourceMonitoringService.send_indexing_metrics(
        base_metric_name,
        mock_index_info,
        completed,
        user_id=mock_user.id,
        user_name=mock_user.name,
        additional_attributes=additional_attributes,
    )

    mock_send_count_metric.assert_any_call(
        name=f"{base_metric_name}_total",
        attributes=expected_attributes,
    )

    mock_send_count_metric.assert_any_call(
        name=f"{base_metric_name}_documents",
        attributes=expected_attributes,
        count=mock_index_info.current_state,
    )


@patch.object(DatasourceMonitoringService, "send_count_metric")
def test_send_indexing_metrics_with_incomplete_status(mock_send_count_metric, mock_index_info):
    base_metric_name = "datasource_index"
    completed = False  # Incomplete indexing
    additional_attributes = {"error_class": "error"}

    DatasourceMonitoringService.send_indexing_metrics(
        base_metric_name, mock_index_info, completed, additional_attributes=additional_attributes
    )
    # Ensure that the status attribute is correctly set to 'error'
    assert all(
        call.kwargs["attributes"]["error_class"] == "error" for call in mock_send_count_metric.call_args_list
    ), "Error class attribute should be 'error_class' for incomplete indexing"


@patch.object(DatasourceMonitoringService, "send_count_metric")
def test_send_datasource_tokens_usage_metric(mock_send_count_metric, mock_index_info, mock_user, mock_tokens_usage):
    """Test sending datasource token usage metrics with user context."""
    # Call the method with a user
    DatasourceMonitoringService.send_datasource_tokens_usage_metric(
        index_info=mock_index_info,
        tokens_usage=mock_tokens_usage,
        user=mock_user,
    )

    # Expected attributes
    expected_attributes = {
        MetricsAttributes.DATASOURCE_TYPE: mock_index_info.index_type,
        MetricsAttributes.PROJECT: mock_index_info.project_name,
        MetricsAttributes.REPO_NAME: mock_index_info.repo_name,
        MetricsAttributes.EMBEDDINGS_MODEL: mock_index_info.embeddings_model,
        MetricsAttributes.USER_ID: mock_user.id,
        MetricsAttributes.USER_NAME: mock_user.name,
        MetricsAttributes.USER_EMAIL: mock_user.username,
        MetricsAttributes.INPUT_TOKENS: mock_tokens_usage.input_tokens,
        MetricsAttributes.OUTPUT_TOKENS: mock_tokens_usage.output_tokens,
        MetricsAttributes.CACHE_READ_INPUT_TOKENS: mock_tokens_usage.cached_tokens,
        MetricsAttributes.MONEY_SPENT: mock_tokens_usage.money_spent,
        MetricsAttributes.CACHED_TOKENS_MONEY_SPENT: mock_tokens_usage.cached_tokens_money_spent,
    }

    # Verify the send_count_metric call
    mock_send_count_metric.assert_called_once_with(
        name=DatasourceMonitoringService.DATASOURCE_TOKENS_BASE_METRIC + "_usage",
        attributes=expected_attributes,
    )


@patch.object(DatasourceMonitoringService, "send_count_metric")
def test_send_datasource_tokens_usage_metric_without_user(mock_send_count_metric, mock_index_info, mock_tokens_usage):
    """Test sending datasource token usage metrics without user context."""
    # Call the method without a user
    DatasourceMonitoringService.send_datasource_tokens_usage_metric(
        index_info=mock_index_info,
        tokens_usage=mock_tokens_usage,
    )

    # Expected attributes using created_by from index_info
    expected_attributes = {
        MetricsAttributes.DATASOURCE_TYPE: mock_index_info.index_type,
        MetricsAttributes.PROJECT: mock_index_info.project_name,
        MetricsAttributes.REPO_NAME: mock_index_info.repo_name,
        MetricsAttributes.EMBEDDINGS_MODEL: mock_index_info.embeddings_model,
        MetricsAttributes.USER_ID: mock_index_info.created_by.id,
        MetricsAttributes.USER_NAME: mock_index_info.created_by.username,
        MetricsAttributes.USER_EMAIL: mock_index_info.created_by.username,
        MetricsAttributes.INPUT_TOKENS: mock_tokens_usage.input_tokens,
        MetricsAttributes.OUTPUT_TOKENS: mock_tokens_usage.output_tokens,
        MetricsAttributes.CACHE_READ_INPUT_TOKENS: mock_tokens_usage.cached_tokens,
        MetricsAttributes.MONEY_SPENT: mock_tokens_usage.money_spent,
        MetricsAttributes.CACHED_TOKENS_MONEY_SPENT: mock_tokens_usage.cached_tokens_money_spent,
    }

    # Verify the send_count_metric call
    mock_send_count_metric.assert_called_once_with(
        name=DatasourceMonitoringService.DATASOURCE_TOKENS_BASE_METRIC + "_usage",
        attributes=expected_attributes,
    )


@patch.object(DatasourceMonitoringService, "send_count_metric")
def test_send_datasource_tokens_usage_metric_with_additional_attributes(
    mock_send_count_metric, mock_index_info, mock_user, mock_tokens_usage
):
    """Test sending datasource token usage metrics with additional attributes."""
    # Additional attributes to include
    additional_attributes = {"error": "true", "error_class": "ValueError"}

    # Call the method with additional attributes
    DatasourceMonitoringService.send_datasource_tokens_usage_metric(
        index_info=mock_index_info,
        tokens_usage=mock_tokens_usage,
        user=mock_user,
        additional_attributes=additional_attributes,
    )

    # Expected attributes including additional ones
    expected_attributes = {
        MetricsAttributes.DATASOURCE_TYPE: mock_index_info.index_type,
        MetricsAttributes.PROJECT: mock_index_info.project_name,
        MetricsAttributes.REPO_NAME: mock_index_info.repo_name,
        MetricsAttributes.EMBEDDINGS_MODEL: mock_index_info.embeddings_model,
        MetricsAttributes.USER_ID: mock_user.id,
        MetricsAttributes.USER_NAME: mock_user.name,
        MetricsAttributes.USER_EMAIL: mock_user.username,
        MetricsAttributes.INPUT_TOKENS: mock_tokens_usage.input_tokens,
        MetricsAttributes.OUTPUT_TOKENS: mock_tokens_usage.output_tokens,
        MetricsAttributes.CACHE_READ_INPUT_TOKENS: mock_tokens_usage.cached_tokens,
        MetricsAttributes.MONEY_SPENT: mock_tokens_usage.money_spent,
        MetricsAttributes.CACHED_TOKENS_MONEY_SPENT: mock_tokens_usage.cached_tokens_money_spent,
        "error": "true",
        "error_class": "ValueError",
    }

    # Verify the send_count_metric call with all expected attributes
    mock_send_count_metric.assert_called_once_with(
        name=DatasourceMonitoringService.DATASOURCE_TOKENS_BASE_METRIC + "_usage",
        attributes=expected_attributes,
    )


@patch.object(DatasourceMonitoringService, "send_count_metric")
def test_send_datasource_tokens_usage_metrics_by_model(mock_send_count_metric, mock_index_info, mock_user):
    """Test sending per-model datasource token usage metrics."""
    # Create mock LLM runs for different models
    llm_runs = [
        LLMRun(
            run_id="run1",
            llm_model="codemie-text-embedding-ada-002",
            input_tokens=100,
            output_tokens=0,
            cached_tokens=0,
            money_spent=0.0001,
            cached_tokens_money_spent=0.0,
        ),
        LLMRun(
            run_id="run2",
            llm_model="gpt-4o-2024-08-06",
            input_tokens=200,
            output_tokens=50,
            cached_tokens=10,
            money_spent=0.002,
            cached_tokens_money_spent=0.0001,
        ),
        LLMRun(
            run_id="run3",
            llm_model="gpt-4o-2024-08-06",  # Same model as run2, should aggregate
            input_tokens=100,
            output_tokens=25,
            cached_tokens=5,
            money_spent=0.001,
            cached_tokens_money_spent=0.00005,
        ),
    ]

    # Call the method
    DatasourceMonitoringService.send_datasource_tokens_usage_metrics_by_model(
        index_info=mock_index_info,
        llm_runs=llm_runs,
        user=mock_user,
    )

    # Should send 2 metrics (one per unique model)
    assert mock_send_count_metric.call_count == 2

    # Get all calls
    calls = mock_send_count_metric.call_args_list

    # Verify metrics were sent for both models
    sent_models = {call.kwargs['attributes'][MetricsAttributes.LLM_MODEL] for call in calls}
    assert sent_models == {"codemie-text-embedding-ada-002", "gpt-4o-2024-08-06"}

    # Verify aggregation for gpt-4o (2 runs)
    gpt4o_call = [
        call for call in calls if call.kwargs['attributes'][MetricsAttributes.LLM_MODEL] == "gpt-4o-2024-08-06"
    ][0]
    assert gpt4o_call.kwargs['attributes'][MetricsAttributes.INPUT_TOKENS] == 300  # 200 + 100
    assert gpt4o_call.kwargs['attributes'][MetricsAttributes.OUTPUT_TOKENS] == 75  # 50 + 25
    assert gpt4o_call.kwargs['attributes'][MetricsAttributes.CACHE_READ_INPUT_TOKENS] == 15  # 10 + 5
    assert gpt4o_call.kwargs['attributes'][MetricsAttributes.MONEY_SPENT] == 0.003  # 0.002 + 0.001

    # Verify embedding model
    embedding_call = [
        call
        for call in calls
        if call.kwargs['attributes'][MetricsAttributes.LLM_MODEL] == "codemie-text-embedding-ada-002"
    ][0]
    assert embedding_call.kwargs['attributes'][MetricsAttributes.INPUT_TOKENS] == 100
    assert embedding_call.kwargs['attributes'][MetricsAttributes.OUTPUT_TOKENS] == 0
