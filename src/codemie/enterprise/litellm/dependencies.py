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

import asyncio
from functools import lru_cache
from typing import TYPE_CHECKING, Optional

from codemie.configs.budget_config import budget_config
from codemie.configs import logger
from codemie.enterprise.loader import HAS_LITELLM
from codemie.enterprise.litellm.models import UserKeysSpending

if TYPE_CHECKING:
    from codemie_enterprise.litellm import LiteLLMService
    from codemie.enterprise.litellm.budget_categories import BudgetCategory

# Constants
_LITELLM_NOT_AVAILABLE_MSG = "LiteLLM not available"

# Global service registry (initialized at startup)
_global_litellm_service: Optional["LiteLLMService"] = None

# Strong references to fire-and-forget soft-limit notification tasks
# (EPMCDME-13959 CR-001). The event loop only holds a weak reference to a
# task, so an unreferenced ``create_task`` result can be garbage-collected
# mid-execution. Adding tasks here — and discarding them on completion —
# keeps them alive without leaking.
_pending_notify_tasks: set[asyncio.Task] = set()


def is_litellm_enabled() -> bool:
    """
    Check if LiteLLM is available and enabled.

    This is the centralized function that all code should use to check LiteLLM availability.

    Priority order (CRITICAL):
    1. HAS_LITELLM (source of truth - is enterprise package available?)
    2. config.LLM_PROXY_ENABLED (user preference - do they want it enabled?)

    Returns:
        True if both conditions are met, False otherwise

    Usage:
        from codemie.enterprise.litellm import is_litellm_enabled

        if not is_litellm_enabled():
            return None  # Skip LiteLLM operations
    """
    from codemie.configs import config

    # FIRST: Check if enterprise package is available (SOURCE OF TRUTH)
    if not HAS_LITELLM:
        return False

    # SECOND: Check if LiteLLM proxy is enabled in config (USER PREFERENCE)
    return config.LLM_PROXY_ENABLED


def initialize_litellm_from_config() -> Optional["LiteLLMService"]:
    """
    Initialize LiteLLM service from environment configuration.

    This is a convenience helper for application startup that creates and initializes
    the LiteLLM service based on configuration settings.

    Uses is_litellm_enabled() to check availability and configuration.

    Returns:
        Initialized LiteLLMService or None if not available/disabled

    Usage:
        # In main.py lifespan function:
        litellm_service = initialize_litellm_from_config()
        app.state.litellm_service = litellm_service
        set_global_litellm_service(litellm_service)
    """
    from codemie.configs import config, logger

    # Check if LiteLLM is available and enabled
    if not is_litellm_enabled():
        logger.info("LiteLLM not available or disabled")
        return None

    try:
        from codemie.enterprise import LiteLLMConfig, LiteLLMService

        # Create config from core settings
        platform = next((b for b in budget_config.predefined_budgets if b.budget_category == "platform"), None)
        litellm_config = LiteLLMConfig(
            url=config.LITE_LLM_URL,
            master_key=config.LITE_LLM_MASTER_KEY,
            app_key=config.LITE_LLM_APP_KEY or None,
            enabled=config.LLM_PROXY_ENABLED,
            budget_check_enabled=config.LLM_PROXY_BUDGET_CHECK_ENABLED,
            fail_open_on_503=config.LITELLM_FAIL_OPEN_ON_503,
            request_timeout=config.LITELLM_REQUEST_TIMEOUT,
            list_request_timeout=config.LITELLM_LIST_REQUEST_TIMEOUT,
            max_retries=config.LITELLM_MAX_RETRIES if hasattr(config, "LITELLM_MAX_RETRIES") else 3,
            customer_cache_ttl=config.LITELLM_CUSTOMER_CACHE_TTL,
            models_cache_ttl=config.LITELLM_MODELS_CACHE_TTL,
            default_budget_id=platform.budget_id if platform else "",
            default_hard_budget_limit=platform.max_budget if platform else 0.0,
            default_soft_budget_limit=platform.soft_budget if platform else 0.0,
            default_budget_duration=platform.budget_duration if platform else "30d",
        )

        # Create service
        service = LiteLLMService(litellm_config)
        logger.info("LiteLLM enterprise service initialized")
        return service

    except Exception as e:
        logger.error(f"Failed to initialize LiteLLM: {e}")
        return None


def set_global_litellm_service(service: Optional["LiteLLMService"]) -> None:
    """
    Set the global LiteLLM service instance.

    This is called during application startup to make the service available
    to code that doesn't have access to the FastAPI request context.

    Args:
        service: LiteLLMService instance or None
    """
    global _global_litellm_service
    _global_litellm_service = service


def get_global_litellm_service() -> Optional["LiteLLMService"]:
    """
    Get the global LiteLLM service instance.

    Returns None if enterprise feature not available or not initialized.

    Returns:
        LiteLLMService instance if available, None otherwise

    Usage:
        from codemie.enterprise.litellm import get_global_litellm_service

        litellm_service = get_global_litellm_service()
        if litellm_service:
            models = litellm_service.get_available_models()
            # Use models...
    """
    return _global_litellm_service


def get_litellm_service_or_none() -> Optional["LiteLLMService"]:
    """
    Get LiteLLM service, returns None if not available.

    Use this function everywhere: services, routers, background tasks, and FastAPI dependencies.

    Returns None if:
    - Enterprise package not installed (HAS_LITELLM=False)
    - LiteLLM disabled in config (LLM_PROXY_ENABLED=False)
    - Service not initialized

    Returns:
        LiteLLMService instance or None

    Usage in services/background tasks:
        from codemie.enterprise.litellm import get_litellm_service_or_none

        litellm = get_litellm_service_or_none()
        if litellm is None:
            return None  # Graceful degradation

        models = litellm.get_available_models()

    Usage with FastAPI Depends():
        from fastapi import Depends
        from codemie.enterprise.litellm import get_litellm_service_or_none

        @router.get("/models")
        async def get_models(
            litellm: Optional[LiteLLMService] = Depends(get_litellm_service_or_none)
        ):
            if not litellm:
                raise HTTPException(503, "LiteLLM not available")
            return litellm.get_available_models()
    """
    if not is_litellm_enabled():
        return None
    return get_global_litellm_service()


def require_litellm_enabled() -> None:
    """
    Require LiteLLM to be available, raises exception if not.

    This is a convenience helper for endpoints that REQUIRE LiteLLM to function.
    Use this at the beginning of route handlers to enforce LiteLLM availability.

    Raises:
        ExtendedHTTPException: If LiteLLM is not available or not enabled

    Usage:
        from codemie.enterprise.litellm import require_litellm_enabled

        @router.post("/settings")
        def create_litellm_setting(request: SettingRequest):
            require_litellm_enabled()  # Fails fast if LiteLLM unavailable
            # Continue with LiteLLM operations...
    """
    from codemie.core.exceptions import ExtendedHTTPException

    if not is_litellm_enabled():
        raise ExtendedHTTPException(
            code=400,
            message="LiteLLM not available or not installed",
            details="LiteLLM enterprise features are not enabled or not installed. "
            "Please contact your administrator to enable LiteLLM enterprise features.",
        )


# ============================================================================
# LiteLLM HTTP Client (for proxy endpoints)
# ============================================================================
# NOTE: HTTP client functions (get_llm_proxy_client, close_llm_proxy_client)
# are defined in client.py and imported via __init__.py
# This avoids code duplication and ensures single source of truth.


async def ensure_predefined_budgets() -> None:
    """Force-create or update predefined budgets during reconciliation.

    Delegates to BudgetService.ensure_predefined_budgets() which keeps both DB
    and LiteLLM in sync. Config is the source of truth — existing values are
    overwritten to match the configured definitions.
    """
    from codemie.clients.postgres import get_async_session
    from codemie.service.budget.budget_service import budget_service

    logger.info("Initializing predefined budgets...")
    try:
        async with get_async_session() as session:
            await budget_service.ensure_predefined_budgets(session)
        logger.info("Predefined budgets initialized")
    except Exception as e:
        logger.error(f"Failed to initialize predefined budgets: {e}")
        raise


async def sync_budgets_from_litellm() -> None:
    """Mirror all provider budgets into the local DB during reconciliation.

    Delegates to BudgetService.sync_budgets_from_litellm() which mirrors all
    LiteLLM budget definitions into the local DB.
    """
    from codemie.clients.postgres import get_async_session
    from codemie.service.budget.budget_service import budget_service

    logger.info("Syncing budgets from LiteLLM...")
    try:
        async with get_async_session() as session:
            result = await budget_service.sync_budgets_from_litellm(session, actor_id="system")
        logger.info(
            f"Budgets synced from LiteLLM: created={result.created}, "
            f"updated={result.updated}, unchanged={result.unchanged}, "
            f"total_in_litellm={result.total_in_litellm}"
        )
    except Exception as e:
        logger.error(f"Failed to sync budgets from LiteLLM: {e}")
        raise


async def backfill_project_budget_assignments_from_settings() -> None:
    """Migrate project LiteLLM settings into project budget entities during reconciliation.

    Delegates to BudgetService.backfill_project_budget_assignments_from_settings() which creates
    Budget + ProjectBudgetAssignment + ProjectMemberBudgetAssignment rows for every project
    LiteLLM key that lacks a corresponding budget hierarchy.
    """
    from codemie.clients.postgres import get_async_session
    from codemie.service.budget.budget_service import budget_service

    logger.info("Starting project budget migration from settings...")
    try:
        async with get_async_session() as session:
            result = await budget_service.backfill_project_budget_assignments_from_settings(session)
        logger.info(
            f"✓ Project budget migration complete: migrated={result.migrated}, "
            f"members={result.migrated_members}, skipped_existing={result.skipped_existing}, "
            f"skipped_not_found={result.skipped_not_found}, failed={result.failed}, "
            f"total_settings={result.total_settings}"
        )
    except Exception as e:
        logger.error(f"✗ Project budget migration failed: {e}")
        raise


async def backfill_user_budget_assignments() -> None:
    """Import missing LiteLLM customer budget assignments during reconciliation.

    Delegates to BudgetService.backfill_user_budget_assignments()
    which mirrors missing user/category assignments from LiteLLM customers.
    """
    from codemie.clients.postgres import get_async_session
    from codemie.service.budget.budget_service import budget_service

    logger.info("Backfilling user budget assignments from LiteLLM...")
    try:
        async with get_async_session() as session:
            result = await budget_service.backfill_user_budget_assignments(session)
        logger.info(
            f"User budget assignments backfilled: imported={result.imported}, "
            f"skipped_existing={result.skipped_existing}, "
            f"skipped_missing_user={result.skipped_missing_user}, "
            f"created_budgets={result.created_budgets}, failed={result.failed}, "
            f"total_in_litellm={result.total_in_litellm}"
        )
    except Exception as e:
        logger.error(f"Failed to backfill user budget assignments: {e}")
        raise


def _dispatch_soft_limit_notification(
    budget_id: str | None,
    current_spend: float,
    soft_limit: float,
) -> None:
    """Schedule the budget-owner email as a fire-and-forget task (EPMCDME-13959).

    Never blocks and never raises. Skipped when no compatible event loop is
    available (sync contexts, or cross-loop calls from LangChain sync bridges);
    the soft-limit metric is emitted by the caller and is unaffected either way.
    """
    from codemie.configs import config, logger

    if budget_id is None:
        return

    if not config.BUDGET_SOFT_LIMIT_NOTIFICATION_ENABLED:
        if config.BUDGET_SOFT_LIMIT_EMAIL_ENABLED:
            logger.warning(
                f"budget_event=soft_limit_notification_skipped reason=master_flag_disabled "
                f"budget_id={budget_id!r} hint=set BUDGET_SOFT_LIMIT_NOTIFICATION_ENABLED=true to enable notifications"
            )
        return

    if not config.BUDGET_SOFT_LIMIT_EMAIL_ENABLED:
        return

    try:
        loop = asyncio.get_running_loop()
        from codemie.service.budget.budget_notification_service import notify_soft_limit_reached

        task = loop.create_task(
            notify_soft_limit_reached(
                budget_id=budget_id,
                current_spend=current_spend,
                soft_limit=soft_limit,
            )
        )
        # Retain a strong reference until the task completes: the event loop
        # keeps only a weak one and would let it be garbage-collected.
        _pending_notify_tasks.add(task)
        task.add_done_callback(_pending_notify_tasks.discard)
    except Exception as notify_exc:
        logger.debug(f"budget_notify_skipped budget_id={budget_id!r} reason=dispatch_failed error={notify_exc}")


def _report_hard_limit_exceeded(
    user_email: str,
    user_id: str | None,
    budget_id: str | None,
    current_spend: float,
    hard_limit: float,
) -> None:
    """Log and emit the hard-budget-limit metric."""
    from codemie.configs import logger
    from codemie.service.monitoring.base_monitoring_service import send_log_metric
    from codemie.service.monitoring.metrics_constants import LLM_HARD_BUDGET_LIMIT, MetricsAttributes

    logger.warning(
        f"budget_event=personal_budget_hard_limit_exceeded component=dependencies "
        f"username={user_email!r} user_id={user_id!r} budget_id={budget_id!r} "
        f"spend={current_spend!r} hard_limit={hard_limit!r}"
    )
    send_log_metric(
        LLM_HARD_BUDGET_LIMIT,
        attributes={
            MetricsAttributes.USER_ID: user_id,
            MetricsAttributes.USER_NAME: user_email,
            MetricsAttributes.USER_EMAIL: user_email,
            "hard_limit": hard_limit,
            "spent": current_spend,
        },
    )


def _report_soft_limit_exceeded(
    user_email: str,
    user_id: str | None,
    budget_id: str | None,
    current_spend: float,
    soft_limit: float,
) -> None:
    """Log and emit the soft-budget-limit metric, then notify the budget owner."""
    from codemie.configs import logger
    from codemie.service.monitoring.base_monitoring_service import send_log_metric
    from codemie.service.monitoring.metrics_constants import LLM_SOFT_BUDGET_LIMIT, MetricsAttributes

    logger.warning(
        f"budget_event=personal_budget_soft_limit_exceeded component=dependencies "
        f"username={user_email!r} user_id={user_id!r} budget_id={budget_id!r} "
        f"spend={current_spend!r} soft_limit={soft_limit!r}"
    )
    send_log_metric(
        LLM_SOFT_BUDGET_LIMIT,
        attributes={
            MetricsAttributes.USER_ID: user_id,
            MetricsAttributes.USER_NAME: user_email,
            MetricsAttributes.USER_EMAIL: user_email,
            "soft_limit": soft_limit,
            "spent": current_spend,
        },
    )
    _dispatch_soft_limit_notification(budget_id, current_spend, soft_limit)


def check_user_budget(user_email: str, budget_id: str | None = None, user_id: str | None = None):
    """
    Check if user is within budget limits with caching and metrics.

    This function wraps the enterprise service call with:
    - Budget limit checking (soft/hard limits)
    - Metrics tracking for budget violations
    - Graceful degradation if LiteLLM unavailable
    - Cache optimization (5-min TTL)

    Args:
        user_email: User email (used as LiteLLM username / budget identity key)
        budget_id: Optional explicit LiteLLM budget id to attach to this user. When
            omitted, the enterprise service default budget selection is used.
        user_id: Actual database user ID sent in metrics as USER_ID attribute. When
            omitted, the USER_ID metric attribute is left as None.

    Returns:
        CustomerInfo object or None if check disabled/fails

    Usage:
        from codemie.enterprise.litellm import check_user_budget

        customer = check_user_budget(user_email, user_id=user_id)
        if customer is None:
            # Budget check disabled or failed - continue with request
            pass
    """
    from codemie.configs import logger

    litellm = get_litellm_service_or_none()
    if litellm is None:
        logger.info(
            f"budget_event=personal_budget_check_skipped component=dependencies "
            f"username={user_email!r} user_id={user_id!r} budget_id={budget_id!r} "
            f"reason=litellm_unavailable"
        )
        return None

    logger.info(
        f"budget_event=personal_budget_check_started component=dependencies "
        f"username={user_email!r} user_id={user_id!r} budget_id={budget_id!r}"
    )

    try:
        # Try cache first
        customer = litellm._get_cached_customer(user_email)

        if customer:
            logger.info(
                f"budget_event=personal_budget_check_cache_hit component=dependencies "
                f"username={user_email!r} user_id={user_id!r} budget_id={budget_id!r}"
            )
        else:
            logger.info(
                f"budget_event=personal_budget_check_cache_miss component=dependencies "
                f"username={user_email!r} user_id={user_id!r} budget_id={budget_id!r}"
            )
            if budget_id is None:
                customer = litellm.get_or_create_customer_with_budget(user_email)
            else:
                customer = litellm.get_or_create_customer_with_budget(user_email, budget_id=budget_id)

            if customer:
                litellm._cache_customer(user_email, customer)
            else:
                logger.warning(
                    f"budget_event=personal_budget_check_failed component=dependencies "
                    f"username={user_email!r} user_id={user_id!r} budget_id={budget_id!r} "
                    f"reason=empty_customer_result action=fail_open"
                )
                return None

        # Check budget limits
        if not customer.litellm_budget_table:
            logger.warning(
                f"budget_event=personal_budget_check_no_table component=dependencies "
                f"username={user_email!r} user_id={user_id!r} budget_id={budget_id!r} "
                f"reason=no_budget_table action=allow"
            )
            return customer

        current_spend = customer.spend
        budget_table = customer.litellm_budget_table
        soft_limit = budget_table.soft_budget
        hard_limit = budget_table.max_budget

        logger.info(
            f"budget_event=personal_budget_check_completed component=dependencies "
            f"username={user_email!r} user_id={user_id!r} budget_id={budget_id!r} "
            f"spend={current_spend!r} soft_limit={soft_limit!r} hard_limit={hard_limit!r}"
        )

        # Check hard budget limit
        if hard_limit is not None and current_spend >= hard_limit:
            _report_hard_limit_exceeded(user_email, user_id, budget_id, current_spend, hard_limit)

        # Check soft budget limit
        if soft_limit is not None and current_spend >= soft_limit:
            _report_soft_limit_exceeded(user_email, user_id, budget_id, current_spend, soft_limit)

        return customer

    except Exception as e:
        logger.error(
            f"budget_event=personal_budget_check_failed component=dependencies "
            f"username={user_email!r} user_id={user_id!r} budget_id={budget_id!r} "
            f"error={e}",
            exc_info=True,
        )
        return None


def get_category_budget_id(category: "BudgetCategory") -> str | None:
    """Return the configured LiteLLM budget_id for a category, or None if not configured.

    Looks up the predefined budgets list for a budget matching the given category.
    Returns None if no budget with that category is configured.
    """
    for b in budget_config.predefined_budgets:
        if b.budget_category == category.value:
            return b.budget_id
    return None


@lru_cache(maxsize=1)
def is_premium_models_enabled() -> bool:
    """Return True when a premium_models budget is configured in predefined budgets.

    Result is cached for the process lifetime — config is set at startup and does not
    change while the application is running. Call
    ``is_premium_models_enabled.cache_clear()`` in tests that modify predefined budgets.
    """
    from codemie.enterprise.litellm.budget_categories import BudgetCategory

    return get_category_budget_id(BudgetCategory.PREMIUM_MODELS) is not None


@lru_cache(maxsize=1)
def is_proxy_budget_enabled() -> bool:
    """Return True when a cli budget is configured in predefined budgets."""
    from codemie.enterprise.litellm.budget_categories import BudgetCategory

    return get_category_budget_id(BudgetCategory.CLI) is not None


@lru_cache(maxsize=256)
def is_premium_model(model: str) -> bool:
    """Return True when *model* matches any alias in LITELLM_PREMIUM_MODELS_ALIASES (case-insensitive partial match).

    Result is cached per model name for the process lifetime — the alias list is set at
    startup and does not change while the application is running.  Call
    ``is_premium_model.cache_clear()`` in tests that patch the config value.
    """
    from codemie.configs import config

    aliases: list[str] = config.LITELLM_PREMIUM_MODELS_ALIASES
    if not aliases:
        return False

    model_lower = model.lower()
    return any(alias.lower() in model_lower for alias in aliases)


def get_premium_username(user_email: str, model: str) -> str | None:
    """Derive the LiteLLM username for premium budget attribution.

    Returns the stable category-based user_id (``{user_email}_codemie_premium_models``)
    when *model* matches a configured premium alias; otherwise ``None``. The username
    uses a fixed category suffix, independent of the configured budget_id.
    """
    from codemie.enterprise.litellm.budget_categories import BudgetCategory, build_user_id

    if not is_premium_model(model):
        return None

    return build_user_id(user_email, BudgetCategory.PREMIUM_MODELS)


def get_proxy_username(user_email: str) -> str | None:
    """Derive the LiteLLM username for proxy budget attribution.

    Returns the stable category-based user_id (``{user_email}_codemie_cli``)
    when the cli budget feature is enabled; otherwise ``None``.
    """
    from codemie.enterprise.litellm.budget_categories import BudgetCategory, build_user_id

    if not is_proxy_budget_enabled():
        return None

    return build_user_id(user_email, BudgetCategory.CLI)


def get_premium_customer_spending(user_email: str, on_raise: bool = False) -> dict | None:
    """Get spending for the derived premium budget identity ``{user_email}_{budget_id}``.

    Returns ``None`` when no premium_models budget is configured in predefined budgets
    or when the underlying LiteLLM service is unavailable.

    Args:
        user_email: The authenticated user's email / LiteLLM username.
        on_raise: When True, re-raises backend errors instead of returning None.
    """
    from codemie.configs import logger
    from codemie.enterprise.litellm.budget_categories import BudgetCategory

    if not is_premium_models_enabled():
        logger.debug("Premium models budget not configured, skipping premium spending lookup")
        return None

    from codemie.enterprise.litellm.budget_categories import build_user_id

    derived_id = build_user_id(user_email, BudgetCategory.PREMIUM_MODELS)
    logger.debug(f"Fetching premium customer spending for derived id: {derived_id}")
    return get_customer_spending(derived_id, on_raise=on_raise)


def get_proxy_customer_spending(user_email: str, on_raise: bool = False) -> dict | None:
    """Get spending for the derived proxy budget identity ``{user_email}_{budget_name}``."""
    from codemie.configs import logger

    derived_id = get_proxy_username(user_email)
    if not derived_id:
        logger.debug("Proxy budget name not configured, skipping proxy spending lookup")
        return None

    logger.debug(f"Fetching proxy customer spending for derived id: {derived_id}")
    return get_customer_spending(derived_id, on_raise=on_raise)


def get_customer_spending(user_id: str, on_raise: bool = False):
    """
    Get customer spending information from LiteLLM.

    This function wraps the enterprise service call with error handling.

    Args:
        user_id: User ID to query
        on_raise: If True, raises exceptions on errors. If False, returns None on errors.

    Returns:
        Dictionary with spending data, or None if service not enabled or error occurred (when on_raise=False)

    Raises:
        Exception: If a backend error occurs while fetching spending data (only when on_raise=True)
    """
    from codemie.configs import logger

    litellm = get_litellm_service_or_none()
    if litellm is None:
        logger.debug(_LITELLM_NOT_AVAILABLE_MSG)
        return None

    try:
        return litellm.get_customer_spending(user_id)
    except Exception as e:
        logger.error(f"Error getting customer spending for {user_id}: {e}")
        if on_raise:
            raise
        return None


def get_customer_list_spending(on_raise: bool = False):
    """Get all customer budget entries from LiteLLM /customer/list.

    Used by the budget-based spend collector to derive personal-project spending rows.
    One entry is returned per budget bucket per customer.

    Args:
        on_raise: If True, raises exceptions on errors. If False, returns None on errors.

    Returns:
        List of PersonalBudgetEntry objects, or None if service not enabled or error occurred.

    Raises:
        Exception: If a backend error occurs while fetching data (only when on_raise=True)
    """
    litellm = get_litellm_service_or_none()
    if litellm is None:
        logger.debug(_LITELLM_NOT_AVAILABLE_MSG)
        return None

    try:
        return litellm.get_customer_list()
    except Exception as e:
        logger.error(f"Error getting customer list spending: {e}")
        if on_raise:
            raise
        return None


def get_all_keys_spending(api_keys: list[str], on_raise: bool = False) -> list[dict] | None:
    """
    Get spending info for multiple virtual keys.

    This function wraps the enterprise service call with error handling.
    Queries LiteLLM's /key/info endpoint for each API key.

    Args:
        api_keys: List of API keys to query
        on_raise: If True, raises exceptions on errors. If False, returns None on errors.

    Returns:
        List of spending data dictionaries, or None if service not enabled or error occurred (when on_raise=False)

    Raises:
        Exception: If a backend error occurs while fetching spending data (only when on_raise=True)
    """

    litellm = get_litellm_service_or_none()
    if litellm is None:
        logger.debug(_LITELLM_NOT_AVAILABLE_MSG)
        return None

    try:
        return litellm.get_all_keys_spending_info(api_keys)
    except Exception as e:
        logger.error(f"Error getting keys spending: {e}")
        if on_raise:
            raise
        return None


def get_key_spending_info(key_aliases: list[str], on_raise: bool = False) -> list[dict]:
    """Backward-compatible wrapper for fetching key info by alias.

    Legacy callers expect an empty list when LiteLLM is unavailable or the
    request fails with ``on_raise=False``.
    """
    litellm = get_litellm_service_or_none()
    if litellm is None:
        logger.debug(_LITELLM_NOT_AVAILABLE_MSG)
        return []

    try:
        return litellm.get_key_info(key_aliases)
    except Exception as e:
        logger.error(f"Error getting key info: {e}")
        if on_raise:
            raise
        return []


def get_user_keys_spending(
    user_id: str,
    project_names: list[str],
    on_raise: bool = False,
) -> UserKeysSpending | None:
    """
    Get spending info for all user's LiteLLM virtual keys, grouped by type.

    This function:
    1. Retrieves all LiteLLM API keys from user's settings (USER + PROJECT scoped)
    2. Queries LiteLLM /key/info endpoint for each key
    3. Enriches spending data with project names from Settings
    4. Returns spending data grouped by key type (USER vs PROJECT)

    Args:
        user_id: User ID
        project_names: List of all projects user has access to
        on_raise: If True, raises exceptions on errors. If False, returns None on errors.

    Returns:
        UserKeysSpending model with spending data grouped by type:
        - user_keys: [spending_dict with project_name, ...]
        - project_keys: [spending_dict with project_name, ...]
        or None if service not enabled or error occurred (when on_raise=False)

    Raises:
        Exception: If a backend error occurs while fetching spending data (only when on_raise=True)
    """

    # Check if LiteLLM service is available
    litellm = get_litellm_service_or_none()
    if litellm is None:
        logger.debug(_LITELLM_NOT_AVAILABLE_MSG)
        return None

    # Get all user's LiteLLM settings with metadata (api_key, alias, project_name)
    try:
        from codemie.service.settings.settings import SettingsService

        grouped_settings = SettingsService.get_user_litellm_settings_with_metadata(user_id, project_names)
    except Exception as e:
        logger.error(f"Error retrieving LiteLLM settings for user {user_id}: {e}")
        if on_raise:
            raise
        return None

    user_keys_meta = grouped_settings["user_keys"]
    project_keys_meta = grouped_settings["project_keys"]

    user_keys_spending = get_all_keys_spending([s["api_key"] for s in user_keys_meta], on_raise=on_raise) or []
    project_keys_spending = get_all_keys_spending([s["api_key"] for s in project_keys_meta], on_raise=on_raise) or []

    # Assign project_name from our database by position — same order as the keys we submitted
    if len(user_keys_spending) != len(user_keys_meta):
        logger.warning(
            f"LiteLLM returned {len(user_keys_spending)} results for "
            f"{len(user_keys_meta)} submitted user keys — project_name may be missing on some rows"
        )
    for spending, meta in zip(user_keys_spending, user_keys_meta, strict=False):
        spending["project_name"] = meta["project_name"]

    if len(project_keys_spending) != len(project_keys_meta):
        logger.warning(
            f"LiteLLM returned {len(project_keys_spending)} results for "
            f"{len(project_keys_meta)} submitted project keys — project_name may be missing on some rows"
        )
    for spending, meta in zip(project_keys_spending, project_keys_meta, strict=False):
        spending["project_name"] = meta["project_name"]

    return UserKeysSpending(
        user_keys=user_keys_spending,
        project_keys=project_keys_spending,
    )


def get_available_models(user_id: str | None = None, api_key: str | None = None):
    """
    Get available models from LiteLLM and map to LLMModel objects.

    This function:
    1. Calls enterprise service to get raw model data
    2. Maps each model dict to LLMModel using core mapping function
    3. Deduplicates by base_name
    4. Separates into chat and embedding models

    Args:
        user_id: User ID for cache key (optional)
        api_key: API key to use (defaults to master key)

    Returns:
        LiteLLMModels containing chat and embedding models, or empty LiteLLMModels if not available

    Usage:
        from codemie.enterprise.litellm import get_available_models

        models = get_available_models(user_id="user-123")
        print(f"Chat models: {len(models.chat_models)}")
        print(f"Embedding models: {len(models.embedding_models)}")
    """
    from codemie.configs import logger
    from codemie.configs.llm_config import LiteLLMModels, ModelType

    # Import model mapping function from models module
    from .models import map_litellm_to_llm_model

    litellm = get_litellm_service_or_none()
    if litellm is None:
        logger.debug("LiteLLM not available")
        return LiteLLMModels()

    try:
        # Get raw model dicts from enterprise
        raw_models = litellm.get_available_models(user_id=user_id, api_key=api_key)

        if not raw_models:
            return LiteLLMModels()

        # Map to LLMModel objects using local mapping function
        chat_models = {}
        embedding_models = {}

        for raw_model in raw_models:
            try:
                llm_model = map_litellm_to_llm_model(raw_model)
                if not llm_model.enabled:
                    continue

                mode_str = raw_model.get("model_info", {}).get("mode", ModelType.CHAT.value)

                try:
                    model_type = ModelType(mode_str)
                except ValueError:
                    logger.warning(f"Unknown model mode '{mode_str}', defaulting to CHAT")
                    model_type = ModelType.CHAT

                if model_type == ModelType.EMBEDDING:
                    embedding_models[llm_model.base_name] = llm_model
                else:
                    chat_models[llm_model.base_name] = llm_model

            except Exception as e:
                logger.error(f"Error mapping model {raw_model.get('model_name')}: {e}")
                continue

        return LiteLLMModels(
            chat_models=list(chat_models.values()),
            embedding_models=list(embedding_models.values()),
        )

    except Exception as e:
        logger.error(f"Error getting available models: {e}")
        return LiteLLMModels()
