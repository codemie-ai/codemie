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

import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, TypeAdapter, ValidationError

from codemie.rest_api.models.provisioning import (
    ProvisioningBudgetRequest,
    ProvisioningBudgetResponse,
    ProvisioningBudgetSetRequest,
    ProvisioningProjectName,
    ProvisioningValidationErrorResponse,
)

VENDOR_MARKERS = ("servicenow", "epam_project_code", "action")


def _payload(**overrides: Any) -> dict[str, Any]:
    return {"amounts": {"platform": 500.0}} | overrides


def _response_payload(**overrides: Any) -> dict[str, Any]:
    return {
        "project_name": "epm-aimf",
        "cost_center": "nbs-cmpa",
        "created_project": True,
        "created_cost_center": False,
        "created_budget": True,
        "budget": {
            "total_amount": 500.0,
            "budget_duration": "30d",
            "categories": [
                {
                    "category": "platform",
                    "max_budget": 500.0,
                    "soft_budget": 400.0,
                    "budget_reset_at": "2026-11-18T00:00:00Z",
                }
            ],
        },
        "admins_assigned": ["approver@epam.com"],
        "admins_not_assigned": ["requester@epam.com"],
    } | overrides


class TestProvisioningProjectName:
    def test_is_kept_exactly_as_sent(self):
        assert TypeAdapter(ProvisioningProjectName).validate_python(" EPM-Aimf ") == " EPM-Aimf "

    @pytest.mark.parametrize("name", ["ab", "a ", "x" * 101, ""])
    def test_length_outside_three_to_hundred_is_rejected(self, name):
        with pytest.raises(ValidationError):
            TypeAdapter(ProvisioningProjectName).validate_python(name)

    def test_is_kept_exactly_as_sent_when_used_as_a_path_parameter(self):
        app = FastAPI()

        @app.get("/projects/{project_name}")
        def read(project_name: ProvisioningProjectName) -> dict[str, str]:
            return {"project_name": project_name}

        client = TestClient(app)

        assert client.get("/projects/%20EPM-AIMF%20").json() == {"project_name": " EPM-AIMF "}
        assert client.get("/projects/ab").status_code == 422


class TestProvisioningBudgetRequest:
    def test_project_block_is_optional(self):
        assert ProvisioningBudgetRequest(**_payload()).project is None

    def test_empty_project_block_is_valid_and_names_nothing(self):
        request = ProvisioningBudgetRequest.model_validate(_payload(project={}))

        assert request.project is not None
        assert request.project.cost_center is None
        assert request.project.admin_emails == []

    def test_project_block_with_only_admin_emails_is_valid(self):
        request = ProvisioningBudgetRequest.model_validate(_payload(project={"admin_emails": ["a@epam.com"]}))

        assert request.project.cost_center is None
        assert request.project.admin_emails == ["a@epam.com"]

    def test_case_variants_of_an_email_are_dropped_keeping_the_first_spelling_as_sent(self):
        request = ProvisioningBudgetRequest.model_validate(
            _payload(
                project={
                    "admin_emails": ["Requester@epam.com", "requester@epam.com", "Other@epam.com", "REQUESTER@epam.com"]
                }
            )
        )

        assert request.project.admin_emails == ["Requester@epam.com", "Other@epam.com"]

    def test_cost_center_is_kept_exactly_as_sent(self):
        request = ProvisioningBudgetRequest.model_validate(_payload(project={"cost_center": " ENG-123 "}))

        assert request.project.cost_center == " ENG-123 "

    def test_amounts_are_keyed_by_budget_category(self):
        request = ProvisioningBudgetRequest(**_payload(amounts={"platform": 500, "cli": 0, "premium_models": 50.5}))

        assert {category.value: amount for category, amount in request.amounts.items()} == {
            "platform": 500.0,
            "cli": 0.0,
            "premium_models": 50.5,
        }

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"amounts": None},
            {"amounts": {}},
            {"amounts": {"gpu": 10.0}},
            {"amounts": {"platform": -1.0}},
            {"amounts": {"platform": float("inf")}},
            {"amounts": {"platform": 100.0, "cli": float("inf")}},
            {"amounts": {"platform": float("nan")}},
            {"amounts": {"platform": 500.0}, "unknown": 1},
            {"amounts": {"platform": 500.0}, "project": {"unknown": 1}},
            {"amounts": {"platform": 500.0}, "project": {"admin_emails": ["not-an-email"]}},
            {"amounts": {"platform": 500.0}, "project": {"cost_center": "ab"}},
            {"amounts": {"platform": 500.0}, "project": {"cost_center": ""}},
        ],
    )
    def test_invalid_payload_is_rejected(self, payload):
        with pytest.raises(ValidationError):
            ProvisioningBudgetRequest.model_validate(payload)

    def test_adjust_accepts_zero_platform_and_missing_platform(self):
        assert ProvisioningBudgetRequest(**_payload(amounts={"platform": 0.0})).amounts
        assert ProvisioningBudgetRequest(**_payload(amounts={"cli": 10.0})).amounts


class TestProvisioningBudgetSetRequest:
    def test_positive_platform_is_accepted(self):
        assert ProvisioningBudgetSetRequest(**_payload(amounts={"platform": 0.01, "cli": 0.0})).amounts

    @pytest.mark.parametrize("amounts", [{"platform": 0.0}, {"platform": 0.0, "cli": 10.0}, {"cli": 10.0}])
    def test_set_without_positive_platform_is_rejected(self, amounts):
        with pytest.raises(ValidationError, match="platform must be greater than 0"):
            ProvisioningBudgetSetRequest(**_payload(amounts=amounts))

    def test_inherits_the_request_rules(self):
        with pytest.raises(ValidationError):
            ProvisioningBudgetSetRequest(**_payload(unknown=1))


class TestProvisioningBudgetResponse:
    def test_parses_the_documented_shape(self):
        response = ProvisioningBudgetResponse.model_validate(_response_payload())

        assert response.created_budget is True
        assert response.admins_not_assigned == ["requester@epam.com"]
        assert response.budget.categories[0].category.value == "platform"
        assert response.model_dump(mode="json") == _response_payload()

    def test_cost_center_may_be_null(self):
        assert ProvisioningBudgetResponse.model_validate(_response_payload(cost_center=None)).cost_center is None

    def test_budget_is_required_because_only_cancel_has_none(self):
        payload = _response_payload()
        del payload["budget"]

        with pytest.raises(ValidationError):
            ProvisioningBudgetResponse.model_validate(payload)

    def test_budget_reset_time_may_be_unknown(self):
        payload = _response_payload()
        payload["budget"]["categories"][0]["budget_reset_at"] = None

        assert ProvisioningBudgetResponse.model_validate(payload).budget.categories[0].budget_reset_at is None


class TestProvisioningValidationErrorResponse:
    def test_matches_the_platform_validation_error_body(self):
        body = {
            "error": {
                "message": "Invalid request: amounts: Field required",
                "details": [{"loc": ["body", "amounts"], "msg": "Field required", "type": "missing"}],
            }
        }

        assert ProvisioningValidationErrorResponse.model_validate(body).model_dump() == body


class TestCallerNeutralSchema:
    @pytest.mark.parametrize(
        "model",
        [
            ProvisioningBudgetRequest,
            ProvisioningBudgetSetRequest,
            ProvisioningBudgetResponse,
            ProvisioningValidationErrorResponse,
        ],
    )
    def test_schema_names_no_vendor_and_no_action(self, model: type[BaseModel]):
        schema_text = json.dumps(model.model_json_schema()).lower()

        assert [marker for marker in VENDOR_MARKERS if marker in schema_text] == []

    @pytest.mark.parametrize("model", [ProvisioningBudgetRequest, ProvisioningBudgetSetRequest])
    def test_request_schema_forbids_extra_fields(self, model: type[BaseModel]):
        schema = model.model_json_schema()

        assert schema["additionalProperties"] is False
        assert schema["$defs"]["ProvisioningProjectBlock"]["additionalProperties"] is False
