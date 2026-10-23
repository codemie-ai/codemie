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

from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, EmailStr, Field, field_validator, model_validator

from codemie.service.budget.budget_enums import BudgetCategory


def _strip_and_lowercase(value: Any) -> Any:
    return value.strip().lower() if isinstance(value, str) else value


NormalizedEmail = Annotated[EmailStr, BeforeValidator(_strip_and_lowercase)]


class ServiceNowBudgetAction(str, Enum):
    """Budget action requested by ServiceNow."""

    SET = "set"
    ADJUST = "adjust"
    CANCEL = "cancel"


class ServiceNowBudgetRequest(BaseModel):
    action: ServiceNowBudgetAction = Field(
        description="set replaces the budget, adjust merges into it, cancel deletes it."
    )
    request_id: str = Field(
        min_length=1,
        max_length=64,
        description="ServiceNow request number, used for audit logs and the name of a newly created budget.",
        examples=["RITM0012345"],
    )
    epam_project_code: str = Field(
        min_length=3,
        max_length=100,
        description=(
            "EPAM project code. Used as the CodeMie project name when codemie_project_name is omitted; "
            "when the two differ, it names the cost center a new project is linked to. Case-insensitive."
        ),
        examples=["nbs-cmpa"],
    )
    codemie_project_name: str | None = Field(
        default=None,
        min_length=3,
        max_length=100,
        description="CodeMie project to manage. Optional; defaults to epam_project_code. Case-insensitive.",
        examples=["epm-aimf"],
    )
    admin_emails: list[NormalizedEmail] = Field(
        default_factory=list,
        description="People to make project admins (requester and approvers). Ignored by cancel.",
    )
    amounts: dict[BudgetCategory, Annotated[float, Field(ge=0, allow_inf_nan=False)]] | None = Field(
        default=None,
        description=(
            "Monthly budget per category in USD (platform, cli, premium_models). "
            "Required for set and adjust; set must include platform > 0. Ignored by cancel."
        ),
        examples=[{"platform": 500, "cli": 100, "premium_models": 50}],
    )

    @field_validator("epam_project_code", "codemie_project_name", mode="before")
    @classmethod
    def _normalize_names(cls, value: Any) -> Any:
        return _strip_and_lowercase(value)

    @field_validator("admin_emails", mode="after")
    @classmethod
    def _deduplicate_emails(cls, emails: list[str]) -> list[str]:
        return list(dict.fromkeys(emails))

    @model_validator(mode="after")
    def _validate_action_payload(self) -> ServiceNowBudgetRequest:
        if self.codemie_project_name is None:
            self.codemie_project_name = self.epam_project_code
        if self.action == ServiceNowBudgetAction.CANCEL:
            self.amounts = None
            self.admin_emails = []
            return self
        if not self.amounts:
            raise ValueError("amounts is required for set and adjust")
        if self.action == ServiceNowBudgetAction.SET and self.amounts.get(BudgetCategory.PLATFORM, 0) <= 0:
            raise ValueError("platform must be greater than 0 for set")
        return self


class ServiceNowProjectResponse(BaseModel):
    project_name: str = Field(description="CodeMie project name (unique, lowercase).", examples=["epm-aimf"])
    display_name: str | None = Field(
        default=None,
        description="Human-readable project name, or null when the project has none.",
        examples=["AI Multimodal Framework"],
    )


class ServiceNowBudgetCategoryResponse(BaseModel):
    category: BudgetCategory = Field(description="Budget category.")
    max_budget: float = Field(description="Monthly limit.")
    soft_budget: float = Field(description="Warning threshold; 80% of the limit unless one was already set.")
    budget_reset_at: str | None = Field(default=None, description="Next reset time, or null.")


class ServiceNowBudgetGroupResponse(BaseModel):
    total_amount: float = Field(description="Sum of all category budgets.")
    budget_duration: str = Field(description="Budget period; 30d for budgets created by this integration.")
    categories: list[ServiceNowBudgetCategoryResponse] = Field(description="Budget per category.")


class ServiceNowBudgetResponse(BaseModel):
    project_name: str = Field(description="Project the request was applied to, as stored in CodeMie.")
    cost_center: str | None = Field(description="Cost center the project is linked to, or null.")
    created_project: bool = Field(description="Whether this request created the project.")
    created_cost_center: bool = Field(description="Whether this request created the cost center.")
    budget: ServiceNowBudgetGroupResponse | None = Field(description="Budget after the request, or null after cancel.")
    admins_assigned: list[str] = Field(
        description=(
            "Requested emails whose users are project admins after the request, " + "including those who already were."
        )
    )
    admins_pending: list[str] = Field(
        description=(
            "Requested emails that couldn't be assigned: no active CodeMie user "
            "(they haven't signed in yet) or a service account."
        )
    )
