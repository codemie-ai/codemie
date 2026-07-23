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

"""Project assignment service - Business logic for project membership management.

Addresses Code Review MEDIUM #5: Layering violation fix.
Moves assignment logic from router to service layer following API->Service->Repository pattern.
"""

from typing import Optional

import asyncio
from datetime import UTC, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlmodel import Session, select

from codemie.clients.postgres import get_async_session
from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.constants import Environment
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.repository.project_spend_tracking_repository import project_spend_tracking_repository
from codemie.repository.user_project_repository import user_project_repository
from codemie.repository.user_repository import user_repository
from codemie.rest_api.models.user_management import UserProject
from codemie.rest_api.security.user import User
from codemie.service.activity.activity_models import (
    ActivityDomain,
    ActivityEntityType,
    ActivityEventCreate,
    UserManagementEvent,
)
from codemie.service.activity.activity_repository import activity_event_repository
from codemie.service.budget.budget_enums import AllocationMode, SyncStatus
from codemie.service.budget.budget_models import (
    Budget,
    ProjectBudgetAssignment,
    ProjectMemberBudgetAssignment,
    build_shared_project_budget_id,
)
from codemie.service.budget.budget_resolution_service import _resolution_cache
from codemie.service.budget.provider_registry import get_active_provider
from codemie.service.spend_tracking.spend_models import ProjectSpendTracking


_USER_NOT_FOUND = "User not found"
_VERIFY_USER_ID_HELP = "Verify the user ID and try again"
_PERSONAL_PROJECT_MEMBERSHIP = "Cannot modify membership of a personal project"


class ProjectAssignmentService:
    """Service for managing project membership assignments"""

    @staticmethod
    def _run_budget_provider_coro(coro):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop in this thread (sync route handler thread pool).
            # Prefer the main app loop so coroutines can use the asyncpg pool.
            from codemie.core.event_loop import get_main_event_loop

            main_loop = get_main_event_loop()
            if main_loop is not None and main_loop.is_running():
                future = asyncio.run_coroutine_threadsafe(coro, main_loop)
                try:
                    return future.result(timeout=30)
                except TimeoutError:
                    future.cancel()
                    logger.warning("Project budget provider sync timed out after 30s; skipping")
                    return None
            return asyncio.run(coro)
        # Running inside an async context (e.g. tests). Submit to the running loop.
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=30)
        except TimeoutError:
            future.cancel()
            logger.warning("Project budget provider sync timed out after 30s; skipping")
            return None

    @staticmethod
    def _build_member_provider_metadata(member_state) -> dict:
        raw = dict(member_state.metadata or {})
        if member_state.provider_member_ref is not None:
            raw["provider_member_ref"] = member_state.provider_member_ref
        return {
            "provider": member_state.provider,
            "last_synced_at": datetime.now(tz=timezone.utc).isoformat(),
            "sync_status": member_state.sync_status,
            "raw": raw,
        }

    @staticmethod
    async def _capture_terminal_member_spend(
        allocation: ProjectMemberBudgetAssignment,
        provider,
        now: datetime,
    ) -> None:
        """Capture a terminal spend snapshot before the LiteLLM customer is deleted."""
        ref = (allocation.provider_metadata or {}).get("raw", {}).get("provider_member_ref")
        if not ref:
            logger.debug(
                f"budget_event=project_member_spend_snapshot_skipped component=project_assignment_service "
                f"user_id={allocation.user_id!r} project_name={allocation.project_name!r} "
                f"reason=no_provider_member_ref"
            )
            return

        snapshots = await provider.collect_member_budget_spend_for_refs({ref})
        if not snapshots:
            logger.debug(
                f"budget_event=project_member_spend_snapshot_skipped component=project_assignment_service "
                f"user_id={allocation.user_id!r} project_name={allocation.project_name!r} "
                f"reason=no_snapshots_returned"
            )
            return

        snapshot = snapshots[0]
        current_spend = Decimal(str(snapshot.spend)).quantize(Decimal("0.0000000001"))

        async with get_async_session() as db_session:
            prev_rows = await project_spend_tracking_repository.get_latest_before_by_member_budget_ids(
                db_session,
                [(snapshot.project_name, snapshot.budget_id, snapshot.user_id)],
                now,
            )
            prev_row = prev_rows.get((snapshot.project_name, snapshot.budget_id, snapshot.user_id))

            if prev_row is None:
                daily_spend = current_spend
                cumulative_spend = current_spend
            else:
                prev_period_spend = Decimal(str(prev_row.budget_period_spend)).quantize(Decimal("0.0000000001"))
                delta = current_spend - prev_period_spend
                if delta <= Decimal("0"):
                    logger.warning(
                        f"budget_event=project_member_spend_snapshot_skipped component=project_assignment_service "
                        f"user_id={allocation.user_id!r} project_name={allocation.project_name!r} "
                        f"reason=zero_delta current_spend={current_spend} prev_period_spend={prev_period_spend}"
                    )
                    return
                daily_spend = delta
                cumulative_spend = Decimal(str(prev_row.cumulative_spend)).quantize(Decimal("0.0000000001")) + delta

            row = ProjectSpendTracking(
                id=uuid4(),
                project_name=snapshot.project_name,
                spend_date=now,
                daily_spend=daily_spend,
                cumulative_spend=cumulative_spend,
                budget_period_spend=current_spend,
                budget_id=snapshot.budget_id,
                budget_category=snapshot.budget_category.value,
                user_id=snapshot.user_id,
                provider_subject_id=snapshot.provider_subject_id or ref,
            )
            await project_spend_tracking_repository.insert_member_budget_entries(db_session, [row])

        logger.info(
            f"budget_event=project_member_spend_snapshot_captured component=project_assignment_service "
            f"user_id={allocation.user_id!r} project_name={allocation.project_name!r} "
            f"budget_id={snapshot.budget_id!r} daily_spend={daily_spend} cumulative_spend={cumulative_spend}"
        )

    @staticmethod
    def _get_member_added_allocation_amounts(
        session: Session,
        project_name: str,
        budget_category: str,
        project_budget_id: str,
        budget: "Budget",
    ) -> tuple[float, float]:
        """Return (allocated_soft_budget, allocated_max_budget) for a newly added member.

        Precedence:
        1. Copy from the first active equal-mode member allocation for the same
           project, category, and budget.
        2. Fall back to the project budget's own soft_budget / max_budget.
        """
        equal_alloc = session.exec(
            select(ProjectMemberBudgetAssignment).where(
                ProjectMemberBudgetAssignment.project_name == project_name,
                ProjectMemberBudgetAssignment.budget_category == budget_category,
                ProjectMemberBudgetAssignment.project_budget_id == project_budget_id,
                ProjectMemberBudgetAssignment.allocation_mode == AllocationMode.EQUAL.value,
                ProjectMemberBudgetAssignment.deleted_at.is_(None),
            )
        ).first()
        if equal_alloc is not None:
            return equal_alloc.allocated_soft_budget, equal_alloc.allocated_max_budget
        return budget.soft_budget, budget.max_budget

    @staticmethod
    def _sync_project_budget_member_added(
        session: Session, project_name: str, user_id: str, actor_id: Optional[str]
    ) -> None:
        """Create conservative allocations for a newly added member on active project budgets."""
        assignments = session.exec(
            select(ProjectBudgetAssignment).where(
                ProjectBudgetAssignment.project_name == project_name,
                ProjectBudgetAssignment.deleted_at.is_(None),
            )
        ).all()
        if not assignments:
            return

        for assignment in assignments:
            existing = session.exec(
                select(ProjectMemberBudgetAssignment).where(
                    ProjectMemberBudgetAssignment.project_name == project_name,
                    ProjectMemberBudgetAssignment.budget_category == assignment.budget_category,
                    ProjectMemberBudgetAssignment.user_id == user_id,
                    ProjectMemberBudgetAssignment.deleted_at.is_(None),
                )
            ).first()
            if existing is not None:
                continue

            budget = session.get(Budget, assignment.budget_id)
            if budget is None:
                logger.warning(
                    f"budget_event=project_member_budget_allocation_skipped component=project_assignment_service "
                    f"user_id={user_id!r} project_name={project_name!r} "
                    f"budget_id={assignment.budget_id!r} budget_category={assignment.budget_category!r} "
                    f"reason=budget_row_missing hint=pmba_will_not_be_created_for_this_category"
                )
                continue
            soft_budget, max_budget = ProjectAssignmentService._get_member_added_allocation_amounts(
                session, project_name, assignment.budget_category, assignment.budget_id, budget
            )
            allocation = ProjectMemberBudgetAssignment(
                project_name=project_name,
                budget_category=assignment.budget_category,
                project_budget_id=assignment.budget_id,
                user_id=user_id,
                allocation_mode=AllocationMode.EQUAL.value,
                allocated_soft_budget=soft_budget,
                allocated_max_budget=max_budget,
                shared_budget_id=build_shared_project_budget_id(assignment.budget_id),
                effective_budget_id=build_shared_project_budget_id(assignment.budget_id),
                budget_reset_at=budget.budget_reset_at,
                assigned_by=actor_id,
                sync_status=SyncStatus.PENDING,
            )
            session.add(allocation)
            logger.info(
                f"budget_event=project_member_budget_allocation_created component=project_assignment_service "
                f"user_id={user_id!r} project_name={project_name!r} budget_id={assignment.budget_id!r} "
                f"budget_category={assignment.budget_category!r} sync_status=pending_provider_sync"
            )

    @staticmethod
    def _sync_project_budget_member_removed(session: Session, project_name: str, user_id: str) -> None:
        """Soft-delete active allocations for a removed member and disable provider state."""
        allocations = session.exec(
            select(ProjectMemberBudgetAssignment).where(
                ProjectMemberBudgetAssignment.project_name == project_name,
                ProjectMemberBudgetAssignment.user_id == user_id,
                ProjectMemberBudgetAssignment.deleted_at.is_(None),
            )
        ).all()
        if not allocations:
            return

        provider = get_active_provider()
        now = datetime.now(tz=timezone.utc)
        changed = False
        for allocation in allocations:
            try:
                ProjectAssignmentService._run_budget_provider_coro(
                    ProjectAssignmentService._capture_terminal_member_spend(allocation, provider, now)
                )
            except Exception as exc:
                logger.warning(
                    f"budget_event=project_member_spend_snapshot_failed component=project_assignment_service "
                    f"user_id={user_id!r} allocation_id={allocation.id!r}: {exc}"
                )
            try:
                ProjectAssignmentService._run_budget_provider_coro(
                    provider.delete_member_allocation(allocation=allocation)
                )
            except Exception as exc:
                logger.warning(
                    f"Failed to delete provider member allocation {allocation.id!r} "
                    f"for removed member {user_id!r}: {exc}"
                )
            allocation.deleted_at = now
            session.add(allocation)
            _resolution_cache.pop((project_name, allocation.budget_category, user_id), None)
            changed = True
        if changed:
            session.flush()

    @staticmethod
    def _reject_if_personal_project(project: Application, actor: User, action: str) -> None:
        """Block membership changes on personal projects.

        Super admin or project owner → 403 (project existence is known).
        Otherwise → 404 (hide project existence from unrelated callers).
        """
        if project.project_type != Application.ProjectType.PERSONAL:
            return

        http_method = action.split()[0] if action else "UNKNOWN"
        logger.warning(f"personal_project_assignment_blocked: user_id={actor.id}, method={http_method}")

        if actor.is_admin_or_maintainer or project.created_by == actor.id:
            raise ExtendedHTTPException(code=403, message=_PERSONAL_PROJECT_MEMBERSHIP)

        raise ExtendedHTTPException(code=404, message="Project not found")

    @staticmethod
    def _reject_if_creator(session: Session, project: Application, user_ids: list[str], project_name: str) -> None:
        """Raise 400 if any of the given user_ids is the project creator."""
        if project.created_by in user_ids:
            creator = user_repository.get_by_id(session, project.created_by)
            creator_name = creator.username if creator else project.created_by
            raise ExtendedHTTPException(
                code=400,
                message="Cannot remove the project creator from the project",
                details=f"User '{creator_name}' is the creator of project '{project_name}' and cannot be unassigned",
                help="The project creator must always remain a member of the project",
            )

    @staticmethod
    def _validate_user_id_format(user_id: str) -> None:
        """Validate user_id is a valid UUID format"""
        from uuid import UUID

        if Environment.LOCAL.value == config.ENV:
            return

        try:
            UUID(user_id)
        except ValueError:
            raise ExtendedHTTPException(
                code=400,
                message="Invalid user_id format",
                details="user_id must be a valid UUID",
            )

    @staticmethod
    def assign_user_to_project(
        session: Session,
        project: Application,
        user_id: str,
        project_name: str,
        is_project_admin: bool,
        actor: User,
        action: str,
    ) -> dict:
        """Assign a user to a project.

        Args:
            session: Database session
            project: Authorized project from dependency
            user_id: Target user ID to assign
            project_name: Project name
            is_project_admin: Whether user should be project admin
            actor: User performing the request
            action: Action string for logging (e.g., "POST /v1/projects/...")

        Returns:
            dict with assignment details

        Raises:
            ExtendedHTTPException: On validation failures
        """
        # Reject personal project modification
        ProjectAssignmentService._reject_if_personal_project(project, actor, action)

        # Validate user_id format before DB lookup
        ProjectAssignmentService._validate_user_id_format(user_id)

        # Validate target user exists
        target_user = user_repository.get_by_id(session, user_id)
        if not target_user:
            raise ExtendedHTTPException(
                code=404,
                message=_USER_NOT_FOUND,
                details=f"No user found with ID '{user_id}'",
                help=_VERIFY_USER_ID_HELP,
            )

        # Check if already assigned
        existing = user_project_repository.get_by_user_and_project(session, user_id, project_name)
        if existing:
            raise ExtendedHTTPException(
                code=409,
                message="User already assigned to project",
                details=f"User '{user_id}' is already a member of project '{project_name}'",
                help="Use PUT endpoint to update user's role instead",
            )

        # Create assignment
        user_project_repository.add_project(
            session=session,
            user_id=user_id,
            project_name=project_name,
            is_project_admin=is_project_admin,
        )
        ProjectAssignmentService._sync_project_budget_member_added(session, project_name, user_id, actor.id)
        activity_event_repository.insert(
            ActivityEventCreate(
                domain=ActivityDomain.USER_MANAGEMENT,
                event_type=UserManagementEvent.USER_PROJECT_ASSIGNED,
                entity_type=ActivityEntityType.USER,
                entity_id=user_id,
                actor_id=actor.id,
                attributes={"project_name": project_name, "is_project_admin": is_project_admin},
            ),
            session,
        )
        logger.info(
            f"User assigned to project: user_id={user_id}, project={project_name}, "
            f"is_admin={is_project_admin}, by={actor.id}"
        )

        return {
            "message": "User assigned to project successfully",
            "user_id": user_id,
            "project_name": project_name,
            "is_project_admin": is_project_admin,
        }

    @staticmethod
    def update_user_project_role(
        session: Session,
        project: Application,
        user_id: str,
        project_name: str,
        is_project_admin: bool,
        actor: User,
        action: str,
    ) -> dict:
        """Update user's project admin status.

        Args:
            session: Database session
            project: Authorized project from dependency
            user_id: Target user ID
            project_name: Project name
            is_project_admin: New admin status
            actor: User performing the request
            action: Action string for logging

        Returns:
            dict with update details

        Raises:
            ExtendedHTTPException: On validation failures
        """
        # Reject personal project modification
        ProjectAssignmentService._reject_if_personal_project(project, actor, action)

        # Validate user_id format before DB lookup
        ProjectAssignmentService._validate_user_id_format(user_id)

        # Validate target user exists
        target_user = user_repository.get_by_id(session, user_id)
        if not target_user:
            raise ExtendedHTTPException(
                code=404,
                message=_USER_NOT_FOUND,
                details=f"No user found with ID '{user_id}'",
                help=_VERIFY_USER_ID_HELP,
            )

        # Check if user is assigned to project
        membership = user_project_repository.get_by_user_and_project(session, user_id, project_name)
        if not membership:
            raise ExtendedHTTPException(
                code=404,
                message="User is not assigned to this project",
                details=f"User '{user_id}' is not a member of project '{project_name}'",
                help="Use POST endpoint to assign the user first",
            )

        # Update role
        user_project_repository.update_admin_status(session, user_id, project_name, is_project_admin)
        activity_event_repository.insert(
            ActivityEventCreate(
                domain=ActivityDomain.USER_MANAGEMENT,
                event_type=UserManagementEvent.USER_PROJECT_ROLE_UPDATED,
                entity_type=ActivityEntityType.USER,
                entity_id=user_id,
                actor_id=actor.id,
                attributes={"project_name": project_name, "is_project_admin": is_project_admin},
            ),
            session,
        )
        logger.info(
            f"User role updated: user_id={user_id}, project={project_name}, "
            f"is_admin={is_project_admin}, by={actor.id}"
        )

        return {
            "message": "User role updated successfully",
            "user_id": user_id,
            "project_name": project_name,
            "is_project_admin": is_project_admin,
        }

    @staticmethod
    def bulk_assign_users_to_project(
        session: Session,
        project: Application,
        users: list[dict],
        project_name: str,
        actor: User,
        action: str,
    ) -> list[dict]:
        """Bulk assign/upsert users to a project (all-or-nothing).

        Phase 1 - Validation (no DB writes):
          - Reject personal project
          - Check for duplicate user_ids in request
          - Validate all user_id formats (UUID)
          - Bulk-check all users exist in DB
          - Bulk-fetch existing assignments

        Phase 2 - Execution (all DB writes, single flush):
          - For each user: assign (new) or update role (existing)

        Args:
            session: Database session
            project: Authorized project from dependency
            users: List of dicts with 'user_id' and 'is_project_admin' keys
            project_name: Project name
            actor: User performing the request
            action: Action string for logging

        Returns:
            List of per-user result dicts

        Raises:
            ExtendedHTTPException: On validation failures
        """
        # Reject personal project modification
        ProjectAssignmentService._reject_if_personal_project(project, actor, action)

        # Check for duplicate user_ids in request
        user_ids = [u["user_id"] for u in users]
        if len(user_ids) != len(set(user_ids)):
            from collections import Counter

            duplicates = sorted(uid for uid, count in Counter(user_ids).items() if count > 1)
            raise ExtendedHTTPException(
                code=400,
                message="Duplicate user IDs in request",
                details=f"Duplicate user_ids: {duplicates}",
                help="Each user_id must appear only once in the request",
            )

        # Validate all UUID formats
        for user_id in user_ids:
            ProjectAssignmentService._validate_user_id_format(user_id)

        # Bulk-check all users exist
        existing_user_ids = user_repository.get_existing_user_ids(session, user_ids)
        missing_ids = sorted(set(user_ids) - existing_user_ids)
        if missing_ids:
            raise ExtendedHTTPException(
                code=404,
                message="One or more users not found",
                details=f"Users not found: {missing_ids}",
                help="Verify all user IDs and try again",
            )

        # Bulk-fetch existing assignments
        existing_assignments = user_project_repository.get_by_users_and_project(session, user_ids, project_name)

        # Execute: assign new or update existing (using pre-fetched objects to avoid N+1)
        results = []
        assigned_count = 0
        updated_count = 0

        for user_entry in users:
            user_id = user_entry["user_id"]
            is_project_admin = user_entry["is_project_admin"]

            if user_id in existing_assignments:
                user_project = existing_assignments[user_id]
                user_project.is_project_admin = is_project_admin
                user_project.update_date = datetime.now(UTC)
                session.add(user_project)
                action_taken = "updated"
                updated_count += 1
                event_type = UserManagementEvent.USER_PROJECT_ROLE_UPDATED
            else:
                now = datetime.now(UTC)
                user_project = UserProject(
                    user_id=user_id,
                    project_name=project_name,
                    is_project_admin=is_project_admin,
                    date=now,
                    update_date=now,
                )
                session.add(user_project)
                action_taken = "assigned"
                assigned_count += 1
                ProjectAssignmentService._sync_project_budget_member_added(session, project_name, user_id, actor.id)
                event_type = UserManagementEvent.USER_PROJECT_ASSIGNED

            activity_event_repository.insert(
                ActivityEventCreate(
                    domain=ActivityDomain.USER_MANAGEMENT,
                    event_type=event_type,
                    entity_type=ActivityEntityType.USER,
                    entity_id=user_id,
                    actor_id=actor.id,
                    attributes={"project_name": project_name, "is_project_admin": is_project_admin},
                ),
                session,
            )
            results.append(
                {
                    "user_id": user_id,
                    "action": action_taken,
                    "is_project_admin": is_project_admin,
                }
            )

        session.flush()

        logger.info(
            f"Bulk assignment completed: project={project_name}, "
            f"assigned={assigned_count}, updated={updated_count}, "
            f"total={len(users)}, by={actor.id}"
        )

        return results

    @staticmethod
    def bulk_remove_users_from_project(
        session: Session,
        project: Application,
        user_ids: list[str],
        project_name: str,
        actor: User,
        action: str,
    ) -> list[dict]:
        """Bulk remove users from a project (all-or-nothing).

        Phase 1 - Validation:
          - Reject personal project
          - Check for duplicate user_ids
          - Validate all user_id formats (UUID)
          - Bulk-check all users exist
          - Verify all users are currently assigned to project

        Phase 2 - Execution:
          - Bulk-delete all assignments

        Args:
            session: Database session
            project: Authorized project from dependency
            user_ids: List of user UUIDs to remove
            project_name: Project name
            actor: User performing the request
            action: Action string for logging

        Returns:
            List of per-user result dicts

        Raises:
            ExtendedHTTPException: On validation failures
        """
        ProjectAssignmentService._reject_if_personal_project(project, actor, action)
        ProjectAssignmentService._reject_if_creator(session, project, user_ids, project_name)

        # Check for duplicate user_ids in request
        unique_ids = set(user_ids)
        if len(user_ids) != len(unique_ids):
            from collections import Counter

            duplicates = sorted(uid for uid, count in Counter(user_ids).items() if count > 1)
            raise ExtendedHTTPException(
                code=400,
                message="Duplicate user IDs in request",
                details=f"Duplicate user_ids: {duplicates}",
                help="Each user_id must appear only once in the request",
            )

        # Validate all UUID formats
        for user_id in user_ids:
            ProjectAssignmentService._validate_user_id_format(user_id)

        # Bulk-check all users exist
        existing_user_ids = user_repository.get_existing_user_ids(session, user_ids)
        missing_ids = sorted(set(user_ids) - existing_user_ids)
        if missing_ids:
            raise ExtendedHTTPException(
                code=404,
                message="One or more users not found",
                details=f"Users not found: {missing_ids}",
                help="Verify all user IDs and try again",
            )

        # Verify all users are assigned to project
        existing_assignments = user_project_repository.get_by_users_and_project(session, user_ids, project_name)
        not_assigned = sorted(set(user_ids) - set(existing_assignments.keys()))
        if not_assigned:
            raise ExtendedHTTPException(
                code=404,
                message="One or more users are not assigned to this project",
                details=f"Users not assigned: {not_assigned}",
                help="Verify all users are members of this project",
            )

        # Execute: bulk delete using pre-fetched records (avoids redundant query)
        for record in existing_assignments.values():
            session.delete(record)
            ProjectAssignmentService._sync_project_budget_member_removed(session, project_name, record.user_id)
            activity_event_repository.insert(
                ActivityEventCreate(
                    domain=ActivityDomain.USER_MANAGEMENT,
                    event_type=UserManagementEvent.USER_PROJECT_REMOVED,
                    entity_type=ActivityEntityType.USER,
                    entity_id=record.user_id,
                    actor_id=actor.id,
                    attributes={"project_name": project_name},
                ),
                session,
            )
        session.flush()

        results = [{"user_id": uid, "action": "removed"} for uid in user_ids]

        logger.info(f"Bulk removal completed: project={project_name}, removed={len(user_ids)}, by={actor.id}")

        return results

    @staticmethod
    def remove_user_from_project(
        session: Session,
        project: Application,
        user_id: str,
        project_name: str,
        actor: User,
        action: str,
    ) -> dict:
        """Remove a user from a project.

        Args:
            session: Database session
            project: Authorized project from dependency
            user_id: Target user ID to remove
            project_name: Project name
            actor: User performing the request
            action: Action string for logging

        Returns:
            dict with removal confirmation

        Raises:
            ExtendedHTTPException: On validation failures
        """
        ProjectAssignmentService._reject_if_personal_project(project, actor, action)
        ProjectAssignmentService._reject_if_creator(session, project, [user_id], project_name)
        ProjectAssignmentService._validate_user_id_format(user_id)

        # Validate target user exists
        target_user = user_repository.get_by_id(session, user_id)
        if not target_user:
            raise ExtendedHTTPException(
                code=404,
                message=_USER_NOT_FOUND,
                details=f"No user found with ID '{user_id}'",
                help=_VERIFY_USER_ID_HELP,
            )

        # Remove assignment
        removed = user_project_repository.remove_project(session, user_id, project_name)
        if not removed:
            raise ExtendedHTTPException(
                code=404,
                message="User is not assigned to this project",
                details=f"User '{user_id}' is not a member of project '{project_name}'",
                help="Verify the user is assigned to this project",
            )

        ProjectAssignmentService._sync_project_budget_member_removed(session, project_name, user_id)
        activity_event_repository.insert(
            ActivityEventCreate(
                domain=ActivityDomain.USER_MANAGEMENT,
                event_type=UserManagementEvent.USER_PROJECT_REMOVED,
                entity_type=ActivityEntityType.USER,
                entity_id=user_id,
                actor_id=actor.id,
                attributes={"project_name": project_name},
            ),
            session,
        )
        logger.info(f"User removed from project: user_id={user_id}, project={project_name}, by={actor.id}")

        return {
            "message": "User removed from project successfully",
            "user_id": user_id,
            "project_name": project_name,
        }


# Singleton instance
project_assignment_service = ProjectAssignmentService()
