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

import logging
from typing import TYPE_CHECKING

from tenacity import before_sleep_log, retry, retry_if_result, stop_after_attempt, wait_exponential

from codemie.enterprise.litellm.dependencies import get_litellm_service_or_none

if TYPE_CHECKING:
    from codemie_enterprise.litellm import BudgetTable

logger = logging.getLogger(__name__)

# Shared retry config: 3 attempts, exponential backoff 1-10 s.
# The enterprise service catches all HTTP exceptions internally and returns
# None (on error) or False (delete failure), so we retry on those sentinel values.
_RETRY_ATTEMPTS = 3
_RETRY_WAIT = wait_exponential(multiplier=1, min=1, max=10)
_BEFORE_SLEEP = before_sleep_log(logger, logging.WARNING)


def create_budget_in_litellm(
    budget_id: str,
    max_budget: float,
    soft_budget: float,
    budget_duration: str,
) -> BudgetTable | None:
    """Create a proxy budget via the enterprise budget facade.

    Retries up to 3 times on transient failures (indicated by None return).
    """
    service = get_litellm_service_or_none()
    if service is None:
        logger.debug("LiteLLM not available — skipping create_budget_in_litellm")
        return None

    @retry(
        retry=retry_if_result(lambda r: r is None),
        stop=stop_after_attempt(_RETRY_ATTEMPTS),
        wait=_RETRY_WAIT,
        before_sleep=_BEFORE_SLEEP,
    )
    def _call() -> BudgetTable | None:
        return service.create_managed_budget(
            budget_id=budget_id,
            max_budget=max_budget,
            soft_budget=soft_budget,
            budget_duration=budget_duration,
        )

    return _call()


def update_budget_in_litellm(
    budget_id: str,
    max_budget: float,
    soft_budget: float,
    budget_duration: str,
) -> BudgetTable | None:
    """Update a proxy budget via LiteLLM POST /budget/update.

    Retries up to 3 times on transient failures (indicated by None return).
    Returns updated BudgetTable or None after all attempts exhausted.
    """
    service = get_litellm_service_or_none()
    if service is None:
        logger.debug("LiteLLM not available — skipping update_budget_in_litellm")
        return None

    @retry(
        retry=retry_if_result(lambda r: r is None),
        stop=stop_after_attempt(_RETRY_ATTEMPTS),
        wait=_RETRY_WAIT,
        before_sleep=_BEFORE_SLEEP,
    )
    def _call() -> BudgetTable | None:
        return service.update_managed_budget(
            budget_id=budget_id,
            max_budget=max_budget,
            soft_budget=soft_budget,
            budget_duration=budget_duration,
        )

    result = _call()
    if result is None:
        logger.error(f"Failed to update budget {budget_id!r} in LiteLLM after {_RETRY_ATTEMPTS} attempts")
    return result


def get_budget_reset_at(budget_id: str) -> str | None:
    """Fetch budget_reset_at for a single budget_id via /budget/info.

    Only needed when creating/updating a single budget and reading back the reset timestamp.
    For bulk sync, budget_reset_at is already in /budget/list.
    Returns None if LiteLLM unavailable or budget not found.
    """
    service = get_litellm_service_or_none()
    if service is None:
        logger.debug("LiteLLM not available — skipping get_budget_reset_at")
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
    service = get_litellm_service_or_none()
    if service is None:
        logger.debug("LiteLLM not available — skipping list_budgets_from_litellm")
        return None

    try:
        return service.list_managed_budgets()
    except Exception as e:
        logger.warning(f"Failed to list budgets from LiteLLM: {e}")
        return None


def reset_customer_spending_in_litellm(user_id: str, budget_id: str) -> bool:
    """Reset a customer's spending in LiteLLM by deleting and recreating them.

    Used to unblock a user who has hit their budget limit. The customer record
    is deleted (spend counter resets to 0) and recreated with the same budget_id.
    Returns True on success, False if LiteLLM is unavailable or recreation fails.
    Never raises — failures are logged so the caller can proceed (fail-open).
    """
    service = get_litellm_service_or_none()
    if service is None:
        logger.debug("LiteLLM not available — skipping reset_customer_spending_in_litellm")
        return False

    try:
        result = service.reset_customer_spending(user_id=user_id, budget_id=budget_id)
        return result is not None
    except Exception as e:
        logger.warning(f"Failed to reset customer spending in LiteLLM for {user_id!r}: {e}")
        return False


def update_customer_budget_in_litellm(user_id: str, budget_id: str | None) -> bool:
    """Assign or clear a customer's proxy budget via the enterprise budget facade.

    Returns True on success, False if LiteLLM is unavailable or the call fails.
    Never raises — failures are logged as warnings so the DB write can proceed (fail-open).
    """
    service = get_litellm_service_or_none()
    if service is None:
        logger.debug("LiteLLM not available — skipping update_customer_budget_in_litellm")
        return False

    try:
        return service.set_customer_budget_assignment(user_id=user_id, budget_id=budget_id)
    except Exception as e:
        logger.warning(f"Failed to update customer budget in LiteLLM for {user_id!r}: {e}")
        return False
