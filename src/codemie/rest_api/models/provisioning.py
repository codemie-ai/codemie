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

from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
)

from codemie.service.budget.budget_enums import BudgetCategory

# Project and cost center names share one rule: 3-100 characters, used exactly as sent. Never coerce them:
# the permission and lookup decisions must run on the value the caller sent (security-patterns.md).
ProvisioningProjectName = Annotated[str, StringConstraints(min_length=3, max_length=100)]


class ProvisioningProjectResponse(BaseModel):
    project_name: str = Field(
        description=(
            "CodeMie project name as stored (unique). A name not created by this API, such as a legacy one, "
            "may have upper-case letters."
        ),
        examples=["epm-aimf"],
    )
    display_name: str | None = Field(
        default=None,
        description="Human-readable project name, or null when the project has none.",
        examples=["AI Multimodal Framework"],
    )


class ProvisioningProjectBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cost_center: ProvisioningProjectName | None = Field(
        default=None,
        description=(
            "Cost center to link the project to while it has no active one (none, or a deleted one). "
            "Ignored when the project is linked to an active cost center; otherwise it must match "
            "`^[a-z0-9]+-[a-z0-9]+$`, else 400."
        ),
        examples=["eng-123"],
    )
    admin_emails: list[EmailStr] = Field(
        default_factory=list,
        description=(
            "People to make project admins. Matched to users case-insensitively, "
            "so an email repeated in any case counts once, as first sent."
        ),
        examples=[["requester@example.com"]],
    )

    @field_validator("admin_emails", mode="after")
    @classmethod
    def _deduplicate_emails(cls, emails: list[str]) -> list[str]:
        # Keyed like the user lookup (lower-cased); the value kept is the first spelling, never case-folded.
        first_spellings: dict[str, str] = {}
        for email in emails:
            first_spellings.setdefault(email.lower(), email)
        return list(first_spellings.values())


class ProvisioningBudgetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amounts: dict[BudgetCategory, Annotated[float, Field(ge=0, allow_inf_nan=False)]] = Field(
        min_length=1,
        description="Monthly budget per category in USD (platform, cli, premium_models). At least one category.",
        examples=[{"platform": 500, "cli": 100, "premium_models": 50}],
    )
    project: ProvisioningProjectBlock | None = Field(
        default=None,
        description="Project settings applied together with the budget. Optional.",
    )


class ProvisioningBudgetSetRequest(ProvisioningBudgetRequest):
    @field_validator("amounts", mode="after")
    @classmethod
    def _require_positive_platform(cls, amounts: dict[BudgetCategory, float]) -> dict[BudgetCategory, float]:
        if amounts.get(BudgetCategory.PLATFORM, 0) <= 0:
            raise ValueError("platform must be greater than 0")
        return amounts


class ProvisioningBudgetCategoryResponse(BaseModel):
    category: BudgetCategory = Field(description="Budget category.", examples=["platform"])
    max_budget: float = Field(description="Hard monthly limit in USD.", examples=[500.0])
    soft_budget: float = Field(description="Monthly soft limit in USD.", examples=[400.0])
    budget_reset_at: str | None = Field(
        default=None,
        description="When the budget next resets (ISO 8601), or null when not yet known.",
        examples=["2026-11-18T00:00:00Z"],
    )


class ProvisioningBudgetGroupResponse(BaseModel):
    total_amount: float = Field(description="Sum of the category limits in USD.", examples=[650.0])
    budget_duration: str = Field(description="Budget period.", examples=["30d"])
    categories: list[ProvisioningBudgetCategoryResponse] = Field(description="Budget state per category.")


class ProvisioningBudgetResponse(BaseModel):
    project_name: str = Field(
        description=(
            "CodeMie project name as stored (unique). A name not created by this API, such as a legacy one, "
            "may have upper-case letters."
        ),
        examples=["epm-aimf"],
    )
    cost_center: str | None = Field(
        description="Name of the cost center the project is linked to after the call, or null.",
        examples=["eng-123"],
    )
    created_project: bool = Field(description="True when the call created the project.")
    created_cost_center: bool = Field(description="True when the call created the cost center.")
    created_budget: bool = Field(description="True when the call created the budget.")
    budget: ProvisioningBudgetGroupResponse = Field(description="The project budget after the call.")
    admins_assigned: list[str] = Field(
        description="Requested emails whose user is a project admin after the call, including existing admins.",
        examples=[["approver@example.com"]],
    )
    admins_not_assigned: list[str] = Field(
        description="Requested emails with no active, non-service-account user, who therefore were not made admins.",
        examples=[["requester@example.com"]],
    )


class ProvisioningValidationErrorBody(BaseModel):
    message: str = Field(description="Summary of the first validation problems.")
    details: list[dict[str, Any]] = Field(description="One entry per validation problem: loc, msg and type.")


class ProvisioningValidationErrorResponse(BaseModel):
    """Body of the platform's 422 answer, documented for OpenAPI only."""

    error: ProvisioningValidationErrorBody
