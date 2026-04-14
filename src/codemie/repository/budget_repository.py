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

from typing import TYPE_CHECKING

from sqlalchemy import func, or_, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from codemie.service.budget.budget_models import Budget, UserBudgetAssignment

if TYPE_CHECKING:
    from codemie.enterprise.litellm.budget_categories import BudgetCategory


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

    async def list_paginated(
        self,
        session: AsyncSession,
        page: int,
        per_page: int,
        category: str | None = None,
    ) -> tuple[list[Budget], int]:
        """SELECT with optional WHERE budget_category = category, OFFSET/LIMIT, COUNT."""
        base_stmt = select(Budget)
        if category is not None:
            base_stmt = base_stmt.where(Budget.budget_category == category)

        count_stmt = select(func.count()).select_from(base_stmt.subquery())
        total = int((await session.execute(count_stmt)).scalar_one())

        data_stmt = base_stmt.order_by(Budget.created_at.desc()).offset(page * per_page).limit(per_page)
        result = await session.execute(data_stmt)
        return list(result.scalars().all()), total

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

    async def get_all_keyed_by_id(self, session: AsyncSession) -> dict[str, Budget]:
        """SELECT all rows, return dict keyed by budget_id. Used by sync."""
        result = await session.execute(select(Budget))
        return {b.budget_id: b for b in result.scalars().all()}

    async def upsert_from_litellm(
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

        litellm_owned = {"soft_budget", "max_budget", "budget_duration", "budget_reset_at"}
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
