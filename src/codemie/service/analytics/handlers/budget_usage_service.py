# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _get_key_spending_columns() -> list[dict]:
    """Get column definitions for budget usage tabular response."""
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


def _calculate_time_until_reset(budget_reset_at: str | None) -> str | None:
    """Calculate formatted time remaining until budget resets."""
    if not budget_reset_at:
        return None
    try:
        reset_time = datetime.fromisoformat(budget_reset_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if reset_time.tzinfo is None:
            reset_time = reset_time.replace(tzinfo=timezone.utc)
        delta = reset_time - now
        if delta.total_seconds() <= 0:
            return "Expired"
        total_seconds = int(delta.total_seconds())
        days = total_seconds // 86400
        remaining_seconds = total_seconds % 86400
        hours = remaining_seconds // 3600
        minutes = (remaining_seconds % 3600) // 60
        return f"{days} days {hours} hours {minutes} mins"
    except (ValueError, AttributeError) as e:
        logger.warning(f"Failed to parse budget_reset_at: {budget_reset_at}, error: {e}")
        return None


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


def _build_budget_usage_rows(
    user_label: str,
    assignments: list,
    budgets_map: dict,
    spend_map: dict,
) -> tuple[list[dict], list[dict]]:
    """Build tabular budget usage data from personal budget assignments."""
    rows = []

    category_labels = {
        "platform": user_label,
        "cli": f"{user_label} (cli)",
        "premium_models": f"{user_label} (premium)",
    }

    for assignment in assignments:
        budget = budgets_map.get(assignment.budget_id)
        if budget is None:
            continue
        spend_row = spend_map.get(assignment.budget_id)
        label = category_labels.get(assignment.category, f"{user_label} ({assignment.category})")
        spending = {
            "total_spend": float(spend_row.budget_period_spend) if spend_row else 0.0,
            "max_budget": budget.max_budget,
            "budget_reset_at": budget.budget_reset_at,
        }
        rows.append(_build_spending_row(label, spending))

    return _get_key_spending_columns(), rows


class BudgetUsageService:
    """Service for retrieving budget usage with lazy LiteLLM refresh.

    On each request, checks if the spend data in DB is older than the threshold.
    If stale, pulls fresh data from LiteLLM, persists it, and returns it.
    Falls back gracefully to DB data if LiteLLM is unavailable.
    """

    async def get_budget_usage(
        self,
        session: AsyncSession,
        subject_user_id: str,
        subject_label: str,
    ) -> tuple[list[dict], list[dict]]:
        """Return (columns, rows) for the budget usage tabular response.

        Performs a lazy refresh from LiteLLM if spend data is older than
        config.BUDGET_USAGE_STALENESS_THRESHOLD_MS milliseconds.
        """
        assignments, budgets_map, spend_map = await self._load_from_db(session, subject_user_id)

        needs_refresh = self._needs_refresh(spend_map) and self._is_litellm_enabled()
        if needs_refresh:
            logger.info(
                f"Refreshing budget spend from LiteLLM for subject={subject_user_id} subject_label={subject_label}"
            )
        else:
            logger.info(
                f"Returning cached budget spend from DB for subject={subject_user_id} subject_label={subject_label}"
            )
        if needs_refresh and assignments:
            spend_map = await self._refresh_from_litellm(
                session, subject_user_id, subject_label, assignments, spend_map
            )
            # session.commit() inside insert_budget_entries expires all previously
            # loaded ORM objects; rollback clears any error state from a failed
            # insert, then reload fresh objects for _build_budget_usage_rows.
            await session.rollback()
            assignments, budgets_map, _ = await self._load_from_db(session, subject_user_id)

        return _build_budget_usage_rows(subject_label, assignments, budgets_map, spend_map)

    def _needs_refresh(self, spend_map: dict) -> bool:
        """Return True if spend_map is empty or the most recent row is older than the threshold."""
        from codemie.configs.config import config

        if not spend_map:
            return True
        latest = max(row.spend_date for row in spend_map.values())
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        age_ms = (datetime.now(timezone.utc) - latest).total_seconds() * 1000
        return age_ms > config.BUDGET_USAGE_STALENESS_THRESHOLD_MS

    @staticmethod
    def _is_litellm_enabled() -> bool:
        try:
            from codemie.enterprise.litellm.dependencies import is_litellm_enabled

            return is_litellm_enabled()
        except ImportError:
            return False

    async def _load_from_db(
        self,
        session: AsyncSession,
        subject_user_id: str,
    ) -> tuple[list, dict, dict]:
        """Load all DB data needed for budget usage in parallel.

        Returns (assignments, budgets_map, spend_map).
        """
        from codemie.repository.budget_repository import budget_repository
        from codemie.repository.project_spend_tracking_repository import ProjectSpendTrackingRepository

        tracking_repo = ProjectSpendTrackingRepository()
        assignments = await budget_repository.get_user_category_assignments(session, subject_user_id)
        budget_ids = [a.budget_id for a in assignments]

        budgets_map, spend_map = await asyncio.gather(
            budget_repository.get_by_ids(session, budget_ids),
            tracking_repo.get_latest_by_budget_ids(session, budget_ids, subject_user_id),
        )
        return assignments, budgets_map, spend_map

    async def _refresh_from_litellm(
        self,
        session: AsyncSession,
        subject_user_id: str,
        subject_label: str,
        assignments: list,
        current_spend_map: dict,
    ) -> dict:
        """Fetch fresh spend from LiteLLM, persist to DB, and return updated spend_map.

        On any LiteLLM error, logs a warning and returns the original (stale) spend_map.
        """
        from codemie.enterprise.litellm.dependencies import (
            get_customer_spending,
            get_premium_customer_spending,
            get_proxy_customer_spending,
        )
        from codemie.repository.project_spend_tracking_repository import ProjectSpendTrackingRepository
        from codemie.service.spend_tracking.spend_models import ProjectSpendTracking

        tracking_repo = ProjectSpendTrackingRepository()
        budget_ids = [a.budget_id for a in assignments]

        # on_raise intentionally omitted (defaults to False): LiteLLM errors are
        # returned as None and handled gracefully — we fall back to stale DB data
        # rather than raising. The old /spending endpoint used on_raise=True because
        # it had no fallback; the lazy-refresh path does.
        category_fetchers = {
            "platform": lambda: asyncio.to_thread(get_customer_spending, subject_label),
            "cli": lambda: asyncio.to_thread(get_proxy_customer_spending, subject_label),
            "premium_models": lambda: asyncio.to_thread(get_premium_customer_spending, subject_label),
        }

        try:
            fetch_tasks = []
            fetch_assignments = []
            for assignment in assignments:
                fetcher = category_fetchers.get(assignment.category)
                if fetcher is not None:
                    fetch_tasks.append(fetcher())
                    fetch_assignments.append(assignment)

            results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

            now = datetime.now(timezone.utc)
            rows_to_insert = []
            for assignment, result in zip(fetch_assignments, results, strict=False):
                if isinstance(result, BaseException):
                    logger.warning(
                        f"LiteLLM fetch failed for category={assignment.category} " f"subject={subject_label}: {result}"
                    )
                    continue
                if result is None:
                    continue
                fresh_spend = Decimal(str(result.get("total_spend", 0.0)))
                existing = current_spend_map.get(assignment.budget_id)
                if existing is not None and existing.budget_period_spend == fresh_spend:
                    logger.debug(
                        f"Spend unchanged for budget_id={assignment.budget_id} "
                        f"category={assignment.category} spend={fresh_spend}, skipping insert."
                    )
                    continue
                rows_to_insert.append(
                    ProjectSpendTracking(
                        id=uuid.uuid4(),
                        project_name=subject_user_id,
                        spend_date=now,
                        budget_period_spend=fresh_spend,
                        budget_id=assignment.budget_id,
                        budget_category=assignment.category,
                        spend_subject_type="budget",
                        daily_spend=Decimal("0"),
                        cumulative_spend=Decimal("0"),
                    )
                )

            fallback = await self._persist_tracking_rows(session, tracking_repo, subject_user_id, rows_to_insert)
            if fallback is not None:
                return fallback

            return await tracking_repo.get_latest_by_budget_ids(session, budget_ids, subject_user_id)

        except Exception as e:
            logger.warning(f"LiteLLM refresh failed for subject={subject_user_id}: {e}. Returning stale DB data.")
            return await tracking_repo.get_latest_by_budget_ids(session, budget_ids, subject_user_id)

    @staticmethod
    async def _persist_tracking_rows(
        session: AsyncSession,
        tracking_repo: Any,
        subject_user_id: str,
        rows_to_insert: list,
    ) -> dict | None:
        """Persist spend rows to DB. Returns an in-memory fallback map on DB error, None on success."""
        if not rows_to_insert:
            logger.info(f"LiteLLM refresh returned no data for subject={subject_user_id}, nothing persisted.")
            return None
        try:
            await tracking_repo.insert_budget_entries(session, rows_to_insert)
            categories = [r.budget_category for r in rows_to_insert]
            logger.info(
                f"LiteLLM refresh persisted {len(rows_to_insert)} row(s) for "
                f"subject={subject_user_id} categories={categories}"
            )
            return None
        except Exception as db_err:
            logger.warning(
                f"DB write failed after LiteLLM fetch for subject={subject_user_id}: {db_err}. "
                "Returning fresh LiteLLM data without persisting."
            )
            return {row.budget_id: row for row in rows_to_insert}


budget_usage_service = BudgetUsageService()
