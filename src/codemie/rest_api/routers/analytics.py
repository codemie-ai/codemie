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

"""Analytics API router for dashboard metrics and reporting.

This module provides REST API endpoints for querying analytics data with
role-based access control and flexible filtering options.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from functools import wraps
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, Query, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from codemie.configs.config import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.analytics import (
    AnalyticsDetailResponse,
    SummariesResponse,
    TabularResponse,
    UsersListResponse,
)
from codemie.rest_api.security.authentication import admin_access_only, authenticate
from codemie.rest_api.security.user import User
from codemie.service.analytics.analytics_service import AnalyticsService
from codemie.service.analytics.queries.ai_adoption_framework.config import AIAdoptionConfig

logger = logging.getLogger(__name__)

# Query parameter descriptions
PROJECTS_FILTER_ADMIN_DESC = "Filter by projects (comma-separated, admin only)"
USERS_FILTER_DESC = "Filter by users (comma-separated user IDs)"
PROJECTS_FILTER_DESC = "Filter by projects (comma-separated project names)"

ERROR_MSG_PROJECT_EMPTY = "project field cannot be empty or whitespace-only"
ERROR_MSG_ACCESS_DENIED = "Access denied"
ERROR_MSG_ADMIN_HELP = "Contact your administrator to request access to this project."
LEADERBOARD_SNAPSHOT_ID_DESC = "Specific snapshot ID (defaults to latest)"
LEADERBOARD_VIEW_PATTERN = "^(current|monthly|quarterly)$"
LEADERBOARD_VIEW_DESC = "Leaderboard view: current, monthly, quarterly"
LEADERBOARD_SEASON_KEY_DESC = "Optional season key for seasonal views, e.g. 2026-03 or 2026-Q1"


# Request models for AI Adoption Framework queries
class AiAdoptionQueryRequest(BaseModel):
    """Request model for AI adoption queries with optional custom configuration."""

    projects: list[str] | None = None
    config: dict | None = None  # Accept nested dict from frontend, convert to AIAdoptionConfig later


class AiAdoptionTabularQueryRequest(BaseModel):
    """Request model for AI adoption tabular queries with pagination and custom configuration."""

    projects: list[str] | None = None
    page: int = 0
    per_page: int = 20
    config: dict | None = None  # Accept nested dict from frontend, convert to AIAdoptionConfig later


class UserEngagementUsersQueryRequest(BaseModel):
    """Request model for user-level drill-down of User Engagement dimension.

    Returns individual user details for a single project.
    """

    project: str  # Single project (required)
    page: int = 0
    per_page: int = 20

    # Optional filters
    user_type: Literal['power_user', 'engaged', 'occasional', 'new', 'inactive'] | None = None
    activity_level: Literal['daily', 'weekly', 'monthly', 'inactive'] | None = None
    multi_assistant_only: bool | None = None

    # Sorting
    sort_by: Literal['engagement_score', 'total_interactions', 'last_used', 'user_name'] = 'engagement_score'
    sort_order: Literal['asc', 'desc'] = 'desc'

    # Config override (reuse existing pattern)
    config: dict | None = None

    @field_validator('project')
    @classmethod
    def validate_project_not_empty(cls, v: str) -> str:
        """Ensure project is not empty or whitespace-only."""
        if not v or not v.strip():
            raise RequestValidationError(
                [
                    {
                        "loc": ["body", "project"],
                        "msg": ERROR_MSG_PROJECT_EMPTY,
                        "type": "value_error",
                    }
                ]
            )
        return v.strip()


class AssistantReusabilityDetailRequest(BaseModel):
    """Request model for assistant-level drill-down of Asset Reusability dimension.

    Returns individual assistant details for a single project.
    """

    project: str  # Single project (required)
    page: int = 0
    per_page: int = 20

    # Optional filters
    status: Literal['active', 'inactive'] | None = None
    adoption: Literal['team_adopted', 'single_user'] | None = None

    # Sorting
    sort_by: Literal['total_usage', 'unique_users', 'last_used', 'assistant_name', 'created_date'] = 'total_usage'
    sort_order: Literal['asc', 'desc'] = 'desc'

    # Config override (reuse existing pattern)
    config: dict | None = None

    @field_validator('project')
    @classmethod
    def validate_project_not_empty(cls, v: str) -> str:
        """Ensure project is not empty or whitespace-only."""
        if not v or not v.strip():
            raise RequestValidationError(
                [
                    {
                        "loc": ["body", "project"],
                        "msg": ERROR_MSG_PROJECT_EMPTY,
                        "type": "value_error",
                    }
                ]
            )
        return v.strip()


class WorkflowReusabilityDetailRequest(BaseModel):
    """Request model for workflow-level drill-down of Asset Reusability dimension.

    Returns individual workflow details for a single project.
    """

    project: str  # Single project (required)
    page: int = 0
    per_page: int = 20

    # Optional filters
    status: Literal['active', 'inactive'] | None = None
    reuse: Literal['multi_user', 'single_user'] | None = None

    # Sorting
    sort_by: Literal['execution_count', 'unique_users', 'last_executed', 'workflow_name', 'created_date'] = (
        'execution_count'
    )
    sort_order: Literal['asc', 'desc'] = 'desc'

    # Config override (reuse existing pattern)
    config: dict | None = None

    @field_validator('project')
    @classmethod
    def validate_project_not_empty(cls, v: str) -> str:
        """Ensure project is not empty or whitespace-only."""
        if not v or not v.strip():
            raise RequestValidationError(
                [
                    {
                        "loc": ["body", "project"],
                        "msg": ERROR_MSG_PROJECT_EMPTY,
                        "type": "value_error",
                    }
                ]
            )
        return v.strip()


class DatasourceReusabilityDetailRequest(BaseModel):
    """Request model for datasource-level drill-down of Asset Reusability dimension.

    Returns individual datasource details for a single project.
    """

    project: str  # Single project (required)
    page: int = 0
    per_page: int = 20

    # Optional filters
    status: Literal['active', 'inactive'] | None = None
    shared: Literal['shared', 'single'] | None = None
    type: str | None = None  # datasource type (git, confluence, jira, etc)

    # Sorting
    sort_by: Literal['assistant_count', 'max_usage', 'last_indexed', 'datasource_name', 'created_date'] = (
        'assistant_count'
    )
    sort_order: Literal['asc', 'desc'] = 'desc'

    # Config override (reuse existing pattern)
    config: dict | None = None

    @field_validator('project')
    @classmethod
    def validate_project_not_empty(cls, v: str) -> str:
        """Ensure project is not empty or whitespace-only."""
        if not v or not v.strip():
            raise RequestValidationError(
                [
                    {
                        "loc": ["body", "project"],
                        "msg": ERROR_MSG_PROJECT_EMPTY,
                        "type": "value_error",
                    }
                ]
            )
        return v.strip()


def _parse_config_from_request(config_dict: dict | None) -> AIAdoptionConfig | None:
    """Parse config dict from request body into AIAdoptionConfig instance.

    Frontend sends nested structure with value/description objects.
    Backend needs flat Pydantic model.

    Args:
        config_dict: Nested dict from frontend or None

    Returns:
        AIAdoptionConfig instance or None
    """
    if config_dict is None:
        return None

    try:
        return AIAdoptionConfig.from_dict(config_dict)
    except Exception as e:
        logger.warning(f"Could not parse config from request: {e}")
        return None


def _format_config_for_log(config: AIAdoptionConfig | None) -> str:
    """Format config for logging (dump entire config as JSON).

    Args:
        config: Configuration object or None

    Returns:
        String representation of config for logging
    """
    if config is None:
        return "None"

    try:
        # Convert Pydantic model to dict and then to JSON string (single line)
        config_dict = config.model_dump() if hasattr(config, "model_dump") else config.dict()
        return json.dumps(config_dict)
    except Exception as e:
        logger.warning(f"Could not serialize config for logging: {e}")
        return f"<unparseable: {type(config).__name__}>"


def handle_analytics_errors(endpoint_name: str) -> Callable:
    """Decorator to handle common analytics endpoint errors.

    Eliminates duplicate exception handling across all analytics endpoints.

    Args:
        endpoint_name: Human-readable name of the endpoint for error messages

    Returns:
        Decorator function that wraps the endpoint handler
    """

    def decorator(func: Callable[..., Awaitable[JSONResponse]]) -> Callable[..., Awaitable[JSONResponse]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> JSONResponse:
            # Extract user from kwargs for logging (injected by Depends(authenticate))
            user = kwargs.get("user")
            user_id = user.id if user else "unknown"

            try:
                return await func(*args, **kwargs)

            except ExtendedHTTPException:
                # Re-raise ExtendedHTTPException as-is (already has proper code, message, details)
                # This allows repository and service layers to set specific error codes
                raise

            except ValueError as e:
                logger.warning(f"Invalid parameters for {endpoint_name}: {str(e)}")
                raise ExtendedHTTPException(
                    code=status.HTTP_400_BAD_REQUEST,
                    message="Invalid request parameters",
                    details=str(e),
                    help="Please check your time_period, start_date, end_date, or date range values.",
                ) from e

            except Exception as e:
                logger.exception(f"Failed to get {endpoint_name} for user {user_id}: {str(e)}")
                raise ExtendedHTTPException(
                    code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    message=f"Failed to retrieve {endpoint_name}",
                    details=f"An error occurred: {str(e)}",
                    help="Please try again or contact support if the issue persists.",
                ) from e

        return wrapper

    return decorator


router = APIRouter(tags=["Dashboard Analytics"], prefix="/v1/analytics", dependencies=[Depends(authenticate)])


# Constants for spending analytics
_SPENDING_METRIC_DEFINITIONS = {
    "current_spending": {
        "label": "Current Spending ($)",
        "description": "Total amount spent in current budget period",
    },
    "budget_limit": {
        "label": "Budget Limit ($)",
        "description": "Soft budget limit (warning threshold)",
    },
    "budget_reset_at": {
        "label": "Budget Reset Date",
        "description": "Timestamp when budget will reset",
    },
    "time_until_reset": {
        "label": "Time Until Reset",
        "description": "Time remaining until budget resets",
    },
}


def _get_user_all_projects(user: User) -> list[str]:
    """Get all projects user has access to (regular + admin).

    Args:
        user: Authenticated user object

    Returns:
        List of all project names user can access
    """
    return list(set(user.project_names) | set(user.admin_project_names))


def _build_spending_metric(
    metric_id: str, value: Any, value_type: str = "string", format_type: str | None = None
) -> dict:
    """Build a spending metric dictionary with consistent structure.

    Args:
        metric_id: Unique identifier for the metric
        value: The metric value (can be number, string, date, or N/A)
        value_type: Type of value (number, string, date)
        format_type: Optional format hint (currency, timestamp)

    Returns:
        Metric dictionary with id, label, type, value, format, and description
    """
    definition = _SPENDING_METRIC_DEFINITIONS.get(metric_id, {})
    metric = {
        "id": metric_id,
        "label": definition.get("label", metric_id),
        "type": value_type,
        "value": value,
        "description": definition.get("description", ""),
    }

    if format_type:
        metric["format"] = format_type

    return metric


def _build_spending_metrics(spending_data: dict | None, user_id: str) -> list[dict]:
    """Build spending metrics list based on available data.

    Args:
        spending_data: Dictionary with spending information or None if service unavailable

    Returns:
        List of metric dictionaries
    """
    if not spending_data:
        logger.info(f"Budget tracking not available for user {user_id}")
        return [
            _build_spending_metric("current_spending", 0.0, "number", "currency"),
            _build_spending_metric("budget_limit", "N/A", "string"),
            _build_spending_metric("budget_reset_at", "N/A", "string"),
            _build_spending_metric("time_until_reset", "N/A", "string"),
        ]

    current_spending = spending_data.get("total_spend", 0.0)
    budget_limit = spending_data.get("max_budget")

    # Handle empty/None fields - show "N/A" instead
    budget_reset_at_raw = spending_data.get("budget_reset_at")
    budget_reset_at = budget_reset_at_raw if budget_reset_at_raw else "N/A"

    time_until_reset = _calculate_time_until_reset(budget_reset_at_raw) if budget_reset_at_raw else "N/A"

    logger.info(
        f"Spending analytics retrieved for user {user_id}: "
        f"spend=${current_spending:.2f}, budget={budget_limit}, time_until_reset={time_until_reset}"
    )

    # Adjust metric type/format based on whether value is N/A
    return [
        _build_spending_metric("current_spending", current_spending, "number", "currency"),
        _build_spending_metric("budget_limit", budget_limit, "number", "currency"),
        _build_spending_metric(
            "budget_reset_at",
            budget_reset_at,
            "date" if budget_reset_at_raw else "string",
            "timestamp" if budget_reset_at_raw else None,
        ),
        _build_spending_metric("time_until_reset", time_until_reset, "string"),
    ]


def _calculate_time_until_reset(budget_reset_at: str | None) -> str | None:
    """Calculate time until budget reset.

    Args:
        budget_reset_at: ISO 8601 timestamp of budget reset

    Returns:
        Time until reset formatted as "X days Y hours Zmins", or None if timestamp is invalid
    """
    if not budget_reset_at:
        return None

    try:
        from datetime import timezone

        reset_date = datetime.fromisoformat(budget_reset_at.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        delta = reset_date - now

        # Don't return negative time
        if delta.total_seconds() < 0:
            return "0 days 0 hours 0 mins"

        # Calculate days, hours, and minutes
        total_seconds = int(delta.total_seconds())
        days = total_seconds // 86400  # 86400 seconds in a day
        remaining_seconds = total_seconds % 86400
        hours = remaining_seconds // 3600  # 3600 seconds in an hour
        minutes = (remaining_seconds % 3600) // 60

        return f"{days} days {hours} hours {minutes} mins"
    except (ValueError, AttributeError) as e:
        logger.warning(f"Failed to parse budget_reset_at: {budget_reset_at}, error: {e}")
        return None


def _get_key_spending_columns() -> list[dict]:
    """Get column definitions for key spending tabular response."""
    return [
        {
            "id": "project_name",
            "label": "Project",
            "type": "string",
            "format": None,
            "description": "",
        },
        {
            "id": "current_spending",
            "label": "Current Spending ($)",
            "type": "number",
            "format": "currency",
            "description": "Total amount spent in current budget period",
        },
        {
            "id": "budget_reset_at",
            "label": "Budget Reset Date",
            "type": "string",
            "format": "timestamp",
            "description": "Timestamp when budget will reset",
        },
        {
            "id": "time_until_reset",
            "label": "Time Until Reset",
            "type": "string",
            "format": None,
            "description": "Time remaining until budget resets",
        },
        {
            "id": "budget_limit",
            "label": "Budget Limit ($)",
            "type": "number",
            "format": "currency",
            "description": "Soft budget limit (warning threshold)",
        },
        {
            "id": "total",
            "label": "Total",
            "type": "number",
            "format": "percentage",
            "description": "",
        },
    ]


def _build_spending_row(label: str, spending: dict) -> dict[str, Any]:
    """Build a single spending table row from a spending dict."""
    current = spending.get("total_spend", 0.0)
    limit = spending.get("max_budget")
    reset_at = spending.get("budget_reset_at") or None
    return {
        "project_name": label,
        "current_spending": round(current, 2),
        "budget_reset_at": reset_at,
        "time_until_reset": _calculate_time_until_reset(reset_at) if reset_at else None,
        "budget_limit": round(limit, 2) if limit is not None else None,
        "total": round(current / limit * 100, 2) if limit and limit > 0 else 0.0,
    }


def _build_key_spending_tabular_data(
    user_identifier: str,
    user_personal_spending: dict | None,
    user_proxy_spending: dict | None,
    user_premium_spending: dict | None,
    user_keys_spending: list[dict],
) -> tuple[list[dict], list[dict[str, Any]]]:
    """Build tabular data structure for key spending.

    Creates:
    - First row: User's personal budget spending (overall spending, not key-specific)
    - Subsequent rows: Individual LiteLLM key details (if any)

    Args:
        user_identifier: Best available user label to display for personal budget
        user_personal_spending: Dict with user's personal budget data from get_customer_spending()
                                Fields: total_spend, max_budget, budget_reset_at
        user_proxy_spending: Dict with user's proxy/CLI budget data from
                             get_proxy_customer_spending(); same shape as personal spending
        user_premium_spending: Dict with user's premium budget data from
                               get_premium_customer_spending(); same shape as personal spending
        user_keys_spending: List of spending dicts for USER-scoped keys only
                            Each dict already has project_name enriched by get_user_keys_spending()

    Returns:
        Tuple of (columns, rows)
    """
    budget_sources = [
        (user_identifier, user_personal_spending),
        (f"{user_identifier} (premium)", user_premium_spending),
        (f"{user_identifier} (cli)", user_proxy_spending),
    ]

    rows = [_build_spending_row(label, data) for label, data in budget_sources if data]

    for key_data in user_keys_spending:
        label = key_data.get("project_name") or key_data.get("key_alias", "Unknown Key")
        rows.append(_build_spending_row(label, key_data))

    return _get_key_spending_columns(), rows


def _create_response(data: dict, model_class) -> JSONResponse:
    """Helper to create JSON response with cache headers."""
    validated = model_class(**data)
    response_dict = validated.model_dump(by_alias=True)
    response = JSONResponse(content=response_dict, status_code=status.HTTP_200_OK)
    if config.is_local:
        response.headers["Cache-Control"] = "no-store"
    else:
        response.headers["Cache-Control"] = "public, max-age=300"
        response.headers["ETag"] = hashlib.md5(json.dumps(response_dict, sort_keys=True).encode()).hexdigest()
    return response


@router.get(
    "/summaries",
    status_code=status.HTTP_200_OK,
    response_model=SummariesResponse,
    response_model_by_alias=True,
    summary="Get summary metrics",
    description="Retrieve total input/output tokens, cached tokens, and money spent across all usage types",
)
@handle_analytics_errors("analytics summaries")
async def get_summaries(
    user: User = Depends(authenticate),
    time_period: str | None = Query(
        None,
        description=(
            "Predefined time range (last_hour, last_6_hours, last_24_hours, "
            "last_7_days, last_30_days, last_60_days, last_year)"
        ),
        examples=["last_30_days"],
    ),
    start_date: datetime | None = Query(
        None,
        description="Custom range start (ISO 8601 format). Use with end_date instead of time_period.",
        examples=["2025-11-01T00:00:00Z"],
    ),
    end_date: datetime | None = Query(
        None,
        description="Custom range end (ISO 8601 format). Use with start_date instead of time_period.",
        examples=["2025-12-01T00:00:00Z"],
    ),
    users: str | None = Query(None, description=USERS_FILTER_DESC),
    projects: str | None = Query(None, description=PROJECTS_FILTER_DESC, examples=["codemie,project-alpha"]),
) -> JSONResponse:
    """Get summary metrics: total input/output tokens, cached tokens, money spent.

    This endpoint provides aggregated metrics across all usage types (conversations, datasources, workflows).
    Users can only see data for projects they have access to.

    **Access Control:**
    - Plain users: See data for projects in their `applications` list
    - Project admins: See data for projects in `applications` + `applications_admin` lists

    **Time Filtering:**
    - Use `time_period` for predefined ranges (e.g., "last_30_days")
    - OR use `start_date` + `end_date` for custom ranges
    - If neither provided, defaults to last 30 days

    **Additional Filters:**
    - `users`: Filter by specific user IDs (must be within accessible projects)
    - `projects`: Filter by specific projects (must be within user's accessible projects)

    **Response:**
    - Returns 11 metrics including 4 spending metrics:
      - platform_cost: LLM costs from web platform (conversations + workflows, excluding CLI)
      - cli_cost: CLI-specific spending
      - embedding_cost: Embedding model usage costs
      - total_money_spent: Total spending across all categories
    - Also includes token counts and usage statistics
    - Includes metadata: timestamp, data_as_of, filters_applied, execution_time_ms

    **Caching:**
    - Response includes Cache-Control and ETag headers (5-minute TTL)
    """
    logger.info(
        f"User {user.id} requesting summaries. "
        f"Period={time_period}, StartDate={start_date}, EndDate={end_date}, "
        f"Users={users}, Projects={projects}"
    )

    service = AnalyticsService(user)
    response_data = await service.get_summaries(
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
    )

    return _create_response(response_data, SummariesResponse)


# Additional endpoints for comprehensive analytics


@router.get(
    "/assistants-chats", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True
)
@handle_analytics_errors("assistants chats analytics")
async def get_assistants_chats(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get assistants chats analytics."""
    service = AnalyticsService(user)
    data = await service.get_assistants_chats(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get("/workflows", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True)
@handle_analytics_errors("workflows analytics")
async def get_workflows(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get workflows analytics."""
    service = AnalyticsService(user)
    data = await service.get_workflows(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/tools-usage", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True
)
@handle_analytics_errors("tools usage analytics")
async def get_tools_usage(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get tools usage analytics."""
    service = AnalyticsService(user)
    data = await service.get_tools_usage(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/agents-usage", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True
)
@handle_analytics_errors("agents usage analytics")
async def get_agents_usage(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get agents usage analytics."""
    service = AnalyticsService(user)
    data = await service.get_agents_usage(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/power-users", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True
)
@handle_analytics_errors("power users analytics")
async def get_power_users(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get power users analytics."""
    service = AnalyticsService(user)
    data = await service.get_power_users(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/knowledge-sharing", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True
)
@handle_analytics_errors("knowledge sharing analytics")
async def get_knowledge_sharing(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get knowledge sharing analytics."""
    service = AnalyticsService(user)
    data = await service.get_knowledge_sharing(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/top-agents-usage", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True
)
@handle_analytics_errors("top agents usage analytics")
async def get_top_agents_usage(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get top agents usage analytics."""
    service = AnalyticsService(user)
    data = await service.get_top_agents_usage(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/top-workflow-usage",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("top workflow usage analytics")
async def get_top_workflow_usage(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get top workflow usage analytics."""
    service = AnalyticsService(user)
    data = await service.get_top_workflow_usage(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/published-to-marketplace",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("published to marketplace analytics")
async def get_published_to_marketplace(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get published to marketplace analytics."""
    service = AnalyticsService(user)
    data = await service.get_published_to_marketplace(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/webhooks-invocation", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True
)
@handle_analytics_errors("webhooks invocation analytics")
async def get_webhooks_invocation(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get webhooks invocation analytics."""
    service = AnalyticsService(user)
    data = await service.get_webhooks_invocation(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/mcp-servers", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True
)
@handle_analytics_errors("MCP servers analytics")
async def get_mcp_servers(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get MCP servers analytics."""
    service = AnalyticsService(user)
    data = await service.get_mcp_servers(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/mcp-servers-by-users",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("MCP servers by users analytics")
async def get_mcp_servers_by_users(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get MCP servers by users analytics."""
    service = AnalyticsService(user)
    data = await service.get_mcp_servers_by_users(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/projects-spending", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True
)
@handle_analytics_errors("projects spending analytics")
async def get_projects_spending(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get projects spending analytics."""
    service = AnalyticsService(user)
    data = await service.get_projects_spending(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get("/llms-usage", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True)
@handle_analytics_errors("LLMs usage analytics")
async def get_llms_usage(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get LLMs usage analytics."""
    service = AnalyticsService(user)
    data = await service.get_llms_usage(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/embeddings-usage", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True
)
@handle_analytics_errors("embeddings usage analytics")
async def get_embeddings_usage(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get embedding model usage analytics."""
    service = AnalyticsService(user)
    data = await service.get_embeddings_usage(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/users-spending", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True
)
@handle_analytics_errors("users spending analytics")
async def get_users_spending(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get users spending analytics."""
    service = AnalyticsService(user)
    data = await service.get_users_spending(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/budget-soft-limit", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True
)
@handle_analytics_errors("budget soft limit analytics")
async def get_budget_soft_limit(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get budget soft limit analytics."""
    service = AnalyticsService(user)
    data = await service.get_budget_soft_limit(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/budget-hard-limit", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True
)
@handle_analytics_errors("budget hard limit analytics")
async def get_budget_hard_limit(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get budget hard limit analytics."""
    service = AnalyticsService(user)
    data = await service.get_budget_hard_limit(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/users-activity", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True
)
@handle_analytics_errors("users activity analytics")
async def get_users_activity(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get users activity analytics."""
    service = AnalyticsService(user)
    data = await service.get_users_activity(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/users-unique-daily", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True
)
@handle_analytics_errors("unique daily users analytics")
async def get_users_unique_daily(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None, description="Predefined time range (e.g., 'last_30_days')"),
    start_date: datetime | None = Query(None, description="Custom range start date"),
    end_date: datetime | None = Query(None, description="Custom range end date"),
    users: str | None = Query(None, description="Comma-separated user IDs to filter by"),
    projects: str | None = Query(None, description="Comma-separated project names to filter by"),
) -> JSONResponse:
    """Get unique users per day analytics.

    Returns time-series data showing the number of unique active users for each day,
    based on conversation_assistant_usage metric. The endpoint aggregates user activity
    by calendar day (UTC timezone) and counts distinct users for each day.

    **IMPORTANT: All timestamps are in UTC timezone.** Input dates (start_date/end_date) must be
    UTC-aware or will be treated as UTC. Output dates are formatted in UTC timezone (YYYY-MM-DD).

    **Note:** This endpoint returns all date records without pagination (up to 10,000 days).

    Access Control:
    - Admin users can see data for all projects in their applications and applications_admin lists
    - Non-admin users can only see data for projects in their applications list

    Query Parameters:
    - time_period: Use predefined ranges like 'last_7_days', 'last_30_days', or 'last_90_days'
    - start_date/end_date: Specify custom date range in UTC (overrides time_period if provided)
    - users: Filter results to specific users (comma-separated user IDs)
    - projects: Filter results to specific projects (comma-separated project names)

    Response Format:
    - Returns TabularResponse with columns: date (YYYY-MM-DD in UTC), unique_users (count)
    - Includes metadata: timestamp, data_as_of, filters_applied, execution_time_ms
    - All date records are returned in a single response
    """
    service = AnalyticsService(user)
    data = await service.get_users_unique_daily(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/users",
    status_code=status.HTTP_200_OK,
    response_model=UsersListResponse,
    response_model_by_alias=True,
    summary="Get unique users list",
    description="Retrieve list of unique users with activity in the specified time range",
)
@handle_analytics_errors("users list")
async def get_users_list(
    user: User = Depends(authenticate),
    time_period: str | None = Query(
        None,
        description=(
            "Predefined time range (last_hour, last_6_hours, last_24_hours, "
            "last_7_days, last_30_days, last_60_days, last_year)"
        ),
        examples=["last_30_days"],
    ),
    start_date: datetime | None = Query(
        None,
        description="Custom range start (ISO 8601 format). Use with end_date instead of time_period.",
        examples=["2025-11-01T00:00:00Z"],
    ),
    end_date: datetime | None = Query(
        None,
        description="Custom range end (ISO 8601 format). Use with start_date instead of time_period.",
        examples=["2025-12-01T00:00:00Z"],
    ),
    users: str | None = Query(
        None,
        description=USERS_FILTER_DESC,
    ),
    projects: str | None = Query(None, description=PROJECTS_FILTER_DESC, examples=["codemie,project-alpha"]),
) -> JSONResponse:
    """Get list of unique users from metrics logs.

    Returns unique users with activity in the specified time range,
    respecting access control (admin sees all users from admin projects,
    plain user sees only themselves).

    **Access Control:**
    - Plain users: See only themselves
    - Project admins: See all users from projects they administer

    **Time Filtering:**
    - Use `time_period` for predefined ranges (e.g., "last_30_days")
    - OR use `start_date` + `end_date` for custom ranges
    - If neither provided, defaults to last 30 days

    **Additional Filters:**
    - `users`: Filter by specific user IDs (within access control)
    - `projects`: Filter by specific projects (within access control)

    **Response:**
    - Returns list of users with id and name
    - Includes total_count
    - Includes metadata: timestamp, data_as_of, filters_applied, execution_time_ms

    **Caching:**
    - Response includes Cache-Control and ETag headers (5-minute TTL)
    """
    logger.info(
        f"User {user.id} requesting users list. "
        f"Period={time_period}, StartDate={start_date}, EndDate={end_date}, "
        f"Users={users}, Projects={projects}"
    )

    service = AnalyticsService(user)
    response_data = await service.get_users_list(
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
    )

    return _create_response(response_data, UsersListResponse)


@router.get(
    "/projects-activity", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True
)
@handle_analytics_errors("projects activity analytics")
async def get_projects_activity(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get projects activity analytics."""
    service = AnalyticsService(user)
    data = await service.get_projects_activity(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/projects-unique-daily",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("unique daily projects analytics")
async def get_projects_unique_daily(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None, description="Predefined time range (e.g., 'last_30_days')"),
    start_date: datetime | None = Query(None, description="Custom range start date"),
    end_date: datetime | None = Query(None, description="Custom range end date"),
    users: str | None = Query(None, description="Comma-separated user IDs to filter by"),
    projects: str | None = Query(None, description="Comma-separated project names to filter by"),
) -> JSONResponse:
    """Get unique projects per day analytics.

    Returns time-series data showing the number of unique active projects for each day,
    based on conversation_assistant_usage metric. The endpoint aggregates project activity
    by calendar day (UTC timezone) and counts distinct projects for each day.

    **IMPORTANT: All timestamps are in UTC timezone.** Input dates (start_date/end_date) must be
    UTC-aware or will be treated as UTC. Output dates are formatted in UTC timezone (YYYY-MM-DD).

    **Note:** This endpoint returns all date records without pagination (up to 10,000 days).

    Access Control:
    - Admin users can see data for all projects in their applications and applications_admin lists
    - Non-admin users can only see data for projects in their applications list

    Query Parameters:
    - time_period: Use predefined ranges like 'last_7_days', 'last_30_days', or 'last_90_days'
    - start_date/end_date: Specify custom date range in UTC (overrides time_period if provided)
    - users: Filter results to specific users (comma-separated user IDs)
    - projects: Filter results to specific projects (comma-separated project names)

    Response Format:
    - Returns TabularResponse with columns: date (YYYY-MM-DD in UTC), unique_projects (count)
    - Includes metadata: timestamp, data_as_of, filters_applied, execution_time_ms
    - All date records are returned in a single response
    """
    # Validate time parameters
    if start_date and end_date and start_date > end_date:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="start_date must be before or equal to end_date"
        )

    service = AnalyticsService(user)
    data = await service.get_projects_unique_daily(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-summary", status_code=status.HTTP_200_OK, response_model=SummariesResponse, response_model_by_alias=True
)
@handle_analytics_errors("CLI summary analytics")
async def get_cli_summary(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
) -> JSONResponse:
    """Get CLI summary analytics."""
    service = AnalyticsService(user)
    data = await service.get_cli_summary(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(data, SummariesResponse)


@router.get("/cli-agents", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True)
@handle_analytics_errors("CLI agents analytics")
async def get_cli_agents(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get CLI agents analytics."""
    service = AnalyticsService(user)
    data = await service.get_cli_agents(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get("/cli-llms", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True)
@handle_analytics_errors("CLI LLMs analytics")
async def get_cli_llms(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get CLI LLMs analytics."""
    service = AnalyticsService(user)
    data = await service.get_cli_llms(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get("/cli-users", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True)
@handle_analytics_errors("CLI users analytics")
async def get_cli_users(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get CLI users analytics."""
    service = AnalyticsService(user)
    data = await service.get_cli_users(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get("/cli-errors", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True)
@handle_analytics_errors("CLI errors analytics")
async def get_cli_errors(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get CLI errors analytics."""
    service = AnalyticsService(user)
    data = await service.get_cli_errors(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-repositories", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True
)
@handle_analytics_errors("CLI repositories analytics")
async def get_cli_repositories(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get CLI repositories analytics."""
    service = AnalyticsService(user)
    data = await service.get_cli_repositories(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-top-performers", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True
)
@handle_analytics_errors("CLI top performers analytics")
async def get_cli_top_performers(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get CLI top performers ranked by total lines added."""
    service = AnalyticsService(user)
    data = await service.get_cli_top_performers(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-top-versions", status_code=status.HTTP_200_OK, response_model=TabularResponse, response_model_by_alias=True
)
@handle_analytics_errors("CLI top versions analytics")
async def get_cli_top_versions(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get CLI top versions ranked by usage count."""
    service = AnalyticsService(user)
    data = await service.get_cli_top_versions(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-top-proxy-endpoints",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI top proxy endpoints analytics")
async def get_cli_top_proxy_endpoints(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get CLI top proxy endpoints ranked by request count."""
    service = AnalyticsService(user)
    data = await service.get_cli_top_proxy_endpoints(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-tools",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI tools usage analytics")
async def get_cli_tools_usage(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get CLI tool usage analytics showing which tools are used most frequently."""
    service = AnalyticsService(user)
    data = await service.get_cli_tools_usage(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-weekday-pattern",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights weekday pattern analytics")
async def get_cli_insights_weekday_pattern(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get CLI Insights weekday pattern widget data."""
    service = AnalyticsService(user)
    data = await service.get_cli_insights_weekday_pattern(
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
        page=page,
        per_page=per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-hourly-usage",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights hourly usage analytics")
async def get_cli_insights_hourly_usage(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get CLI Insights hourly usage widget data."""
    service = AnalyticsService(user)
    data = await service.get_cli_insights_hourly_usage(
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
        page=page,
        per_page=per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-session-depth",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights session depth analytics")
async def get_cli_insights_session_depth(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get CLI Insights session depth widget data."""
    service = AnalyticsService(user)
    data = await service.get_cli_insights_session_depth(
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
        page=page,
        per_page=per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-user-classification",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights user classification analytics")
async def get_cli_insights_user_classification(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get CLI Insights user classification widget data."""
    service = AnalyticsService(user)
    data = await service.get_cli_insights_user_classification(
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
        page=page,
        per_page=per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-top-users-by-cost",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights top users by cost analytics")
async def get_cli_insights_top_users_by_cost(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get CLI Insights top users by cost widget data."""
    service = AnalyticsService(user)
    data = await service.get_cli_insights_top_users_by_cost(
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
        page=page,
        per_page=per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-top-spenders",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights top spenders analytics")
async def get_cli_insights_top_spenders(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get CLI Insights Top Spenders table data."""
    service = AnalyticsService(user)
    data = await service.get_cli_insights_top_spenders(
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
        page=page,
        per_page=per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-users",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights all users analytics")
async def get_cli_insights_all_users(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get CLI Insights all users table data."""
    service = AnalyticsService(user)
    data = await service.get_cli_insights_all_users(
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
        page=page,
        per_page=per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-user-detail",
    status_code=status.HTTP_200_OK,
    response_model=AnalyticsDetailResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights user detail analytics")
async def get_cli_insights_user_detail(
    user: User = Depends(authenticate),
    user_name: str = Query(...),
    user_id: str | None = Query(None),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
) -> JSONResponse:
    """Get CLI Insights user detail drilldown data."""
    service = AnalyticsService(user)
    normalized_user_id = user_id if isinstance(user_id, str) else None
    data = await service.get_cli_insights_user_detail(
        user_name=user_name,
        user_id=normalized_user_id,
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(data, AnalyticsDetailResponse)


@router.get(
    "/cli-insights-user-key-metrics",
    status_code=status.HTTP_200_OK,
    response_model=SummariesResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights user key metrics analytics")
async def get_cli_insights_user_key_metrics(
    user: User = Depends(authenticate),
    user_name: str = Query(...),
    user_id: str | None = Query(None),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
) -> JSONResponse:
    """Get CLI Insights user key metrics widget data."""
    service = AnalyticsService(user)
    normalized_user_id = user_id if isinstance(user_id, str) else None
    data = await service.get_cli_insights_user_key_metrics(
        user_name=user_name,
        user_id=normalized_user_id,
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(data, SummariesResponse)


@router.get(
    "/cli-insights-user-tools",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights user tools analytics")
async def get_cli_insights_user_tools(
    user: User = Depends(authenticate),
    user_name: str = Query(...),
    user_id: str | None = Query(None),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
) -> JSONResponse:
    """Get CLI Insights user tools widget data."""
    service = AnalyticsService(user)
    normalized_user_id = user_id if isinstance(user_id, str) else None
    data = await service.get_cli_insights_user_tools(
        user_name=user_name,
        user_id=normalized_user_id,
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-user-models",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights user models analytics")
async def get_cli_insights_user_models(
    user: User = Depends(authenticate),
    user_name: str = Query(...),
    user_id: str | None = Query(None),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
) -> JSONResponse:
    """Get CLI Insights user models widget data."""
    service = AnalyticsService(user)
    normalized_user_id = user_id if isinstance(user_id, str) else None
    data = await service.get_cli_insights_user_models(
        user_name=user_name,
        user_id=normalized_user_id,
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-user-workflow-intent",
    status_code=status.HTTP_200_OK,
    response_model=SummariesResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights user workflow intent analytics")
async def get_cli_insights_user_workflow_intent(
    user: User = Depends(authenticate),
    user_name: str = Query(...),
    user_id: str | None = Query(None),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
) -> JSONResponse:
    """Get CLI Insights user workflow intent widget data."""
    service = AnalyticsService(user)
    normalized_user_id = user_id if isinstance(user_id, str) else None
    data = await service.get_cli_insights_user_workflow_intent(
        user_name=user_name,
        user_id=normalized_user_id,
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(data, SummariesResponse)


@router.get(
    "/cli-insights-user-classification-detail",
    status_code=status.HTTP_200_OK,
    response_model=SummariesResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights user classification detail analytics")
async def get_cli_insights_user_classification_detail(
    user: User = Depends(authenticate),
    user_name: str = Query(...),
    user_id: str | None = Query(None),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
) -> JSONResponse:
    """Get CLI Insights user classification widget data."""
    service = AnalyticsService(user)
    normalized_user_id = user_id if isinstance(user_id, str) else None
    data = await service.get_cli_insights_user_classification_detail(
        user_name=user_name,
        user_id=normalized_user_id,
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(data, SummariesResponse)


@router.get(
    "/cli-insights-user-category-breakdown",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights user category breakdown analytics")
async def get_cli_insights_user_category_breakdown(
    user: User = Depends(authenticate),
    user_name: str = Query(...),
    user_id: str | None = Query(None),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
) -> JSONResponse:
    """Get CLI Insights user category breakdown widget data."""
    service = AnalyticsService(user)
    normalized_user_id = user_id if isinstance(user_id, str) else None
    data = await service.get_cli_insights_user_category_breakdown(
        user_name=user_name,
        user_id=normalized_user_id,
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-user-repositories",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights user repositories analytics")
async def get_cli_insights_user_repositories(
    user: User = Depends(authenticate),
    user_name: str = Query(...),
    user_id: str | None = Query(None),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
) -> JSONResponse:
    """Get CLI Insights user repositories widget data."""
    service = AnalyticsService(user)
    normalized_user_id = user_id if isinstance(user_id, str) else None
    data = await service.get_cli_insights_user_repositories(
        user_name=user_name,
        user_id=normalized_user_id,
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-project-classification",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights project classification analytics")
async def get_cli_insights_project_classification(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get CLI Insights project classification widget data."""
    service = AnalyticsService(user)
    data = await service.get_cli_insights_project_classification(
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
        page=page,
        per_page=per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-top-projects-by-cost",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights top projects by cost analytics")
async def get_cli_insights_top_projects_by_cost(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get CLI Insights top projects by cost widget data."""
    service = AnalyticsService(user)
    data = await service.get_cli_insights_top_projects_by_cost(
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
        page=page,
        per_page=per_page,
    )
    return _create_response(data, TabularResponse)


@router.post(
    "/ai-adoption-overview",
    status_code=status.HTTP_200_OK,
    response_model=SummariesResponse,
    response_model_by_alias=True,
    summary="Get AI adoption overview metrics for dashboard (with custom config)",
    description=(
        "Get aggregate counts: Projects, Users, Assistants, Workflows, Datasources with optional custom configuration"
    ),
)
@handle_analytics_errors("AI adoption overview metrics")
async def post_ai_adoption_overview(
    request: AiAdoptionQueryRequest = Body(...),
    user: User = Depends(authenticate),
) -> JSONResponse:
    """Get overview metrics for dashboard widgets with custom configuration.

    Returns aggregate counts across all accessible projects for the 5 main dashboard widgets.

    **Metrics:**
    - Total Projects: Number of projects being tracked (respects minimum_users_threshold from config)
    - Total Users: Total unique users across all filtered projects
    - Total Assistants: Total assistants created in filtered projects
    - Total Workflows: Total workflows created in filtered projects
    - Total Datasources: Total datasources configured in filtered projects

    **Access Control:**
    - Admin: See data for all projects or filtered projects
    - Non-Admin: See data for projects in their applications list

    **Request Body:**
    - projects: Optional list of project names to filter
    - config: Optional custom AIAdoptionConfig (weights, thresholds, scoring rules)

    **Response:**
    - Returns SummariesResponse with 5 metrics
    - Includes metadata: timestamp, data_as_of, filters_applied, execution_time_ms
    - If config is provided, calculations use custom parameters (especially minimum_users_threshold)
    - If config is null/omitted, uses default configuration

    **Caching:**
    - Response includes Cache-Control and ETag headers (5-minute TTL)
    """
    # Parse config from nested dict structure to AIAdoptionConfig
    parsed_config = _parse_config_from_request(request.config)
    config_summary = _format_config_for_log(parsed_config)
    logger.info(f"User {user.id} requesting overview metrics. Projects={request.projects}, Config={config_summary}")

    service = AnalyticsService(user)
    response_data = await service.get_ai_adoption_overview(
        projects=request.projects,
        config=parsed_config,
    )

    return _create_response(response_data, SummariesResponse)


@router.post(
    "/ai-adoption-maturity",
    status_code=status.HTTP_200_OK,
    response_model=SummariesResponse,
    response_model_by_alias=True,
    summary="Get AI Adoption Maturity (with custom config)",
    description="Aggregated composite scores with optional custom configuration",
)
@handle_analytics_errors("AI adoption maturity")
async def post_ai_adoption_maturity(
    request: AiAdoptionQueryRequest = Body(...),
    user: User = Depends(authenticate),
) -> JSONResponse:
    """Get AI Maturity Overview with custom configuration.

    Same as GET /ai-adoption-maturity but accepts custom configuration in request body.
    This allows real-time calculations with user-defined weights and thresholds.

    **Request Body:**
    - projects: Optional list of project names to filter
    - config: Optional custom AIAdoptionConfig (weights, thresholds, scoring rules)

    **Returns:**
    - Same response as GET endpoint: SummariesResponse with 6 metrics
    - If config is provided, calculations use custom parameters
    - If config is null/omitted, uses default configuration
    """
    # Parse config from nested dict structure to AIAdoptionConfig
    parsed_config = _parse_config_from_request(request.config)
    config_summary = _format_config_for_log(parsed_config)
    logger.info(f"User {user.id} requesting AI maturity. Projects={request.projects}, Config={config_summary}")

    service = AnalyticsService(user)
    response_data = await service.get_ai_adoption_maturity(
        projects=request.projects,
        config=parsed_config,
    )

    return _create_response(response_data, SummariesResponse)


@router.get(
    "/ai-adoption-config",
    status_code=status.HTTP_200_OK,
    summary="Get AI Adoption Framework Configuration",
    description="Framework weights, thresholds, and scoring parameters",
)
@handle_analytics_errors("AI adoption config")
async def get_ai_adoption_config(
    user: User = Depends(authenticate),
) -> JSONResponse:
    """Get AI Adoption Framework configuration parameters.

    Returns all weights, thresholds, and scoring rules used in the adoption framework.
    No access control - configuration is public information.

    **Returns:**
    - data: Nested configuration organized by dimension (D1-D4)
    - metadata: Timestamp and version information

    **Configuration includes:**

    **AI Maturity:**
    - Activation threshold
    - Maturity level thresholds (L2, L3)
    - Dimension weights (D1: 30%, D2: 30%, D3: 20%, D4: 20%)

    **D1: Daily Active Users:**
    - Component weights (activation, MAU, multi-assistant)
    - Activity window parameters

    **D2: Reusability:**
    - Component weights (team adoption, workflow reuse, datasource reuse)
    - Activation thresholds

    **D3: AI Champions:**
    - Component weights (concentration, non-champion activity, creator diversity)
    - Concentration thresholds and scores
    - Non-champion activity multipliers

    **D4: AI Capabilities:**
    - Component weights (workflow count, complexity, conversation depth)
    - Complexity levels (simple, basic, advanced, complex)
    - Workflow count thresholds

    **Use Cases:**
    - Understanding score calculations
    - Validating scoring logic
    - Building UI explanations
    - Debugging score discrepancies
    """
    logger.info(f"User {user.id} requesting AI adoption framework configuration")

    service = AnalyticsService(user)
    response_data = await service.get_ai_adoption_config()

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=response_data,
    )


@router.post(
    "/ai-adoption-user-engagement",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get AI Adoption User Engagement Metrics (with custom config)",
    description="Project-level User Engagement metrics with optional custom configuration",
)
@handle_analytics_errors("AI adoption user engagement metrics")
async def post_ai_adoption_user_engagement(
    request: AiAdoptionTabularQueryRequest = Body(...),
    user: User = Depends(authenticate),
) -> JSONResponse:
    """Get User Engagement metrics with custom configuration.

    Same as GET endpoint but accepts custom configuration in request body.
    """
    # Parse config from nested dict structure to AIAdoptionConfig
    parsed_config = _parse_config_from_request(request.config)
    config_summary = _format_config_for_log(parsed_config)
    logger.info(
        f"User {user.id} requesting User Engagement. "
        f"Projects={request.projects}, Page={request.page}, Config={config_summary}"
    )

    service = AnalyticsService(user)
    response_data = await service.get_ai_adoption_user_engagement(
        projects=request.projects,
        page=request.page,
        per_page=request.per_page,
        config=parsed_config,
    )

    return _create_response(response_data, TabularResponse)


@router.post(
    "/ai-adoption-user-engagement/users",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,  # Reuse existing response model
    response_model_by_alias=True,
    summary="Get User Engagement User-Level Drill-Down",
    description="Individual user statistics for a single project in the User Engagement dimension",
)
@handle_analytics_errors("AI adoption user engagement drill-down")
async def post_ai_adoption_user_engagement_users(
    request: UserEngagementUsersQueryRequest = Body(...),
    user: User = Depends(authenticate),
) -> JSONResponse:
    """Get user-level drill-down for User Engagement dimension.

    Returns detailed statistics for individual users within a specific project:
    - User activity classifications (power user, engaged, occasional, etc.)
    - Engagement scores and interaction counts
    - Multi-assistant usage patterns
    - Recency metrics (daily/weekly/monthly active status)

    **Required:**
    - `project`: Single project identifier (user must have access)

    **Optional Filters:**
    - `user_type`: Filter by user classification
    - `activity_level`: Filter by activity recency
    - `multi_assistant_only`: Filter for multi-assistant users

    **Sorting:**
    - `sort_by`: Column to sort by (default: engagement_score)
    - `sort_order`: Sort direction (default: desc)

    **Access Control:**
    - Users can only drill down into projects they have access to
    - Admins can access all projects

    **Example Request:**
    ```json
    {
      "project": "demo",
      "page": 0,
      "per_page": 20,
      "user_type": "power_user",
      "sort_by": "engagement_score"
    }
    ```
    """
    # Validate user has access to project
    if not user.is_admin:
        # Get user's accessible projects
        accessible_projects = set(user.project_names or []) | set(user.admin_project_names or [])
        if request.project not in accessible_projects:
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message=ERROR_MSG_ACCESS_DENIED,
                details=f"You do not have access to project '{request.project}'",
                help=ERROR_MSG_ADMIN_HELP,
            )

    # Parse optional config (reuse existing pattern)
    parsed_config = _parse_config_from_request(request.config)
    config_summary = _format_config_for_log(parsed_config)

    logger.info(
        f"User {user.id} requesting User Engagement drill-down. "
        f"Project={request.project}, Page={request.page}, "
        f"Filters=(type={request.user_type}, activity={request.activity_level}, "
        f"multi_assistant={request.multi_assistant_only}), "
        f"Sort={request.sort_by}:{request.sort_order}, Config={config_summary}"
    )

    # Call service layer
    service = AnalyticsService(user)
    response_data = await service.get_user_engagement_users(
        project=request.project,
        page=request.page,
        per_page=request.per_page,
        user_type=request.user_type,
        activity_level=request.activity_level,
        multi_assistant_only=request.multi_assistant_only,
        sort_by=request.sort_by,
        sort_order=request.sort_order,
        config=parsed_config,
    )

    return _create_response(response_data, TabularResponse)


@router.post(
    "/ai-adoption-asset-reusability/assistants",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get Asset Reusability Assistant-Level Drill-Down",
    description="Individual assistant statistics for a single project in the Asset Reusability dimension",
)
@handle_analytics_errors("AI adoption assistant reusability drill-down")
async def post_ai_adoption_assistant_reusability_detail(
    request: AssistantReusabilityDetailRequest = Body(...),
    user: User = Depends(authenticate),
) -> JSONResponse:
    """Get assistant-level drill-down for Asset Reusability dimension.

    Returns detailed statistics for individual assistants within a specific project:
    - Usage metrics (total usage, unique users)
    - Status (active/inactive based on activation threshold)
    - Adoption (team-adopted/single-user based on team threshold)
    - Feature configuration (datasources, toolkits, MCP servers)
    - Complexity classification (simple/basic/advanced/complex)
    - Creator information

    **Required:**
    - `project`: Single project identifier (user must have access)

    **Optional Filters:**
    - `status`: Filter by active/inactive status
    - `adoption`: Filter by team-adopted/single-user

    **Sorting:**
    - `sort_by`: Column to sort by (default: total_usage)
    - `sort_order`: Sort direction (default: desc)

    **Access Control:**
    - Users can only drill down into projects they have access to
    - Admins can access all projects

    **Example Request:**
    ```json
    {
      "project": "demo",
      "page": 0,
      "per_page": 20,
      "status": "inactive",
      "sort_by": "last_used"
    }
    ```
    """
    # Validate user has access to project
    if not user.is_admin:
        accessible_projects = set(user.project_names or []) | set(user.admin_project_names or [])
        if request.project not in accessible_projects:
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message=ERROR_MSG_ACCESS_DENIED,
                details=f"You do not have access to project '{request.project}'",
                help=ERROR_MSG_ADMIN_HELP,
            )

    # Parse optional config
    parsed_config = _parse_config_from_request(request.config)
    config_summary = _format_config_for_log(parsed_config)

    logger.info(
        f"User {user.id} requesting Assistant Reusability drill-down. "
        f"Project={request.project}, Page={request.page}, "
        f"Filters=(status={request.status}, adoption={request.adoption}), "
        f"Sort={request.sort_by}:{request.sort_order}, Config={config_summary}"
    )

    # Call service layer
    service = AnalyticsService(user)
    response_data = await service.get_assistant_reusability_detail(
        project=request.project,
        page=request.page,
        per_page=request.per_page,
        status=request.status,
        adoption=request.adoption,
        sort_by=request.sort_by,
        sort_order=request.sort_order,
        config=parsed_config,
    )

    return _create_response(response_data, TabularResponse)


@router.post(
    "/ai-adoption-asset-reusability/workflows",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get Asset Reusability Workflow-Level Drill-Down",
    description="Individual workflow statistics for a single project in the Asset Reusability dimension",
)
@handle_analytics_errors("AI adoption workflow reusability drill-down")
async def post_ai_adoption_workflow_reusability_detail(
    request: WorkflowReusabilityDetailRequest = Body(...),
    user: User = Depends(authenticate),
) -> JSONResponse:
    """Get workflow-level drill-down for Asset Reusability dimension.

    Returns detailed statistics for individual workflows within a specific project:
    - Execution metrics (execution count, unique users)
    - Status (active/inactive based on execution threshold)
    - Reuse (multi-user/single-user based on user threshold)
    - Component counts (states, tools, custom nodes)
    - Creator information

    **Required:**
    - `project`: Single project identifier (user must have access)

    **Optional Filters:**
    - `status`: Filter by active/inactive status
    - `reuse`: Filter by multi-user/single-user

    **Sorting:**
    - `sort_by`: Column to sort by (default: execution_count)
    - `sort_order`: Sort direction (default: desc)

    **Access Control:**
    - Users can only drill down into projects they have access to
    - Admins can access all projects

    **Example Request:**
    ```json
    {
      "project": "demo",
      "page": 0,
      "per_page": 20,
      "status": "inactive",
      "sort_by": "last_executed"
    }
    ```
    """
    # Validate user has access to project
    if not user.is_admin:
        accessible_projects = set(user.project_names or []) | set(user.admin_project_names or [])
        if request.project not in accessible_projects:
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message=ERROR_MSG_ACCESS_DENIED,
                details=f"You do not have access to project '{request.project}'",
                help=ERROR_MSG_ADMIN_HELP,
            )

    # Parse optional config
    parsed_config = _parse_config_from_request(request.config)
    config_summary = _format_config_for_log(parsed_config)

    logger.info(
        f"User {user.id} requesting Workflow Reusability drill-down. "
        f"Project={request.project}, Page={request.page}, "
        f"Filters=(status={request.status}, reuse={request.reuse}), "
        f"Sort={request.sort_by}:{request.sort_order}, Config={config_summary}"
    )

    # Call service layer
    service = AnalyticsService(user)
    response_data = await service.get_workflow_reusability_detail(
        project=request.project,
        page=request.page,
        per_page=request.per_page,
        status=request.status,
        reuse=request.reuse,
        sort_by=request.sort_by,
        sort_order=request.sort_order,
        config=parsed_config,
    )

    return _create_response(response_data, TabularResponse)


@router.post(
    "/ai-adoption-asset-reusability/datasources",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get Asset Reusability Datasource-Level Drill-Down",
    description="Individual datasource statistics for a single project in the Asset Reusability dimension",
)
@handle_analytics_errors("AI adoption datasource reusability drill-down")
async def post_ai_adoption_datasource_reusability_detail(
    request: DatasourceReusabilityDetailRequest = Body(...),
    user: User = Depends(authenticate),
) -> JSONResponse:
    """Get datasource-level drill-down for Asset Reusability dimension.

    Returns detailed statistics for individual datasources within a specific project:
    - Usage metrics (assistant count, max usage)
    - Status (active/inactive based on usage threshold)
    - Sharing (shared/single based on assistant count)
    - Type classification (git, confluence, jira, etc)
    - Last indexed date
    - Creator information

    **Required:**
    - `project`: Single project identifier (user must have access)

    **Optional Filters:**
    - `status`: Filter by active/inactive status
    - `shared`: Filter by shared/single
    - `type`: Filter by datasource type

    **Sorting:**
    - `sort_by`: Column to sort by (default: assistant_count)
    - `sort_order`: Sort direction (default: desc)

    **Access Control:**
    - Users can only drill down into projects they have access to
    - Admins can access all projects

    **Example Request:**
    ```json
    {
      "project": "demo",
      "page": 0,
      "per_page": 20,
      "status": "inactive",
      "sort_by": "last_indexed"
    }
    ```
    """
    # Validate user has access to project
    if not user.is_admin:
        accessible_projects = set(user.project_names or []) | set(user.admin_project_names or [])
        if request.project not in accessible_projects:
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message=ERROR_MSG_ACCESS_DENIED,
                details=f"You do not have access to project '{request.project}'",
                help=ERROR_MSG_ADMIN_HELP,
            )

    # Parse optional config
    parsed_config = _parse_config_from_request(request.config)
    config_summary = _format_config_for_log(parsed_config)

    logger.info(
        f"User {user.id} requesting Datasource Reusability drill-down. "
        f"Project={request.project}, Page={request.page}, "
        f"Filters=(status={request.status}, shared={request.shared}, "
        f"type={request.type}), "
        f"Sort={request.sort_by}:{request.sort_order}, Config={config_summary}"
    )

    # Call service layer
    service = AnalyticsService(user)
    response_data = await service.get_datasource_reusability_detail(
        project=request.project,
        page=request.page,
        per_page=request.per_page,
        status=request.status,
        shared=request.shared,
        type=request.type,
        sort_by=request.sort_by,
        sort_order=request.sort_order,
        config=parsed_config,
    )

    return _create_response(response_data, TabularResponse)


@router.post(
    "/ai-adoption-asset-reusability",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get AI Adoption Asset Reusability Metrics (with custom config)",
    description="Project-level Asset Reusability metrics with optional custom configuration",
)
@handle_analytics_errors("AI adoption asset reusability metrics")
async def post_ai_adoption_asset_reusability(
    request: AiAdoptionTabularQueryRequest = Body(...),
    user: User = Depends(authenticate),
) -> JSONResponse:
    """Get Asset Reusability metrics with custom configuration.

    Same as GET endpoint but accepts custom configuration in request body.
    """
    # Parse config from nested dict structure to AIAdoptionConfig
    parsed_config = _parse_config_from_request(request.config)
    config_summary = _format_config_for_log(parsed_config)
    logger.info(
        f"User {user.id} requesting Asset Reusability. "
        f"Projects={request.projects}, Page={request.page}, Config={config_summary}"
    )

    service = AnalyticsService(user)
    response_data = await service.get_ai_adoption_asset_reusability(
        projects=request.projects,
        page=request.page,
        per_page=request.per_page,
        config=parsed_config,
    )

    return _create_response(response_data, TabularResponse)


@router.post(
    "/ai-adoption-expertise-distribution",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get AI Adoption Expertise Distribution Metrics (with custom config)",
    description="Project-level Expertise Distribution metrics with optional custom configuration",
)
@handle_analytics_errors("AI adoption expertise distribution metrics")
async def post_ai_adoption_expertise_distribution(
    request: AiAdoptionTabularQueryRequest = Body(...),
    user: User = Depends(authenticate),
) -> JSONResponse:
    """Get Expertise Distribution metrics with custom configuration.

    Same as GET endpoint but accepts custom configuration in request body.
    """
    # Parse config from nested dict structure to AIAdoptionConfig
    parsed_config = _parse_config_from_request(request.config)
    config_summary = _format_config_for_log(parsed_config)
    logger.info(
        f"User {user.id} requesting Expertise Distribution. "
        f"Projects={request.projects}, Page={request.page}, Config={config_summary}"
    )

    service = AnalyticsService(user)
    response_data = await service.get_ai_adoption_expertise_distribution(
        projects=request.projects,
        page=request.page,
        per_page=request.per_page,
        config=parsed_config,
    )

    return _create_response(response_data, TabularResponse)


@router.post(
    "/ai-adoption-feature-adoption",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get AI Adoption Feature Adoption Metrics (with custom config)",
    description="Project-level Feature Adoption metrics with optional custom configuration",
)
@handle_analytics_errors("AI adoption feature adoption metrics")
async def post_ai_adoption_feature_adoption(
    request: AiAdoptionTabularQueryRequest = Body(...),
    user: User = Depends(authenticate),
) -> JSONResponse:
    """Get Feature Adoption metrics with custom configuration.

    Same as GET endpoint but accepts custom configuration in request body.
    """
    # Parse config from nested dict structure to AIAdoptionConfig
    parsed_config = _parse_config_from_request(request.config)
    config_summary = _format_config_for_log(parsed_config)
    logger.info(
        f"User {user.id} requesting Feature Adoption. "
        f"Projects={request.projects}, Page={request.page}, Config={config_summary}"
    )

    service = AnalyticsService(user)
    response_data = await service.get_ai_adoption_feature_adoption(
        projects=request.projects,
        page=request.page,
        per_page=request.per_page,
        config=parsed_config,
    )

    return _create_response(response_data, TabularResponse)


@router.get(
    "/spending",
    status_code=status.HTTP_200_OK,
    response_model=SummariesResponse,
    response_model_by_alias=True,
    summary="Get user spending and budget information",
    description="Retrieve current spending, budget limits, and reset period for the authenticated user",
)
@handle_analytics_errors("user spending analytics")
async def get_user_spending(
    user: User = Depends(authenticate),
) -> JSONResponse:
    """Get spending analytics for the authenticated user.

    This endpoint retrieves spending information from LiteLLM for budget tracking.
    Returns current spending, budget limits, and time until reset.

    **Access Control:**
    - Users can only see their own spending data

    **Response:**
    - current_spending: Current amount spent in USD
    - budget_limit: Soft budget limit (warning threshold)
    - hard_budget_limit: Hard budget limit (blocking threshold)
    - budget_reset_at: ISO 8601 timestamp of next reset
    - time_until_reset: Formatted time until budget resets (e.g., "5 days 4 hours 31mins")

    **Error Handling:**
    - Gracefully handles API failures with clear messages
    """
    from datetime import timezone
    from codemie.enterprise.litellm.dependencies import (
        get_customer_spending,
        get_proxy_customer_spending,
        get_premium_customer_spending,
        is_premium_models_enabled,
    )

    start_time = datetime.now(timezone.utc)
    logger.info(f"User {user.id} requesting spending analytics")

    # Get standard spending data from LiteLLM
    try:
        spending_data = await asyncio.to_thread(get_customer_spending, user.username, True)
    except Exception as e:
        logger.error(f"Backend error fetching spending for user {user.id}: {e}")
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Unable to retrieve spending information at this time.",
            help="A temporary issue occurred. Please try again later",
        ) from e

    metrics = _build_spending_metrics(spending_data, user.username)

    try:
        proxy_spending_data = await asyncio.to_thread(get_proxy_customer_spending, user.username)
        if proxy_spending_data is not None:
            proxy_current_spending = proxy_spending_data.get("total_spend", 0.0)
            logger.debug(f"Proxy spending retrieved for user {user.id}: spend=${proxy_current_spending:.2f}")
            metrics.append(_build_spending_metric("cli_current_spending", proxy_current_spending, "number", "currency"))
    except Exception as e:
        logger.warning(f"Failed to fetch proxy spending for user {user.id}: {e}")

    # Include premium budget spending when feature is enabled
    if is_premium_models_enabled():
        try:
            premium_spending_data = await asyncio.to_thread(get_premium_customer_spending, user.username)
            if premium_spending_data is not None:
                premium_current_spending = premium_spending_data.get("total_spend", 0.0)
                logger.debug(f"Premium spending retrieved for user {user.id}: spend=${premium_current_spending:.2f}")
                metrics.append(
                    _build_spending_metric(
                        "premium_current_spending",
                        premium_current_spending,
                        "number",
                        "currency",
                    )
                )
        except Exception as e:
            logger.warning(f"Failed to fetch premium spending for user {user.id}: {e}")

    response_data = {
        "data": {"metrics": metrics},
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data_as_of": datetime.now(timezone.utc).isoformat(),
            "filters_applied": {},
            "execution_time_ms": (datetime.now(timezone.utc) - start_time).total_seconds() * 1000,
        },
    }

    return _create_response(response_data, SummariesResponse)


@router.get(
    "/budget_usage",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get user budget usage",
    description="Retrieve budget usage for the authenticated user: personal budget and individual LiteLLM keys",
)
@handle_analytics_errors("budget usage analytics")
async def get_user_budget_usage(
    user: User = Depends(authenticate),
) -> TabularResponse:
    """Get budget usage for the authenticated user.

    Returns tabular data with:
    - First row: User's overall personal budget (identified by email)
    - Subsequent rows: Individual LiteLLM keys with their own budget limits (if any configured)

    Only USER-scoped keys are included; PROJECT-scoped keys are excluded.
    """
    from datetime import timezone
    from codemie.enterprise.litellm.dependencies import (
        get_customer_spending,
        get_proxy_customer_spending,
        get_premium_customer_spending,
        get_user_keys_spending,
        is_premium_models_enabled,
    )
    from codemie.service.analytics.response_formatter import ResponseFormatter

    start_time = datetime.now(timezone.utc)
    logger.info(f"User {user.id} requesting budget usage analytics")

    # Get all projects for virtual keys query
    all_projects = _get_user_all_projects(user)

    premium_enabled = is_premium_models_enabled()

    # Get user's personal budget spending and keys spending in parallel
    try:
        gathered_results = await asyncio.gather(
            asyncio.to_thread(get_customer_spending, user.username, True),
            asyncio.to_thread(get_proxy_customer_spending, user.username, True),
            asyncio.to_thread(get_premium_customer_spending, user.username, True)
            if premium_enabled
            else asyncio.sleep(0, result=None),
            asyncio.to_thread(get_user_keys_spending, user.id, all_projects, True),
        )
        user_spending_data, proxy_spending_data, premium_spending_data, keys_spending_data = gathered_results
    except Exception as e:
        logger.error(f"Backend error fetching spending data for user {user.id}: {e}")
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Unable to retrieve spending information at this time.",
            help="A temporary issue occurred. Please try again later",
        ) from e

    # user_spending_data is a dict with: total_spend, max_budget, budget_reset_at
    user_personal_spending = user_spending_data
    user_proxy_spending = proxy_spending_data
    user_premium_spending = premium_spending_data

    # Extract only USER-scoped keys (filter out project keys)
    # Each key now has project_name already enriched by get_user_keys_spending()
    user_keys_spending = keys_spending_data.user_keys if keys_spending_data else []
    user_budget_label = user.email or user.username or user.id

    # Build tabular response structure
    columns, rows = _build_key_spending_tabular_data(
        user_budget_label,
        user_personal_spending,
        user_proxy_spending,
        user_premium_spending,
        user_keys_spending,
    )

    # Format using ResponseFormatter for consistency
    execution_time_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000

    return ResponseFormatter.format_tabular_response(
        columns=columns,
        rows=rows,
        filters_applied={},
        execution_time_ms=execution_time_ms,
        totals=None,
    )


# ---------------------------------------------------------------------------
# Engagement widget: weekly histogram
# This endpoint intentionally omits time_period/start_date/end_date params.
# The underlying query always operates on the last 7 days.
# ---------------------------------------------------------------------------


@router.get(
    "/engagement/weekly-histogram",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get weekly spending histogram",
    description=(
        "Returns a time series with one row per 3-hour interval showing money spent "
        "broken down by source: Assistants, Workflows, Datasources, CLI. "
        "Always covers the last 7 days — the dashboard time filter is intentionally ignored."
    ),
)
@handle_analytics_errors("engagement weekly histogram")
async def get_engagement_weekly_histogram(
    user: User = Depends(authenticate),
    users: str | None = Query(None, description=USERS_FILTER_DESC),
    projects: str | None = Query(None, description=PROJECTS_FILTER_DESC, examples=["codemie"]),
) -> JSONResponse:
    """Get weekly spending histogram — 3h intervals, always last 7 days."""
    logger.info(f"User {user.id} requesting weekly spending histogram")
    service = AnalyticsService(user)
    response_data = await service.get_weekly_spending(
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(response_data, TabularResponse)


# ---------------------------------------------------------------------------
# Spending breakdown by user (platform vs CLI)
# ---------------------------------------------------------------------------


@router.get(
    "/spending/by-users/platform",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get platform spending per user",
    description="Returns per-user spending for platform metrics (Assistants, Workflows, Datasources).",
)
@handle_analytics_errors("platform spending by users")
async def get_spending_by_users_platform(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get platform spending per user (Assistants + Workflows + Datasources)."""
    service = AnalyticsService(user)
    data = await service.get_users_platform_spending(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/spending/by-users/cli",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get CLI spending per user",
    description="Returns per-user spending for CLI proxy usage only (cli_request=true), grouped by user_name.",
)
@handle_analytics_errors("CLI spending by users")
async def get_spending_by_users_cli(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get CLI-only spending per user grouped by user_name."""
    service = AnalyticsService(user)
    data = await service.get_users_cli_spending(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
        page,
        per_page,
    )
    return _create_response(data, TabularResponse)


# ── Leaderboard endpoints (admin-only) ─────────────────────────────────────


@router.get(
    "/leaderboard/summary",
    status_code=status.HTTP_200_OK,
    response_model=SummariesResponse,
    response_model_by_alias=True,
    summary="Get leaderboard summary metrics",
    description="Returns high-level leaderboard metrics: total users, tier counts, top score, etc.",
    dependencies=[Depends(admin_access_only)],
)
@handle_analytics_errors("leaderboard summary")
async def get_leaderboard_summary(
    user: User = Depends(authenticate),
    snapshot_id: str | None = Query(None, description=LEADERBOARD_SNAPSHOT_ID_DESC),
    view: str = Query("current", description=LEADERBOARD_VIEW_DESC, pattern=LEADERBOARD_VIEW_PATTERN),
    season_key: str | None = Query(None, description=LEADERBOARD_SEASON_KEY_DESC),
) -> JSONResponse:
    """Get leaderboard summary metrics."""
    service = AnalyticsService(user)
    data = await service.get_leaderboard_summary(snapshot_id, view=view, season_key=season_key)
    return _create_response(data, SummariesResponse)


@router.get(
    "/leaderboard/entries",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get leaderboard entries table",
    description="Returns paginated leaderboard entries with scores and tier info.",
    dependencies=[Depends(admin_access_only)],
)
@handle_analytics_errors("leaderboard entries")
async def get_leaderboard_entries(
    user: User = Depends(authenticate),
    snapshot_id: str | None = Query(None, description=LEADERBOARD_SNAPSHOT_ID_DESC),
    view: str = Query("current", description=LEADERBOARD_VIEW_DESC, pattern=LEADERBOARD_VIEW_PATTERN),
    season_key: str | None = Query(None, description=LEADERBOARD_SEASON_KEY_DESC),
    tier: str | None = Query(
        None,
        description="Filter by tier name (pioneer, expert, advanced, practitioner, newcomer)",
    ),
    search: str | None = Query(
        None,
        description="Search by user name or email (case-insensitive partial match)",
    ),
    intent: str | None = Query(
        None,
        description="Filter by usage intent (e.g. cli_focused, platform_focused, hybrid, sdlc_unicorn)",
    ),
    sort_by: str | None = Query(
        None,
        description="Sort column: rank, total_score, user_name, tier_level",
    ),
    sort_order: str = Query(
        "asc",
        description="Sort direction: asc or desc",
        pattern="^(asc|desc)$",
    ),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get paginated leaderboard entries with optional filtering and sorting."""
    service = AnalyticsService(user)
    data = await service.get_leaderboard_entries(
        snapshot_id,
        tier,
        page,
        per_page,
        search,
        intent,
        sort_by,
        sort_order,
        view=view,
        season_key=season_key,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/leaderboard/user/{user_id}",
    status_code=status.HTTP_200_OK,
    response_model=AnalyticsDetailResponse,
    response_model_by_alias=True,
    summary="Get leaderboard detail for a specific user",
    description="Returns detailed leaderboard data for a single user including dimension breakdowns. "
    "Accepts either a user ID or user email as the path parameter. Admin only.",
    dependencies=[Depends(admin_access_only)],
)
@handle_analytics_errors("leaderboard user detail")
async def get_leaderboard_user_detail(
    user_id: str,
    user: User = Depends(authenticate),
    snapshot_id: str | None = Query(None, description=LEADERBOARD_SNAPSHOT_ID_DESC),
    view: str = Query("current", description=LEADERBOARD_VIEW_DESC, pattern=LEADERBOARD_VIEW_PATTERN),
    season_key: str | None = Query(None, description=LEADERBOARD_SEASON_KEY_DESC),
) -> JSONResponse:
    """Get detailed leaderboard data for a specific user."""
    if not user.is_admin:
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=ERROR_MSG_ACCESS_DENIED,
            details="Leaderboard API access is restricted to administrators.",
            help=ERROR_MSG_ADMIN_HELP,
        )
    service = AnalyticsService(user)
    data = await service.get_leaderboard_user_detail(user_id, snapshot_id, view=view, season_key=season_key)
    return _create_response(data, AnalyticsDetailResponse)


@router.get(
    "/leaderboard/tiers",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get tier distribution",
    description="Returns user count and percentage per tier.",
    dependencies=[Depends(admin_access_only)],
)
@handle_analytics_errors("leaderboard tier distribution")
async def get_leaderboard_tier_distribution(
    user: User = Depends(authenticate),
    snapshot_id: str | None = Query(None, description=LEADERBOARD_SNAPSHOT_ID_DESC),
    view: str = Query("current", description=LEADERBOARD_VIEW_DESC, pattern=LEADERBOARD_VIEW_PATTERN),
    season_key: str | None = Query(None, description=LEADERBOARD_SEASON_KEY_DESC),
) -> JSONResponse:
    """Get tier distribution data."""
    service = AnalyticsService(user)
    data = await service.get_leaderboard_tier_distribution(snapshot_id, view=view, season_key=season_key)
    return _create_response(data, TabularResponse)


@router.get(
    "/leaderboard/scores",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get score distribution",
    description="Returns histogram of user scores in 10-point bins.",
    dependencies=[Depends(admin_access_only)],
)
@handle_analytics_errors("leaderboard score distribution")
async def get_leaderboard_score_distribution(
    user: User = Depends(authenticate),
    snapshot_id: str | None = Query(None, description=LEADERBOARD_SNAPSHOT_ID_DESC),
    view: str = Query("current", description=LEADERBOARD_VIEW_DESC, pattern=LEADERBOARD_VIEW_PATTERN),
    season_key: str | None = Query(None, description=LEADERBOARD_SEASON_KEY_DESC),
) -> JSONResponse:
    """Get score distribution histogram."""
    service = AnalyticsService(user)
    data = await service.get_leaderboard_score_distribution(snapshot_id, view=view, season_key=season_key)
    return _create_response(data, TabularResponse)


@router.get(
    "/leaderboard/dimensions",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get dimension breakdown",
    description="Returns average scores per scoring dimension across all users.",
    dependencies=[Depends(admin_access_only)],
)
@handle_analytics_errors("leaderboard dimension breakdown")
async def get_leaderboard_dimension_breakdown(
    user: User = Depends(authenticate),
    snapshot_id: str | None = Query(None, description=LEADERBOARD_SNAPSHOT_ID_DESC),
    view: str = Query("current", description=LEADERBOARD_VIEW_DESC, pattern=LEADERBOARD_VIEW_PATTERN),
    season_key: str | None = Query(None, description=LEADERBOARD_SEASON_KEY_DESC),
) -> JSONResponse:
    """Get dimension breakdown with averages."""
    service = AnalyticsService(user)
    data = await service.get_leaderboard_dimension_breakdown(snapshot_id, view=view, season_key=season_key)
    return _create_response(data, TabularResponse)


@router.get(
    "/leaderboard/top-performers",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get top performers",
    description="Returns top N leaderboard entries by total score.",
    dependencies=[Depends(admin_access_only)],
)
@handle_analytics_errors("leaderboard top performers")
async def get_leaderboard_top_performers(
    user: User = Depends(authenticate),
    snapshot_id: str | None = Query(None, description=LEADERBOARD_SNAPSHOT_ID_DESC),
    view: str = Query("current", description=LEADERBOARD_VIEW_DESC, pattern=LEADERBOARD_VIEW_PATTERN),
    season_key: str | None = Query(None, description=LEADERBOARD_SEASON_KEY_DESC),
    limit: int = Query(3, ge=1, le=50, description="Number of top performers to return"),
) -> JSONResponse:
    """Get top N performers."""
    service = AnalyticsService(user)
    data = await service.get_leaderboard_top_performers(snapshot_id, limit, view=view, season_key=season_key)
    return _create_response(data, TabularResponse)


@router.get(
    "/leaderboard/snapshots",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get leaderboard snapshots",
    description="Returns paginated list of leaderboard computation snapshots.",
    dependencies=[Depends(admin_access_only)],
)
@handle_analytics_errors("leaderboard snapshots")
async def get_leaderboard_snapshots(
    user: User = Depends(authenticate),
    view: str | None = Query(
        None,
        description="Optional leaderboard view filter: current, monthly, quarterly",
        pattern=LEADERBOARD_VIEW_PATTERN,
    ),
    status: str | None = Query(None, description="Optional snapshot status filter"),
    is_final: bool | None = Query(None, description="Optional finality filter"),
    page: int = Query(0, ge=0),
    per_page: int = Query(10, ge=1, le=100),
) -> JSONResponse:
    """Get list of leaderboard snapshots."""
    service = AnalyticsService(user)
    data = await service.get_leaderboard_snapshots(page, per_page, view=view, status=status, is_final=is_final)
    return _create_response(data, TabularResponse)


@router.get(
    "/leaderboard/seasons",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get available leaderboard seasons",
    description="Returns available completed monthly or quarterly leaderboard seasons for UI selectors.",
    dependencies=[Depends(admin_access_only)],
)
@handle_analytics_errors("leaderboard seasons")
async def get_leaderboard_seasons(
    user: User = Depends(authenticate),
    view: str = Query(
        ..., description="Seasonal leaderboard view: monthly or quarterly", pattern="^(monthly|quarterly)$"
    ),
    page: int = Query(0, ge=0),
    per_page: int = Query(50, ge=1, le=200),
) -> JSONResponse:
    """Get available monthly or quarterly seasons."""
    service = AnalyticsService(user)
    data = await service.get_leaderboard_seasons(view, page, per_page)
    return _create_response(data, TabularResponse)


@router.post(
    "/leaderboard/compute",
    status_code=status.HTTP_200_OK,
    response_model=AnalyticsDetailResponse,
    response_model_by_alias=True,
    summary="Trigger leaderboard computation",
    description="Manually triggers a leaderboard computation. Admin only.",
    dependencies=[Depends(admin_access_only)],
)
@handle_analytics_errors("leaderboard compute")
async def trigger_leaderboard_computation(
    user: User = Depends(authenticate),
    period_days: int = Query(30, ge=1, le=365, description="Number of days to include in computation"),
    view: str = Query(
        "current",
        description="Leaderboard view to compute: current, monthly, quarterly",
        pattern="^(current|monthly|quarterly)$",
    ),
    season_key: str | None = Query(
        None, description="Optional season key for seasonal computation, e.g. 2026-03 or 2026-Q1"
    ),
) -> JSONResponse:
    """Trigger a manual leaderboard computation."""
    service = AnalyticsService(user)
    data = await service.trigger_leaderboard_computation(period_days, view=view, season_key=season_key)
    return _create_response(data, AnalyticsDetailResponse)


@router.get(
    "/leaderboard/framework",
    status_code=status.HTTP_200_OK,
    response_model=AnalyticsDetailResponse,
    response_model_by_alias=True,
    summary="Get leaderboard framework metadata",
    description="Returns static scoring framework metadata: dimension descriptions, "
    "component explanations, tier definitions, intent definitions, and scoring principles. "
    "This data is static and can be cached indefinitely by the client.",
    dependencies=[Depends(admin_access_only)],
)
@handle_analytics_errors("leaderboard framework")
async def get_leaderboard_framework(
    user: User = Depends(authenticate),
) -> JSONResponse:
    """Get leaderboard scoring framework metadata."""
    service = AnalyticsService(user)
    data = await service.get_leaderboard_framework()
    return _create_response(data, AnalyticsDetailResponse)
