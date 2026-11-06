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

"""Audited field sets and change-attribute builders for budget activity events."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from codemie.service.activity.activity_attributes import build_change_attributes

if TYPE_CHECKING:
    from codemie.rest_api.models.project_budget import ProjectBudgetGroupUpdateRequest

_CENT = Decimal("0.01")

BUDGET_AUDIT_VALUE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "name",
        "soft_budget",
        "max_budget",
        "budget_duration",
        "budget_category",
        "soft_limit_notify_once",
        "soft_limit_notification_enabled",
    }
)
BUDGET_AUDIT_NAME_ONLY_FIELDS: Final[frozenset[str]] = frozenset({"description", "notification_owner_email"})

PROJECT_BUDGET_AUDIT_VALUE_FIELDS: Final[frozenset[str]] = frozenset(
    {"name", "soft_budget", "max_budget", "budget_duration"}
)
PROJECT_BUDGET_AUDIT_NAME_ONLY_FIELDS: Final[frozenset[str]] = frozenset({"description"})

_GROUP_AUDIT_REQUEST_FIELDS: Final[tuple[str, ...]] = (
    "name",
    "budget_duration",
    "description",
    "notification_owner_email",
    "soft_limit_notify_once",
    "soft_limit_notification_enabled",
)
GROUP_AUDIT_VALUE_FIELDS: Final[frozenset[str]] = frozenset(
    {"name", "budget_duration", "total_amount", "soft_limit_notify_once", "soft_limit_notification_enabled"}
)
GROUP_AUDIT_NAME_ONLY_FIELDS: Final[frozenset[str]] = frozenset(
    {"description", "notification_owner_email", "categories"}
)


def round_amount(amount: float) -> float:
    return float(Decimal(str(amount)).quantize(_CENT))


def project_budget_update_attributes(
    previous: Mapping[str, Any],
    changed_fields: Mapping[str, Any],
    *,
    new_models: list[str] | None,
    project_name: str | None,
) -> dict[str, Any]:
    """Diff a project budget update; `previous["models"]` is compared only when the key is present."""
    attributes = build_change_attributes(
        previous,
        changed_fields,
        value_fields=PROJECT_BUDGET_AUDIT_VALUE_FIELDS,
        name_only_fields=PROJECT_BUDGET_AUDIT_NAME_ONLY_FIELDS,
    )
    if new_models is not None:
        _add_models_change(attributes, previous, new_models)
    if project_name is not None:
        attributes["project_name"] = project_name
    return attributes


def _add_models_change(attributes: dict[str, Any], previous: Mapping[str, Any], new_models: list[str]) -> None:
    if "models" in previous:
        previous_models = list(previous["models"] or [])
        if sorted(previous_models) == sorted(new_models):
            return
        attributes["previous_models"] = previous_models
    attributes["updated_fields"] = sorted([*attributes["updated_fields"], "models"])
    attributes["new_models"] = list(new_models)


def group_category_audit_spec(pct: float, soft_budget: float) -> dict[str, float]:
    return {"pct": round_amount(pct), "soft_budget": round_amount(soft_budget)}


def group_update_attributes(
    data: ProjectBudgetGroupUpdateRequest,
    previous: Mapping[str, Any],
    *,
    new_total_amount: float | None = None,
    new_categories: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Diff a group update; amounts and categories are audited only when the caller applied them."""
    requested: dict[str, Any] = {
        field: getattr(data, field, None)
        for field in _GROUP_AUDIT_REQUEST_FIELDS
        if getattr(data, field, None) is not None
    }
    if new_total_amount is not None:
        requested["total_amount"] = round_amount(new_total_amount)
    if new_categories is not None:
        requested["categories"] = dict(new_categories)
    return build_change_attributes(
        previous,
        requested,
        value_fields=GROUP_AUDIT_VALUE_FIELDS,
        name_only_fields=GROUP_AUDIT_NAME_ONLY_FIELDS,
    )
