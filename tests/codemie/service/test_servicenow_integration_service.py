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


from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.servicenow_integration import ServiceNowBudgetRequest
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.servicenow_integration_service import (
    ServiceNowBudgetResult,
    ServiceNowIntegrationService as Svc,
    _UpsertPlan,
    build_category_specs,
    merge_amounts,
    replace_amounts,
)

P, C, M = BudgetCategory.PLATFORM, BudgetCategory.CLI, BudgetCategory.PREMIUM_MODELS


class TestBudgetMath:
    def test_replace_amounts_zero_fills(self):
        assert replace_amounts({P: 500.0}) == {P: 500.0, C: 0.0, M: 0.0}

    def test_merge_amounts_overlays_current(self):
        assert merge_amounts({C: 200.0}, {P: 500.0, C: 100.0}) == {P: 500.0, C: 200.0, M: 0.0}

    def test_merge_zero_removes_category(self):
        assert merge_amounts({M: 0.0}, {P: 500.0, M: 50.0})[M] == 0.0

    def test_specs_cover_all_categories_and_sum_to_100(self):
        total, specs = build_category_specs({P: 500.0, C: 100.0, M: 0.0}, {})
        assert total == 600.0
        assert set(specs) == {"platform", "cli", "premium_models"}
        assert specs["premium_models"].pct == 0
        assert abs(sum(s.pct for s in specs.values()) - 100) < 1e-9

    @pytest.mark.parametrize(
        "amounts",
        [
            {P: 10.0, C: 5.0},
            {P: 1.0, C: 1.0, M: 1.0},
            {P: 0.1, C: 0.2, M: 0.3},
            {P: 999_999.99, C: 0.01, M: 333_333.33},
            {P: 500.0, C: 100.0, M: 50.0},
            {P: 7.0, C: 3.0},
            {P: 1.0, C: 2.0, M: 4.0},
        ],
    )
    def test_amounts_derive_back_exactly(self, amounts):
        total, specs = build_category_specs(replace_amounts(amounts), {})

        assert total == float(sum(Decimal(str(amount)) for amount in amounts.values()))
        for category, amount in amounts.items():
            derived = float(Decimal(str(total)) * Decimal(str(specs[category.value].pct)) / Decimal("100"))
            assert derived == amount
        assert abs(sum(spec.pct for spec in specs.values()) - 100) <= 0.5

    def test_platform_zero_is_400(self):
        with pytest.raises(ExtendedHTTPException) as exc:
            build_category_specs({P: 0.0, C: 100.0, M: 0.0}, {})
        assert exc.value.code == 400

    def test_unchanged_amount_keeps_soft_limit(self):
        current = {
            P: SimpleNamespace(max_budget=500.0, soft_budget=250.0),
            C: SimpleNamespace(max_budget=100.0, soft_budget=90.0),
        }
        _, specs = build_category_specs({P: 500.0, C: 200.0, M: 0.0}, current)
        assert specs["platform"].soft_budget == 250.0
        assert specs["cli"].soft_budget is None
        assert specs["premium_models"].soft_budget is None

    def test_amount_differing_by_sub_cent_resets_soft_limit(self):
        current = {P: SimpleNamespace(max_budget=100.004, soft_budget=100.004)}
        _, specs = build_category_specs({P: 100.0, C: 0.0, M: 0.0}, current)
        assert specs["platform"].soft_budget is None

    def test_amount_within_float_drift_keeps_soft_limit(self):
        current = {P: SimpleNamespace(max_budget=500.0 + 1e-10, soft_budget=250.0)}
        _, specs = build_category_specs({P: 500.0, C: 0.0, M: 0.0}, current)
        assert specs["platform"].soft_budget == 250.0

    def test_preserved_soft_limit_never_exceeds_amount(self):
        current = {P: SimpleNamespace(max_budget=100.0 + 5e-7, soft_budget=100.0 + 5e-7)}
        _, specs = build_category_specs({P: 100.0, C: 0.0, M: 0.0}, current)
        assert specs["platform"].soft_budget <= 100.0


MODULE = "codemie.service.servicenow_integration_service"


def _patch(name):
    return patch(f"{MODULE}.{name}")


def _user(email, user_id=None, user_type="regular"):
    return SimpleNamespace(id=user_id or f"id-{email}", email=email, user_type=user_type)


def _app(name="epm-aimf", project_type="shared", deleted_at=None, cost_center_id=None):
    return SimpleNamespace(
        name=name, project_type=project_type, deleted_at=deleted_at, cost_center_id=cost_center_id, id=uuid4()
    )


@pytest.fixture
def actor():
    return SimpleNamespace(id="sa-1")


@pytest.fixture
def session():
    return MagicMock()


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
                Svc._find_project(session, "epm-aimf")

        assert (exc.value.code, exc.value.message, exc.value.help) == (code, message, help_text)

    @pytest.mark.parametrize("project", [_app(), None])
    def test_returns_shared_project_or_none(self, session, project):
        with _patch("application_repository") as apps:
            apps.get_by_name_case_insensitive.return_value = project

            assert Svc._find_project(session, "epm-aimf") is project

        apps.get_by_name_case_insensitive.assert_called_once_with(session, "epm-aimf")


class TestResolveProject:
    def test_checked_project_is_reused_without_lookup(self, session, actor):
        cc_id = uuid4()
        existing = _app(cost_center_id=cc_id)
        with (
            _patch("application_repository") as apps,
            _patch("cost_center_repository") as ccs,
            _patch("cost_center_service") as cc_service,
            _patch("project_service") as projects,
        ):
            ccs.get_active_by_id.return_value = SimpleNamespace(name="other-cc")

            project, cost_center, created_project, created_cc = Svc._resolve_project(
                session, actor, project=existing, project_name="epm-aimf", epam_project_code="nbs-cmpa"
            )

        assert project is existing
        assert (cost_center, created_project, created_cc) == ("other-cc", False, False)
        apps.get_by_name_case_insensitive.assert_not_called()
        ccs.get_active_by_id.assert_called_once_with(session, cc_id)
        cc_service.get_or_create.assert_not_called()
        projects.create_shared_project_in_session.assert_not_called()

    def test_reused_project_reports_only_active_cost_center(self, session, actor):
        with _patch("cost_center_repository") as ccs:
            ccs.get_active_by_id.return_value = None

            _, cost_center, _, _ = Svc._resolve_project(
                session,
                actor,
                project=_app(cost_center_id=uuid4()),
                project_name="epm-aimf",
                epam_project_code="nbs-cmpa",
            )

        assert cost_center is None
        ccs.get_by_id.assert_not_called()

    def test_existing_project_without_cost_center(self, session, actor):
        with _patch("cost_center_repository") as ccs:
            _, cost_center, _, _ = Svc._resolve_project(
                session, actor, project=_app(), project_name="epm-aimf", epam_project_code="nbs-cmpa"
            )

        assert cost_center is None
        ccs.get_active_by_id.assert_not_called()

    def test_project_created_after_check_is_reused(self, session, actor):
        concurrent = _app(name="epm-aimf")
        with (
            _patch("application_repository") as apps,
            _patch("cost_center_service") as cc_service,
            _patch("project_service") as projects,
        ):
            apps.get_by_name_case_insensitive.return_value = concurrent

            result = Svc._resolve_project(
                session, actor, project=None, project_name="epm-aimf", epam_project_code="nbs-cmpa"
            )

        assert result == (concurrent, None, False, False)
        cc_service.get_or_create.assert_not_called()
        projects.create_shared_project_in_session.assert_not_called()

    def test_missing_with_equal_names_creates_without_cost_center(self, session, actor):
        created = _app(name="nbs-cmpa")
        with (
            _patch("application_repository") as apps,
            _patch("cost_center_service") as cc_service,
            _patch("project_service") as projects,
        ):
            apps.get_by_name_case_insensitive.return_value = None
            projects.create_shared_project_in_session.return_value = created

            result = Svc._resolve_project(
                session, actor, project=None, project_name="nbs-cmpa", epam_project_code="nbs-cmpa"
            )

        assert result == (created, None, True, False)
        projects.create_shared_project_in_session.assert_called_once_with(
            session, actor=actor, name="nbs-cmpa", cost_center_id=None
        )
        cc_service.get_or_create.assert_not_called()

    def test_missing_with_different_names_links_cost_center(self, session, actor):
        created = _app(name="epm-aimf")
        cost_center = SimpleNamespace(id=uuid4(), name="nbs-cmpa")
        with (
            _patch("application_repository") as apps,
            _patch("cost_center_service") as cc_service,
            _patch("project_service") as projects,
        ):
            apps.get_by_name_case_insensitive.return_value = None
            cc_service.get_or_create.return_value = (cost_center, True)
            projects.create_shared_project_in_session.return_value = created

            result = Svc._resolve_project(
                session, actor, project=None, project_name="epm-aimf", epam_project_code="nbs-cmpa"
            )

        assert result == (created, "nbs-cmpa", True, True)
        cc_service.get_or_create.assert_called_once_with(session, name="nbs-cmpa", created_by="sa-1")
        projects.create_shared_project_in_session.assert_called_once_with(
            session, actor=actor, name="epm-aimf", cost_center_id=cost_center.id
        )

    def test_existing_cost_center_is_reported_as_not_created(self, session, actor):
        cost_center = SimpleNamespace(id=uuid4(), name="nbs-cmpa")
        with (
            _patch("application_repository") as apps,
            _patch("cost_center_service") as cc_service,
            _patch("project_service") as projects,
        ):
            apps.get_by_name_case_insensitive.return_value = None
            cc_service.get_or_create.return_value = (cost_center, False)
            projects.create_shared_project_in_session.return_value = _app()

            _, _, created_project, created_cc = Svc._resolve_project(
                session, actor, project=None, project_name="epm-aimf", epam_project_code="nbs-cmpa"
            )

        assert (created_project, created_cc) == (True, False)


class TestAssignAdmins:
    def test_unknown_and_service_account_emails_are_pending(self, session, actor):
        service_account = _user("sa@epam.com", user_type="service_account")
        with (
            _patch("user_repository") as users,
            _patch("user_project_repository") as memberships,
            _patch("ProjectAssignmentService") as assignment,
        ):
            users.get_by_emails.return_value = {"sa@epam.com": service_account}
            memberships.get_by_users_and_project.return_value = {}

            assigned, pending, changed = Svc._assign_admins(session, actor, _app(), ["sa@epam.com", "ghost@epam.com"])

        assert (assigned, pending, changed) == ([], ["sa@epam.com", "ghost@epam.com"], [])
        assignment.bulk_assign_users_to_project.assert_not_called()

    def test_existing_admin_is_skipped_and_reported_assigned(self, session, actor):
        admin = _user("a@epam.com")
        with (
            _patch("user_repository") as users,
            _patch("user_project_repository") as memberships,
            _patch("ProjectAssignmentService") as assignment,
        ):
            users.get_by_emails.return_value = {"a@epam.com": admin}
            memberships.get_by_users_and_project.return_value = {admin.id: SimpleNamespace(is_project_admin=True)}

            assigned, pending, changed = Svc._assign_admins(session, actor, _app(), ["A@epam.com"])

        assert assigned == ["A@epam.com"]
        assert (pending, changed) == ([], [])
        assignment.bulk_assign_users_to_project.assert_not_called()

    def test_member_promoted_and_non_member_added_in_one_bulk_call(self, session, actor):
        project = _app(name="EPM-Legacy")
        member = _user("m@epam.com")
        newcomer = _user("n@epam.com")
        with (
            _patch("user_repository") as users,
            _patch("user_project_repository") as memberships,
            _patch("ProjectAssignmentService") as assignment,
        ):
            users.get_by_emails.return_value = {"m@epam.com": member, "n@epam.com": newcomer}
            memberships.get_by_users_and_project.return_value = {member.id: SimpleNamespace(is_project_admin=False)}

            assigned, pending, changed = Svc._assign_admins(session, actor, project, ["m@epam.com", "n@epam.com"])

        assert (assigned, pending, changed) == (["m@epam.com", "n@epam.com"], [], [member.id, newcomer.id])
        assignment.bulk_assign_users_to_project.assert_called_once_with(
            session,
            project,
            [
                {"user_id": member.id, "is_project_admin": True},
                {"user_id": newcomer.id, "is_project_admin": True},
            ],
            "EPM-Legacy",
            actor,
            "POST /v1/integrations/servicenow/budgets",
        )

    def test_no_emails_makes_no_calls(self, session, actor):
        with (
            _patch("user_repository") as users,
            _patch("user_project_repository") as memberships,
            _patch("ProjectAssignmentService") as assignment,
        ):
            result = Svc._assign_admins(session, actor, _app(), [])

        assert result == ([], [], [])
        users.get_by_emails.assert_not_called()
        memberships.get_by_users_and_project.assert_not_called()
        assignment.bulk_assign_users_to_project.assert_not_called()


class TestSyncStep:
    @pytest.fixture
    def db(self, session):
        with _patch("get_session") as get_session:
            get_session.return_value.__enter__.return_value = session
            yield get_session

    def test_commits_once_and_invalidates_changed_users_after_commit(self, db, session, actor):
        manager = MagicMock()
        manager.attach_mock(session.commit, "commit")
        with (
            _patch("invalidate_user_from_cache") as invalidate,
            patch.object(Svc, "_resolve_project", return_value=(_app(), "nbs-cmpa", True, True)),
            patch.object(Svc, "_assign_admins", return_value=(["a@epam.com"], ["b@epam.com"], ["u1", "u2"])),
        ):
            manager.attach_mock(invalidate, "invalidate")
            result = Svc._resolve_and_assign(
                actor, project=None, project_name="epm-aimf", epam_project_code="nbs-cmpa", admin_emails=["a@epam.com"]
            )

        session.commit.assert_called_once()
        assert [c[0] for c in manager.mock_calls] == ["commit", "invalidate", "invalidate"]
        assert [c.args[0] for c in invalidate.call_args_list] == ["u1", "u2"]
        assert result.admins_assigned == ["a@epam.com"]
        assert result.admins_pending == ["b@epam.com"]
        assert (result.created_project, result.created_cost_center, result.cost_center) == (True, True, "nbs-cmpa")

    def test_no_invalidation_when_nothing_changed(self, db, session, actor):
        with (
            _patch("invalidate_user_from_cache") as invalidate,
            patch.object(Svc, "_resolve_project", return_value=(_app(), None, False, False)),
            patch.object(Svc, "_assign_admins", return_value=([], [], [])),
        ):
            Svc._resolve_and_assign(
                actor, project=_app(), project_name="epm-aimf", epam_project_code="epm-aimf", admin_emails=[]
            )

        invalidate.assert_not_called()

    def test_resolution_carries_stored_project_name(self, db, session, actor):
        with (
            _patch("application_repository") as apps,
            _patch("invalidate_user_from_cache"),
            patch.object(Svc, "_assign_admins", return_value=([], [], [])),
        ):
            apps.get_by_name_case_insensitive.return_value = _app(name="EPM-Legacy")
            result = Svc._resolve_and_assign(
                actor, project=None, project_name="epm-legacy", epam_project_code="epm-legacy", admin_emails=[]
            )

        assert result.project_name == "EPM-Legacy"
        assert result.created_project is False

    def test_integrity_error_retries_once_and_reuses_rows(self, db, session, actor):
        with (
            _patch("application_repository") as apps,
            _patch("invalidate_user_from_cache"),
            _patch("project_service") as projects,
            patch.object(Svc, "_assign_admins", return_value=([], [], [])),
        ):
            apps.get_by_name_case_insensitive.side_effect = [None, _app(name="epm-aimf")]
            projects.create_shared_project_in_session.side_effect = IntegrityError("insert", {}, Exception("dup"))

            result = Svc._resolve_and_assign(
                actor, project=None, project_name="epm-aimf", epam_project_code="epm-aimf", admin_emails=[]
            )

        session.rollback.assert_called_once()
        assert db.call_count == 2
        assert result.created_project is False
        assert result.project_name == "epm-aimf"

    def test_second_integrity_error_propagates(self, db, session, actor):
        with (
            _patch("application_repository") as apps,
            _patch("invalidate_user_from_cache") as invalidate,
            _patch("project_service") as projects,
        ):
            apps.get_by_name_case_insensitive.return_value = None
            projects.create_shared_project_in_session.side_effect = IntegrityError("insert", {}, Exception("dup"))

            with pytest.raises(IntegrityError):
                Svc._resolve_and_assign(
                    actor, project=None, project_name="epm-aimf", epam_project_code="epm-aimf", admin_emails=[]
                )

        assert db.call_count == 2
        assert session.rollback.call_count == 2
        invalidate.assert_not_called()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
class TestListProjects:
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

            result = await Svc.list_projects(["r@epam.com", "sa@epam.com"])

        assert result == projects
        users.get_by_emails.assert_called_once_with(session, ["r@epam.com", "sa@epam.com"], active_only=True)
        apps.list_shared_projects_for_users.assert_called_once_with(session, [regular.id])

    async def test_no_matching_users_returns_empty_without_project_query(self, session):
        with (
            _patch("get_session") as get_session,
            _patch("user_repository") as users,
            _patch("application_repository") as apps,
        ):
            get_session.return_value.__enter__.return_value = session
            users.get_by_emails.return_value = {}

            result = await Svc.list_projects(["ghost@epam.com"])

        assert result == []
        apps.list_shared_projects_for_users.assert_not_called()


def _request(action="set", amounts=None, admin_emails=None, project="epm-aimf", name=None, request_id="RITM0012345"):
    return ServiceNowBudgetRequest(
        action=action,
        request_id=request_id,
        epam_project_code=project,
        codemie_project_name=name,
        admin_emails=admin_emails or [],
        amounts=amounts,
    )


def _budget(max_budget, soft_budget, deleted_at=None):
    return SimpleNamespace(max_budget=max_budget, soft_budget=soft_budget, deleted_at=deleted_at)


def _full_group(group_id="g-1", **budgets):
    return SimpleNamespace(
        group=SimpleNamespace(id=group_id, project_name="epm-aimf"),
        categories=[
            SimpleNamespace(assignment=SimpleNamespace(budget_category=category), budget=budget)
            for category, budget in budgets.items()
        ],
    )


def _replace(requested, _current):
    return replace_amounts(requested)


def _plan(group_id=None, project_name="epm-aimf", amounts=None, requested=None, compute_amounts=_replace):
    amounts = amounts or {P: 300.0, C: 0.0, M: 0.0}
    total, specs = build_category_specs(amounts, {})
    return _UpsertPlan(
        project=None,
        project_name=project_name,
        group_id=group_id,
        total=total,
        specs=specs,
        requested=requested or amounts,
        compute_amounts=compute_amounts,
    )


def _result(**overrides):
    values = {
        "project_name": "EPM-Legacy",
        "cost_center": "nbs-cmpa",
        "created_project": True,
        "created_cost_center": True,
        "budget_group": None,
        "admins_assigned": ["a@epam.com"],
        "admins_pending": ["b@epam.com"],
    }
    return ServiceNowBudgetResult(**{**values, **overrides})


CURRENT_GROUP = {"platform": _budget(500.0, 400.0), "cli": _budget(100.0, 80.0), "premium_models": _budget(50.0, 40.0)}


@pytest.fixture
def async_session():
    return AsyncMock()


@pytest.fixture
def budget_env(async_session):
    @asynccontextmanager
    async def fake_session():
        yield async_session

    with (
        patch(f"{MODULE}.get_async_session", fake_session),
        patch(f"{MODULE}.project_budget_service", new_callable=AsyncMock) as service,
        patch(f"{MODULE}.project_budget_group_repository", new_callable=AsyncMock) as repository,
        patch(f"{MODULE}.project_budget_assignment_repository", new_callable=AsyncMock) as assignments,
        patch(f"{MODULE}.budget_repository", new_callable=AsyncMock) as budgets,
    ):
        repository.get_active_by_project.return_value = None
        service.create_project_budget_group.return_value = _full_group("g-new")
        service.get_project_budget_group.return_value = _full_group("g-1", **CURRENT_GROUP)
        assignments.get_active_by_group_id.return_value = [
            SimpleNamespace(budget_id=f"b-{category}", budget_category=category) for category in CURRENT_GROUP
        ]
        budgets.get_by_ids.return_value = {f"b-{category}": budget for category, budget in CURRENT_GROUP.items()}
        yield SimpleNamespace(
            service=service, repository=repository, session=async_session, assignments=assignments, budgets=budgets
        )


def _with_active_group(env):
    env.repository.get_active_by_project.return_value = SimpleNamespace(id="g-1")


@pytest.fixture
def flow(budget_env, session):
    with (
        _patch("get_session") as get_session,
        _patch("application_repository") as apps,
        _patch("cost_center_repository") as ccs,
        _patch("cost_center_service") as cc_service,
        _patch("CostCenterService.ensure_feature_enabled") as cc_feature,
        _patch("project_service") as projects,
        _patch("user_repository") as users,
        _patch("user_project_repository") as memberships,
        _patch("ProjectAssignmentService") as assignment,
        _patch("invalidate_user_from_cache"),
        _patch("logger"),
    ):
        get_session.return_value.__enter__.return_value = session
        apps.get_by_name_case_insensitive.return_value = _app()
        ccs.get_active_by_id.return_value = None
        ccs.get_by_name_case_insensitive.return_value = None
        cc_service.get_or_create.return_value = (SimpleNamespace(id=uuid4(), name="nbs-cmpa"), True)
        projects.create_shared_project_in_session.return_value = _app()
        users.get_by_emails.return_value = {"a@epam.com": _user("a@epam.com")}
        memberships.get_by_users_and_project.return_value = {}
        yield SimpleNamespace(
            **vars(budget_env),
            sync=session,
            apps=apps,
            ccs=ccs,
            cc_feature=cc_feature,
            cc_service=cc_service,
            projects=projects,
            users=users,
            memberships=memberships,
            assignment=assignment,
        )


def _assert_nothing_written(flow):
    flow.sync.commit.assert_not_called()
    flow.projects.create_shared_project_in_session.assert_not_called()
    flow.cc_service.get_or_create.assert_not_called()
    flow.assignment.bulk_assign_users_to_project.assert_not_called()
    flow.service.create_project_budget_group.assert_not_awaited()
    flow.service.update_project_budget_group.assert_not_awaited()
    flow.service.delete_project_budget_group.assert_not_awaited()
    flow.session.commit.assert_not_awaited()


@pytest.mark.anyio
class TestReadBudgets:
    async def test_maps_active_assignments_to_budgets_without_loading_allocations(self, budget_env):
        result = await Svc._read_budgets(budget_env.session, "g-1")

        assert result == {BudgetCategory(category): budget for category, budget in CURRENT_GROUP.items()}
        budget_env.assignments.get_active_by_group_id.assert_awaited_once_with(budget_env.session, "g-1")
        budget_env.service.get_project_budget_group.assert_not_awaited()

    async def test_skips_deleted_and_missing_budgets(self, budget_env):
        budget_env.budgets.get_by_ids.return_value = {
            "b-platform": CURRENT_GROUP["platform"],
            "b-cli": _budget(100.0, 80.0, deleted_at=object()),
        }

        result = await Svc._read_budgets(budget_env.session, "g-1")

        assert result == {P: CURRENT_GROUP["platform"]}


@pytest.mark.anyio
class TestWriteGroup:
    async def test_missing_group_is_created_with_defaults(self, budget_env, actor):
        plan = _plan(amounts={P: 500.0, C: 100.0, M: 0.0})

        await Svc._write_group(actor, plan, "RITM0012345")

        data = budget_env.service.create_project_budget_group.call_args.args[1]
        assert (data.project_name, data.name, data.total_amount, data.budget_duration) == (
            "epm-aimf",
            "ServiceNow RITM0012345",
            600.0,
            "30d",
        )
        assert data.categories == plan.specs
        assert budget_env.service.create_project_budget_group.call_args.kwargs == {"actor_id": "sa-1"}
        budget_env.session.commit.assert_awaited_once()
        budget_env.service.get_project_budget_group.assert_awaited_once_with(budget_env.session, "g-new")

    async def test_existing_group_is_updated_without_name_or_duration(self, budget_env, actor):
        plan = _plan(group_id="g-1")

        await Svc._write_group(actor, plan, "RITM0012345")

        budget_env.service.create_project_budget_group.assert_not_awaited()
        update = budget_env.service.update_project_budget_group
        update.assert_awaited_once()
        data = update.call_args.kwargs["data"]
        assert (update.call_args.kwargs["group_id"], update.call_args.kwargs["actor_id"]) == ("g-1", "sa-1")
        assert data.total_amount == 300.0
        assert {name: spec.pct for name, spec in data.categories.items()} == {
            name: spec.pct for name, spec in plan.specs.items()
        }
        assert (data.name, data.budget_duration, data.description) == (None, None, None)
        budget_env.session.commit.assert_awaited_once()
        budget_env.service.get_project_budget_group.assert_awaited_once_with(budget_env.session, "g-1")

    @pytest.mark.parametrize(
        "conflict",
        [ExtendedHTTPException(code=409, message="exists"), IntegrityError("insert", {}, Exception())],
    )
    async def test_create_conflict_falls_back_to_update(self, conflict, budget_env, actor):
        _with_active_group(budget_env)
        budget_env.service.create_project_budget_group.side_effect = conflict

        await Svc._write_group(actor, _plan(), "RITM0012345")

        budget_env.session.rollback.assert_awaited_once()
        budget_env.repository.get_active_by_project.assert_awaited_once_with(budget_env.session, "epm-aimf")
        budget_env.service.update_project_budget_group.assert_awaited_once()
        assert budget_env.service.update_project_budget_group.call_args.kwargs["group_id"] == "g-1"
        budget_env.session.commit.assert_awaited_once()

    @pytest.mark.parametrize(
        ("compute_amounts", "requested", "expected_amounts", "expected_soft"),
        [
            pytest.param(
                merge_amounts,
                {C: 200.0},
                {"platform": 500.0, "cli": 200.0, "premium_models": 50.0},
                {"platform": 400.0, "cli": None, "premium_models": 40.0},
                id="adjust",
            ),
            pytest.param(
                _replace,
                {P: 500.0, C: 200.0},
                {"platform": 500.0, "cli": 200.0, "premium_models": 0.0},
                {"platform": 400.0, "cli": None, "premium_models": None},
                id="set",
            ),
        ],
    )
    async def test_create_conflict_recomputes_from_winning_group(
        self, budget_env, actor, compute_amounts, requested, expected_amounts, expected_soft
    ):
        _with_active_group(budget_env)
        budget_env.service.create_project_budget_group.side_effect = IntegrityError("insert", {}, Exception())
        plan = _plan(requested=requested, compute_amounts=compute_amounts)

        await Svc._write_group(actor, plan, "RITM0012345")

        budget_env.assignments.get_active_by_group_id.assert_awaited_once_with(budget_env.session, "g-1")
        data = budget_env.service.update_project_budget_group.call_args.kwargs["data"]
        assert data.total_amount == sum(expected_amounts.values())
        derived = {name: data.total_amount * spec.pct / 100 for name, spec in data.categories.items()}
        assert derived == pytest.approx(expected_amounts)
        assert {name: spec.soft_budget for name, spec in data.categories.items()} == expected_soft

    async def test_create_conflict_merge_without_platform_is_400(self, budget_env, actor):
        _with_active_group(budget_env)
        budget_env.service.create_project_budget_group.side_effect = IntegrityError("insert", {}, Exception())

        with pytest.raises(ExtendedHTTPException) as exc:
            await Svc._write_group(actor, _plan(requested={P: 0.0}, compute_amounts=merge_amounts), "RITM0012345")

        assert (exc.value.code, exc.value.message) == (400, "Platform budget must be greater than 0")
        budget_env.service.update_project_budget_group.assert_not_awaited()
        budget_env.session.commit.assert_not_awaited()

    async def test_create_conflict_without_surviving_group_reraises_original(self, budget_env, actor):
        conflict = IntegrityError("insert", {}, Exception("dup"))
        budget_env.service.create_project_budget_group.side_effect = conflict

        with pytest.raises(IntegrityError) as exc:
            await Svc._write_group(actor, _plan(), "RITM0012345")

        assert exc.value is conflict
        budget_env.service.update_project_budget_group.assert_not_awaited()
        budget_env.session.commit.assert_not_awaited()

    async def test_create_non_conflict_http_error_propagates(self, budget_env, actor):
        budget_env.service.create_project_budget_group.side_effect = ExtendedHTTPException(code=400, message="bad")

        with pytest.raises(ExtendedHTTPException) as exc:
            await Svc._write_group(actor, _plan(), "RITM0012345")

        assert exc.value.code == 400
        budget_env.session.rollback.assert_not_awaited()


HANDLERS = ("_handle_set", "_handle_adjust", "_handle_cancel")


@pytest.mark.anyio
class TestHandlers:
    @pytest.mark.parametrize(
        ("action", "amounts", "handler"),
        [
            ("set", {"platform": 1.0}, "_handle_set"),
            ("adjust", {"cli": 1.0}, "_handle_adjust"),
            ("cancel", None, "_handle_cancel"),
        ],
    )
    async def test_dispatch_routes_each_action_to_its_handler(self, actor, action, amounts, handler):
        request = _request(action=action, amounts=amounts)
        handlers = {name: AsyncMock(return_value=_result()) for name in HANDLERS}
        with patch.multiple(Svc, **handlers), _patch("logger"):
            result = await Svc.apply_budget_request(actor, request)

        for name, mock in handlers.items():
            assert mock.await_args_list == ([call(actor, request)] if name == handler else [])
        assert result is handlers[handler].return_value

    async def test_cancel_missing_project_is_404_and_writes_nothing(self, flow, actor):
        with (
            patch.object(Svc, "_find_project", return_value=None),
            patch.object(Svc, "_resolve_and_assign") as resolve,
        ):
            with pytest.raises(ExtendedHTTPException) as exc:
                await Svc.apply_budget_request(actor, _request(action="cancel", project="project-999"))

        assert exc.value.code == 404
        assert exc.value.message == "Project 'project-999' does not exist"
        assert exc.value.help == "Check the CodeMie project name; there is no budget to cancel."
        resolve.assert_not_called()
        _assert_nothing_written(flow)

    async def test_cancel_never_assigns_admins(self, flow, actor):
        _with_active_group(flow)
        request = _request(action="cancel", amounts={"cli": 1.0}, admin_emails=["a@epam.com"])
        with patch.object(Svc, "_assign_admins") as assign:
            result = await Svc.apply_budget_request(actor, request)

        flow.service.delete_project_budget_group.assert_awaited_once_with(flow.session, group_id="g-1", actor_id="sa-1")
        flow.session.commit.assert_awaited_once()
        assign.assert_not_called()
        flow.assignment.bulk_assign_users_to_project.assert_not_called()
        flow.sync.commit.assert_not_called()
        assert (result.budget_group, result.admins_assigned, result.admins_pending) == (None, [], [])
        assert (result.created_project, result.created_cost_center) == (False, False)

    async def test_cancel_existing_project_without_group_is_noop(self, flow, actor):
        result = await Svc.apply_budget_request(actor, _request(action="cancel"))

        assert result == ServiceNowBudgetResult(
            project_name="epm-aimf",
            cost_center=None,
            created_project=False,
            created_cost_center=False,
            budget_group=None,
            admins_assigned=[],
            admins_pending=[],
        )
        _assert_nothing_written(flow)

    async def test_cancel_reports_active_cost_center(self, flow, actor):
        cc_id = uuid4()
        flow.apps.get_by_name_case_insensitive.return_value = _app(cost_center_id=cc_id)
        with _patch("cost_center_repository") as ccs:
            ccs.get_active_by_id.return_value = SimpleNamespace(name="nbs-cmpa")
            result = await Svc.apply_budget_request(actor, _request(action="cancel"))

        assert result.cost_center == "nbs-cmpa"
        ccs.get_active_by_id.assert_called_once_with(flow.sync, cc_id)

    async def test_adjust_missing_project_creates_project_and_group_from_request(self, flow, actor):
        flow.apps.get_by_name_case_insensitive.return_value = None
        request = _request(action="adjust", amounts={"platform": 300.0, "cli": 100.0})

        result = await Svc.apply_budget_request(actor, request)

        flow.projects.create_shared_project_in_session.assert_called_once()
        flow.service.get_project_budget_group.assert_awaited_once_with(flow.session, "g-new")
        data = flow.service.create_project_budget_group.call_args.args[1]
        assert data.total_amount == 400.0
        assert {name: spec.pct for name, spec in data.categories.items()} == {
            "platform": 75.0,
            "cli": 25.0,
            "premium_models": 0.0,
        }
        assert result.created_project is True

    async def test_adjust_merge_dropping_platform_is_400_and_writes_nothing(self, flow, actor):
        _with_active_group(flow)
        request = _request(action="adjust", amounts={"platform": 0.0}, admin_emails=["a@epam.com"])

        with pytest.raises(ExtendedHTTPException) as exc:
            await Svc.apply_budget_request(actor, request)

        assert exc.value.code == 400
        assert exc.value.message == "Platform budget must be greater than 0"
        _assert_nothing_written(flow)

    async def test_adjust_existing_project_without_group_needs_platform(self, flow, actor):
        request = _request(action="adjust", amounts={"cli": 100.0}, admin_emails=["a@epam.com"])

        with pytest.raises(ExtendedHTTPException) as exc:
            await Svc.apply_budget_request(actor, request)

        assert (exc.value.code, exc.value.message) == (400, "Platform budget must be greater than 0")
        _assert_nothing_written(flow)

    @pytest.mark.parametrize("action", ["set", "adjust"])
    async def test_missing_project_skips_group_read_and_cost_center_checks(self, flow, actor, action):
        flow.apps.get_by_name_case_insensitive.return_value = None

        await Svc.apply_budget_request(actor, _request(action=action, amounts={"platform": 300.0}))

        flow.repository.get_active_by_project.assert_not_awaited()
        flow.service.get_project_budget_group.assert_awaited_once_with(flow.session, "g-new")
        flow.cc_feature.assert_not_called()
        flow.ccs.get_by_name_case_insensitive.assert_not_called()

    @pytest.mark.parametrize("action", ["set", "adjust"])
    async def test_soft_deleted_cost_center_on_missing_project_is_409_and_writes_nothing(self, flow, actor, action):
        flow.apps.get_by_name_case_insensitive.return_value = None
        flow.ccs.get_by_name_case_insensitive.return_value = SimpleNamespace(
            name="nbs-cmpa", deleted_at=datetime(2026, 1, 1)
        )
        request = _request(
            action=action, amounts={"platform": 1.0}, admin_emails=["a@epam.com"], project="nbs-cmpa", name="epm-aimf"
        )

        with pytest.raises(ExtendedHTTPException) as exc:
            await Svc.apply_budget_request(actor, request)

        assert (exc.value.code, exc.value.message, exc.value.help) == (
            409,
            "Cost center 'nbs-cmpa' has been deleted",
            "Restore the cost center or use a different EPAM project code.",
        )
        flow.ccs.get_by_name_case_insensitive.assert_called_once_with(flow.sync, "nbs-cmpa")
        _assert_nothing_written(flow)

    @pytest.mark.parametrize("action", ["set", "adjust"])
    async def test_cost_centers_disabled_on_missing_project_is_403_and_writes_nothing(self, flow, actor, action):
        flow.apps.get_by_name_case_insensitive.return_value = None
        flow.cc_feature.side_effect = ExtendedHTTPException(code=403, message="Feature not available")
        request = _request(
            action=action, amounts={"platform": 1.0}, admin_emails=["a@epam.com"], project="nbs-cmpa", name="epm-aimf"
        )

        with pytest.raises(ExtendedHTTPException) as exc:
            await Svc.apply_budget_request(actor, request)

        assert (exc.value.code, exc.value.message) == (403, "Feature not available")
        _assert_nothing_written(flow)

    async def test_active_cost_center_on_missing_project_passes_check(self, flow, actor):
        flow.apps.get_by_name_case_insensitive.return_value = None
        flow.ccs.get_by_name_case_insensitive.return_value = SimpleNamespace(name="nbs-cmpa", deleted_at=None)
        request = _request(amounts={"platform": 1.0}, project="nbs-cmpa", name="epm-aimf")

        result = await Svc.apply_budget_request(actor, request)

        flow.cc_feature.assert_called_once_with()
        flow.cc_service.get_or_create.assert_called_once_with(flow.sync, name="nbs-cmpa", created_by="sa-1")
        assert result.cost_center == "nbs-cmpa"

    async def test_invalid_project_name_on_missing_project_is_400_and_writes_nothing(self, flow, actor):
        flow.apps.get_by_name_case_insensitive.return_value = None
        request = _request(amounts={"platform": 1.0}, admin_emails=["a@epam.com"], project="has space")

        with pytest.raises(ExtendedHTTPException) as exc:
            await Svc.apply_budget_request(actor, request)

        assert exc.value.code == 400
        _assert_nothing_written(flow)

    async def test_invalid_cost_center_code_on_missing_project_is_400_and_writes_nothing(self, flow, actor):
        flow.apps.get_by_name_case_insensitive.return_value = None
        request = _request(
            action="adjust", amounts={"cli": 1.0}, admin_emails=["a@epam.com"], project="bad--code", name="epm-aimf"
        )

        with pytest.raises(ExtendedHTTPException) as exc:
            await Svc.apply_budget_request(actor, request)

        assert (exc.value.code, exc.value.message) == (400, "Invalid cost center name")
        _assert_nothing_written(flow)

    @pytest.mark.parametrize("action", ["set", "adjust", "cancel"])
    async def test_existing_project_ignores_invalid_cost_center_code(self, flow, actor, action):
        amounts = None if action == "cancel" else {"platform": 1.0}
        request = _request(action=action, amounts=amounts, project="bad--code", name="epm-aimf")
        with patch(f"{MODULE}.CostCenterService") as cost_centers, patch(f"{MODULE}.ProjectService") as projects:
            result = await Svc.apply_budget_request(actor, request)

        assert result.project_name == "epm-aimf"
        cost_centers.validate_name.assert_not_called()
        projects.validate_shared_project_name.assert_not_called()
        flow.cc_service.get_or_create.assert_not_called()

    @pytest.mark.parametrize(("action", "expected_total"), [("set", 500.0), ("adjust", 550.0)])
    async def test_set_replaces_and_adjust_merges_on_existing_group(self, flow, actor, action, expected_total):
        _with_active_group(flow)
        request = _request(action=action, amounts={"platform": 300.0, "cli": 200.0})

        await Svc.apply_budget_request(actor, request)

        flow.service.create_project_budget_group.assert_not_awaited()
        update = flow.service.update_project_budget_group
        update.assert_awaited_once()
        assert update.call_args.kwargs["group_id"] == "g-1"
        data = update.call_args.kwargs["data"]
        assert data.total_amount == expected_total
        derived = {name: data.total_amount * spec.pct / 100 for name, spec in data.categories.items()}
        expected_premium = 50.0 if action == "adjust" else 0.0
        assert derived == pytest.approx({"platform": 300.0, "cli": 200.0, "premium_models": expected_premium})

    async def test_adjust_resending_current_amounts_keeps_all_soft_limits(self, flow, actor):
        _with_active_group(flow)
        request = _request(action="adjust", amounts={"platform": 500.0, "cli": 100.0, "premium_models": 50.0})

        await Svc.apply_budget_request(actor, request)

        data = flow.service.update_project_budget_group.call_args.kwargs["data"]
        assert {name: spec.soft_budget for name, spec in data.categories.items()} == {
            name: budget.soft_budget for name, budget in CURRENT_GROUP.items()
        }

    @pytest.mark.parametrize(
        ("action", "expected_amounts", "expected_soft"),
        [
            (
                "set",
                {"platform": 300.0, "cli": 100.0, "premium_models": 0.0},
                {"platform": None, "cli": 80.0, "premium_models": None},
            ),
            (
                "adjust",
                {"platform": 300.0, "cli": 100.0, "premium_models": 50.0},
                {"platform": None, "cli": 80.0, "premium_models": 40.0},
            ),
        ],
    )
    async def test_group_created_concurrently_between_check_and_write_converges(
        self, flow, actor, action, expected_amounts, expected_soft
    ):
        flow.repository.get_active_by_project.side_effect = [None, SimpleNamespace(id="g-1")]
        flow.service.create_project_budget_group.side_effect = IntegrityError("insert", {}, Exception("dup"))
        request = _request(action=action, amounts={"platform": 300.0, "cli": 100.0})

        await Svc.apply_budget_request(actor, request)

        flow.session.rollback.assert_awaited_once()
        assert flow.repository.get_active_by_project.await_count == 2
        update = flow.service.update_project_budget_group
        update.assert_awaited_once()
        data = update.call_args.kwargs["data"]
        assert update.call_args.kwargs["group_id"] == "g-1"
        assert data.total_amount == sum(expected_amounts.values())
        derived = {name: data.total_amount * spec.pct / 100 for name, spec in data.categories.items()}
        assert derived == pytest.approx(expected_amounts)
        assert {name: spec.soft_budget for name, spec in data.categories.items()} == expected_soft
        flow.session.commit.assert_awaited_once()

    @pytest.mark.parametrize(
        ("action", "amounts"), [("set", {"platform": 1.0}), ("adjust", {"platform": 1.0}), ("cancel", None)]
    )
    async def test_budget_calls_use_stored_project_name(self, flow, actor, action, amounts):
        flow.apps.get_by_name_case_insensitive.return_value = _app(name="EPM-Legacy")

        result = await Svc.apply_budget_request(actor, _request(action=action, amounts=amounts, project="epm-legacy"))

        assert result.project_name == "EPM-Legacy"
        assert {c.args[1] for c in flow.repository.get_active_by_project.await_args_list} == {"EPM-Legacy"}
        if action != "cancel":
            assert flow.service.create_project_budget_group.call_args.args[1].project_name == "EPM-Legacy"

    async def test_upsert_returns_combined_result_with_stored_name(self, actor):
        group = _full_group()
        plan = _plan(project_name="epm-aimf")
        request = _request(action="set", amounts={"platform": 1.0}, admin_emails=["a@epam.com", "b@epam.com"])
        with (
            patch.object(Svc, "_check_upsert", new=AsyncMock(return_value=plan)),
            patch.object(Svc, "_resolve_and_assign", return_value=_result()) as resolve,
            patch.object(Svc, "_write_group", new=AsyncMock(return_value=group)) as write_group,
        ):
            result = await Svc._handle_set(actor, request)

        resolve.assert_called_once_with(
            actor,
            project=None,
            project_name="epm-aimf",
            epam_project_code="epm-aimf",
            admin_emails=["a@epam.com", "b@epam.com"],
        )
        written_plan = write_group.await_args.args[1]
        assert written_plan.project_name == "EPM-Legacy"
        assert written_plan.specs == plan.specs
        assert result == _result(budget_group=group)

    async def test_admin_assignment_is_committed_after_check_and_before_budget_write(self, session, actor):
        manager = MagicMock()
        request = _request(action="set", amounts={"platform": 1.0}, admin_emails=["a@epam.com"])
        with (
            _patch("get_session") as get_session,
            _patch("invalidate_user_from_cache"),
            _patch("logger"),
            patch.object(Svc, "_check_upsert", new=AsyncMock(return_value=_plan())) as check,
            patch.object(Svc, "_resolve_project", return_value=(_app(), None, True, False)),
            patch.object(Svc, "_assign_admins", return_value=(["a@epam.com"], [], ["u1"])) as assign,
            patch.object(Svc, "_write_group", new=AsyncMock(return_value=None)) as write_group,
        ):
            get_session.return_value.__enter__.return_value = session
            manager.attach_mock(check, "check")
            manager.attach_mock(assign, "assign_admins")
            manager.attach_mock(session.commit, "commit")
            manager.attach_mock(write_group, "write_group")

            await Svc.apply_budget_request(actor, request)

        assert [c[0] for c in manager.mock_calls] == ["check", "assign_admins", "commit", "write_group"]

    async def test_retry_after_partial_failure_reuses_project_and_applies_budget(self, flow, actor):
        project = _app(name="epm-aimf")
        admin = _user("a@epam.com")
        request = _request(action="set", amounts={"platform": 300.0}, admin_emails=["a@epam.com"])
        flow.service.create_project_budget_group.side_effect = [RuntimeError("provider down")]
        flow.apps.get_by_name_case_insensitive.side_effect = [None, None, project]
        flow.projects.create_shared_project_in_session.return_value = project
        flow.users.get_by_emails.return_value = {"a@epam.com": admin}
        flow.memberships.get_by_users_and_project.side_effect = [
            {},
            {admin.id: SimpleNamespace(is_project_admin=True)},
        ]

        with pytest.raises(RuntimeError):
            await Svc.apply_budget_request(actor, request)
        flow.assignment.bulk_assign_users_to_project.assert_called_once()
        flow.assignment.bulk_assign_users_to_project.reset_mock()
        flow.service.create_project_budget_group.reset_mock(side_effect=True)

        result = await Svc.apply_budget_request(actor, request)

        assert result.created_project is False
        assert result.admins_assigned == ["a@epam.com"]
        flow.assignment.bulk_assign_users_to_project.assert_not_called()
        flow.service.create_project_budget_group.assert_awaited_once()


@pytest.mark.anyio
class TestApplyBudgetRequestLogging:
    async def test_logs_started_and_completed(self, actor):
        request = _request(action="set", amounts={"platform": 1.0}, admin_emails=["a@epam.com", "b@epam.com"])
        with (
            patch.object(Svc, "_handle_set", new=AsyncMock(return_value=_result())),
            _patch("logger") as log,
        ):
            await Svc.apply_budget_request(actor, request)

        started, completed = (call.args[0] for call in log.info.call_args_list)
        assert started == (
            "servicenow_budget_request_started: request_id='RITM0012345', action=set, project='epm-aimf', "
            "epam_project_code='epm-aimf', admin_emails=2, actor_user_id=sa-1, domain=servicenow_integration"
        )
        assert completed == (
            "servicenow_budget_request_completed: request_id='RITM0012345', action=set, project='EPM-Legacy', "
            "cost_center='nbs-cmpa', created_project=True, created_cost_center=True, admins_assigned=1, "
            "admins_pending=1, actor_user_id=sa-1, domain=servicenow_integration"
        )

    async def test_handler_failure_is_logged_and_reraised(self, actor):
        request = _request(action="set", amounts={"platform": 1.0})
        boom = RuntimeError("provider down")
        with (
            patch.object(Svc, "_handle_set", new=AsyncMock(side_effect=boom)),
            _patch("logger") as log,
        ):
            with pytest.raises(RuntimeError) as exc:
                await Svc.apply_budget_request(actor, request)

        assert exc.value is boom
        log.error.assert_called_once_with(
            "servicenow_budget_request_failed: request_id='RITM0012345', action=set, "
            "error_type=RuntimeError, error='provider down', domain=servicenow_integration"
        )

    async def test_http_failure_log_carries_status_and_message(self, actor):
        with (
            patch.object(
                Svc, "_handle_cancel", new=AsyncMock(side_effect=ExtendedHTTPException(code=409, message="gone"))
            ),
            _patch("logger") as log,
        ):
            with pytest.raises(ExtendedHTTPException):
                await Svc.apply_budget_request(actor, _request(action="cancel"))

        log.error.assert_called_once_with(
            "servicenow_budget_request_failed: request_id='RITM0012345', action=cancel, "
            "status=409, message='gone', domain=servicenow_integration"
        )
