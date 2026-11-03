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

import asyncio
import logging
from datetime import datetime, timezone
from typing import NamedTuple

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from codemie.configs.config import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.analytics import (
    SummariesResponse,
    TabularResponse,
)
from codemie.rest_api.routers.analytics._common import (
    CLIENT_SOURCE_FILTER_DESC,
    ERROR_MSG_ACCESS_DENIED,
    ERROR_MSG_ADMIN_HELP,
    PROJECTS_FILTER_DESC,
    USERS_FILTER_DESC,
    AnalyticsQueryParams,
    _create_response,
    handle_analytics_errors,
)
from codemie.rest_api.routers.analytics._spending_support import _authorize_admin_budget_view
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.utils.client_context import ClientSource
from codemie.rest_api.security.user import User
from codemie.clients.postgres import get_async_session
from codemie.service.analytics.analytics_service import AnalyticsService
from codemie.repository.budget_repository import budget_repository
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.budget.budget_models import Budget
from codemie.service.spend.budget_period import budget_period_range
from codemie.service.spend.coverage import SpendResult, TimeRange
from codemie.service.spend.identity_bridge import resolve_identity
from codemie.service.spend.ledger import PersonalHolder
from codemie.service.spend.spend_service import spend_service
from codemie.service.analytics.handlers.member_spend_service import member_spend_service
from codemie.service.analytics.spending_formatter import (
    build_money_metric,
    build_spending_metrics,
    paginate_tabular,
)

logger = logging.getLogger(__name__)

router = APIRouter()


async def _consumed_per_budget_category(
    user_id: str, policy_by_category: dict[BudgetCategory, dict | None]
) -> dict[BudgetCategory, SpendResult]:
    """What the user consumed in each category's own budget period, read through the spend port.

    A category absent from the result has **no figure** - never a zero - and was deliberately not
    asked about, because no budget period could be derived for it.

    Args:
        user_id: The canonical `users.id` of the budget subject.
        policy_by_category: Each category's budget policy dict, or None where the provider supplied
            none. The period is derived from the same dict whose `budget_reset_at` the response
            displays, so the window a figure covers and the reset the reader sees are one fact.
    """
    if not _budget_periods(user_id, policy_by_category, assigned_budgets={}):
        # Nothing is askable, so nothing is asked - and the identity is not resolved either, which
        # would be a query answering a question no holder is going to pose. Whether a period exists
        # does not depend on the assigned budgets, which only say where it starts.
        return {}

    async with get_async_session() as session:
        assigned = await _assigned_budgets(session, user_id)
        periods = _budget_periods(user_id, policy_by_category, assigned)
        # Resolved once for the subject and passed through unchanged. `None` is meaningful: it says
        # this person's pre-rollout rows cannot be identified, which the port labels on the figure.
        # An empty set would instead claim they have no fallback identities.
        aliases = (await resolve_identity(session, user_id)).aliases

    categories: dict[PersonalHolder, BudgetCategory] = {}
    holders_by_period: dict[TimeRange, list[PersonalHolder]] = {}
    for category, period in periods.items():
        holder = PersonalHolder(user_id=user_id, category=category, aliases=aliases)
        categories[holder] = category
        holders_by_period.setdefault(period, []).append(holder)

    # One round trip per distinct period, not per category: categories on the same reset schedule
    # collapse to the same range, and all holders in one call must share one period.
    consumed: dict[BudgetCategory, SpendResult] = {}
    for period, holders in holders_by_period.items():
        for holder, result in (await spend_service.consumed_for_holders(holders, period)).items():
            consumed[categories[holder]] = result
    return consumed


class _AssignedBudget(NamedTuple):
    budget: Budget
    spend_reset_at: datetime | None


async def _assigned_budgets(session: AsyncSession, user_id: str) -> dict[str, _AssignedBudget]:
    """The user's assigned budget per category value, with what only CodeMie records about its period.

    That is the reset CodeMie set on the budget and the instant an administrator last reset the
    user's spend by hand.
    """
    assignments = await budget_repository.get_user_category_assignments(session, user_id)
    budgets = await budget_repository.get_by_ids(session, [a.budget_id for a in assignments])
    return {
        a.category: _AssignedBudget(budgets[a.budget_id], a.spend_reset_at)
        for a in assignments
        if a.budget_id in budgets
    }


def _budget_periods(
    user_id: str, policy_by_category: dict[BudgetCategory, dict | None], assigned_budgets: dict[str, _AssignedBudget]
) -> dict[BudgetCategory, TimeRange]:
    """Each category's budget period, omitting every category that has none.

    The one definition of a budget period. An inline `[now - duration, now)` is a rolling window that
    answers a different question just as convincingly. A category omitted here is **not queried at
    all**, and its figure is reported absent rather than computed over a guessed window. The provider's
    policy names the reset; where the period began after a reset CodeMie set is recorded only on the
    assigned budget, and a manual spend reset only on the assignment.
    """
    periods: dict[BudgetCategory, TimeRange] = {}
    for category, policy in policy_by_category.items():
        if not policy:
            continue
        assigned = assigned_budgets.get(category.value)
        budget = assigned.budget if assigned else None
        period = budget_period_range(
            budget_reset_at=policy.get("budget_reset_at"),
            budget_duration=policy.get("budget_duration"),
            pinned_reset_at=budget.pinned_reset_at if budget else None,
            pinned_period_start=budget.pinned_period_start if budget else None,
            effective_from=assigned.spend_reset_at if assigned else None,
        )
        if period is None:
            logger.warning(
                f"spending: no budget period derivable for category={category.value} user={user_id} "
                f"(reset_at={policy.get('budget_reset_at')!r} duration={policy.get('budget_duration')!r}); "
                "reporting no figure rather than guessing a window"
            )
            continue
        periods[category] = period
    return periods


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
    - budget_limit: Maximum budget for the period (hard limit)
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
        is_proxy_budget_enabled,
    )

    start_time = datetime.now(timezone.utc)
    logger.info(f"User {user.id} requesting spending analytics")

    # Budget **policy** from LiteLLM: the limit and the reset window. Every money figure below comes
    # from the spend port instead, because LiteLLM's own counter is an enforcement input and not a
    # reported figure.
    try:
        spending_data = await asyncio.to_thread(get_customer_spending, user.username, True)
    except Exception as e:
        logger.error(f"Backend error fetching spending for user {user.id}: {e}")
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Unable to retrieve spending information at this time.",
            help="A temporary issue occurred. Please try again later",
        ) from e

    # Whether each optional budget **exists** is a configuration fact, and it is the only reason a
    # metric is omitted below: no such budget is a different answer from an unknown figure, and only
    # the first justifies saying nothing at all.
    cli_configured = is_proxy_budget_enabled()
    premium_configured = is_premium_models_enabled()

    # A failed policy read costs the figure's **window**, not the metric: the port is still asked
    # about a configured budget, and the user is still told what it holds. Dropping the metric here -
    # which the code this replaces did - made a provider hiccup indistinguishable from a budget that
    # does not exist.
    cli_policy = None
    if cli_configured:
        try:
            cli_policy = await asyncio.to_thread(get_proxy_customer_spending, user.username)
        except Exception as e:
            logger.warning(f"Failed to fetch proxy budget policy for user {user.id}: {e}")

    premium_policy = None
    if premium_configured:
        try:
            premium_policy = await asyncio.to_thread(get_premium_customer_spending, user.username)
        except Exception as e:
            logger.warning(f"Failed to fetch premium budget policy for user {user.id}: {e}")

    consumed = await _consumed_per_budget_category(
        user.id,
        {
            BudgetCategory.PLATFORM: spending_data,
            BudgetCategory.CLI: cli_policy,
            BudgetCategory.PREMIUM_MODELS: premium_policy,
        },
    )

    metrics = build_spending_metrics(spending_data, consumed.get(BudgetCategory.PLATFORM), user.username)

    if cli_configured:
        metrics.append(build_money_metric("cli_current_spending", consumed.get(BudgetCategory.CLI)))

    if premium_configured:
        metrics.append(build_money_metric("premium_current_spending", consumed.get(BudgetCategory.PREMIUM_MODELS)))

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


async def _resolve_admin_budget_subject(user: "User", user_id: str) -> tuple[str, str | None, str | None]:
    """Look up target user for admin budget view; raise if not found or not authorized."""
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="user_id parameter requires ENABLE_USER_MANAGEMENT to be enabled.",
            help="Remove the user_id parameter or enable user management.",
        )
    from codemie.clients.postgres import get_session
    from codemie.repository.user_project_repository import user_project_repository
    from codemie.service.user.user_management_service import UserManagementService

    def _lookup():
        with get_session() as session:
            db_user = UserManagementService.get_user_by_id(session, user_id)
            project_names = user_project_repository.get_project_names_for_user(session, user_id) if db_user else []
        return db_user, project_names

    target_user_db, target_project_names = await asyncio.to_thread(_lookup)
    if target_user_db is None:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=f"User {user_id} not found.",
            help="Verify the user_id and try again.",
        )
    _authorize_admin_budget_view(user, target_project_names)
    subject_label = target_user_db.username
    if not subject_label:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=f"User {user_id} has no username; budget lookup cannot be performed.",
            help="Ensure the target user has a valid username configured.",
        )
    logger.info(f"Admin budget view: caller={user.id} viewing target={user_id}")
    return str(target_user_db.id), subject_label, target_user_db.email


@router.get(
    "/budget_usage",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get user budget usage",
    description=(
        "Retrieve budget usage for a user: personal budget categories (platform, CLI, premium) "
        "and per-project member allocations. "
        "When user_id is omitted, returns data for the authenticated caller. "
        "When user_id is provided, requires the caller to be a global admin, maintainer, "
        "or project admin sharing at least one project with the target user."
    ),
)
@handle_analytics_errors("budget usage analytics")
async def get_user_budget_usage(
    user: User = Depends(authenticate),
    user_id: str | None = Query(
        default=None,
        min_length=1,
        description=(
            "Optional target user ID. When provided, returns budget usage for that user. "
            "Requires the caller to be a global admin, maintainer, or project admin "
            "for at least one project the target user belongs to. "
            "Only valid when ENABLE_USER_MANAGEMENT=True."
        ),
    ),
) -> TabularResponse:
    from datetime import timezone

    from codemie.service.analytics.handlers.budget_usage_service import budget_usage_service
    from codemie.service.analytics.response_formatter import ResponseFormatter

    start_time = datetime.now(timezone.utc)

    # --- Resolve subject (target user or caller) ---
    if user_id is not None:
        subject_user_id, subject_label, subject_email = await _resolve_admin_budget_subject(user, user_id)
    else:
        subject_user_id = user.id
        subject_label = user.username
        subject_email = user.email
        if not subject_label:
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message="Your account has no username; budget lookup cannot be performed.",
                help="Ensure your account has a valid username configured.",
            )
        logger.info(f"User {user.id} requesting budget usage analytics")

    try:
        columns, rows = await budget_usage_service.get_budget_usage(subject_user_id, subject_label, subject_email)
    except ExtendedHTTPException:
        raise
    except Exception as e:
        logger.error(f"DB error fetching budget data for subject={subject_user_id} requested_by={user.id}: {e}")
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Unable to retrieve spending information at this time.",
            help="A temporary issue occurred. Please try again later.",
        ) from e

    execution_time_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000

    return ResponseFormatter.format_tabular_response(
        columns=columns,
        rows=rows,
        filters_applied={},
        execution_time_ms=execution_time_ms,
        totals=None,
    )


@router.get(
    "/user-project-spending",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get a user's spend broken down by project",
    description=(
        "Returns one row per project the user belongs to, with spend and configured limit "
        "per budget category for the current budget cycle. Requires the caller to be a global "
        "admin, maintainer, or project admin for at least one project the target user belongs to."
    ),
)
@handle_analytics_errors("user project spending analytics")
async def get_user_project_spending(
    user: User = Depends(authenticate),
    users: str = Query(..., min_length=1, description="Target user email. Exactly one."),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> TabularResponse:
    start_time = datetime.now(timezone.utc)
    target_email = users.split(",")[0].strip()

    target_user, target_projects = await asyncio.to_thread(member_spend_service.resolve_spend_subject, target_email)
    if target_user is None:
        if not user.is_admin:
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message=ERROR_MSG_ACCESS_DENIED,
                help=ERROR_MSG_ADMIN_HELP,
            )
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=f"User {target_email} not found.",
            help="Verify the email and try again.",
        )
    _authorize_admin_budget_view(user, target_projects)

    columns, rows = await member_spend_service.get_user_project_spend(str(target_user.id))

    return paginate_tabular(
        columns=columns,
        rows=rows,
        filters_applied={"users": target_email},
        start_time=start_time,
        page=page,
        per_page=per_page,
    )


@router.get(
    "/project-member-spending",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get each project member's spend in that project",
    description=(
        "Returns one row per project member, keyed by user_id, with spend and configured limit "
        "per budget category for the current budget cycle. Requires the caller to be a global "
        "admin, maintainer, or admin of the requested project."
    ),
)
@handle_analytics_errors("project member spending analytics")
async def get_project_member_spending(
    user: User = Depends(authenticate),
    projects: str = Query(..., min_length=1, description="Target project name. Exactly one."),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> TabularResponse:
    start_time = datetime.now(timezone.utc)
    project_name = projects.split(",")[0].strip()

    _authorize_admin_budget_view(user, {project_name})

    columns, rows = await member_spend_service.get_project_member_spend(project_name)

    return paginate_tabular(
        columns=columns,
        rows=rows,
        filters_applied={"projects": project_name},
        start_time=start_time,
        page=page,
        per_page=per_page,
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
    client_source: ClientSource | None = Query(None, description=CLIENT_SOURCE_FILTER_DESC),
) -> JSONResponse:
    """Get weekly spending histogram — 3h intervals, always last 7 days."""
    logger.info(f"User {user.id} requesting weekly spending histogram")
    service = AnalyticsService(user)
    response_data = await service.get_weekly_spending(
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
        client_source=client_source.value if client_source else None,
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
    params: AnalyticsQueryParams = Depends(),
    client_source: ClientSource | None = Query(None, description=CLIENT_SOURCE_FILTER_DESC),
) -> JSONResponse:
    """Get platform spending per user (Assistants + Workflows + Datasources)."""
    service = AnalyticsService(user)
    data = await service.get_users_platform_spending(
        params.time_period,
        params.start_date,
        params.end_date,
        params.users_list,
        params.projects_list,
        params.page,
        params.per_page,
        client_source=client_source.value if client_source else None,
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
    params: AnalyticsQueryParams = Depends(),
    client_source: ClientSource | None = Query(None, description=CLIENT_SOURCE_FILTER_DESC),
) -> JSONResponse:
    """Get CLI-only spending per user grouped by user_name."""
    service = AnalyticsService(user)
    data = await service.get_users_cli_spending(
        params.time_period,
        params.start_date,
        params.end_date,
        params.users_list,
        params.projects_list,
        params.page,
        params.per_page,
        client_source=client_source.value if client_source else None,
    )
    return _create_response(data, TabularResponse)
