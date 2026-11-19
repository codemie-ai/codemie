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

"""IdP-sync service: idempotent user and membership operations for the customer sync job (EPMCDME-16895).

Thin orchestration over existing repositories, ``ProjectAssignmentService`` and
``UserManagementService`` post-commit hooks. Every method works in the caller's session and never commits
or rolls back; the router commits once per call and rolls back on error.

Concurrency: the database runs at PostgreSQL's default isolation level, READ COMMITTED (no
``isolation_level`` is configured on the engine in ``codemie.clients.postgres``). Retried or overlapping
upserts rely on a savepoint around the insert: a unique violation (id or email) ends the savepoint and the
call continues as an update. Demoting or deactivating an admin takes ``SELECT ... FOR UPDATE`` on the active admin rows,
so a parallel demotion blocks, then re-evaluates the predicate and sees one admin fewer.

``get_or_create`` of a project may roll back the session on a creation race, so it is the first write of
``assign``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus

from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from sqlmodel import Session, select

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.repository.application_repository import application_repository
from codemie.repository.user_project_repository import user_project_repository
from codemie.repository.user_repository import MembershipRow, user_repository
from codemie.rest_api.models.idp_sync import (
    IdpAssignRequest,
    IdpMembership,
    IdpUserPage,
    IdpUserResponse,
    IdpUserUpsertRequest,
)
from codemie.rest_api.models.user_management import UserDB
from codemie.rest_api.security.user import User
from codemie.service.project.personal_project_service import personal_project_service
from codemie.service.activity.activity_models import (
    ActivityDomain,
    ActivityEntityType,
    ActivityEventCreate,
    UserManagementEvent,
)
from codemie.service.activity.activity_repository import activity_event_repository
from codemie.service.project.project_assignment_service import ProjectAssignmentService
from codemie.service.user.user_management_service import UserManagementService

_USER_NOT_FOUND = "User not found"
# Fields of a change that the audit event ``user.updated`` carries.
_AUDITED_FIELDS = frozenset(
    {"email", "username", "name", "is_admin", "is_maintainer", "is_auditor", "picture", "user_type"}
)
_PERSONAL_PROJECT = "Cannot modify membership of a personal project"
_DEFAULT_USER_TYPE = "regular"
_LAST_MAINTAINER_MESSAGES = {
    "demote": "Cannot remove the last maintainer",
    "deactivate": "Cannot deactivate the last maintainer",
}


@dataclass(frozen=True)
class _RoleTargets:
    """The role flags a user ends up with after one upsert."""

    admin: bool
    maintainer: bool
    auditor: bool


def _not_found(user_id: str) -> ExtendedHTTPException:
    return ExtendedHTTPException(
        code=404,
        message=_USER_NOT_FOUND,
        details=f"No user found with ID '{user_id}'",
        help="Verify the user ID and try again",
    )


def _conflict(message: str, details: str) -> ExtendedHTTPException:
    return ExtendedHTTPException(code=409, message=message, details=details)


def _personal_project(project_name: str) -> ExtendedHTTPException:
    """409, not 403: the caller is allowed, this one change is refused for good (as for a project creator)."""
    return _conflict(_PERSONAL_PROJECT, f"Project '{project_name}' is a personal project.")


class IdpSyncService:
    """Operations behind ``/v1/idp-sync/``."""

    # ---------------------------------------------------------------- reads

    @staticmethod
    def _to_response(user: UserDB, memberships: list[MembershipRow]) -> IdpUserResponse:
        return IdpUserResponse(
            id=user.id,
            is_active=bool(user.is_active and user.deleted_at is None),
            email=user.email,
            username=user.username,
            name=user.name,
            is_admin=user.is_admin,
            picture=user.picture or None,
            user_type=user.user_type,
            is_maintainer=bool(user.is_maintainer),
            is_auditor=bool(user.is_auditor),
            memberships=[
                IdpMembership(
                    project_name=m.project_name, is_project_admin=m.is_project_admin, is_personal=m.is_personal
                )
                for m in memberships
            ],
        )

    @staticmethod
    def _response_for(session: Session, user: UserDB) -> IdpUserResponse:
        memberships = user_repository.fetch_memberships_with_personal(session, [user.id])
        return IdpSyncService._to_response(user, memberships.get(user.id, []))

    @staticmethod
    def get_user(session: Session, user_id: str) -> IdpUserResponse:
        user = user_repository.get_by_id(session, user_id)
        if user is None:
            raise _not_found(user_id)
        return IdpSyncService._response_for(session, user)

    @staticmethod
    def list_users(session: Session, page: int, per_page: int) -> IdpUserPage:
        users, total = user_repository.list_page_including_deleted(session, page, per_page)
        memberships = user_repository.fetch_memberships_with_personal(session, [u.id for u in users])
        return IdpUserPage(
            items=[IdpSyncService._to_response(u, memberships.get(u.id, [])) for u in users],
            total=total,
            page=page,
            per_page=per_page,
        )

    # --------------------------------------------------------------- events

    @staticmethod
    def _emit(session: Session, actor: User, event_type: str, user_id: str, attributes: dict | None = None) -> None:
        activity_event_repository.insert(
            ActivityEventCreate(
                domain=ActivityDomain.USER_MANAGEMENT,
                event_type=event_type,
                entity_type=ActivityEntityType.USER,
                entity_id=user_id,
                actor_id=actor.id,
                attributes=attributes or {},
            ),
            session,
        )

    # --------------------------------------------------------------- upsert

    @staticmethod
    def _create(session: Session, req: IdpUserUpsertRequest, is_admin: bool) -> UserDB | None:
        """Insert inside a savepoint; None when a concurrent insert won (id or email unique violation)."""
        try:
            with session.begin_nested():
                return user_repository.create(
                    session,
                    UserDB(
                        id=req.id,
                        email=req.email,
                        username=req.username,
                        name=req.name,
                        picture=req.picture or None,
                        user_type=req.user_type or _DEFAULT_USER_TYPE,
                        auth_source=config.IDP_PROVIDER,
                        email_verified=True,
                        is_active=True,
                        is_admin=is_admin,
                        is_maintainer=bool(req.is_maintainer),
                        is_auditor=bool(req.is_auditor),
                        project_limit=None if is_admin else config.USER_PROJECT_LIMIT,
                    ),
                )
        except IntegrityError:
            logger.info(f"idp_sync_upsert_conflict: user_id={req.id}, continuing as update")
            return None

    @staticmethod
    def _ensure_not_last_admin(session: Session, user_id: str, action: str = "demote") -> None:
        """409 unless another active administrator remains. Locks the administrators."""
        if user_repository.lock_and_count_active_admins(session) <= 1:
            logger.warning(f"idp_sync_blocked_last_admin_{action}: target_user_id={user_id}")
            raise ExtendedHTTPException(
                code=409,
                message=f"Cannot {action} the last administrator",
                details="The system must keep at least one active administrator.",
            )

    @staticmethod
    def _ensure_not_last_maintainer(session: Session, user_id: str, action: str = "demote") -> None:
        """409 unless another active maintainer remains. Locks the maintainers (after the administrators)."""
        holders = user_repository.lock_active_maintainer_ids(session)
        if not any(holder != user_id for holder in holders):
            logger.warning(f"idp_sync_blocked_last_maintainer_{action}: target_user_id={user_id}")
            raise ExtendedHTTPException(
                code=409,
                message=f"Cannot {action} the last maintainer"
                if action != "demote"
                else "Cannot remove the last maintainer",
                details="The system must keep at least one active maintainer.",
            )

    @staticmethod
    def _add_missing_budget_shares(session: Session, user_id: str, actor_id: str) -> None:
        """A reactivated user keeps memberships and budget allocations; only the missing shares are added.

        Shares are split among active members, so a project budget created while the user was deactivated has none
        for them. This is the same step as the administrator reactivation: existing allocations are left as they are.
        """
        for membership in user_project_repository.get_by_user_id(session, user_id):
            ProjectAssignmentService._sync_project_budget_member_added(
                session, membership.project_name, user_id, actor_id
            )

    @staticmethod
    def _collect_role_changes(session: Session, user: UserDB, targets: _RoleTargets, reactivated: bool) -> dict:
        """Admin, maintainer and auditor flags. The administrators are locked before the maintainers."""
        changes: dict = {}
        if user.is_admin != targets.admin:
            if user.is_admin and not reactivated:
                IdpSyncService._ensure_not_last_admin(session, user.id)
            changes["is_admin"] = targets.admin
            changes["project_limit"] = None if targets.admin else config.USER_PROJECT_LIMIT
        if bool(user.is_maintainer) != targets.maintainer:
            if user.is_maintainer and not reactivated:
                IdpSyncService._ensure_not_last_maintainer(session, user.id)
            changes["is_maintainer"] = targets.maintainer
        if bool(user.is_auditor) != targets.auditor:
            changes["is_auditor"] = targets.auditor
        return changes

    @staticmethod
    def _collect_changes(
        session: Session, user: UserDB, req: IdpUserUpsertRequest, targets: _RoleTargets, reactivated: bool
    ) -> dict:
        changes: dict = {}
        for field, value in (("email", req.email), ("username", req.username), ("name", req.name)):
            if getattr(user, field) != value:
                changes[field] = value
        if req.picture and req.picture != (user.picture or ""):
            changes["picture"] = req.picture
        if req.user_type is not None and req.user_type != user.user_type:
            changes["user_type"] = req.user_type
        changes.update(IdpSyncService._collect_role_changes(session, user, targets, reactivated))
        if reactivated:
            changes.update(is_active=True, deleted_at=None)
        return changes

    @staticmethod
    def _write_changes(session: Session, user: UserDB, changes: dict) -> None:
        try:
            with session.begin_nested():
                for field, value in changes.items():
                    setattr(user, field, value)
                user.update_date = datetime.now(UTC)
                session.add(user)
                session.flush()
        except IntegrityError as exc:
            raise _conflict(
                "Email or username already in use", "Another user already has this email or username."
            ) from exc

    @staticmethod
    def _apply_update(
        session: Session, actor: User, user: UserDB, req: IdpUserUpsertRequest, targets: _RoleTargets
    ) -> None:
        reactivated = not user.is_active or user.deleted_at is not None
        changes = IdpSyncService._collect_changes(session, user, req, targets, reactivated)
        if not changes:
            return
        IdpSyncService._write_changes(session, user, changes)
        if reactivated:
            IdpSyncService._add_missing_budget_shares(session, user.id, actor.id)
        attributes = {k: v for k, v in changes.items() if k in _AUDITED_FIELDS}
        if attributes:
            IdpSyncService._emit(session, actor, UserManagementEvent.USER_UPDATED, user.id, attributes)
        if reactivated:
            # the same event as the administrator reactivation, so the audit sees both paths alike
            IdpSyncService._emit(session, actor, UserManagementEvent.USER_REACTIVATED, user.id)

    @staticmethod
    def _targets(user: UserDB, req: IdpUserUpsertRequest) -> _RoleTargets:
        """The role flags the user ends up with.

        The IdP decides each flag it sends; an omitted one keeps the stored value. The ``ADMIN_USER_ID`` user is
        always an administrator. A maintainer is an administrator, so a user who ends up without the administrator
        flag ends up without the maintainer flag too; removing the last active maintainer is refused later (409).
        """
        configured = bool(config.ADMIN_USER_ID) and req.id == config.ADMIN_USER_ID
        admin = req.is_admin or configured
        maintainer = bool(user.is_maintainer) if req.is_maintainer is None else req.is_maintainer
        return _RoleTargets(
            admin=admin,
            maintainer=maintainer and admin,
            auditor=bool(user.is_auditor) if req.is_auditor is None else req.is_auditor,
        )

    @staticmethod
    def upsert_user(session: Session, actor: User, req: IdpUserUpsertRequest) -> tuple[IdpUserResponse, bool]:
        """Create or update; returns the user and whether this call created it."""
        target_admin = req.is_admin or (bool(config.ADMIN_USER_ID) and req.id == config.ADMIN_USER_ID)
        user = user_repository.get_by_id(session, req.id)
        if user is None:
            user = IdpSyncService._create(session, req, target_admin)
            if user is not None:
                attributes = {"is_admin": target_admin}
                attributes.update(
                    {k: v for k, v in (("is_maintainer", req.is_maintainer), ("is_auditor", req.is_auditor)) if v}
                )
                IdpSyncService._emit(session, actor, UserManagementEvent.USER_CREATED, user.id, attributes)
                return IdpSyncService._response_for(session, user), True
            user = user_repository.get_by_id(session, req.id)
            if user is None:
                raise _conflict("Email or username already in use", "Another user already has this email or username.")
        IdpSyncService._apply_update(session, actor, user, req, IdpSyncService._targets(user, req))
        return IdpSyncService._response_for(session, user_repository.get_by_id(session, req.id)), False

    # --------------------------------------------------------------- assign

    @staticmethod
    def _user_or_error(session: Session, user_id: str) -> UserDB:
        user = user_repository.get_by_id(session, user_id)
        if user is None:
            raise _not_found(user_id)
        return user

    @staticmethod
    def _is_own_personal_name(user: UserDB, project_name: str) -> bool:
        """The personal project of a user is named by the user email."""
        return bool(user.email) and project_name.casefold() == user.email.casefold()

    @staticmethod
    def _writable_project(session: Session, project_name: str) -> Application | None:
        project = application_repository.get_by_name_case_insensitive(session, project_name)
        if project is not None and project.project_type == Application.ProjectType.PERSONAL:
            raise _personal_project(project.name)
        return project

    @staticmethod
    def _ensure_personal_membership(session: Session, user: UserDB, project_name: str) -> None:
        """Create the personal project of the user when missing and add the user to it. Idempotent."""
        project = application_repository.get_by_name_case_insensitive(session, project_name)
        if project is None:
            project = application_repository.get_or_create(session, user.email)
            personal_project_service.mark_personal(project, user.id, user.email)
            session.add(project)
            session.flush()
        elif project.project_type != Application.ProjectType.PERSONAL or project.created_by != user.id:
            IdpSyncService._convert_to_personal(session, project, user)
        if user_project_repository.get_by_user_and_project(session, user.id, project.name) is None:
            user_project_repository.add_project(session, user.id, project.name, is_project_admin=False)

    @staticmethod
    def _convert_to_personal(session: Session, project: Application, user: UserDB) -> None:
        """The rules of the login flow: only a project nobody else uses and no other user's personal project."""
        others = [m for m in user_project_repository.get_by_project_name(session, project.name) if m.user_id != user.id]
        if project.project_type == Application.ProjectType.PERSONAL and project.created_by != user.id:
            raise _personal_project(project.name)
        if others or not personal_project_service.is_reusable(project, user.id, user.email):
            raise _personal_project(project.name)
        if project.name != user.email:  # a case variant: the user must not end up with two personal projects
            own = session.exec(
                select(Application.id).where(
                    func.lower(Application.name) == user.email.lower(),
                    Application.project_type == Application.ProjectType.PERSONAL,
                    Application.created_by == user.id,
                    Application.deleted_at.is_(None),
                )
            ).first()
            if own is not None:
                raise _personal_project(project.name)
        personal_project_service.mark_personal(project, user.id, user.email)
        session.add(project)
        session.flush()

    @staticmethod
    def _remove_personal_membership(session: Session, user: UserDB, project_name: str) -> None:
        """Remove the user from the personal project. The project itself stays. Idempotent."""
        project = application_repository.get_by_name_case_insensitive(session, project_name)
        if project is None:
            return
        if project.project_type != Application.ProjectType.PERSONAL or project.created_by != user.id:
            raise _personal_project(project.name)
        user_project_repository.remove_project(session, user.id, project.name)

    @staticmethod
    def assign(
        session: Session, actor: User, user_id: str, req: IdpAssignRequest
    ) -> tuple[IdpUserResponse, str | None]:
        """Returns the user and the project name when the user was added to it (the member set changed)."""
        user = IdpSyncService._user_or_error(session, user_id)
        if not user.is_active or user.deleted_at is not None:
            raise _conflict("User is deactivated", "Upsert the user to reactivate it before assigning projects.")
        if IdpSyncService._is_own_personal_name(user, req.project_name):
            IdpSyncService._ensure_personal_membership(session, user, req.project_name)
            return IdpSyncService._response_for(session, user), None
        IdpSyncService._writable_project(session, req.project_name)
        project = application_repository.get_or_create(session, req.project_name)
        if project.project_type == Application.ProjectType.PERSONAL:
            raise _personal_project(project.name)
        added = IdpSyncService._ensure_membership(session, actor, project, user_id, req)
        return IdpSyncService._response_for(session, user), (project.name if added else None)

    @staticmethod
    def _ensure_membership(
        session: Session, actor: User, project: Application, user_id: str, req: IdpAssignRequest
    ) -> bool:
        """True when the user was added; a change of the admin flag alone leaves the member set as it was."""
        name = project.name
        existing = user_project_repository.get_by_user_and_project(session, user_id, name)
        if existing is None:
            try:
                with session.begin_nested():
                    ProjectAssignmentService.assign_user_to_project(
                        session, project, user_id, name, req.is_project_admin, actor, "PUT /v1/idp-sync"
                    )
                return True
            except ExtendedHTTPException as exc:
                if exc.code != HTTPStatus.CONFLICT:
                    raise
            except IntegrityError:
                logger.info(f"idp_sync_assign_conflict: user_id={user_id}, project={name}, treated as done")
            existing = user_project_repository.get_by_user_and_project(session, user_id, name)
        if existing is not None and existing.is_project_admin != req.is_project_admin:
            ProjectAssignmentService.update_user_project_role(
                session, project, user_id, name, req.is_project_admin, actor, "PUT /v1/idp-sync"
            )
        return False

    # ------------------------------------------------------------- unassign

    @staticmethod
    def unassign(session: Session, actor: User, user_id: str, project_name: str) -> str | None:
        """The project name when the user was removed from it, else None."""
        user = IdpSyncService._user_or_error(session, user_id)
        if IdpSyncService._is_own_personal_name(user, project_name):
            IdpSyncService._remove_personal_membership(session, user, project_name)
            return None
        project = IdpSyncService._writable_project(session, project_name)
        if project is None:
            return None
        if project.created_by == user_id:
            raise _conflict(
                "Cannot remove the project creator from the project",
                f"User '{user_id}' created project '{project.name}' and must remain a member.",
            )
        if user_project_repository.get_by_user_and_project(session, user_id, project.name) is None:
            return None
        try:
            ProjectAssignmentService.remove_user_from_project(
                session, project, user_id, project.name, actor, "DELETE /v1/idp-sync"
            )
        except ExtendedHTTPException as exc:
            if exc.code != HTTPStatus.NOT_FOUND or exc.message == _USER_NOT_FOUND:
                raise
            return None
        return project.name

    # ----------------------------------------------------------- deactivate

    @staticmethod
    def deactivate(session: Session, actor: User, user_id: str) -> bool:
        """Soft-delete in the caller's transaction; True when the user was active.

        The sync account acts for the IdP, so the rank rule of the administrator UI (only a maintainer
        deactivates a maintainer) does not apply. The last active administrator and the last active maintainer
        are refused with 409, as for a demotion; the administrators are locked before the maintainers.
        The caller commits and then runs :meth:`after_deactivation_commit`.
        """
        if user_id == actor.id:
            raise _conflict("Cannot deactivate the sync account", "The service account cannot deactivate itself.")
        user = IdpSyncService._user_or_error(session, user_id)
        if not user.is_active or user.deleted_at is not None:
            return False
        if user.is_admin:
            IdpSyncService._ensure_not_last_admin(session, user_id, "deactivate")
        if user.is_maintainer:
            IdpSyncService._ensure_not_last_maintainer(session, user_id, "deactivate")
        user_repository.soft_delete(session, user_id)
        IdpSyncService._emit(session, actor, UserManagementEvent.USER_DEACTIVATED, user_id)
        return True

    @staticmethod
    def after_deactivation_commit(user_id: str, actor_id: str) -> None:
        """Side effects that must follow the commit; they never fail the request."""
        UserManagementService._after_status_change(user_id, actor_id, "user_deactivated", cleanup_mcp=True)


idp_sync_service = IdpSyncService()
