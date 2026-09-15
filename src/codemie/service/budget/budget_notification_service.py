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

"""Fire-and-forget notifier for budget soft-limit crossings (EPMCDME-13959).

All failures are swallowed. The soft-limit metric is emitted upstream in
``check_user_budget()`` and must not be affected by any error here.

Dedup is atomic: the notification slot is claimed via a conditional UPDATE
before the email is sent, so concurrent LiteLLM callbacks (across requests
or replicas) race safely — exactly one caller wins the slot per dedup window.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlmodel import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from codemie.clients.postgres import get_async_session
from codemie.configs.logger import logger
from codemie.repository.budget_repository import budget_repository
from codemie.service.budget.budget_duration import parse_duration
from codemie.service.email_service import BudgetCategorySnapshot, email_service

# Cap the dedup window so short billing periods (e.g. "60s") do not spam
# and long billing periods still respect a sensible re-notification floor.
# See CR-005 in the code-review report for the design rationale.
_MAX_DEDUP_WINDOW = timedelta(hours=24)


def _effective_dedup_window(budget_duration: str | None) -> timedelta | None:
    """Return the clamped dedup window, or ``None`` if the duration is invalid."""
    parsed = parse_duration(budget_duration)
    if parsed is None:
        return None
    return min(parsed, _MAX_DEDUP_WINDOW)


async def _fetch_project_budget_snapshots(
    session: "AsyncSession",
    *,
    budget_id: str,
) -> list[BudgetCategorySnapshot] | None:
    """Return BudgetCategorySnapshot for every active budget in the same project group.

    Returns None on any error so the caller can still send the email without the breakdown.
    """
    try:
        from codemie.service.budget.budget_models import Budget, ProjectBudgetAssignment

        # Find the group_id for the triggered budget.
        stmt = select(ProjectBudgetAssignment.group_id).where(
            ProjectBudgetAssignment.budget_id == budget_id,
            ProjectBudgetAssignment.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        row = result.first()
        if row is None or row.group_id is None:
            return None
        group_id = row.group_id

        # Load all assignments in the same group.
        stmt = select(ProjectBudgetAssignment.budget_id, ProjectBudgetAssignment.budget_category).where(
            ProjectBudgetAssignment.group_id == group_id,
            ProjectBudgetAssignment.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        assignments = result.all()
        if not assignments:
            return None

        # Bulk-load all budgets in the group.
        sibling_ids = [a.budget_id for a in assignments]
        stmt = select(Budget).where(Budget.budget_id.in_(sibling_ids))
        result = await session.execute(stmt)
        budgets_by_id = {b.budget_id: b for b in result.scalars().all()}

        snapshots: list[BudgetCategorySnapshot] = []
        for a in sorted(assignments, key=lambda x: x.budget_category):
            b = budgets_by_id.get(a.budget_id)
            if b is None:
                continue
            snapshots.append(
                BudgetCategorySnapshot(
                    category=b.budget_category,
                    budget_name=b.name,
                    soft_budget=b.soft_budget,
                    max_budget=b.max_budget,
                    triggered=(b.budget_id == budget_id),
                )
            )
        return snapshots or None
    except Exception as exc:
        logger.warning(f"budget_sibling_fetch_failed budget_id={budget_id!r} error={exc}")
        return None


async def notify_soft_limit_reached(
    budget_id: str,
    current_spend: float,
    soft_limit: float,
) -> None:
    """Send one soft-limit email per budget per dedup window. Fail-open."""
    try:
        async with get_async_session() as session:
            budget = await budget_repository.get_by_id(session, budget_id)
            if budget is None:
                logger.debug(f"budget_notify_skipped budget_id={budget_id!r} reason=budget_missing")
                return

            # Snapshot every ORM attribute we need BEFORE committing. Session
            # commit expires loaded instances (expire_on_commit defaults to
            # True), and re-reading an expired attribute afterwards triggers a
            # lazy refresh that raises MissingGreenlet under asyncio.
            email = budget.notification_owner_email
            budget_name = budget.name
            resolved_budget_id = budget.budget_id
            budget_duration = budget.budget_duration
            notify_once = budget.soft_limit_notify_once
            notification_enabled = budget.soft_limit_notification_enabled
            project_name = budget.project_name
            budget_category = budget.budget_category

            if not email or not email.strip():
                logger.debug(f"budget_notify_skipped budget_id={budget_id!r} reason=no_owner_email")
                return

            if not notification_enabled:
                logger.debug(f"budget_notify_skipped budget_id={budget_id!r} reason=notifications_disabled")
                return

            window = _effective_dedup_window(budget_duration)
            if window is None:
                # Malformed / empty budget_duration → we cannot compute a safe
                # dedup window. Skip conservatively rather than notify on every
                # request (CR-009).
                logger.warning(
                    f"budget_notify_skipped budget_id={budget_id!r} "
                    f"reason=invalid_budget_duration duration={budget_duration!r}"
                )
                return

            # Fetch sibling project budgets for the breakdown section.
            sibling_budgets: list[BudgetCategorySnapshot] | None = None
            if project_name:
                sibling_budgets = await _fetch_project_budget_snapshots(session, budget_id=budget_id)

            now = datetime.now(timezone.utc)

            # Atomically claim the notification slot BEFORE sending. Concurrent
            # callers each race the same conditional UPDATE; exactly one wins
            # per dedup window (CR-002, CR-008).
            claimed = await budget_repository.try_claim_soft_limit_notification(
                session, budget_id=budget_id, now=now, window=window, notify_once=notify_once
            )
            if not claimed:
                logger.debug(f"budget_notify_skipped budget_id={budget_id!r} reason=dedup_window")
                return
            # Commit the reservation before dispatching the email so the slot
            # is durable even if the send hangs / crashes / the worker restarts.
            await session.commit()

            await email_service.send_budget_soft_limit_notification(
                email=email,
                budget_name=budget_name,
                budget_id=resolved_budget_id,
                current_spend=current_spend,
                soft_limit=soft_limit,
                project_name=project_name,
                budget_category=budget_category,
                sibling_budgets=sibling_budgets,
            )

            logger.info(f"budget_notify_sent budget_id={budget_id!r}")
    except Exception as exc:
        logger.warning(
            f"budget_notify_failed budget_id={budget_id!r} error={exc}",
            exc_info=True,
        )
