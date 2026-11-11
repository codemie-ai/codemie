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

"""Shared `budgetManagement` feature gate, reused by every Budget Management call site.

A single module so the three surfaces (project_budget_router.py's two routers,
budget_router.py's router, and get_cost_center_budget_summary()'s chokepoint) can't drift
into three independently-written checks.
"""

from __future__ import annotations

from fastapi import Depends

from codemie.configs.customer_config import customer_config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.authentication import authenticate


def is_budget_management_enabled() -> bool:
    """`features:budgetManagement` is available (flag AND enterprise ceiling). For paths that degrade instead of 403."""
    return customer_config.is_feature_enabled("budgetManagement")


def ensure_budget_management_enabled() -> None:
    """Raise 403 unless `features:budgetManagement` is available (flag AND enterprise ceiling).

    Call this from any code path that returns Budget Management data or performs a Budget
    Management action — including a future second caller not wired through the FastAPI
    dependency below — so the check can't be bypassed by construction.
    """
    if not is_budget_management_enabled():
        raise ExtendedHTTPException(
            code=403,
            message="Feature not available",
            details="Budget management is not enabled for this customer.",
            help="Contact your system administrator to enable the 'features:budgetManagement' component.",
        )


def require_budget_management_enabled() -> None:
    ensure_budget_management_enabled()


# authenticate must run first so an unauthenticated caller gets 401, never 403.
budget_management_guard = [Depends(authenticate), Depends(require_budget_management_enabled)]
