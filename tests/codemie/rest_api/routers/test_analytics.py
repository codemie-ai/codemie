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
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.responses import JSONResponse

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.analytics import SummariesResponse, TabularResponse
from codemie.rest_api.routers.analytics import _create_response, handle_analytics_errors
from codemie.rest_api.security.user import User


@pytest.fixture
def mock_user():
    """Create a mock authenticated user for testing."""
    user = MagicMock(spec=User)
    user.id = "user123@example.com"
    user.username = "testuser"
    user.name = "Test User"
    user.is_admin = False
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
    @patch("codemie.rest_api.routers.analytics.logger")
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
    @patch("codemie.rest_api.routers.analytics.logger")
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
        response = _create_response(sample_summaries_response_data, SummariesResponse)

        # Assert
        assert "Cache-Control" in response.headers
        assert response.headers["Cache-Control"] == "public, max-age=300"
        assert "ETag" in response.headers

    def test_etag_consistent_for_same_data(self, sample_summaries_response_data):
        """Verify ETag is deterministic for identical data."""
        # Act
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
        response1 = _create_response(data1, SummariesResponse)
        response2 = _create_response(data2, SummariesResponse)

        # Assert
        assert response1.headers["ETag"] != response2.headers["ETag"]

    def test_etag_is_md5_hash_of_response_body(self, sample_summaries_response_data):
        """Verify ETag is MD5 hash of response body."""
        # Act
        response = _create_response(sample_summaries_response_data, SummariesResponse)

        # Assert
        validated = SummariesResponse(**sample_summaries_response_data)
        response_dict = validated.model_dump(by_alias=True)
        expected_etag = hashlib.md5(json.dumps(response_dict, sort_keys=True).encode()).hexdigest()
        assert response.headers["ETag"] == expected_etag


class TestGetSummariesEndpoint:
    """Tests for the /v1/analytics/summaries endpoint."""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_with_time_period(self, mock_service_class, mock_user, sample_summaries_response_data):
        """Verify predefined time period is passed to service."""
        # Arrange
        from codemie.rest_api.routers.analytics import get_summaries

        mock_service = AsyncMock()
        mock_service.get_summaries.return_value = sample_summaries_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_summaries(
            user=mock_user, time_period="last_30_days", start_date=None, end_date=None, users=None, projects=None
        )

        # Assert
        mock_service.get_summaries.assert_called_once_with(
            time_period="last_30_days", start_date=None, end_date=None, users=None, projects=None
        )
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_with_custom_date_range(self, mock_service_class, mock_user, sample_summaries_response_data):
        """Verify custom date range parameters are parsed correctly."""
        # Arrange
        from codemie.rest_api.routers.analytics import get_summaries

        mock_service = AsyncMock()
        mock_service.get_summaries.return_value = sample_summaries_response_data
        mock_service_class.return_value = mock_service

        start_date = datetime(2025, 1, 1, 0, 0, 0)
        end_date = datetime(2025, 1, 31, 23, 59, 59)

        # Act
        response = await get_summaries(
            user=mock_user, time_period=None, start_date=start_date, end_date=end_date, users=None, projects=None
        )

        # Assert
        mock_service.get_summaries.assert_called_once_with(
            time_period=None, start_date=start_date, end_date=end_date, users=None, projects=None
        )
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_with_user_filter(self, mock_service_class, mock_user, sample_summaries_response_data):
        """Verify comma-separated users are parsed into list."""
        # Arrange
        from codemie.rest_api.routers.analytics import get_summaries

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
        )

        # Assert
        mock_service.get_summaries.assert_called_once_with(
            time_period="last_30_days",
            start_date=None,
            end_date=None,
            users=["user1@example.com", "user2@example.com"],
            projects=None,
        )
        assert isinstance(response, JSONResponse)

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_with_project_filter(self, mock_service_class, mock_user, sample_summaries_response_data):
        """Verify comma-separated projects are parsed into list."""
        # Arrange
        from codemie.rest_api.routers.analytics import get_summaries

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
        )

        # Assert
        mock_service.get_summaries.assert_called_once_with(
            time_period="last_30_days",
            start_date=None,
            end_date=None,
            users=None,
            projects=["codemie", "project-alpha"],
        )
        assert isinstance(response, JSONResponse)

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.logger")
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_logs_request_parameters(
        self, mock_service_class, mock_logger, mock_user, sample_summaries_response_data
    ):
        """Verify request parameters are logged for audit trail."""
        # Arrange
        from codemie.rest_api.routers.analytics import get_summaries

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
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    @patch("codemie.rest_api.routers.analytics.config")
    async def test_pagination_defaults(self, mock_config, mock_service_class, mock_user, sample_tabular_response_data):
        """Verify default pagination values are applied."""
        # Arrange
        from codemie.rest_api.routers.analytics import get_assistants_chats

        mock_config.ANALYTICS_DEFAULT_PAGE_SIZE = 50
        mock_service = AsyncMock()
        mock_service.get_assistants_chats.return_value = sample_tabular_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_assistants_chats(
            user=mock_user,
            time_period=None,
            start_date=None,
            end_date=None,
            users=None,
            projects=None,
            page=0,
            per_page=50,
        )

        # Assert
        mock_service.get_assistants_chats.assert_called_once()
        call_kwargs = mock_service.get_assistants_chats.call_args
        # Check positional args (page and per_page are last two)
        assert call_kwargs[0][-2] == 0  # page
        assert call_kwargs[0][-1] == 50  # per_page
        assert isinstance(response, JSONResponse)

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_pagination_custom_values(self, mock_service_class, mock_user, sample_tabular_response_data):
        """Verify custom pagination parameters are respected."""
        # Arrange
        from codemie.rest_api.routers.analytics import get_assistants_chats

        mock_service = AsyncMock()
        mock_service.get_assistants_chats.return_value = sample_tabular_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_assistants_chats(
            user=mock_user,
            time_period=None,
            start_date=None,
            end_date=None,
            users=None,
            projects=None,
            page=2,
            per_page=100,
        )

        # Assert
        mock_service.get_assistants_chats.assert_called_once()
        call_kwargs = mock_service.get_assistants_chats.call_args
        # Check positional args (page and per_page are last two)
        assert call_kwargs[0][-2] == 2  # page
        assert call_kwargs[0][-1] == 100  # per_page
        assert isinstance(response, JSONResponse)


class TestWorkflowsEndpoint:
    """Tests for the /v1/analytics/workflows endpoint."""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_workflows_endpoint_basic(self, mock_service_class, mock_user, sample_tabular_response_data):
        """Verify workflows endpoint processes request correctly."""
        # Arrange
        from codemie.rest_api.routers.analytics import get_workflows

        mock_service = AsyncMock()
        mock_service.get_workflows.return_value = sample_tabular_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_workflows(
            user=mock_user,
            time_period="last_7_days",
            start_date=None,
            end_date=None,
            users=None,
            projects=None,
            page=0,
            per_page=50,
        )

        # Assert
        mock_service.get_workflows.assert_called_once_with("last_7_days", None, None, None, None, 0, 50)
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_200_OK


class TestToolsUsageEndpoint:
    """Tests for the /v1/analytics/tools-usage endpoint."""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_tools_usage_endpoint_with_filters(self, mock_service_class, mock_user, sample_tabular_response_data):
        """Verify tools usage endpoint applies filters correctly."""
        # Arrange
        from codemie.rest_api.routers.analytics import get_tools_usage

        mock_service = AsyncMock()
        mock_service.get_tools_usage.return_value = sample_tabular_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_tools_usage(
            user=mock_user,
            time_period=None,
            start_date=datetime(2025, 1, 1),
            end_date=datetime(2025, 1, 31),
            users="user1@example.com",
            projects="project1,project2",
            page=1,
            per_page=25,
        )

        # Assert
        mock_service.get_tools_usage.assert_called_once_with(
            None,
            datetime(2025, 1, 1),
            datetime(2025, 1, 31),
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
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_service_value_error_returns_400(self, mock_service_class, mock_user):
        """Verify ValueError from service returns 400 with helpful message."""
        # Arrange
        from codemie.rest_api.routers.analytics import get_summaries

        mock_service = AsyncMock()
        mock_service.get_summaries.side_effect = ValueError("Invalid time_period: 'invalid_value'")
        mock_service_class.return_value = mock_service

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await get_summaries(
                user=mock_user, time_period="invalid_value", start_date=None, end_date=None, users=None, projects=None
            )

        exception = exc_info.value
        assert exception.code == status.HTTP_400_BAD_REQUEST
        assert exception.message == "Invalid request parameters"
        assert "Invalid time_period" in exception.details

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_service_generic_exception_returns_500(self, mock_service_class, mock_user):
        """Verify unexpected service errors return 500."""
        # Arrange
        from codemie.rest_api.routers.analytics import get_summaries

        mock_service = AsyncMock()
        mock_service.get_summaries.side_effect = Exception("Database connection failed")
        mock_service_class.return_value = mock_service

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await get_summaries(
                user=mock_user, time_period="last_30_days", start_date=None, end_date=None, users=None, projects=None
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
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_agents_usage_endpoint(self, mock_service_class, mock_user, sample_tabular_response_data):
        """Verify agents usage endpoint follows standard pattern."""
        # Arrange
        from codemie.rest_api.routers.analytics import get_agents_usage

        mock_service = AsyncMock()
        mock_service.get_agents_usage.return_value = sample_tabular_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_agents_usage(
            user=mock_user,
            time_period="last_24_hours",
            start_date=None,
            end_date=None,
            users=None,
            projects=None,
            page=0,
            per_page=50,
        )

        # Assert
        mock_service.get_agents_usage.assert_called_once()
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_200_OK
        assert "Cache-Control" in response.headers
        assert "ETag" in response.headers

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_llms_usage_endpoint(self, mock_service_class, mock_user, sample_tabular_response_data):
        """Verify LLMs usage endpoint follows standard pattern."""
        # Arrange
        from codemie.rest_api.routers.analytics import get_llms_usage

        mock_service = AsyncMock()
        mock_service.get_llms_usage.return_value = sample_tabular_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_llms_usage(
            user=mock_user,
            time_period="last_60_days",
            start_date=None,
            end_date=None,
            users="user1@example.com,user2@example.com",
            projects="project1",
            page=0,
            per_page=50,
        )

        # Assert
        mock_service.get_llms_usage.assert_called_once_with(
            "last_60_days", None, None, ["user1@example.com", "user2@example.com"], ["project1"], 0, 50
        )
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_200_OK


class TestCliEndpoints:
    """Tests for CLI-specific analytics endpoints."""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_cli_summary_endpoint(self, mock_service_class, mock_user, sample_summaries_response_data):
        """Verify CLI summary endpoint returns SummariesResponse format."""
        # Arrange
        from codemie.rest_api.routers.analytics import get_cli_summary

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
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_cli_agents_endpoint(self, mock_service_class, mock_user, sample_tabular_response_data):
        """Verify CLI agents endpoint with pagination."""
        # Arrange
        from codemie.rest_api.routers.analytics import get_cli_agents

        mock_service = AsyncMock()
        mock_service.get_cli_agents.return_value = sample_tabular_response_data
        mock_service_class.return_value = mock_service

        # Act
        response = await get_cli_agents(
            user=mock_user,
            time_period="last_30_days",
            start_date=None,
            end_date=None,
            users=None,
            projects=None,
            page=0,
            per_page=50,
        )

        # Assert
        mock_service.get_cli_agents.assert_called_once()
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
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_get_users_list_returns_correct_response(
        self, mock_service_class, mock_user, sample_users_list_response_data
    ):
        """Verify endpoint returns correct response model."""
        # Arrange
        from codemie.rest_api.routers.analytics import get_users_list

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
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_get_users_list_passes_filters(self, mock_service_class, mock_user, sample_users_list_response_data):
        """Verify filters are passed to service."""
        # Arrange
        from codemie.rest_api.routers.analytics import get_users_list

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
        )

        # Assert
        mock_service.get_users_list.assert_called_once_with(
            time_period=None,
            start_date=datetime(2025, 1, 1),
            end_date=datetime(2025, 1, 31),
            users=["u1", "u2"],
            projects=["p1"],
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
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_get_projects_unique_daily_success(
        self, mock_service_class, mock_user, sample_projects_unique_daily_response_data
    ):
        """Verify successful response with default parameters."""
        # Arrange
        from codemie.rest_api.routers.analytics import get_projects_unique_daily

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
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_get_projects_unique_daily_with_date_range(
        self, mock_service_class, mock_user, sample_projects_unique_daily_response_data
    ):
        """Verify custom date range parameters are passed correctly."""
        # Arrange
        from codemie.rest_api.routers.analytics import get_projects_unique_daily

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
        mock_service.get_projects_unique_daily.assert_called_once_with(None, start, end, None, None)

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_get_projects_unique_daily_with_filters(
        self, mock_service_class, mock_user, sample_projects_unique_daily_response_data
    ):
        """Verify user and project filters are parsed from CSV strings."""
        # Arrange
        from codemie.rest_api.routers.analytics import get_projects_unique_daily

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
        from codemie.rest_api.routers.analytics import get_projects_unique_daily

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

        # Verify it's an HTTP 500 exception (HTTPException wrapped by error handler)
        assert exc_info.value.code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "start_date" in exc_info.value.details.lower() or "end_date" in exc_info.value.details.lower()

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_get_projects_unique_daily_handles_empty_result(self, mock_service_class, mock_user):
        """Verify handling of empty result set."""
        # Arrange
        from codemie.rest_api.routers.analytics import get_projects_unique_daily

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
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_get_projects_unique_daily_response_format(
        self, mock_service_class, mock_user, sample_projects_unique_daily_response_data
    ):
        """Verify response format matches TabularResponse model."""
        # Arrange
        from codemie.rest_api.routers.analytics import get_projects_unique_daily

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


class TestUserEngagementUsersDrillDown:
    """Tests for /ai-adoption-user-engagement/users drill-down endpoint."""

    @pytest.fixture
    def sample_user_engagement_users_response(self):
        """Sample response data for user engagement users drill-down."""
        return {
            "data": {
                "columns": [
                    {"id": "user_name", "label": "User Name", "type": "string"},
                    {"id": "user_id", "label": "User ID", "type": "string"},
                    {"id": "engagement_score", "label": "Engagement Score", "type": "number"},
                    {"id": "total_interactions", "label": "Total Interactions", "type": "number"},
                    {"id": "user_type", "label": "User Type", "type": "string"},
                    {"id": "activity_level", "label": "Activity Level", "type": "string"},
                ],
                "rows": [
                    {
                        "user_name": "John Doe",
                        "user_id": "user1@example.com",
                        "engagement_score": 95.5,
                        "total_interactions": 500,
                        "user_type": "power_user",
                        "activity_level": "daily",
                    },
                    {
                        "user_name": "Jane Smith",
                        "user_id": "user2@example.com",
                        "engagement_score": 75.0,
                        "total_interactions": 200,
                        "user_type": "engaged",
                        "activity_level": "weekly",
                    },
                ],
            },
            "metadata": {
                "timestamp": "2026-01-23T10:00:00Z",
                "data_as_of": "2026-01-23T09:55:00Z",
                "filters_applied": {"project": "project1"},
                "execution_time_ms": 45.2,
            },
            "pagination": {"page": 0, "per_page": 20, "total_count": 2, "has_more": False},
        }

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_successful_request_with_project_access(
        self, mock_service_class, mock_user, sample_user_engagement_users_response
    ):
        """Verify user can access drill-down for projects they have access to."""
        # Arrange
        from codemie.rest_api.routers.analytics import post_ai_adoption_user_engagement_users

        mock_service = AsyncMock()
        mock_service.get_user_engagement_users.return_value = sample_user_engagement_users_response
        mock_service_class.return_value = mock_service

        request_data = MagicMock()
        request_data.project = "project1"
        request_data.page = 0
        request_data.per_page = 20
        request_data.user_type = None
        request_data.activity_level = None
        request_data.multi_assistant_only = None
        request_data.sort_by = "engagement_score"
        request_data.sort_order = "desc"
        request_data.config = None

        # Act
        response = await post_ai_adoption_user_engagement_users(request=request_data, user=mock_user)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        mock_service.get_user_engagement_users.assert_called_once_with(
            project="project1",
            page=0,
            per_page=20,
            user_type=None,
            activity_level=None,
            multi_assistant_only=None,
            sort_by="engagement_score",
            sort_order="desc",
            config=None,
        )

    @pytest.mark.asyncio
    async def test_access_denied_for_project_without_permission(self, mock_user):
        """Verify non-admin users cannot access projects they don't have permission for."""
        # Arrange
        from codemie.rest_api.routers.analytics import post_ai_adoption_user_engagement_users

        request_data = MagicMock()
        request_data.project = "unauthorized_project"

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await post_ai_adoption_user_engagement_users(request=request_data, user=mock_user)

        assert exc_info.value.code == status.HTTP_403_FORBIDDEN
        assert "access" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_admin_can_access_any_project(self, mock_service_class, sample_user_engagement_users_response):
        """Verify admin users can access any project."""
        # Arrange
        from codemie.rest_api.routers.analytics import post_ai_adoption_user_engagement_users

        admin_user = MagicMock(spec=User)
        admin_user.id = "admin@example.com"
        admin_user.is_admin = True

        mock_service = AsyncMock()
        mock_service.get_user_engagement_users.return_value = sample_user_engagement_users_response
        mock_service_class.return_value = mock_service

        request_data = MagicMock()
        request_data.project = "any_project"
        request_data.page = 0
        request_data.per_page = 20
        request_data.user_type = None
        request_data.activity_level = None
        request_data.multi_assistant_only = None
        request_data.sort_by = "engagement_score"
        request_data.sort_order = "desc"
        request_data.config = None

        # Act
        response = await post_ai_adoption_user_engagement_users(request=request_data, user=admin_user)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        mock_service.get_user_engagement_users.assert_called_once()

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_request_with_filters(self, mock_service_class, mock_user, sample_user_engagement_users_response):
        """Verify filters are properly passed to service layer."""
        # Arrange
        from codemie.rest_api.routers.analytics import post_ai_adoption_user_engagement_users

        mock_service = AsyncMock()
        mock_service.get_user_engagement_users.return_value = sample_user_engagement_users_response
        mock_service_class.return_value = mock_service

        request_data = MagicMock()
        request_data.project = "project1"
        request_data.page = 0
        request_data.per_page = 20
        request_data.user_type = "power_user"
        request_data.activity_level = "daily"
        request_data.multi_assistant_only = True
        request_data.sort_by = "total_interactions"
        request_data.sort_order = "asc"
        request_data.config = None

        # Act
        response = await post_ai_adoption_user_engagement_users(request=request_data, user=mock_user)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        mock_service.get_user_engagement_users.assert_called_once_with(
            project="project1",
            page=0,
            per_page=20,
            user_type="power_user",
            activity_level="daily",
            multi_assistant_only=True,
            sort_by="total_interactions",
            sort_order="asc",
            config=None,
        )


class TestAssistantReusabilityDrillDown:
    """Tests for /ai-adoption-asset-reusability/assistants drill-down endpoint."""

    @pytest.fixture
    def sample_assistant_reusability_response(self):
        """Sample response data for assistant reusability drill-down."""
        return {
            "data": {
                "columns": [
                    {"id": "assistant_name", "label": "Assistant Name", "type": "string"},
                    {"id": "assistant_id", "label": "Assistant ID", "type": "string"},
                    {"id": "total_usage", "label": "Total Usage", "type": "number"},
                    {"id": "unique_users", "label": "Unique Users", "type": "number"},
                    {"id": "status", "label": "Status", "type": "string"},
                    {"id": "adoption", "label": "Adoption", "type": "string"},
                ],
                "rows": [
                    {
                        "assistant_name": "Code Helper",
                        "assistant_id": "assistant-1",
                        "total_usage": 1500,
                        "unique_users": 25,
                        "status": "active",
                        "adoption": "team_adopted",
                    },
                    {
                        "assistant_name": "Data Analyzer",
                        "assistant_id": "assistant-2",
                        "total_usage": 300,
                        "unique_users": 3,
                        "status": "inactive",
                        "adoption": "single_user",
                    },
                ],
            },
            "metadata": {
                "timestamp": "2026-01-23T10:00:00Z",
                "data_as_of": "2026-01-23T09:55:00Z",
                "filters_applied": {"project": "project1"},
                "execution_time_ms": 50.0,
            },
            "pagination": {"page": 0, "per_page": 20, "total_count": 2, "has_more": False},
        }

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_successful_request(self, mock_service_class, mock_user, sample_assistant_reusability_response):
        """Verify successful assistant reusability drill-down request."""
        # Arrange
        from codemie.rest_api.routers.analytics import post_ai_adoption_assistant_reusability_detail

        mock_service = AsyncMock()
        mock_service.get_assistant_reusability_detail.return_value = sample_assistant_reusability_response
        mock_service_class.return_value = mock_service

        request_data = MagicMock()
        request_data.project = "project1"
        request_data.page = 0
        request_data.per_page = 20
        request_data.status = None
        request_data.adoption = None
        request_data.sort_by = "total_usage"
        request_data.sort_order = "desc"
        request_data.config = None

        # Act
        response = await post_ai_adoption_assistant_reusability_detail(request=request_data, user=mock_user)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        mock_service.get_assistant_reusability_detail.assert_called_once_with(
            project="project1",
            page=0,
            per_page=20,
            status=None,
            adoption=None,
            sort_by="total_usage",
            sort_order="desc",
            config=None,
        )

    @pytest.mark.asyncio
    async def test_access_denied_for_unauthorized_project(self, mock_user):
        """Verify access control for assistant reusability drill-down."""
        # Arrange
        from codemie.rest_api.routers.analytics import post_ai_adoption_assistant_reusability_detail

        request_data = MagicMock()
        request_data.project = "unauthorized_project"

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await post_ai_adoption_assistant_reusability_detail(request=request_data, user=mock_user)

        assert exc_info.value.code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_request_with_status_filter(
        self, mock_service_class, mock_user, sample_assistant_reusability_response
    ):
        """Verify status filter is properly passed to service."""
        # Arrange
        from codemie.rest_api.routers.analytics import post_ai_adoption_assistant_reusability_detail

        mock_service = AsyncMock()
        mock_service.get_assistant_reusability_detail.return_value = sample_assistant_reusability_response
        mock_service_class.return_value = mock_service

        request_data = MagicMock()
        request_data.project = "project1"
        request_data.page = 0
        request_data.per_page = 20
        request_data.status = "inactive"
        request_data.adoption = "single_user"
        request_data.sort_by = "last_used"
        request_data.sort_order = "desc"
        request_data.config = None

        # Act
        response = await post_ai_adoption_assistant_reusability_detail(request=request_data, user=mock_user)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        mock_service.get_assistant_reusability_detail.assert_called_once_with(
            project="project1",
            page=0,
            per_page=20,
            status="inactive",
            adoption="single_user",
            sort_by="last_used",
            sort_order="desc",
            config=None,
        )


class TestWorkflowReusabilityDrillDown:
    """Tests for /ai-adoption-asset-reusability/workflows drill-down endpoint."""

    @pytest.fixture
    def sample_workflow_reusability_response(self):
        """Sample response data for workflow reusability drill-down."""
        return {
            "data": {
                "columns": [
                    {"id": "workflow_name", "label": "Workflow Name", "type": "string"},
                    {"id": "workflow_id", "label": "Workflow ID", "type": "string"},
                    {"id": "execution_count", "label": "Execution Count", "type": "number"},
                    {"id": "unique_users", "label": "Unique Users", "type": "number"},
                    {"id": "status", "label": "Status", "type": "string"},
                    {"id": "reuse", "label": "Reuse", "type": "string"},
                ],
                "rows": [
                    {
                        "workflow_name": "Data Pipeline",
                        "workflow_id": "workflow-1",
                        "execution_count": 2500,
                        "unique_users": 15,
                        "status": "active",
                        "reuse": "multi_user",
                    },
                    {
                        "workflow_name": "Report Generator",
                        "workflow_id": "workflow-2",
                        "execution_count": 50,
                        "unique_users": 1,
                        "status": "inactive",
                        "reuse": "single_user",
                    },
                ],
            },
            "metadata": {
                "timestamp": "2026-01-23T10:00:00Z",
                "data_as_of": "2026-01-23T09:55:00Z",
                "filters_applied": {"project": "project1"},
                "execution_time_ms": 48.0,
            },
            "pagination": {"page": 0, "per_page": 20, "total_count": 2, "has_more": False},
        }

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_successful_request(self, mock_service_class, mock_user, sample_workflow_reusability_response):
        """Verify successful workflow reusability drill-down request."""
        # Arrange
        from codemie.rest_api.routers.analytics import post_ai_adoption_workflow_reusability_detail

        mock_service = AsyncMock()
        mock_service.get_workflow_reusability_detail.return_value = sample_workflow_reusability_response
        mock_service_class.return_value = mock_service

        request_data = MagicMock()
        request_data.project = "project1"
        request_data.page = 0
        request_data.per_page = 20
        request_data.status = None
        request_data.reuse = None
        request_data.sort_by = "execution_count"
        request_data.sort_order = "desc"
        request_data.config = None

        # Act
        response = await post_ai_adoption_workflow_reusability_detail(request=request_data, user=mock_user)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        mock_service.get_workflow_reusability_detail.assert_called_once_with(
            project="project1",
            page=0,
            per_page=20,
            status=None,
            reuse=None,
            sort_by="execution_count",
            sort_order="desc",
            config=None,
        )

    @pytest.mark.asyncio
    async def test_access_denied_for_unauthorized_project(self, mock_user):
        """Verify access control for workflow reusability drill-down."""
        # Arrange
        from codemie.rest_api.routers.analytics import post_ai_adoption_workflow_reusability_detail

        request_data = MagicMock()
        request_data.project = "unauthorized_project"

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await post_ai_adoption_workflow_reusability_detail(request=request_data, user=mock_user)

        assert exc_info.value.code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_request_with_filters(self, mock_service_class, mock_user, sample_workflow_reusability_response):
        """Verify filters are properly passed to service layer."""
        # Arrange
        from codemie.rest_api.routers.analytics import post_ai_adoption_workflow_reusability_detail

        mock_service = AsyncMock()
        mock_service.get_workflow_reusability_detail.return_value = sample_workflow_reusability_response
        mock_service_class.return_value = mock_service

        request_data = MagicMock()
        request_data.project = "project1"
        request_data.page = 1
        request_data.per_page = 50
        request_data.status = "active"
        request_data.reuse = "multi_user"
        request_data.sort_by = "last_executed"
        request_data.sort_order = "asc"
        request_data.config = None

        # Act
        response = await post_ai_adoption_workflow_reusability_detail(request=request_data, user=mock_user)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        mock_service.get_workflow_reusability_detail.assert_called_once_with(
            project="project1",
            page=1,
            per_page=50,
            status="active",
            reuse="multi_user",
            sort_by="last_executed",
            sort_order="asc",
            config=None,
        )


class TestDatasourceReusabilityDrillDown:
    """Tests for /ai-adoption-asset-reusability/datasources drill-down endpoint."""

    @pytest.fixture
    def sample_datasource_reusability_response(self):
        """Sample response data for datasource reusability drill-down."""
        return {
            "data": {
                "columns": [
                    {"id": "datasource_name", "label": "Datasource Name", "type": "string"},
                    {"id": "datasource_id", "label": "Datasource ID", "type": "string"},
                    {"id": "assistant_count", "label": "Assistant Count", "type": "number"},
                    {"id": "max_usage", "label": "Max Usage", "type": "number"},
                    {"id": "status", "label": "Status", "type": "string"},
                    {"id": "shared", "label": "Shared", "type": "string"},
                    {"id": "type", "label": "Type", "type": "string"},
                ],
                "rows": [
                    {
                        "datasource_name": "Main Repository",
                        "datasource_id": "datasource-1",
                        "assistant_count": 10,
                        "max_usage": 5000,
                        "status": "active",
                        "shared": "shared",
                        "type": "git",
                    },
                    {
                        "datasource_name": "Private Wiki",
                        "datasource_id": "datasource-2",
                        "assistant_count": 1,
                        "max_usage": 50,
                        "status": "inactive",
                        "shared": "single",
                        "type": "confluence",
                    },
                ],
            },
            "metadata": {
                "timestamp": "2026-01-23T10:00:00Z",
                "data_as_of": "2026-01-23T09:55:00Z",
                "filters_applied": {"project": "project1"},
                "execution_time_ms": 52.0,
            },
            "pagination": {"page": 0, "per_page": 20, "total_count": 2, "has_more": False},
        }

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_successful_request(self, mock_service_class, mock_user, sample_datasource_reusability_response):
        """Verify successful datasource reusability drill-down request."""
        # Arrange
        from codemie.rest_api.routers.analytics import post_ai_adoption_datasource_reusability_detail

        mock_service = AsyncMock()
        mock_service.get_datasource_reusability_detail.return_value = sample_datasource_reusability_response
        mock_service_class.return_value = mock_service

        request_data = MagicMock()
        request_data.project = "project1"
        request_data.page = 0
        request_data.per_page = 20
        request_data.status = None
        request_data.shared = None
        request_data.type = None
        request_data.sort_by = "assistant_count"
        request_data.sort_order = "desc"
        request_data.config = None

        # Act
        response = await post_ai_adoption_datasource_reusability_detail(request=request_data, user=mock_user)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        mock_service.get_datasource_reusability_detail.assert_called_once_with(
            project="project1",
            page=0,
            per_page=20,
            status=None,
            shared=None,
            type=None,
            sort_by="assistant_count",
            sort_order="desc",
            config=None,
        )

    @pytest.mark.asyncio
    async def test_access_denied_for_unauthorized_project(self, mock_user):
        """Verify access control for datasource reusability drill-down."""
        # Arrange
        from codemie.rest_api.routers.analytics import post_ai_adoption_datasource_reusability_detail

        request_data = MagicMock()
        request_data.project = "unauthorized_project"

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await post_ai_adoption_datasource_reusability_detail(request=request_data, user=mock_user)

        assert exc_info.value.code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_request_with_filters(self, mock_service_class, mock_user, sample_datasource_reusability_response):
        """Verify filters are properly passed to service layer."""
        # Arrange
        from codemie.rest_api.routers.analytics import post_ai_adoption_datasource_reusability_detail

        mock_service = AsyncMock()
        mock_service.get_datasource_reusability_detail.return_value = sample_datasource_reusability_response
        mock_service_class.return_value = mock_service

        request_data = MagicMock()
        request_data.project = "project1"
        request_data.page = 0
        request_data.per_page = 20
        request_data.status = "active"
        request_data.shared = "shared"
        request_data.type = "git"
        request_data.sort_by = "max_usage"
        request_data.sort_order = "desc"
        request_data.config = None

        # Act
        response = await post_ai_adoption_datasource_reusability_detail(request=request_data, user=mock_user)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        mock_service.get_datasource_reusability_detail.assert_called_once_with(
            project="project1",
            page=0,
            per_page=20,
            status="active",
            shared="shared",
            type="git",
            sort_by="max_usage",
            sort_order="desc",
            config=None,
        )


# ============================================================================
# Tests for /analytics/spending endpoint
# ============================================================================


class TestGetUserSpending:
    """Tests for GET /analytics/spending endpoint."""

    @pytest.mark.asyncio
    async def test_get_user_spending_success(self, mock_user):
        """Test successful spending data retrieval."""
        from codemie.rest_api.routers.analytics import get_user_spending

        # Mock spending data from LiteLLM
        mock_spending_data = {
            "customer_id": "testuser",
            "total_spend": 15.50,
            "max_budget": 100.0,
            "budget_duration": "30d",
            "budget_reset_at": "2026-03-01T00:00:00Z",
        }

        # Patch at the source where it's defined, not where it's imported
        with patch("codemie.enterprise.litellm.dependencies.get_customer_spending") as mock_get_spending:
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
        """Test spending endpoint when budget tracking is not available (returns N/A values)."""
        from codemie.rest_api.routers.analytics import get_user_spending

        # Patch at the source
        with patch("codemie.enterprise.litellm.dependencies.get_customer_spending") as mock_get_spending:
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

            # Current spending should be 0
            assert metrics_by_id["current_spending"]["value"] == 0.0
            assert metrics_by_id["current_spending"]["type"] == "number"

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
        from codemie.rest_api.routers.analytics import get_user_spending
        from datetime import datetime, timedelta, timezone

        # Set reset date to 10 days from now
        future_date = datetime.now(timezone.utc) + timedelta(days=10)
        reset_at = future_date.isoformat()

        mock_spending_data = {
            "customer_id": "testuser",
            "total_spend": 5.0,
            "max_budget": 50.0,
            "budget_duration": "30d",
            "budget_reset_at": reset_at,
        }

        # Patch at the source
        with patch("codemie.enterprise.litellm.dependencies.get_customer_spending") as mock_get_spending:
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
        from codemie.rest_api.routers.analytics import get_user_spending

        mock_spending_data = {
            "customer_id": "testuser",
            "total_spend": 5.0,
            "max_budget": 50.0,
            "budget_duration": "30d",
            "budget_reset_at": None,
        }

        # Patch at the source
        with patch("codemie.enterprise.litellm.dependencies.get_customer_spending") as mock_get_spending:
            mock_get_spending.return_value = mock_spending_data

            response = await get_user_spending(user=mock_user)
            response_data = json.loads(response.body)

            # Find budget_reset_at and time_until_reset metrics
            metrics = response_data["data"]["metrics"]
            metrics_dict = {m["id"]: m for m in metrics}

            # budget_reset_at and time_until_reset should be "N/A" when null
            assert metrics_dict["budget_reset_at"]["value"] == "N/A"
            assert metrics_dict["time_until_reset"]["value"] == "N/A"


class TestGetUserKeySpending:
    """Tests for GET /analytics/key_spending endpoint."""

    @pytest.mark.asyncio
    async def test_returns_grouped_key_spending_success(self, mock_user):
        """Test endpoint returns spending data grouped by USER and PROJECT keys."""
        from codemie.rest_api.routers.analytics import get_user_key_spending

        # Mock spending data - import the model
        from codemie.enterprise.litellm.models import UserKeysSpending

        mock_spending_data = UserKeysSpending(
            user_keys=[
                {
                    "key_alias": "user-key-1",
                    "total_spend": 15.5,
                    "max_budget": 100.0,
                    "budget_duration": "30d",
                    "budget_reset_at": "2026-04-01T00:00:00Z",
                },
                {
                    "key_alias": "user-key-2",
                    "total_spend": 25.0,
                    "max_budget": 50.0,
                    "budget_duration": "7d",
                    "budget_reset_at": None,
                },
            ],
            project_keys=[
                {
                    "key_alias": "project-key-1",
                    "total_spend": 50.0,
                    "max_budget": 200.0,
                    "budget_duration": "30d",
                    "budget_reset_at": "2026-04-15T00:00:00Z",
                }
            ],
        )

        with patch("codemie.enterprise.litellm.dependencies.get_user_keys_spending") as mock_get_spending:
            mock_get_spending.return_value = mock_spending_data

            response = await get_user_key_spending(user=mock_user)

            # Response is now a Pydantic model, convert to dict for assertions
            response_data = response.model_dump(by_alias=True)

            # Verify structure
            assert "data" in response_data
            assert "user_keys" in response_data["data"]
            assert "project_keys" in response_data["data"]
            assert "metadata" in response_data

            # Verify user keys
            user_keys = response_data["data"]["user_keys"]
            assert len(user_keys) == 2
            assert user_keys[0]["key_identifier"] == "user-key-1"
            assert len(user_keys[0]["metrics"]) > 0

            # Verify project keys
            project_keys = response_data["data"]["project_keys"]
            assert len(project_keys) == 1
            assert project_keys[0]["key_identifier"] == "project-key-1"

            # Verify function was called once with correct user_id and on_raise=True
            # Note: project list order may vary, so we check individual params
            mock_get_spending.assert_called_once()
            call_args = mock_get_spending.call_args
            assert call_args[0][0] == mock_user.id
            assert set(call_args[0][1]) == set(mock_user.project_names)
            assert call_args[0][2] is True

    @pytest.mark.asyncio
    async def test_handles_empty_keys_spending(self, mock_user):
        """Test endpoint handles empty spending data gracefully."""
        from codemie.rest_api.routers.analytics import get_user_key_spending
        from codemie.enterprise.litellm.models import UserKeysSpending

        mock_spending_data = UserKeysSpending(user_keys=[], project_keys=[])

        with patch("codemie.enterprise.litellm.dependencies.get_user_keys_spending") as mock_get_spending:
            mock_get_spending.return_value = mock_spending_data

            response = await get_user_key_spending(user=mock_user)

            # Response is now a Pydantic model, convert to dict for assertions
            response_data = response.model_dump(by_alias=True)

            # Verify empty arrays are returned
            assert response_data["data"]["user_keys"] == []
            assert response_data["data"]["project_keys"] == []

    @pytest.mark.asyncio
    async def test_raises_500_on_backend_error(self, mock_user):
        """Test endpoint returns 500 error when backend fails."""
        from codemie.rest_api.routers.analytics import get_user_key_spending

        with patch("codemie.enterprise.litellm.dependencies.get_user_keys_spending") as mock_get_spending:
            mock_get_spending.side_effect = Exception("Backend service unavailable")

            with pytest.raises(ExtendedHTTPException) as exc_info:
                await get_user_key_spending(user=mock_user)

            # Verify error details
            exception = exc_info.value
            assert exception.code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Unable to retrieve spending information" in exception.message

    @pytest.mark.asyncio
    async def test_handles_none_budget_reset_at(self, mock_user):
        """Test endpoint handles None budget_reset_at values correctly."""
        from codemie.rest_api.routers.analytics import get_user_key_spending
        from codemie.enterprise.litellm.models import UserKeysSpending

        mock_spending_data = UserKeysSpending(
            user_keys=[
                {
                    "key_alias": "user-key-1",
                    "total_spend": 10.0,
                    "max_budget": 100.0,
                    "budget_duration": "30d",
                    "budget_reset_at": None,
                }
            ],
            project_keys=[],
        )

        with patch("codemie.enterprise.litellm.dependencies.get_user_keys_spending") as mock_get_spending:
            mock_get_spending.return_value = mock_spending_data

            response = await get_user_key_spending(user=mock_user)

            # Response is now a Pydantic model, convert to dict for assertions
            response_data = response.model_dump(by_alias=True)

            # Find the metrics for the key
            user_keys = response_data["data"]["user_keys"]
            assert len(user_keys) == 1

            metrics_dict = {m["id"]: m for m in user_keys[0]["metrics"]}

            # budget_reset_at and time_until_reset should be "N/A" when None
            assert metrics_dict["budget_reset_at"]["value"] == "N/A"
            assert metrics_dict["time_until_reset"]["value"] == "N/A"

    @pytest.mark.asyncio
    async def test_response_metadata_contains_required_fields(self, mock_user):
        """Test that response metadata contains all required fields."""
        from codemie.rest_api.routers.analytics import get_user_key_spending
        from codemie.enterprise.litellm.models import UserKeysSpending

        mock_spending_data = UserKeysSpending(user_keys=[], project_keys=[])

        with patch("codemie.enterprise.litellm.dependencies.get_user_keys_spending") as mock_get_spending:
            mock_get_spending.return_value = mock_spending_data

            response = await get_user_key_spending(user=mock_user)

            # Response is now a Pydantic model, convert to dict for assertions
            response_data = response.model_dump(by_alias=True)

            # Verify metadata structure
            metadata = response_data["metadata"]
            assert "timestamp" in metadata
            assert "data_as_of" in metadata
            assert "filters_applied" in metadata
            assert "execution_time_ms" in metadata
            assert isinstance(metadata["execution_time_ms"], (int, float))
            assert metadata["execution_time_ms"] >= 0
