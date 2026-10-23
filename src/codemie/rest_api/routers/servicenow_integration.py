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

from decimal import Decimal

from fastapi import APIRouter, Body, Depends, Query

from codemie.rest_api.models.analytics import ErrorResponse
from codemie.rest_api.models.servicenow_integration import (
    NormalizedEmail,
    ServiceNowBudgetCategoryResponse,
    ServiceNowBudgetGroupResponse,
    ServiceNowBudgetRequest,
    ServiceNowBudgetResponse,
    ServiceNowProjectResponse,
)
from codemie.rest_api.security.authentication import authenticate, servicenow_integration_access_only
from codemie.rest_api.security.user import User
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.servicenow_integration_service import ServiceNowBudgetResult, servicenow_integration_service

router = APIRouter(
    tags=["ServiceNow Integration"],
    prefix="/v1/integrations/servicenow",
    dependencies=[],
)

_CATEGORY_ORDER = {category.value: position for position, category in enumerate(BudgetCategory)}


def _to_budget_response(result: ServiceNowBudgetResult) -> ServiceNowBudgetResponse:
    group = result.budget_group
    budget = None
    if group is not None:
        budgets = sorted(
            (category_result.budget for category_result in group.categories),
            key=lambda item: _CATEGORY_ORDER[item.budget_category],
        )
        budget = ServiceNowBudgetGroupResponse(
            total_amount=float(sum(Decimal(str(item.max_budget)) for item in budgets)),
            budget_duration=group.group.budget_duration,
            categories=[
                ServiceNowBudgetCategoryResponse(
                    category=BudgetCategory(item.budget_category),
                    max_budget=item.max_budget,
                    soft_budget=item.soft_budget,
                    budget_reset_at=item.budget_reset_at,
                )
                for item in budgets
            ],
        )
    return ServiceNowBudgetResponse(
        project_name=result.project_name,
        cost_center=result.cost_center,
        created_project=result.created_project,
        created_cost_center=result.created_cost_center,
        budget=budget,
        admins_assigned=result.admins_assigned,
        admins_pending=result.admins_pending,
    )


_PROJECTS_DESCRIPTION = """\
Returns the CodeMie projects that any of the given users belong to. Personal projects are excluded.

The result is the deduplicated union across all emails, sorted by project name. \
Emails with no active CodeMie user are skipped; if no email matches, the response is an empty list.

Callable only by the ServiceNow integration service account.
"""

_BUDGETS_DESCRIPTION = """\
Applies an approved ServiceNow request to a project's monthly budget.

| `action` | Missing project | Missing budget | Existing budget |
|---|---|---|---|
| `set` | created (+ cost center when the names differ) | created | **replaced**: categories not sent are removed |
| `adjust` | created (+ cost center when the names differ) | created from the request | \
**merged**: categories not sent are kept; `0` removes one |
| `cancel` | 404 | nothing to do (200) | deleted |

- `set` and `adjust` make every `admin_emails` user a project admin. Unknown and service-account emails \
are reported in `admins_pending`. `cancel` ignores `admin_emails` and `amounts`.
- Every check runs before anything is written: a request that fails changes nothing.
- An existing project is never renamed, and its cost center link never changes.
- Requests are safe to retry: the same input produces the same final state.

Callable only by the ServiceNow integration service account.
"""

_BUDGET_REQUEST_EXAMPLES = {
    "set": {
        "summary": "Set a new budget",
        "value": {
            "action": "set",
            "request_id": "RITM0012345",
            "epam_project_code": "nbs-cmpa",
            "codemie_project_name": "epm-aimf",
            "admin_emails": ["requester@epam.com", "approver@epam.com"],
            "amounts": {"platform": 500, "cli": 100, "premium_models": 50},
        },
    },
    "adjust": {
        "summary": "Adjust one category",
        "value": {
            "action": "adjust",
            "request_id": "RITM0012346",
            "epam_project_code": "nbs-cmpa",
            "codemie_project_name": "epm-aimf",
            "amounts": {"cli": 200},
        },
    },
    "cancel": {
        "summary": "Cancel the budget",
        "value": {
            "action": "cancel",
            "request_id": "RITM0012347",
            "epam_project_code": "nbs-cmpa",
            "codemie_project_name": "epm-aimf",
        },
    },
}


@router.get(
    "/projects",
    response_model=list[ServiceNowProjectResponse],
    summary="List projects for the given users",
    description=_PROJECTS_DESCRIPTION,
    responses={
        403: {"model": ErrorResponse, "description": "The caller is not the ServiceNow integration service account."},
        422: {"description": "Invalid query: no emails or an invalid email."},
    },
)
async def list_servicenow_projects(
    emails: list[NormalizedEmail] = Query(
        description="Requester and approver emails. Letter case and surrounding spaces are ignored.",
        examples=["requester@epam.com"],
    ),
    user: User = Depends(authenticate),
    _: None = Depends(servicenow_integration_access_only),
) -> list[ServiceNowProjectResponse]:
    projects = await servicenow_integration_service.list_projects(emails)
    return [
        ServiceNowProjectResponse(project_name=project.name, display_name=project.display_name) for project in projects
    ]


@router.post(
    "/budgets",
    response_model=ServiceNowBudgetResponse,
    summary="Apply a ServiceNow budget request to a project",
    description=_BUDGETS_DESCRIPTION,
    responses={
        400: {
            "model": ErrorResponse,
            "description": (
                "A project or cost center name to be created is invalid, or an adjust would leave platform at 0."
            ),
        },
        403: {
            "model": ErrorResponse,
            "description": (
                "The caller is not the ServiceNow service account, the project is a personal project, "
                "or a cost center is needed while cost centers are disabled."
            ),
        },
        404: {"model": ErrorResponse, "description": "cancel on a project that doesn't exist."},
        409: {"model": ErrorResponse, "description": "The name belongs to a deleted project or cost center."},
        422: {
            "description": (
                "Invalid request body (unknown category, negative or non-finite amount, missing amounts, "
                "set without platform, invalid email)."
            )
        },
    },
)
async def apply_servicenow_budget(
    payload: ServiceNowBudgetRequest = Body(openapi_examples=_BUDGET_REQUEST_EXAMPLES),
    user: User = Depends(authenticate),
    _: None = Depends(servicenow_integration_access_only),
) -> ServiceNowBudgetResponse:
    result = await servicenow_integration_service.apply_budget_request(user, payload)
    return _to_budget_response(result)
