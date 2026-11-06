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

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.provisioning_service import provisioning_service
from tests.codemie.configs.provisioning_clients_support import CLIENT_USER_ID, make_client, registered_clients

MODULE = "codemie.service.provisioning_service"


def _patch(name):
    return patch(f"{MODULE}.{name}")


def _user(email, user_id=None, user_type="regular"):
    return SimpleNamespace(id=user_id or f"id-{email}", email=email, user_type=user_type)


def _app(name="epm-aimf", project_type="shared", deleted_at=None, cost_center_id=None):
    return SimpleNamespace(
        name=name, project_type=project_type, deleted_at=deleted_at, cost_center_id=cost_center_id, id=uuid4()
    )


def _cost_center(name="nbs-cmpa"):
    return SimpleNamespace(id=uuid4(), name=name, deleted_at=None)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def session():
    return MagicMock()


@pytest.fixture
def actor():
    return SimpleNamespace(id="sa-1")


@pytest.fixture
def registered_client():
    with registered_clients(make_client()):
        yield _user("client@svc.example.com", user_id=CLIENT_USER_ID)


@pytest.mark.anyio
class TestListMemberProjects:
    async def test_unions_projects_across_emails_excluding_service_accounts(self, session):
        regular = _user("r@epam.com")
        service_account = _user("sa@epam.com", user_type="service_account")
        projects = [_app(name="a-proj"), _app(name="b-proj")]
        with (
            _patch("get_session") as get_session,
            _patch("user_repository") as users,
            _patch("application_repository") as apps,
        ):
            get_session.return_value.__enter__.return_value = session
            users.get_by_emails.return_value = {"r@epam.com": regular, "sa@epam.com": service_account}
            apps.list_shared_projects_for_users.return_value = projects

            result = await provisioning_service.list_member_projects(["r@epam.com", "sa@epam.com"])

        assert result == projects
        users.get_by_emails.assert_called_once_with(session, ["r@epam.com", "sa@epam.com"], active_only=True)
        apps.list_shared_projects_for_users.assert_called_once_with(session, [regular.id])

    async def test_registered_client_contributes_no_projects(self, session, registered_client):
        with (
            _patch("get_session") as get_session,
            _patch("user_repository") as users,
            _patch("application_repository") as apps,
        ):
            get_session.return_value.__enter__.return_value = session
            users.get_by_emails.return_value = {registered_client.email: registered_client}

            result = await provisioning_service.list_member_projects([registered_client.email])

        assert result == []
        apps.list_shared_projects_for_users.assert_not_called()

    async def test_no_matching_users_returns_empty_without_project_query(self, session):
        with (
            _patch("get_session") as get_session,
            _patch("user_repository") as users,
            _patch("application_repository") as apps,
        ):
            get_session.return_value.__enter__.return_value = session
            users.get_by_emails.return_value = {}

            result = await provisioning_service.list_member_projects(["ghost@epam.com"])

        assert result == []
        apps.list_shared_projects_for_users.assert_not_called()


class TestFindProject:
    @pytest.mark.parametrize(
        ("project", "code", "message", "help_text"),
        [
            (
                _app(deleted_at=datetime(2026, 1, 1)),
                409,
                "Project 'epm-aimf' has been deleted",
                "Restore the project or use a different CodeMie project name.",
            ),
            (
                _app(project_type="personal"),
                403,
                "Budgets cannot be managed for personal projects",
                "Use a shared project.",
            ),
        ],
    )
    def test_deleted_and_personal_projects_carry_help(self, session, project, code, message, help_text):
        with _patch("application_repository") as apps:
            apps.get_by_name_case_insensitive.return_value = project
            with pytest.raises(ExtendedHTTPException) as exc:
                provisioning_service.find_project(session, "epm-aimf")

        assert (exc.value.code, exc.value.message, exc.value.help) == (code, message, help_text)

    @pytest.mark.parametrize("project", [_app(), None])
    def test_returns_shared_project_or_none(self, session, project):
        with _patch("application_repository") as apps:
            apps.get_by_name_case_insensitive.return_value = project

            assert provisioning_service.find_project(session, "epm-aimf") is project

        apps.get_by_name_case_insensitive.assert_called_once_with(session, "epm-aimf")


class TestCheckProjectPrerequisites:
    @pytest.fixture
    def cost_centers(self):
        with (
            _patch("CostCenterService.ensure_feature_enabled") as feature,
            _patch("cost_center_repository") as repository,
        ):
            repository.get_by_name_case_insensitive.return_value = None
            yield SimpleNamespace(feature=feature, repository=repository)

    def _assert_cost_center_unchecked(self, cost_centers):
        cost_centers.feature.assert_not_called()
        cost_centers.repository.get_by_name_case_insensitive.assert_not_called()

    def test_new_project_with_invalid_name_is_400(self, session, cost_centers):
        with pytest.raises(ExtendedHTTPException) as exc:
            provisioning_service.check_project_prerequisites(session, None, "has space", None)

        assert exc.value.code == 400

    def test_new_project_without_cost_center_checks_nothing_else(self, session, cost_centers):
        provisioning_service.check_project_prerequisites(session, None, "epm-aimf", None)

        self._assert_cost_center_unchecked(cost_centers)

    def test_new_project_with_active_cost_center_passes(self, session, cost_centers):
        cost_centers.repository.get_by_name_case_insensitive.return_value = _cost_center()

        provisioning_service.check_project_prerequisites(session, None, "epm-aimf", "nbs-cmpa")

        cost_centers.feature.assert_called_once_with()
        cost_centers.repository.get_by_name_case_insensitive.assert_called_once_with(session, "nbs-cmpa")

    def test_cost_center_equal_to_project_name_is_still_checked(self, session, cost_centers):
        provisioning_service.check_project_prerequisites(session, None, "nbs-cmpa", "nbs-cmpa")

        cost_centers.feature.assert_called_once_with()
        cost_centers.repository.get_by_name_case_insensitive.assert_called_once_with(session, "nbs-cmpa")

    def test_cost_centers_disabled_is_403(self, session, cost_centers):
        cost_centers.feature.side_effect = ExtendedHTTPException(code=403, message="Feature not available")

        with pytest.raises(ExtendedHTTPException) as exc:
            provisioning_service.check_project_prerequisites(session, None, "epm-aimf", "nbs-cmpa")

        assert (exc.value.code, exc.value.message) == (403, "Feature not available")

    def test_invalid_cost_center_name_is_400(self, session, cost_centers):
        with pytest.raises(ExtendedHTTPException) as exc:
            provisioning_service.check_project_prerequisites(session, None, "epm-aimf", "bad--code")

        assert (exc.value.code, exc.value.message) == (400, "Invalid cost center name")

    def test_deleted_cost_center_is_409_with_help(self, session, cost_centers):
        cost_centers.repository.get_by_name_case_insensitive.return_value = SimpleNamespace(
            name="nbs-cmpa", deleted_at=datetime(2026, 1, 1)
        )

        with pytest.raises(ExtendedHTTPException) as exc:
            provisioning_service.check_project_prerequisites(session, None, "epm-aimf", "nbs-cmpa")

        assert (exc.value.code, exc.value.message, exc.value.help) == (
            409,
            "Cost center 'nbs-cmpa' has been deleted",
            "Restore the cost center or use a different cost center name.",
        )

    def test_unlinked_existing_project_checks_the_named_cost_center(self, session, cost_centers):
        cost_centers.repository.get_by_name_case_insensitive.return_value = SimpleNamespace(
            name="nbs-cmpa", deleted_at=datetime(2026, 1, 1)
        )

        with pytest.raises(ExtendedHTTPException) as exc:
            provisioning_service.check_project_prerequisites(session, _app(), "epm-aimf", "nbs-cmpa")

        assert exc.value.code == 409

    def test_linked_existing_project_ignores_the_named_cost_center(self, session, cost_centers):
        linked_cost_center = _cost_center("aaa-111")
        cost_centers.repository.get_active_by_id.return_value = linked_cost_center

        provisioning_service.check_project_prerequisites(
            session, _app(cost_center_id=linked_cost_center.id), "epm-aimf", "bad--code"
        )

        self._assert_cost_center_unchecked(cost_centers)

    def test_link_to_a_deleted_cost_center_counts_as_none_and_the_named_one_is_checked(self, session, cost_centers):
        cost_centers.repository.get_active_by_id.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc:
            provisioning_service.check_project_prerequisites(
                session, _app(cost_center_id=uuid4()), "epm-aimf", "bad--code"
            )

        assert exc.value.code == 400

    def test_existing_project_does_not_validate_its_name(self, session, cost_centers):
        provisioning_service.check_project_prerequisites(session, _app(name="has space"), "has space", None)

        self._assert_cost_center_unchecked(cost_centers)


class TestResolveProject:
    @pytest.fixture
    def env(self):
        with (
            _patch("application_repository") as apps,
            _patch("cost_center_repository") as cost_center_repository,
            _patch("CostCenterService") as cost_centers,
            _patch("ProjectService") as projects,
        ):
            apps.get_by_name_case_insensitive.return_value = None
            cost_center_repository.get_active_by_id.return_value = None
            yield SimpleNamespace(apps=apps, names=cost_center_repository, cost_centers=cost_centers, projects=projects)

    def _resolve(self, session, actor, project_name="epm-aimf", cost_center="nbs-cmpa"):
        return provisioning_service.resolve_project(session, actor, project_name, cost_center)

    def test_project_is_read_again_in_the_session(self, env, session, actor):
        env.apps.get_by_name_case_insensitive.return_value = _app()

        self._resolve(session, actor, cost_center=None)

        env.apps.get_by_name_case_insensitive.assert_called_once_with(session, "epm-aimf")

    def test_linked_to_a_with_b_named_stays_on_a_and_b_is_not_touched(self, env, session, actor):
        cost_center_a = _cost_center("aaa-111")
        existing = _app(cost_center_id=cost_center_a.id)
        env.apps.get_by_name_case_insensitive.return_value = existing
        env.names.get_active_by_id.return_value = cost_center_a

        result = self._resolve(session, actor, cost_center="bbb-222")

        assert (result.project, result.cost_center) == (existing, "aaa-111")
        assert (result.created_project, result.created_cost_center) == (False, False)
        env.names.get_active_by_id.assert_called_once_with(session, cost_center_a.id)
        env.cost_centers.get_or_create.assert_not_called()
        env.projects.link_cost_center_in_session.assert_not_called()

    def test_link_to_a_deleted_cost_center_counts_as_none_and_the_named_one_is_linked(self, env, session, actor):
        cost_center_b = _cost_center("bbb-222")
        existing = _app(cost_center_id=uuid4())
        env.apps.get_by_name_case_insensitive.return_value = existing
        env.cost_centers.get_or_create.return_value = (cost_center_b, False)
        env.projects.link_cost_center_in_session.return_value = cost_center_b.id
        env.names.get_active_by_id.side_effect = lambda _session, cost_center_id: (
            cost_center_b if cost_center_id == cost_center_b.id else None
        )

        result = self._resolve(session, actor, cost_center="bbb-222")

        assert (result.cost_center, result.created_cost_center) == ("bbb-222", False)
        env.projects.link_cost_center_in_session.assert_called_once_with(
            session, actor=actor, project=existing, cost_center_id=cost_center_b.id
        )

    def test_link_to_a_deleted_cost_center_without_a_named_one_is_reported_as_none(self, env, session, actor):
        env.apps.get_by_name_case_insensitive.return_value = _app(cost_center_id=uuid4())

        result = self._resolve(session, actor, cost_center=None)

        assert result.cost_center is None
        env.projects.link_cost_center_in_session.assert_not_called()

    def test_unlinked_project_without_named_cost_center_stays_unlinked(self, env, session, actor):
        env.apps.get_by_name_case_insensitive.return_value = _app()

        result = self._resolve(session, actor, cost_center=None)

        assert (result.cost_center, result.created_cost_center) == (None, False)
        env.cost_centers.get_or_create.assert_not_called()
        env.projects.link_cost_center_in_session.assert_not_called()
        env.names.get_active_by_id.assert_not_called()

    def test_unlinked_project_is_linked_to_the_named_cost_center(self, env, session, actor):
        cost_center_b = _cost_center("bbb-222")
        existing = _app()
        env.apps.get_by_name_case_insensitive.return_value = existing
        env.cost_centers.get_or_create.return_value = (cost_center_b, True)
        env.projects.link_cost_center_in_session.return_value = cost_center_b.id
        env.names.get_active_by_id.return_value = cost_center_b

        result = self._resolve(session, actor, cost_center="bbb-222")

        assert (result.project, result.cost_center) == (existing, "bbb-222")
        assert (result.created_project, result.created_cost_center) == (False, True)
        env.cost_centers.get_or_create.assert_called_once_with(session, name="bbb-222", created_by="sa-1")
        env.projects.link_cost_center_in_session.assert_called_once_with(
            session, actor=actor, project=existing, cost_center_id=cost_center_b.id
        )
        env.projects.create_shared_project_in_session.assert_not_called()

    def test_existing_cost_center_is_reported_as_not_created(self, env, session, actor):
        cost_center_b = _cost_center("bbb-222")
        env.apps.get_by_name_case_insensitive.return_value = _app()
        env.cost_centers.get_or_create.return_value = (cost_center_b, False)
        env.projects.link_cost_center_in_session.return_value = cost_center_b.id
        env.names.get_active_by_id.return_value = cost_center_b

        result = self._resolve(session, actor, cost_center="bbb-222")

        assert result.created_cost_center is False

    def test_concurrent_link_wins_and_its_cost_center_is_reported(self, env, session, actor):
        concurrent = _cost_center("aaa-111")
        env.apps.get_by_name_case_insensitive.return_value = _app()
        env.cost_centers.get_or_create.return_value = (_cost_center("bbb-222"), True)
        env.projects.link_cost_center_in_session.return_value = concurrent.id
        env.names.get_active_by_id.return_value = concurrent

        result = self._resolve(session, actor, cost_center="bbb-222")

        assert (result.cost_center, result.created_cost_center) == ("aaa-111", False)
        env.names.get_active_by_id.assert_called_once_with(session, concurrent.id)

    def test_missing_project_is_created_with_the_cost_center(self, env, session, actor):
        created = _app()
        cost_center = _cost_center()
        env.cost_centers.get_or_create.return_value = (cost_center, True)
        env.projects.create_shared_project_in_session.return_value = created

        result = self._resolve(session, actor)

        assert (result.project, result.cost_center) == (created, "nbs-cmpa")
        assert (result.created_project, result.created_cost_center) == (True, True)
        env.cost_centers.get_or_create.assert_called_once_with(session, name="nbs-cmpa", created_by="sa-1")
        env.projects.create_shared_project_in_session.assert_called_once_with(
            session, actor=actor, name="epm-aimf", cost_center_id=cost_center.id
        )

    def test_cost_center_equal_to_the_project_name_is_created_and_linked(self, env, session, actor):
        created = _app(name="nbs-cmpa")
        cost_center = _cost_center("nbs-cmpa")
        env.cost_centers.get_or_create.return_value = (cost_center, True)
        env.projects.create_shared_project_in_session.return_value = created

        result = self._resolve(session, actor, project_name="nbs-cmpa", cost_center="nbs-cmpa")

        assert (result.cost_center, result.created_cost_center) == ("nbs-cmpa", True)
        env.cost_centers.get_or_create.assert_called_once_with(session, name="nbs-cmpa", created_by="sa-1")
        env.projects.create_shared_project_in_session.assert_called_once_with(
            session, actor=actor, name="nbs-cmpa", cost_center_id=cost_center.id
        )

    def test_missing_project_without_cost_center_is_created_without_one(self, env, session, actor):
        created = _app()
        env.projects.create_shared_project_in_session.return_value = created

        result = self._resolve(session, actor, cost_center=None)

        assert (result.project, result.cost_center) == (created, None)
        assert (result.created_project, result.created_cost_center) == (True, False)
        env.cost_centers.get_or_create.assert_not_called()
        env.projects.create_shared_project_in_session.assert_called_once_with(
            session, actor=actor, name="epm-aimf", cost_center_id=None
        )

    def test_nothing_is_committed(self, env, session, actor):
        env.cost_centers.get_or_create.return_value = (_cost_center(), True)

        self._resolve(session, actor)

        session.commit.assert_not_called()


class TestAssignAdmins:
    ACTION = "PUT /v1/provisioning/projects/{project_name}/budget"

    @pytest.fixture
    def env(self):
        with (
            _patch("user_repository") as users,
            _patch("user_project_repository") as memberships,
            _patch("ProjectAssignmentService") as assignment,
        ):
            memberships.get_by_users_and_project.return_value = {}
            yield SimpleNamespace(users=users, memberships=memberships, assignment=assignment)

    def _assign(self, session, actor, emails, project=None):
        return provisioning_service.assign_admins(session, actor, project or _app(), emails, self.ACTION)

    def test_unknown_and_service_account_emails_are_not_assigned_and_nobody_is_removed(self, env, session, actor):
        service_account = _user("sa@epam.com", user_type="service_account")
        env.users.get_by_emails.return_value = {"sa@epam.com": service_account}

        result = self._assign(session, actor, ["sa@epam.com", "ghost@epam.com"])

        assert (result.assigned, result.not_assigned, result.changed_user_ids) == (
            [],
            ["sa@epam.com", "ghost@epam.com"],
            [],
        )
        env.assignment.bulk_assign_users_to_project.assert_not_called()

    def test_registered_client_email_is_not_assigned_regardless_of_user_type(
        self, env, session, actor, registered_client
    ):
        env.users.get_by_emails.return_value = {registered_client.email: registered_client}

        result = self._assign(session, actor, [registered_client.email])

        assert (result.assigned, result.not_assigned) == ([], [registered_client.email])
        env.assignment.bulk_assign_users_to_project.assert_not_called()

    def test_existing_admin_is_assigned_but_not_changed(self, env, session, actor):
        admin = _user("a@epam.com")
        env.users.get_by_emails.return_value = {"a@epam.com": admin}
        env.memberships.get_by_users_and_project.return_value = {admin.id: SimpleNamespace(is_project_admin=True)}

        result = self._assign(session, actor, ["A@epam.com"])

        assert (result.assigned, result.not_assigned, result.changed_user_ids) == (["A@epam.com"], [], [])
        env.assignment.bulk_assign_users_to_project.assert_not_called()

    def test_member_is_promoted_and_non_member_added_in_one_bulk_call(self, env, session, actor):
        project = _app(name="EPM-Legacy")
        member = _user("m@epam.com")
        newcomer = _user("n@epam.com")
        env.users.get_by_emails.return_value = {"m@epam.com": member, "n@epam.com": newcomer}
        env.memberships.get_by_users_and_project.return_value = {member.id: SimpleNamespace(is_project_admin=False)}

        result = self._assign(session, actor, ["m@epam.com", "n@epam.com"], project)

        assert (result.assigned, result.not_assigned, result.changed_user_ids) == (
            ["m@epam.com", "n@epam.com"],
            [],
            [member.id, newcomer.id],
        )
        env.assignment.bulk_assign_users_to_project.assert_called_once_with(
            session,
            project,
            [
                {"user_id": member.id, "is_project_admin": True},
                {"user_id": newcomer.id, "is_project_admin": True},
            ],
            "EPM-Legacy",
            actor,
            self.ACTION,
        )

    def test_two_spellings_of_one_email_promote_the_user_once(self, env, session, actor):
        user = _user("m@epam.com")
        env.users.get_by_emails.return_value = {"m@epam.com": user}

        result = self._assign(session, actor, ["M@epam.com", "m@epam.com"])

        assert (result.assigned, result.changed_user_ids) == (["M@epam.com", "m@epam.com"], [user.id])
        assert env.assignment.bulk_assign_users_to_project.call_args.args[2] == [
            {"user_id": user.id, "is_project_admin": True}
        ]

    def test_results_keep_request_order(self, env, session, actor):
        first, second = _user("z@epam.com"), _user("b@epam.com")
        env.users.get_by_emails.return_value = {"z@epam.com": first, "b@epam.com": second}

        result = self._assign(session, actor, ["z@epam.com", "ghost@epam.com", "b@epam.com"])

        assert (result.assigned, result.not_assigned) == (["z@epam.com", "b@epam.com"], ["ghost@epam.com"])
        assert result.changed_user_ids == [first.id, second.id]

    def test_no_emails_makes_no_calls(self, env, session, actor):
        result = self._assign(session, actor, [])

        assert (result.assigned, result.not_assigned, result.changed_user_ids) == ([], [], [])
        env.users.get_by_emails.assert_not_called()
        env.memberships.get_by_users_and_project.assert_not_called()
        env.assignment.bulk_assign_users_to_project.assert_not_called()

    def test_nothing_is_committed(self, env, session, actor):
        env.users.get_by_emails.return_value = {"a@epam.com": _user("a@epam.com")}

        self._assign(session, actor, ["a@epam.com"])

        session.commit.assert_not_called()
