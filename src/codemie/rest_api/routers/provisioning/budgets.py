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
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Response, status

from codemie.configs.provisioning_clients_config import ProvisioningClient, ProvisioningPermission
from codemie.rest_api.models.analytics import ErrorResponse
from codemie.rest_api.models.provisioning import (
    ProvisioningBudgetCategoryResponse,
    ProvisioningBudgetGroupResponse,
    ProvisioningBudgetRequest,
    ProvisioningBudgetResponse,
    ProvisioningBudgetSetRequest,
    ProvisioningProjectName,
    ProvisioningValidationErrorResponse,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.provisioning_access import RequirePermission
from codemie.rest_api.security.user import User
from codemie.service.provisioning_budget_service import ProvisioningBudgetResult, provisioning_budget_service

router = APIRouter()

ProjectNamePath = Annotated[
    ProvisioningProjectName,
    Path(
        description=(
            "CodeMie project name, used exactly as sent; 3-100 characters. An existing project is matched "
            "regardless of case and the response names it as stored. A project the call creates needs a name "
            "matching `^[a-z0-9][a-z0-9_-]*$` that is not reserved, else 400."
        ),
        examples=["epm-aimf"],
    ),
]
ChangeReference = Annotated[
    str,
    Header(
        alias="X-Change-Reference",
        min_length=1,
        max_length=64,
        description="The caller's reference for this change. It names a new budget and is logged. 1-64 characters.",
        examples=["CHG0012345"],
    ),
]

_BUDGET_URL = "/projects/{project_name}/budget"

_SET_DESCRIPTION = """\
Replaces the amounts of the project's budget with the given ones, creating the project and the budget when missing. \
A category left out gets 0; `platform` must be above 0. \
An existing budget keeps its period; a new budget gets a 30-day period.

The optional `project` block links the project to a cost center while it has no active one \
and makes the listed people project admins.

Answers 201 when the call created the budget and 200 when it replaced one. \
Every check runs before anything is written, so a 4xx means nothing changed. \
After a 500 or 502 the call is partly applied: send the same request again.

Callable only by registered provisioning clients holding `provisioning:manage-budgets`. \
A call that would create the project, or that names a cost center or admins, also needs \
`provisioning:manage-projects`.
"""

_ADJUST_DESCRIPTION = """\
Merges the given amounts into the project's budget: a category listed changes, \
a category left out keeps its amount, and 0 removes a category. \
The merged `platform` amount must stay above 0. Creates the project and the budget when missing.

Always answers 200. Every check runs before anything is written, so a 4xx means nothing changed. \
After a 500 or 502 the call is partly applied: send the same request again.

Callable only by registered provisioning clients holding `provisioning:manage-budgets`. \
A call that would create the project, or that names a cost center or admins, also needs \
`provisioning:manage-projects`.
"""

_CANCEL_DESCRIPTION = """\
Deletes the project's budget. A project without a budget is not an error: the answer is still 204. \
A project that does not exist is 404.

Every check runs before anything is written, so a 4xx means nothing changed. \
After a 500 send the same request again.

Callable only by registered provisioning clients holding `provisioning:manage-budgets`.
"""

_WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {
        "model": ErrorResponse,
        "description": (
            "The amounts do not make a valid budget, the new project's name is invalid, "
            "or the cost center name is invalid."
        ),
    },
    403: {
        "model": ErrorResponse,
        "description": (
            "The caller is not a registered provisioning client holding provisioning:manage-budgets, "
            "or the call would create the project or carries a project block naming a cost center or admins "
            "and the client lacks provisioning:manage-projects, or the project is a personal one, "
            "or cost centers are disabled and the call would create or link a cost center."
        ),
    },
    409: {"model": ErrorResponse, "description": "The project or the named cost center is deleted."},
    422: {
        "model": ProvisioningValidationErrorResponse,
        "description": "Invalid project name, X-Change-Reference header or body.",
    },
    500: {"model": ErrorResponse, "description": "The call was partly applied; send the same request again."},
    502: {
        "model": ErrorResponse,
        "description": "The budget provider sync failed; the call was partly applied. Send the same request again.",
    },
}
_CANCEL_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {
        "model": ErrorResponse,
        "description": (
            "The caller is not a registered provisioning client holding provisioning:manage-budgets, "
            "or the project is a personal one."
        ),
    },
    404: {"model": ErrorResponse, "description": "The project does not exist."},
    409: {"model": ErrorResponse, "description": "The project is deleted."},
    422: {
        "model": ProvisioningValidationErrorResponse,
        "description": "Invalid project name or X-Change-Reference header.",
    },
    500: {"model": ErrorResponse, "description": "The cancel did not complete; send the same request again."},
}


def _to_response(result: ProvisioningBudgetResult) -> ProvisioningBudgetResponse:
    group = result.budget_group
    return ProvisioningBudgetResponse(
        project_name=result.project_name,
        cost_center=result.cost_center,
        created_project=result.created_project,
        created_cost_center=result.created_cost_center,
        created_budget=result.created_budget,
        budget=ProvisioningBudgetGroupResponse(
            total_amount=float(sum(Decimal(str(category.budget.max_budget)) for category in group.categories)),
            budget_duration=group.group.budget_duration,
            categories=[
                ProvisioningBudgetCategoryResponse(
                    category=category.budget.budget_category,
                    max_budget=category.budget.max_budget,
                    soft_budget=category.budget.soft_budget,
                    budget_reset_at=category.budget.budget_reset_at,
                )
                for category in group.categories
            ],
        ),
        admins_assigned=result.admins_assigned,
        admins_not_assigned=result.admins_not_assigned,
    )


@router.put(
    _BUDGET_URL,
    response_model=ProvisioningBudgetResponse,
    summary="Set a project's budget",
    description=_SET_DESCRIPTION,
    response_description="The call replaced the existing budget.",
    responses={
        201: {"model": ProvisioningBudgetResponse, "description": "The call created the budget."},
        **_WRITE_RESPONSES,
    },
)
async def set_project_budget(
    response: Response,
    project_name: ProjectNamePath,
    change_reference: ChangeReference,
    body: ProvisioningBudgetSetRequest,
    actor: User = Depends(authenticate),
    client: ProvisioningClient = Depends(RequirePermission(ProvisioningPermission.MANAGE_BUDGETS)),
) -> ProvisioningBudgetResponse:
    result = await provisioning_budget_service.set_budget(
        actor=actor, client=client, reference=change_reference, project_name=project_name, request=body
    )
    response.status_code = status.HTTP_201_CREATED if result.created_budget else status.HTTP_200_OK
    return _to_response(result)


@router.patch(
    _BUDGET_URL,
    response_model=ProvisioningBudgetResponse,
    summary="Adjust a project's budget",
    description=_ADJUST_DESCRIPTION,
    responses=_WRITE_RESPONSES,
)
async def adjust_project_budget(
    project_name: ProjectNamePath,
    change_reference: ChangeReference,
    body: ProvisioningBudgetRequest,
    actor: User = Depends(authenticate),
    client: ProvisioningClient = Depends(RequirePermission(ProvisioningPermission.MANAGE_BUDGETS)),
) -> ProvisioningBudgetResponse:
    result = await provisioning_budget_service.adjust_budget(
        actor=actor, client=client, reference=change_reference, project_name=project_name, request=body
    )
    return _to_response(result)


@router.delete(
    _BUDGET_URL,
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel a project's budget",
    description=_CANCEL_DESCRIPTION,
    responses=_CANCEL_RESPONSES,
)
async def cancel_project_budget(
    project_name: ProjectNamePath,
    change_reference: ChangeReference,
    actor: User = Depends(authenticate),
    client: ProvisioningClient = Depends(RequirePermission(ProvisioningPermission.MANAGE_BUDGETS)),
) -> None:
    await provisioning_budget_service.cancel_budget(
        actor=actor, client=client, reference=change_reference, project_name=project_name
    )
