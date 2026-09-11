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

from datetime import datetime, timezone

from datetime import timedelta

from sqlalchemy import func, or_, text, update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session, select

from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.budget.budget_models import Budget, UserBudgetAssignment


class BudgetRepository:
    """Async repository for budgets and user budget assignments."""

    async def insert(self, session: AsyncSession, budget: Budget) -> Budget:
        """Persist new Budget row.

        Raises IntegrityError on duplicate budget_id or name.
        """
        session.add(budget)
        await session.flush()
        await session.refresh(budget)
        return budget

    async def get_by_id(self, session: AsyncSession, budget_id: str) -> Budget | None:
        """Return Budget by primary key or None."""
        stmt = select(Budget).where(Budget.budget_id == budget_id)
        result = await session.execute(stmt)
        return result.scalars().first()

    async def get_by_name(self, session: AsyncSession, name: str) -> Budget | None:
        """Return Budget by unique name or None (used for duplicate-name check)."""
        stmt = select(Budget).where(Budget.name == name)
        result = await session.execute(stmt)
        return result.scalars().first()

    async def get_child_budget(
        self,
        session: AsyncSession,
        *,
        parent_budget_id: str,
        budget_origin_type: str,
        owner_user_id: str | None = None,
    ) -> Budget | None:
        """Return an active child budget for the given parent/origin/user tuple."""
        stmt = select(Budget).where(
            Budget.parent_budget_id == parent_budget_id,
            Budget.budget_origin_type == budget_origin_type,
            Budget.deleted_at.is_(None),
        )
        if owner_user_id is None:
            stmt = stmt.where(Budget.owner_user_id.is_(None))
        else:
            stmt = stmt.where(Budget.owner_user_id == owner_user_id)
        result = await session.execute(stmt)
        return result.scalars().first()

    async def list_active_child_budgets(
        self,
        session: AsyncSession,
        *,
        parent_budget_id: str,
    ) -> list[Budget]:
        """Return all active child budgets for a parent budget."""
        stmt = select(Budget).where(
            Budget.parent_budget_id == parent_budget_id,
            Budget.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def list_paginated(
        self,
        session: AsyncSession,
        page: int,
        per_page: int,
        category: str | None = None,
        budget_type: str | None = None,
    ) -> tuple[list[Budget], int]:
        """SELECT with optional WHERE filters, OFFSET/LIMIT, COUNT."""
        base_stmt = select(Budget)
        if category is not None:
            base_stmt = base_stmt.where(Budget.budget_category == category)
        if budget_type is not None:
            base_stmt = base_stmt.where(Budget.budget_type == budget_type)

        count_stmt = select(func.count()).select_from(base_stmt.subquery())
        total = int((await session.execute(count_stmt)).scalar_one())

        data_stmt = base_stmt.order_by(Budget.created_at.desc()).offset(page * per_page).limit(per_page)
        result = await session.execute(data_stmt)
        return list(result.scalars().all()), total

    async def try_claim_soft_limit_notification(
        self,
        session: AsyncSession,
        *,
        budget_id: str,
        now: datetime,
        window: timedelta,
        notify_once: bool = False,
    ) -> bool:
        """Atomically claim the soft-limit notification slot for ``budget_id`` (EPMCDME-13959).

        Uses a conditional UPDATE so concurrent callers race safely: exactly one
        wins the slot when the dedup window has elapsed.

        When ``notify_once`` is True the slot fires only if ``soft_limit_notified_at``
        is NULL — i.e. exactly once per budget edit cycle, never repeating.

        Returns True iff this call claimed the slot.
        """
        if notify_once:
            condition = Budget.soft_limit_notified_at.is_(None)
        else:
            threshold = now - window
            condition = or_(
                Budget.soft_limit_notified_at.is_(None),
                Budget.soft_limit_notified_at < threshold,
            )
        stmt = (
            sa_update(Budget).where(Budget.budget_id == budget_id).where(condition).values(soft_limit_notified_at=now)
        )
        result = await session.execute(stmt)
        return (result.rowcount or 0) == 1

    async def update(self, session: AsyncSession, budget_id: str, fields: dict) -> Budget:
        """Partial update: apply provided values in fields dict."""
        from codemie.core.exceptions import ExtendedHTTPException

        budget = await self.get_by_id(session, budget_id)
        if budget is None:
            raise ExtendedHTTPException(code=404, message=f"Budget not found: {budget_id}")
        for key, value in fields.items():
            setattr(budget, key, value)
        session.add(budget)
        await session.flush()
        await session.refresh(budget)
        return budget

    async def detach_budget(self, session: AsyncSession, budget_id: str) -> Budget | None:
        """Mark a budget as detached from active routing while keeping it for audit."""
        budget = await self.get_by_id(session, budget_id)
        if budget is None:
            return None
        budget.detached_at = datetime.now(tz=timezone.utc)
        session.add(budget)
        await session.flush()
        await session.refresh(budget)
        return budget

    def clear_project_on_deleted_budgets(self, session: Session, project_name: str) -> list[str]:
        """Detach the project's soft-deleted budgets from it, returning their ids."""
        budget_ids = list(
            session.exec(
                select(Budget.budget_id).where(Budget.project_name == project_name, Budget.deleted_at.is_not(None))
            ).all()
        )
        if budget_ids:
            session.execute(sa_update(Budget).where(Budget.budget_id.in_(budget_ids)).values(project_name=None))
            session.flush()
        return budget_ids

    async def delete(self, session: AsyncSession, budget_id: str) -> None:
        """Hard delete row by primary key."""
        budget = await self.get_by_id(session, budget_id)
        if budget is not None:
            await session.delete(budget)
            await session.flush()

    async def count_assignments(self, session: AsyncSession, budget_id: str) -> int:
        """Return total user assignment rows referencing this budget_id."""
        uba_count_stmt = select(func.count()).where(UserBudgetAssignment.budget_id == budget_id)

        return int((await session.execute(uba_count_stmt)).scalar_one())

    async def count_project_assignments(self, session: AsyncSession, budget_id: str) -> int:
        """Return active project_budget_assignments referencing this budget_id."""
        from codemie.service.budget.budget_models import ProjectBudgetAssignment

        stmt = select(func.count()).where(
            ProjectBudgetAssignment.budget_id == budget_id,
            ProjectBudgetAssignment.deleted_at.is_(None),
        )
        return int((await session.execute(stmt)).scalar_one())

    async def get_all_keyed_by_id(self, session: AsyncSession) -> dict[str, Budget]:
        """SELECT all rows, return dict keyed by budget_id. Used by sync."""
        result = await session.execute(select(Budget))
        return {b.budget_id: b for b in result.scalars().all()}

    async def list_active_project_budgets(self, session: AsyncSession, project_name: str) -> list[Budget]:
        """Return all active (is_active=True, deleted_at IS NULL) budgets for a project."""
        stmt = (
            select(Budget)
            .where(Budget.project_name == project_name)
            .where(Budget.is_active.is_(True))
            .where(Budget.deleted_at.is_(None))
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def list_stopped_project_budgets(self, session: AsyncSession, project_name: str) -> list[Budget]:
        """Return all stopped (is_active=False, deleted_at IS NULL) budgets for a project."""
        stmt = (
            select(Budget)
            .where(Budget.project_name == project_name)
            .where(Budget.is_active.is_(False))
            .where(Budget.deleted_at.is_(None))
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def list_overdue_reset_budgets(self, session: AsyncSession, now: datetime) -> list[Budget]:
        """Return active budgets whose stored reset timestamp is overdue."""
        stmt = (
            select(Budget)
            .where(Budget.deleted_at.is_(None))
            .where(Budget.budget_reset_at.is_not(None))
            .where(text("CAST(budget_reset_at AS timestamptz) < :now"))
        )
        result = await session.execute(stmt, {"now": now})
        return list(result.scalars().all())

    async def update_budget_reset_at(
        self,
        session: AsyncSession,
        budget_id: str,
        budget_reset_at: str,
    ) -> Budget | None:
        """Update only the stored provider reset timestamp for one budget."""
        budget = await self.get_by_id(session, budget_id)
        if budget is None:
            return None
        budget.budget_reset_at = budget_reset_at
        session.add(budget)
        await session.flush()
        await session.refresh(budget)
        return budget

    async def get_by_ids(self, session: AsyncSession, budget_ids: list[str]) -> dict[str, Budget]:
        """SELECT rows matching budget_ids, return dict keyed by budget_id."""
        if not budget_ids:
            return {}
        result = await session.execute(select(Budget).where(Budget.budget_id.in_(budget_ids)))
        return {b.budget_id: b for b in result.scalars().all()}

    async def upsert_from_provider(
        self,
        session: AsyncSession,
        budget_id: str,
        fields: dict,
    ) -> tuple[Budget, str]:
        """Insert if not exists, otherwise update LiteLLM-owned fields.

        Returns (budget_row, status) where status is created, updated, or unchanged.
        """
        existing = await self.get_by_id(session, budget_id)
        if existing is None:
            budget = Budget(budget_id=budget_id, **fields)
            session.add(budget)
            await session.flush()
            await session.refresh(budget)
            return budget, "created"

        litellm_owned = {"soft_budget", "max_budget", "budget_duration", "budget_reset_at", "provider_metadata"}
        changed = False
        for key in litellm_owned:
            if key in fields and getattr(existing, key) != fields[key]:
                setattr(existing, key, fields[key])
                changed = True
        if changed:
            session.add(existing)
            await session.flush()
            await session.refresh(existing)
            return existing, "updated"
        return existing, "unchanged"

    async def get_user_id_by_identifier(
        self,
        session: AsyncSession,
        identifier: str,
    ) -> str | None:
        """Return active user id by username or email, or None if not found."""
        from codemie.rest_api.models.user_management import UserDB

        stmt = select(UserDB.id).where(
            or_(UserDB.username == identifier, UserDB.email == identifier),
            UserDB.is_active.is_(True),
            UserDB.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    async def upsert_user_category_assignment(
        self,
        session: AsyncSession,
        user_id: str,
        category: BudgetCategory,
        budget_id: str,
        assigned_by: str,
    ) -> None:
        """INSERT ON CONFLICT (user_id, category) DO UPDATE."""
        stmt = (
            pg_insert(UserBudgetAssignment)
            .values(
                user_id=user_id,
                category=category.value,
                budget_id=budget_id,
                assigned_by=assigned_by,
            )
            .on_conflict_do_update(
                index_elements=["user_id", "category"],
                set_={
                    "budget_id": pg_insert(UserBudgetAssignment).excluded.budget_id,
                    "assigned_by": pg_insert(UserBudgetAssignment).excluded.assigned_by,
                    "assigned_at": text("NOW()"),
                },
            )
        )
        await session.execute(stmt)
        await session.flush()

    async def delete_user_category_assignment(
        self,
        session: AsyncSession,
        user_id: str,
        category: BudgetCategory,
    ) -> None:
        """DELETE FROM user_budget_assignments WHERE user_id=? AND category=?"""
        stmt = select(UserBudgetAssignment).where(
            UserBudgetAssignment.user_id == user_id,
            UserBudgetAssignment.category == category.value,
        )
        result = await session.execute(stmt)
        row = result.scalars().first()
        if row is not None:
            await session.delete(row)
            await session.flush()

    async def get_user_category_assignments(
        self,
        session: AsyncSession,
        user_id: str,
    ) -> list[UserBudgetAssignment]:
        """Return all category assignments for a user; empty list if none."""
        stmt = select(UserBudgetAssignment).where(UserBudgetAssignment.user_id == user_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_user_category_budget_id(
        self,
        session: AsyncSession,
        user_id: str,
        category: BudgetCategory,
    ) -> str | None:
        """Return assigned budget_id for a user/category pair, or None."""
        stmt = select(UserBudgetAssignment.budget_id).where(
            UserBudgetAssignment.user_id == user_id,
            UserBudgetAssignment.category == category.value,
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    async def get_assignments_for_users(
        self,
        session: AsyncSession,
        user_ids: list[str],
    ) -> dict[str, list[UserBudgetAssignment]]:
        """Return all category assignments for multiple users, grouped by user_id."""
        if not user_ids:
            return {}
        stmt = select(UserBudgetAssignment).where(UserBudgetAssignment.user_id.in_(user_ids))
        result = await session.execute(stmt)
        rows = result.scalars().all()
        grouped: dict[str, list[UserBudgetAssignment]] = {}
        for row in rows:
            grouped.setdefault(row.user_id, []).append(row)
        return grouped


budget_repository = BudgetRepository()
