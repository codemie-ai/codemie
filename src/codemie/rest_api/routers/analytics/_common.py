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

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from functools import wraps
from typing import Any

from fastapi import Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from codemie.configs.config import config
from codemie.core.exceptions import ExtendedHTTPException

logger = logging.getLogger(__name__)


# Query parameter descriptions
PROJECTS_FILTER_ADMIN_DESC = "Filter by projects (comma-separated, admin only)"
USERS_FILTER_DESC = "Filter by users (comma-separated user IDs)"
PROJECTS_FILTER_DESC = "Filter by projects (comma-separated project names)"
CLIENT_SOURCE_FILTER_DESC = "Filter by client source (platform, teams, cli, sdk, chrome_extension, or other)"


ERROR_MSG_PROJECT_EMPTY = "project field cannot be empty or whitespace-only"
ERROR_MSG_ACCESS_DENIED = "Access denied"
ERROR_MSG_ADMIN_HELP = "Contact your administrator to request access to this project."


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


def _create_response(data: dict, model_class) -> JSONResponse:
    """Helper to create JSON response with cache headers."""
    validated = model_class(**data)
    response_dict = validated.model_dump(by_alias=True)
    response = JSONResponse(content=response_dict, status_code=status.HTTP_200_OK)
    if config.is_local:
        response.headers["Cache-Control"] = "no-store"
    else:
        response.headers["Cache-Control"] = "public, max-age=300"
        response.headers["ETag"] = hashlib.sha256(json.dumps(response_dict, sort_keys=True).encode()).hexdigest()
    return response


class AnalyticsFilterParams(BaseModel):
    """Common filter parameters shared across analytics endpoints."""

    time_period: str | None = Query(None)
    start_date: datetime | None = Query(None)
    end_date: datetime | None = Query(None)
    users: str | None = Query(None, description="Comma-separated user emails")
    projects: str | None = Query(None, description="Comma-separated project names")

    @property
    def users_list(self) -> list[str] | None:
        return [u.strip() for u in self.users.split(",")] if self.users else None

    @property
    def projects_list(self) -> list[str] | None:
        return [p.strip() for p in self.projects.split(",")] if self.projects else None


class AnalyticsQueryParams(AnalyticsFilterParams):
    """Common filter + pagination parameters shared across analytics endpoints."""

    page: int = Query(0, ge=0)
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000)
