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

"""Helper layer wrapping LiteLLMService budget operations.

These functions follow the get_litellm_service_or_none() guard pattern:
if the service is None, they return None/False with a logger.debug log.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from codemie.configs import logger
from codemie.enterprise.litellm.dependencies import get_litellm_service_or_none

if TYPE_CHECKING:
    from codemie_enterprise.litellm import BudgetTable


def create_budget_in_litellm(
    budget_id: str,
    max_budget: float,
    soft_budget: float,
    budget_duration: str,
) -> BudgetTable | None:
    """Create a proxy budget via the enterprise budget facade."""
    logger.debug(
        f"budget_event=provider_global_budget_create_started component=litellm_budget_helpers "
        f"provider=litellm budget_id={budget_id!r} max_budget={max_budget!r} "
        f"soft_budget={soft_budget!r} budget_duration={budget_duration!r}"
    )
    service = get_litellm_service_or_none()
    if service is None:
        logger.debug(
            f"budget_event=provider_unavailable component=litellm_budget_helpers provider=litellm "
            f"operation=create_global_budget budget_id={budget_id!r}"
        )
        return None

    result = service.create_managed_budget(
        budget_id=budget_id,
        max_budget=max_budget,
        soft_budget=soft_budget,
        budget_duration=budget_duration,
    )
    logger.debug(
        f"budget_event=provider_global_budget_create_completed component=litellm_budget_helpers "
        f"provider=litellm budget_id={budget_id!r} result_present={result is not None}"
    )
    return result


def update_budget_in_litellm(
    budget_id: str,
    max_budget: float,
    soft_budget: float,
    budget_duration: str,
) -> BudgetTable | None:
    """Update a proxy budget via LiteLLM POST /budget/update.

    Returns updated BudgetTable or None on failure.
    """
    logger.debug(
        f"budget_event=provider_global_budget_update_started component=litellm_budget_helpers "
        f"provider=litellm budget_id={budget_id!r} max_budget={max_budget!r} "
        f"soft_budget={soft_budget!r} budget_duration={budget_duration!r}"
    )
    service = get_litellm_service_or_none()
    if service is None:
        logger.debug(
            f"budget_event=provider_unavailable component=litellm_budget_helpers provider=litellm "
            f"operation=update_global_budget budget_id={budget_id!r}"
        )
        return None

    result = service.update_managed_budget(
        budget_id=budget_id,
        max_budget=max_budget,
        soft_budget=soft_budget,
        budget_duration=budget_duration,
    )
    if result is None:
        logger.error(
            f"budget_event=provider_global_budget_update_failed component=litellm_budget_helpers "
            f"provider=litellm budget_id={budget_id!r} reason=empty_provider_result"
        )
    logger.debug(
        f"budget_event=provider_global_budget_update_completed component=litellm_budget_helpers "
        f"provider=litellm budget_id={budget_id!r} result_present={result is not None}"
    )
    return result


def get_budget_reset_at(budget_id: str) -> str | None:
    """Fetch budget_reset_at for a single budget_id via /budget/info.

    Only needed when creating/updating a single budget and reading back the reset timestamp.
    For bulk sync, budget_reset_at is already in /budget/list.
    Returns None if LiteLLM unavailable or budget not found.
    """
    service = get_litellm_service_or_none()
    if service is None:
        logger.debug(
            f"budget_event=provider_unavailable component=litellm_budget_helpers provider=litellm "
            f"operation=get_budget_reset_at budget_id={budget_id!r}"
        )
        return None

    budgets = service.get_budget_info([budget_id])
    if not budgets:
        return None
    return next((b.budget_reset_at for b in budgets if b.budget_id == budget_id), None)


def list_budgets_from_litellm() -> list[BudgetTable] | None:
    """List proxy budgets via the enterprise budget facade.

    Returns None (not empty list) when unreachable, so callers can distinguish
    'zero budgets' from 'LiteLLM unavailable'.
    """
    logger.debug("budget_event=provider_global_budget_list_started component=litellm_budget_helpers provider=litellm")
    service = get_litellm_service_or_none()
    if service is None:
        logger.debug(
            "budget_event=provider_unavailable component=litellm_budget_helpers provider=litellm "
            "operation=list_global_budgets"
        )
        return None

    try:
        result = service.list_managed_budgets()
        logger.debug(
            f"budget_event=provider_global_budget_list_completed component=litellm_budget_helpers "
            f"provider=litellm budget_count={len(result)}"
        )
        return result
    except Exception as e:
        logger.warning(
            f"budget_event=provider_global_budget_list_failed component=litellm_budget_helpers "
            f"provider=litellm error={e}"
        )
        return None


def reset_customer_spending_in_litellm(user_id: str, budget_id: str) -> bool:
    """Reset a customer's spending in LiteLLM by deleting and recreating them.

    Used to unblock a user who has hit their budget limit. The customer record
    is deleted (spend counter resets to 0) and recreated with the same budget_id.
    Returns True on success, False if LiteLLM is unavailable or recreation fails.
    Never raises — failures are logged so the caller can proceed (fail-open).
    """
    logger.debug(
        f"budget_event=provider_customer_spending_reset_started component=litellm_budget_helpers "
        f"provider=litellm provider_member_ref={user_id!r} budget_id={budget_id!r}"
    )
    service = get_litellm_service_or_none()
    if service is None:
        logger.debug(
            f"budget_event=provider_unavailable component=litellm_budget_helpers provider=litellm "
            f"operation=reset_customer_spending provider_member_ref={user_id!r} budget_id={budget_id!r}"
        )
        return False

    try:
        result = service.reset_customer_spending(user_id=user_id, budget_id=budget_id)
        logger.debug(
            f"budget_event=provider_customer_spending_reset_completed component=litellm_budget_helpers "
            f"provider=litellm provider_member_ref={user_id!r} budget_id={budget_id!r} "
            f"result_present={result is not None}"
        )
        return result is not None
    except Exception as e:
        logger.warning(
            f"budget_event=provider_customer_spending_reset_failed component=litellm_budget_helpers "
            f"provider=litellm provider_member_ref={user_id!r} budget_id={budget_id!r} error={e}"
        )
        return False


def update_customer_budget_in_litellm(user_id: str, budget_id: str | None) -> bool:
    """Assign or clear a customer's proxy budget via the enterprise budget facade.

    Returns True on success, False if LiteLLM is unavailable or the call fails.
    Never raises — failures are logged as warnings so the DB write can proceed (fail-open).
    """
    logger.debug(
        f"budget_event=provider_customer_budget_assignment_started component=litellm_budget_helpers "
        f"provider=litellm provider_member_ref={user_id!r} budget_id={budget_id!r} "
        f"operation={'assign' if budget_id else 'clear'}"
    )
    service = get_litellm_service_or_none()
    if service is None:
        logger.debug(
            f"budget_event=provider_unavailable component=litellm_budget_helpers provider=litellm "
            f"operation=update_customer_budget provider_member_ref={user_id!r} budget_id={budget_id!r}"
        )
        return False

    try:
        success = service.set_customer_budget_assignment(user_id=user_id, budget_id=budget_id)
        logger.debug(
            f"budget_event=provider_customer_budget_assignment_completed component=litellm_budget_helpers "
            f"provider=litellm provider_member_ref={user_id!r} budget_id={budget_id!r} success={success}"
        )
        return success
    except Exception as e:
        logger.warning(
            f"budget_event=provider_customer_budget_assignment_failed component=litellm_budget_helpers "
            f"provider=litellm provider_member_ref={user_id!r} budget_id={budget_id!r} error={e}"
        )
        return False
