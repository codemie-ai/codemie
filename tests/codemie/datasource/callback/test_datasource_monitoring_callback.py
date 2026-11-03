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
from unittest.mock import MagicMock, patch

from langchain_core.documents import Document

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
    @patch.object(IndexInfo, "update_progress")
    def test_on_complete_with_tokens_usage(
        self,
        mock_update_progress,
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
        mock_update_progress.assert_called_once()

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
    @patch.object(IndexInfo, "update_progress")
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
    @patch.object(IndexInfo, "update_progress")
    def test_on_error_with_tokens_usage(
        self,
        mock_update_progress,
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
        mock_update_progress.assert_called_once()

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
    @patch.object(IndexInfo, "update_progress")
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

    def test_on_split_documents_counts_endoftext_marker(self, mock_index_info, mock_user, mock_request_uuid):
        callback = DatasourceMonitoringCallback(mock_index_info, mock_user, request_uuid=mock_request_uuid)

        callback.on_split_documents([Document(page_content="x = '<|endoftext|>'")])

        assert callback._estimated_embedding_tokens > 0

    def test_on_split_documents_swallows_estimator_error(self, mock_index_info, mock_user, mock_request_uuid):
        callback = DatasourceMonitoringCallback(mock_index_info, mock_user, request_uuid=mock_request_uuid)
        callback._estimated_embedding_tokens = 5
        secret = "SECRET-DOCUMENT-CONTENT"

        with (
            patch(
                "codemie.datasource.callback.datasource_monitoring_callback.calculate_tokens",
                side_effect=ValueError(secret),
            ),
            patch("codemie.datasource.callback.datasource_monitoring_callback.logger") as mock_logger,
        ):
            callback.on_split_documents([Document(page_content=secret)])

        assert callback._estimated_embedding_tokens == 5
        mock_logger.warning.assert_called_once()
        assert secret not in str(mock_logger.warning.call_args)


class TestEstimatedEmbeddingFallbackMoney:
    """The last-resort branch reached when no embedding usage was captured at all.

    Completion and error give **different** answers and that is the point: a completed
    embedding on the LiteLLM path is *unsettled* (AD-28, an absence the ledger fills in), while
    a failed indexing run billed nothing and carries a real, measured *zero* (AD-20). Reporting
    one as the other either fabricates money or loses it.
    """

    @staticmethod
    def _callback_with_estimated_tokens(index_info, user, request_uuid, tokens=2587):
        callback = DatasourceMonitoringCallback(index=index_info, user=user, request_uuid=request_uuid)
        callback._estimated_embedding_tokens = tokens
        return callback

    @patch("codemie.datasource.callback.datasource_monitoring_callback.uses_litellm", return_value=True)
    @patch("codemie.datasource.callback.datasource_monitoring_callback.llm_service.get_embeddings_model_cost")
    @patch("codemie.datasource.callback.datasource_monitoring_callback.request_summary_manager")
    @patch.object(DatasourceMonitoringService, "send_indexing_metrics")
    @patch.object(DatasourceMonitoringService, "send_datasource_tokens_usage_metrics_by_model")
    @patch.object(IndexInfo, "update_progress")
    def test_on_complete_on_litellm_path_is_unsettled_not_zero(
        self,
        mock_update_progress,
        mock_send_by_model,
        mock_send_indexing_metrics,
        mock_request_summary_manager,
        mock_get_embeddings_model_cost,
        mock_uses_litellm,
        mock_index_info,
        mock_user,
        mock_request_uuid,
    ):
        mock_summary = RequestSummary(request_id=mock_request_uuid)
        mock_summary.tokens_usage = None
        mock_request_summary_manager.get_summary.return_value = mock_summary

        callback = self._callback_with_estimated_tokens(mock_index_info, mock_user, mock_request_uuid)
        callback.on_complete(result="some result")

        llm_run = mock_send_by_model.call_args[1]["llm_runs"][0]
        assert llm_run.money_spent is None
        assert not isinstance(llm_run.money_spent, float)
        mock_get_embeddings_model_cost.assert_not_called()
        assert llm_run.input_tokens == 2587

    @patch("codemie.datasource.callback.datasource_monitoring_callback.uses_litellm", return_value=False)
    @patch("codemie.datasource.callback.datasource_monitoring_callback.llm_service.get_embeddings_model_cost")
    @patch("codemie.datasource.callback.datasource_monitoring_callback.request_summary_manager")
    @patch.object(DatasourceMonitoringService, "send_indexing_metrics")
    @patch.object(DatasourceMonitoringService, "send_datasource_tokens_usage_metrics_by_model")
    @patch.object(IndexInfo, "update_progress")
    def test_on_complete_off_the_litellm_path_still_estimates(
        self,
        mock_update_progress,
        mock_send_by_model,
        mock_send_indexing_metrics,
        mock_request_summary_manager,
        mock_get_embeddings_model_cost,
        mock_uses_litellm,
        mock_index_info,
        mock_user,
        mock_request_uuid,
    ):
        """The mirror case - removing the estimate everywhere would break the default path."""
        mock_summary = RequestSummary(request_id=mock_request_uuid)
        mock_summary.tokens_usage = None
        mock_request_summary_manager.get_summary.return_value = mock_summary
        mock_get_embeddings_model_cost.return_value = MagicMock(input=0.0001)

        callback = self._callback_with_estimated_tokens(mock_index_info, mock_user, mock_request_uuid, tokens=100)
        callback.on_complete(result="some result")

        llm_run = mock_send_by_model.call_args[1]["llm_runs"][0]
        assert llm_run.money_spent == pytest.approx(100 * 0.0001)

    @patch("codemie.datasource.callback.datasource_monitoring_callback.uses_litellm", return_value=True)
    @patch("codemie.datasource.callback.datasource_monitoring_callback.llm_service.get_embeddings_model_cost")
    @patch("codemie.datasource.callback.datasource_monitoring_callback.request_summary_manager")
    @patch.object(DatasourceMonitoringService, "send_indexing_metrics")
    @patch.object(DatasourceMonitoringService, "send_datasource_tokens_usage_metrics_by_model")
    @patch.object(IndexInfo, "update_progress")
    def test_on_error_carries_a_real_measured_zero(
        self,
        mock_update_progress,
        mock_send_by_model,
        mock_send_indexing_metrics,
        mock_request_summary_manager,
        mock_get_embeddings_model_cost,
        mock_uses_litellm,
        mock_index_info,
        mock_user,
        mock_request_uuid,
    ):
        """AD-20 on the LiteLLM path: the failure was not billed, so zero - and never `None`."""
        mock_summary = RequestSummary(request_id=mock_request_uuid)
        mock_summary.tokens_usage = None
        mock_request_summary_manager.get_summary.return_value = mock_summary

        callback = self._callback_with_estimated_tokens(mock_index_info, mock_user, mock_request_uuid)
        callback.on_error(exception=ValueError("Test error"))

        llm_run = mock_send_by_model.call_args[1]["llm_runs"][0]
        assert llm_run.money_spent == 0.0
        assert llm_run.money_spent is not None
        mock_get_embeddings_model_cost.assert_not_called()
        assert llm_run.input_tokens == 2587

    @patch("codemie.datasource.callback.datasource_monitoring_callback.uses_litellm", return_value=False)
    @patch("codemie.datasource.callback.datasource_monitoring_callback.llm_service.get_embeddings_model_cost")
    @patch("codemie.datasource.callback.datasource_monitoring_callback.request_summary_manager")
    @patch.object(DatasourceMonitoringService, "send_indexing_metrics")
    @patch.object(DatasourceMonitoringService, "send_datasource_tokens_usage_metrics_by_model")
    @patch.object(IndexInfo, "update_progress")
    def test_on_error_off_the_litellm_path_keeps_the_local_estimate(
        self,
        mock_update_progress,
        mock_send_by_model,
        mock_send_indexing_metrics,
        mock_request_summary_manager,
        mock_get_embeddings_model_cost,
        mock_uses_litellm,
        mock_index_info,
        mock_user,
        mock_request_uuid,
    ):
        """The mirror case, and it is why the failure zero is gated rather than unconditional.

        This branch's own condition — no proxy usage captured — is the **ordinary** case for a
        non-LiteLLM deployment, where proxy data never exists at all. AD-20 scopes its zero to the
        ledger (*"counts come from the ledger, never from the callback"*), so an unconditional zero
        here would replace the only figure such a deployment ever had for a failed index with a claim
        that it cost nothing. Driven from `uses_litellm` so the assertion pins AD-2's routing seam.
        """
        mock_get_embeddings_model_cost.return_value = MagicMock(input=0.0001)
        mock_summary = RequestSummary(request_id=mock_request_uuid)
        mock_summary.tokens_usage = None
        mock_request_summary_manager.get_summary.return_value = mock_summary

        callback = self._callback_with_estimated_tokens(mock_index_info, mock_user, mock_request_uuid, tokens=100)
        callback.on_error(exception=ValueError("Test error"))

        llm_run = mock_send_by_model.call_args[1]["llm_runs"][0]
        assert llm_run.money_spent == pytest.approx(100 * 0.0001)
