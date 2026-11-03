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

import logging
from datetime import datetime, timezone
from typing import Any

from codemie.service.analytics.response_formatter import ResponseFormatter
from codemie.service.spend.coverage import SpendResult
from codemie.service.spend.rendering import render_spend, spend_row_fields

logger = logging.getLogger(__name__)


# Constants for spending analytics
_SPENDING_METRIC_DEFINITIONS = {
    "current_spending": {
        "label": "Current Spending ($)",
        "description": "Total amount spent in current budget period",
    },
    "budget_limit": {
        "label": "Budget Limit ($)",
        "description": "Maximum budget for the period (hard limit)",
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


def build_spending_metric(
    metric_id: str, value: Any, value_type: str = "string", format_type: str | None = None
) -> dict[str, Any]:
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


def build_money_metric(metric_id: str, spend: SpendResult | None) -> dict[str, Any]:
    """A currency metric whose figure comes from the spend port, carrying its qualification.

    The annotation travels in the metric dict beside the number rather than being dropped, because a
    truncated or dimension-incomplete total otherwise renders indistinguishably from a whole one. An
    absent figure stays absent: `None` is the no-figure state and never a zero, which would claim the
    user spent nothing.
    """
    rendered = render_spend(spend)
    return {
        **build_spending_metric(metric_id, rendered.figure, "number", "currency"),
        **spend_row_fields(spend),
    }


def build_spending_metrics(
    spending_data: dict[str, Any] | None, spend: SpendResult | None, user_id: str
) -> list[dict[str, Any]]:
    """Build spending metrics list based on available data.

    Args:
        spending_data: Budget **policy** for the user - limit and reset window - or None if the
            budget service answered nothing. It no longer supplies the money.
        spend: What the user consumed over the platform budget period, from the spend port. None
            means the port produced no figure for it.
        user_id: User the metrics describe, for logging.

    Returns:
        List of metric dictionaries
    """
    rendered = render_spend(spend)
    current_spending_metric = build_money_metric("current_spending", spend)

    if not spending_data:
        logger.info(f"Budget tracking not available for user {user_id}")
        return [
            # What is unavailable in this branch is the **policy**, not the money: a user with no
            # budget record can still have ledger spend, so the figure the port supplied is rendered
            # here with its annotation. Overriding it with `None` would put a second, wrong
            # unavailable state on the wire, and `0.0` would say the user spent nothing.
            current_spending_metric,
            build_spending_metric("budget_limit", "N/A", "string"),
            build_spending_metric("budget_reset_at", "N/A", "string"),
            build_spending_metric("time_until_reset", "N/A", "string"),
        ]

    budget_limit = spending_data.get("max_budget")

    # Handle empty/None fields - show "N/A" instead
    budget_reset_at_raw = spending_data.get("budget_reset_at")
    budget_reset_at = budget_reset_at_raw if budget_reset_at_raw else "N/A"

    time_until_reset = calculate_time_until_reset(budget_reset_at_raw) if budget_reset_at_raw else "N/A"

    logger.info(
        f"Spending analytics retrieved for user {user_id}: "
        f"spend={'unavailable' if rendered.figure is None else f'${rendered.figure:.2f}'}, "
        f"budget={budget_limit}, time_until_reset={time_until_reset}"
    )

    # Adjust metric type/format based on whether value is N/A
    return [
        current_spending_metric,
        build_spending_metric("budget_limit", budget_limit, "number", "currency"),
        build_spending_metric(
            "budget_reset_at",
            budget_reset_at,
            "date" if budget_reset_at_raw else "string",
            "timestamp" if budget_reset_at_raw else None,
        ),
        build_spending_metric("time_until_reset", time_until_reset, "string"),
    ]


def calculate_time_until_reset(budget_reset_at: str | None) -> str | None:
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


def paginate_tabular(
    *,
    columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    filters_applied: dict[str, Any],
    start_time: datetime,
    page: int,
    per_page: int,
) -> dict[str, Any]:
    """Slice rows for the requested page and format the tabular response.

    total_count is the unsliced row count so the client can page through the rest.
    """
    page_rows = rows[page * per_page : (page + 1) * per_page]
    execution_time_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
    return ResponseFormatter.format_tabular_response(
        columns=columns,
        rows=page_rows,
        filters_applied=filters_applied,
        execution_time_ms=execution_time_ms,
        totals=None,
        page=page,
        per_page=per_page,
        total_count=len(rows),
    )
