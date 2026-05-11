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

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from codemie.service.budget.budget_models import ProjectBudgetAssignment, ProjectMemberBudgetAssignment
from codemie.service.spend_tracking.spend_models import ProjectSpendTracking


@dataclass
class ProjectBudgetContext:
    """Flattened result of the project budget JOIN query used by BudgetResolutionService."""

    budget_id: str
    allocation_id: str
    effective_budget_id: str | None = None
    shared_budget_id: str | None = None
    override_budget_id: str | None = None
    budget_provider_metadata: dict = field(default_factory=dict)
    member_provider_metadata: dict = field(default_factory=dict)


@dataclass
class ProjectAssignedBudgetSummaryRow:
    """Compact project budget summary for project list enrichment."""

    project_name: str
    budget_id: str
    name: str
    budget_category: str
    soft_budget: float
    max_budget: float
    budget_duration: str
    budget_reset_at: str | None
    provider_sync_status: str | None
    member_count: int
    allocated_member_budget_total: float
    current_spending: float | None


@dataclass
class ResetWindowMemberAllocationRow:
    """Active member allocation whose linked project budget resets within the requested window."""

    allocation_id: str
    project_name: str
    budget_id: str
    budget_category: str
    user_id: str
    provider_metadata: dict
    budget_reset_at: str


@dataclass
class ParentMemberResetPairRow:
    """Parent/member reset timestamp pair used for drift detection."""

    allocation_id: str
    budget_id: str
    parent_budget_reset_at: str | None
    member_budget_reset_at: str | None


class ProjectBudgetAssignmentRepository:
    """Async repository for project_budget_assignments."""

    async def insert(
        self,
        session: AsyncSession,
        assignment: ProjectBudgetAssignment,
    ) -> ProjectBudgetAssignment:
        session.add(assignment)
        await session.flush()
        await session.refresh(assignment)
        return assignment

    async def get_active_by_project_category(
        self,
        session: AsyncSession,
        project_name: str,
        budget_category: str,
    ) -> ProjectBudgetAssignment | None:
        """Return the active assignment for (project_name, budget_category) or None."""
        stmt = select(ProjectBudgetAssignment).where(
            ProjectBudgetAssignment.project_name == project_name,
            ProjectBudgetAssignment.budget_category == budget_category,
            ProjectBudgetAssignment.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    async def get_active_by_budget_id(
        self,
        session: AsyncSession,
        budget_id: str,
    ) -> ProjectBudgetAssignment | None:
        """Return the active assignment for the given budget_id or None."""
        stmt = select(ProjectBudgetAssignment).where(
            ProjectBudgetAssignment.budget_id == budget_id,
            ProjectBudgetAssignment.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    async def get_active_for_project(
        self,
        session: AsyncSession,
        project_name: str,
    ) -> list[ProjectBudgetAssignment]:
        """Return all active assignments for the given project."""
        stmt = select(ProjectBudgetAssignment).where(
            ProjectBudgetAssignment.project_name == project_name,
            ProjectBudgetAssignment.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def soft_delete_by_user(
        self,
        session: AsyncSession,
        project_name: str,
        budget_category: str,
        user_id: str,
    ) -> int:
        """Soft-delete active member allocations for one project/category/user."""
        from datetime import datetime, timezone

        stmt = select(ProjectMemberBudgetAssignment).where(
            ProjectMemberBudgetAssignment.project_name == project_name,
            ProjectMemberBudgetAssignment.budget_category == budget_category,
            ProjectMemberBudgetAssignment.user_id == user_id,
            ProjectMemberBudgetAssignment.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
        now = datetime.now(tz=timezone.utc)
        for row in rows:
            row.deleted_at = now
            session.add(row)
        await session.flush()
        return len(rows)

    async def get_active_for_projects(
        self,
        session: AsyncSession,
        project_names: list[str],
    ) -> list[ProjectBudgetAssignment]:
        """Return all active assignments for the given set of projects."""
        if not project_names:
            return []
        stmt = select(ProjectBudgetAssignment).where(
            ProjectBudgetAssignment.project_name.in_(project_names),
            ProjectBudgetAssignment.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_assigned_budget_summaries_for_projects(
        self,
        session: AsyncSession,
        project_names: list[str],
        budget_category: str | None = None,
    ) -> dict[str, list[ProjectAssignedBudgetSummaryRow]]:
        """Return compact budget summaries for the given projects."""
        if not project_names:
            return {}

        from sqlalchemy import and_, func
        from sqlalchemy.sql.selectable import Subquery

        from codemie.service.budget.budget_models import Budget

        def _latest_spend_subquery(spend_subject_type: str) -> Subquery:
            latest_spend_dates_subq = (
                select(
                    ProjectSpendTracking.project_name.label("project_name"),
                    ProjectSpendTracking.budget_id.label("budget_id"),
                    ProjectSpendTracking.budget_category.label("budget_category"),
                    func.max(ProjectSpendTracking.spend_date).label("max_spend_date"),
                )
                .where(ProjectSpendTracking.project_name.in_(project_names))
                .where(ProjectSpendTracking.spend_subject_type == spend_subject_type)
                .group_by(
                    ProjectSpendTracking.project_name,
                    ProjectSpendTracking.budget_id,
                    ProjectSpendTracking.budget_category,
                )
                .subquery()
            )

            return (
                select(
                    ProjectSpendTracking.project_name.label("project_name"),
                    ProjectSpendTracking.budget_id.label("budget_id"),
                    ProjectSpendTracking.budget_category.label("budget_category"),
                    ProjectSpendTracking.budget_period_spend.label("current_spending"),
                )
                .join(
                    latest_spend_dates_subq,
                    and_(
                        ProjectSpendTracking.project_name == latest_spend_dates_subq.c.project_name,
                        ProjectSpendTracking.budget_id == latest_spend_dates_subq.c.budget_id,
                        ProjectSpendTracking.budget_category == latest_spend_dates_subq.c.budget_category,
                        ProjectSpendTracking.spend_date == latest_spend_dates_subq.c.max_spend_date,
                    ),
                )
                .where(ProjectSpendTracking.spend_subject_type == spend_subject_type)
                .subquery()
            )

        latest_project_budget_spend_subq = _latest_spend_subquery("project_budget")
        latest_legacy_budget_spend_subq = _latest_spend_subquery("budget")
        current_spending_expr = func.coalesce(
            latest_project_budget_spend_subq.c.current_spending,
            latest_legacy_budget_spend_subq.c.current_spending,
        ).label("current_spending")

        stmt = (
            select(
                ProjectBudgetAssignment.project_name,
                Budget.budget_id,
                Budget.name,
                ProjectBudgetAssignment.budget_category,
                Budget.soft_budget,
                Budget.max_budget,
                Budget.budget_duration,
                Budget.budget_reset_at,
                Budget.provider_metadata,
                current_spending_expr,
                func.count(ProjectMemberBudgetAssignment.id).label("member_count"),
                func.coalesce(func.sum(ProjectMemberBudgetAssignment.allocated_max_budget), 0.0).label(
                    "allocated_member_budget_total",
                ),
            )
            .join(Budget, Budget.budget_id == ProjectBudgetAssignment.budget_id)
            .outerjoin(
                latest_project_budget_spend_subq,
                and_(
                    latest_project_budget_spend_subq.c.project_name == ProjectBudgetAssignment.project_name,
                    latest_project_budget_spend_subq.c.budget_id == Budget.budget_id,
                    latest_project_budget_spend_subq.c.budget_category == ProjectBudgetAssignment.budget_category,
                ),
            )
            .outerjoin(
                latest_legacy_budget_spend_subq,
                and_(
                    latest_legacy_budget_spend_subq.c.project_name == ProjectBudgetAssignment.project_name,
                    latest_legacy_budget_spend_subq.c.budget_id == Budget.budget_id,
                    latest_legacy_budget_spend_subq.c.budget_category == ProjectBudgetAssignment.budget_category,
                ),
            )
            .outerjoin(
                ProjectMemberBudgetAssignment,
                and_(
                    ProjectMemberBudgetAssignment.project_name == ProjectBudgetAssignment.project_name,
                    ProjectMemberBudgetAssignment.budget_category == ProjectBudgetAssignment.budget_category,
                    ProjectMemberBudgetAssignment.project_budget_id == Budget.budget_id,
                    ProjectMemberBudgetAssignment.deleted_at.is_(None),
                ),
            )
            .where(
                ProjectBudgetAssignment.project_name.in_(project_names),
                ProjectBudgetAssignment.deleted_at.is_(None),
                Budget.deleted_at.is_(None),
            )
            .group_by(
                ProjectBudgetAssignment.project_name,
                Budget.budget_id,
                Budget.name,
                ProjectBudgetAssignment.budget_category,
                Budget.soft_budget,
                Budget.max_budget,
                Budget.budget_duration,
                Budget.budget_reset_at,
                Budget.provider_metadata,
                latest_project_budget_spend_subq.c.current_spending,
                latest_legacy_budget_spend_subq.c.current_spending,
            )
            .order_by(ProjectBudgetAssignment.project_name.asc(), Budget.budget_category.asc())
        )
        if budget_category is not None:
            stmt = stmt.where(ProjectBudgetAssignment.budget_category == budget_category)

        result = await session.execute(stmt)
        summaries: dict[str, list[ProjectAssignedBudgetSummaryRow]] = {}
        for row in result.mappings().all():
            provider_metadata = row["provider_metadata"] or {}
            summary = ProjectAssignedBudgetSummaryRow(
                project_name=row["project_name"],
                budget_id=row["budget_id"],
                name=row["name"],
                budget_category=row["budget_category"],
                soft_budget=row["soft_budget"],
                max_budget=row["max_budget"],
                budget_duration=row["budget_duration"],
                budget_reset_at=row["budget_reset_at"],
                provider_sync_status=provider_metadata.get("sync_status"),
                member_count=row["member_count"],
                allocated_member_budget_total=row["allocated_member_budget_total"],
                current_spending=float(row["current_spending"]) if row["current_spending"] is not None else None,
            )
            summaries.setdefault(summary.project_name, []).append(summary)
        return summaries

    async def get_project_budget_context(
        self,
        session: AsyncSession,
        project_name: str,
        budget_category: str,
        user_id: str,
    ) -> ProjectBudgetContext | None:
        """Return all fields needed to build ResolvedBudgetContext in one JOIN query.

        Replaces three sequential SELECTs (project assignment, member allocation,
        budget provider_metadata) with a single round-trip.

        Returns None when no active project assignment + member allocation exists
        for the given (project_name, budget_category, user_id) triple.
        """
        from sqlalchemy import text

        stmt = text(
            """
            SELECT pba.budget_id,
                   pmba.id                     AS allocation_id,
                   pmba.effective_budget_id    AS effective_budget_id,
                   pmba.shared_budget_id       AS shared_budget_id,
                   pmba.override_budget_id     AS override_budget_id,
                   b.provider_metadata         AS budget_meta,
                   pmba.pmba_provider_metadata AS member_meta
            FROM   project_budget_assignments pba
            JOIN   project_member_budget_assignments pmba
                     ON  pmba.project_name    = pba.project_name
                     AND pmba.budget_category = pba.budget_category
                     AND pmba.user_id         = :user_id
                     AND pmba.pmba_deleted_at IS NULL
            JOIN   budgets b ON b.budget_id = pba.budget_id
            WHERE  pba.project_name    = :project_name
              AND  pba.budget_category = :budget_category
              AND  pba.deleted_at IS NULL
            LIMIT 1
            """
        )
        result = await session.execute(
            stmt,
            {"project_name": project_name, "budget_category": budget_category, "user_id": user_id},
        )
        row = result.mappings().first()
        if row is None:
            return None
        return ProjectBudgetContext(
            budget_id=row["budget_id"],
            allocation_id=row["allocation_id"],
            effective_budget_id=row.get("effective_budget_id"),
            shared_budget_id=row.get("shared_budget_id"),
            override_budget_id=row.get("override_budget_id"),
            budget_provider_metadata=row["budget_meta"] or {},
            member_provider_metadata=row["member_meta"] or {},
        )

    async def get_project_budget_categories_batch(
        self,
        session: AsyncSession,
        project_name: str,
        user_id: str,
        categories: list[str],
    ) -> dict[str, ProjectBudgetContext]:
        """Return project budget contexts for all requested categories in one query."""
        if not categories:
            return {}

        stmt = text(
            """
            SELECT pba.budget_category,
                   pba.budget_id,
                   pmba.id                     AS allocation_id,
                   pmba.effective_budget_id    AS effective_budget_id,
                   pmba.shared_budget_id       AS shared_budget_id,
                   pmba.override_budget_id     AS override_budget_id,
                   b.provider_metadata         AS budget_meta,
                   pmba.pmba_provider_metadata AS member_meta
            FROM   project_budget_assignments pba
            JOIN   project_member_budget_assignments pmba
                     ON  pmba.project_name    = pba.project_name
                     AND pmba.budget_category = pba.budget_category
                     AND pmba.user_id         = :user_id
                     AND pmba.pmba_deleted_at IS NULL
            JOIN   budgets b ON b.budget_id = pba.budget_id
            WHERE  pba.project_name = :project_name
              AND  pba.budget_category = ANY(:categories)
              AND  pba.deleted_at IS NULL
            """
        )
        result = await session.execute(
            stmt,
            {
                "project_name": project_name,
                "user_id": user_id,
                "categories": categories,
            },
        )

        return {
            row["budget_category"]: ProjectBudgetContext(
                budget_id=row["budget_id"],
                allocation_id=row["allocation_id"],
                effective_budget_id=row.get("effective_budget_id"),
                shared_budget_id=row.get("shared_budget_id"),
                override_budget_id=row.get("override_budget_id"),
                budget_provider_metadata=row["budget_meta"] or {},
                member_provider_metadata=row["member_meta"] or {},
            )
            for row in result.mappings().all()
        }

    async def soft_delete(
        self,
        session: AsyncSession,
        assignment_id: str,
    ) -> None:
        """Soft-delete an assignment by setting deleted_at to now."""
        from datetime import datetime, timezone

        stmt = select(ProjectBudgetAssignment).where(ProjectBudgetAssignment.id == assignment_id)
        result = await session.execute(stmt)
        row = result.scalars().first()
        if row is not None:
            row.deleted_at = datetime.now(tz=timezone.utc)
            session.add(row)
            await session.flush()


class ProjectMemberBudgetAssignmentRepository:
    """Async repository for project_member_budget_assignments."""

    async def insert_many(
        self,
        session: AsyncSession,
        rows: list[ProjectMemberBudgetAssignment],
    ) -> list[ProjectMemberBudgetAssignment]:
        """Persist a list of new member allocation rows."""
        for row in rows:
            session.add(row)
        await session.flush()
        for row in rows:
            await session.refresh(row)
        return rows

    async def get_active_by_project_category(
        self,
        session: AsyncSession,
        project_name: str,
        budget_category: str,
    ) -> list[ProjectMemberBudgetAssignment]:
        """Return all active member allocations for (project_name, budget_category)."""
        stmt = select(ProjectMemberBudgetAssignment).where(
            ProjectMemberBudgetAssignment.project_name == project_name,
            ProjectMemberBudgetAssignment.budget_category == budget_category,
            ProjectMemberBudgetAssignment.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_active_by_project_category_user(
        self,
        session: AsyncSession,
        project_name: str,
        budget_category: str,
        user_id: str,
    ) -> ProjectMemberBudgetAssignment | None:
        """Return the active allocation for a specific member or None."""
        stmt = select(ProjectMemberBudgetAssignment).where(
            ProjectMemberBudgetAssignment.project_name == project_name,
            ProjectMemberBudgetAssignment.budget_category == budget_category,
            ProjectMemberBudgetAssignment.user_id == user_id,
            ProjectMemberBudgetAssignment.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    async def get_active_by_budget_id(
        self,
        session: AsyncSession,
        budget_id: str,
    ) -> list[ProjectMemberBudgetAssignment]:
        """Return all active member allocations for the given project_budget_id."""
        stmt = select(ProjectMemberBudgetAssignment).where(
            ProjectMemberBudgetAssignment.project_budget_id == budget_id,
            ProjectMemberBudgetAssignment.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def list_overdue_reset_member_allocations(
        self,
        session: AsyncSession,
        now: datetime,
    ) -> list[ProjectMemberBudgetAssignment]:
        """Return active member allocations whose own reset timestamp is overdue."""
        stmt = (
            select(ProjectMemberBudgetAssignment)
            .where(ProjectMemberBudgetAssignment.deleted_at.is_(None))
            .where(ProjectMemberBudgetAssignment.budget_reset_at.is_not(None))
            .where(text("CAST(project_member_budget_assignments.budget_reset_at AS timestamptz) < :now"))
        )
        result = await session.execute(stmt, {"now": now})
        return list(result.scalars().all())

    async def list_parent_member_reset_pairs(
        self,
        session: AsyncSession,
        budget_ids: list[str],
    ) -> list[ParentMemberResetPairRow]:
        """Return active parent/member reset timestamp pairs for the given project budgets."""
        if not budget_ids:
            return []

        stmt = text(
            """
            SELECT pmba.id AS allocation_id,
                   pmba.project_budget_id AS budget_id,
                   b.budget_reset_at AS parent_budget_reset_at,
                   pmba.budget_reset_at AS member_budget_reset_at
            FROM project_member_budget_assignments pmba
            JOIN budgets b
              ON b.budget_id = pmba.project_budget_id
            WHERE pmba.pmba_deleted_at IS NULL
              AND b.deleted_at IS NULL
              AND pmba.project_budget_id = ANY(:budget_ids)
            """
        )
        result = await session.execute(stmt, {"budget_ids": budget_ids})
        return [
            ParentMemberResetPairRow(
                allocation_id=row["allocation_id"],
                budget_id=row["budget_id"],
                parent_budget_reset_at=row["parent_budget_reset_at"],
                member_budget_reset_at=row["member_budget_reset_at"],
            )
            for row in result.mappings().all()
        ]

    async def get_allocations_resetting_within_window(
        self,
        session: AsyncSession,
        window_start: datetime,
        window_end: datetime,
    ) -> list[ResetWindowMemberAllocationRow]:
        """Return active member allocations whose linked budget resets inside the requested window."""
        stmt = text(
            """
            SELECT pmba.id AS allocation_id,
                   pmba.project_name,
                   pmba.project_budget_id AS budget_id,
                   pmba.budget_category,
                   pmba.user_id,
                   pmba.pmba_provider_metadata AS provider_metadata,
                   b.budget_reset_at
            FROM project_member_budget_assignments pmba
            JOIN budgets b
              ON b.budget_id = pmba.project_budget_id
            WHERE pmba.pmba_deleted_at IS NULL
              AND b.deleted_at IS NULL
              AND b.budget_reset_at IS NOT NULL
              AND CAST(b.budget_reset_at AS timestamptz) >= :window_start
              AND CAST(b.budget_reset_at AS timestamptz) <= :window_end
            ORDER BY CAST(b.budget_reset_at AS timestamptz), pmba.project_name, pmba.user_id
            """
        )
        result = await session.execute(stmt, {"window_start": window_start, "window_end": window_end})
        return [
            ResetWindowMemberAllocationRow(
                allocation_id=row["allocation_id"],
                project_name=row["project_name"],
                budget_id=row["budget_id"],
                budget_category=row["budget_category"],
                user_id=row["user_id"],
                provider_metadata=row["provider_metadata"] or {},
                budget_reset_at=row["budget_reset_at"],
            )
            for row in result.mappings().all()
        ]

    async def update_provider_metadata(
        self,
        session: AsyncSession,
        allocation_id: str,
        provider_metadata: dict,
        sync_status: str,
        budget_reset_at: str | None = None,
    ) -> None:
        """Update provider_metadata and sync_status for a single allocation."""
        stmt = select(ProjectMemberBudgetAssignment).where(
            ProjectMemberBudgetAssignment.id == allocation_id,
        )
        result = await session.execute(stmt)
        row = result.scalars().first()
        if row is not None:
            row.provider_metadata = provider_metadata
            row.sync_status = sync_status
            if budget_reset_at is not None:
                row.budget_reset_at = budget_reset_at
            session.add(row)
            await session.flush()

    async def update_budget_reset_at(
        self,
        session: AsyncSession,
        allocation_id: str,
        budget_reset_at: str,
    ) -> ProjectMemberBudgetAssignment | None:
        """Update only the stored provider reset timestamp for one member allocation."""
        stmt = select(ProjectMemberBudgetAssignment).where(ProjectMemberBudgetAssignment.id == allocation_id)
        result = await session.execute(stmt)
        row = result.scalars().first()
        if row is None:
            return None
        row.budget_reset_at = budget_reset_at
        session.add(row)
        await session.flush()
        await session.refresh(row)
        return row

    async def update_allocation(
        self,
        session: AsyncSession,
        allocation_id: str,
        allocated_max_budget: float,
        allocated_soft_budget: float,
    ) -> ProjectMemberBudgetAssignment | None:
        """Update allocated amounts for a single allocation. Returns the updated row."""
        stmt = select(ProjectMemberBudgetAssignment).where(
            ProjectMemberBudgetAssignment.id == allocation_id,
        )
        result = await session.execute(stmt)
        row = result.scalars().first()
        if row is not None:
            row.allocated_max_budget = allocated_max_budget
            row.allocated_soft_budget = allocated_soft_budget
            session.add(row)
            await session.flush()
            await session.refresh(row)
        return row

    async def update_member_budget_routing(
        self,
        session: AsyncSession,
        *,
        allocation_id: str,
        shared_budget_id: str | None,
        override_budget_id: str | None,
        effective_budget_id: str | None,
        allocation_mode: str,
        override_reason: str | None = None,
    ) -> ProjectMemberBudgetAssignment | None:
        """Update the effective child-budget routing for one member allocation."""
        stmt = select(ProjectMemberBudgetAssignment).where(ProjectMemberBudgetAssignment.id == allocation_id)
        result = await session.execute(stmt)
        row = result.scalars().first()
        if row is None:
            return None
        row.shared_budget_id = shared_budget_id
        row.override_budget_id = override_budget_id
        row.effective_budget_id = effective_budget_id
        row.allocation_mode = allocation_mode
        row.override_reason = override_reason
        session.add(row)
        await session.flush()
        await session.refresh(row)
        return row

    async def update_member_override(
        self,
        session: AsyncSession,
        budget_id: str,
        user_id: str,
        allocated_max_budget: float,
        allocated_soft_budget: float,
        override_reason: str | None,
        assigned_by: str,
    ) -> ProjectMemberBudgetAssignment | None:
        """Set a fixed member allocation override."""
        stmt = select(ProjectMemberBudgetAssignment).where(
            ProjectMemberBudgetAssignment.project_budget_id == budget_id,
            ProjectMemberBudgetAssignment.user_id == user_id,
            ProjectMemberBudgetAssignment.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        row = result.scalars().first()
        if row is None:
            return None
        row.allocation_mode = "fixed"
        row.allocated_max_budget = allocated_max_budget
        row.allocated_soft_budget = allocated_soft_budget
        row.override_reason = override_reason
        row.assigned_by = assigned_by
        session.add(row)
        await session.flush()
        await session.refresh(row)
        return row

    async def clear_member_override(
        self,
        session: AsyncSession,
        budget_id: str,
        user_id: str,
    ) -> ProjectMemberBudgetAssignment | None:
        """Clear a fixed member allocation override."""
        stmt = select(ProjectMemberBudgetAssignment).where(
            ProjectMemberBudgetAssignment.project_budget_id == budget_id,
            ProjectMemberBudgetAssignment.user_id == user_id,
            ProjectMemberBudgetAssignment.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        row = result.scalars().first()
        if row is None:
            return None
        row.allocation_mode = "equal"
        row.override_reason = None
        session.add(row)
        await session.flush()
        await session.refresh(row)
        return row

    async def soft_delete_missing_members(
        self,
        session: AsyncSession,
        project_name: str,
        budget_category: str,
        active_user_ids: list[str],
    ) -> int:
        """Soft-delete active allocations whose user_id is not in active_user_ids.

        Used after a membership sync to prune stale rows for members who were
        removed outside the normal assignment flow.

        Returns:
            Number of rows soft-deleted.
        """
        from datetime import datetime, timezone

        stmt = select(ProjectMemberBudgetAssignment).where(
            ProjectMemberBudgetAssignment.project_name == project_name,
            ProjectMemberBudgetAssignment.budget_category == budget_category,
            ProjectMemberBudgetAssignment.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
        active_set = set(active_user_ids)
        now = datetime.now(tz=timezone.utc)
        removed = 0
        for row in rows:
            if row.user_id not in active_set:
                row.deleted_at = now
                session.add(row)
                removed += 1
        if removed:
            await session.flush()
        return removed

    async def soft_delete_all_by_budget_id(
        self,
        session: AsyncSession,
        budget_id: str,
    ) -> int:
        """Soft-delete all active member allocations for a project budget. Returns count."""
        from datetime import datetime, timezone

        stmt = select(ProjectMemberBudgetAssignment).where(
            ProjectMemberBudgetAssignment.project_budget_id == budget_id,
            ProjectMemberBudgetAssignment.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
        now = datetime.now(tz=timezone.utc)
        for row in rows:
            row.deleted_at = now
            session.add(row)
        await session.flush()
        return len(rows)


project_budget_assignment_repository = ProjectBudgetAssignmentRepository()
project_member_budget_assignment_repository = ProjectMemberBudgetAssignmentRepository()
