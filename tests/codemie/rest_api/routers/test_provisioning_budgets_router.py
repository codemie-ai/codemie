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

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from codemie.configs import config
from codemie.configs.provisioning_clients_config import ProvisioningPermission
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import extended_http_exception_handler, validation_exception_handler
from codemie.rest_api.models.provisioning import ProvisioningBudgetRequest, ProvisioningBudgetSetRequest
from codemie.rest_api.routers.provisioning import mount_provisioning_api
from codemie.rest_api.security import provisioning_access
from codemie.rest_api.security.authentication import ACCESS_DENIED_MESSAGE
from codemie.rest_api.security.user import User
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.budget.budget_models import Budget, ProjectBudgetAssignment, ProjectBudgetGroup
from codemie.service.budget.project_budget_service import ProjectBudgetGroupCategoryResult, ProjectBudgetGroupFullResult
from codemie.service.provisioning_budget_service import ProvisioningBudgetResult, provisioning_budget_service
from tests.codemie.configs.provisioning_clients_support import CLIENT_USER_ID, make_client, registered_clients

PROJECT_NAME = "epm-aimf"
BUDGET_URL = "/v1/provisioning/projects/{project_name}/budget"
CHANGE_REFERENCE_HEADER = "X-Change-Reference"
CHANGE_REFERENCE = "CHG0012345"
VALID_BODY = '{"amounts": {"platform": 500, "cli": 100}}'
ALL_METHODS = ["PUT", "PATCH", "DELETE"]
WRITE_METHODS = ["PUT", "PATCH"]
MANAGE_BUDGETS_ONLY = frozenset({ProvisioningPermission.MANAGE_BUDGETS})
FORBIDDEN_VENDOR_TERMS = ("servicenow", "epam_project_code", "action")


@pytest.fixture(autouse=True)
def api_switched_on(monkeypatch):
    monkeypatch.setattr(provisioning_access, "_provisioning_routes", frozenset())
    monkeypatch.setattr(config, "PROVISIONING_API_ENABLED", True)
    monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", True)


@pytest.fixture
def mock_service():
    with (
        patch.object(provisioning_budget_service, "set_budget", new_callable=AsyncMock) as set_budget,
        patch.object(provisioning_budget_service, "adjust_budget", new_callable=AsyncMock) as adjust_budget,
        patch.object(provisioning_budget_service, "cancel_budget", new_callable=AsyncMock) as cancel_budget,
    ):
        set_budget.return_value = adjust_budget.return_value = _result()
        cancel_budget.return_value = None
        yield SimpleNamespace(set_budget=set_budget, adjust_budget=adjust_budget, cancel_budget=cancel_budget)


@pytest.fixture
def act_as():
    """Mount the API on a fresh app whose real authenticate() resolves every request to `user`."""
    provider = MagicMock()
    with (
        patch("codemie.rest_api.security.authentication.get_user_provider", return_value=provider),
        patch("codemie.rest_api.security.authentication.IdpFactory"),
        patch("codemie.rest_api.security.teams_authentication_resolver.is_teams_bot_request", return_value=False),
    ):

        def build(user: User) -> TestClient:
            provider.authenticate_and_load_user = AsyncMock(return_value=user)
            return TestClient(_build_app())

        yield build


@pytest.fixture
def budget_client(act_as):
    """A budget-only provisioning client's session, with that client registered."""
    with registered_clients(make_client(permissions=MANAGE_BUDGETS_ONLY)):
        yield act_as(User(id=CLIENT_USER_ID, email="client@svc.example.com"))


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    mount_provisioning_api(app)
    return app


def _result(created_budget: bool = True) -> ProvisioningBudgetResult:
    platform = _category_result(BudgetCategory.PLATFORM, max_budget=500.0, soft_budget=400.0, reset="2026-11-18T00:00Z")
    cli = _category_result(BudgetCategory.CLI, max_budget=150.0, soft_budget=120.0, reset=None)
    return ProvisioningBudgetResult(
        project_name=PROJECT_NAME,
        cost_center="eng-123",
        created_project=False,
        created_cost_center=True,
        created_budget=created_budget,
        budget_group=ProjectBudgetGroupFullResult(
            group=ProjectBudgetGroup(project_name=PROJECT_NAME, name="g", budget_duration="30d", created_by="u"),
            categories=[platform, cli],
        ),
        admins_assigned=["approver@example.com"],
        admins_not_assigned=["requester@example.com"],
    )


def _category_result(
    category: BudgetCategory, max_budget: float, soft_budget: float, reset: str | None
) -> ProjectBudgetGroupCategoryResult:
    return ProjectBudgetGroupCategoryResult(
        budget=Budget(
            budget_id=f"{PROJECT_NAME}-{category.value}",
            name=f"{PROJECT_NAME}-{category.value}",
            budget_category=category.value,
            max_budget=max_budget,
            soft_budget=soft_budget,
            budget_duration="30d",
            budget_reset_at=reset,
            created_by="u",
        ),
        assignment=ProjectBudgetAssignment(
            project_name=PROJECT_NAME, budget_category=category.value, budget_id="b", assigned_by="u"
        ),
    )


def _send(client: TestClient, method: str, project: str = PROJECT_NAME, body: str = VALID_BODY, headers=None):
    headers = {CHANGE_REFERENCE_HEADER: CHANGE_REFERENCE} if headers is None else headers
    if method == "DELETE":
        return client.delete(BUDGET_URL.format(project_name=project), headers=headers)
    return client.request(
        method,
        BUDGET_URL.format(project_name=project),
        content=body,
        headers={**headers, "Content-Type": "application/json"},
    )


def _assert_platform_422(response, mock_service) -> None:
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["message"]
    assert isinstance(error["details"], list)
    _assert_service_untouched(mock_service)


def _assert_service_untouched(mock_service) -> None:
    for service_call in vars(mock_service).values():
        service_call.assert_not_called()


class TestStatusCodes:
    @pytest.mark.parametrize("created_budget,expected_status", [(True, 201), (False, 200)], ids=["created", "replaced"])
    def test_put_answers_201_when_it_created_the_budget_and_200_otherwise(
        self, budget_client, mock_service, created_budget, expected_status
    ):
        mock_service.set_budget.return_value = _result(created_budget)

        response = _send(budget_client, "PUT")

        assert response.status_code == expected_status
        assert response.json()["created_budget"] is created_budget

    def test_patch_answers_200_even_when_it_created_the_budget(self, budget_client, mock_service):
        mock_service.adjust_budget.return_value = _result(created_budget=True)

        response = _send(budget_client, "PATCH")

        assert response.status_code == 200
        assert response.json()["created_budget"] is True

    def test_delete_answers_204_without_a_body(self, budget_client, mock_service):
        response = _send(budget_client, "DELETE")

        assert response.status_code == 204
        assert response.content == b""


class TestResponseBody:
    def test_result_is_mapped_to_the_documented_shape(self, budget_client, mock_service):
        response = _send(budget_client, "PUT")

        assert response.json() == {
            "project_name": PROJECT_NAME,
            "cost_center": "eng-123",
            "created_project": False,
            "created_cost_center": True,
            "created_budget": True,
            "budget": {
                "total_amount": 650.0,
                "budget_duration": "30d",
                "categories": [
                    {
                        "category": "platform",
                        "max_budget": 500.0,
                        "soft_budget": 400.0,
                        "budget_reset_at": "2026-11-18T00:00Z",
                    },
                    {"category": "cli", "max_budget": 150.0, "soft_budget": 120.0, "budget_reset_at": None},
                ],
            },
            "admins_assigned": ["approver@example.com"],
            "admins_not_assigned": ["requester@example.com"],
        }

    # Python 3.12's float sum() already gives 0.6 for the first case; the second is where it lands off by one ulp.
    @pytest.mark.parametrize(
        "limits,total", [((0.1, 0.2, 0.3), 0.6), ((15.59, 9.21, 9.68), 34.48)], ids=["tenths", "cents"]
    )
    def test_total_amount_is_the_exact_decimal_sum_of_the_category_limits(
        self, budget_client, mock_service, limits, total
    ):
        result = _result()
        result.budget_group.categories = [
            _category_result(category, max_budget=limit, soft_budget=limit, reset=None)
            for category, limit in zip(BudgetCategory, limits, strict=True)
        ]
        mock_service.set_budget.return_value = result

        response = _send(budget_client, "PUT")

        assert response.json()["budget"]["total_amount"] == total


class TestServiceCall:
    def test_put_passes_actor_client_reference_name_and_set_request(self, budget_client, mock_service):
        _send(budget_client, "PUT")

        mock_service.set_budget.assert_awaited_once()
        call = mock_service.set_budget.await_args.kwargs
        assert call["actor"].id == CLIENT_USER_ID
        assert call["client"].name == "support-portal"
        assert call["reference"] == CHANGE_REFERENCE
        assert call["project_name"] == PROJECT_NAME
        assert call["request"] == ProvisioningBudgetSetRequest(
            amounts={BudgetCategory.PLATFORM: 500, BudgetCategory.CLI: 100}
        )

    def test_patch_passes_the_adjust_request(self, budget_client, mock_service):
        _send(budget_client, "PATCH", body='{"amounts": {"cli": 0}}')

        call = mock_service.adjust_budget.await_args.kwargs
        assert call["project_name"] == PROJECT_NAME
        assert call["reference"] == CHANGE_REFERENCE
        assert call["request"] == ProvisioningBudgetRequest(amounts={BudgetCategory.CLI: 0})

    def test_delete_passes_actor_client_reference_and_name(self, budget_client, mock_service):
        _send(budget_client, "DELETE")

        mock_service.cancel_budget.assert_awaited_once()
        call = mock_service.cancel_budget.await_args.kwargs
        assert call["actor"].id == CLIENT_USER_ID
        assert call["client"].name == "support-portal"
        assert call["reference"] == CHANGE_REFERENCE
        assert call["project_name"] == PROJECT_NAME

    def test_patch_accepts_zero_platform_and_leaves_the_merged_total_to_the_service(self, budget_client, mock_service):
        response = _send(budget_client, "PATCH", body='{"amounts": {"platform": 0}}')

        assert response.status_code == 200
        mock_service.adjust_budget.assert_awaited_once()


class TestChangeReferenceHeader:
    @pytest.mark.parametrize("method", ALL_METHODS)
    def test_missing_header_is_422(self, budget_client, mock_service, method):
        response = _send(budget_client, method, headers={})

        _assert_platform_422(response, mock_service)
        assert CHANGE_REFERENCE_HEADER.lower() in json.dumps(response.json()).lower()

    @pytest.mark.parametrize("method", ALL_METHODS)
    @pytest.mark.parametrize("reference", ["", "x" * 65], ids=["empty", "65_chars"])
    def test_empty_or_oversized_header_is_422(self, budget_client, mock_service, method, reference):
        response = _send(budget_client, method, headers={CHANGE_REFERENCE_HEADER: reference})

        _assert_platform_422(response, mock_service)

    @pytest.mark.parametrize("method", ALL_METHODS)
    def test_64_char_header_is_accepted(self, budget_client, mock_service, method):
        response = _send(budget_client, method, headers={CHANGE_REFERENCE_HEADER: "x" * 64})

        assert response.status_code in (200, 201, 204)


class TestBodyValidation:
    @pytest.mark.parametrize(
        "body",
        [
            '{"amounts": {"platform": 500}, "unknown": 1}',
            '{"amounts": {"platform": 500}, "project": {"epam_project_code": "x"}}',
            "{}",
            '{"amounts": {}}',
            '{"amounts": {"platform": 500, "cli": -1}}',
            '{"amounts": {"platform": Infinity}}',
            '{"amounts": {"platform": 1e999}}',
        ],
        ids=[
            "unknown_top_level_field",
            "unknown_project_field",
            "amounts_missing",
            "amounts_empty",
            "negative_amount",
            "infinite_amount",
            "overflowing_amount",
        ],
    )
    @pytest.mark.parametrize("method", WRITE_METHODS)
    def test_invalid_body_is_422_in_the_platform_shape(self, budget_client, mock_service, method, body):
        response = _send(budget_client, method, body=body)

        _assert_platform_422(response, mock_service)

    def test_put_with_zero_platform_is_422(self, budget_client, mock_service):
        response = _send(budget_client, "PUT", body='{"amounts": {"platform": 0}}')

        _assert_platform_422(response, mock_service)


class TestProjectNamePath:
    @pytest.mark.parametrize("method", ALL_METHODS)
    def test_name_reaches_the_service_exactly_as_sent(self, budget_client, mock_service, method):
        _send(budget_client, method, project="%20My-Proj")

        service_call = {
            "PUT": mock_service.set_budget,
            "PATCH": mock_service.adjust_budget,
            "DELETE": mock_service.cancel_budget,
        }[method]
        assert service_call.await_args.kwargs["project_name"] == " My-Proj"

    @pytest.mark.parametrize("method", ALL_METHODS)
    def test_two_character_name_is_422(self, budget_client, mock_service, method):
        response = _send(budget_client, method, project="ab")

        _assert_platform_422(response, mock_service)


class TestAccess:
    @pytest.mark.parametrize("method", ALL_METHODS)
    @pytest.mark.parametrize(
        "permissions",
        [
            frozenset(),
            frozenset({ProvisioningPermission.READ_PROJECTS}),
            frozenset({ProvisioningPermission.MANAGE_PROJECTS}),
        ],
        ids=["none", "read_projects", "manage_projects_only"],
    )
    def test_client_without_manage_budgets_is_403(self, act_as, mock_service, method, permissions):
        with registered_clients(make_client(permissions=permissions)):
            client = act_as(User(id=CLIENT_USER_ID, email="client@svc.example.com"))
            response = _send(client, method)

        assert response.status_code == 403
        assert response.json()["error"]["message"] == ACCESS_DENIED_MESSAGE
        _assert_service_untouched(mock_service)


class TestOpenApi:
    @pytest.fixture
    def openapi(self):
        with registered_clients(make_client()):
            return _build_app().openapi()

    @pytest.fixture
    def operations(self, openapi):
        return openapi["paths"][BUDGET_URL]

    def test_put_patch_and_delete_are_documented(self, operations):
        assert set(operations) == {"put", "patch", "delete"}
        assert all(operation["tags"] == ["Provisioning"] for operation in operations.values())

    @pytest.mark.parametrize(
        "method,expected_codes",
        [
            ("put", {"200", "201", "400", "403", "409", "422", "500", "502"}),
            ("patch", {"200", "400", "403", "409", "422", "500", "502"}),
            ("delete", {"204", "403", "404", "409", "422", "500"}),
        ],
    )
    def test_each_operation_documents_the_status_codes_it_can_answer(self, operations, method, expected_codes):
        assert set(operations[method]["responses"]) == expected_codes

    @pytest.mark.parametrize("method", ALL_METHODS)
    def test_422_is_documented_in_the_platform_shape(self, operations, method):
        schema = operations[method.lower()]["responses"]["422"]["content"]["application/json"]["schema"]

        assert schema == {"$ref": "#/components/schemas/ProvisioningValidationErrorResponse"}

    @pytest.mark.parametrize("method", ALL_METHODS)
    def test_error_statuses_use_the_error_response_schema(self, operations, method):
        responses = operations[method.lower()]["responses"]
        for code in {"403", "500", "502"} & set(responses):
            schema = responses[code]["content"]["application/json"]["schema"]
            assert schema == {"$ref": "#/components/schemas/ErrorResponse"}, code

    @pytest.mark.parametrize("method", ALL_METHODS)
    def test_every_route_states_the_resend_rule(self, operations, method):
        description = operations[method.lower()]["description"]

        assert "a 4xx means nothing changed" in description
        assert "send the same request again" in description

    def test_delete_documents_neither_a_body_nor_a_partly_applied_answer(self, operations):
        responses = operations["delete"]["responses"]

        assert "body" not in responses["422"]["description"]
        assert "partly applied" not in json.dumps(responses)

    @pytest.mark.parametrize("method", ["put", "patch"])
    def test_write_errors_name_every_condition(self, operations, method):
        responses = operations[method]["responses"]

        assert "cost centers are disabled" in responses["403"]["description"]
        assert "project's name is invalid" in responses["400"]["description"]
        assert "cost center name is invalid" in responses["400"]["description"]

    def test_403_names_the_manage_projects_condition_except_for_delete(self, operations):
        manage_projects = ProvisioningPermission.MANAGE_PROJECTS.value
        descriptions = {
            method: operation["responses"]["403"]["description"] for method, operation in operations.items()
        }

        assert ProvisioningPermission.MANAGE_BUDGETS.value in descriptions["put"]
        assert manage_projects in descriptions["put"]
        assert manage_projects in descriptions["patch"]
        assert manage_projects not in descriptions["delete"]

    def test_change_reference_header_is_required_with_length_bounds(self, operations):
        for operation in operations.values():
            header = next(p for p in operation["parameters"] if p["in"] == "header")

            assert header["name"] == CHANGE_REFERENCE_HEADER
            assert header["required"] is True
            assert (header["schema"]["minLength"], header["schema"]["maxLength"]) == (1, 64)

    def test_operations_and_schemas_are_free_of_vendor_terms(self, openapi, operations):
        schemas = {
            name: schema for name, schema in openapi["components"]["schemas"].items() if name.startswith("Provisioning")
        }
        documented = json.dumps([operations, schemas]).lower()

        for term in FORBIDDEN_VENDOR_TERMS:
            assert term not in documented, term
