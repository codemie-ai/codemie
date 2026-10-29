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

from types import SimpleNamespace

import pytest

from codemie.service.budget.budget_models import member_effective_max_budget

BUDGET = SimpleNamespace(max_budget=5000.0)


@pytest.mark.parametrize(
    ("allocation_mode", "enforce_limit", "expected"),
    [
        ("equal", True, 500.0),
        ("equal", False, 5000.0),
        ("fixed", True, 500.0),
        # An override is the member's own cap whether or not equal shares are enforced.
        ("fixed", False, 500.0),
    ],
)
def test_member_effective_max_budget(allocation_mode, enforce_limit, expected):
    allocation = SimpleNamespace(allocation_mode=allocation_mode, allocated_max_budget=500.0)

    assert member_effective_max_budget(allocation, BUDGET, enforce_limit) == expected


def test_member_without_allocation_mode_is_treated_as_equal_share():
    allocation = SimpleNamespace(allocated_max_budget=500.0)

    assert member_effective_max_budget(allocation, BUDGET, enforce_limit=False) == 5000.0
