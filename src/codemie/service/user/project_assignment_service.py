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

from datetime import UTC, datetime

from sqlmodel import Session

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.repository.user_project_repository import user_project_repository
from codemie.repository.user_repository import user_repository
from codemie.rest_api.models.user_management import UserProject
from codemie.service.user.project_visibility_service import project_visibility_service


_USER_NOT_FOUND = "User not found"
_VERIFY_USER_ID_HELP = "Verify the user ID and try again"


class ProjectAssignmentService:
    """Service for managing project membership assignments"""

    @staticmethod
    def _validate_user_id_format(user_id: str) -> None:
        """Validate user_id is a valid UUID format (FR-5.1: 400 for invalid format)."""
        from uuid import UUID

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
        requesting_user_id: str,
        action: str,
    ) -> dict:
        """Assign a user to a project.

        Args:
            session: Database session
            project: Authorized project from dependency
            user_id: Target user ID to assign
            project_name: Project name
            is_project_admin: Whether user should be project admin
            requesting_user_id: ID of user making the request
            action: Action string for logging (e.g., "POST /v1/projects/...")

        Returns:
            dict with assignment details

        Raises:
            ExtendedHTTPException: On validation failures
        """
        # Reject personal project modification
        if project.project_type == "personal":
            project_visibility_service.raise_project_not_found(
                user_id=requesting_user_id,
                project_name=project_name,
                action=action,
            )

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

        logger.info(
            f"User assigned to project: user_id={user_id}, project={project_name}, "
            f"is_admin={is_project_admin}, by={requesting_user_id}"
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
        requesting_user_id: str,
        action: str,
    ) -> dict:
        """Update user's project admin status.

        Args:
            session: Database session
            project: Authorized project from dependency
            user_id: Target user ID
            project_name: Project name
            is_project_admin: New admin status
            requesting_user_id: ID of user making the request
            action: Action string for logging

        Returns:
            dict with update details

        Raises:
            ExtendedHTTPException: On validation failures
        """
        # Reject personal project modification
        if project.project_type == "personal":
            project_visibility_service.raise_project_not_found(
                user_id=requesting_user_id,
                project_name=project_name,
                action=action,
            )

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

        logger.info(
            f"User role updated: user_id={user_id}, project={project_name}, "
            f"is_admin={is_project_admin}, by={requesting_user_id}"
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
        requesting_user_id: str,
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
            requesting_user_id: ID of user making the request
            action: Action string for logging

        Returns:
            List of per-user result dicts

        Raises:
            ExtendedHTTPException: On validation failures
        """
        # Reject personal project modification
        if project.project_type == "personal":
            project_visibility_service.raise_project_not_found(
                user_id=requesting_user_id,
                project_name=project_name,
                action=action,
            )

        # Check for duplicate user_ids in request
        user_ids = [u["user_id"] for u in users]
        if len(user_ids) != len(set(user_ids)):
            seen = set()
            duplicates = sorted({uid for uid in user_ids if uid in seen or seen.add(uid)})
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
            f"total={len(users)}, by={requesting_user_id}"
        )

        return results

    @staticmethod
    def bulk_remove_users_from_project(
        session: Session,
        project: Application,
        user_ids: list[str],
        project_name: str,
        requesting_user_id: str,
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
            requesting_user_id: ID of user making the request
            action: Action string for logging

        Returns:
            List of per-user result dicts

        Raises:
            ExtendedHTTPException: On validation failures
        """
        # Reject personal project modification
        if project.project_type == "personal":
            project_visibility_service.raise_project_not_found(
                user_id=requesting_user_id,
                project_name=project_name,
                action=action,
            )

        # Check for duplicate user_ids in request
        unique_ids = set(user_ids)
        if len(user_ids) != len(unique_ids):
            seen = set()
            duplicates = sorted({uid for uid in user_ids if uid in seen or seen.add(uid)})
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
        session.flush()

        results = [{"user_id": uid, "action": "removed"} for uid in user_ids]

        logger.info(f"Bulk removal completed: project={project_name}, removed={len(user_ids)}, by={requesting_user_id}")

        return results

    @staticmethod
    def remove_user_from_project(
        session: Session,
        project: Application,
        user_id: str,
        project_name: str,
        requesting_user_id: str,
        action: str,
    ) -> dict:
        """Remove a user from a project.

        Args:
            session: Database session
            project: Authorized project from dependency
            user_id: Target user ID to remove
            project_name: Project name
            requesting_user_id: ID of user making the request
            action: Action string for logging

        Returns:
            dict with removal confirmation

        Raises:
            ExtendedHTTPException: On validation failures
        """
        # Reject personal project modification
        if project.project_type == "personal":
            project_visibility_service.raise_project_not_found(
                user_id=requesting_user_id,
                project_name=project_name,
                action=action,
            )

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

        # Remove assignment
        removed = user_project_repository.remove_project(session, user_id, project_name)
        if not removed:
            raise ExtendedHTTPException(
                code=404,
                message="User is not assigned to this project",
                details=f"User '{user_id}' is not a member of project '{project_name}'",
                help="Verify the user is assigned to this project",
            )

        logger.info(f"User removed from project: user_id={user_id}, project={project_name}, by={requesting_user_id}")

        return {
            "message": "User removed from project successfully",
            "user_id": user_id,
            "project_name": project_name,
        }


# Singleton instance
project_assignment_service = ProjectAssignmentService()
