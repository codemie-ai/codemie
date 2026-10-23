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

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import extended_http_exception_handler, validation_exception_handler
from codemie.rest_api.models.servicenow_integration import (
    NormalizedEmail,
    ServiceNowBudgetCategoryResponse,
    ServiceNowBudgetGroupResponse,
    ServiceNowBudgetRequest,
    ServiceNowBudgetResponse,
    ServiceNowProjectResponse,
)
from codemie.rest_api.routers.servicenow_integration import (
    _to_budget_response,
    apply_servicenow_budget,
    list_servicenow_projects,
    router,
)
from codemie.rest_api.security.authentication import (
    ACCESS_DENIED_MESSAGE,
    authenticate,
    servicenow_integration_access_only,
)
from codemie.rest_api.security import servicenow_access
from codemie.rest_api.security.servicenow_access import _allowed_routes
from codemie.rest_api.security.user import User
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.servicenow_integration_service import ServiceNowBudgetResult

SERVICE_PATH = "codemie.rest_api.routers.servicenow_integration.servicenow_integration_service"
CONFIG_PATH = "codemie.rest_api.security.servicenow_access.config"


def _payload(**overrides):
    return {
        "action": "set",
        "request_id": "RITM0012345",
        "epam_project_code": "nbs-cmpa",
        "admin_emails": [],
        "amounts": {"platform": 500.0},
    } | overrides


class TestServiceNowBudgetRequest:
    def test_project_name_defaults_to_lowercased_code(self):
        request = ServiceNowBudgetRequest(**_payload(epam_project_code=" NBS-CMPA "))

        assert request.epam_project_code == "nbs-cmpa"
        assert request.codemie_project_name == "nbs-cmpa"

    def test_emails_are_trimmed_lowercased_and_deduplicated(self):
        request = ServiceNowBudgetRequest(
            **_payload(admin_emails=[" Requester@EPAM.com ", "requester@epam.com", "b@epam.com"])
        )

        assert request.admin_emails == ["requester@epam.com", "b@epam.com"]

    def test_normalized_email_type_for_query_params(self):
        assert TypeAdapter(list[NormalizedEmail]).validate_python([" A@EPAM.com "]) == ["a@epam.com"]

    def test_names_differing_only_by_case_become_equal(self):
        request = ServiceNowBudgetRequest(**_payload(epam_project_code="NBS-CMPA", codemie_project_name="nbs-cmpa"))

        assert request.epam_project_code == request.codemie_project_name == "nbs-cmpa"

    @pytest.mark.parametrize(
        "overrides",
        [
            {"amounts": None},
            {"action": "adjust", "amounts": {}},
            {"amounts": {"gpu": 10.0}},
            {"amounts": {"platform": -1.0}},
            {"admin_emails": ["not-an-email"]},
            {"request_id": ""},
            {"epam_project_code": "x" * 101},
            {"epam_project_code": "ab"},
            {"codemie_project_name": " a "},
            {"amounts": {"platform": float("inf")}},
            {"amounts": {"platform": 100.0, "cli": float("inf")}},
            {"amounts": {"platform": float("nan")}},
        ],
    )
    def test_invalid_payload_is_rejected(self, overrides):
        with pytest.raises(ValidationError):
            ServiceNowBudgetRequest(**_payload(**overrides))

    def test_cancel_without_amounts_is_valid(self):
        assert ServiceNowBudgetRequest(**_payload(action="cancel", amounts=None)).amounts is None

    @pytest.mark.parametrize("amounts", [{"cli": 10.0}, {"platform": 0.0, "cli": 10.0}])
    def test_set_without_positive_platform_is_rejected(self, amounts):
        with pytest.raises(ValidationError, match="platform must be greater than 0 for set"):
            ServiceNowBudgetRequest(**_payload(action="set", amounts=amounts))

    def test_adjust_without_platform_is_accepted(self):
        assert ServiceNowBudgetRequest(**_payload(action="adjust", amounts={"cli": 10.0})).amounts

    def test_cancel_ignores_amounts_and_admin_emails(self):
        request = ServiceNowBudgetRequest(
            **_payload(action="cancel", amounts={"cli": 1.0}, admin_emails=["a@epam.com"])
        )

        assert request.amounts is None
        assert request.admin_emails == []


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def service_account():
    return User(id="sn-sa", username="servicenow", email="sn@example.com")


@pytest.fixture
def mock_service():
    with patch(SERVICE_PATH) as service:
        yield service


def _category_result(category: str, max_budget: float, soft_budget: float, reset_at: str | None):
    budget = SimpleNamespace(
        budget_category=category, max_budget=max_budget, soft_budget=soft_budget, budget_reset_at=reset_at
    )
    return SimpleNamespace(budget=budget)


def _budget_result(**overrides) -> ServiceNowBudgetResult:
    group = SimpleNamespace(
        group=SimpleNamespace(budget_duration="30d"),
        categories=[
            _category_result("premium_models", 50.0, 40.0, None),
            _category_result("platform", 500.0, 400.0, "2026-11-18T00:00:00+00:00"),
            _category_result("cli", 100.0, 80.0, "2026-11-18T00:00:00+00:00"),
        ],
        total_amount=650.0,
    )
    return ServiceNowBudgetResult(
        **{
            "project_name": "Epm-AIMF",
            "cost_center": "epm-cdme",
            "created_project": True,
            "created_cost_center": False,
            "budget_group": group,
            "admins_assigned": ["a@epam.com"],
            "admins_pending": ["b@epam.com"],
        }
        | overrides
    )


@pytest.mark.anyio
class TestListProjectsEndpoint:
    async def test_returns_project_names_and_display_names(self, mock_service, service_account):
        mock_service.list_projects = AsyncMock(
            return_value=[SimpleNamespace(name="epm-aimf", display_name="AI Multimodal Framework")]
        )

        response = await list_servicenow_projects(emails=["a@epam.com"], user=service_account, _=None)

        mock_service.list_projects.assert_awaited_once_with(["a@epam.com"])
        assert response == [ServiceNowProjectResponse(project_name="epm-aimf", display_name="AI Multimodal Framework")]

    async def test_missing_display_name_is_null(self, mock_service, service_account):
        mock_service.list_projects = AsyncMock(return_value=[SimpleNamespace(name="epm-aimf", display_name=None)])

        response = await list_servicenow_projects(emails=["a@epam.com"], user=service_account, _=None)

        assert response[0].display_name is None


@pytest.mark.anyio
class TestBudgetsEndpoint:
    async def test_maps_result_to_response(self, mock_service, service_account):
        mock_service.apply_budget_request = AsyncMock(return_value=_budget_result())
        payload = ServiceNowBudgetRequest(**_payload())

        response = await apply_servicenow_budget(payload=payload, user=service_account, _=None)

        mock_service.apply_budget_request.assert_awaited_once_with(service_account, payload)
        assert response == ServiceNowBudgetResponse(
            project_name="Epm-AIMF",
            cost_center="epm-cdme",
            created_project=True,
            created_cost_center=False,
            budget=ServiceNowBudgetGroupResponse(
                total_amount=650.0,
                budget_duration="30d",
                categories=[
                    ServiceNowBudgetCategoryResponse(
                        category=BudgetCategory.PLATFORM,
                        max_budget=500.0,
                        soft_budget=400.0,
                        budget_reset_at="2026-11-18T00:00:00+00:00",
                    ),
                    ServiceNowBudgetCategoryResponse(
                        category=BudgetCategory.CLI,
                        max_budget=100.0,
                        soft_budget=80.0,
                        budget_reset_at="2026-11-18T00:00:00+00:00",
                    ),
                    ServiceNowBudgetCategoryResponse(
                        category=BudgetCategory.PREMIUM_MODELS, max_budget=50.0, soft_budget=40.0, budget_reset_at=None
                    ),
                ],
            ),
            admins_assigned=["a@epam.com"],
            admins_pending=["b@epam.com"],
        )

    async def test_cancel_result_has_null_budget(self, mock_service, service_account):
        mock_service.apply_budget_request = AsyncMock(
            return_value=_budget_result(budget_group=None, created_project=False)
        )

        response = await apply_servicenow_budget(
            payload=ServiceNowBudgetRequest(**_payload(action="cancel", amounts=None)), user=service_account, _=None
        )

        assert response.budget is None
        assert response.project_name == "Epm-AIMF"

    @pytest.mark.parametrize("code", [400, 403, 404, 409])
    async def test_service_errors_propagate(self, code, mock_service, service_account):
        error = ExtendedHTTPException(code=code, message="x")
        mock_service.apply_budget_request = AsyncMock(side_effect=error)

        with pytest.raises(ExtendedHTTPException) as raised:
            await apply_servicenow_budget(payload=ServiceNowBudgetRequest(**_payload()), user=service_account, _=None)

        assert raised.value is error


class TestBudgetResponseTotal:
    @pytest.mark.parametrize(
        ("max_budgets", "float_sum", "expected"),
        [
            ({"platform": 10.0, "cli": 5.0}, 14.999999999999998, 15.0),
            ({"platform": 0.1, "cli": 0.2, "premium_models": 0.3}, 0.6000000000000001, 0.6),
        ],
    )
    def test_total_is_exact_decimal_sum_of_category_max_budgets(self, max_budgets, float_sum, expected):
        group = SimpleNamespace(
            group=SimpleNamespace(budget_duration="30d"),
            categories=[_category_result(category, amount, 0.0, None) for category, amount in max_budgets.items()],
            total_amount=float_sum,
        )

        response = _to_budget_response(_budget_result(budget_group=group))

        assert response.budget.total_amount == expected


PROJECTS_URL = "/v1/integrations/servicenow/projects"
BUDGETS_URL = "/v1/integrations/servicenow/budgets"
SERVICE_ACCOUNT_ID = "sn-sa"


def _http_service_account() -> User:
    return User(id=SERVICE_ACCOUNT_ID, email="sn@svc.example.com", user_type="service_account")


def _http_regular_user() -> User:
    return User(id="u-1", email="user@epam.com")


def _http_admin_user() -> User:
    return User(id="admin-1", email="admin@epam.com", is_admin=True)


@pytest.fixture
def http():
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    client = TestClient(app)

    def act_as(user: User) -> TestClient:
        def override(request: Request) -> User:
            request.state.user = user
            return user

        app.dependency_overrides[authenticate] = override
        return client

    with patch(CONFIG_PATH) as mock_config:
        mock_config.SERVICENOW_INTEGRATION_ENABLED = True
        mock_config.SERVICENOW_SERVICE_ACCOUNT_ID = SERVICE_ACCOUNT_ID
        yield act_as


def _get_projects(client: TestClient, emails: list[str]):
    return client.get(PROJECTS_URL, params=[("emails", email) for email in emails])


class TestHttpAccess:
    @pytest.mark.parametrize("user_factory", [_http_regular_user, _http_admin_user])
    def test_non_service_account_is_403_on_both_routes(self, http, mock_service, user_factory):
        client = http(user_factory())

        responses = [_get_projects(client, ["a@epam.com"]), client.post(BUDGETS_URL, json=_payload())]

        for response in responses:
            assert response.status_code == 403
            assert response.json()["error"]["message"] == ACCESS_DENIED_MESSAGE
        mock_service.list_projects.assert_not_called()
        mock_service.apply_budget_request.assert_not_called()

    def test_service_account_reaches_both_routes(self, http, mock_service):
        mock_service.list_projects = AsyncMock(return_value=[SimpleNamespace(name="epm-aimf", display_name=None)])
        mock_service.apply_budget_request = AsyncMock(return_value=_budget_result())
        client = http(_http_service_account())

        projects = _get_projects(client, [" A@EPAM.com "])
        budgets = client.post(BUDGETS_URL, json=_payload())

        assert projects.status_code == 200
        assert projects.json() == [{"project_name": "epm-aimf", "display_name": None}]
        mock_service.list_projects.assert_awaited_once_with(["a@epam.com"])
        assert budgets.status_code == 200
        assert budgets.json()["project_name"] == "Epm-AIMF"


class TestHttpValidation:
    def test_projects_without_emails_is_422(self, http, mock_service):
        response = _get_projects(http(_http_service_account()), [])

        assert response.status_code == 422
        assert response.json()["error"]["details"]
        mock_service.list_projects.assert_not_called()

    def test_many_emails_are_accepted_on_both_routes(self, http, mock_service):
        mock_service.list_projects = AsyncMock(return_value=[])
        mock_service.apply_budget_request = AsyncMock(return_value=_budget_result())
        emails = [f"u{i}@epam.com" for i in range(25)]
        client = http(_http_service_account())

        projects = _get_projects(client, emails)
        budgets = client.post(BUDGETS_URL, json=_payload(admin_emails=emails))

        assert projects.status_code == 200
        mock_service.list_projects.assert_awaited_once_with(emails)
        assert budgets.status_code == 200
        assert mock_service.apply_budget_request.await_args.args[1].admin_emails == emails

    def test_projects_invalid_email_is_422(self, http, mock_service):
        response = _get_projects(http(_http_service_account()), ["not-an-email"])

        assert response.status_code == 422
        mock_service.list_projects.assert_not_called()

    @pytest.mark.parametrize(
        "overrides",
        [
            {"action": "grow"},
            {"amounts": None},
            {"action": "adjust", "amounts": {}},
            {"amounts": {"gpu": 10.0}},
            {"amounts": {"platform": -1.0}},
            {"admin_emails": ["not-an-email"]},
            {"request_id": ""},
            {"request_id": "x" * 65},
            {"epam_project_code": "x" * 101},
            {"epam_project_code": "ab"},
            {"codemie_project_name": "ab"},
        ],
    )
    def test_invalid_budget_payload_is_422(self, http, mock_service, overrides):
        response = http(_http_service_account()).post(BUDGETS_URL, json=_payload(**overrides))

        assert response.status_code == 422
        assert set(response.json()["error"]) == {"message", "details"}
        mock_service.apply_budget_request.assert_not_called()

    @pytest.mark.parametrize("literal", ["Infinity", "NaN"])
    def test_non_finite_amount_is_422(self, http, mock_service, literal):
        body = (
            '{"action": "set", "request_id": "RITM1", "epam_project_code": "nbs-cmpa", '
            f'"amounts": {{"platform": 100, "cli": {literal}}}}}'
        )

        response = http(_http_service_account()).post(
            BUDGETS_URL, content=body, headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 422
        mock_service.apply_budget_request.assert_not_called()

    @pytest.mark.parametrize("code", [400, 403, 404, 409])
    def test_service_error_uses_structured_shape(self, http, mock_service, code):
        mock_service.apply_budget_request = AsyncMock(
            side_effect=ExtendedHTTPException(code=code, message="Rejected", details="why", help="fix it")
        )

        response = http(_http_service_account()).post(BUDGETS_URL, json=_payload())

        assert response.status_code == code
        assert response.json() == {"error": {"message": "Rejected", "details": "why", "help": "fix it"}}


class TestRouteWiring:
    @pytest.mark.parametrize(("path", "method"), [(PROJECTS_URL, "GET"), (BUDGETS_URL, "POST")])
    def test_route_declares_authenticate_and_servicenow_guard(self, path, method):
        route = next(
            route
            for route in router.routes
            if isinstance(route, APIRoute) and route.path == path and method in route.methods
        )

        dependency_calls = [dependency.call for dependency in route.dependant.dependencies]
        assert authenticate in dependency_calls
        assert servicenow_integration_access_only in dependency_calls
        assert dependency_calls.index(authenticate) < dependency_calls.index(servicenow_integration_access_only)


class TestAllowlistMatchesRouter:
    def test_allowlist_is_exactly_the_two_integration_routes(self):
        _allowed_routes.cache_clear()

        assert _allowed_routes() == {
            ("GET", "/v1/integrations/servicenow/projects"),
            ("POST", "/v1/integrations/servicenow/budgets"),
        }


class TestRealAuthenticateScope:
    OTHER_URL = "/v1/other"

    @pytest.fixture
    def real_auth(self, mock_service):
        app = FastAPI()
        app.include_router(router)
        app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
        app.add_exception_handler(RequestValidationError, validation_exception_handler)

        @app.get(self.OTHER_URL)
        async def other(user: User = Depends(authenticate)) -> dict:
            return {"user_id": user.id}

        mock_service.list_projects = AsyncMock(return_value=[SimpleNamespace(name="epm-aimf", display_name=None)])
        mock_service.apply_budget_request = AsyncMock(return_value=_budget_result())
        client = TestClient(app)
        servicenow_access._allowed_routes.cache_clear()

        with (
            ExitStack() as stack,
            patch("codemie.rest_api.security.authentication.IdpFactory"),
            patch("codemie.rest_api.security.teams_authentication_resolver.is_teams_bot_request", return_value=False),
            patch(CONFIG_PATH) as mock_config,
        ):
            mock_config.SERVICENOW_INTEGRATION_ENABLED = True
            mock_config.SERVICENOW_SERVICE_ACCOUNT_ID = SERVICE_ACCOUNT_ID

            def act_as(user: User) -> TestClient:
                provider = MagicMock()
                provider.authenticate_and_load_user = AsyncMock(return_value=user)
                stack.enter_context(
                    patch("codemie.rest_api.security.authentication.get_user_provider", return_value=provider)
                )
                return client

            yield act_as
        servicenow_access._allowed_routes.cache_clear()

    def test_service_account_reaches_integration_routes_but_not_others(self, real_auth):
        client = real_auth(User(id=SERVICE_ACCOUNT_ID, email="sn@svc.example.com", user_type="service_account"))

        projects = _get_projects(client, ["a@epam.com"])
        budgets = client.post(BUDGETS_URL, json=_payload(action="cancel", amounts=None))
        other = client.get(self.OTHER_URL)

        assert projects.status_code == 200
        assert budgets.status_code == 200
        assert other.status_code == 403
        assert other.json()["error"]["message"] == ACCESS_DENIED_MESSAGE

    def test_regular_user_is_blocked_from_integration_routes_but_not_others(self, real_auth):
        client = real_auth(_http_regular_user())

        projects = _get_projects(client, ["a@epam.com"])
        budgets = client.post(BUDGETS_URL, json=_payload(action="cancel", amounts=None))
        other = client.get(self.OTHER_URL)

        assert projects.status_code == 403
        assert budgets.status_code == 403
        assert other.status_code == 200
        assert other.json() == {"user_id": "u-1"}
