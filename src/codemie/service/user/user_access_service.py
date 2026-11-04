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

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError
from sqlmodel import Session

from codemie.configs.logger import logger
from codemie.rest_api.security.user import User, resolve_membership_default
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.repository.user_repository import user_repository
from codemie.service.user.active_user import require_active_user
from codemie.repository.user_project_repository import user_project_repository
from codemie.repository.user_kb_repository import user_kb_repository
from codemie.repository.application_repository import application_repository


_ERRORS = SimpleNamespace(
    USER_NOT_FOUND="User not found",
    PROJECT_NOT_FOUND="Project not found",
    NO_PROJECT_ACCESS="User does not have access to this project",
)

_ONE_DEFAULT_INDEX = "uix_user_projects_one_default"


class UserAccessService:
    """User access service for project and KB access management"""

    # ===========================================
    # Project Access Management
    # ===========================================

    @staticmethod
    def get_user_projects_list(user_id: str) -> dict[str, list]:
        """Get user's project access list

        Handles complete flow with session management.

        Args:
            user_id: User UUID

        Returns:
            Dict with "projects" list

        Raises:
            ExtendedHTTPException: If user not found
        """
        from codemie.clients.postgres import get_session

        with get_session() as session:
            target_user = user_repository.get_by_id(session, user_id)
            if not target_user:
                raise ExtendedHTTPException(code=404, message=_ERRORS.USER_NOT_FOUND)

            projects = user_project_repository.get_by_user_id(session, user_id)
            default_project = resolve_membership_default(projects, target_user.email)

            return {
                "projects": [
                    {
                        "project_name": p.project_name,
                        "is_project_admin": p.is_project_admin,
                        "is_default": p.project_name == default_project,
                        "date": p.date,
                    }
                    for p in projects
                ]
            }

    @staticmethod
    def grant_project_access(user_id: str, project_name: str, is_project_admin: bool, actor: User) -> dict[str, str]:
        """Grant project access to user through the Projects-management assignment flow."""
        from codemie.service.project.project_assignment_service import project_assignment_service

        UserAccessService._apply_membership_change(
            user_id,
            project_name,
            "POST",
            lambda session, project, action: project_assignment_service.assign_user_to_project(
                session, project, user_id, project_name, is_project_admin, actor, action
            ),
        )

        log_details = UserAccessService._build_project_access_log_details(actor.id, user_id)
        logger.info(f"project_access_granted: {log_details}, domain=user_management")

        return {"message": "Project access granted successfully"}

    @staticmethod
    def update_user_project_access(
        user_id: str, project_name: str, is_project_admin: bool, actor: User
    ) -> dict[str, str]:
        """Update user's project admin status through the Projects-management assignment flow."""
        from codemie.service.project.project_assignment_service import project_assignment_service

        UserAccessService._apply_membership_change(
            user_id,
            project_name,
            "PUT",
            lambda session, project, action: project_assignment_service.update_user_project_role(
                session, project, user_id, project_name, is_project_admin, actor, action
            ),
        )

        log_details = UserAccessService._build_project_access_log_details(actor.id, user_id)
        logger.info(f"project_access_updated: {log_details}, domain=user_management")

        return {"message": "Project access updated successfully"}

    @staticmethod
    def set_default_project(user_id: str, project_name: str, actor: User) -> dict[str, str]:
        """Mark project_name as user_id's default project, unsetting any prior default.

        Any membership qualifies, including the user's personal project (selecting it keeps
        unbound spend on the personal budget); no personal-project guard applies here because
        the default flag changes no membership.
        """
        UserAccessService._change_default_project(
            user_id, project_name, actor, user_project_repository.set_default, "default_project_set"
        )
        return {"message": "Default project set successfully"}

    @staticmethod
    def clear_default_project(user_id: str, project_name: str, actor: User) -> dict[str, str]:
        """Unset project_name as user_id's default project, if currently set."""
        UserAccessService._change_default_project(
            user_id, project_name, actor, user_project_repository.clear_default, "default_project_cleared"
        )
        return {"message": "Default project cleared successfully"}

    @staticmethod
    def _change_default_project(user_id: str, project_name: str, actor: User, mutate, event: str) -> None:
        from codemie.clients.postgres import get_session

        with get_session() as session:
            require_active_user(session, user_id, user_repository)

            try:
                if not mutate(session, user_id, project_name):
                    raise ExtendedHTTPException(code=404, message=_ERRORS.NO_PROJECT_ACCESS)
                session.commit()
            except IntegrityError as e:
                session.rollback()
                if _ONE_DEFAULT_INDEX not in str(e.orig):
                    raise
                raise ExtendedHTTPException(code=409, message="Default project changed concurrently — please retry")
            except (StaleDataError, ObjectDeletedError):
                session.rollback()
                raise ExtendedHTTPException(code=404, message=_ERRORS.NO_PROJECT_ACCESS)

        UserAccessService._invalidate_auth_cache(user_id)
        log_details = UserAccessService._build_project_access_log_details(actor.id, user_id)
        logger.info(f"{event}: {log_details}, project_name={project_name!r}, domain=user_management")

    @staticmethod
    def _apply_membership_change(
        user_id: str,
        project_name: str,
        http_method: str,
        operation: Callable[[Session, Application, str], object],
    ) -> None:
        """Run a ProjectAssignmentService membership operation for an existing project, then commit.

        Raises 404 when the project does not exist. Exceptions from the operation propagate
        before commit, so nothing is persisted and the auth cache is left untouched.
        """
        from codemie.clients.postgres import get_session

        with get_session() as session:
            project = application_repository.get_by_name(session, project_name)
            if project is None:
                raise ExtendedHTTPException(code=404, message=_ERRORS.PROJECT_NOT_FOUND)

            action = f"{http_method} /v1/admin/users/{user_id}/projects/{project_name}"
            operation(session, project, action)
            session.commit()

        UserAccessService._invalidate_auth_cache(user_id)

    @staticmethod
    def _invalidate_auth_cache(user_id: str) -> None:
        """Drop cached auth users so membership/default changes apply on the next request."""
        from codemie.service.user.authentication_service import invalidate_user_from_cache

        invalidate_user_from_cache(user_id)

    @staticmethod
    def revoke_project_access(user_id: str, project_name: str, actor: User) -> dict[str, str]:
        """Remove user's project access through the Projects-management assignment flow."""
        from codemie.service.project.project_assignment_service import project_assignment_service

        UserAccessService._apply_membership_change(
            user_id,
            project_name,
            "DELETE",
            lambda session, project, action: project_assignment_service.remove_user_from_project(
                session, project, user_id, project_name, actor, action
            ),
        )

        log_details = UserAccessService._build_project_access_log_details(actor.id, user_id)
        logger.info(f"project_access_removed: {log_details}, domain=user_management")

        return {"message": "Project access removed successfully"}

    # ===========================================
    # Knowledge Base Access Management
    # ===========================================

    @staticmethod
    def get_user_knowledge_bases_list(user_id: str) -> dict[str, list]:
        """Get user's knowledge base access list

        Handles complete flow with session management.

        Args:
            user_id: User UUID

        Returns:
            Dict with "knowledge_bases" list

        Raises:
            ExtendedHTTPException: If user not found
        """
        from codemie.clients.postgres import get_session

        with get_session() as session:
            # Verify user exists
            target_user = user_repository.get_by_id(session, user_id)
            if not target_user:
                raise ExtendedHTTPException(code=404, message=_ERRORS.USER_NOT_FOUND)

            kbs = user_kb_repository.get_by_user_id(session, user_id)

            return {"knowledge_bases": [{"kb_name": kb.kb_name, "date": kb.date} for kb in kbs]}

    @staticmethod
    def grant_kb_access(user_id: str, kb_name: str, actor_user_id: str) -> dict[str, str]:
        """Grant knowledge base access to user

        Handles complete flow with validation and session management.

        Args:
            user_id: Target user UUID
            kb_name: Knowledge base name
            actor_user_id: User performing the action

        Returns:
            Dict with "message"

        Raises:
            ExtendedHTTPException: Various error conditions
        """
        from codemie.clients.postgres import get_session

        with get_session() as session:
            # Verify user exists and is active
            require_active_user(session, user_id, user_repository)

            # Check if already has access
            existing = user_kb_repository.get_by_user_and_kb(session, user_id, kb_name)

            if existing:
                raise ExtendedHTTPException(code=409, message="User already has access to this knowledge base")

            # Grant access
            user_kb_repository.add_kb(session, user_id, kb_name)
            session.commit()

            logger.info(
                f"kb_access_granted: actor_user_id={actor_user_id},"
                f" target_user_id={user_id}, kb={kb_name}, domain=user_management"
            )

            return {"message": "Knowledge base access granted successfully"}

    @staticmethod
    def revoke_kb_access(user_id: str, kb_name: str, actor_user_id: str) -> dict[str, str]:
        """Remove user's knowledge base access

        Handles complete flow with validation and session management.

        Args:
            user_id: Target user UUID
            kb_name: Knowledge base name
            actor_user_id: User performing the action

        Returns:
            Dict with "message"

        Raises:
            ExtendedHTTPException: Various error conditions
        """
        from codemie.clients.postgres import get_session

        with get_session() as session:
            # Verify user exists
            target_user = user_repository.get_by_id(session, user_id)
            if not target_user:
                raise ExtendedHTTPException(code=404, message=_ERRORS.USER_NOT_FOUND)

            # Remove access
            removed = user_kb_repository.remove_kb(session, user_id, kb_name)

            if not removed:
                raise ExtendedHTTPException(code=404, message="User does not have access to this knowledge base")

            session.commit()

            logger.info(
                f"kb_access_removed: actor_user_id={actor_user_id},"
                f" target_user_id={user_id}, kb={kb_name}, domain=user_management"
            )

            return {"message": "Knowledge base access removed successfully"}

    # ===========================================
    # Private helpers
    # ===========================================

    @staticmethod
    def _build_project_access_log_details(actor_user_id: str, target_user_id: str) -> str:
        """Build consistent project access audit details (no PII)."""
        return ", ".join(
            [
                f"actor_user_id={actor_user_id}",
                f"target_user_id={target_user_id}",
            ]
        )


# Singleton instance
user_access_service = UserAccessService()
