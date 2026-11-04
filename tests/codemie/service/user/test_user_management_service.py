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

from unittest.mock import MagicMock, patch

import pytest

from codemie.service.activity.activity_models import ActivityDomain, ActivityEntityType, UserManagementEvent
from codemie.service.budget.budget_models import Budget, ProjectBudgetAssignment, ProjectMemberBudgetAssignment
from codemie.service.project.project_assignment_service import ProjectAssignmentService
from codemie.service.user.user_management_service import UserManagementService

_REAL_BUDGET_SYNC = ProjectAssignmentService._sync_project_budget_member_added


def _mock_user(user_id: str = "user-1") -> MagicMock:
    user = MagicMock()
    user.id = user_id
    user.is_admin = False
    user.is_maintainer = False
    user.is_active = True
    return user


# ---------------------------------------------------------------------------
# create_local_user
# ---------------------------------------------------------------------------


@patch("codemie.service.user.user_management_service.activity_event_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_create_local_user_emits_user_created_event(mock_repo, mock_activity):
    session = MagicMock()
    mock_repo.exists_by_email.return_value = False
    mock_repo.exists_by_username.return_value = False
    created_user = _mock_user("new-user-id")
    mock_repo.create.return_value = created_user

    with patch("codemie.service.password_service.password_service.hash_password", return_value="hashed"):
        UserManagementService.create_local_user(session, email="a@b.com", username="auser", password="password123")

    mock_activity.insert.assert_called_once()
    call_args = mock_activity.insert.call_args[0]
    event_dto = call_args[0]
    assert event_dto.domain == ActivityDomain.USER_MANAGEMENT
    assert event_dto.event_type == UserManagementEvent.USER_CREATED
    assert event_dto.entity_type == ActivityEntityType.USER
    assert event_dto.entity_id == "new-user-id"


@patch("codemie.service.user.user_management_service.activity_event_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_create_local_user_no_event_on_duplicate_email(mock_repo, mock_activity):
    """No event should be emitted when creation is rejected due to duplicate email."""
    from codemie.core.exceptions import ExtendedHTTPException

    session = MagicMock()
    mock_repo.exists_by_email.return_value = True

    with pytest.raises(ExtendedHTTPException):
        UserManagementService.create_local_user(session, email="dup@b.com", username="auser", password="password123")

    mock_activity.insert.assert_not_called()


@patch("codemie.service.user.user_management_service.activity_event_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_create_local_user_persists_is_auditor_true(mock_repo, mock_activity):
    """EPMCDME-10930 regression: create_local_user must accept and persist is_auditor.

    Previously create_local_user had no is_auditor parameter at all, so a request to
    create an auditor user silently created a non-auditor (is_auditor=False) instead.
    """
    session = MagicMock()
    mock_repo.exists_by_email.return_value = False
    mock_repo.exists_by_username.return_value = False
    mock_repo.create.side_effect = lambda _session, user: user

    with patch("codemie.service.password_service.password_service.hash_password", return_value="hashed"):
        created = UserManagementService.create_local_user(
            session, email="a@b.com", username="auser", password="password123", is_auditor=True
        )

    assert created.is_auditor is True


@patch("codemie.service.user.user_management_service.activity_event_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_create_local_user_defaults_is_auditor_false(mock_repo, mock_activity):
    session = MagicMock()
    mock_repo.exists_by_email.return_value = False
    mock_repo.exists_by_username.return_value = False
    mock_repo.create.side_effect = lambda _session, user: user

    with patch("codemie.service.password_service.password_service.hash_password", return_value="hashed"):
        created = UserManagementService.create_local_user(
            session, email="a@b.com", username="auser", password="password123"
        )

    assert created.is_auditor is False


# ---------------------------------------------------------------------------
# update_user
# ---------------------------------------------------------------------------


@patch("codemie.service.user.user_management_service.activity_event_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_update_user_emits_user_updated_event(mock_repo, mock_activity):
    session = MagicMock()
    updated_user = _mock_user("target-id")
    mock_repo.update.return_value = updated_user

    UserManagementService.update_user(session, user_id="target-id", actor_user_id="admin-id", is_admin=True)

    mock_activity.insert.assert_called_once()
    call_args = mock_activity.insert.call_args[0]
    event_dto = call_args[0]
    assert event_dto.domain == ActivityDomain.USER_MANAGEMENT
    assert event_dto.event_type == UserManagementEvent.USER_UPDATED
    assert event_dto.entity_type == ActivityEntityType.USER
    assert event_dto.entity_id == "target-id"
    assert event_dto.actor_id == "admin-id"


@patch("codemie.service.user.user_management_service.activity_event_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_update_user_no_event_when_user_not_found(mock_repo, mock_activity):
    """No event emitted when repository returns None (user not found)."""
    session = MagicMock()
    mock_repo.update.return_value = None

    result = UserManagementService.update_user(session, user_id="missing-id", actor_user_id="admin-id")

    assert result is None
    mock_activity.insert.assert_not_called()


# ---------------------------------------------------------------------------
# deactivate_user
# ---------------------------------------------------------------------------


@patch("codemie.service.user.user_management_service.activity_event_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_deactivate_user_emits_user_deactivated_event(mock_repo, mock_activity):
    session = MagicMock()
    target_user = _mock_user("target-id")
    target_user.is_admin = False
    deactivated_user = _mock_user("target-id")
    deactivated_user.is_active = False
    mock_repo.get_by_id.side_effect = [target_user, deactivated_user]
    mock_repo.soft_delete.return_value = None

    UserManagementService.deactivate_user(session, user_id="target-id", actor_user_id="admin-id")

    mock_activity.insert.assert_called_once()
    call_args = mock_activity.insert.call_args[0]
    event_dto = call_args[0]
    assert event_dto.domain == ActivityDomain.USER_MANAGEMENT
    assert event_dto.event_type == UserManagementEvent.USER_DEACTIVATED
    assert event_dto.entity_type == ActivityEntityType.USER
    assert event_dto.entity_id == "target-id"
    assert event_dto.actor_id == "admin-id"


@patch("codemie.service.user.user_management_service.activity_event_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_deactivate_user_no_event_when_user_not_found(mock_repo, mock_activity):
    """No event emitted when user is not found (raises 404)."""
    from codemie.core.exceptions import ExtendedHTTPException

    session = MagicMock()
    mock_repo.get_by_id.return_value = None

    with pytest.raises(ExtendedHTTPException):
        UserManagementService.deactivate_user(session, user_id="ghost-id", actor_user_id="admin-id")

    mock_activity.insert.assert_not_called()


def _deactivation_repo(mock_repo, target, actor, active_maintainers=2):
    mock_repo.get_by_id.side_effect = lambda _s, uid: target if uid == target.id else actor
    mock_repo.count_active_maintainers.return_value = active_maintainers
    mock_repo.count_active_admins.return_value = 2


@patch("codemie.service.user.user_management_service.activity_event_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_deactivate_user_refuses_own_account(mock_repo, mock_activity):
    from codemie.core.exceptions import ExtendedHTTPException

    admin = _mock_user("admin-id")
    _deactivation_repo(mock_repo, admin, admin)

    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.deactivate_user(MagicMock(), user_id="admin-id", actor_user_id="admin-id")

    assert exc_info.value.code == 403
    assert exc_info.value.message == "You cannot deactivate your own account"
    mock_repo.soft_delete.assert_not_called()
    mock_activity.insert.assert_not_called()


@patch("codemie.service.user.user_management_service.activity_event_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_deactivate_maintainer_requires_a_maintainer_actor(mock_repo, mock_activity):
    from codemie.core.exceptions import ExtendedHTTPException

    target = _mock_user("target-id")
    target.is_maintainer = True
    _deactivation_repo(mock_repo, target, _mock_user("admin-id"))

    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.deactivate_user(MagicMock(), user_id="target-id", actor_user_id="admin-id")

    assert exc_info.value.code == 403
    assert exc_info.value.details == "Only maintainers can deactivate a maintainer"
    mock_repo.soft_delete.assert_not_called()


@patch("codemie.service.user.user_management_service.activity_event_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_maintainer_can_deactivate_a_maintainer(mock_repo, mock_activity):
    session = MagicMock()
    target = _mock_user("target-id")
    target.is_maintainer = True
    maintainer = _mock_user("admin-id")
    maintainer.is_maintainer = True
    _deactivation_repo(mock_repo, target, maintainer)

    UserManagementService.deactivate_user(session, user_id="target-id", actor_user_id="admin-id")

    mock_repo.soft_delete.assert_called_once_with(session, "target-id")


@patch("codemie.service.user.user_management_service.activity_event_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_admin_can_deactivate_an_admin(mock_repo, mock_activity):
    session = MagicMock()
    target = _mock_user("target-id")
    target.is_admin = True
    _deactivation_repo(mock_repo, target, _mock_user("admin-id"))

    UserManagementService.deactivate_user(session, user_id="target-id", actor_user_id="admin-id")

    mock_repo.soft_delete.assert_called_once_with(session, "target-id")


@patch("codemie.service.user.user_management_service.activity_event_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_deactivate_last_maintainer_is_refused(mock_repo, mock_activity):
    from codemie.core.exceptions import ExtendedHTTPException

    target = _mock_user("target-id")
    target.is_maintainer = True
    maintainer = _mock_user("admin-id")
    maintainer.is_maintainer = True
    _deactivation_repo(mock_repo, target, maintainer, active_maintainers=1)

    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.deactivate_user(MagicMock(), user_id="target-id", actor_user_id="admin-id")

    assert exc_info.value.code == 403
    assert exc_info.value.message == "Cannot deactivate last maintainer - assign another maintainer first"
    mock_repo.soft_delete.assert_not_called()


# ---------------------------------------------------------------------------
# reactivate_user
# ---------------------------------------------------------------------------

_SVC = "codemie.service.user.user_management_service"


def _inactive_user(user_id: str = "target-id") -> MagicMock:
    user = _mock_user(user_id)
    user.is_active = False
    user.deleted_at = "2026-01-01T00:00:00"
    return user


@pytest.fixture
def reactivation_env():
    """Patch collaborators of reactivate_user_flow and expose a shared call-order manager."""
    session = MagicMock()
    manager = MagicMock()
    manager.attach_mock(session.commit, "commit")
    with (
        patch("codemie.clients.postgres.get_session") as mock_get_session,
        patch(f"{_SVC}.user_repository") as mock_repo,
        patch(f"{_SVC}.activity_event_repository") as mock_activity,
        patch(f"{_SVC}.invalidate_user_from_cache") as mock_invalidate,
        patch(f"{_SVC}.logger") as mock_logger,
        patch.object(UserManagementService, "get_user_with_relationships") as mock_detail,
        patch("codemie.repository.user_project_repository.user_project_repository") as mock_up_repo,
        patch(
            "codemie.service.project.project_assignment_service.ProjectAssignmentService"
            "._sync_project_budget_member_added"
        ) as mock_budget_sync,
    ):
        mock_up_repo.get_by_user_id.return_value = []
        mock_get_session.return_value.__enter__.return_value = session
        manager.attach_mock(mock_activity.insert, "insert")
        manager.attach_mock(mock_logger.info, "log_info")
        manager.attach_mock(mock_invalidate, "invalidate")
        target = _inactive_user()
        admin = _mock_user("admin-id")
        mock_repo.get_by_id.side_effect = lambda _s, uid: target if uid == "target-id" else admin
        yield MagicMock(
            session=session,
            manager=manager,
            repo=mock_repo,
            activity=mock_activity,
            invalidate=mock_invalidate,
            logger=mock_logger,
            detail=mock_detail,
            target=target,
            up_repo=mock_up_repo,
            budget_sync=mock_budget_sync,
        )


def _assert_nothing_side_effected(env):
    env.activity.insert.assert_not_called()
    env.session.commit.assert_not_called()
    env.logger.info.assert_not_called()
    env.invalidate.assert_not_called()


def test_reactivate_user_emits_user_reactivated_event(reactivation_env):
    env = reactivation_env

    result = UserManagementService.reactivate_user_flow("target-id", "admin-id")

    env.activity.insert.assert_called_once()
    event_dto, event_session = env.activity.insert.call_args[0]
    assert event_dto.domain == ActivityDomain.USER_MANAGEMENT
    assert event_dto.event_type == UserManagementEvent.USER_REACTIVATED
    assert event_dto.entity_type == ActivityEntityType.USER
    assert event_dto.entity_id == "target-id"
    assert event_dto.actor_id == "admin-id"
    assert event_session is env.session
    env.repo.reactivate.assert_called_once_with(env.session, "target-id")
    env.session.commit.assert_called_once_with()
    names = [c[0] for c in env.manager.mock_calls if c[0] in ("insert", "commit")]
    assert names == ["insert", "commit"]
    assert result is env.detail.return_value


def test_reactivate_user_logs_once_after_commit(reactivation_env):
    env = reactivation_env

    UserManagementService.reactivate_user_flow("target-id", "admin-id")

    env.logger.info.assert_called_once_with(
        "user_reactivated: actor_user_id=admin-id, target_user_id=target-id, domain=user_management"
    )
    names = [c[0] for c in env.manager.mock_calls if c[0] in ("commit", "log_info")]
    assert names == ["commit", "log_info"]


def test_reactivate_user_invalidates_cache_once_after_commit(reactivation_env):
    env = reactivation_env

    UserManagementService.reactivate_user_flow("target-id", "admin-id")

    env.invalidate.assert_called_once_with("target-id")
    names = [c[0] for c in env.manager.mock_calls if c[0] in ("commit", "invalidate")]
    assert names == ["commit", "invalidate"]


def test_reactivate_user_not_found_404_adds_no_event(reactivation_env):
    from codemie.core.exceptions import ExtendedHTTPException

    env = reactivation_env
    env.repo.get_by_id.side_effect = lambda _s, _uid: None

    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.reactivate_user_flow("ghost-id", "admin-id")

    assert exc_info.value.code == 404
    env.repo.reactivate.assert_not_called()
    _assert_nothing_side_effected(env)


def test_reactivate_user_already_active_409_adds_no_event(reactivation_env):
    from codemie.core.exceptions import ExtendedHTTPException

    env = reactivation_env
    env.target.is_active = True
    env.target.deleted_at = None

    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.reactivate_user_flow("target-id", "admin-id")

    assert exc_info.value.code == 409
    env.repo.reactivate.assert_not_called()
    _assert_nothing_side_effected(env)


@pytest.mark.parametrize(
    ("is_active", "deleted_at"),
    [(True, "2026-01-01T00:00:00"), (False, None)],
    ids=["active_but_deleted_at_set", "inactive_but_deleted_at_none"],
)
def test_reactivate_user_half_state_is_reactivatable(reactivation_env, is_active, deleted_at):
    env = reactivation_env
    env.target.is_active = is_active
    env.target.deleted_at = deleted_at

    UserManagementService.reactivate_user_flow("target-id", "admin-id")

    env.repo.reactivate.assert_called_once_with(env.session, "target-id")
    env.activity.insert.assert_called_once()
    env.session.commit.assert_called_once_with()


def test_reactivate_user_commit_failure_skips_log_and_cache(reactivation_env):
    env = reactivation_env
    env.session.commit.side_effect = RuntimeError("db down")

    with pytest.raises(RuntimeError, match="db down"):
        UserManagementService.reactivate_user_flow("target-id", "admin-id")

    env.session.commit.assert_called_once_with()
    env.logger.info.assert_not_called()
    env.invalidate.assert_not_called()


def test_reactivate_user_refuses_self_reactivation(reactivation_env):
    from codemie.core.exceptions import ExtendedHTTPException

    env = reactivation_env

    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.reactivate_user_flow("admin-id", "admin-id")

    assert exc_info.value.code == 403
    env.repo.reactivate.assert_not_called()
    _assert_nothing_side_effected(env)


def test_reactivate_user_cache_failure_does_not_fail_the_request(reactivation_env):
    env = reactivation_env
    env.invalidate.side_effect = KeyError("racing scan")

    result = UserManagementService.reactivate_user_flow("target-id", "admin-id")

    assert result is env.detail.return_value
    env.logger.exception.assert_called_once()
    env.logger.info.assert_called_once()


def test_reactivate_maintainer_requires_a_maintainer_actor(reactivation_env):
    from codemie.core.exceptions import ExtendedHTTPException

    env = reactivation_env
    env.target.is_maintainer = True
    admin = _mock_user("admin-id")
    admin.is_maintainer = False
    env.repo.get_by_id.side_effect = lambda _s, uid: env.target if uid == "target-id" else admin

    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.reactivate_user_flow("target-id", "admin-id")

    assert exc_info.value.code == 403
    env.repo.reactivate.assert_not_called()
    _assert_nothing_side_effected(env)


def test_maintainer_can_reactivate_a_maintainer(reactivation_env):
    env = reactivation_env
    env.target.is_maintainer = True
    maintainer = _mock_user("admin-id")
    maintainer.is_maintainer = True
    env.repo.get_by_id.side_effect = lambda _s, uid: env.target if uid == "target-id" else maintainer

    UserManagementService.reactivate_user_flow("target-id", "admin-id")

    env.repo.reactivate.assert_called_once_with(env.session, "target-id")


def test_admin_can_reactivate_a_non_maintainer(reactivation_env):
    env = reactivation_env
    env.target.is_maintainer = False
    admin = _mock_user("admin-id")
    admin.is_maintainer = False
    env.repo.get_by_id.side_effect = lambda _s, uid: env.target if uid == "target-id" else admin

    UserManagementService.reactivate_user_flow("target-id", "admin-id")

    env.repo.reactivate.assert_called_once_with(env.session, "target-id")


def test_reactivate_user_restores_budget_shares_on_every_project(reactivation_env):
    env = reactivation_env
    env.up_repo.get_by_user_id.return_value = [MagicMock(project_name="alpha"), MagicMock(project_name="beta")]
    env.manager.attach_mock(env.budget_sync, "budget_sync")

    UserManagementService.reactivate_user_flow("target-id", "admin-id")

    env.up_repo.get_by_user_id.assert_called_once_with(env.session, "target-id")
    assert env.budget_sync.call_args_list == [
        ((env.session, "alpha", "target-id", "admin-id"),),
        ((env.session, "beta", "target-id", "admin-id"),),
    ]
    names = [c[0] for c in env.manager.mock_calls if c[0] in ("budget_sync", "commit")]
    assert names == ["budget_sync", "budget_sync", "commit"]


def test_reactivate_user_refused_touches_no_budget(reactivation_env):
    from codemie.core.exceptions import ExtendedHTTPException

    env = reactivation_env
    env.target.is_active = True
    env.target.deleted_at = None

    with pytest.raises(ExtendedHTTPException):
        UserManagementService.reactivate_user_flow("target-id", "admin-id")

    env.budget_sync.assert_not_called()


def _project_budget_queries(env, existing_share):
    """Feed the real budget sync: one project budget, the user's share lookup, then the equal-share lookup."""
    assignment = ProjectBudgetAssignment(
        id="pba-1",
        project_name="alpha",
        budget_category="platform",
        budget_id="bgt-1",
        allocation_mode="equal",
        assigned_by="admin",
    )
    env.session.exec.side_effect = [
        MagicMock(all=MagicMock(return_value=[assignment])),
        MagicMock(first=MagicMock(return_value=existing_share)),
        MagicMock(first=MagicMock(return_value=None)),
    ]
    env.session.get.return_value = Budget(
        budget_id="bgt-1",
        budget_type="project",
        name="alpha-budget",
        soft_budget=100.0,
        max_budget=200.0,
        budget_duration="30d",
        budget_category="platform",
        created_by="admin",
    )
    env.up_repo.get_by_user_id.return_value = [MagicMock(project_name="alpha")]
    env.budget_sync.side_effect = _REAL_BUDGET_SYNC


def test_reactivate_user_gets_share_of_budget_created_while_deactivated(reactivation_env):
    env = reactivation_env
    _project_budget_queries(env, existing_share=None)

    UserManagementService.reactivate_user_flow("target-id", "admin-id")

    shares = [c[0][0] for c in env.session.add.call_args_list if isinstance(c[0][0], ProjectMemberBudgetAssignment)]
    assert len(shares) == 1
    assert shares[0].user_id == "target-id"
    assert shares[0].project_name == "alpha"
    assert shares[0].project_budget_id == "bgt-1"
    assert shares[0].assigned_by == "admin-id"
    env.session.commit.assert_called_once_with()


def test_reactivate_user_keeps_an_existing_share(reactivation_env):
    env = reactivation_env
    _project_budget_queries(env, existing_share=MagicMock())

    UserManagementService.reactivate_user_flow("target-id", "admin-id")

    assert not [c for c in env.session.add.call_args_list if isinstance(c[0][0], ProjectMemberBudgetAssignment)]
