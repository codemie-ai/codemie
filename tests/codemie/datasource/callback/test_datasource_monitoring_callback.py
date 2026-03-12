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

import uuid
from unittest.mock import patch

import pytest

from codemie.core.models import TokensUsage, CreatedByUser
from codemie.datasource.callback.datasource_monitoring_callback import DatasourceMonitoringCallback
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.security.user import User
from codemie.service.monitoring.datasource_monitoring_service import DatasourceMonitoringService
from codemie.service.request_summary_manager import RequestSummary


@pytest.fixture
def mock_index_info():
    return IndexInfo(
        project_name="test_project",
        repo_name="test_repo",
        embeddings_model="ada-002",
        current_state=10,
        complete_state=10,
        index_type="code",
        created_by=CreatedByUser(username="test_user", id="123"),
    )


@pytest.fixture
def mock_user():
    return User(id="test_user_id", name="Test User", username="test_username")


@pytest.fixture
def mock_tokens_usage():
    return TokensUsage(
        input_tokens=2587,
        output_tokens=0,
        cached_tokens=0,
        money_spent=0.0002587,
    )


@pytest.fixture
def mock_request_uuid():
    return str(uuid.uuid4())


class TestDatasourceMonitoringCallback:
    @patch("codemie.datasource.callback.datasource_monitoring_callback.request_summary_manager")
    @patch.object(DatasourceMonitoringService, "send_indexing_metrics")
    @patch.object(DatasourceMonitoringService, "send_datasource_tokens_usage_metric")
    @patch.object(IndexInfo, "update")
    def test_on_complete_with_tokens_usage(
        self,
        mock_update,
        mock_send_tokens_metric,
        mock_send_indexing_metrics,
        mock_request_summary_manager,
        mock_index_info,
        mock_user,
        mock_tokens_usage,
        mock_request_uuid,
    ):
        """Test that token usage metrics are sent when processing completes successfully."""
        # Setup the mock for request summary manager
        mock_summary = RequestSummary(request_id=mock_request_uuid)
        mock_summary.tokens_usage = mock_tokens_usage
        mock_request_summary_manager.get_summary.return_value = mock_summary

        # Create callback
        callback = DatasourceMonitoringCallback(index=mock_index_info, user=mock_user, request_uuid=mock_request_uuid)

        # Call on_complete
        callback.on_complete(result="some result")

        # Verify send_indexing_metrics was called
        mock_send_indexing_metrics.assert_called_once()

        # Verify index was updated with token usage
        assert mock_index_info.tokens_usage == mock_tokens_usage
        mock_update.assert_called_once()

        # Verify send_datasource_tokens_usage_metric was called with correct parameters
        mock_send_tokens_metric.assert_called_once_with(
            index_info=mock_index_info,
            tokens_usage=mock_tokens_usage,
            user=mock_user,
        )

        # Verify summary was cleared
        mock_request_summary_manager.clear_summary.assert_called_once_with(mock_request_uuid)

    @patch("codemie.datasource.callback.datasource_monitoring_callback.request_summary_manager")
    @patch.object(DatasourceMonitoringService, "send_indexing_metrics")
    @patch.object(DatasourceMonitoringService, "send_datasource_tokens_usage_metric")
    @patch.object(IndexInfo, "update")
    def test_on_complete_without_tokens_usage(
        self,
        mock_update,
        mock_send_tokens_metric,
        mock_send_indexing_metrics,
        mock_request_summary_manager,
        mock_index_info,
        mock_user,
        mock_request_uuid,
    ):
        """Test that token usage metrics are not sent when no tokens usage is available."""
        # Setup the mock for request summary manager with no tokens_usage
        mock_summary = RequestSummary(request_id=mock_request_uuid)
        mock_summary.tokens_usage = None
        mock_request_summary_manager.get_summary.return_value = mock_summary

        # Create callback
        callback = DatasourceMonitoringCallback(index=mock_index_info, user=mock_user, request_uuid=mock_request_uuid)

        # Call on_complete
        callback.on_complete(result="some result")

        # Verify send_indexing_metrics was called
        mock_send_indexing_metrics.assert_called_once()

        # Verify index was updated with token usage
        assert mock_index_info.tokens_usage is None
        mock_update.assert_called_once()

        # Verify send_datasource_tokens_usage_metric was NOT called
        mock_send_tokens_metric.assert_not_called()

        # Verify summary was cleared
        mock_request_summary_manager.clear_summary.assert_called_once_with(mock_request_uuid)

    @patch("codemie.datasource.callback.datasource_monitoring_callback.request_summary_manager")
    @patch.object(DatasourceMonitoringService, "send_indexing_metrics")
    @patch.object(DatasourceMonitoringService, "send_datasource_tokens_usage_metric")
    @patch.object(IndexInfo, "update")
    def test_on_error_with_tokens_usage(
        self,
        mock_update,
        mock_send_tokens_metric,
        mock_send_indexing_metrics,
        mock_request_summary_manager,
        mock_index_info,
        mock_user,
        mock_tokens_usage,
        mock_request_uuid,
    ):
        """Test that token usage metrics are sent with error attributes when processing fails."""
        # Setup the mock for request summary manager
        mock_summary = RequestSummary(request_id=mock_request_uuid)
        mock_summary.tokens_usage = mock_tokens_usage
        mock_request_summary_manager.get_summary.return_value = mock_summary

        # Create callback
        callback = DatasourceMonitoringCallback(index=mock_index_info, user=mock_user, request_uuid=mock_request_uuid)

        # Create test exception
        test_exception = ValueError("Test error")

        # Call on_error
        callback.on_error(exception=test_exception)

        # Verify send_indexing_metrics was called with completed=False and error attributes
        mock_send_indexing_metrics.assert_called_once_with(
            base_metric_name=callback.datasource_metric_name,
            index_info=mock_index_info,
            user_id=mock_user.id,
            user_name=mock_user.name,
            completed=False,
            additional_attributes={"error_class": "ValueError"},
        )

        # Verify index was updated with token usage
        assert mock_index_info.tokens_usage == mock_tokens_usage
        mock_update.assert_called_once()

        # Verify send_datasource_tokens_usage_metric was called with correct parameters including error attributes
        mock_send_tokens_metric.assert_called_once_with(
            index_info=mock_index_info,
            tokens_usage=mock_tokens_usage,
            user=mock_user,
            additional_attributes={"error": "true", "error_class": "ValueError"},
        )

        # Verify summary was cleared
        mock_request_summary_manager.clear_summary.assert_called_once_with(mock_request_uuid)

    @patch("codemie.datasource.callback.datasource_monitoring_callback.request_summary_manager")
    @patch.object(DatasourceMonitoringService, "send_indexing_metrics")
    @patch.object(DatasourceMonitoringService, "send_datasource_tokens_usage_metric")
    @patch.object(IndexInfo, "update")
    def test_on_error_without_tokens_usage(
        self,
        mock_update,
        mock_send_tokens_metric,
        mock_send_indexing_metrics,
        mock_request_summary_manager,
        mock_index_info,
        mock_user,
        mock_request_uuid,
    ):
        """Test that token usage metrics are not sent when no tokens usage is available during an error."""
        # Setup the mock for request summary manager with no tokens_usage
        mock_summary = RequestSummary(request_id=mock_request_uuid)
        mock_summary.tokens_usage = None
        mock_request_summary_manager.get_summary.return_value = mock_summary

        # Create callback
        callback = DatasourceMonitoringCallback(index=mock_index_info, user=mock_user, request_uuid=mock_request_uuid)

        # Create test exception
        test_exception = ValueError("Test error")

        # Call on_error
        callback.on_error(exception=test_exception)

        # Verify send_indexing_metrics was called with completed=False and error attributes
        mock_send_indexing_metrics.assert_called_once()

        # Verify index was updated with token usage
        assert mock_index_info.tokens_usage is None
        mock_update.assert_called_once()

        # Verify send_datasource_tokens_usage_metric was NOT called
        mock_send_tokens_metric.assert_not_called()

        # Verify summary was cleared
        mock_request_summary_manager.clear_summary.assert_called_once_with(mock_request_uuid)
