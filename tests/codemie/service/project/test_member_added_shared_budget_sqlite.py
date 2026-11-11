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

"""Member add creates a missing shared budget row before the allocation that references it (EPMCDME-17061)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, func, select

from codemie.service.budget.budget_enums import AllocationMode, BudgetType, SyncStatus
from codemie.service.budget.budget_models import (
    Budget,
    ProjectBudgetAssignment,
    ProjectBudgetGroup,
    ProjectMemberBudgetAssignment,
    build_shared_project_budget_row,
)
from codemie.service.project.project_assignment_service import ProjectAssignmentService
from codemie.service.settings.settings import SettingsService
from tests.codemie.sqlite_harness import constrained_sqlite_engine

PROJECT = "p"
BUDGET_TABLES = [
    Budget.__table__,
    ProjectBudgetGroup.__table__,
    ProjectBudgetAssignment.__table__,
    ProjectMemberBudgetAssignment.__table__,
]


def _main_budget(budget_id: str = "b1", name: str = "p-platform", category: str = "platform") -> Budget:
    return Budget(
        budget_id=budget_id,
        budget_type=BudgetType.PROJECT.value,
        project_name=PROJECT,
        name=name,
        description="Project budget",
        soft_budget=80.0,
        max_budget=100.0,
        budget_duration="30d",
        budget_category=category,
        created_by="admin",
    )


def _add_budget_with_assignment(session: Session, budget: Budget) -> None:
    session.add(budget)
    session.add(
        ProjectBudgetAssignment(
            project_name=PROJECT,
            budget_category=budget.budget_category,
            budget_id=budget.budget_id,
            assigned_by="admin",
        )
    )


@pytest.fixture
def enforce_limit():
    return True


@pytest.fixture
def session(enforce_limit):
    engine = constrained_sqlite_engine()
    with engine.begin() as connection:
        # Only the key columns the budget tables reference.
        connection.exec_driver_sql("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE applications (id VARCHAR(100) PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO users (id) VALUES ('u0'), ('u1')")
        connection.exec_driver_sql(f"INSERT INTO applications (id) VALUES ('{PROJECT}')")
    SQLModel.metadata.create_all(engine, tables=BUDGET_TABLES)
    with (
        Session(engine) as db_session,
        patch.object(SettingsService, "get_enforce_member_spend_limits", return_value=enforce_limit),
    ):
        _add_budget_with_assignment(db_session, _main_budget())
        db_session.commit()
        yield db_session
    engine.dispose()


def _add_member(session: Session, actor_id: str | None = "actor-1") -> None:
    ProjectAssignmentService._sync_project_budget_member_added(session, PROJECT, "u1", actor_id)
    session.commit()


def _shared_row(session: Session, budget_id: str = "b1") -> Budget:
    return session.get(Budget, f"{budget_id}:shared")


def test_first_member_creates_shared_row_then_allocation(session):
    _add_member(session, actor_id="actor-1")

    expected = build_shared_project_budget_row(
        _main_budget(), project_name=PROJECT, actor_id="actor-1", soft_budget=80.0, max_budget=100.0
    )
    shared = _shared_row(session)
    assert {key: getattr(shared, key) for key in expected} == expected
    allocation = session.exec(
        select(ProjectMemberBudgetAssignment).where(ProjectMemberBudgetAssignment.user_id == "u1")
    ).one()
    assert allocation.shared_budget_id == allocation.effective_budget_id == "b1:shared"
    assert allocation.sync_status == SyncStatus.PENDING


def test_bootstrap_without_actor_uses_main_creator(session):
    _add_member(session, actor_id=None)

    assert _shared_row(session).created_by == "admin"


def test_existing_shared_row_left_untouched(session):
    row = build_shared_project_budget_row(
        _main_budget(), project_name=PROJECT, actor_id="admin", soft_budget=10.0, max_budget=20.0
    )
    session.add(Budget(**row))
    session.commit()

    _add_member(session)

    shared = _shared_row(session)
    assert (shared.soft_budget, shared.max_budget) == (10.0, 20.0)


@pytest.mark.parametrize(("enforce_limit", "expected_max"), [(True, 40.0), (False, 100.0)])
def test_shared_row_amounts_follow_member_limits(session, expected_max):
    session.add(
        ProjectMemberBudgetAssignment(
            project_name=PROJECT,
            budget_category="platform",
            project_budget_id="b1",
            user_id="u0",
            allocation_mode=AllocationMode.EQUAL.value,
            allocated_soft_budget=30.0,
            allocated_max_budget=40.0,
        )
    )
    session.commit()

    _add_member(session)

    shared = _shared_row(session)
    assert (shared.soft_budget, shared.max_budget) == (30.0, expected_max)


def test_two_categories_each_get_a_shared_row(session):
    _add_budget_with_assignment(session, _main_budget(budget_id="b2", name="p-cli", category="cli"))
    session.commit()

    _add_member(session)

    assert _shared_row(session, "b1") is not None
    assert _shared_row(session, "b2") is not None


def test_repeat_add_is_idempotent(session):
    _add_member(session)
    _add_member(session)

    shared_rows = session.exec(
        select(func.count()).select_from(Budget).where(Budget.budget_origin_type == "shared_default")
    ).one()
    allocations = session.exec(
        select(func.count())
        .select_from(ProjectMemberBudgetAssignment)
        .where(ProjectMemberBudgetAssignment.user_id == "u1")
    ).one()
    assert (shared_rows, allocations) == (1, 1)
