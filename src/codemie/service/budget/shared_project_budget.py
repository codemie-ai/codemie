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

"""Creation of a project budget's shared child row inside the caller's sync session."""

from __future__ import annotations

from sqlmodel import Session, select

from codemie.configs.logger import logger
from codemie.service.budget.budget_enums import AllocationMode
from codemie.service.budget.budget_models import (
    Budget,
    ProjectMemberBudgetAssignment,
    build_shared_project_budget_id,
    build_shared_project_budget_row,
    shared_project_budget_amounts,
)


def first_equal_member_allocation(
    session: Session,
    project_name: str,
    budget_category: str,
    project_budget_id: str,
) -> ProjectMemberBudgetAssignment | None:
    """Return the first active equal-mode member allocation of a project budget, if any."""
    return session.exec(
        select(ProjectMemberBudgetAssignment).where(
            ProjectMemberBudgetAssignment.project_name == project_name,
            ProjectMemberBudgetAssignment.budget_category == budget_category,
            ProjectMemberBudgetAssignment.project_budget_id == project_budget_id,
            ProjectMemberBudgetAssignment.allocation_mode == AllocationMode.EQUAL.value,
            ProjectMemberBudgetAssignment.deleted_at.is_(None),
        )
    ).first()


def ensure_shared_project_budget_row(session: Session, budget: Budget, project_name: str, actor_id: str | None) -> None:
    """Create the project budget's shared child row when it is missing; an existing row is left as is.

    A DB write only: the provider learns about the row on the next sync of the pending allocation.
    """
    shared_budget_id = build_shared_project_budget_id(budget.budget_id)
    if session.get(Budget, shared_budget_id) is not None:
        return
    from codemie.service.settings.settings import SettingsService

    soft_budget, max_budget = shared_project_budget_amounts(
        budget,
        first_equal_member_allocation(session, project_name, budget.budget_category, budget.budget_id),
        SettingsService.get_enforce_member_spend_limits(project_name),
    )
    session.add(
        Budget(
            **build_shared_project_budget_row(
                budget,
                project_name=project_name,
                actor_id=actor_id,
                soft_budget=soft_budget,
                max_budget=max_budget,
            )
        )
    )
    # No relationship() orders this insert before the allocation that references it.
    session.flush()
    logger.info(
        f"budget_event=project_shared_budget_created component=shared_project_budget "
        f"budget_id={shared_budget_id!r} parent_budget_id={budget.budget_id!r} project_name={project_name!r} "
        f"budget_category={budget.budget_category!r} soft_budget={soft_budget} max_budget={max_budget}"
    )
