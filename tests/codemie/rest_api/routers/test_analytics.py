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

"""Unit tests for analytics router endpoints.

Tests cover:
- Error handling decorator behavior
- Response formatting and caching
- Query parameter handling
- Authentication and authorization
- Pagination validation
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.responses import JSONResponse

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.analytics import Metric, SummariesResponse, TabularResponse
from codemie.rest_api.routers.analytics._common import _create_response, handle_analytics_errors
from codemie.rest_api.security.user import User
from codemie.configs import config


@pytest.fixture
def mock_user():
    """Create a mock authenticated user for testing."""
    user = MagicMock(spec=User)
    user.id = "user123@example.com"
    user.username = "testuser"
    user.email = "testuser@example.com"
    user.name = "Test User"
    user.is_admin = False
    user.is_maintainer = False
    user.is_admin_or_maintainer = False
    user.project_names = ["project1", "project2"]
    user.admin_project_names = ["project1"]
    return user


@pytest.fixture
def sample_summaries_response_data():
    """Sample data for SummariesResponse."""
    return {
        "data": {
            "metrics": [
                {"id": "total_input_tokens", "label": "Total Input Tokens", "type": "number", "value": 1000},
                {"id": "total_output_tokens", "label": "Total Output Tokens", "type": "number", "value": 2000},
                {"id": "total_cached_input_tokens", "label": "Cached Tokens", "type": "number", "value": 500},
                {"id": "total_money_spent", "label": "Money Spent", "type": "currency", "value": 1.25},
            ]
        },
        "metadata": {
            "timestamp": "2025-01-15T10:00:00Z",
            "data_as_of": "2025-01-15T09:55:00Z",
            "filters_applied": {"time_period": "last_30_days"},
            "execution_time_ms": 45.2,
        },
    }


@pytest.fixture
def sample_tabular_response_data():
    """Sample data for TabularResponse."""
    return {
        "data": {
            "columns": [
                {"id": "user_id", "label": "User ID", "type": "string"},
                {"id": "tokens", "label": "Tokens", "type": "number"},
            ],
            "rows": [
                {"user_id": "user1@example.com", "tokens": 1000},
                {"user_id": "user2@example.com", "tokens": 2000},
            ],
            "totals": {"tokens": 3000},
        },
        "metadata": {
            "timestamp": "2025-01-15T10:00:00Z",
            "data_as_of": "2025-01-15T09:55:00Z",
            "filters_applied": {},
            "execution_time_ms": 30.5,
        },
        "pagination": {"page": 0, "per_page": 50, "total_count": 2, "has_more": False},
    }


class TestHandleAnalyticsErrorsDecorator:
    """Tests for the handle_analytics_errors decorator."""

    @pytest.mark.asyncio
    async def test_decorator_passes_through_success(self, mock_user):
        """Verify decorator doesn't interfere with successful responses."""
        # Arrange
        expected_response = JSONResponse(content={"data": "test"}, status_code=200)

        @handle_analytics_errors("test endpoint")
        async def mock_endpoint(user: User) -> JSONResponse:
            return expected_response

        # Act
        result = await mock_endpoint(user=mock_user)

        # Assert
        assert result is expected_response
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_decorator_transforms_value_error_to_400(self, mock_user):
        """Verify invalid parameter errors return 400 Bad Request."""

        # Arrange
        @handle_analytics_errors("test endpoint")
        async def mock_endpoint(user: User) -> JSONResponse:
            raise ValueError("Invalid time_period: 'last_invalid_days'")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await mock_endpoint(user=mock_user)

        exception = exc_info.value
        assert exception.code == status.HTTP_400_BAD_REQUEST
        assert exception.message == "Invalid request parameters"
        assert "Invalid time_period" in exception.details
        assert "time_period" in exception.help

    @pytest.mark.asyncio
    async def test_decorator_transforms_generic_exception_to_500(self, mock_user):
        """Verify unexpected errors return 500 Internal Server Error."""

        # Arrange
        @handle_analytics_errors("analytics summaries")
        async def mock_endpoint(user: User) -> JSONResponse:
            raise Exception("Database connection lost")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await mock_endpoint(user=mock_user)

        exception = exc_info.value
        assert exception.code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "analytics summaries" in exception.message
        assert "Database connection lost" in exception.details

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics._common.logger")
    async def test_decorator_logs_user_id_from_kwargs(self, mock_logger, mock_user):
        """Verify user context is extracted for logging."""
        # Arrange
        mock_user.id = "user123@example.com"

        @handle_analytics_errors("test endpoint")
        async def mock_endpoint(user: User) -> JSONResponse:
            raise Exception("Test error")

        # Act
        with pytest.raises(ExtendedHTTPException):
            await mock_endpoint(user=mock_user)

        # Assert
        mock_logger.exception.assert_called_once()
        call_args = mock_logger.exception.call_args[0][0]
        assert "user123@example.com" in call_args

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics._common.logger")
    async def test_decorator_handles_missing_user(self, mock_logger):
        """Verify decorator handles cases where user is not in kwargs."""

        # Arrange
        @handle_analytics_errors("test endpoint")
        async def mock_endpoint() -> JSONResponse:
            raise Exception("Test error")

        # Act
        with pytest.raises(ExtendedHTTPException):
            await mock_endpoint()

        # Assert
        mock_logger.exception.assert_called_once()
        call_args = mock_logger.exception.call_args[0][0]
        assert "unknown" in call_args


class TestCreateResponse:
    """Tests for the _create_response helper function."""

    def test_creates_response_with_pydantic_validation(self, sample_summaries_response_data):
        """Verify response data is validated against Pydantic model."""
        # Act
        with patch.object(config, "ENV", "production"):
            response = _create_response(sample_summaries_response_data, SummariesResponse)

        # Assert
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_200_OK
        response_body = json.loads(response.body)
        assert "data" in response_body
        assert "metadata" in response_body
        assert len(response_body["data"]["metrics"]) == 4

    def test_adds_cache_headers(self, sample_summaries_response_data):
        """Verify cache control and ETag headers are added."""
        # Act
        with patch.object(config, "ENV", "production"):
            response = _create_response(sample_summaries_response_data, SummariesResponse)

        # Assert
        assert "Cache-Control" in response.headers
        assert response.headers["Cache-Control"] == "public, max-age=300"
        assert "ETag" in response.headers

    def test_etag_consistent_for_same_data(self, sample_summaries_response_data):
        """Verify ETag is deterministic for identical data."""
        # Act
        with patch.object(config, "ENV", "production"):
            response1 = _create_response(sample_summaries_response_data, SummariesResponse)
            response2 = _create_response(sample_summaries_response_data, SummariesResponse)

        # Assert
        assert response1.headers["ETag"] == response2.headers["ETag"]

    def test_etag_different_for_different_data(self, sample_summaries_response_data):
        """Verify ETag changes when data changes."""
        # Arrange
        data1 = copy.deepcopy(sample_summaries_response_data)
        data2 = copy.deepcopy(sample_summaries_response_data)
        data2["data"]["metrics"][0]["value"] = 9999

        # Act
        with patch.object(config, "ENV", "production"):
            response1 = _create_response(data1, SummariesResponse)
            response2 = _create_response(data2, SummariesResponse)

        # Assert
        assert response1.headers["ETag"] != response2.headers["ETag"]

    def test_etag_is_sha256_hash_of_response_body(self, sample_summaries_response_data):
        """Verify ETag is SHA-256 hash of response body."""
        # Act
        with patch.object(config, "ENV", "production"):
            response = _create_response(sample_summaries_response_data, SummariesResponse)

        # Assert
        validated = SummariesResponse(**sample_summaries_response_data)
        response_dict = validated.model_dump(by_alias=True)
        expected_etag = hashlib.sha256(json.dumps(response_dict, sort_keys=True).encode()).hexdigest()
        assert response.headers["ETag"] == expected_etag


class TestGetSummariesEndpoint:
    """Tests for the /v1/analytics/summaries endpoint."""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.usage.AnalyticsService")
    async def test_with_time_period(self, mock_service_class, mock_user, sample_summaries_response_data):
        """Verify predefined time period is passed to service."""
        # Arrange
        from codemie.rest_api.routers.analytics.usage import get_summaries

        mock_service = AsyncMock()
        mock_service.get_summaries.return_value = sample_summaries_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_summaries(
            user=mock_user,
            time_period="last_30_days",
            start_date=None,
            end_date=None,
            users=None,
            projects=None,
            client_source=None,
        )

        # Assert
        mock_service.get_summaries.assert_called_once_with(
            time_period="last_30_days", start_date=None, end_date=None, users=None, projects=None, client_source=None
        )
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.usage.AnalyticsService")
    async def test_with_custom_date_range(self, mock_service_class, mock_user, sample_summaries_response_data):
        """Verify custom date range parameters are parsed correctly."""
        # Arrange
        from codemie.rest_api.routers.analytics.usage import get_summaries

        mock_service = AsyncMock()
        mock_service.get_summaries.return_value = sample_summaries_response_data
        mock_service_class.return_value = mock_service

        start_date = datetime(2025, 1, 1, 0, 0, 0)
        end_date = datetime(2025, 1, 31, 23, 59, 59)

        # Act
        response = await get_summaries(
            user=mock_user,
            time_period=None,
            start_date=start_date,
            end_date=end_date,
            users=None,
            projects=None,
            client_source=None,
        )

        # Assert
        mock_service.get_summaries.assert_called_once_with(
            time_period=None, start_date=start_date, end_date=end_date, users=None, projects=None, client_source=None
        )
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.usage.AnalyticsService")
    async def test_with_user_filter(self, mock_service_class, mock_user, sample_summaries_response_data):
        """Verify comma-separated users are parsed into list."""
        # Arrange
        from codemie.rest_api.routers.analytics.usage import get_summaries

        mock_service = AsyncMock()
        mock_service.get_summaries.return_value = sample_summaries_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_summaries(
            user=mock_user,
            time_period="last_30_days",
            start_date=None,
            end_date=None,
            users="user1@example.com,user2@example.com",
            projects=None,
            client_source=None,
        )

        # Assert
        mock_service.get_summaries.assert_called_once_with(
            time_period="last_30_days",
            start_date=None,
            end_date=None,
            users=["user1@example.com", "user2@example.com"],
            projects=None,
            client_source=None,
        )
        assert isinstance(response, JSONResponse)

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.usage.AnalyticsService")
    async def test_with_project_filter(self, mock_service_class, mock_user, sample_summaries_response_data):
        """Verify comma-separated projects are parsed into list."""
        # Arrange
        from codemie.rest_api.routers.analytics.usage import get_summaries

        mock_service = AsyncMock()
        mock_service.get_summaries.return_value = sample_summaries_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_summaries(
            user=mock_user,
            time_period="last_30_days",
            start_date=None,
            end_date=None,
            users=None,
            projects="codemie,project-alpha",
            client_source=None,
        )

        # Assert
        mock_service.get_summaries.assert_called_once_with(
            time_period="last_30_days",
            start_date=None,
            end_date=None,
            users=None,
            projects=["codemie", "project-alpha"],
            client_source=None,
        )
        assert isinstance(response, JSONResponse)

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.usage.AnalyticsService")
    async def test_with_client_source_filter(self, mock_service_class, mock_user, sample_summaries_response_data):
        """Verify client_source enum query param is threaded to the service as its string value."""
        # Arrange
        from codemie.rest_api.utils.client_context import ClientSource
        from codemie.rest_api.routers.analytics.usage import get_summaries

        mock_service = AsyncMock()
        mock_service.get_summaries.return_value = sample_summaries_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_summaries(
            user=mock_user,
            time_period="last_30_days",
            start_date=None,
            end_date=None,
            users=None,
            projects=None,
            client_source=ClientSource.MS_TEAMS_BOT,
        )

        # Assert
        mock_service.get_summaries.assert_called_once_with(
            time_period="last_30_days",
            start_date=None,
            end_date=None,
            users=None,
            projects=None,
            client_source="ms-teams-bot",
        )
        assert isinstance(response, JSONResponse)

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.usage.logger")
    @patch("codemie.rest_api.routers.analytics.usage.AnalyticsService")
    async def test_logs_request_parameters(
        self, mock_service_class, mock_logger, mock_user, sample_summaries_response_data
    ):
        """Verify request parameters are logged for audit trail."""
        # Arrange
        from codemie.rest_api.routers.analytics.usage import get_summaries

        mock_service = AsyncMock()
        mock_service.get_summaries.return_value = sample_summaries_response_data
        mock_service_class.return_value = mock_service

        # Act
        await get_summaries(
            user=mock_user,
            time_period="last_30_days",
            start_date=None,
            end_date=None,
            users="user1@example.com",
            projects="project1",
            client_source=None,
        )

        # Assert
        mock_logger.info.assert_called_once()
        log_message = mock_logger.info.call_args[0][0]
        assert mock_user.id in log_message
        assert "last_30_days" in log_message
        assert "user1@example.com" in log_message
        assert "project1" in log_message


class TestPaginationEndpoints:
    """Tests for pagination in analytics endpoints."""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.usage.AnalyticsService")
    async def test_pagination_defaults(self, mock_service_class, mock_user, sample_tabular_response_data):
        """Verify default pagination values are applied."""
        # Arrange
        from codemie.rest_api.routers.analytics._common import AnalyticsQueryParams
        from codemie.rest_api.routers.analytics.usage import get_assistants_chats

        mock_service = AsyncMock()
        mock_service.get_assistants_chats.return_value = sample_tabular_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_assistants_chats(
            user=mock_user,
            params=AnalyticsQueryParams(page=0, per_page=50),
            client_source=None,
        )

        # Assert
        mock_service.get_assistants_chats.assert_called_once()
        call_kwargs = mock_service.get_assistants_chats.call_args
        # Check positional args (page and per_page are last two)
        assert call_kwargs[0][-2] == 0  # page
        assert call_kwargs[0][-1] == 50  # per_page
        assert isinstance(response, JSONResponse)

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.usage.AnalyticsService")
    async def test_pagination_custom_values(self, mock_service_class, mock_user, sample_tabular_response_data):
        """Verify custom pagination parameters are respected."""
        # Arrange
        from codemie.rest_api.routers.analytics._common import AnalyticsQueryParams
        from codemie.rest_api.routers.analytics.usage import get_assistants_chats

        mock_service = AsyncMock()
        mock_service.get_assistants_chats.return_value = sample_tabular_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_assistants_chats(
            user=mock_user,
            params=AnalyticsQueryParams(page=2, per_page=100),
            client_source=None,
        )

        # Assert
        mock_service.get_assistants_chats.assert_called_once()
        call_kwargs = mock_service.get_assistants_chats.call_args
        # Check positional args (page and per_page are last two)
        assert call_kwargs[0][-2] == 2  # page
        assert call_kwargs[0][-1] == 100  # per_page
        assert isinstance(response, JSONResponse)


class TestAssistantsRequestsEndpoint:
    """Tests for the /v1/analytics/assistants-requests endpoint."""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.usage.AnalyticsService")
    async def test_assistants_requests_route_forwards_filters(
        self, mock_service_class, mock_user, sample_tabular_response_data
    ):
        from codemie.rest_api.routers.analytics._common import AnalyticsQueryParams
        from codemie.rest_api.routers.analytics.usage import get_assistants_requests
        from codemie.rest_api.utils.client_context import ClientSource

        mock_service = AsyncMock()
        mock_service.get_assistants_requests.return_value = sample_tabular_response_data
        mock_service_class.return_value = mock_service

        response = await get_assistants_requests(
            user=mock_user,
            params=AnalyticsQueryParams(
                time_period="last_7_days", users="u1@example.com, u2@example.com", projects="p1", page=3, per_page=40
            ),
            client_source=ClientSource.MS_TEAMS_BOT,
            assistant_id="a-1",
        )

        mock_service.get_assistants_requests.assert_called_once_with(
            time_period="last_7_days",
            start_date=None,
            end_date=None,
            users=["u1@example.com", "u2@example.com"],
            projects=["p1"],
            page=3,
            per_page=40,
            client_source="ms-teams-bot",
            assistant_id="a-1",
        )
        assert isinstance(response, JSONResponse)


class TestWorkflowsEndpoint:
    """Tests for the /v1/analytics/workflows endpoint."""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.usage.AnalyticsService")
    async def test_workflows_endpoint_basic(self, mock_service_class, mock_user, sample_tabular_response_data):
        """Verify workflows endpoint processes request correctly."""
        # Arrange
        from codemie.rest_api.routers.analytics._common import AnalyticsQueryParams
        from codemie.rest_api.routers.analytics.usage import get_workflows

        mock_service = AsyncMock()
        mock_service.get_workflows.return_value = sample_tabular_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_workflows(
            user=mock_user,
            params=AnalyticsQueryParams(time_period="last_7_days", page=0, per_page=50),
            client_source=None,
        )

        # Assert
        mock_service.get_workflows.assert_called_once_with(
            "last_7_days", None, None, None, None, 0, 50, client_source=None
        )
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_200_OK


class TestToolsUsageEndpoint:
    """Tests for the /v1/analytics/tools-usage endpoint."""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.usage.AnalyticsService")
    async def test_tools_usage_endpoint_with_filters(self, mock_service_class, mock_user, sample_tabular_response_data):
        """Verify tools usage endpoint applies filters correctly."""
        # Arrange
        from codemie.rest_api.routers.analytics._common import AnalyticsQueryParams
        from codemie.rest_api.routers.analytics.usage import get_tools_usage

        mock_service = AsyncMock()
        mock_service.get_tools_usage.return_value = sample_tabular_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_tools_usage(
            user=mock_user,
            params=AnalyticsQueryParams(
                start_date=datetime(2025, 1, 1),
                end_date=datetime(2025, 1, 31),
                users="user1@example.com",
                projects="project1,project2",
                page=1,
                per_page=25,
            ),
        )

        # Assert
        mock_service.get_tools_usage.assert_called_once_with(
            None,
            datetime(2025, 1, 1, tzinfo=timezone.utc),
            datetime(2025, 1, 31, tzinfo=timezone.utc),
            ["user1@example.com"],
            ["project1", "project2"],
            1,
            25,
        )
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_200_OK


class TestErrorHandlingIntegration:
    """Integration tests for error handling across endpoints."""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.usage.AnalyticsService")
    async def test_service_value_error_returns_400(self, mock_service_class, mock_user):
        """Verify ValueError from service returns 400 with helpful message."""
        # Arrange
        from codemie.rest_api.routers.analytics.usage import get_summaries

        mock_service = AsyncMock()
        mock_service.get_summaries.side_effect = ValueError("Invalid time_period: 'invalid_value'")
        mock_service_class.return_value = mock_service

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await get_summaries(
                user=mock_user,
                time_period="invalid_value",
                start_date=None,
                end_date=None,
                users=None,
                projects=None,
                client_source=None,
            )

        exception = exc_info.value
        assert exception.code == status.HTTP_400_BAD_REQUEST
        assert exception.message == "Invalid request parameters"
        assert "Invalid time_period" in exception.details

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.usage.AnalyticsService")
    async def test_service_generic_exception_returns_500(self, mock_service_class, mock_user):
        """Verify unexpected service errors return 500."""
        # Arrange
        from codemie.rest_api.routers.analytics.usage import get_summaries

        mock_service = AsyncMock()
        mock_service.get_summaries.side_effect = Exception("Database connection failed")
        mock_service_class.return_value = mock_service

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await get_summaries(
                user=mock_user,
                time_period="last_30_days",
                start_date=None,
                end_date=None,
                users=None,
                projects=None,
                client_source=None,
            )

        exception = exc_info.value
        assert exception.code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert exception.message == "Failed to retrieve analytics summaries"


class TestResponseModelsValidation:
    """Tests for Pydantic model validation in responses."""

    def test_summaries_response_validates_correct_structure(self, sample_summaries_response_data):
        """Verify SummariesResponse model validates correct data structure."""
        # Act
        response = _create_response(sample_summaries_response_data, SummariesResponse)

        # Assert
        response_body = json.loads(response.body)
        assert "data" in response_body
        assert "metadata" in response_body
        assert "metrics" in response_body["data"]
        assert len(response_body["data"]["metrics"]) > 0

    def test_tabular_response_validates_correct_structure(self, sample_tabular_response_data):
        """Verify TabularResponse model validates correct data structure."""
        # Act
        response = _create_response(sample_tabular_response_data, TabularResponse)

        # Assert
        response_body = json.loads(response.body)
        assert "data" in response_body
        assert "metadata" in response_body
        assert "pagination" in response_body
        assert "columns" in response_body["data"]
        assert "rows" in response_body["data"]


class TestMultipleEndpointsPatterns:
    """Test patterns that apply across multiple analytics endpoints."""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.usage.AnalyticsService")
    async def test_agents_usage_endpoint(self, mock_service_class, mock_user, sample_tabular_response_data):
        """Verify agents usage endpoint follows standard pattern."""
        # Arrange
        from codemie.rest_api.routers.analytics._common import AnalyticsQueryParams
        from codemie.rest_api.routers.analytics.usage import get_agents_usage

        mock_service = AsyncMock()
        mock_service.get_agents_usage.return_value = sample_tabular_response_data
        mock_service_class.return_value = mock_service

        # Act
        with patch.object(config, "ENV", "production"):
            response = await get_agents_usage(
                user=mock_user,
                params=AnalyticsQueryParams(time_period="last_24_hours", page=0, per_page=50),
            )

        # Assert
        mock_service.get_agents_usage.assert_called_once()
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_200_OK
        assert "Cache-Control" in response.headers
        assert "ETag" in response.headers

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.activity.AnalyticsService")
    async def test_llms_usage_endpoint(self, mock_service_class, mock_user, sample_tabular_response_data):
        """Verify LLMs usage endpoint follows standard pattern."""
        # Arrange
        from codemie.rest_api.routers.analytics._common import AnalyticsQueryParams
        from codemie.rest_api.routers.analytics.activity import get_llms_usage

        mock_service = AsyncMock()
        mock_service.get_llms_usage.return_value = sample_tabular_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_llms_usage(
            user=mock_user,
            params=AnalyticsQueryParams(
                time_period="last_60_days",
                users="user1@example.com,user2@example.com",
                projects="project1",
                page=0,
                per_page=50,
            ),
            client_source=None,
        )

        # Assert
        mock_service.get_llms_usage.assert_called_once_with(
            "last_60_days",
            None,
            None,
            ["user1@example.com", "user2@example.com"],
            ["project1"],
            0,
            50,
            client_source=None,
        )
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_200_OK


class TestCliEndpoints:
    """Tests for CLI-specific analytics endpoints."""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.cli.AnalyticsService")
    async def test_cli_summary_endpoint(self, mock_service_class, mock_user, sample_summaries_response_data):
        """Verify CLI summary endpoint returns SummariesResponse format."""
        # Arrange
        from codemie.rest_api.routers.analytics.cli import get_cli_summary

        mock_service = AsyncMock()
        mock_service.get_cli_summary.return_value = sample_summaries_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_cli_summary(
            user=mock_user, time_period="last_7_days", start_date=None, end_date=None, users=None, projects=None
        )

        # Assert
        mock_service.get_cli_summary.assert_called_once_with("last_7_days", None, None, None, None)
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.cli.AnalyticsService")
    async def test_get_cli_summary_normalizes_naive_dates(
        self, mock_service_class, mock_user, sample_summaries_response_data
    ):
        """Naive start_date/end_date query params are normalized to UTC before reaching the service."""
        # Arrange
        from codemie.rest_api.routers.analytics.cli import get_cli_summary

        mock_service = AsyncMock()
        mock_service.get_cli_summary.return_value = sample_summaries_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_cli_summary(
            user=mock_user,
            time_period=None,
            start_date=datetime(2026, 1, 1),
            end_date=datetime(2026, 1, 2),
            users=None,
            projects=None,
        )

        # Assert
        call_args = mock_service.get_cli_summary.call_args
        assert call_args.args[1].tzinfo == timezone.utc
        assert call_args.args[2].tzinfo == timezone.utc
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.cli.AnalyticsService")
    async def test_cli_agents_endpoint(self, mock_service_class, mock_user, sample_tabular_response_data):
        """Verify CLI agents endpoint with pagination."""
        # Arrange
        from codemie.rest_api.routers.analytics._common import AnalyticsQueryParams
        from codemie.rest_api.routers.analytics.cli import get_cli_agents

        mock_service = AsyncMock()
        mock_service.get_cli_agents.return_value = sample_tabular_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_cli_agents(
            user=mock_user,
            params=AnalyticsQueryParams(time_period="last_30_days", page=0, per_page=50),
        )

        # Assert
        mock_service.get_cli_agents.assert_called_once()
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.cli_insights.AnalyticsService")
    async def test_cli_insights_weekday_pattern_endpoint(
        self, mock_service_class, mock_user, sample_tabular_response_data
    ):
        """Verify CLI insights weekday pattern endpoint returns tabular response."""
        from codemie.rest_api.routers.analytics._common import AnalyticsQueryParams
        from codemie.rest_api.routers.analytics.cli_insights import get_cli_insights_weekday_pattern

        mock_service = AsyncMock()
        mock_service.get_cli_insights_weekday_pattern.return_value = sample_tabular_response_data
        mock_service_class.return_value = mock_service

        response = await get_cli_insights_weekday_pattern(
            user=mock_user,
            params=AnalyticsQueryParams(time_period="last_30_days", page=0, per_page=50),
        )

        mock_service.get_cli_insights_weekday_pattern.assert_called_once()
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.cli_insights.AnalyticsService")
    async def test_cli_insights_top_spenders_endpoint(
        self, mock_service_class, mock_user, sample_tabular_response_data
    ):
        """Verify CLI insights Top Spenders endpoint returns tabular response."""
        from codemie.rest_api.routers.analytics._common import AnalyticsQueryParams
        from codemie.rest_api.routers.analytics.cli_insights import get_cli_insights_top_spenders

        mock_service = AsyncMock()
        mock_service.get_cli_insights_top_spenders.return_value = sample_tabular_response_data
        mock_service_class.return_value = mock_service

        response = await get_cli_insights_top_spenders(
            user=mock_user,
            params=AnalyticsQueryParams(time_period="last_30_days", page=0, per_page=50),
        )

        mock_service.get_cli_insights_top_spenders.assert_called_once()
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.cli_insights.AnalyticsService")
    async def test_cli_insights_user_detail_endpoint(self, mock_service_class, mock_user):
        """Verify CLI insights user detail endpoint returns detail response."""
        from codemie.rest_api.routers.analytics.cli_insights import get_cli_insights_user_detail

        mock_service = AsyncMock()
        mock_service.get_cli_insights_user_detail.return_value = {
            "data": {"user_name": "Pavlo Chaikivskyi"},
            "metadata": {
                "timestamp": "2026-03-24T10:00:00Z",
                "data_as_of": "2026-03-24T10:00:00Z",
                "filters_applied": {},
                "execution_time_ms": 0.0,
            },
        }
        mock_service_class.return_value = mock_service

        response = await get_cli_insights_user_detail(
            user=mock_user,
            user_name="Pavlo Chaikivskyi",
            time_period="last_30_days",
            start_date=None,
            end_date=None,
            users=None,
            projects=None,
        )

        mock_service.get_cli_insights_user_detail.assert_called_once_with(
            user_name="Pavlo Chaikivskyi",
            user_id=None,
            time_period="last_30_days",
            start_date=None,
            end_date=None,
            users=None,
            projects=None,
        )
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_200_OK


class TestUsersListEndpoint:
    """Tests for the /v1/analytics/users endpoint."""

    @pytest.fixture
    def sample_users_list_response_data(self):
        """Sample data for UsersListResponse."""
        return {
            "data": {
                "users": [{"id": "user1", "name": "User One"}, {"id": "user2", "name": "User Two"}],
                "total_count": 2,
            },
            "metadata": {
                "timestamp": "2025-01-15T10:00:00Z",
                "data_as_of": "2025-01-15T09:55:00Z",
                "filters_applied": {},
                "execution_time_ms": 30.5,
            },
        }

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.activity.AnalyticsService")
    async def test_get_users_list_returns_correct_response(
        self, mock_service_class, mock_user, sample_users_list_response_data
    ):
        """Verify endpoint returns correct response model."""
        # Arrange
        from codemie.rest_api.routers.analytics.activity import get_users_list

        mock_service = AsyncMock()
        mock_service.get_users_list.return_value = sample_users_list_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_users_list(
            user=mock_user, time_period="last_30_days", start_date=None, end_date=None, users=None, projects=None
        )

        # Assert
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_200_OK
        response_body = json.loads(response.body)
        assert "users" in response_body["data"]
        assert response_body["data"]["total_count"] == 2

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.activity.AnalyticsService")
    async def test_get_users_list_passes_filters(self, mock_service_class, mock_user, sample_users_list_response_data):
        """Verify filters are passed to service."""
        # Arrange
        from codemie.rest_api.routers.analytics.activity import get_users_list

        mock_service = AsyncMock()
        mock_service.get_users_list.return_value = sample_users_list_response_data
        mock_service_class.return_value = mock_service

        # Act
        await get_users_list(
            user=mock_user,
            time_period=None,
            start_date=datetime(2025, 1, 1),
            end_date=datetime(2025, 1, 31),
            users="u1,u2",
            projects="p1",
            search=None,
        )

        # Assert
        mock_service.get_users_list.assert_called_once_with(
            time_period=None,
            start_date=datetime(2025, 1, 1),
            end_date=datetime(2025, 1, 31),
            users=["u1", "u2"],
            projects=["p1"],
            search=None,
        )


class TestGetProjectsUniqueDaily:
    """Tests for get_projects_unique_daily endpoint."""

    @pytest.fixture
    def sample_projects_unique_daily_response_data(self):
        """Sample response data for projects unique daily."""
        return {
            "data": {
                "columns": [
                    {"id": "date", "label": "Date", "type": "date"},
                    {"id": "unique_projects", "label": "Unique Projects", "type": "number"},
                ],
                "rows": [
                    {"date": "2026-01-01", "unique_projects": 5},
                    {"date": "2026-01-02", "unique_projects": 8},
                    {"date": "2026-01-03", "unique_projects": 12},
                ],
            },
            "metadata": {
                "timestamp": "2026-01-23T10:00:00Z",
                "data_as_of": "2026-01-23T09:55:00Z",
                "filters_applied": {"time_period": "last_30_days"},
                "execution_time_ms": 45.2,
            },
            "pagination": {"page": 0, "per_page": 20, "total_count": 3, "has_more": False},
        }

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.activity.AnalyticsService")
    async def test_get_projects_unique_daily_success(
        self, mock_service_class, mock_user, sample_projects_unique_daily_response_data
    ):
        """Verify successful response with default parameters."""
        # Arrange
        from codemie.rest_api.routers.analytics.activity import get_projects_unique_daily

        mock_service = AsyncMock()
        mock_service.get_projects_unique_daily.return_value = sample_projects_unique_daily_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_projects_unique_daily(
            user=mock_user, time_period="last_30_days", start_date=None, end_date=None, users=None, projects=None
        )

        # Assert
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_200_OK
        response_body = json.loads(response.body)
        assert len(response_body["data"]["rows"]) == 3
        assert response_body["data"]["columns"][1]["id"] == "unique_projects"

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.activity.AnalyticsService")
    async def test_get_projects_unique_daily_with_date_range(
        self, mock_service_class, mock_user, sample_projects_unique_daily_response_data
    ):
        """Verify custom date range parameters are passed correctly."""
        # Arrange
        from codemie.rest_api.routers.analytics.activity import get_projects_unique_daily

        mock_service = AsyncMock()
        mock_service.get_projects_unique_daily.return_value = sample_projects_unique_daily_response_data
        mock_service_class.return_value = mock_service

        start = datetime(2026, 1, 1, 0, 0, 0)
        end = datetime(2026, 1, 31, 23, 59, 59)

        # Act
        await get_projects_unique_daily(
            user=mock_user,
            time_period=None,
            start_date=start,
            end_date=end,
            users=None,
            projects=None,
        )

        # Assert
        mock_service.get_projects_unique_daily.assert_called_once_with(
            None, start.replace(tzinfo=timezone.utc), end.replace(tzinfo=timezone.utc), None, None
        )

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.activity.AnalyticsService")
    async def test_get_projects_unique_daily_with_filters(
        self, mock_service_class, mock_user, sample_projects_unique_daily_response_data
    ):
        """Verify user and project filters are parsed from CSV strings."""
        # Arrange
        from codemie.rest_api.routers.analytics.activity import get_projects_unique_daily

        mock_service = AsyncMock()
        mock_service.get_projects_unique_daily.return_value = sample_projects_unique_daily_response_data
        mock_service_class.return_value = mock_service

        # Act
        await get_projects_unique_daily(
            user=mock_user,
            time_period="last_7_days",
            start_date=None,
            end_date=None,
            users="user1@example.com,user2@example.com",
            projects="project1,project2",
        )

        # Assert
        mock_service.get_projects_unique_daily.assert_called_once_with(
            "last_7_days", None, None, ["user1@example.com", "user2@example.com"], ["project1", "project2"]
        )

    @pytest.mark.asyncio
    async def test_get_projects_unique_daily_validates_date_order(self, mock_user):
        """Verify start_date must be before end_date."""
        # Arrange
        from codemie.rest_api.routers.analytics.activity import get_projects_unique_daily

        start = datetime(2026, 1, 31, 0, 0, 0)
        end = datetime(2026, 1, 1, 0, 0, 0)  # End before start (invalid)

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await get_projects_unique_daily(
                user=mock_user,
                time_period=None,
                start_date=start,
                end_date=end,
                users=None,
                projects=None,
            )

        # Verify it's a 400 (not masked as a 500 by the generic error handler)
        assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
        assert "start_date" in exc_info.value.details.lower() or "end_date" in exc_info.value.details.lower()

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.activity.AnalyticsService")
    async def test_get_projects_unique_daily_normalizes_naive_non_reversed_dates(
        self, mock_service_class, mock_user, sample_projects_unique_daily_response_data
    ):
        """Naive, non-reversed start_date/end_date are normalized to UTC, not rejected or 500'd."""
        # Arrange
        from codemie.rest_api.routers.analytics.activity import get_projects_unique_daily

        mock_service = AsyncMock()
        mock_service.get_projects_unique_daily.return_value = sample_projects_unique_daily_response_data
        mock_service_class.return_value = mock_service

        start = datetime(2026, 1, 1)
        end = datetime(2026, 1, 2)

        # Act
        response = await get_projects_unique_daily(
            user=mock_user,
            time_period=None,
            start_date=start,
            end_date=end,
            users=None,
            projects=None,
        )

        # Assert
        call_args = mock_service.get_projects_unique_daily.call_args
        assert call_args.args[1].tzinfo == timezone.utc
        assert call_args.args[2].tzinfo == timezone.utc
        assert response.status_code == status.HTTP_200_OK

    def test_get_projects_unique_daily_malformed_date_string_returns_422(self, mock_user):
        """An unparseable date string on this endpoint's query params still 422s via FastAPI coercion."""
        # Arrange
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from codemie.rest_api.routers.analytics.activity import router
        from codemie.rest_api.security.authentication import authenticate

        app = FastAPI()
        app.include_router(router, prefix="/v1/analytics")
        app.dependency_overrides[authenticate] = lambda: mock_user
        client = TestClient(app)

        # Act
        response = client.get("/v1/analytics/projects-unique-daily", params={"start_date": "not-a-date"})

        # Assert
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.activity.AnalyticsService")
    async def test_get_projects_unique_daily_handles_empty_result(self, mock_service_class, mock_user):
        """Verify handling of empty result set."""
        # Arrange
        from codemie.rest_api.routers.analytics.activity import get_projects_unique_daily

        mock_service = AsyncMock()
        empty_response = {
            "data": {
                "columns": [
                    {"id": "date", "label": "Date", "type": "date"},
                    {"id": "unique_projects", "label": "Unique Projects", "type": "number"},
                ],
                "rows": [],
            },
            "metadata": {
                "timestamp": "2026-01-23T10:00:00Z",
                "data_as_of": "2026-01-23T09:55:00Z",
                "filters_applied": {},
                "execution_time_ms": 10.0,
            },
            "pagination": {"page": 0, "per_page": 20, "total_count": 0, "has_more": False},
        }
        mock_service.get_projects_unique_daily.return_value = empty_response
        mock_service_class.return_value = mock_service

        # Act
        response = await get_projects_unique_daily(
            user=mock_user, time_period="last_30_days", start_date=None, end_date=None, users=None, projects=None
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        response_body = json.loads(response.body)
        assert len(response_body["data"]["rows"]) == 0
        assert response_body["pagination"]["total_count"] == 0

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.activity.AnalyticsService")
    async def test_get_projects_unique_daily_response_format(
        self, mock_service_class, mock_user, sample_projects_unique_daily_response_data
    ):
        """Verify response format matches TabularResponse model."""
        # Arrange
        from codemie.rest_api.routers.analytics.activity import get_projects_unique_daily

        mock_service = AsyncMock()
        mock_service.get_projects_unique_daily.return_value = sample_projects_unique_daily_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_projects_unique_daily(
            user=mock_user, time_period="last_30_days", start_date=None, end_date=None, users=None, projects=None
        )

        # Assert
        response_body = json.loads(response.body)
        # Verify top-level structure
        assert "data" in response_body
        assert "metadata" in response_body
        assert "pagination" in response_body
        # Verify data structure
        assert "columns" in response_body["data"]
        assert "rows" in response_body["data"]
        # Verify columns
        assert len(response_body["data"]["columns"]) == 2
        assert response_body["data"]["columns"][0]["id"] == "date"
        assert response_body["data"]["columns"][1]["id"] == "unique_projects"
        # Verify rows
        for row in response_body["data"]["rows"]:
            assert "date" in row
            assert "unique_projects" in row


# ============================================================================
# Tests for /analytics/spending endpoint
# ============================================================================


class TestGetUserSpending:
    """Tests for GET /analytics/spending endpoint.

    The three currency metrics take their money from the spend port; the LiteLLM dicts these tests
    mock now supply budget **policy** only - the limit and the reset window the period is derived
    from. So a test that wants a figure states it as a `SpendResult`, and a test that wants *no*
    figure simply omits the category from the port's answer.
    """

    @staticmethod
    def _spend_result(amount: str | None = None, **kwargs):
        """A port answer for one category. No amount means no figure, which is never a zero."""
        from decimal import Decimal

        from codemie.service.spend.coverage import SpendResult, TimeRange

        period = TimeRange(
            datetime(2026, 2, 1, tzinfo=timezone.utc),
            datetime(2026, 3, 1, tzinfo=timezone.utc),
        )
        if amount is None:
            return SpendResult.no_figure(period)
        return SpendResult(figure=Decimal(amount), **kwargs)

    @staticmethod
    def _patch_port(consumed):
        """Answer the endpoint's single port read with `consumed`, keyed by BudgetCategory."""
        return patch(
            "codemie.rest_api.routers.analytics.spending._consumed_per_budget_category",
            new=AsyncMock(return_value=consumed),
        )

    @pytest.mark.asyncio
    async def test_get_user_spending_success(self, mock_user):
        """Test successful spending data retrieval."""
        from codemie.rest_api.routers.analytics.spending import get_user_spending
        from codemie.service.budget.budget_enums import BudgetCategory

        # Budget policy from LiteLLM. `total_spend` is deliberately absent: the endpoint must not
        # read a figure from it, and a dict still carrying one would hide a regression that did.
        mock_spending_data = {
            "customer_id": "testuser",
            "max_budget": 100.0,
            "budget_duration": "30d",
            "budget_reset_at": "2026-03-01T00:00:00Z",
        }

        with (
            patch("codemie.enterprise.litellm.dependencies.get_customer_spending") as mock_get_spending,
            patch("codemie.enterprise.litellm.dependencies.get_proxy_customer_spending", return_value=None),
            patch("codemie.enterprise.litellm.dependencies.get_premium_customer_spending", return_value=None),
            patch("codemie.enterprise.litellm.dependencies.is_premium_models_enabled", return_value=False),
            patch("codemie.enterprise.litellm.dependencies.is_proxy_budget_enabled", return_value=False),
            self._patch_port({BudgetCategory.PLATFORM: self._spend_result("15.50")}),
        ):
            mock_get_spending.return_value = mock_spending_data

            response = await get_user_spending(user=mock_user)

            # Verify response structure
            assert isinstance(response, JSONResponse)
            response_data = json.loads(response.body)

            # Verify data structure (metrics list format)
            assert "data" in response_data
            assert "metrics" in response_data["data"]
            metrics = response_data["data"]["metrics"]
            assert isinstance(metrics, list)
            assert len(metrics) == 4

            # Verify metrics by ID
            metrics_dict = {m["id"]: m for m in metrics}

            assert metrics_dict["current_spending"]["value"] == 15.50
            assert metrics_dict["current_spending"]["type"] == "number"
            assert metrics_dict["current_spending"]["format"] == "currency"
            # An exact figure still says so explicitly: the annotation travels with every money
            # metric, so a screen never has to guess whether a bare number was qualified.
            assert metrics_dict["current_spending"]["spend_unavailable"] is False
            assert metrics_dict["current_spending"]["spend_partial"] is False
            assert metrics_dict["current_spending"]["spend_missing_amount"] is None

            # A metric that carries no annotation carries exactly the keys it always did - the
            # `extra="allow"` passthrough must not add `null`s across every analytics response.
            assert set(metrics_dict["budget_limit"]) == set(Metric.model_fields)

            assert metrics_dict["budget_limit"]["value"] == 100.0
            assert metrics_dict["budget_limit"]["type"] == "number"
            assert metrics_dict["budget_limit"]["format"] == "currency"

            assert metrics_dict["budget_reset_at"]["value"] == "2026-03-01T00:00:00Z"
            assert metrics_dict["budget_reset_at"]["type"] == "date"

            assert "time_until_reset" in metrics_dict
            assert metrics_dict["time_until_reset"]["type"] == "string"

            # Verify metadata
            assert "metadata" in response_data
            assert "timestamp" in response_data["metadata"]
            assert "execution_time_ms" in response_data["metadata"]
            assert response_data["metadata"]["filters_applied"] == {}  # No filters for this endpoint

    @pytest.mark.asyncio
    async def test_get_user_spending_no_data(self, mock_user):
        """No budget **policy** and no figure: the money reads absent, the policy fields read N/A."""
        from codemie.rest_api.routers.analytics.spending import get_user_spending

        with (
            patch("codemie.enterprise.litellm.dependencies.get_customer_spending") as mock_get_spending,
            patch("codemie.enterprise.litellm.dependencies.is_premium_models_enabled", return_value=False),
            patch("codemie.enterprise.litellm.dependencies.is_proxy_budget_enabled", return_value=False),
            self._patch_port({}),
        ):
            mock_get_spending.return_value = None

            # Should return 200 OK with N/A values, not 404
            response = await get_user_spending(user=mock_user)

            assert response.status_code == status.HTTP_200_OK
            response_data = json.loads(response.body)

            # Verify metrics structure
            assert "data" in response_data
            assert "metrics" in response_data["data"]
            metrics = response_data["data"]["metrics"]

            # Should have 4 metrics
            assert len(metrics) == 4

            # Find each metric by id
            metrics_by_id = {m["id"]: m for m in metrics}

            # Current spending is UNAVAILABLE, not zero. The port produced no figure, and a zero in
            # a currency slot would say the user spent nothing - a different claim, and one a caller
            # can read straight off the screen as a fact.
            assert metrics_by_id["current_spending"]["value"] is None
            assert metrics_by_id["current_spending"]["type"] == "number"
            assert metrics_by_id["current_spending"]["spend_unavailable"] is True
            assert metrics_by_id["current_spending"]["spend_missing_amount"] is None

            # No fabricated zero survives anywhere in the payload, in any spelling.
            assert "0.0" not in json.dumps(response_data["data"])

            # Other fields should be N/A
            assert metrics_by_id["budget_limit"]["value"] == "N/A"
            assert metrics_by_id["budget_limit"]["type"] == "string"
            assert metrics_by_id["budget_reset_at"]["value"] == "N/A"
            assert metrics_by_id["budget_reset_at"]["type"] == "string"
            assert metrics_by_id["time_until_reset"]["value"] == "N/A"
            assert metrics_by_id["time_until_reset"]["type"] == "string"

    @pytest.mark.asyncio
    async def test_get_user_spending_calculates_time_correctly(self, mock_user):
        """Test that time until reset is calculated correctly."""
        from codemie.rest_api.routers.analytics.spending import get_user_spending
        from datetime import datetime, timedelta, timezone

        # Set reset date to 10 days from now
        future_date = datetime.now(timezone.utc) + timedelta(days=10)
        reset_at = future_date.isoformat()

        mock_spending_data = {
            "customer_id": "testuser",
            "max_budget": 50.0,
            "budget_duration": "30d",
            "budget_reset_at": reset_at,
        }

        with (
            patch("codemie.enterprise.litellm.dependencies.get_customer_spending") as mock_get_spending,
            patch("codemie.enterprise.litellm.dependencies.is_premium_models_enabled", return_value=False),
            patch("codemie.enterprise.litellm.dependencies.is_proxy_budget_enabled", return_value=False),
            self._patch_port({}),
        ):
            mock_get_spending.return_value = mock_spending_data

            response = await get_user_spending(user=mock_user)
            response_data = json.loads(response.body)

            # Find time_until_reset metric
            metrics = response_data["data"]["metrics"]
            metrics_dict = {m["id"]: m for m in metrics}

            # Should contain days, hours, and minutes format
            time_value = metrics_dict["time_until_reset"]["value"]
            assert isinstance(time_value, str)
            assert "days" in time_value
            assert "hours" in time_value
            assert "mins" in time_value

    @pytest.mark.asyncio
    async def test_get_user_spending_handles_null_reset_date(self, mock_user):
        """Test spending endpoint when reset date is null."""
        from codemie.rest_api.routers.analytics.spending import get_user_spending

        mock_spending_data = {
            "customer_id": "testuser",
            "max_budget": 50.0,
            "budget_duration": "30d",
            "budget_reset_at": None,
        }

        with (
            patch("codemie.enterprise.litellm.dependencies.get_customer_spending") as mock_get_spending,
            patch("codemie.enterprise.litellm.dependencies.is_premium_models_enabled", return_value=False),
            patch("codemie.enterprise.litellm.dependencies.is_proxy_budget_enabled", return_value=False),
            self._patch_port({}),
        ):
            mock_get_spending.return_value = mock_spending_data

            response = await get_user_spending(user=mock_user)
            response_data = json.loads(response.body)

            # Find budget_reset_at and time_until_reset metrics
            metrics = response_data["data"]["metrics"]
            metrics_dict = {m["id"]: m for m in metrics}

            # budget_reset_at and time_until_reset should be "N/A" when null
            assert metrics_dict["budget_reset_at"]["value"] == "N/A"
            assert metrics_dict["time_until_reset"]["value"] == "N/A"

    @pytest.mark.asyncio
    async def test_measured_zero_renders_as_zero_not_as_absent(self, mock_user):
        """A measured zero is a fact - `Decimal('0')` reaches the wire as 0, unqualified."""
        from codemie.rest_api.routers.analytics.spending import get_user_spending
        from codemie.service.budget.budget_enums import BudgetCategory

        policy = {"customer_id": "testuser", "max_budget": 100.0, "budget_duration": "30d", "budget_reset_at": None}

        with (
            patch("codemie.enterprise.litellm.dependencies.get_customer_spending", return_value=policy),
            patch("codemie.enterprise.litellm.dependencies.is_premium_models_enabled", return_value=False),
            patch("codemie.enterprise.litellm.dependencies.is_proxy_budget_enabled", return_value=False),
            self._patch_port({BudgetCategory.PLATFORM: self._spend_result("0")}),
        ):
            response = await get_user_spending(user=mock_user)

        metric = {m["id"]: m for m in json.loads(response.body)["data"]["metrics"]}["current_spending"]
        assert metric["value"] == 0
        assert metric["value"] is not None
        assert metric["spend_unavailable"] is False
        assert metric["spend_partial"] is False

    @pytest.mark.asyncio
    async def test_partial_figure_renders_its_number_and_says_it_is_partial(self, mock_user):
        """A figure incomplete in one dimension is a real number - and never a bare one."""
        from decimal import Decimal

        from codemie.rest_api.routers.analytics.spending import get_user_spending
        from codemie.service.budget.budget_enums import BudgetCategory
        from codemie.service.spend.coverage import Portion, SpendDimension

        policy = {"customer_id": "testuser", "max_budget": 100.0, "budget_duration": "30d", "budget_reset_at": None}
        partial = self._spend_result("7.25", partial={SpendDimension.WORKFLOW: Portion(spend=Decimal("2.00"), rows=3)})

        with (
            patch("codemie.enterprise.litellm.dependencies.get_customer_spending", return_value=policy),
            patch("codemie.enterprise.litellm.dependencies.is_premium_models_enabled", return_value=False),
            patch("codemie.enterprise.litellm.dependencies.is_proxy_budget_enabled", return_value=False),
            self._patch_port({BudgetCategory.PLATFORM: partial}),
        ):
            response = await get_user_spending(user=mock_user)

        metric = {m["id"]: m for m in json.loads(response.body)["data"]["metrics"]}["current_spending"]
        assert metric["value"] == 7.25
        assert metric["spend_partial"] is True
        assert metric["spend_missing_amount"] == 2.00

    @pytest.mark.asyncio
    async def test_unconfigured_cli_budget_omits_the_metric(self, mock_user):
        """*No such budget* is a different answer from *unknown figure*, and only it says nothing."""
        from codemie.rest_api.routers.analytics.spending import get_user_spending

        policy = {"customer_id": "testuser", "max_budget": 100.0, "budget_duration": "30d", "budget_reset_at": None}

        with (
            patch("codemie.enterprise.litellm.dependencies.get_customer_spending", return_value=policy),
            patch("codemie.enterprise.litellm.dependencies.get_proxy_customer_spending") as mock_proxy,
            patch("codemie.enterprise.litellm.dependencies.get_premium_customer_spending") as mock_premium,
            patch("codemie.enterprise.litellm.dependencies.is_premium_models_enabled", return_value=False),
            patch("codemie.enterprise.litellm.dependencies.is_proxy_budget_enabled", return_value=False),
            self._patch_port({}),
        ):
            response = await get_user_spending(user=mock_user)

        metric_ids = {m["id"] for m in json.loads(response.body)["data"]["metrics"]}
        assert "cli_current_spending" not in metric_ids
        assert "premium_current_spending" not in metric_ids
        # An unconfigured budget is not even looked up: there is nothing to look up.
        mock_proxy.assert_not_called()
        mock_premium.assert_not_called()

    @pytest.mark.asyncio
    async def test_configured_cli_budget_keeps_its_metric_when_the_policy_read_fails(self, mock_user):
        """A provider hiccup costs the reset window, not the metric - the figure reads absent."""
        from codemie.rest_api.routers.analytics.spending import get_user_spending

        policy = {"customer_id": "testuser", "max_budget": 100.0, "budget_duration": "30d", "budget_reset_at": None}

        with (
            patch("codemie.enterprise.litellm.dependencies.get_customer_spending", return_value=policy),
            patch(
                "codemie.enterprise.litellm.dependencies.get_proxy_customer_spending",
                side_effect=RuntimeError("proxy budget lookup exploded"),
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.get_premium_customer_spending",
                side_effect=RuntimeError("premium budget lookup exploded"),
            ),
            patch("codemie.enterprise.litellm.dependencies.is_premium_models_enabled", return_value=True),
            patch("codemie.enterprise.litellm.dependencies.is_proxy_budget_enabled", return_value=True),
            self._patch_port({}),
        ):
            response = await get_user_spending(user=mock_user)

        metrics = {m["id"]: m for m in json.loads(response.body)["data"]["metrics"]}
        for metric_id in ("cli_current_spending", "premium_current_spending"):
            assert metric_id in metrics, f"a configured budget keeps its metric, {metric_id} was dropped"
            assert metrics[metric_id]["value"] is None
            assert metrics[metric_id]["spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_annotation_keys_survive_the_response_model(self, mock_user):
        """The keys must outlive `SummariesResponse` validation and `model_dump(by_alias=True)`.

        This is the assertion that catches a plain `BaseModel` silently dropping them, which would
        put a qualified figure on the wire looking exactly like an exact one.
        """
        from decimal import Decimal

        from codemie.rest_api.routers.analytics.spending import get_user_spending
        from codemie.service.budget.budget_enums import BudgetCategory
        from codemie.service.spend.coverage import Portion, SpendDimension

        policy = {"customer_id": "testuser", "max_budget": 100.0, "budget_duration": "30d", "budget_reset_at": None}
        consumed = {
            BudgetCategory.PLATFORM: self._spend_result(
                "3.00", partial={SpendDimension.IDENTITY_BRIDGE: Portion(spend=None, rows=None)}
            ),
            BudgetCategory.CLI: self._spend_result("1.25"),
        }

        with (
            patch("codemie.enterprise.litellm.dependencies.get_customer_spending", return_value=policy),
            patch("codemie.enterprise.litellm.dependencies.get_proxy_customer_spending", return_value=policy),
            patch("codemie.enterprise.litellm.dependencies.is_premium_models_enabled", return_value=False),
            patch("codemie.enterprise.litellm.dependencies.is_proxy_budget_enabled", return_value=True),
            self._patch_port(consumed),
        ):
            response = await get_user_spending(user=mock_user)

        metrics = {m["id"]: m for m in json.loads(response.body)["data"]["metrics"]}
        assert metrics["current_spending"]["value"] == 3.00
        assert metrics["current_spending"]["spend_partial"] is True
        # An unresolvable alias set means an *unknown* amount is missing, never a zero.
        assert metrics["current_spending"]["spend_missing_amount"] is None
        assert metrics["cli_current_spending"]["value"] == 1.25
        assert metrics["cli_current_spending"]["spend_unavailable"] is False
        assert Decimal("1.25") == Decimal(str(metrics["cli_current_spending"]["value"]))


class TestConsumedPerBudgetCategory:
    """Tests for the single spend-port read behind GET /analytics/spending."""

    @pytest.fixture(autouse=True)
    def assigned_budgets(self):
        """The user's assigned budgets, which carry no record of a reset CodeMie set unless a test adds one."""
        with patch(
            "codemie.rest_api.routers.analytics.spending._assigned_budgets", new=AsyncMock(return_value={})
        ) as assigned:
            yield assigned

    @staticmethod
    def _patch_session():
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
        ctx.__aexit__ = AsyncMock(return_value=False)
        return patch("codemie.rest_api.routers.analytics.spending.get_async_session", return_value=ctx)

    @staticmethod
    def _patch_identity(aliases):
        return patch(
            "codemie.rest_api.routers.analytics.spending.resolve_identity",
            new=AsyncMock(return_value=MagicMock(aliases=aliases)),
        )

    @pytest.mark.asyncio
    async def test_categories_sharing_a_reset_schedule_cost_one_round_trip(self):
        """Grouping is by period, not by category: one reset schedule is one call."""
        from decimal import Decimal

        from codemie.rest_api.routers.analytics.spending import _consumed_per_budget_category
        from codemie.service.budget.budget_enums import BudgetCategory
        from codemie.service.spend.coverage import SpendResult
        from codemie.service.spend.spend_service import spend_service

        policy = {"budget_duration": "30d", "budget_reset_at": "2099-03-01T00:00:00Z"}
        calls = []

        async def answer(holders, period):
            calls.append((tuple(holders), period))
            return {holder: SpendResult(figure=Decimal("2.00")) for holder in holders}

        with (
            self._patch_session(),
            self._patch_identity(frozenset({"testuser", "testuser@example.com"})),
            patch.object(spend_service, "consumed_for_holders", new=AsyncMock(side_effect=answer)),
        ):
            consumed = await _consumed_per_budget_category(
                "user123@example.com",
                {BudgetCategory.PLATFORM: policy, BudgetCategory.CLI: policy},
            )

        assert len(calls) == 1, "two categories on one reset schedule must not cost two round trips"
        holders, period = calls[0]
        assert {holder.category for holder in holders} == {BudgetCategory.PLATFORM, BudgetCategory.CLI}
        # The period is the budget's own, derived from the reset the response displays.
        assert period.start == datetime(2099, 2, 1, tzinfo=timezone.utc)
        assert period.end == datetime(2099, 3, 1, tzinfo=timezone.utc)
        assert set(consumed) == {BudgetCategory.PLATFORM, BudgetCategory.CLI}

    @pytest.mark.asyncio
    async def test_a_category_with_no_derivable_period_is_not_queried(self):
        """No period means no figure - never a guessed window, and never a zero."""
        from decimal import Decimal

        from codemie.rest_api.routers.analytics.spending import _consumed_per_budget_category
        from codemie.service.budget.budget_enums import BudgetCategory
        from codemie.service.spend.coverage import SpendResult
        from codemie.service.spend.spend_service import spend_service

        async def answer(holders, period):
            return {holder: SpendResult(figure=Decimal("2.00")) for holder in holders}

        with (
            self._patch_session(),
            self._patch_identity(frozenset()),
            patch.object(spend_service, "consumed_for_holders", new=AsyncMock(side_effect=answer)),
        ):
            consumed = await _consumed_per_budget_category(
                "user123@example.com",
                {
                    # No reset instant, so no period; and no policy dict at all for premium.
                    BudgetCategory.PLATFORM: {"budget_duration": "30d", "budget_reset_at": None},
                    BudgetCategory.CLI: {"budget_duration": "30d", "budget_reset_at": "2099-03-01T00:00:00Z"},
                    BudgetCategory.PREMIUM_MODELS: None,
                },
            )

        assert set(consumed) == {BudgetCategory.CLI}

    @pytest.mark.asyncio
    async def test_nothing_askable_costs_neither_a_session_nor_an_identity_lookup(self):
        """With no derivable period anywhere there is no holder, so there is nothing to resolve."""
        from codemie.rest_api.routers.analytics.spending import _consumed_per_budget_category
        from codemie.service.budget.budget_enums import BudgetCategory
        from codemie.service.spend.spend_service import spend_service

        session_patch = self._patch_session()
        identity = AsyncMock()

        with (
            session_patch as mock_session,
            patch("codemie.rest_api.routers.analytics.spending.resolve_identity", new=identity),
            patch.object(spend_service, "consumed_for_holders", new=AsyncMock()) as mock_port,
        ):
            consumed = await _consumed_per_budget_category(
                "user123@example.com",
                {BudgetCategory.PLATFORM: {"budget_duration": "30d", "budget_reset_at": None}},
            )

        assert consumed == {}
        mock_session.assert_not_called()
        identity.assert_not_awaited()
        mock_port.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_reset_codemie_set_starts_the_period_where_it_was_recorded(self, assigned_budgets):
        """The provider's policy names the reset; only the assigned budget records where its period began.

        EPMCDME-15204: a budget created on 2099-02-09 first resets on 03-12 00:00, prolonging the
        February period; `reset - duration` would start it on 02-10, after its first day.
        """
        from codemie.rest_api.routers.analytics.spending import _AssignedBudget, _consumed_per_budget_category
        from codemie.service.budget.budget_enums import BudgetCategory
        from codemie.service.spend.spend_service import spend_service

        february = datetime(2099, 2, 1, tzinfo=timezone.utc)
        assigned_budgets.return_value = {
            "platform": _AssignedBudget(
                SimpleNamespace(
                    pinned_reset_at=datetime(2099, 3, 12, tzinfo=timezone.utc), pinned_period_start=february
                ),
                None,
            )
        }

        with (
            self._patch_session(),
            self._patch_identity(frozenset()),
            patch.object(spend_service, "consumed_for_holders", new=AsyncMock(return_value={})) as port,
        ):
            await _consumed_per_budget_category(
                "user123@example.com",
                {BudgetCategory.PLATFORM: {"budget_duration": "30d", "budget_reset_at": "2099-03-12T00:00:00Z"}},
            )

        _, period = port.await_args.args
        assert (period.start, period.end) == (february, datetime(2099, 3, 12, tzinfo=timezone.utc))

    @pytest.mark.asyncio
    async def test_an_unresolvable_alias_set_reaches_the_holder_as_none(self):
        """`None` says the person's fallback rows cannot be identified; an empty set says the opposite."""
        from decimal import Decimal

        from codemie.rest_api.routers.analytics.spending import _consumed_per_budget_category
        from codemie.service.budget.budget_enums import BudgetCategory
        from codemie.service.spend.coverage import SpendResult
        from codemie.service.spend.spend_service import spend_service

        seen = []

        async def answer(holders, period):
            seen.extend(holders)
            return {holder: SpendResult(figure=Decimal("2.00")) for holder in holders}

        with (
            self._patch_session(),
            self._patch_identity(None),
            patch.object(spend_service, "consumed_for_holders", new=AsyncMock(side_effect=answer)),
        ):
            await _consumed_per_budget_category(
                "user123@example.com",
                {BudgetCategory.PLATFORM: {"budget_duration": "30d", "budget_reset_at": "2099-03-01T00:00:00Z"}},
            )

        assert [holder.aliases for holder in seen] == [None]
        assert [holder.user_id for holder in seen] == ["user123@example.com"]


@pytest.mark.asyncio
async def test_assigned_budgets_are_keyed_by_category_and_a_missing_budget_is_left_out():
    from codemie.rest_api.routers.analytics.spending import _AssignedBudget, _assigned_budgets

    platform = SimpleNamespace(budget_id="b-platform")
    reset = datetime(2099, 6, 14, 9, 30, tzinfo=timezone.utc)
    assignments = [
        SimpleNamespace(category="platform", budget_id="b-platform", spend_reset_at=reset),
        SimpleNamespace(category="cli", budget_id="b-deleted", spend_reset_at=None),
    ]
    session = AsyncMock()
    with (
        patch(
            "codemie.rest_api.routers.analytics.spending.budget_repository.get_user_category_assignments",
            new=AsyncMock(return_value=assignments),
        ) as get_assignments,
        patch(
            "codemie.rest_api.routers.analytics.spending.budget_repository.get_by_ids",
            new=AsyncMock(return_value={"b-platform": platform}),
        ) as get_by_ids,
    ):
        assert await _assigned_budgets(session, "user-1") == {"platform": _AssignedBudget(platform, reset)}

    get_assignments.assert_awaited_once_with(session, "user-1")
    get_by_ids.assert_awaited_once_with(session, ["b-platform", "b-deleted"])


class TestGetUserKeySpending:
    """Tests for GET /analytics/budget_usage endpoint."""

    @pytest.mark.asyncio
    async def test_returns_grouped_key_spending_success(self, mock_user):
        """Test endpoint returns tabular spending with personal budget rows and member allocation rows."""
        from codemie.rest_api.routers.analytics.spending import get_user_budget_usage
        from codemie.service.analytics.handlers.budget_usage_service import _get_key_spending_columns

        mock_rows = [
            {
                "project_name": mock_user.email,
                "current_spending": 15.5,
                "budget_reset_at": "2026-04-01T00:00:00Z",
                "time_until_reset": None,
                "budget_limit": 100.0,
                "total": 15.5,
            },
            {
                "project_name": f"{mock_user.email} (premium)",
                "current_spending": 1.25,
                "budget_reset_at": "2026-04-02T00:00:00Z",
                "time_until_reset": None,
                "budget_limit": 5.0,
                "total": 25.0,
            },
            {
                "project_name": f"{mock_user.email} (cli)",
                "current_spending": 3.75,
                "budget_reset_at": "2026-04-03T00:00:00Z",
                "time_until_reset": None,
                "budget_limit": 20.0,
                "total": 18.75,
            },
            {
                "project_name": "demo",
                "current_spending": 25.0,
                "budget_reset_at": "2026-04-01T00:00:00Z",
                "time_until_reset": None,
                "budget_limit": 50.0,
                "total": 50.0,
            },
        ]

        with patch(
            "codemie.service.analytics.handlers.budget_usage_service.BudgetUsageService.get_budget_usage",
            new_callable=AsyncMock,
            return_value=(_get_key_spending_columns(), mock_rows),
        ):
            response = await get_user_budget_usage(user=mock_user, user_id=None)

            assert "data" in response
            assert "columns" in response["data"]
            assert "rows" in response["data"]
            assert "metadata" in response

            rows = response["data"]["rows"]
            assert rows[0]["project_name"] == mock_user.email
            assert rows[0]["current_spending"] == 15.5
            assert rows[1]["project_name"] == f"{mock_user.email} (premium)"
            assert rows[1]["current_spending"] == 1.25
            assert rows[2]["project_name"] == f"{mock_user.email} (cli)"
            assert rows[2]["current_spending"] == 3.75
            assert rows[3]["project_name"] == "demo"
            assert rows[3]["current_spending"] == 25.0
            assert len(rows) == 4

    @pytest.mark.asyncio
    async def test_returns_single_budget_row(self, mock_user):
        """Test endpoint returns a single platform budget row when service returns one row."""
        from codemie.rest_api.routers.analytics.spending import get_user_budget_usage
        from codemie.service.analytics.handlers.budget_usage_service import _get_key_spending_columns

        mock_rows = [
            {
                "project_name": mock_user.email,
                "current_spending": 15.5,
                "budget_reset_at": "2026-04-01T00:00:00Z",
                "time_until_reset": None,
                "budget_limit": 100.0,
                "total": 15.5,
            },
        ]

        with patch(
            "codemie.service.analytics.handlers.budget_usage_service.BudgetUsageService.get_budget_usage",
            new_callable=AsyncMock,
            return_value=(_get_key_spending_columns(), mock_rows),
        ):
            response = await get_user_budget_usage(user=mock_user, user_id=None)

            rows = response["data"]["rows"]
            assert len(rows) == 1
            assert rows[0]["project_name"] == mock_user.email

    @pytest.mark.asyncio
    async def test_handles_empty_budget_usage(self, mock_user):
        """Test endpoint returns empty rows when service returns no rows."""
        from codemie.rest_api.routers.analytics.spending import get_user_budget_usage
        from codemie.service.analytics.handlers.budget_usage_service import _get_key_spending_columns

        with patch(
            "codemie.service.analytics.handlers.budget_usage_service.BudgetUsageService.get_budget_usage",
            new_callable=AsyncMock,
            return_value=(_get_key_spending_columns(), []),
        ):
            response = await get_user_budget_usage(user=mock_user, user_id=None)

            assert response["data"]["rows"] == []
            assert "columns" in response["data"]

    @pytest.mark.asyncio
    async def test_raises_500_on_backend_error(self, mock_user):
        """Test endpoint returns 500 error when the service raises an unexpected exception."""
        from codemie.rest_api.routers.analytics.spending import get_user_budget_usage

        with patch(
            "codemie.service.analytics.handlers.budget_usage_service.BudgetUsageService.get_budget_usage",
            new_callable=AsyncMock,
            side_effect=Exception("Backend service unavailable"),
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await get_user_budget_usage(user=mock_user, user_id=None)

            exception = exc_info.value
            assert exception.code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Unable to retrieve spending information" in exception.message

    @pytest.mark.asyncio
    async def test_handles_none_budget_reset_at(self, mock_user):
        """Test endpoint passes through None budget_reset_at and time_until_reset from service."""
        from codemie.rest_api.routers.analytics.spending import get_user_budget_usage
        from codemie.service.analytics.handlers.budget_usage_service import _get_key_spending_columns

        mock_rows = [
            {
                "project_name": "demo",
                "current_spending": 10.0,
                "budget_reset_at": None,
                "time_until_reset": None,
                "budget_limit": 100.0,
                "total": 10.0,
            },
        ]

        with patch(
            "codemie.service.analytics.handlers.budget_usage_service.BudgetUsageService.get_budget_usage",
            new_callable=AsyncMock,
            return_value=(_get_key_spending_columns(), mock_rows),
        ):
            response = await get_user_budget_usage(user=mock_user, user_id=None)

            rows = response["data"]["rows"]
            assert len(rows) == 1
            assert rows[0]["budget_reset_at"] is None
            assert rows[0]["time_until_reset"] is None

    @pytest.mark.asyncio
    async def test_response_metadata_contains_required_fields(self, mock_user):
        """Test that response metadata contains all required fields."""
        from codemie.rest_api.routers.analytics.spending import get_user_budget_usage
        from codemie.service.analytics.handlers.budget_usage_service import _get_key_spending_columns

        with patch(
            "codemie.service.analytics.handlers.budget_usage_service.BudgetUsageService.get_budget_usage",
            new_callable=AsyncMock,
            return_value=(_get_key_spending_columns(), []),
        ):
            response = await get_user_budget_usage(user=mock_user, user_id=None)

            metadata = response["metadata"]
            assert "timestamp" in metadata
            assert "data_as_of" in metadata
            assert "filters_applied" in metadata
            assert "execution_time_ms" in metadata
            assert isinstance(metadata["execution_time_ms"], (int, float))
            assert metadata["execution_time_ms"] >= 0

    @pytest.mark.asyncio
    async def test_user_id_returns_400_when_user_management_disabled(self, mock_user):
        """user_id parameter requires ENABLE_USER_MANAGEMENT=True."""
        from codemie.rest_api.routers.analytics.spending import get_user_budget_usage

        with patch.object(config, "ENABLE_USER_MANAGEMENT", False):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await get_user_budget_usage(user=mock_user, user_id="some-user-id")

        assert exc_info.value.code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.asyncio
    async def test_user_id_returns_404_when_target_not_found(self, mock_user):
        """Returns 404 when the target user does not exist in the database."""
        from codemie.rest_api.routers.analytics.spending import get_user_budget_usage

        mock_user.is_admin = True

        with patch.object(config, "ENABLE_USER_MANAGEMENT", True):
            with patch(
                "codemie.rest_api.routers.analytics.spending.asyncio.to_thread",
                new_callable=AsyncMock,
                return_value=(None, []),
            ):
                with pytest.raises(ExtendedHTTPException) as exc_info:
                    await get_user_budget_usage(user=mock_user, user_id="missing-user-id")

        assert exc_info.value.code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_user_id_returns_403_when_caller_has_no_project_overlap(self, mock_user):
        """Returns 403 when caller is not global admin and shares no projects with the target."""
        from codemie.rest_api.routers.analytics.spending import get_user_budget_usage

        target_db = MagicMock()
        target_db.id = "target-id"
        target_db.email = "target@example.com"
        target_db.username = "target"

        mock_user.is_admin = False
        mock_user.admin_project_names = ["caller-project"]

        with patch.object(config, "ENABLE_USER_MANAGEMENT", True):
            with patch(
                "codemie.rest_api.routers.analytics.spending.asyncio.to_thread",
                new_callable=AsyncMock,
                return_value=(target_db, {"target-only-project"}),
            ):
                with pytest.raises(ExtendedHTTPException) as exc_info:
                    await get_user_budget_usage(user=mock_user, user_id="target-id")

        assert exc_info.value.code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_user_id_global_admin_can_view_any_user_budget(self, mock_user):
        """Global admin can view any user's budget regardless of project membership."""
        from codemie.rest_api.routers.analytics.spending import get_user_budget_usage
        from codemie.service.analytics.handlers.budget_usage_service import _get_key_spending_columns

        target_db = MagicMock()
        target_db.id = "target-id"
        target_db.email = "target@example.com"
        target_db.username = "target_user"

        mock_user.is_admin = True

        with patch.object(config, "ENABLE_USER_MANAGEMENT", True):
            with patch(
                "codemie.rest_api.routers.analytics.spending.asyncio.to_thread",
                new_callable=AsyncMock,
                return_value=(target_db, {"some-project"}),
            ):
                with patch(
                    "codemie.service.analytics.handlers.budget_usage_service.BudgetUsageService.get_budget_usage",
                    new_callable=AsyncMock,
                    return_value=(_get_key_spending_columns(), []),
                ):
                    response = await get_user_budget_usage(user=mock_user, user_id="target-id")

        assert "data" in response
        assert response["data"]["rows"] == []

    @pytest.mark.asyncio
    async def test_user_id_project_admin_with_shared_project_can_view_budget(self, mock_user):
        """Project admin who shares at least one project with target can view the budget."""
        from codemie.rest_api.routers.analytics.spending import get_user_budget_usage
        from codemie.service.analytics.handlers.budget_usage_service import _get_key_spending_columns

        target_db = MagicMock()
        target_db.id = "target-id"
        target_db.email = "target@example.com"
        target_db.username = "target"

        mock_user.is_admin = False
        mock_user.admin_project_names = ["shared-project"]

        with patch.object(config, "ENABLE_USER_MANAGEMENT", True):
            with patch(
                "codemie.rest_api.routers.analytics.spending.asyncio.to_thread",
                new_callable=AsyncMock,
                return_value=(target_db, {"shared-project", "other-project"}),
            ):
                with patch(
                    "codemie.service.analytics.handlers.budget_usage_service.BudgetUsageService.get_budget_usage",
                    new_callable=AsyncMock,
                    return_value=(_get_key_spending_columns(), []),
                ):
                    response = await get_user_budget_usage(user=mock_user, user_id="target-id")

        assert "data" in response

    @pytest.mark.asyncio
    async def test_budget_usage_includes_project_rows(self, mock_user):
        """Per-project rows appear in self-service response after personal rows."""
        from codemie.rest_api.routers.analytics.spending import get_user_budget_usage
        from codemie.service.analytics.handlers.budget_usage_service import _get_key_spending_columns

        rows = [
            {
                "project_name": mock_user.email,
                "current_spending": 1.0,
                "budget_reset_at": None,
                "time_until_reset": None,
                "budget_limit": 10.0,
                "total": 10.0,
            },
            {
                "project_name": "proj-x",
                "current_spending": 2.0,
                "budget_reset_at": None,
                "time_until_reset": None,
                "budget_limit": 20.0,
                "total": 10.0,
            },
        ]
        with patch(
            "codemie.service.analytics.handlers.budget_usage_service.BudgetUsageService.get_budget_usage",
            new_callable=AsyncMock,
            return_value=(_get_key_spending_columns(), rows),
        ):
            response = await get_user_budget_usage(user=mock_user, user_id=None)

        names = [r["project_name"] for r in response["data"]["rows"]]
        assert names.index(mock_user.email) < names.index("proj-x")

    @pytest.mark.asyncio
    async def test_admin_path_includes_project_rows(self, mock_user):
        """Per-project rows appear in admin-path response (AC 1)."""
        from codemie.rest_api.routers.analytics.spending import get_user_budget_usage
        from codemie.service.analytics.handlers.budget_usage_service import _get_key_spending_columns

        rows = [
            {
                "project_name": "target-username",
                "current_spending": 1.0,
                "budget_reset_at": None,
                "time_until_reset": None,
                "budget_limit": 10.0,
                "total": 10.0,
            },
            {
                "project_name": "proj-admin",
                "current_spending": 3.0,
                "budget_reset_at": None,
                "time_until_reset": None,
                "budget_limit": 30.0,
                "total": 10.0,
            },
        ]

        with (
            patch(
                "codemie.rest_api.routers.analytics.spending._resolve_admin_budget_subject",
                new_callable=AsyncMock,
                return_value=("target-user-id", "target-username", "target@corp.com"),
            ),
            patch(
                "codemie.service.analytics.handlers.budget_usage_service.BudgetUsageService.get_budget_usage",
                new_callable=AsyncMock,
                return_value=(_get_key_spending_columns(), rows),
            ),
        ):
            response = await get_user_budget_usage(user=mock_user, user_id="target-user-id")

        result_rows = response["data"]["rows"]
        project_row = next(r for r in result_rows if r["project_name"] == "proj-admin")
        assert project_row is not None


class TestAuthorizeAdminBudgetView:
    """Tests for _authorize_admin_budget_view authorization helper."""

    def test_global_admin_always_authorized(self):
        from codemie.rest_api.routers.analytics._spending_support import _authorize_admin_budget_view

        caller = MagicMock(spec=User)
        caller.is_admin = True
        _authorize_admin_budget_view(caller, {"any-project"})  # must not raise

    def test_global_admin_authorized_for_empty_target_projects(self):
        from codemie.rest_api.routers.analytics._spending_support import _authorize_admin_budget_view

        caller = MagicMock(spec=User)
        caller.is_admin = True
        _authorize_admin_budget_view(caller, set())  # must not raise

    def test_project_admin_with_shared_project_is_authorized(self):
        from codemie.rest_api.routers.analytics._spending_support import _authorize_admin_budget_view

        caller = MagicMock(spec=User)
        caller.is_admin = False
        caller.admin_project_names = ["shared-project"]
        _authorize_admin_budget_view(caller, {"shared-project", "other-project"})  # must not raise

    def test_non_admin_without_shared_project_raises_403(self):
        from codemie.rest_api.routers.analytics._spending_support import _authorize_admin_budget_view

        caller = MagicMock(spec=User)
        caller.is_admin = False
        caller.admin_project_names = ["my-project"]

        with pytest.raises(ExtendedHTTPException) as exc_info:
            _authorize_admin_budget_view(caller, {"other-project"})

        assert exc_info.value.code == status.HTTP_403_FORBIDDEN

    def test_empty_admin_project_names_raises_403(self):
        from codemie.rest_api.routers.analytics._spending_support import _authorize_admin_budget_view

        caller = MagicMock(spec=User)
        caller.is_admin = False
        caller.admin_project_names = []

        with pytest.raises(ExtendedHTTPException) as exc_info:
            _authorize_admin_budget_view(caller, {"any-project"})

        assert exc_info.value.code == status.HTTP_403_FORBIDDEN

    def test_none_admin_project_names_raises_403(self):
        from codemie.rest_api.routers.analytics._spending_support import _authorize_admin_budget_view

        caller = MagicMock(spec=User)
        caller.is_admin = False
        caller.admin_project_names = None

        with pytest.raises(ExtendedHTTPException) as exc_info:
            _authorize_admin_budget_view(caller, {"any-project"})

        assert exc_info.value.code == status.HTTP_403_FORBIDDEN
