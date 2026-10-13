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

from fastapi import APIRouter, Depends

from codemie.rest_api.routers.analytics import (
    activity,
    cli,
    cli_enriched_users,
    cli_insights,
    cli_insights_projects,
    leaderboard,
    routing,
    spending,
    usage,
)
from codemie.rest_api.security.authentication import authenticate

router = APIRouter(tags=["Dashboard Analytics"], prefix="/v1/analytics", dependencies=[Depends(authenticate)])
router.include_router(usage.router)
router.include_router(activity.router)
router.include_router(cli.router)
router.include_router(cli_insights.router)
router.include_router(cli_insights_projects.router)
router.include_router(cli_enriched_users.router)
router.include_router(spending.router)
router.include_router(leaderboard.router)
router.include_router(routing.router)

__all__ = ["router"]
