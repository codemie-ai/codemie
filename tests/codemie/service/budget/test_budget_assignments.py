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

import pytest

from codemie.enterprise.litellm.budget_categories import BudgetCategory
from codemie.core.exceptions import ValidationException
from codemie.service.budget.budget_models import Budget
from codemie.service.budget.budget_service import BudgetService


def test_budget_assignment_rejects_category_mismatch():
    budget = Budget(
        budget_id="platform-budget",
        name="Platform budget",
        soft_budget=10,
        max_budget=20,
        budget_duration="30d",
        budget_category=BudgetCategory.PLATFORM.value,
        created_by="admin",
    )

    with pytest.raises(ValidationException, match="cannot assign"):
        BudgetService._validate_budget_matches_category(budget, BudgetCategory.CLI)
