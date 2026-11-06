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

"""Project operations of the provisioning API."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from sqlmodel import Session

from codemie.clients.postgres import get_session
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.repository.application_repository import application_repository
from codemie.repository.cost_center_repository import cost_center_repository
from codemie.repository.user_project_repository import user_project_repository
from codemie.repository.user_repository import user_repository
from codemie.rest_api.security.user import User
from codemie.rest_api.security.user_type_validator import is_service_account
from codemie.service.cost_center_service import CostCenterService
from codemie.service.project.project_assignment_service import ProjectAssignmentService
from codemie.service.project.project_service import ProjectService

PROJECT_DELETED_MESSAGE = "Project '{name}' has been deleted"
PROJECT_DELETED_HELP = "Restore the project or use a different CodeMie project name."
PERSONAL_PROJECT_BUDGET_FORBIDDEN_MESSAGE = "Budgets cannot be managed for personal projects"
PERSONAL_PROJECT_BUDGET_FORBIDDEN_HELP = "Use a shared project."


@dataclass(frozen=True)
class ResolvedProject:
    project: Application
    cost_center: str | None
    created_project: bool
    created_cost_center: bool


@dataclass(frozen=True)
class AdminAssignment:
    assigned: list[str]
    not_assigned: list[str]
    changed_user_ids: list[str]


class ProvisioningService:
    @classmethod
    async def list_member_projects(cls, emails: list[str]) -> list[Application]:
        """List shared projects of the active users with the given emails. Caller must authorize."""
        return await asyncio.to_thread(cls._list_member_projects_sync, emails)

    @staticmethod
    def _list_member_projects_sync(emails: list[str]) -> list[Application]:
        with get_session() as session:
            users = user_repository.get_by_emails(session, emails, active_only=True)
            user_ids = [user.id for user in users.values() if not is_service_account(user)]
            if not user_ids:
                return []
            return application_repository.list_shared_projects_for_users(session, user_ids)

    @classmethod
    def find_project(cls, session: Session, project_name: str) -> Application | None:
        """Find a shared project by name; a deleted one is 409 and a personal one 403. Caller must authorize."""
        project = application_repository.get_by_name_case_insensitive(session, project_name)
        if project is None:
            return None
        if project.deleted_at is not None:
            raise ExtendedHTTPException(
                code=409, message=PROJECT_DELETED_MESSAGE.format(name=project_name), help=PROJECT_DELETED_HELP
            )
        if project.project_type == Application.ProjectType.PERSONAL:
            raise ExtendedHTTPException(
                code=403,
                message=PERSONAL_PROJECT_BUDGET_FORBIDDEN_MESSAGE,
                help=PERSONAL_PROJECT_BUDGET_FORBIDDEN_HELP,
            )
        return project

    @classmethod
    def check_project_prerequisites(
        cls, session: Session, project: Application | None, project_name: str, cost_center: str | None
    ) -> None:
        """Check, without writing, what resolve_project needs. Caller must authorize.

        A new project needs a valid name. A named cost center is checked only when it would be created or linked,
        so a link to an active cost center makes the named one irrelevant; a link to a deleted one counts as none.
        """
        if project is None:
            ProjectService.validate_shared_project_name(project_name)
        if cost_center is not None and (
            project is None or cls._active_cost_center_name(session, project.cost_center_id) is None
        ):
            cls._check_cost_center(session, cost_center)

    @staticmethod
    def _check_cost_center(session: Session, name: str) -> None:
        CostCenterService.validate_name(name)
        CostCenterService.ensure_feature_enabled()
        existing = cost_center_repository.get_by_name_case_insensitive(session, name)
        if existing is not None:
            CostCenterService.ensure_not_deleted(existing)

    @classmethod
    def resolve_project(
        cls, session: Session, actor: User, project_name: str, cost_center: str | None
    ) -> ResolvedProject:
        """Create the project, or link its cost center while it has no active one, inside the caller's transaction.

        Reads the project again because the check phase ran earlier. A link to an active cost center never changes;
        a link to a deleted one counts as none. The reported cost center is the one stored. Does not commit; caller
        must authorize and call check_project_prerequisites first.
        """
        project = cls.find_project(session, project_name)
        if project is None:
            return cls._create_project(session, actor, project_name, cost_center)

        linked_name = cls._active_cost_center_name(session, project.cost_center_id)
        if linked_name is not None or cost_center is None:
            return ResolvedProject(
                project=project, cost_center=linked_name, created_project=False, created_cost_center=False
            )

        named, created_cost_center = CostCenterService.get_or_create(session, name=cost_center, created_by=actor.id)
        cost_center_id = ProjectService.link_cost_center_in_session(
            session, actor=actor, project=project, cost_center_id=named.id
        )
        return ResolvedProject(
            project=project,
            cost_center=cls._active_cost_center_name(session, cost_center_id),
            created_project=False,
            # A concurrent call that linked another cost center first wins; ours was not the one linked.
            created_cost_center=created_cost_center and cost_center_id == named.id,
        )

    @staticmethod
    def _create_project(session: Session, actor: User, project_name: str, cost_center: str | None) -> ResolvedProject:
        named, created_cost_center = None, False
        if cost_center is not None:
            named, created_cost_center = CostCenterService.get_or_create(session, name=cost_center, created_by=actor.id)
        project = ProjectService.create_shared_project_in_session(
            session, actor=actor, name=project_name, cost_center_id=named.id if named else None
        )
        return ResolvedProject(
            project=project,
            cost_center=named.name if named else None,
            created_project=True,
            created_cost_center=created_cost_center,
        )

    @staticmethod
    def _active_cost_center_name(session: Session, cost_center_id: UUID | None) -> str | None:
        if cost_center_id is None:
            return None
        cost_center = cost_center_repository.get_active_by_id(session, cost_center_id)
        return cost_center.name if cost_center else None

    @classmethod
    def assign_admins(
        cls, session: Session, actor: User, project: Application, emails: list[str], action: str
    ) -> AdminAssignment:
        """Make the active, non-service-account users with these emails admins of the project. Add-only.

        Already-admins count as assigned but are not in changed_user_ids; unknown emails and service accounts are
        not assigned. Does not commit; caller must authorize and invalidate the cached users in changed_user_ids
        after committing.
        """
        if not emails:
            return AdminAssignment(assigned=[], not_assigned=[], changed_user_ids=[])

        users_by_email = user_repository.get_by_emails(session, emails, active_only=True)
        assigned: list[str] = []
        not_assigned: list[str] = []
        resolved_ids: list[str] = []
        for email in emails:
            user = users_by_email.get(email.lower())
            if user is None or is_service_account(user):
                not_assigned.append(email)
            else:
                assigned.append(email)
                resolved_ids.append(user.id)

        memberships = user_project_repository.get_by_users_and_project(session, resolved_ids, project.name)
        # Two spellings of one email resolve to the same user, who is promoted once.
        changed_user_ids = [
            user_id
            for user_id in dict.fromkeys(resolved_ids)
            if user_id not in memberships or not memberships[user_id].is_project_admin
        ]
        if changed_user_ids:
            ProjectAssignmentService.bulk_assign_users_to_project(
                session,
                project,
                [{"user_id": user_id, "is_project_admin": True} for user_id in changed_user_ids],
                project.name,
                actor,
                action,
            )
        return AdminAssignment(assigned=assigned, not_assigned=not_assigned, changed_user_ids=changed_user_ids)


provisioning_service = ProvisioningService()
