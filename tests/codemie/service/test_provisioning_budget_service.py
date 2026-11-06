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

from codemie.configs.provisioning_clients_config import CLIENT_NAME_MAX_LENGTH, ProvisioningPermission
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.project_budget import ProjectBudgetGroupUpdateRequest
from codemie.rest_api.models.provisioning import ProvisioningBudgetRequest, ProvisioningBudgetSetRequest
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.budget.project_budget_service import ProjectBudgetService
from codemie.service.provisioning_budget_service import (
    ProvisioningBudgetResult,
    ProvisioningBudgetService as Svc,
    build_category_specs,
    merge_amounts,
    replace_amounts,
)
from tests.codemie.configs.provisioning_clients_support import make_client

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
            assert ProjectBudgetService.category_amount(total, specs[category.value].pct) == amount
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


MODULE = "codemie.service.provisioning_budget_service"
PROJECT_STEPS_MODULE = "codemie.service.provisioning_service"
REFERENCE = "RITM1"
SET_ROUTE = "PUT /v1/provisioning/projects/{project_name}/budget"
ADJUST_ROUTE = "PATCH /v1/provisioning/projects/{project_name}/budget"
PARTLY_APPLIED = "partly applied; send the same request again"
FULL_ACCESS = frozenset({ProvisioningPermission.MANAGE_BUDGETS, ProvisioningPermission.MANAGE_PROJECTS})
BUDGET_ONLY = frozenset({ProvisioningPermission.MANAGE_BUDGETS})
MANAGE_PROJECTS_DENIED = "This provisioning client does not hold the permission provisioning:manage-projects."


def _patch(name):
    return patch(f"{MODULE}.{name}")


def _patch_project_steps(name):
    return patch(f"{PROJECT_STEPS_MODULE}.{name}")


def _user(email, user_id=None, user_type="regular"):
    return SimpleNamespace(id=user_id or f"id-{email}", email=email, user_type=user_type)


def _app(name="epm-aimf", project_type="shared", deleted_at=None, cost_center_id=None):
    return SimpleNamespace(
        name=name, project_type=project_type, deleted_at=deleted_at, cost_center_id=cost_center_id, id=uuid4()
    )


def _client(permissions=FULL_ACCESS):
    return make_client(name="Portal", permissions=permissions)


async def _set(actor, amounts, project=None, client=None, project_name="epm-aimf"):
    request = ProvisioningBudgetSetRequest(amounts=amounts, project=project)
    return await Svc.set_budget(actor, client or _client(), REFERENCE, project_name, request)


async def _adjust(actor, amounts, project=None, client=None, project_name="epm-aimf"):
    request = ProvisioningBudgetRequest(amounts=amounts, project=project)
    return await Svc.adjust_budget(actor, client or _client(), REFERENCE, project_name, request)


async def _cancel(actor, client=None, project_name="epm-aimf"):
    return await Svc.cancel_budget(actor, client or _client(), REFERENCE, project_name)


WRITES = {"set": _set, "adjust": _adjust}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def actor():
    return SimpleNamespace(id="sa-1")


@pytest.fixture
def session():
    return MagicMock()


def _budget(max_budget, soft_budget, deleted_at=None, sync_status="ok"):
    return SimpleNamespace(
        max_budget=max_budget,
        soft_budget=soft_budget,
        deleted_at=deleted_at,
        provider_metadata={"sync_status": sync_status},
    )


def _full_group(group_id="g-1", members=(), **budgets):
    return SimpleNamespace(
        group=SimpleNamespace(id=group_id, project_name="epm-aimf"),
        categories=[
            SimpleNamespace(
                assignment=SimpleNamespace(budget_category=category), budget=budget, allocations=list(members)
            )
            for category, budget in budgets.items()
        ],
    )


def _member(sync_status):
    return SimpleNamespace(sync_status=sync_status)


def _active_provider(name):
    return patch(f"{MODULE}.get_active_provider", return_value=SimpleNamespace(provider_name=name))


def _budgets_by_id(budgets):
    return {f"b-{category}": budget for category, budget in budgets.items()}


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
        budgets.get_by_ids.return_value = _budgets_by_id(CURRENT_GROUP)
        yield SimpleNamespace(
            service=service, repository=repository, session=async_session, assignments=assignments, budgets=budgets
        )


def _with_active_group(env):
    env.repository.get_active_by_project.return_value = SimpleNamespace(id="g-1")


@pytest.fixture
def flow(budget_env, session):
    cost_center = SimpleNamespace(id=uuid4(), name="nbs-cmpa")
    with (
        _patch("get_session") as get_session,
        _patch("invalidate_user_from_cache") as invalidate,
        _patch("logger") as log,
        _patch_project_steps("application_repository") as apps,
        _patch_project_steps("cost_center_repository") as ccs,
        _patch_project_steps("CostCenterService.get_or_create") as get_or_create_cost_center,
        _patch_project_steps("CostCenterService.ensure_feature_enabled") as cost_center_feature,
        _patch_project_steps("ProjectService.create_shared_project_in_session") as create_project,
        _patch_project_steps("ProjectService.link_cost_center_in_session") as link_cost_center,
        _patch_project_steps("user_repository") as users,
        _patch_project_steps("user_project_repository") as memberships,
        _patch_project_steps("ProjectAssignmentService") as assignment,
    ):
        get_session.return_value.__enter__.return_value = session
        apps.get_by_name_case_insensitive.return_value = _app()
        ccs.get_active_by_id.return_value = None
        ccs.get_by_name_case_insensitive.return_value = None
        get_or_create_cost_center.return_value = (cost_center, True)
        create_project.return_value = _app()
        link_cost_center.return_value = cost_center.id
        users.get_by_emails.return_value = {"a@epam.com": _user("a@epam.com")}
        memberships.get_by_users_and_project.return_value = {}
        yield SimpleNamespace(
            **vars(budget_env),
            sync=session,
            get_session=get_session,
            invalidate=invalidate,
            log=log,
            apps=apps,
            ccs=ccs,
            cost_center=cost_center,
            get_or_create_cost_center=get_or_create_cost_center,
            cost_center_feature=cost_center_feature,
            create_project=create_project,
            link_cost_center=link_cost_center,
            users=users,
            memberships=memberships,
            assignment=assignment,
        )


def _assert_nothing_written(flow):
    flow.sync.commit.assert_not_called()
    flow.create_project.assert_not_called()
    flow.get_or_create_cost_center.assert_not_called()
    flow.link_cost_center.assert_not_called()
    flow.assignment.bulk_assign_users_to_project.assert_not_called()
    flow.service.create_project_budget_group.assert_not_awaited()
    flow.service.update_project_budget_group.assert_not_awaited()
    flow.service.delete_project_budget_group.assert_not_awaited()
    flow.session.commit.assert_not_awaited()


def _derived_amounts(data):
    return {name: data.total_amount * spec.pct / 100 for name, spec in data.categories.items()}


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


DELETED_OR_PERSONAL_PROJECT = [
    (_app(deleted_at=datetime(2026, 1, 1)), 409, "Project 'epm-aimf' has been deleted"),
    (_app(project_type="personal"), 403, "Budgets cannot be managed for personal projects"),
]


@pytest.mark.anyio
class TestRejectionsWriteNothing:
    @pytest.mark.parametrize("operation", ["set", "adjust"])
    @pytest.mark.parametrize(("project", "code", "message"), DELETED_OR_PERSONAL_PROJECT)
    async def test_deleted_or_personal_project(self, flow, actor, operation, project, code, message):
        flow.apps.get_by_name_case_insensitive.return_value = project

        with pytest.raises(ExtendedHTTPException) as exc:
            await WRITES[operation](actor, {P: 1.0}, project={"admin_emails": ["a@epam.com"]})

        assert (exc.value.code, exc.value.message) == (code, message)
        _assert_nothing_written(flow)

    @pytest.mark.parametrize(("project", "code", "message"), DELETED_OR_PERSONAL_PROJECT)
    async def test_cancel_on_deleted_or_personal_project(self, flow, actor, project, code, message):
        flow.apps.get_by_name_case_insensitive.return_value = project
        _with_active_group(flow)

        with pytest.raises(ExtendedHTTPException) as exc:
            await _cancel(actor)

        assert (exc.value.code, exc.value.message) == (code, message)
        _assert_nothing_written(flow)

    async def test_deleted_project_wins_over_missing_manage_projects(self, flow, actor):
        flow.apps.get_by_name_case_insensitive.return_value = _app(deleted_at=datetime(2026, 1, 1))

        with pytest.raises(ExtendedHTTPException) as exc:
            await _set(actor, {P: 1.0}, project={"cost_center": "nbs-cmpa"}, client=_client(BUDGET_ONLY))

        assert exc.value.code == 409
        _assert_nothing_written(flow)

    @pytest.mark.parametrize("operation", ["set", "adjust"])
    @pytest.mark.parametrize(
        ("existing", "project"),
        [
            pytest.param(None, None, id="missing-project"),
            pytest.param(_app(), {"cost_center": "nbs-cmpa"}, id="names-cost-center"),
            pytest.param(_app(), {"admin_emails": ["a@epam.com"]}, id="names-admin"),
        ],
    )
    async def test_budget_only_client_needs_manage_projects(self, flow, actor, operation, existing, project):
        flow.apps.get_by_name_case_insensitive.return_value = existing

        with pytest.raises(ExtendedHTTPException) as exc:
            await WRITES[operation](actor, {P: 1.0}, project=project, client=_client(BUDGET_ONLY))

        assert (exc.value.code, exc.value.details) == (403, MANAGE_PROJECTS_DENIED)
        _assert_nothing_written(flow)

    async def test_invalid_project_name_on_missing_project(self, flow, actor):
        flow.apps.get_by_name_case_insensitive.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc:
            await _set(actor, {P: 1.0}, project={"admin_emails": ["a@epam.com"]}, project_name="has space")

        assert exc.value.code == 400
        _assert_nothing_written(flow)

    @pytest.mark.parametrize("operation", ["set", "adjust"])
    async def test_invalid_cost_center_name(self, flow, actor, operation):
        flow.apps.get_by_name_case_insensitive.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc:
            await WRITES[operation](actor, {P: 1.0}, project={"cost_center": "bad--code"})

        assert (exc.value.code, exc.value.message) == (400, "Invalid cost center name")
        _assert_nothing_written(flow)

    @pytest.mark.parametrize("operation", ["set", "adjust"])
    async def test_cost_centers_disabled(self, flow, actor, operation):
        flow.apps.get_by_name_case_insensitive.return_value = None
        flow.cost_center_feature.side_effect = ExtendedHTTPException(code=403, message="Feature not available")

        with pytest.raises(ExtendedHTTPException) as exc:
            await WRITES[operation](actor, {P: 1.0}, project={"cost_center": "nbs-cmpa"})

        assert (exc.value.code, exc.value.message) == (403, "Feature not available")
        _assert_nothing_written(flow)

    async def test_deleted_cost_center(self, flow, actor):
        flow.apps.get_by_name_case_insensitive.return_value = None
        flow.ccs.get_by_name_case_insensitive.return_value = SimpleNamespace(
            name="nbs-cmpa", deleted_at=datetime(2026, 1, 1)
        )

        with pytest.raises(ExtendedHTTPException) as exc:
            await _set(actor, {P: 1.0}, project={"cost_center": "nbs-cmpa", "admin_emails": ["a@epam.com"]})

        assert (exc.value.code, exc.value.message) == (409, "Cost center 'nbs-cmpa' has been deleted")
        _assert_nothing_written(flow)

    @pytest.mark.parametrize("operation", ["set", "adjust"])
    async def test_broken_group_rule(self, flow, actor, operation):
        _with_active_group(flow)
        flow.service.check_group_request.side_effect = ExtendedHTTPException(code=400, message="below fixed limits")

        with pytest.raises(ExtendedHTTPException) as exc:
            await WRITES[operation](actor, {P: 300.0}, project={"admin_emails": ["a@epam.com"]})

        assert (exc.value.code, exc.value.message) == (400, "below fixed limits")
        _assert_nothing_written(flow)

    async def test_merge_leaving_platform_at_zero(self, flow, actor):
        _with_active_group(flow)

        with pytest.raises(ExtendedHTTPException) as exc:
            await _adjust(actor, {P: 0.0}, project={"admin_emails": ["a@epam.com"]})

        assert (exc.value.code, exc.value.message) == (400, "Platform budget must be greater than 0")
        _assert_nothing_written(flow)

    async def test_adjust_without_budget_needs_platform(self, flow, actor):
        with pytest.raises(ExtendedHTTPException) as exc:
            await _adjust(actor, {C: 100.0}, project={"admin_emails": ["a@epam.com"]})

        assert (exc.value.code, exc.value.message) == (400, "Platform budget must be greater than 0")
        _assert_nothing_written(flow)

    async def test_group_request_that_cannot_be_built(self, flow, actor):
        _with_active_group(flow)
        flow.budgets.get_by_ids.return_value = _budgets_by_id({**CURRENT_GROUP, "platform": _budget(500.0, -1.0)})

        with pytest.raises(ExtendedHTTPException) as exc:
            await _adjust(actor, {C: 200.0})

        assert (exc.value.code, exc.value.message) == (400, "Invalid budget request")
        _assert_nothing_written(flow)

    async def test_check_runs_against_the_exact_group_request(self, flow, actor):
        _with_active_group(flow)

        await _adjust(actor, {C: 200.0})

        checked = flow.service.check_group_request.await_args
        assert checked.args[:2] == (flow.session, "epm-aimf")
        assert checked.args[2] == flow.service.update_project_budget_group.call_args.kwargs["data"]


@pytest.mark.anyio
class TestAllowedWithoutManageProjects:
    @pytest.mark.parametrize("operation", ["set", "adjust"])
    @pytest.mark.parametrize("project", [None, {}], ids=["no-block", "empty-block"])
    async def test_budget_only_client_writes_budget_of_existing_project(self, flow, actor, operation, project):
        _with_active_group(flow)

        result = await WRITES[operation](actor, {P: 300.0}, project=project, client=_client(BUDGET_ONLY))

        flow.service.update_project_budget_group.assert_awaited_once()
        assert result.created_project is False

    async def test_budget_only_client_cancels(self, flow, actor):
        _with_active_group(flow)

        await _cancel(actor, client=_client(BUDGET_ONLY))

        flow.service.delete_project_budget_group.assert_awaited_once_with(flow.session, group_id="g-1", actor_id="sa-1")


@pytest.mark.anyio
class TestSetAndAdjust:
    async def test_set_on_new_project_creates_project_cost_center_admins_and_budget(self, flow, actor):
        created = _app()
        flow.apps.get_by_name_case_insensitive.return_value = None
        flow.create_project.return_value = created

        result = await _set(
            actor,
            {P: 500.0, C: 100.0},
            project={"cost_center": "nbs-cmpa", "admin_emails": ["a@epam.com", "ghost@epam.com"]},
        )

        flow.get_or_create_cost_center.assert_called_once_with(flow.sync, name="nbs-cmpa", created_by="sa-1")
        flow.create_project.assert_called_once_with(
            flow.sync, actor=actor, name="epm-aimf", cost_center_id=flow.cost_center.id
        )
        flow.assignment.bulk_assign_users_to_project.assert_called_once_with(
            flow.sync,
            created,
            [{"user_id": "id-a@epam.com", "is_project_admin": True}],
            "epm-aimf",
            actor,
            SET_ROUTE,
        )
        data = flow.service.create_project_budget_group.call_args.args[1]
        assert (data.project_name, data.name, data.total_amount, data.budget_duration) == (
            "epm-aimf",
            "Portal RITM1",
            600.0,
            "30d",
        )
        assert flow.service.create_project_budget_group.call_args.kwargs == {"actor_id": "sa-1"}
        flow.service.get_project_budget_group.assert_awaited_once_with(flow.session, "g-new")
        assert result == ProvisioningBudgetResult(
            project_name="epm-aimf",
            cost_center="nbs-cmpa",
            created_project=True,
            created_cost_center=True,
            created_budget=True,
            budget_group=flow.service.get_project_budget_group.return_value,
            admins_assigned=["a@epam.com"],
            admins_not_assigned=["ghost@epam.com"],
        )

    async def test_adjust_labels_admin_assignment_with_its_route(self, flow, actor):
        _with_active_group(flow)

        await _adjust(actor, {C: 200.0}, project={"admin_emails": ["a@epam.com"]})

        assert flow.assignment.bulk_assign_users_to_project.call_args.args[-1] == ADJUST_ROUTE

    async def test_new_group_name_holds_the_longest_client_name_and_reference_whole(self, flow, actor):
        client = make_client(name="x" * CLIENT_NAME_MAX_LENGTH, permissions=FULL_ACCESS)
        longest_reference = "R" * 64  # X-Change-Reference allows 1-64 characters

        request = ProvisioningBudgetSetRequest(amounts={P: 1.0})
        await Svc.set_budget(actor, client, longest_reference, "epm-aimf", request)

        assert flow.service.create_project_budget_group.call_args.args[1].name == f"{client.name} {longest_reference}"

    @pytest.mark.parametrize(("operation", "expected_total"), [("set", 500.0), ("adjust", 550.0)])
    async def test_set_replaces_and_adjust_merges_existing_budget(self, flow, actor, operation, expected_total):
        _with_active_group(flow)

        result = await WRITES[operation](actor, {P: 300.0, C: 200.0})

        flow.service.create_project_budget_group.assert_not_awaited()
        update = flow.service.update_project_budget_group
        update.assert_awaited_once()
        assert (update.call_args.kwargs["group_id"], update.call_args.kwargs["actor_id"]) == ("g-1", "sa-1")
        data = update.call_args.kwargs["data"]
        assert (data.name, data.budget_duration, data.description) == (None, None, None)
        assert data.total_amount == expected_total
        expected_premium = 50.0 if operation == "adjust" else 0.0
        assert _derived_amounts(data) == pytest.approx(
            {"platform": 300.0, "cli": 200.0, "premium_models": expected_premium}
        )
        flow.repository.get_active_by_project.assert_any_await(flow.session, "epm-aimf", for_update=True)
        flow.session.commit.assert_awaited_once()
        assert (result.created_project, result.created_budget) == (False, False)

    async def test_adjust_zero_removes_category_and_keeps_others(self, flow, actor):
        _with_active_group(flow)

        await _adjust(actor, {C: 200.0, M: 0.0})

        data = flow.service.update_project_budget_group.call_args.kwargs["data"]
        assert data.categories["premium_models"].pct == 0
        assert _derived_amounts(data) == pytest.approx({"platform": 500.0, "cli": 200.0, "premium_models": 0.0})
        assert data.categories["platform"].soft_budget == 400.0

    async def test_adjust_resending_current_amounts_keeps_all_soft_limits(self, flow, actor):
        _with_active_group(flow)

        await _adjust(actor, {P: 500.0, C: 100.0, M: 50.0})

        data = flow.service.update_project_budget_group.call_args.kwargs["data"]
        assert {name: spec.soft_budget for name, spec in data.categories.items()} == {
            name: budget.soft_budget for name, budget in CURRENT_GROUP.items()
        }

    async def test_adjust_without_budget_creates_one(self, flow, actor):
        result = await _adjust(actor, {P: 300.0, C: 100.0})

        data = flow.service.create_project_budget_group.call_args.args[1]
        assert data.total_amount == 400.0
        assert {name: spec.pct for name, spec in data.categories.items()} == {
            "platform": 75.0,
            "cli": 25.0,
            "premium_models": 0.0,
        }
        assert (result.created_project, result.created_budget) == (False, True)

    async def test_check_then_project_commit_then_budget_commit(self, flow, actor):
        manager = MagicMock()
        manager.attach_mock(flow.service.check_group_request, "check")
        manager.attach_mock(flow.sync.commit, "commit_project")
        manager.attach_mock(flow.service.create_project_budget_group, "create_group")
        manager.attach_mock(flow.session.commit, "commit_budget")

        await _set(actor, {P: 1.0}, project={"admin_emails": ["a@epam.com"]})

        assert [c[0] for c in manager.mock_calls] == ["check", "commit_project", "create_group", "commit_budget"]

    async def test_project_commit_invalidates_changed_users_after_commit(self, flow, actor):
        manager = MagicMock()
        manager.attach_mock(flow.sync.commit, "commit")
        manager.attach_mock(flow.invalidate, "invalidate")

        await _set(actor, {P: 1.0}, project={"admin_emails": ["a@epam.com"]})

        assert [c[0] for c in manager.mock_calls] == ["commit", "invalidate"]
        flow.invalidate.assert_called_once_with("id-a@epam.com")

    async def test_project_commit_retries_once_when_a_concurrent_write_wins(self, flow, actor):
        flow.apps.get_by_name_case_insensitive.side_effect = [None, None, _app()]
        flow.create_project.side_effect = IntegrityError("insert", {}, Exception("dup"))

        result = await _set(actor, {P: 1.0})

        flow.sync.rollback.assert_called_once()
        flow.sync.commit.assert_called_once()
        assert result.created_project is False
        flow.log.warning.assert_called_once_with(
            "provisioning_sync_conflict_retry: client=Portal, change_reference='RITM1', operation=set, "
            "project='epm-aimf', domain=provisioning"
        )

    async def test_second_project_commit_conflict_propagates(self, flow, actor):
        flow.apps.get_by_name_case_insensitive.return_value = None
        flow.create_project.side_effect = IntegrityError("insert", {}, Exception("dup"))

        with pytest.raises(IntegrityError):
            await _set(actor, {P: 1.0}, project={"admin_emails": ["a@epam.com"]})

        assert flow.sync.rollback.call_count == 2
        flow.invalidate.assert_not_called()
        flow.service.create_project_budget_group.assert_not_awaited()


@pytest.mark.anyio
class TestCancel:
    async def test_deletes_the_budget(self, flow, actor):
        _with_active_group(flow)

        assert await _cancel(actor) is None

        flow.service.delete_project_budget_group.assert_awaited_once_with(flow.session, group_id="g-1", actor_id="sa-1")
        flow.session.commit.assert_awaited_once()
        flow.sync.commit.assert_not_called()

    async def test_no_budget_is_a_noop(self, flow, actor):
        await _cancel(actor)

        _assert_nothing_written(flow)

    async def test_missing_project_is_404_and_creates_nothing(self, flow, actor):
        flow.apps.get_by_name_case_insensitive.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc:
            await _cancel(actor, project_name="project-999")

        assert (exc.value.code, exc.value.message, exc.value.help) == (
            404,
            "Project 'project-999' does not exist",
            "Check the CodeMie project name; there is no budget to cancel.",
        )
        _assert_nothing_written(flow)


@pytest.mark.anyio
class TestPartialWrites:
    async def test_conflict_with_no_surviving_group_is_500_and_resend_creates_it(self, flow, actor):
        flow.apps.get_by_name_case_insensitive.side_effect = [None, None, _app(), _app()]
        flow.service.create_project_budget_group.side_effect = [
            ExtendedHTTPException(code=409, message="exists"),
            _full_group("g-new"),
        ]

        with pytest.raises(ExtendedHTTPException) as exc:
            await _set(actor, {P: 300.0}, project={"admin_emails": ["a@epam.com"]})

        assert (exc.value.code, exc.value.message, exc.value.details) == (500, PARTLY_APPLIED, None)
        flow.create_project.assert_called_once()
        flow.sync.commit.assert_called_once()
        flow.session.rollback.assert_awaited_once()
        flow.session.commit.assert_not_awaited()
        flow.log.error.assert_any_call(
            "provisioning_budget_partly_applied: client=Portal, change_reference='RITM1', operation=set, "
            "project='epm-aimf', status=409, message='exists', domain=provisioning"
        )

        result = await _set(actor, {P: 300.0}, project={"admin_emails": ["a@epam.com"]})

        assert (result.created_project, result.created_budget) == (False, True)
        flow.create_project.assert_called_once()
        flow.session.commit.assert_awaited_once()

    async def test_conflict_with_no_surviving_group_is_500(self, flow, actor):
        conflict = IntegrityError("insert", {}, Exception("dup"))
        flow.service.create_project_budget_group.side_effect = conflict

        with pytest.raises(ExtendedHTTPException) as exc:
            await _set(actor, {P: 300.0})

        assert (exc.value.code, exc.value.message, exc.value.__cause__) == (500, PARTLY_APPLIED, conflict)
        flow.service.update_project_budget_group.assert_not_awaited()
        flow.session.commit.assert_not_awaited()

    async def test_unexpected_error_after_project_commit_is_500_logged_with_project(self, flow, actor):
        boom = RuntimeError("provider down")
        flow.service.create_project_budget_group.side_effect = boom

        with pytest.raises(ExtendedHTTPException) as exc:
            await _set(actor, {P: 300.0})

        assert (exc.value.code, exc.value.message, exc.value.__cause__) == (500, PARTLY_APPLIED, boom)
        flow.sync.commit.assert_called_once()
        flow.log.error.assert_any_call(
            "provisioning_budget_partly_applied: client=Portal, change_reference='RITM1', operation=set, "
            "project='epm-aimf', error_type=RuntimeError, error='provider down', domain=provisioning"
        )

    async def test_other_4xx_after_project_commit_is_500(self, flow, actor):
        flow.service.create_project_budget_group.side_effect = ExtendedHTTPException(code=404, message="gone")

        with pytest.raises(ExtendedHTTPException) as exc:
            await _set(actor, {P: 300.0})

        assert (exc.value.code, exc.value.message) == (500, PARTLY_APPLIED)
        flow.session.rollback.assert_not_awaited()

    async def test_provider_502_passes_through_and_resend_creates(self, flow, actor):
        flow.apps.get_by_name_case_insensitive.side_effect = [None, None, _app(), _app()]
        provider_error = ExtendedHTTPException(code=502, message="Failed to sync project budget")
        flow.service.create_project_budget_group.side_effect = [provider_error, _full_group("g-new")]

        with pytest.raises(ExtendedHTTPException) as exc:
            await _set(actor, {P: 300.0, C: 100.0})

        assert exc.value is provider_error
        flow.sync.commit.assert_called_once()

        result = await _set(actor, {P: 300.0, C: 100.0})

        assert (result.created_project, result.created_budget) == (False, True)
        assert flow.service.create_project_budget_group.await_count == 2

    async def test_category_not_synced_is_502_after_commit(self, flow, actor):
        _with_active_group(flow)
        flow.service.get_project_budget_group.return_value = _full_group(
            "g-1", **{**CURRENT_GROUP, "cli": _budget(200.0, 160.0, sync_status="failed")}
        )

        with pytest.raises(ExtendedHTTPException) as exc:
            await _adjust(actor, {C: 200.0})

        assert (exc.value.code, exc.value.message) == (502, PARTLY_APPLIED)
        flow.session.commit.assert_awaited_once()
        flow.log.error.assert_any_call(
            "provisioning_budget_partly_applied: client=Portal, change_reference='RITM1', operation=adjust, "
            "project='epm-aimf', unsynced_categories=['cli'], domain=provisioning"
        )

    async def test_category_synced_while_enforcement_was_off_is_502_under_litellm(self, flow, actor):
        _with_active_group(flow)
        flow.service.get_project_budget_group.return_value = _full_group(
            "g-1", platform=_budget(500.0, 400.0, sync_status="noop")
        )

        with _active_provider("litellm"), pytest.raises(ExtendedHTTPException) as exc:
            await _adjust(actor, {C: 200.0})

        assert (exc.value.code, exc.value.message) == (502, PARTLY_APPLIED)

    async def test_member_push_failed_is_502_after_commit(self, flow, actor):
        _with_active_group(flow)
        flow.service.get_project_budget_group.return_value = _full_group(
            "g-1", members=[_member("ok"), _member("failed")], platform=_budget(500.0, 400.0)
        )

        with _active_provider("litellm"), pytest.raises(ExtendedHTTPException) as exc:
            await _adjust(actor, {C: 200.0})

        assert (exc.value.code, exc.value.message) == (502, PARTLY_APPLIED)
        flow.session.commit.assert_awaited_once()
        flow.log.error.assert_any_call(
            "provisioning_budget_partly_applied: client=Portal, change_reference='RITM1', operation=adjust, "
            "project='epm-aimf', unsynced_categories=['platform'], domain=provisioning"
        )

    @pytest.mark.parametrize(("sync_status", "provider"), [("ok", "litellm"), ("noop", "noop")])
    async def test_synced_categories_answer_normally(self, flow, actor, sync_status, provider):
        _with_active_group(flow)
        # A pending member is pushed lazily by the runtime sync, so it is no failure.
        flow.service.get_project_budget_group.return_value = _full_group(
            "g-1", members=[_member("pending")], platform=_budget(500.0, 400.0, sync_status=sync_status)
        )

        with _active_provider(provider):
            result = await _adjust(actor, {C: 200.0})

        assert result.budget_group is flow.service.get_project_budget_group.return_value

    async def test_group_changed_so_replan_fails_is_500(self, flow, actor):
        _with_active_group(flow)
        without_platform = {"cli": _budget(100.0, 80.0)}
        flow.budgets.get_by_ids.side_effect = [_budgets_by_id(CURRENT_GROUP), _budgets_by_id(without_platform)]

        with pytest.raises(ExtendedHTTPException) as exc:
            await _adjust(actor, {C: 200.0}, project={"admin_emails": ["a@epam.com"]})

        assert (exc.value.code, exc.value.message) == (500, PARTLY_APPLIED)
        flow.sync.commit.assert_called_once()
        flow.service.update_project_budget_group.assert_not_awaited()
        flow.log.error.assert_any_call(
            "provisioning_budget_partly_applied: client=Portal, change_reference='RITM1', operation=adjust, "
            "project='epm-aimf', status=400, message='Platform budget must be greater than 0', domain=provisioning"
        )


@pytest.mark.anyio
class TestConcurrencyAndResend:
    async def test_budget_commit_replans_against_the_locked_group(self, flow, actor):
        _with_active_group(flow)
        changed = {**CURRENT_GROUP, "cli": _budget(150.0, 120.0)}
        flow.budgets.get_by_ids.side_effect = [_budgets_by_id(CURRENT_GROUP), _budgets_by_id(changed)]

        await _adjust(actor, {P: 600.0})

        data = flow.service.update_project_budget_group.call_args.kwargs["data"]
        assert data.total_amount == 800.0
        assert _derived_amounts(data) == pytest.approx({"platform": 600.0, "cli": 150.0, "premium_models": 50.0})
        assert data.categories["cli"].soft_budget == 120.0

    @pytest.mark.parametrize(
        "conflict", [ExtendedHTTPException(code=409, message="exists"), IntegrityError("insert", {}, Exception())]
    )
    @pytest.mark.parametrize(
        ("operation", "expected_amounts", "expected_soft"),
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
    async def test_group_created_concurrently_is_updated_instead(
        self, flow, actor, conflict, operation, expected_amounts, expected_soft
    ):
        flow.repository.get_active_by_project.side_effect = [None, None, SimpleNamespace(id="g-1")]
        flow.service.create_project_budget_group.side_effect = conflict

        result = await WRITES[operation](actor, {P: 300.0, C: 100.0})

        flow.session.rollback.assert_awaited_once()
        assert flow.repository.get_active_by_project.await_args_list[2] == call(
            flow.session, "epm-aimf", for_update=True
        )
        update = flow.service.update_project_budget_group
        update.assert_awaited_once()
        assert update.call_args.kwargs["group_id"] == "g-1"
        data = update.call_args.kwargs["data"]
        assert isinstance(data, ProjectBudgetGroupUpdateRequest)
        assert _derived_amounts(data) == pytest.approx(expected_amounts)
        assert {name: spec.soft_budget for name, spec in data.categories.items()} == expected_soft
        flow.session.commit.assert_awaited_once()
        assert result.created_budget is False

    async def test_resend_after_budget_failure_on_new_project_completes(self, flow, actor):
        project = _app()
        admin = _user("a@epam.com")
        flow.apps.get_by_name_case_insensitive.side_effect = [None, None, project, project]
        flow.create_project.return_value = project
        flow.users.get_by_emails.return_value = {"a@epam.com": admin}
        flow.memberships.get_by_users_and_project.side_effect = [{}, {admin.id: SimpleNamespace(is_project_admin=True)}]
        flow.service.create_project_budget_group.side_effect = [RuntimeError("provider down"), _full_group("g-new")]

        with pytest.raises(ExtendedHTTPException) as exc:
            await _set(actor, {P: 300.0}, project={"admin_emails": ["a@epam.com"]})
        assert (exc.value.code, exc.value.message) == (500, PARTLY_APPLIED)
        flow.assignment.bulk_assign_users_to_project.assert_called_once()
        flow.assignment.bulk_assign_users_to_project.reset_mock()

        result = await _set(actor, {P: 300.0}, project={"admin_emails": ["a@epam.com"]})

        assert (result.created_project, result.created_budget) == (False, True)
        assert result.admins_assigned == ["a@epam.com"]
        flow.create_project.assert_called_once()
        flow.assignment.bulk_assign_users_to_project.assert_not_called()

    async def test_project_deleted_after_the_check_is_not_recreated_without_manage_projects(self, flow, actor):
        flow.apps.get_by_name_case_insensitive.side_effect = [_app(), None]

        with pytest.raises(ExtendedHTTPException) as exc:
            await _set(actor, {P: 1.0}, client=_client(BUDGET_ONLY))

        assert (exc.value.code, exc.value.details) == (403, MANAGE_PROJECTS_DENIED)
        flow.sync.commit.assert_not_called()
        flow.assignment.bulk_assign_users_to_project.assert_not_called()
        flow.service.create_project_budget_group.assert_not_awaited()

    @pytest.mark.parametrize("operation", ["set", "adjust", "cancel"])
    async def test_path_name_in_another_case_uses_stored_project(self, flow, actor, operation):
        flow.apps.get_by_name_case_insensitive.return_value = _app(name="EPM-Legacy")

        if operation == "cancel":
            await _cancel(actor, project_name="epm-legacy")
        else:
            result = await WRITES[operation](actor, {P: 1.0}, project_name="epm-legacy")
            assert result.project_name == "EPM-Legacy"
            assert flow.service.create_project_budget_group.call_args.args[1].project_name == "EPM-Legacy"
        assert {c.args[1] for c in flow.repository.get_active_by_project.await_args_list} == {"EPM-Legacy"}


@pytest.mark.anyio
class TestLogging:
    async def test_set_logs_started_resolved_and_completed(self, flow, actor):
        flow.apps.get_by_name_case_insensitive.return_value = None

        await _set(actor, {P: 1.0}, project={"cost_center": "nbs-cmpa", "admin_emails": ["a@epam.com", "b@epam.com"]})

        assert [c.args[0] for c in flow.log.info.call_args_list] == [
            "provisioning_budget_request_started: client=Portal, change_reference='RITM1', operation=set, "
            "project='epm-aimf', cost_center='nbs-cmpa', admin_emails=2, actor_user_id=sa-1, domain=provisioning",
            "provisioning_project_resolved: client=Portal, change_reference='RITM1', operation=set, "
            "project='epm-aimf', cost_center='nbs-cmpa', created_project=True, created_cost_center=True, "
            "admins_assigned=1, admins_not_assigned=1, domain=provisioning",
            "provisioning_budget_request_completed: client=Portal, change_reference='RITM1', operation=set, "
            "project='epm-aimf', cost_center='nbs-cmpa', created_project=True, created_cost_center=True, "
            "created_budget=True, admins_assigned=1, admins_not_assigned=1, actor_user_id=sa-1, domain=provisioning",
        ]

    async def test_cancel_logs_started_and_completed(self, flow, actor):
        _with_active_group(flow)
        flow.apps.get_by_name_case_insensitive.return_value = _app(name="EPM-Legacy")

        await _cancel(actor, project_name="epm-legacy")

        assert [c.args[0] for c in flow.log.info.call_args_list] == [
            "provisioning_budget_request_started: client=Portal, change_reference='RITM1', operation=cancel, "
            "project='epm-legacy', actor_user_id=sa-1, domain=provisioning",
            "provisioning_budget_request_completed: client=Portal, change_reference='RITM1', operation=cancel, "
            "project='EPM-Legacy', deleted_budget=True, actor_user_id=sa-1, domain=provisioning",
        ]

    async def test_unexpected_failure_is_logged_at_error_and_reraised(self, flow, actor):
        boom = RuntimeError("database down")
        flow.apps.get_by_name_case_insensitive.return_value = None
        flow.create_project.side_effect = boom

        with pytest.raises(RuntimeError) as exc:
            await _set(actor, {P: 1.0})

        assert exc.value is boom
        flow.log.error.assert_called_once_with(
            "provisioning_budget_request_failed: client=Portal, change_reference='RITM1', operation=set, "
            "project='epm-aimf', error_type=RuntimeError, error='database down', domain=provisioning"
        )

    async def test_partly_applied_500_is_logged_at_error(self, flow, actor):
        flow.service.create_project_budget_group.side_effect = ExtendedHTTPException(code=404, message="gone")

        with pytest.raises(ExtendedHTTPException):
            await _set(actor, {P: 1.0})

        flow.log.error.assert_any_call(
            "provisioning_budget_request_failed: client=Portal, change_reference='RITM1', operation=set, "
            f"project='epm-aimf', status=500, message='{PARTLY_APPLIED}', domain=provisioning"
        )
        flow.log.warning.assert_not_called()

    async def test_client_rejection_is_logged_at_warning_with_status_and_message(self, flow, actor):
        flow.apps.get_by_name_case_insensitive.return_value = None

        with pytest.raises(ExtendedHTTPException):
            await _cancel(actor)

        flow.log.warning.assert_called_once_with(
            "provisioning_budget_request_failed: client=Portal, change_reference='RITM1', operation=cancel, "
            "project='epm-aimf', status=404, message=\"Project 'epm-aimf' does not exist\", domain=provisioning"
        )
        flow.log.error.assert_not_called()
