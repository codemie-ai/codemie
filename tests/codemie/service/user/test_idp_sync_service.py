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

"""IdpSyncService behaviour: idempotent upsert, assign, unassign, deactivate and reads (EPMCDME-16895).

Runs against in-memory SQLite for the repository layer; the PostgreSQL-only parts (``FOR UPDATE``,
READ COMMITTED re-evaluation) are asserted through the repository call, see the service module docstring.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.compiler import compiles
from sqlmodel import Session, SQLModel, select

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.repository.user_repository import user_repository
from codemie.rest_api.models.idp_sync import IdpAssignRequest, IdpUserUpsertRequest
from codemie.rest_api.models.user_management import UserDB, UserProject
from codemie.rest_api.security.user import User
from codemie.service.activity.activity_models import UserManagementEvent
from codemie.service.activity.activity_repository import activity_event_repository
from codemie.service.project.project_assignment_service import ProjectAssignmentService
from codemie.service.user import idp_sync_service as module
from codemie.service.user.idp_sync_service import IdpSyncService

ACTOR_ID = "11111111-1111-4111-8111-111111111111"
U1 = "8f14e45f-ceea-467a-9575-1c5e1f2b3d4a"
U2 = "9f14e45f-ceea-467a-9575-1c5e1f2b3d4b"


@compiles(ARRAY, "sqlite")
@compiles(JSONB, "sqlite")
def _as_json(_type, _compiler, **_kw):
    return "JSON"


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine, tables=[UserDB.__table__, UserProject.__table__, Application.__table__])
    with engine.begin() as connection:  # SQLite ignores the PostgreSQL partial predicate of this index
        connection.exec_driver_sql("DROP INDEX uix_user_projects_one_default")
    with Session(engine) as db:
        yield db


@pytest.fixture()
def actor() -> User:
    return User(
        id=ACTOR_ID,
        username="idp-sync",
        name="idp-sync",
        email="idp-sync@example.com",
        user_type="service_account",
        project_names=[],
        admin_project_names=[],
        knowledge_bases=[],
    )


@pytest.fixture()
def events():
    with patch.object(activity_event_repository, "insert") as insert:
        yield insert


def _req(user_id: str = U1, **overrides) -> IdpUserUpsertRequest:
    data = {
        "id": user_id,
        "email": f"{user_id[:4]}@example.com",
        "username": user_id[:4],
        "name": "N",
        "is_admin": False,
    }
    return IdpUserUpsertRequest(**{**data, **overrides})


def _upsert(session, actor, req):
    """The response of an upsert; ``_upsert_with_flag`` also gives the created flag."""
    return IdpSyncService.upsert_user(session, actor, req)[0]


def _assign(session, actor, user_id, req):
    return IdpSyncService.assign(session, actor, user_id, req)[0]


def _seed_user(session: Session, user_id: str = U1, **fields) -> UserDB:
    data = {"id": user_id, "email": f"{user_id[:4]}@example.com", "username": user_id[:4], "name": "N", **fields}
    user = UserDB(**data)
    session.add(user)
    session.commit()
    return user


def _event_types(events: MagicMock) -> list[str]:
    return [c.args[0].event_type for c in events.call_args_list]


# ------------------------------------------------------------------ upsert


def test_upsert_creates_user_without_personal_project(session, actor, events):
    result = _upsert(session, actor, _req())

    assert result.id == U1 and result.is_active and result.memberships == []
    assert session.exec(select(Application)).all() == []
    assert session.exec(select(UserProject)).all() == []
    assert _event_types(events) == [UserManagementEvent.USER_CREATED]
    assert events.call_args.args[0].actor_id == ACTOR_ID


def test_upsert_updates_differing_profile_and_admin(session, actor, events):
    _seed_user(session)
    with patch.object(user_repository, "lock_and_count_active_admins", return_value=2):
        _upsert(session, actor, _req(email="new@example.com", username="newname", name="New", is_admin=True))

    user = user_repository.get_by_id(session, U1)
    assert (user.email, user.username, user.name, user.is_admin) == ("new@example.com", "newname", "New", True)
    assert user.project_limit is None
    assert _event_types(events) == [UserManagementEvent.USER_UPDATED]
    assert events.call_args.args[0].actor_id == ACTOR_ID


def test_upsert_without_changes_is_a_noop(session, actor, events):
    _upsert(session, actor, _req())
    events.reset_mock()

    _upsert(session, actor, _req())

    events.assert_not_called()


def test_configured_admin_user_id_is_admin_even_when_flag_false(session, actor, events):
    with patch.object(config, "ADMIN_USER_ID", U1):
        result = _upsert(session, actor, _req(is_admin=False))

    assert result.is_admin is True


def test_demoting_the_last_admin_is_rejected_under_row_lock(session, actor, events):
    _seed_user(session, is_admin=True)

    with patch.object(user_repository, "lock_and_count_active_admins", return_value=1) as lock:
        with pytest.raises(ExtendedHTTPException) as exc:
            _upsert(session, actor, _req(is_admin=False))

    lock.assert_called_once_with(session)
    assert exc.value.code == 409
    assert user_repository.get_by_id(session, U1).is_admin is True


def test_parallel_demotions_second_one_sees_one_admin_and_is_rejected(session, actor, events):
    _seed_user(session, U1, is_admin=True)
    _seed_user(session, U2, is_admin=True)

    # first transaction holds the lock and sees both admins; the second waits, then sees only itself
    with patch.object(user_repository, "lock_and_count_active_admins", side_effect=[2, 1]) as lock:
        _upsert(session, actor, _req(U1, is_admin=False))
        with pytest.raises(ExtendedHTTPException) as exc:
            _upsert(session, actor, _req(U2, is_admin=False))

    assert lock.call_count == 2
    assert exc.value.code == 409


def test_soft_deleted_user_is_reactivated_keeping_memberships_and_adding_missing_budget_shares(session, actor, events):
    from datetime import UTC, datetime

    _seed_user(session, is_active=False, deleted_at=datetime.now(UTC))
    session.add(UserProject(user_id=U1, project_name="old-project"))
    session.commit()

    with (
        patch.object(ProjectAssignmentService, "_sync_project_budget_member_removed") as budget_removed,
        patch.object(ProjectAssignmentService, "_sync_project_budget_member_added") as budget_added,
    ):
        result = _upsert(session, actor, _req())

    user = user_repository.get_by_id(session, U1)
    assert user.is_active is True and user.deleted_at is None
    assert result.is_active is True
    assert [m.project_name for m in result.memberships] == ["old-project"]
    assert [m.project_name for m in session.exec(select(UserProject)).all()] == ["old-project"]
    # existing allocations are never deleted; only the missing shares are added, as the administrator reactivation does
    budget_removed.assert_not_called()
    budget_added.assert_called_once_with(session, "old-project", U1, actor.id)
    # recorded like the administrator reactivation, with the sync account as actor; no field changed, so no update
    assert [c.args[0].event_type for c in events.call_args_list] == [UserManagementEvent.USER_REACTIVATED]
    assert events.call_args.args[0].actor_id == ACTOR_ID


def test_reactivation_that_also_changes_fields_records_both_events(session, actor, events):
    from datetime import UTC, datetime

    _seed_user(session, name="Old", is_active=False, deleted_at=datetime.now(UTC))

    _upsert(session, actor, _req(name="Renamed"))

    assert [c.args[0].event_type for c in events.call_args_list] == [
        UserManagementEvent.USER_UPDATED,
        UserManagementEvent.USER_REACTIVATED,
    ]
    assert events.call_args_list[0].args[0].attributes == {"name": "Renamed"}


def test_retried_upsert_losing_the_insert_race_continues_as_update(session, actor, events):
    existing = _seed_user(session, name="Old")
    real_get = user_repository.get_by_id
    with (
        patch.object(user_repository, "get_by_id", side_effect=[None, existing, existing]) as get,
        patch.object(user_repository, "create", side_effect=IntegrityError("insert", {}, Exception("duplicate"))),
    ):
        result = _upsert(session, actor, _req(name="Renamed"))

    assert get.call_count == 3
    assert result.name == "Renamed"
    assert real_get(session, U1).name == "Renamed"


def test_unresolvable_conflict_on_create_is_409(session, actor, events):
    with (
        patch.object(user_repository, "get_by_id", return_value=None),
        patch.object(user_repository, "create", side_effect=IntegrityError("insert", {}, Exception("duplicate"))),
    ):
        with pytest.raises(ExtendedHTTPException) as exc:
            _upsert(session, actor, _req())

    assert exc.value.code == 409


def test_update_to_a_taken_email_is_409(session, actor, events):
    _seed_user(session, U1)
    _seed_user(session, U2)

    with pytest.raises(ExtendedHTTPException) as exc:
        _upsert(session, actor, _req(U2, email=f"{U1[:4]}@example.com"))

    assert exc.value.code == 409


# ------------------------------------------------------------------ assign


@pytest.fixture()
def budget_added():
    with patch.object(ProjectAssignmentService, "_sync_project_budget_member_added") as spy:
        yield spy


def test_assign_creates_missing_project_and_membership_with_actor(session, actor, events, budget_added):
    _seed_user(session)

    result = _assign(session, actor, U1, IdpAssignRequest(project_name="team-a", is_project_admin=True))

    project = session.exec(select(Application)).one()
    assert project.name == "team-a" and project.project_type == "shared"
    assert [(m.project_name, m.is_project_admin, m.is_personal) for m in result.memberships] == [
        ("team-a", True, False)
    ]
    budget_added.assert_called_once_with(session, "team-a", U1, ACTOR_ID)
    assert _event_types(events) == [UserManagementEvent.USER_PROJECT_ASSIGNED]
    assert events.call_args.args[0].actor_id == ACTOR_ID


def test_repeated_assign_is_a_noop(session, actor, events, budget_added):
    _seed_user(session)
    req = IdpAssignRequest(project_name="team-a")
    _assign(session, actor, U1, req)
    events.reset_mock()

    with patch.object(ProjectAssignmentService, "assign_user_to_project") as assign:
        _assign(session, actor, U1, req)

    assign.assert_not_called()
    events.assert_not_called()
    assert len(session.exec(select(UserProject)).all()) == 1


def test_assign_treats_409_from_assignment_service_as_done(session, actor, events, budget_added):
    _seed_user(session)
    conflict = ExtendedHTTPException(code=409, message="User already assigned to project")

    with patch.object(ProjectAssignmentService, "assign_user_to_project", side_effect=conflict):
        result = _assign(session, actor, U1, IdpAssignRequest(project_name="team-a"))

    assert result.id == U1


def test_assign_updates_a_differing_admin_flag(session, actor, events, budget_added):
    _seed_user(session)
    _assign(session, actor, U1, IdpAssignRequest(project_name="team-a", is_project_admin=False))
    events.reset_mock()

    with patch.object(
        ProjectAssignmentService, "update_user_project_role", wraps=ProjectAssignmentService.update_user_project_role
    ) as update:
        result = _assign(session, actor, U1, IdpAssignRequest(project_name="team-a", is_project_admin=True))

    update.assert_called_once()
    assert result.memberships[0].is_project_admin is True
    assert _event_types(events) == [UserManagementEvent.USER_PROJECT_ROLE_UPDATED]


def test_assign_to_personal_project_is_rejected(session, actor, events, budget_added):
    _seed_user(session)
    session.add(Application(id="p@example.com", name="p@example.com", project_type="personal"))
    session.commit()

    with pytest.raises(ExtendedHTTPException) as exc:
        _assign(session, actor, U1, IdpAssignRequest(project_name="p@example.com"))

    assert exc.value.code == 409
    assert session.exec(select(UserProject)).all() == []


def test_assign_own_personal_project_creates_it_and_the_membership(session, actor, events, budget_added):
    user = _seed_user(session)

    result = _assign(session, actor, U1, IdpAssignRequest(project_name=user.email.upper()))

    project = session.exec(select(Application).where(Application.name == user.email)).one()
    assert (project.project_type, project.created_by) == ("personal", U1)
    assert [(m.project_name, m.is_project_admin, m.is_personal) for m in result.memberships] == [
        (user.email, False, True)
    ]


def test_assign_own_personal_project_is_idempotent(session, actor, events, budget_added):
    user = _seed_user(session)

    _assign(session, actor, U1, IdpAssignRequest(project_name=user.email))
    _assign(session, actor, U1, IdpAssignRequest(project_name=user.email))

    assert len(session.exec(select(UserProject)).all()) == 1
    assert len(session.exec(select(Application)).all()) == 1


def test_assign_own_email_turns_an_unowned_shared_project_into_the_personal_one(session, actor, events, budget_added):
    user = _seed_user(session)
    session.add(Application(id=user.email, name=user.email, project_type="shared"))
    session.commit()

    result = _assign(session, actor, U1, IdpAssignRequest(project_name=user.email))

    project = session.exec(select(Application)).one()
    assert (project.project_type, project.created_by, project.cli_analytics_enabled) == ("personal", U1, False)
    assert [(m.project_name, m.is_personal) for m in result.memberships] == [(user.email, True)]


def test_assign_own_email_turns_the_project_the_login_added_the_user_to_into_the_personal_one(
    session, actor, events, budget_added
):
    user = _seed_user(session)
    session.add(Application(id=user.email, name=user.email, project_type="shared"))
    session.add(UserProject(user_id=U1, project_name=user.email, is_project_admin=False))
    session.commit()

    _assign(session, actor, U1, IdpAssignRequest(project_name=user.email))

    assert session.exec(select(Application)).one().project_type == "personal"
    assert len(session.exec(select(UserProject)).all()) == 1


def test_assign_own_email_is_refused_when_other_users_are_in_the_project(session, actor, events, budget_added):
    user = _seed_user(session)
    _seed_user(session, U2)
    session.add(Application(id=user.email, name=user.email, project_type="shared"))
    session.add(UserProject(user_id=U2, project_name=user.email, is_project_admin=False))
    session.commit()

    with pytest.raises(ExtendedHTTPException) as exc:
        _assign(session, actor, U1, IdpAssignRequest(project_name=user.email))

    assert exc.value.code == 409
    assert session.exec(select(Application)).one().project_type == "shared"


def test_assign_own_email_is_refused_when_the_project_is_another_users_personal_one(
    session, actor, events, budget_added
):
    user = _seed_user(session)
    _seed_user(session, U2)
    session.add(Application(id=user.email, name=user.email, project_type="personal", created_by=U2))
    session.commit()

    with pytest.raises(ExtendedHTTPException) as exc:
        _assign(session, actor, U1, IdpAssignRequest(project_name=user.email))

    assert exc.value.code == 409


def test_unassign_own_personal_project_removes_the_membership_and_keeps_the_project(
    session, actor, events, budget_removed
):
    user = _seed_user(session)
    _assign(session, actor, U1, IdpAssignRequest(project_name=user.email))

    assert IdpSyncService.unassign(session, actor, U1, user.email) is None

    assert session.exec(select(UserProject)).all() == []
    assert session.exec(select(Application).where(Application.name == user.email)).one().project_type == "personal"
    assert IdpSyncService.unassign(session, actor, U1, user.email) is None


def test_assign_unknown_user_is_404(session, actor, events, budget_added):
    with pytest.raises(ExtendedHTTPException) as exc:
        _assign(session, actor, U1, IdpAssignRequest(project_name="team-a"))

    assert exc.value.code == 404


def test_budget_failure_reraises_and_caller_rollback_leaves_nothing(session, actor, events):
    _seed_user(session)
    with (
        patch.object(
            ProjectAssignmentService, "_sync_project_budget_member_added", side_effect=RuntimeError("provider down")
        ),
        patch.object(session, "rollback", wraps=session.rollback) as rollback,
    ):
        with pytest.raises(RuntimeError):
            _assign(session, actor, U1, IdpAssignRequest(project_name="team-a"))

    rollback.assert_not_called()  # the transaction belongs to the router
    session.rollback()
    assert session.exec(select(UserProject)).all() == []
    assert session.exec(select(Application)).all() == []


# ---------------------------------------------------------------- unassign


@pytest.fixture()
def budget_removed():
    with patch.object(ProjectAssignmentService, "_sync_project_budget_member_removed") as spy:
        yield spy


def test_unassign_removes_membership_and_cleans_budget_with_actor(session, actor, events, budget_added, budget_removed):
    _seed_user(session)
    _assign(session, actor, U1, IdpAssignRequest(project_name="team-a"))
    events.reset_mock()

    IdpSyncService.unassign(session, actor, U1, "team-a")

    assert session.exec(select(UserProject)).all() == []
    budget_removed.assert_called_once_with(session, "team-a", U1)
    assert _event_types(events) == [UserManagementEvent.USER_PROJECT_REMOVED]
    assert events.call_args.args[0].actor_id == ACTOR_ID


def test_unassign_non_member_is_done(session, actor, events, budget_removed):
    _seed_user(session)
    session.add(Application(id="team-a", name="team-a"))
    session.commit()

    with patch.object(ProjectAssignmentService, "remove_user_from_project") as remove:
        IdpSyncService.unassign(session, actor, U1, "team-a")

    remove.assert_not_called()


def test_unassign_from_missing_project_is_done(session, actor, events, budget_removed):
    _seed_user(session)

    IdpSyncService.unassign(session, actor, U1, "nope")


def test_unassign_treats_404_not_assigned_race_as_done(session, actor, events, budget_added, budget_removed):
    _seed_user(session)
    _assign(session, actor, U1, IdpAssignRequest(project_name="team-a"))
    race = ExtendedHTTPException(code=404, message="User is not assigned to this project")

    with patch.object(ProjectAssignmentService, "remove_user_from_project", side_effect=race):
        IdpSyncService.unassign(session, actor, U1, "team-a")


def test_unassign_from_personal_project_is_rejected(session, actor, events, budget_removed):
    _seed_user(session)
    session.add(Application(id="p@example.com", name="p@example.com", project_type="personal"))
    session.commit()

    with pytest.raises(ExtendedHTTPException) as exc:
        IdpSyncService.unassign(session, actor, U1, "p@example.com")

    assert exc.value.code == 409


def test_unassign_project_creator_is_409(session, actor, events, budget_removed):
    _seed_user(session)
    session.add(Application(id="team-a", name="team-a", created_by=U1))
    session.add(UserProject(user_id=U1, project_name="team-a"))
    session.commit()

    with pytest.raises(ExtendedHTTPException) as exc:
        IdpSyncService.unassign(session, actor, U1, "team-a")

    assert exc.value.code == 409
    assert len(session.exec(select(UserProject)).all()) == 1


def test_unassign_unknown_user_is_404(session, actor, events, budget_removed):
    with pytest.raises(ExtendedHTTPException) as exc:
        IdpSyncService.unassign(session, actor, U1, "team-a")

    assert exc.value.code == 404


# -------------------------------------------------------------- deactivate


def test_deactivate_unknown_user_is_404(session, actor):
    with pytest.raises(ExtendedHTTPException) as exc:
        IdpSyncService.deactivate(session, actor, U1)

    assert exc.value.code == 404


def test_deactivate_refuses_the_service_account_itself(session, actor):
    _seed_user(session, ACTOR_ID, user_type="service_account")

    with pytest.raises(ExtendedHTTPException) as exc:
        IdpSyncService.deactivate(session, actor, ACTOR_ID)

    assert exc.value.code == 409
    assert session.get(UserDB, ACTOR_ID).is_active is True


def test_deactivate_last_admin_is_409_after_taking_the_row_lock(session, actor, events):
    _seed_user(session, is_admin=True)

    with patch.object(user_repository, "lock_and_count_active_admins", return_value=1) as lock:
        with pytest.raises(ExtendedHTTPException) as exc:
            IdpSyncService.deactivate(session, actor, U1)

    lock.assert_called_once_with(session)
    assert exc.value.code == 409
    assert session.get(UserDB, U1).is_active is True
    events.assert_not_called()


def test_deactivate_last_maintainer_is_409(session, actor, events):
    _seed_user(session, is_admin=True, is_maintainer=True)

    with (
        patch.object(user_repository, "lock_and_count_active_admins", return_value=2),
        patch.object(user_repository, "lock_active_maintainer_ids", return_value=[U1]),
    ):
        with pytest.raises(ExtendedHTTPException) as exc:
            IdpSyncService.deactivate(session, actor, U1)

    assert exc.value.code == 409
    assert session.get(UserDB, U1).is_active is True


def test_deactivate_a_maintainer_does_not_need_the_actor_to_be_one(session, actor, events):
    """The rank rule of the administrator UI does not apply to the sync account."""
    _seed_user(session, is_admin=True, is_maintainer=True)

    with (
        patch.object(user_repository, "lock_and_count_active_admins", return_value=2),
        patch.object(user_repository, "lock_active_maintainer_ids", return_value=[U1, U2]),
    ):
        assert IdpSyncService.deactivate(session, actor, U1) is True

    session.expire_all()
    assert session.get(UserDB, U1).is_active is False
    assert _event_types(events) == [UserManagementEvent.USER_DEACTIVATED]
    assert events.call_args.args[0].actor_id == ACTOR_ID


def test_deactivate_locks_administrators_before_maintainers(session, actor, events):
    _seed_user(session, is_admin=True, is_maintainer=True)
    order = MagicMock()
    order.admins.return_value = 2
    order.maintainers.return_value = [U1, U2]

    with (
        patch.object(user_repository, "lock_and_count_active_admins", order.admins),
        patch.object(user_repository, "lock_active_maintainer_ids", order.maintainers),
    ):
        IdpSyncService.deactivate(session, actor, U1)

    assert order.mock_calls == [call.admins(session), call.maintainers(session)]


def test_deactivate_non_admin_takes_no_lock(session, actor, events):
    _seed_user(session)
    with (
        patch.object(user_repository, "lock_and_count_active_admins") as admins,
        patch.object(user_repository, "lock_active_maintainer_ids") as maintainers,
    ):
        assert IdpSyncService.deactivate(session, actor, U1) is True

    admins.assert_not_called()
    maintainers.assert_not_called()
    session.expire_all()
    assert session.get(UserDB, U1).is_active is False


def test_after_deactivation_commit_uses_the_shared_post_commit_steps():
    with patch.object(module.UserManagementService, "_after_status_change") as after:
        IdpSyncService.after_deactivation_commit(U1, ACTOR_ID)

    after.assert_called_once_with(U1, ACTOR_ID, "user_deactivated", cleanup_mcp=True)


def test_deactivate_already_deactivated_is_a_noop(session, actor, events):
    from datetime import UTC, datetime

    _seed_user(session, is_active=False, deleted_at=datetime.now(UTC))

    assert IdpSyncService.deactivate(session, actor, U1) is False

    events.assert_not_called()


# ------------------------------------------------------------------- reads


def test_get_unknown_user_is_404(session):
    with pytest.raises(ExtendedHTTPException) as exc:
        IdpSyncService.get_user(session, U1)

    assert exc.value.code == 404


def test_get_deactivated_user_reports_inactive(session):
    from datetime import UTC, datetime

    _seed_user(session, is_active=False, deleted_at=datetime.now(UTC))

    assert IdpSyncService.get_user(session, U1).is_active is False


def test_list_includes_deactivated_in_id_order_with_memberships(session):
    from datetime import UTC, datetime

    _seed_user(session, U2, is_active=False, deleted_at=datetime.now(UTC))
    _seed_user(session, U1)
    session.add(UserProject(user_id=U1, project_name="team-a", is_project_admin=True))
    session.commit()

    page = IdpSyncService.list_users(session, 0, 100)

    assert [i.id for i in page.items] == [U1, U2]
    assert [i.is_active for i in page.items] == [True, False]
    assert page.items[0].memberships[0].project_name == "team-a"
    assert (page.total, page.page, page.per_page) == (2, 0, 100)


# -------------------------------------------------------------- maintainer


def test_the_last_maintainer_is_not_demoted_by_an_admin_false_request(session, actor, events):
    _seed_user(session, is_admin=True, is_maintainer=True, project_limit=None)

    with (
        patch.object(user_repository, "lock_and_count_active_admins", return_value=2),
        patch.object(user_repository, "lock_active_maintainer_ids", return_value=[U1]),
    ):
        with pytest.raises(ExtendedHTTPException) as exc:
            _upsert(session, actor, _req(is_admin=False))

    assert exc.value.code == 409
    user = user_repository.get_by_id(session, U1)
    assert (user.is_admin, user.is_maintainer, user.project_limit) == (True, True, None)
    events.assert_not_called()


def test_another_maintainer_is_demoted_with_the_admin_flag_when_the_request_says_not_admin(session, actor, events):
    _seed_user(session, is_admin=True, is_maintainer=True, project_limit=None)

    with (
        patch.object(user_repository, "lock_and_count_active_admins", return_value=2),
        patch.object(user_repository, "lock_active_maintainer_ids", return_value=[U1, U2]),
    ):
        _upsert(session, actor, _req(is_admin=False))

    user = user_repository.get_by_id(session, U1)
    assert (user.is_admin, user.is_maintainer) == (False, False)
    assert user.project_limit == config.USER_PROJECT_LIMIT
    assert _updated_attributes(events) == {"is_admin": False, "is_maintainer": False}


def test_a_reactivated_maintainer_follows_the_idp_without_the_last_maintainer_lock(session, actor, events):
    from datetime import UTC, datetime

    _seed_user(
        session, is_admin=True, is_maintainer=True, project_limit=None, is_active=False, deleted_at=datetime.now(UTC)
    )

    with patch.object(user_repository, "lock_active_maintainer_ids") as lock:
        _upsert(session, actor, _req(is_admin=False))

    lock.assert_not_called()
    user = user_repository.get_by_id(session, U1)
    assert user.is_active and user.deleted_at is None
    assert (user.is_admin, user.is_maintainer) == (False, False)


def test_a_legacy_maintainer_stored_without_admin_is_not_made_admin_by_the_api(session, actor, events):
    _seed_user(session, is_admin=False, is_maintainer=True, project_limit=config.USER_PROJECT_LIMIT)

    with patch.object(user_repository, "lock_active_maintainer_ids", return_value=[U1, U2]):
        _upsert(session, actor, _req(is_admin=False))

    user = user_repository.get_by_id(session, U1)
    assert (user.is_admin, user.is_maintainer) == (False, False)


def test_an_admin_true_request_leaves_a_stored_maintainer_untouched(session, actor, events):
    _seed_user(session, is_admin=True, is_maintainer=True)

    _upsert(session, actor, _req(is_admin=True))

    assert user_repository.get_by_id(session, U1).is_maintainer is True
    events.assert_not_called()


def test_non_maintainer_demotion_is_unchanged(session, actor, events):
    _seed_user(session, is_admin=True, is_maintainer=False)

    with patch.object(user_repository, "lock_and_count_active_admins", return_value=2):
        _upsert(session, actor, _req(is_admin=False))

    assert user_repository.get_by_id(session, U1).is_admin is False


def test_non_maintainer_last_admin_demotion_is_still_409(session, actor, events):
    _seed_user(session, is_admin=True, is_maintainer=False)

    with patch.object(user_repository, "lock_and_count_active_admins", return_value=1):
        with pytest.raises(ExtendedHTTPException) as exc:
            _upsert(session, actor, _req(is_admin=False))

    assert exc.value.code == 409


# ----------------------------------------------------------------- picture

_PIC = "https://example.com/a.png"


def test_new_picture_is_written_and_recorded_in_event(session, actor, events):
    _seed_user(session)

    _upsert(session, actor, _req(picture=_PIC))

    assert user_repository.get_by_id(session, U1).picture == _PIC
    assert events.call_args.args[0].attributes["picture"] == _PIC


def test_same_picture_is_a_noop(session, actor, events):
    _seed_user(session, picture=_PIC)

    _upsert(session, actor, _req(picture=_PIC))

    events.assert_not_called()


@pytest.mark.parametrize("stored", [None, "", _PIC])
@pytest.mark.parametrize("sent", [None, ""])
def test_omitted_or_empty_picture_never_clears(session, actor, events, stored, sent):
    _seed_user(session, picture=stored)
    kwargs = {} if sent is None else {"picture": sent}

    _upsert(session, actor, _req(**kwargs))

    assert (user_repository.get_by_id(session, U1).picture or "") == (stored or "")
    events.assert_not_called()


def test_create_stores_non_empty_picture(session, actor, events):
    result = _upsert(session, actor, _req(picture=_PIC))

    assert user_repository.get_by_id(session, U1).picture == _PIC
    assert result.picture == _PIC and result.is_maintainer is False


def test_create_without_picture_stores_none(session, actor, events):
    result = _upsert(session, actor, _req(picture=""))

    assert user_repository.get_by_id(session, U1).picture is None
    assert result.picture is None


# ------------------------------------------------------------ maintainer and auditor roles


def _updated_attributes(events: MagicMock) -> dict:
    updated = [c.args[0] for c in events.call_args_list if c.args[0].event_type == UserManagementEvent.USER_UPDATED]
    assert len(updated) == 1
    return updated[0].attributes


def test_an_omitted_role_flag_leaves_the_stored_flag_alone(session, actor, events):
    _seed_user(session, is_admin=True, is_maintainer=True, is_auditor=True)

    result = _upsert(session, actor, _req(is_admin=True))

    user = user_repository.get_by_id(session, U1)
    assert (user.is_maintainer, user.is_auditor) == (True, True)
    assert result.is_maintainer is True and result.is_auditor is True
    events.assert_not_called()


def test_granting_the_auditor_role_is_audited_with_the_service_account_as_actor(session, actor, events):
    _seed_user(session)

    result = _upsert(session, actor, _req(is_auditor=True))

    assert user_repository.get_by_id(session, U1).is_auditor is True and result.is_auditor is True
    assert _updated_attributes(events) == {"is_auditor": True}
    assert events.call_args.args[0].actor_id == ACTOR_ID


def test_removing_the_auditor_role(session, actor, events):
    _seed_user(session, is_auditor=True)

    _upsert(session, actor, _req(is_auditor=False))

    assert user_repository.get_by_id(session, U1).is_auditor is False
    assert _updated_attributes(events) == {"is_auditor": False}


def test_granting_the_maintainer_role_to_an_administrator(session, actor, events):
    _seed_user(session, is_admin=True)

    result = _upsert(session, actor, _req(is_admin=True, is_maintainer=True))

    assert user_repository.get_by_id(session, U1).is_maintainer is True and result.is_maintainer is True
    assert _updated_attributes(events) == {"is_maintainer": True}


def test_granting_maintainer_and_admin_together_audits_both(session, actor, events):
    _seed_user(session)

    _upsert(session, actor, _req(is_admin=True, is_maintainer=True))

    user = user_repository.get_by_id(session, U1)
    assert (user.is_admin, user.is_maintainer, user.project_limit) == (True, True, None)
    assert _updated_attributes(events) == {"is_admin": True, "is_maintainer": True}


def test_removing_a_maintainer_keeps_the_administrator_when_the_idp_still_says_admin(session, actor, events):
    _seed_user(session, is_admin=True, is_maintainer=True)

    with patch.object(user_repository, "lock_active_maintainer_ids", return_value=[U1, U2]) as lock:
        _upsert(session, actor, _req(is_admin=True, is_maintainer=False))

    lock.assert_called_once()
    user = user_repository.get_by_id(session, U1)
    assert (user.is_admin, user.is_maintainer) == (True, False)
    assert _updated_attributes(events) == {"is_maintainer": False}


def test_removing_maintainer_and_admin_together_lets_both_go(session, actor, events):
    _seed_user(session, is_admin=True, is_maintainer=True, project_limit=None)

    with (
        patch.object(user_repository, "lock_and_count_active_admins", return_value=2),
        patch.object(user_repository, "lock_active_maintainer_ids", return_value=[U1, U2]),
    ):
        _upsert(session, actor, _req(is_admin=False, is_maintainer=False))

    user = user_repository.get_by_id(session, U1)
    assert (user.is_admin, user.is_maintainer) == (False, False)
    assert user.project_limit == config.USER_PROJECT_LIMIT
    assert _updated_attributes(events) == {"is_admin": False, "is_maintainer": False}


def test_removing_the_last_active_maintainer_is_409_and_changes_nothing(session, actor, events):
    _seed_user(session, is_admin=True, is_maintainer=True)

    with patch.object(user_repository, "lock_active_maintainer_ids", return_value=[U1]):
        with pytest.raises(ExtendedHTTPException) as exc:
            _upsert(session, actor, _req(is_admin=True, is_maintainer=False))

    assert exc.value.code == 409
    assert user_repository.get_by_id(session, U1).is_maintainer is True
    events.assert_not_called()


def test_the_last_maintainer_check_ignores_the_target_itself_when_others_remain(session, actor, events):
    _seed_user(session, is_admin=True, is_maintainer=True)

    with patch.object(user_repository, "lock_active_maintainer_ids", return_value=[U1, U2]):
        _upsert(session, actor, _req(is_admin=True, is_maintainer=False))

    assert user_repository.get_by_id(session, U1).is_maintainer is False


def test_a_reactivated_maintainer_can_be_demoted_without_the_last_maintainer_lock(session, actor, events):
    from datetime import UTC, datetime

    _seed_user(session, is_admin=True, is_maintainer=True, is_active=False, deleted_at=datetime.now(UTC))

    with patch.object(user_repository, "lock_active_maintainer_ids") as lock:
        _upsert(session, actor, _req(is_admin=True, is_maintainer=False))

    lock.assert_not_called()
    user = user_repository.get_by_id(session, U1)
    assert user.is_active and user.is_maintainer is False


def test_the_administrators_are_locked_before_the_maintainers(session, actor, events):
    _seed_user(session, is_admin=True, is_maintainer=True, project_limit=None)
    order: list[str] = []

    def admins(_session):
        order.append("admins")
        return 2

    def maintainers(_session):
        order.append("maintainers")
        return [U1, U2]

    with (
        patch.object(user_repository, "lock_and_count_active_admins", side_effect=admins),
        patch.object(user_repository, "lock_active_maintainer_ids", side_effect=maintainers),
    ):
        _upsert(session, actor, _req(is_admin=False, is_maintainer=False))

    assert order == ["admins", "maintainers"]


def test_the_configured_admin_user_stays_admin_when_its_roles_are_removed(session, actor, events):
    _seed_user(session, is_admin=True, is_maintainer=True, project_limit=None)

    with (
        patch.object(config, "ADMIN_USER_ID", U1),
        patch.object(user_repository, "lock_active_maintainer_ids", return_value=[U1, U2]),
    ):
        _upsert(session, actor, _req(is_admin=False, is_maintainer=False))

    user = user_repository.get_by_id(session, U1)
    assert (user.is_admin, user.is_maintainer) == (True, False)


def test_a_new_user_is_created_with_the_roles_from_the_idp(session, actor, events):
    result = _upsert(session, actor, _req(is_admin=True, is_maintainer=True, is_auditor=True))

    assert (result.is_admin, result.is_maintainer, result.is_auditor) == (True, True, True)
    created = events.call_args.args[0]
    assert created.event_type == UserManagementEvent.USER_CREATED
    assert created.attributes == {"is_admin": True, "is_maintainer": True, "is_auditor": True}


def test_a_new_user_without_role_flags_has_no_roles(session, actor, events):
    result = _upsert(session, actor, _req())

    assert (result.is_admin, result.is_maintainer, result.is_auditor) == (False, False, False)
    assert events.call_args.args[0].attributes == {"is_admin": False}


def test_reads_report_the_auditor_flag(session):
    _seed_user(session, is_auditor=True)

    assert IdpSyncService.get_user(session, U1).is_auditor is True
    assert IdpSyncService.list_users(session, 0, 100).items[0].is_auditor is True


def test_lock_active_maintainer_ids_lists_only_active_maintainers(session):
    from datetime import UTC, datetime

    _seed_user(session, U1, is_admin=True, is_maintainer=True)
    _seed_user(session, U2, is_admin=True, is_maintainer=True, is_active=False, deleted_at=datetime.now(UTC))
    _seed_user(session, "7f14e45f-ceea-467a-9575-1c5e1f2b3d4c", is_admin=True)

    assert user_repository.lock_active_maintainer_ids(session) == [U1]


# ---------------------------------------------------------- user type, flags


def test_upsert_reports_whether_it_created_the_user(session, actor, events):
    _, created = IdpSyncService.upsert_user(session, actor, _req())
    _, created_again = IdpSyncService.upsert_user(session, actor, _req(name="Renamed"))

    assert created is True
    assert created_again is False


def test_a_new_user_is_regular_unless_the_type_is_sent(session, actor, events):
    assert _upsert(session, actor, _req(U1)).user_type == "regular"
    assert _upsert(session, actor, _req(U2, user_type="external")).user_type == "external"
    assert session.get(UserDB, U2).user_type == "external"


def test_upsert_changes_the_user_type_and_audits_it(session, actor, events):
    _seed_user(session)

    result = _upsert(session, actor, _req(user_type="external"))

    assert result.user_type == "external"
    assert session.get(UserDB, U1).user_type == "external"
    assert events.call_args.args[0].attributes == {"user_type": "external"}


def test_an_omitted_user_type_leaves_the_stored_type_alone(session, actor, events):
    _seed_user(session, user_type="external")

    result = _upsert(session, actor, _req())

    assert result.user_type == "external"
    events.assert_not_called()


def test_reads_report_the_user_type(session):
    _seed_user(session, user_type="external")

    assert IdpSyncService.get_user(session, U1).user_type == "external"
    assert IdpSyncService.list_users(session, 0, 10).items[0].user_type == "external"


def test_assign_reports_the_project_only_when_the_member_set_changed(session, actor, events, budget_added):
    _seed_user(session)
    req = IdpAssignRequest(project_name="team-a")

    _, first = IdpSyncService.assign(session, actor, U1, req)
    _, again = IdpSyncService.assign(session, actor, U1, req)
    _, role_only = IdpSyncService.assign(
        session, actor, U1, IdpAssignRequest(project_name="team-a", is_project_admin=True)
    )

    assert first == "team-a"
    assert again is None
    assert role_only is None


def test_unassign_reports_the_project_only_when_the_user_was_removed(
    session, actor, events, budget_added, budget_removed
):
    _seed_user(session)
    _seed_user(session, U2)
    IdpSyncService.assign(session, actor, U2, IdpAssignRequest(project_name="team-a"))
    IdpSyncService.assign(session, actor, U1, IdpAssignRequest(project_name="team-a"))

    assert IdpSyncService.unassign(session, actor, U1, "team-a") == "team-a"
    assert IdpSyncService.unassign(session, actor, U1, "team-a") is None
    assert IdpSyncService.unassign(session, actor, U1, "no-such-project") is None
