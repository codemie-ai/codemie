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


class BudgetCategory(str, Enum):
    PLATFORM = "platform"  # default web/API usage; no suffix in user_id
    CLI = "cli"  # Codemie CLI proxy spending
    PREMIUM_MODELS = "premium_models"  # costly model spending via CLI


def build_user_id(email: str, category: BudgetCategory) -> str:
    """Build the canonical LiteLLM user_id for the given email and category.

    For PLATFORM the user_id is just the email (no suffix).
    For other categories: ``{email}_codemie_{category.value}``
    e.g. ``{email}_codemie_cli``, ``{email}_codemie_premium_models``.

    Suffixes are stable and must match existing LiteLLM customer entries.
    """
    if category == BudgetCategory.PLATFORM:
        return email
    return f"{email}_codemie_{category.value}"


def derive_category_from_user_id(user_id: str) -> BudgetCategory:
    """Derive BudgetCategory from a LiteLLM user_id by matching known suffixes.

    Inverse of ``build_user_id``: strips ``_codemie_{category.value}`` suffixes
    to identify the category.  Returns ``BudgetCategory.PLATFORM`` when no known
    suffix is found (plain email address).

    Args:
        user_id: LiteLLM customer user_id, e.g. ``alice@example.com_codemie_cli``.

    Returns:
        Matching BudgetCategory, or PLATFORM as the default fallback.
    """
    for category in BudgetCategory:
        if category == BudgetCategory.PLATFORM:
            continue
        if user_id.endswith(f"_codemie_{category.value}"):
            return category
    return BudgetCategory.PLATFORM
