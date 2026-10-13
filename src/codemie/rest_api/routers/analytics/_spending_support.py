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

from typing import Any

from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers.analytics._common import (
    ERROR_MSG_ACCESS_DENIED,
    ERROR_MSG_ADMIN_HELP,
)
from codemie.rest_api.security.user import User
from codemie.service.analytics.spending_formatter import calculate_time_until_reset


def _get_user_all_projects(user: User) -> list[str]:
    """Get all projects user has access to (regular + admin).

    Args:
        user: Authenticated user object

    Returns:
        List of all project names user can access
    """
    return list(set(user.project_names) | set(user.admin_project_names))


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
        "time_until_reset": calculate_time_until_reset(reset_at) if reset_at else None,
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


def _authorize_admin_budget_view(
    caller: User,
    target_project_names: set[str],
) -> None:
    """Raise HTTP 403 if caller is not authorized to view the target user's budget."""
    if caller.is_admin or getattr(caller, "is_auditor", False):
        return
    caller_admin_projects = set(caller.admin_project_names or [])
    if caller_admin_projects & target_project_names:
        return
    raise ExtendedHTTPException(
        code=status.HTTP_403_FORBIDDEN,
        message=ERROR_MSG_ACCESS_DENIED,
        help=ERROR_MSG_ADMIN_HELP,
    )
