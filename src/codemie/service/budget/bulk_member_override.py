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

from types import SimpleNamespace
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException, ValidationException
from codemie.repository.project_budget_repository import project_member_budget_assignment_repository
from codemie.rest_api.models.project_budget import BulkMemberOverrideItem, BulkMemberOverrideResult
from codemie.service.activity.activity_models import (
    ActivityDomain,
    ActivityEntityType,
    ActivityEventCreate,
    BudgetManagementEvent,
)
from codemie.service.activity.activity_repository import activity_event_repository
from codemie.service.budget.budget_enums import AllocationMode
from codemie.service.budget.budget_models import (
    Budget,
    ProjectBudgetAssignment,
    ProjectMemberBudgetAssignment,
    build_shared_project_budget_id,
)

if TYPE_CHECKING:
    from codemie.rest_api.security.user import User
    from codemie.service.budget.project_budget_service import ProjectBudgetService

IndexedItems = list[tuple[int, BulkMemberOverrideItem]]
LoadedBudgets = dict[str, tuple[Budget, ProjectBudgetAssignment | None]]


def _failed_result(item: BulkMemberOverrideItem, code: int, message: str) -> BulkMemberOverrideResult:
    return BulkMemberOverrideResult(
        budget_id=item.budget_id,
        user_id=item.user_id,
        status="failed",
        code=code,
        message=message,
    )


class BulkMemberOverrideRunner:
    """Applies member overrides across budgets with one rebalance per touched budget."""

    def __init__(self, service: ProjectBudgetService) -> None:
        self._service = service

    def _screen_items(
        self,
        budget_items: IndexedItems,
        member_ids: set[str],
        failed: dict[int, BulkMemberOverrideResult],
    ) -> IndexedItems:
        passing: IndexedItems = []
        for index, item in budget_items:
            try:
                self._service._validate_member_amounts(item.allocated_max_budget, item.allocated_soft_budget)
            except ValidationException as exc:
                failed[index] = _failed_result(item, 400, str(exc))
                continue
            if item.user_id not in member_ids:
                failed[index] = _failed_result(item, 404, f"Member allocation not found for user '{item.user_id}'")
                continue
            passing.append((index, item))
        return passing

    def _validate_fixed_overrides(
        self,
        budget: Budget,
        allocations: list[ProjectMemberBudgetAssignment],
        passing: IndexedItems,
    ) -> str | None:
        copies = [
            SimpleNamespace(
                user_id=a.user_id,
                allocation_mode=a.allocation_mode,
                allocated_max_budget=a.allocated_max_budget,
                allocated_soft_budget=a.allocated_soft_budget,
            )
            for a in allocations
        ]
        copy_by_user = {copy.user_id: copy for copy in copies}
        for _index, item in passing:
            copy = copy_by_user[item.user_id]
            copy.allocation_mode = AllocationMode.FIXED.value
            copy.allocated_max_budget = item.allocated_max_budget
            copy.allocated_soft_budget = item.allocated_soft_budget
        try:
            self._service._equal_amounts_with_fixed_overrides(copies, budget.max_budget, budget.soft_budget)
        except ValidationException as exc:
            return str(exc)
        return None

    async def check(
        self,
        session: AsyncSession,
        items: IndexedItems,
    ) -> tuple[dict[int, BulkMemberOverrideResult], IndexedItems, LoadedBudgets]:
        """Validate items without writing; return failed results, accepted items and loaded budgets."""
        by_budget: dict[str, IndexedItems] = {}
        for index, item in items:
            by_budget.setdefault(item.budget_id, []).append((index, item))

        failed: dict[int, BulkMemberOverrideResult] = {}
        accepted: IndexedItems = []
        loaded: LoadedBudgets = {}
        for budget_id, budget_items in by_budget.items():
            try:
                budget, assignment, allocations = await self._service.get_project_budget(session, budget_id)
            except ExtendedHTTPException as exc:
                for index, item in budget_items:
                    failed[index] = _failed_result(item, exc.code, exc.message)
                continue
            passing = self._screen_items(budget_items, {a.user_id for a in allocations}, failed)
            if not passing:
                continue
            error = self._validate_fixed_overrides(budget, allocations, passing)
            if error is not None:
                for index, item in passing:
                    failed[index] = _failed_result(item, 400, error)
                continue
            accepted.extend(passing)
            loaded[budget_id] = (budget, assignment)
        accepted.sort(key=lambda pair: pair[0])
        return failed, accepted, loaded

    async def _write_override(
        self,
        session: AsyncSession,
        *,
        budget: Budget,
        assignment: ProjectBudgetAssignment | None,
        item: BulkMemberOverrideItem,
        actor_id: str,
    ) -> None:
        budget_id = item.budget_id
        allocation = await project_member_budget_assignment_repository.update_member_override(
            session,
            budget_id=budget_id,
            user_id=item.user_id,
            allocated_max_budget=item.allocated_max_budget,
            allocated_soft_budget=item.allocated_soft_budget,
            override_reason=item.override_reason,
            assigned_by=actor_id,
        )
        if allocation is None:
            raise ExtendedHTTPException(code=404, message=f"Member allocation not found for user '{item.user_id}'")
        override_budget = await self._service._ensure_override_child_budget(
            session,
            main_budget=budget,
            project_name=assignment.project_name if assignment else "",
            user_id=item.user_id,
            actor_id=actor_id,
            allocated_soft_budget=item.allocated_soft_budget,
            allocated_max_budget=item.allocated_max_budget,
        )
        allocation = await project_member_budget_assignment_repository.update_member_budget_routing(
            session,
            allocation_id=allocation.id,
            shared_budget_id=allocation.shared_budget_id or build_shared_project_budget_id(budget_id),
            override_budget_id=override_budget.budget_id,
            effective_budget_id=override_budget.budget_id,
            allocation_mode=AllocationMode.FIXED.value,
            override_reason=item.override_reason,
        )
        if allocation is None:
            raise ExtendedHTTPException(code=404, message=f"Member allocation not found for user '{item.user_id}'")

    async def _recheck_budget(self, session: AsyncSession, budget_id: str, budget: Budget) -> None:
        allocations = await project_member_budget_assignment_repository.get_active_by_budget_id(
            session, budget_id, populate_existing=True
        )
        await session.refresh(budget)
        try:
            self._service._equal_amounts_with_fixed_overrides(allocations, budget.max_budget, budget.soft_budget)
        except ValidationException as exc:
            raise ExtendedHTTPException(code=400, message=str(exc)) from exc

    @staticmethod
    async def _record_override(
        session: AsyncSession,
        *,
        budget: Budget,
        assignment: ProjectBudgetAssignment | None,
        item: BulkMemberOverrideItem,
        actor: User,
    ) -> None:
        await activity_event_repository.async_insert(
            ActivityEventCreate(
                domain=ActivityDomain.BUDGET_MANAGEMENT,
                event_type=BudgetManagementEvent.MEMBER_ALLOCATION_OVERRIDDEN,
                entity_type=ActivityEntityType.PROJECT_BUDGET_GROUP,
                entity_id=item.budget_id,
                entity_label=budget.name,
                actor_id=actor.id,
                attributes={
                    "user_id": item.user_id,
                    "allocated_max_budget": item.allocated_max_budget,
                    "allocated_soft_budget": item.allocated_soft_budget,
                },
                project_name=assignment.project_name if assignment else None,
                actor_email=actor.email,
                actor_display_name=actor.name or None,
            ),
            session,
        )
        logger.info(
            f"budget_event=member_allocation_override_completed component=project_budget_service "
            f"project_name={assignment.project_name if assignment else None!r} budget_id={item.budget_id!r} "
            f"budget_category={budget.budget_category!r} user_id={item.user_id!r} "
            f"allocated_max_budget={item.allocated_max_budget!r} "
            f"allocated_soft_budget={item.allocated_soft_budget!r} "
            f"actor_id={actor.id!r} domain=budget_management"
        )

    async def run(
        self,
        session: AsyncSession,
        items: IndexedItems,
        actor: User,
    ) -> dict[int, BulkMemberOverrideResult]:
        results, accepted, loaded = await self.check(session, items)
        if not accepted:
            return results

        for _index, item in accepted:
            budget, assignment = loaded[item.budget_id]
            await self._write_override(session, budget=budget, assignment=assignment, item=item, actor_id=actor.id)

        await session.flush()
        for budget_id, (budget, _assignment) in loaded.items():
            await self._recheck_budget(session, budget_id, budget)

        for budget_id in loaded:
            await self._service.rebalance_project_budget(session, budget_id, actor)

        for index, item in accepted:
            budget, assignment = loaded[item.budget_id]
            await self._record_override(session, budget=budget, assignment=assignment, item=item, actor=actor)
            results[index] = BulkMemberOverrideResult(budget_id=item.budget_id, user_id=item.user_id, status="ok")
        return results
