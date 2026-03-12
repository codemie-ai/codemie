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

from datetime import UTC, datetime

from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.user_repository import user_repository
from codemie.repository.user_project_repository import user_project_repository
from codemie.repository.user_kb_repository import user_kb_repository
from codemie.repository.application_repository import application_repository


_USER_NOT_FOUND = "User not found"
_PROJECT_NOT_FOUND = "Project not found"


class UserAccessService:
    """User access service for project and KB access management"""

    @staticmethod
    def _build_project_access_log_details(actor_user_id: str, target_user_id: str) -> str:
        """Build consistent project access audit details (no PII)."""
        return ", ".join(
            [
                f"actor_user_id={actor_user_id}",
                f"target_user_id={target_user_id}",
            ]
        )

    @staticmethod
    def _log_project_authorization_failure(
        user_id: str,
        target_user_id: str,
        action: str,
    ) -> None:
        """Log project authorization failures with audit context (no PII)."""
        timestamp = datetime.now(UTC).isoformat()
        http_method = action.split()[0] if action else "UNKNOWN"
        log_details = ", ".join(
            [
                f"user_id={user_id}",
                f"target_user_id={target_user_id}",
                f"method={http_method}",
                f"timestamp={timestamp}",
            ]
        )
        logger.warning(f"project_authorization_failed: {log_details}")

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
            # Verify user exists
            target_user = user_repository.get_by_id(session, user_id)
            if not target_user:
                raise ExtendedHTTPException(code=404, message=_USER_NOT_FOUND)

            projects = user_project_repository.get_by_user_id(session, user_id)

            return {
                "projects": [
                    {"project_name": p.project_name, "is_project_admin": p.is_project_admin, "date": p.date}
                    for p in projects
                ]
            }

    @staticmethod
    def grant_project_access(
        user_id: str, project_name: str, is_project_admin: bool, actor_user_id: str
    ) -> dict[str, str]:
        """Grant project access to user

        Handles complete flow with validation and session management.

        Story 10: Blocks personal project assignment with 404 (not 403) to prevent
        information leakage about project type.

        Args:
            user_id: Target user UUID
            project_name: Project name
            is_project_admin: Grant admin status
            actor_user_id: User performing the action

        Returns:
            Dict with "message"

        Raises:
            ExtendedHTTPException: Various error conditions (404 for personal projects)
        """
        from codemie.clients.postgres import get_session

        with get_session() as session:
            # Verify user exists
            target_user = user_repository.get_by_id(session, user_id)
            if not target_user:
                raise ExtendedHTTPException(code=404, message=_USER_NOT_FOUND)

            # Story 10: Block personal project assignment (404 strategy)
            # Check BEFORE checking existing access to prevent info leakage
            if application_repository.is_personal_project(session, project_name):
                UserAccessService._log_project_authorization_failure(
                    user_id=actor_user_id,
                    target_user_id=user_id,
                    action="grant_project_access",
                )
                # Return 404 (not 403) to hide project existence
                raise ExtendedHTTPException(code=404, message=_PROJECT_NOT_FOUND)

            # Check if already has access
            existing = user_project_repository.get_by_user_and_project(session, user_id, project_name)

            if existing:
                raise ExtendedHTTPException(code=409, message="User already has access to this project")

            # Ensure project exists in applications table
            application_repository.get_or_create(session, project_name)

            # Grant access
            user_project_repository.add_project(session, user_id, project_name, is_project_admin)
            session.commit()

            log_details = UserAccessService._build_project_access_log_details(actor_user_id, user_id)
            logger.info(f"project_access_granted: {log_details}")

            return {"message": "Project access granted successfully"}

    @staticmethod
    def update_user_project_access(
        user_id: str, project_name: str, is_project_admin: bool, actor_user_id: str
    ) -> dict[str, str]:
        """Update user's project admin status

        Handles complete flow with validation and session management.

        Story 10: Blocks personal project updates with 404 (not 403) to prevent
        information leakage about project type.

        Args:
            user_id: Target user UUID
            project_name: Project name
            is_project_admin: New admin status
            actor_user_id: User performing the action

        Returns:
            Dict with "message"

        Raises:
            ExtendedHTTPException: Various error conditions (404 for personal projects)
        """
        from codemie.clients.postgres import get_session

        with get_session() as session:
            # Verify user exists
            target_user = user_repository.get_by_id(session, user_id)
            if not target_user:
                raise ExtendedHTTPException(code=404, message=_USER_NOT_FOUND)

            # Story 10: Block personal project updates (404 strategy)
            if application_repository.is_personal_project(session, project_name):
                UserAccessService._log_project_authorization_failure(
                    user_id=actor_user_id,
                    target_user_id=user_id,
                    action="update_user_project_access",
                )
                # Return 404 (not 403) to hide project existence
                raise ExtendedHTTPException(code=404, message=_PROJECT_NOT_FOUND)

            # Get existing access
            project = user_project_repository.get_by_user_and_project(session, user_id, project_name)

            if not project:
                raise ExtendedHTTPException(code=404, message="User does not have access to this project")

            # Update admin status
            user_project_repository.update_admin_status(session, user_id, project_name, is_project_admin)
            session.commit()

            log_details = UserAccessService._build_project_access_log_details(actor_user_id, user_id)
            logger.info(f"project_access_updated: {log_details}")

            return {"message": "Project access updated successfully"}

    @staticmethod
    def revoke_project_access(user_id: str, project_name: str, actor_user_id: str) -> dict[str, str]:
        """Remove user's project access

        Handles complete flow with validation and session management.

        Story 10: Blocks personal project revocation with 404 (not 403) to prevent
        information leakage about project type.

        Args:
            user_id: Target user UUID
            project_name: Project name
            actor_user_id: User performing the action

        Returns:
            Dict with "message"

        Raises:
            ExtendedHTTPException: Various error conditions (404 for personal projects)
        """
        from codemie.clients.postgres import get_session

        with get_session() as session:
            # Verify user exists
            target_user = user_repository.get_by_id(session, user_id)
            if not target_user:
                raise ExtendedHTTPException(code=404, message=_USER_NOT_FOUND)

            # Story 10: Block personal project revocation (404 strategy)
            if application_repository.is_personal_project(session, project_name):
                UserAccessService._log_project_authorization_failure(
                    user_id=actor_user_id,
                    target_user_id=user_id,
                    action="revoke_project_access",
                )
                # Return 404 (not 403) to hide project existence
                raise ExtendedHTTPException(code=404, message=_PROJECT_NOT_FOUND)

            # Remove access
            removed = user_project_repository.remove_project(session, user_id, project_name)

            if not removed:
                raise ExtendedHTTPException(code=404, message="User does not have access to this project")

            session.commit()

            log_details = UserAccessService._build_project_access_log_details(actor_user_id, user_id)
            logger.info(f"project_access_removed: {log_details}")

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
                raise ExtendedHTTPException(code=404, message=_USER_NOT_FOUND)

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
            # Verify user exists
            target_user = user_repository.get_by_id(session, user_id)
            if not target_user:
                raise ExtendedHTTPException(code=404, message=_USER_NOT_FOUND)

            # Check if already has access
            existing = user_kb_repository.get_by_user_and_kb(session, user_id, kb_name)

            if existing:
                raise ExtendedHTTPException(code=409, message="User already has access to this knowledge base")

            # Grant access
            user_kb_repository.add_kb(session, user_id, kb_name)
            session.commit()

            logger.info(f"kb_access_granted: actor_user_id={actor_user_id}, target_user_id={user_id}, kb={kb_name}")

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
                raise ExtendedHTTPException(code=404, message=_USER_NOT_FOUND)

            # Remove access
            removed = user_kb_repository.remove_kb(session, user_id, kb_name)

            if not removed:
                raise ExtendedHTTPException(code=404, message="User does not have access to this knowledge base")

            session.commit()

            logger.info(f"kb_access_removed: actor_user_id={actor_user_id}, target_user_id={user_id}, kb={kb_name}")

            return {"message": "Knowledge base access removed successfully"}


# Singleton instance
user_access_service = UserAccessService()
