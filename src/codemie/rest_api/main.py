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

import asyncio
import math
import uuid
import contextlib
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from elasticsearch import ApiError
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
from codemie.configs import config
from codemie.rest_api.core_gating import log_core_gating as _log_core_gating, schema_routes
from codemie.configs.customer_config import customer_config as _customer_config
from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.configs.config import ENV_LOCAL, retrieval_available
from codemie.enterprise.observability import get_observability_provider
from codemie.enterprise.litellm import (
    close_llm_proxy_client,
    initialize_litellm_from_config,
    is_litellm_enabled,
    set_global_litellm_service,
)
from codemie.enterprise.plugin import (
    initialize_plugin_from_config,
    set_global_plugin_service,
    get_global_plugin_service,
)
from codemie.enterprise.mcp_auth.router import get_mcp_auth_router, get_cimd_router
from codemie.enterprise.mcp_auth.dependencies import initialize_mcp_auth, shutdown_mcp_auth
from codemie.configs.logger import set_logging_info, logger
from codemie.core.constants import APP_DESCRIPTION, HEADER_CODEMIE_CLIENT
from codemie.rest_api.utils.client_context import set_client_source
from codemie.core.exceptions import (
    ConfluenceAuthRequiredException,
    ExtendedHTTPException,
    GitLabAuthRequiredException,
    JiraAuthRequiredException,
    MCPAuthenticationRequiredException,
    NotFoundException,
    OAuthConnectRequiredException,
    ValidationException,
)
from codemie.service.security.token_providers.base_provider import BrokerAuthRequiredException
from codemie.rest_api.routers import budget_router, project_budget_router
from codemie.rest_api.routers import (
    a2a,
    assistant_mapping,
    assistant,
    assistant_prompt_variable_mapping,
    category,
    conversation,
    conversation_analysis,
    share,
    workflow,
    workflow_marketplace,
    workflow_executions,
    webhook,
    skill_events,
    skill,
    schedulers,
    agent_workspace,
    guardrail,
    index,
    common,
    admin,
    feedback,
    background_tasks,
    vendor,
    user_settings,
    project_settings,
    settings,
    projects,
    cost_centers,
    llm_models,
    files,
    user,
    customer_config,
    provider,
    tool,
    ide,
    permission,
    auth,
    metrics,
    analytics,
    cli_analytics,
    callbacks,
    logs,
    mcp_config,
    mcp_managed,
    ai_kata,
    user_kata_progress,
    dynamic_config,
    export,
)
from codemie.rest_api.routers import sharepoint_oauth
from codemie.rest_api.routers import google_oauth
from codemie.rest_api.routers import gitlab_oauth
from codemie.rest_api.routers import jira_oauth
from codemie.rest_api.routers import confluence_oauth
from codemie.rest_api.routers.provisioning import mount_provisioning_api
from codemie.service.oauth_security import (
    assert_oauth_state_signing_secret_configured,
    assert_token_vault_available,
    warn_insecure_oauth_storage_for_enabled_providers,
)
from codemie.service.model_provider_readiness import (
    ModelProviderReadiness,
    ModelProviderStatus,
    check_model_provider_readiness,
    log_model_provider_readiness,
)

# User management routers (EPMCDME-10160)
from codemie.rest_api.routers import local_auth_router
from codemie.rest_api.routers import user_management_router
from codemie.rest_api.routers.marketplace_management import router as marketplace_management_router
from codemie.configs.marketplace_management import (
    is_marketplace_management_enabled,
    warn_marketplace_approval_gate_enabled,
)
from codemie.rest_api.routers import user_profile_router
from codemie.rest_api.routers import user_preferences_router
from codemie.rest_api.routers import user_profile_settings_router
from codemie.rest_api.routers import activity_events_router
from codemie.rest_api.utils.state_import import StateImportService
from codemie.rest_api.utils.default_applications import create_default_applications
from codemie.triggers.node_controller import NodeController
from external.deployment_scripts.preconfigured_assistants import manage_preconfigured_assistants
from external.deployment_scripts.preconfigured_skills import manage_preconfigured_skills
from external.deployment_scripts.preconfigured_workflows import create_preconfigured_workflows
from external.deployment_scripts.preconfigured_katas import import_preconfigured_katas
from codemie.clients.postgres import alembic_upgrade_enterprise_postgres, alembic_upgrade_postgres
from codemie.service.analytics.cli_analytics_jobs import start_cli_analytics_runtime, stop_cli_analytics_runtime
from codemie.service.budget.startup_reconciliation_service import budget_startup_reconciliation_service
from codemie.service.customer_config_service import override_cache, refresh_overrides

# Rate limiting imports (EPMCDME-10160)
from slowapi.middleware import SlowAPIMiddleware
from codemie.rest_api.middleware.security_headers import HSTS_HEADER_VALUE, SecurityHeadersMiddleware
from slowapi.errors import RateLimitExceeded
from codemie.rest_api.rate_limit import limiter
from codemie.rest_api.litellm_models_recovery import (
    LITELLM_MODELS_LOADED,
    LITELLM_MODELS_NOT_APPLICABLE,
    LITELLM_MODELS_UNAVAILABLE,
    initialize_litellm_models as _initialize_litellm_models,
    schedule_litellm_models_recovery as _schedule_litellm_models_recovery,
)


def _setup_litellm_cache_cleanup_scheduler():
    """
    Start cache cleanup schedulers for LiteLLM customer and model caches.

    Sets up periodic cleanup jobs using APScheduler based on configured TTL values.
    """
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from codemie.service.llm_proxy.provider_registry import get_active_llm_proxy_provider

    provider = get_active_llm_proxy_provider()
    if not provider.is_available():
        logger.warning("LLM proxy provider not available, skipping cache cleanup scheduler")
        return

    litellm_scheduler = AsyncIOScheduler()

    # Customer cache cleanup
    customer_cleanup_interval_minutes = max(1, config.LITELLM_CUSTOMER_CACHE_TTL // 60)
    litellm_scheduler.add_job(
        provider.clean_expired_customer_cache,
        "interval",
        minutes=customer_cleanup_interval_minutes,
        id="litellm_customer_cache_cleanup",
        replace_existing=True,
    )

    # Models cache cleanup
    models_cleanup_interval_minutes = max(1, config.LITELLM_MODELS_CACHE_TTL // 60)
    litellm_scheduler.add_job(
        provider.clean_expired_models_cache,
        "interval",
        minutes=models_cleanup_interval_minutes,
        id="litellm_models_cache_cleanup",
        replace_existing=True,
    )

    litellm_scheduler.start()
    logger.info(
        f"LiteLLM cache cleanup schedulers started "
        f"(customer: every {customer_cleanup_interval_minutes} min, "
        f"models: every {models_cleanup_interval_minutes} min)"
    )


def _setup_memory_profiling_scheduler():
    """
    Start memory profiling scheduler for periodic snapshot collection.

    Captures memory snapshots at regular intervals and stores them using FileRepositoryFactory.
    Snapshots are compressed with gzip and stored in cloud storage (S3/Azure/GCP) or local filesystem.

    Controlled by environment variables:
    - MEMORY_PROFILING_ENABLED: Enable/disable memory profiling (default: False)
    - MEMORY_PROFILING_INTERVAL_MINUTES: Snapshot interval (default: 30)
    - FILES_STORAGE_TYPE: Storage backend (filesystem, aws, azure, gcp)

    Note: Retention/cleanup is managed by cloud storage lifecycle policies (S3/Azure/GCP),
    not by the application. Configure lifecycle rules in your cloud provider.
    """
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from codemie.service.monitoring.memory_profiling_service import memory_profiling_service

    # Start tracemalloc tracking
    if memory_profiling_service.start_tracking():
        memory_scheduler = AsyncIOScheduler()

        # Add periodic snapshot job
        # max_instances=1 prevents overlapping executions (skips if previous still running)
        # coalesce=True means if multiple runs were missed, only execute once
        memory_scheduler.add_job(
            memory_profiling_service.take_snapshot,
            "interval",
            minutes=config.MEMORY_PROFILING_INTERVAL_MINUTES,
            id="memory_snapshot_collection",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        memory_scheduler.start()
        logger.info(
            f"Memory profiling scheduler started "
            f"(snapshots: every {config.MEMORY_PROFILING_INTERVAL_MINUTES} min, "
            f"storage: {config.FILES_STORAGE_TYPE})"
        )
    else:
        logger.warning("Failed to start memory profiling - tracemalloc initialization failed")


async def _initialize_plugin_service():
    """
    Initialize plugin enterprise service with configuration and dependencies.

    Creates plugin service with all dependencies injected and initializes it.

    Returns:
        Initialized PluginService or None if initialization failed
    """
    # Create plugin service from config with dependencies injected
    plugin_service = initialize_plugin_from_config()
    if not plugin_service:
        logger.info("Plugin service not available or disabled")
        return None

    try:
        # Initialize the service
        success = await plugin_service.initialize()
        if success:
            logger.info("Plugin enterprise service initialized")
            return plugin_service
        else:
            logger.warning("Plugin service initialization failed")
            return None

    except Exception as e:
        logger.error(f"Failed to initialize plugin service: {e}", exc_info=True)
        return None


_MODEL_PROVIDER_READINESS_TIMEOUT_SECONDS = 5


async def _check_model_provider_readiness(app: FastAPI) -> None:
    """Compute model-provider startup readiness once, store it, and log it.

    Never blocks startup — the app must start whether or not a provider is configured
    (EPMCDME-14653). The check runs its synchronous, potentially slow work (the AWS
    credential chain has no overall deadline of its own) off the event loop via
    asyncio.to_thread, bounded by a wait_for timeout so a hanging credential_process
    cannot stall startup. A bug in, or timeout of, the check itself must not be able to
    abort startup either, so both degrade to a "not checked" readiness rather than
    propagating.
    """
    try:
        readiness = await asyncio.wait_for(
            asyncio.to_thread(check_model_provider_readiness),
            timeout=_MODEL_PROVIDER_READINESS_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Model provider readiness check timed out after %ss; treating as not checked",
            _MODEL_PROVIDER_READINESS_TIMEOUT_SECONDS,
        )
        readiness = ModelProviderReadiness(
            status=ModelProviderStatus.NOT_CHECKED,
            provider=None,
            missing=["model provider readiness check timed out; see startup logs"],
        )
    except Exception:
        logger.exception("Model provider readiness check failed")
        readiness = ModelProviderReadiness(
            status=ModelProviderStatus.NOT_CHECKED,
            provider=None,
            missing=["model provider readiness check failed; see startup logs"],
        )
    app.state.model_provider_readiness = readiness
    log_model_provider_readiness(readiness)


async def _initialize_enterprise_services(app: FastAPI):
    """Initialize observability and LiteLLM enterprise services."""
    observability_provider = get_observability_provider()
    observability_provider.initialize()
    app.state.observability_provider = observability_provider

    litellm_service = initialize_litellm_from_config()
    app.state.litellm_service = litellm_service
    set_global_litellm_service(litellm_service)

    if litellm_service is not None and config.LLM_PROXY_BUDGET_CHECK_ENABLED:
        try:
            team_provisioned = await asyncio.to_thread(litellm_service.ensure_team_exists)
            if team_provisioned:
                logger.info("LiteLLM codemie-projects team provisioning ensured")
            else:
                logger.warning("LiteLLM codemie-projects team could not be confirmed/created")
        except Exception as e:
            logger.warning(f"Failed to ensure LiteLLM codemie-projects team exists: {e}")

    if litellm_service is not None:
        from codemie.enterprise.litellm.llm_proxy_provider_adapter import LiteLLMLLMProxyProvider
        from codemie.service.llm_proxy.provider_registry import register_llm_proxy_provider

        register_llm_proxy_provider(LiteLLMLLMProxyProvider(litellm_service))
        logger.info("LiteLLM proxy lifecycle provider registered")

    if litellm_service is not None and config.LLM_PROXY_BUDGET_CHECK_ENABLED:
        from codemie.enterprise.litellm.budget_provider_adapter import LiteLLMBudgetEnforcementProvider
        from codemie.service.budget.provider_registry import register_budget_enforcement_provider

        register_budget_enforcement_provider(LiteLLMBudgetEnforcementProvider(litellm_service))
        logger.info("LiteLLM budget enforcement provider registered")

    _register_spend_ledgers()


def _register_spend_ledgers() -> None:
    """Register the spend ledgers - the one port every spend figure is read through.

    Two registrations, and they are **not** gated on the same condition. The core side owns the
    classes LiteLLM does not route, and registers even in a deployment with no enterprise package at
    all. The LiteLLM side is registered only where the routing predicate actually selects it - which
    is narrower than "LiteLLM is enabled": that flag is true in deployments whose traffic never
    reaches LiteLLM, and registering there would point every consumer at a table no request writes
    to, so every figure would come back unavailable.

    A failure to register is logged and swallowed. An unregistered ledger reports **unavailable**,
    never zero, so a spend figure degrades while the application still starts - no request has ever
    depended on a spend read.

    **The coverage values are read once here, before either ledger, and registered for both.** Two of
    the four - the emission-lag allowance `L` and the paging threshold `N` - bound the metric-store
    window `SpendService` resolves ids over, which happens in every deployment regardless of which
    adapter answers a figure, so they cannot hang off a provider branch. One read also keeps the
    property the coverage design rests on: a floor becomes visible to every consumer at the same
    instant.

    **The pre-floor cutover is wrapped on last, once no further ledger can arrive.** It re-registers
    whichever ledgers the two steps above left in the registry, each one composed over the legacy
    Elasticsearch engine so periods below the coverage floor keep answering instead of reporting
    unavailable. Wrapping earlier would wrap only what had been registered by then and let the rest
    land unwrapped afterwards - a cutover covering half a deployment, which looks registered and is
    not. It is gated on its own opt-in flag inside `register_spend_cutover`, so a deployment that has
    not opted in ends this function with exactly the registry it had before the step existed.
    """
    from codemie.service.spend.composition import load_ledger_coverage, register_spend_ledgers
    from codemie.service.spend.ledger_registry import register_ledger_coverage

    try:
        register_ledger_coverage(load_ledger_coverage())
    except Exception as error:
        # Swallowed like every other failure here. Unregistered coverage leaves the floor unset,
        # which reports every figure unavailable rather than serving one against a floor nobody
        # published - and the LiteLLM ledger below is then built on that same honest state.
        logger.error(f"Failed to read the spend ledger coverage: {error}", exc_info=True)

    try:
        register_spend_ledgers()
    except Exception as error:
        logger.error(f"Failed to register the default spend ledger: {error}", exc_info=True)

    from codemie.service.spend.routing import LLMCallClass, uses_litellm

    # A branch rather than an early return: every deployment has to reach the cutover step below, and
    # the one that would skip it is the fully-native one - the deployment whose figures are entirely
    # pre-floor and therefore the one the step exists for.
    if any(uses_litellm(call_class) for call_class in LLMCallClass):
        try:
            from codemie.enterprise.litellm.spend_ledger import build_spend_logs_ledger
            from codemie.service.spend.ledger_registry import ledger_coverage, register_spend_logs_ledger

            # The coverage read above, not a second read of the same configuration: two reads could
            # observe a value mid-change and put two consumers on opposite sides of one floor.
            register_spend_logs_ledger(build_spend_logs_ledger(ledger_coverage()))
            logger.info("LiteLLM spend ledger registered")
        except Exception as error:
            logger.error(f"Failed to register the LiteLLM spend ledger: {error}", exc_info=True)

    try:
        from codemie.service.spend.composition import register_spend_cutover

        register_spend_cutover()
    except Exception as error:
        # Swallowed like the registrations above, and the slot keeps whatever it already holds: a
        # ledger that answers above the floor and reports unavailable below it is the behaviour of
        # every deployment before this step, whereas an emptied slot would report unavailable
        # everywhere - a working figure turned off by a failure to improve it.
        logger.error(f"Failed to register the pre-floor spend cutover: {error}", exc_info=True)


def _setup_litellm_features(app: FastAPI):
    """Initialize LiteLLM features if enabled and record the models status on app state."""
    if not is_litellm_enabled():
        app.state.litellm_models_status = LITELLM_MODELS_NOT_APPLICABLE
        return

    loaded = _initialize_litellm_models()
    app.state.litellm_models_status = LITELLM_MODELS_LOADED if loaded else LITELLM_MODELS_UNAVAILABLE
    if config.LLM_PROXY_BUDGET_CHECK_ENABLED:
        _setup_litellm_cache_cleanup_scheduler()


async def ensure_predefined_budgets() -> None:
    """Backward-compatible startup entrypoint for predefined budget initialization."""
    from codemie.enterprise.litellm import ensure_predefined_budgets as _ensure_predefined_budgets

    await _ensure_predefined_budgets()


def _schedule_startup_recovery(tasks: list[asyncio.Task]) -> None:
    """Schedule orphaned workflow state recovery as a background task after readiness.

    Running this after yield means the app is considered ready immediately; the scan
    happens in a thread so the event loop is not blocked.
    """
    from codemie.service.workflow_execution.startup_recovery import recover_orphaned_workflow_states

    started_before = datetime.now()
    recovery_task = asyncio.create_task(
        asyncio.to_thread(recover_orphaned_workflow_states, started_before),
        name="startup_recovery",
    )
    tasks.append(recovery_task)


async def _warm_customer_config() -> None:
    """Load the dynamic-configuration snapshot before the app serves traffic.

    Ordered before ``_initialize_optional_features``, which reads a feature flag itself.
    A failure here leaves the service on YAML; it never blocks start-up.
    """
    try:
        await refresh_overrides()
    except Exception as error:
        logger.warning(f"Customer config warm-up failed, serving YAML until the next refresh. {error=}")


def _schedule_customer_config_refresh(tasks: list[asyncio.Task]) -> None:
    """Keep the override snapshot fresh for synchronous readers that never touch /v1/config."""

    async def _loop() -> None:
        # Sleep first: the lifespan already warmed the snapshot moments ago. The interval is
        # clamped so a TTL of 0 cannot turn this into a hot loop against the database.
        while True:
            await asyncio.sleep(max(override_cache.ttl_seconds, 1))
            await _warm_customer_config()

    tasks.append(asyncio.create_task(_loop(), name="customer_config_refresh"))


def _schedule_event_loop_lag_monitor(tasks: list[asyncio.Task]) -> None:
    """Sample event-loop lag for the life of the app unless the interval is 0 (EPMCDME-15537).

    A blocking call stalls every request on the worker, and only the lag names that cost.
    """
    from codemie.service.monitoring.event_loop_monitor import monitor_event_loop_lag

    if config.EVENT_LOOP_LAG_MONITOR_INTERVAL_SECONDS <= 0:
        return
    tasks.append(
        asyncio.create_task(
            monitor_event_loop_lag(config.EVENT_LOOP_LAG_MONITOR_INTERVAL_SECONDS, config.EVENT_LOOP_LAG_WARN_SECONDS),
            name="event_loop_lag_monitor",
        )
    )


def _schedule_budget_reconciliation(app: FastAPI, tasks: list[asyncio.Task]) -> None:
    """Schedule budget reconciliation after readiness if enabled."""
    if not is_litellm_enabled():
        return

    if not config.LLM_PROXY_BUDGET_CHECK_ENABLED:
        logger.info("Budget check disabled, skipping background task scheduling")
        return

    if not config.LLM_PROXY_BUDGET_RECONCILIATION_ENABLED:
        logger.info("Budget startup reconciliation disabled, skipping background task scheduling")
        return

    reconciliation_task = asyncio.create_task(
        budget_startup_reconciliation_service.run(),
        name="budget_startup_reconciliation",
    )
    app.state.budget_reconciliation_task = reconciliation_task
    tasks.append(reconciliation_task)
    logger.info("Budget startup reconciliation task scheduled")


def _initialize_database_and_defaults():
    """Run database migrations and create default data."""
    alembic_upgrade_postgres()
    alembic_upgrade_enterprise_postgres()
    create_default_applications()


def _initialize_preconfigured_content():
    """Create preconfigured assistants, skills, workflows, and katas.

    Must run after _setup_litellm_features() so llm_service.default_llm_model
    resolves against LiteLLM proxy models rather than the YAML fallback.
    """
    manage_preconfigured_assistants()
    manage_preconfigured_skills()
    create_preconfigured_workflows()
    import_preconfigured_katas()


def _initialize_optional_features():
    """Initialize optional features like tool indexing and platform datasources."""
    if config.TOOL_SELECTION_ENABLED:
        from codemie.service.tools.toolkit_lookup_service import ToolkitLookupService

        indexed_count = ToolkitLookupService.index_all_tools()
        logger.info(f"SmartToolSelection: Successfully indexed {indexed_count} tools on startup")

    if config.PLATFORM_DATASOURCES_SYNC_ENABLED:
        from codemie.service.platform.platform_indexing_service import PlatformIndexingService

        results = PlatformIndexingService.sync_all_platform_datasources()
        logger.info(f"Platform datasources synced successfully: {results}")

    if _customer_config.is_feature_enabled("subWorkflow") and config.SUBWORKFLOW_POOL_ENABLED:
        from codemie.service.workflow_pool import workflow_pool

        workflow_pool.initialize()
        logger.info("WorkflowPool initialized for sub-workflow pre-compilation")


def _check_usage_attribution_settings() -> None:
    """Validate the Usage Attribution settings of the customer configuration when the feature is on.

    A value outside its bounds is reported here, at startup; the feature's endpoints then answer 500 until it is
    fixed, so a setting can never default to something the Analysis Settings dialog would reject.
    """
    from codemie.service.usage_attribution.constants import FEATURE_KEY

    if not _customer_config.is_feature_enabled(FEATURE_KEY):
        return
    try:
        from codemie.service.usage_attribution.handler import load_defaults

        load_defaults()
        logger.info("Usage Attribution: settings validated")
    except Exception as exc:
        logger.error(f"Usage Attribution settings are invalid: {exc}")


def _check_sharepoint_pkce_redis() -> None:
    """Warn at startup if SharePoint PKCE is enabled but Redis is unreachable."""
    if not config.SHAREPOINT_PKCE_ENABLED:
        return
    try:
        from codemie.clients.redis import create_redis_client

        create_redis_client().ping()
        logger.info("SharePoint PKCE: Redis connection verified")
    except Exception as exc:
        logger.warning(f"SharePoint PKCE: Redis unavailable at startup, PKCE flow will fail: {exc}")


def _run_standalone_startup() -> None:
    """Single entry point for standalone-image startup behaviour; no-op unless STANDALONE is set."""
    if not config.STANDALONE:
        return
    logger.info("Standalone mode enabled")
    _log_core_gating()
    if config.MCP_CONNECT_ENABLED and not config.MCP_CONNECT_URL:
        logger.warning(
            "MCP_CONNECT_ENABLED is set but MCP_CONNECT_URL is empty; MCP Connect is degraded in "
            "standalone mode. The following features are affected: MCP Servers on assistants, "
            "workflow MCP tools, the admin MCP page, the MCP settings tab, and the MCP onboarding "
            "steps. Set MCP_CONNECT_URL or set MCP_CONNECT_ENABLED=false to disable intentionally."
        )

    from codemie.service.file_service.mermaid_service import MermaidService

    if not MermaidService.is_configured():
        logger.warning(
            "Standalone: Mermaid rendering is not configured (set MERMAID_SERVER_URL or "
            "MERMAID_USE_MERMAID_INC=true). Degraded features: chat diagrams, workflow graph preview, "
            "stored workflow diagram image, conversation export diagrams."
        )

    from codemie.configs.code_executor_availability import warn_code_executor_unavailable

    warn_code_executor_unavailable()

    from codemie.clients.redis import redis_configured

    if not redis_configured():
        if not config.GOOGLE_OAUTH_ENABLED:
            logger.warning(
                "Standalone: Redis is not configured, so Google OAuth stays disabled. Set "
                "REDIS_HOST (and GOOGLE_OAUTH_ENABLED=true) to enable it."
            )
        else:
            logger.warning(
                "Standalone: Google OAuth requires Redis. Redis is required but not configured, "
                "so Google OAuth stays disabled until REDIS_HOST is set."
            )
        if config.WEBHOOK_RATE_LIMIT_ENABLED:
            logger.warning(
                "Standalone: Redis is not configured, so webhook rate limiting is disabled. "
                "Webhooks are accepted without rate limiting until REDIS_HOST is set."
            )


def _initialize_deployment_version() -> None:
    """Record first-start timestamp for APP_VERSION if not already stored.

    Non-fatal: a warning is logged and the application continues if the DB is
    unavailable at startup. Environment is implied by which database is configured.
    """
    try:
        from codemie.service.deployment.deployment_version_service import deployment_version_service

        deployment_version_service.record_if_absent()
        logger.info(f"Deployment version recorded: version={config.APP_VERSION!r}")
    except Exception as exc:
        logger.warning(f"Failed to record deployment version at startup: {exc}")


def _setup_conversation_analysis_scheduler(app: FastAPI):
    """Setup conversation analysis scheduler if enabled."""
    if not config.CONVERSATION_ANALYSIS_ENABLED:
        return

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from codemie.service.conversation_analysis.scheduler import ConversationAnalysisScheduler
    from codemie.service.conversation_analysis.conversation_analytics_elasticsearch_service import (
        ConversationAnalyticsElasticsearchService,
    )

    try:
        ConversationAnalyticsElasticsearchService.create_index_if_not_exists()
    except Exception as e:
        logger.warning(f"Failed to create conversation analytics Elasticsearch index: {e}")

    analysis_scheduler = AsyncIOScheduler()
    conversation_analysis_scheduler = ConversationAnalysisScheduler(scheduler=analysis_scheduler)
    conversation_analysis_scheduler.start()
    app.state.conversation_analysis_scheduler = conversation_analysis_scheduler
    logger.info("Conversation analysis scheduler started successfully")


def _setup_budget_reset_scheduler(app: FastAPI):
    """Set up the budget reset reconciliation scheduler if enabled."""
    if not config.LITELLM_BUDGET_RESET_RECONCILIATION_ENABLED:
        return

    if not config.LLM_PROXY_ENABLED:
        logger.warning(
            "Budget reset scheduler enabled but LLM_PROXY_ENABLED=False; "
            "budget reset reconciliation requires the LiteLLM proxy — skipping scheduler setup"
        )
        return

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from codemie.service.budget_reset.scheduler import BudgetResetScheduler

    budget_reset_scheduler_instance = AsyncIOScheduler()
    budget_reset_scheduler = BudgetResetScheduler(scheduler=budget_reset_scheduler_instance)
    budget_reset_scheduler.start()
    app.state.budget_reset_scheduler = budget_reset_scheduler
    logger.info("Budget reset scheduler started successfully")


def _setup_spend_uploader_scheduler(app: FastAPI):
    """Set up the LiteLLM spend uploader scheduler if enabled."""
    if not config.LITELLM_SPEND_UPLOADER_ENABLED:
        return

    if not config.LLM_PROXY_ENABLED:
        logger.warning(
            "Spend uploader scheduler enabled but LLM_PROXY_ENABLED=False; "
            "the spend uploader requires the LiteLLM proxy — skipping scheduler setup"
        )
        return

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from codemie.service.spend_uploader.scheduler import SpendUploaderScheduler

    spend_uploader_scheduler = SpendUploaderScheduler(scheduler=AsyncIOScheduler())
    spend_uploader_scheduler.start()
    app.state.spend_uploader_scheduler = spend_uploader_scheduler
    logger.info("Spend uploader scheduler started successfully")


def _setup_leaderboard_scheduler(app: FastAPI):
    """Setup leaderboard computation scheduler if enabled."""
    if not config.LEADERBOARD_ENABLED:
        return

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from codemie.service.leaderboard.scheduler import LeaderboardScheduler

    leaderboard_scheduler_instance = AsyncIOScheduler()
    leaderboard_scheduler = LeaderboardScheduler(scheduler=leaderboard_scheduler_instance)
    leaderboard_scheduler.start()
    app.state.leaderboard_scheduler = leaderboard_scheduler
    logger.info("Leaderboard scheduler started successfully")


def _setup_inactive_project_budget_scheduler(app: FastAPI):
    """Setup inactive project budget stop scheduler if enabled."""
    if not config.BUDGET_STOP_ENABLED:
        return

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from codemie.service.budget.inactive_project_budget_scheduler import InactiveProjectBudgetScheduler

    scheduler_instance = AsyncIOScheduler()
    scheduler = InactiveProjectBudgetScheduler(scheduler=scheduler_instance)
    scheduler.start()
    app.state.inactive_project_budget_scheduler = scheduler
    logger.info("Inactive project budget stop scheduler started successfully")


def _setup_inactive_cost_center_budget_scheduler(app: FastAPI):
    """Setup inactive cost center budget stop scheduler if enabled."""
    if not config.BUDGET_STOP_ENABLED:
        return

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from codemie.service.budget.inactive_cost_center_budget_scheduler import InactiveCostCenterBudgetScheduler

    scheduler_instance = AsyncIOScheduler()
    scheduler = InactiveCostCenterBudgetScheduler(scheduler=scheduler_instance)
    scheduler.start()
    app.state.inactive_cost_center_budget_scheduler = scheduler
    logger.info("Inactive cost center budget stop scheduler started successfully")


def _setup_active_project_budget_scheduler(app: FastAPI):
    """Setup active project budget restore scheduler if enabled."""
    if not config.BUDGET_STOP_ENABLED:
        return

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from codemie.service.budget.active_project_budget_scheduler import ActiveProjectBudgetScheduler

    scheduler_instance = AsyncIOScheduler()
    scheduler = ActiveProjectBudgetScheduler(scheduler=scheduler_instance)
    scheduler.start()
    app.state.active_project_budget_scheduler = scheduler
    logger.info("Active project budget restore scheduler started successfully")


def _setup_stale_datasource_scheduler(app: FastAPI):
    """Setup stale datasource detection scheduler if enabled."""
    if not config.STALE_DATASOURCE_ENABLED:
        return

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from codemie.service.stale_datasource.scheduler import StaleDatasourceScheduler

    stale_datasource_scheduler_instance = AsyncIOScheduler()
    stale_datasource_scheduler = StaleDatasourceScheduler(scheduler=stale_datasource_scheduler_instance)
    stale_datasource_scheduler.start()
    app.state.stale_datasource_scheduler = stale_datasource_scheduler
    logger.info("Stale datasource scheduler started successfully")


def _setup_activity_events_retention_scheduler(app: FastAPI):
    """Schedule a daily purge of activity events older than ACTIVITY_EVENTS_RETENTION_DAYS."""
    if not config.ACTIVITY_EVENTS_ENABLED:
        return
    if config.ACTIVITY_EVENTS_RETENTION_DAYS <= 0:
        return

    from datetime import datetime, timedelta, timezone

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from sqlmodel import Session

    from codemie.clients.postgres import PostgresClient
    from codemie.service.activity.activity_repository import activity_event_repository
    from codemie.utils.leader_lock import async_leader_lock

    _lock_id = 987654326

    async def _purge():
        async with async_leader_lock(_lock_id) as acquired:
            if not acquired:
                logger.info("[activity_events] retention purge: not the leader, skipping")
                return

            def _do_purge():
                cutoff = datetime.now(timezone.utc) - timedelta(days=config.ACTIVITY_EVENTS_RETENTION_DAYS)
                with Session(PostgresClient.get_engine()) as session:
                    deleted = activity_event_repository.delete_older_than(cutoff, session)
                    session.commit()
                return deleted, cutoff.date()

            try:
                deleted, cutoff_date = await asyncio.to_thread(_do_purge)
                logger.info(
                    f"[activity_events] retention purge: deleted {deleted} event(s) older than {cutoff_date} "
                    f"(retention={config.ACTIVITY_EVENTS_RETENTION_DAYS} days)"
                )
            except Exception as exc:
                logger.error(f"[activity_events] retention purge failed: {exc}", exc_info=True)

    retention_scheduler = AsyncIOScheduler()
    retention_scheduler.add_job(
        _purge,
        CronTrigger.from_crontab("0 2 * * *", timezone="UTC"),
        id="activity_events_retention_purge",
        replace_existing=True,
    )
    retention_scheduler.start()
    app.state.activity_events_retention_scheduler = retention_scheduler
    logger.info(
        f"Activity events retention scheduler started "
        f"(retention={config.ACTIVITY_EVENTS_RETENTION_DAYS} days, runs daily at 02:00 UTC)"
    )


def _setup_marketplace_audit_reaper_scheduler(app: FastAPI):
    """Sweep for audit runs whose worker died, under the same leader lock.

    design-persistence.md 7, F4. The audit worker is a `daemon=True` bare
    thread: it dies with its process, leaving a `mm_audit_run` row at `RUNNING`
    that is indistinguishable from a live one. That row holds the partial unique
    index `uq_mm_audit_run_active`, so until somebody clears it by hand **no
    pod** can start another audit of that entity kind — the invariant that stops
    two pods auditing at once becomes the thing that stops anyone auditing at
    all.

    Armed by default, unlike the retention purge. Retention deletes history and
    so is opt-in; this only relabels a run whose owner is already gone, and the
    cost of leaving it off is a deployment-wide deadlock after any pod restart
    mid-audit — and `rollout.yaml` restarts pods mid-audit on every deploy.

    The sweep also runs once at startup: the pod that just replaced the dead one
    is exactly the process best placed to notice what it left behind.
    """
    if not config.MARKETPLACE_MANAGEMENT_ENABLED:
        return
    if config.MARKETPLACE_AUDIT_HEARTBEAT_TIMEOUT_MINUTES <= 0:
        return

    from datetime import datetime, timedelta, timezone

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    # Dedicated to this reaper: see MR !4070 review comment #15 — reusing
    # 987654328 (stale-datasource job, active-project budget restore) silently
    # starved those jobs whenever this one held the advisory lock.
    _lock_id = 987654329

    async def _reap():
        from codemie.utils import leader_lock as leader_lock_module

        async with leader_lock_module.async_leader_lock(_lock_id) as acquired:
            if not acquired:
                logger.debug("[marketplace_audit] reaper: not the leader, skipping")
                return

            stale_after = timedelta(minutes=config.MARKETPLACE_AUDIT_HEARTBEAT_TIMEOUT_MINUTES)

            def _do_reap():
                from codemie.service.marketplace_management import audit_service

                return audit_service.reap_stale_audit_runs(stale_after=stale_after)

            try:
                reaped = await asyncio.to_thread(_do_reap)
                if reaped:
                    logger.warning(
                        f"[marketplace_audit] reaper: failed {reaped} abandoned audit run(s) "
                        f"with no heartbeat for {config.MARKETPLACE_AUDIT_HEARTBEAT_TIMEOUT_MINUTES} "
                        "minute(s); their entity kind can be audited again"
                    )
            except Exception as exc:
                # Log and continue, exactly as the retention purge does: a
                # re-raise here would surface only under APScheduler's own
                # logger name, where nobody is looking.
                logger.error(f"[marketplace_audit] reaper sweep failed: {exc}", exc_info=True)

    reaper_scheduler = AsyncIOScheduler()
    reaper_scheduler.add_job(
        _reap,
        IntervalTrigger(minutes=config.MARKETPLACE_AUDIT_REAPER_INTERVAL_MINUTES),
        id="marketplace_audit_reaper",
        replace_existing=True,
        # Sweep once on startup, then on the interval.
        next_run_time=datetime.now(timezone.utc),
    )
    reaper_scheduler.start()
    app.state.marketplace_audit_reaper_scheduler = reaper_scheduler


def _setup_marketplace_audit_retention_scheduler(app: FastAPI):
    """Schedule a daily purge of audit runs older than MARKETPLACE_AUDIT_RETENTION_DAYS.

    Mirrors `_setup_activity_events_retention_scheduler` above: same daily cron,
    same `async_leader_lock` guard so N pods do not all purge at once, same
    log-and-continue on failure. The statement itself
    (`design-persistence.md` 6.5) lives in
    `service/marketplace_management/audit_retention.py` and never deletes the
    newest SUCCEEDED run of an entity kind, whatever its age.

    Deleting a run reclaims its verdicts, findings and similarity rows through
    the `ON DELETE CASCADE` foreign keys migration `n1a2u3d4i5t6` installs, so
    one statement per entity kind is the whole job.
    """
    if not config.MARKETPLACE_MANAGEMENT_ENABLED:
        return
    if config.MARKETPLACE_AUDIT_RETENTION_DAYS <= 0:
        return

    from datetime import datetime, timedelta, timezone

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    # Dedicated to this purge: see MR !4070 review comment #15 — reusing
    # 987654327 (metrics rotation, inactive-cost-center budget stop) silently
    # starved those jobs whenever this one held the advisory lock.
    _lock_id = 987654330

    async def _purge():
        from codemie.utils import leader_lock as leader_lock_module

        async with leader_lock_module.async_leader_lock(_lock_id) as acquired:
            if not acquired:
                logger.info("[marketplace_audit] retention purge: not the leader, skipping")
                return

            def _do_purge():
                import sqlmodel

                from codemie.clients.postgres import PostgresClient
                from codemie.service.marketplace_management import audit_retention

                cutoff = datetime.now(timezone.utc) - timedelta(days=config.MARKETPLACE_AUDIT_RETENTION_DAYS)
                removed = 0
                with sqlmodel.Session(PostgresClient.get_engine()) as session:
                    for entity_kind in ("ASSISTANT", "WORKFLOW"):
                        removed += audit_retention.marketplace_audit_retention_repository.delete_older_than(
                            entity_kind=entity_kind, cutoff=cutoff, session=session
                        )
                    session.commit()
                return removed, cutoff.date()

            try:
                removed, cutoff_date = await asyncio.to_thread(_do_purge)
                logger.info(
                    f"[marketplace_audit] retention purge: deleted {removed} audit run(s) "
                    f"older than {cutoff_date} "
                    f"(retention={config.MARKETPLACE_AUDIT_RETENTION_DAYS} days)"
                )
            except Exception as exc:
                logger.error(f"[marketplace_audit] retention purge failed: {exc}", exc_info=True)

    retention_scheduler = AsyncIOScheduler()
    retention_scheduler.add_job(
        _purge,
        CronTrigger.from_crontab("30 2 * * *", timezone="UTC"),
        id="marketplace_audit_retention_purge",
        replace_existing=True,
    )
    retention_scheduler.start()
    app.state.marketplace_audit_retention_scheduler = retention_scheduler
    logger.info(
        f"Marketplace audit retention scheduler started "
        f"(retention={config.MARKETPLACE_AUDIT_RETENTION_DAYS} days, runs daily at 02:30 UTC)"
    )


def _setup_marketplace_moderation_purge_scheduler(app: FastAPI):
    """Daily sweep of moderation data whose subject no longer exists (8.7; F-39, R12, S16).

    Separate from `_setup_marketplace_audit_retention_scheduler` on purpose, and
    NOT gated on `MARKETPLACE_AUDIT_RETENTION_DAYS`. The two jobs answer
    different questions:

      * retention asks "how much history do we keep?" -- destructive, reversible
        only by not having run it, and therefore opt-in (it ships at 0 = keep
        everything);
      * this asks "may we still hold moderation data about content that has been
        deleted?" -- and the answer does not depend on an operator preference.
        Hiding it behind the retention switch would leave F-39 live on every
        installation that never turns retention on, which is all of them by
        default.

    `Assistant._purge_marketplace_moderation_data` already runs the same purge
    inline on every delete. This sweep exists for what that cannot reach: every
    assistant deleted before 8.7 shipped, and any delete whose purge failed or
    whose process died between the two statements.

    Scoped to ASSISTANT because that is the only entity kind whose ids are
    catalogued in `assistants`; a WORKFLOW verdict's `assistant_id` is a
    workflow id and is *supposed* to be absent from that table.
    """
    if not config.MARKETPLACE_MANAGEMENT_ENABLED:
        return

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    # Distinct from the retention lock (987654330) so the two jobs do not
    # serialise behind one another on the same night, and from the reaper's
    # lock (987654329) and every other job's lock — see MR !4070 review
    # comment #15, which flagged this reusing 987654328 (stale-datasource job,
    # active-project budget restore) and silently starving them.
    _lock_id = 987654331

    async def _sweep():
        from codemie.utils import leader_lock as leader_lock_module

        async with leader_lock_module.async_leader_lock(_lock_id) as acquired:
            if not acquired:
                logger.info("[marketplace_audit] moderation purge: not the leader, skipping")
                return

            def _do_sweep():
                import sqlmodel

                from codemie.clients.postgres import PostgresClient
                from codemie.service.marketplace_management import audit_retention

                with sqlmodel.Session(PostgresClient.get_engine()) as session:
                    reclaimed = audit_retention.marketplace_moderation_purge_repository.purge_orphans(
                        entity_kind="ASSISTANT", session=session
                    )
                    session.commit()
                return reclaimed

            try:
                reclaimed = await asyncio.to_thread(_do_sweep)
                logger.info(
                    f"[marketplace_audit] moderation purge: reclaimed moderation data for "
                    f"{reclaimed} deleted assistant(s)"
                )
            except Exception as exc:
                logger.error(f"[marketplace_audit] moderation purge failed: {exc}", exc_info=True)

    purge_scheduler = AsyncIOScheduler()
    purge_scheduler.add_job(
        _sweep,
        # 03:10 UTC: after the 02:30 retention purge, so a night where both run
        # sweeps a table retention has already shrunk.
        CronTrigger.from_crontab("10 3 * * *", timezone="UTC"),
        id="marketplace_moderation_purge",
        replace_existing=True,
    )
    purge_scheduler.start()
    app.state.marketplace_moderation_purge_scheduler = purge_scheduler
    logger.info("Marketplace moderation purge scheduler started (runs daily at 03:10 UTC)")


def _setup_metrics_rotation_scheduler(app: FastAPI):
    """Setup quarterly metrics index rotation scheduler."""
    if not config.METRICS_ROTATION_ENABLED:
        return
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from codemie.service.metrics_rotation.scheduler import MetricsRotationScheduler

    rotation_scheduler_instance = AsyncIOScheduler()
    rotation_scheduler = MetricsRotationScheduler(scheduler=rotation_scheduler_instance)
    rotation_scheduler.start()
    app.state.metrics_rotation_scheduler = rotation_scheduler
    logger.info("Metrics rotation scheduler started successfully")


def _initialize_jwt_keys():
    """Auto-generate RSA keys for local auth if not present (EPMCDME-10160)"""
    if config.IDP_PROVIDER == "local" and config.ENABLE_USER_MANAGEMENT:
        try:
            from codemie.service.jwt_service import jwt_service

            jwt_service.load_or_create_keys()
            logger.info("JWT keys loaded/created successfully")
        except Exception as e:
            logger.error(f"Failed to initialize JWT keys: {e}")
            # Don't crash - local auth endpoints will fail but IDP still works


def _bootstrap_superadmin():
    """Bootstrap SuperAdmin user if configured and none exists (EPMCDME-10160)"""
    if not (
        config.ENABLE_USER_MANAGEMENT
        and config.SUPERADMIN_EMAIL
        and config.SUPERADMIN_PASSWORD
        and config.IDP_PROVIDER == "local"
    ):
        return

    try:
        from codemie.service.user.user_management_service import user_management_service

        # Delegate to service layer (manages session internally)
        user_management_service.bootstrap_superadmin_startup(
            email=config.SUPERADMIN_EMAIL, password=config.SUPERADMIN_PASSWORD
        )
    except Exception as e:
        logger.error(f"SuperAdmin bootstrap failed: {e}")
        # Don't crash app - SuperAdmin can be created manually via API


async def _run_keycloak_migration() -> None:
    """Bulk-migrate Keycloak users to DB at startup (cluster-safe, idempotent)."""
    if not (config.KEYCLOAK_MIGRATION_ENABLED and config.IDP_PROVIDER == "keycloak" and config.ENABLE_USER_MANAGEMENT):
        return
    try:
        from codemie.enterprise.migration.coordinator import run_keycloak_migration

        await run_keycloak_migration()
    except Exception as e:
        logger.error(f"Keycloak migration failed: {e}", exc_info=True)
        # Non-fatal: log and continue. App starts regardless.


def _stop_scheduler(app: FastAPI, attr_name: str, display_name: str, method: str = "stop"):
    scheduler = getattr(app.state, attr_name, None)
    if scheduler is not None:
        getattr(scheduler, method)()
        logger.info(f"{display_name} shutdown complete")


async def _shutdown_services(app: FastAPI, tasks: list):
    """Shutdown all services and background tasks."""
    from codemie.service.llm_proxy.provider_registry import get_active_llm_proxy_provider

    logger.info("Shutting down CodeMie application...")

    await shutdown_mcp_auth()

    observability_provider = getattr(app.state, "observability_provider", None)
    if observability_provider is not None:
        observability_provider.shutdown()
        logger.info("Observability provider shutdown complete")

    get_active_llm_proxy_provider().close()

    plugin_service = get_global_plugin_service()
    if plugin_service is not None:
        try:
            await plugin_service.shutdown()
            logger.info("Plugin service shutdown complete")
        except Exception as e:
            logger.error(f"Error shutting down plugin service: {e}", exc_info=True)

    _stop_scheduler(app, 'conversation_analysis_scheduler', 'Conversation analysis scheduler')
    _stop_scheduler(app, 'budget_reset_scheduler', 'Budget reset scheduler')
    _stop_scheduler(app, 'spend_uploader_scheduler', 'Spend uploader scheduler')
    _stop_scheduler(app, 'leaderboard_scheduler', 'Leaderboard scheduler')
    _stop_scheduler(app, 'stale_datasource_scheduler', 'Stale datasource scheduler')
    _stop_scheduler(
        app, 'activity_events_retention_scheduler', 'Activity events retention scheduler', method='shutdown'
    )
    _stop_scheduler(
        app, 'marketplace_audit_retention_scheduler', 'Marketplace audit retention scheduler', method='shutdown'
    )
    _stop_scheduler(
        app, 'marketplace_moderation_purge_scheduler', 'Marketplace moderation purge scheduler', method='shutdown'
    )
    _stop_scheduler(app, 'marketplace_audit_reaper_scheduler', 'Marketplace audit reaper scheduler', method='shutdown')
    _stop_scheduler(app, 'metrics_rotation_scheduler', 'Metrics rotation scheduler')

    from codemie.service.usage_attribution.analysis import shutdown_analysis_service

    shutdown_analysis_service()
    _stop_scheduler(app, 'inactive_project_budget_scheduler', 'Inactive project budget stop scheduler')
    _stop_scheduler(app, 'inactive_cost_center_budget_scheduler', 'Inactive cost center budget stop scheduler')
    _stop_scheduler(app, 'active_project_budget_scheduler', 'Active project budget restore scheduler')
    await stop_cli_analytics_runtime(getattr(app.state, "cli_analytics_jobs", None))

    await close_llm_proxy_client()
    logger.info("LLM Proxy HTTP client closed")

    prometheus_metrics_server = getattr(app.state, "prometheus_metrics_server", None)
    prometheus_metrics_task = getattr(app.state, "prometheus_metrics_task", None)
    if prometheus_metrics_server is not None:
        prometheus_metrics_server.should_exit = True
        if prometheus_metrics_task is not None:
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(prometheus_metrics_task, timeout=5.0)
        logger.info("Prometheus metrics server shutdown complete")

    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    logger.info("CodeMie application shutdown complete")

    # Flush any remaining spans before the process exits.
    from opentelemetry import trace as otel_trace

    otel_provider = otel_trace.get_tracer_provider()
    if hasattr(otel_provider, "shutdown"):
        otel_provider.shutdown()


def _instrument_sqlalchemy_engines() -> None:
    # Instrument SQLAlchemy engines explicitly after they are created.
    # PostgresClient uses from-imports (sqlmodel.create_engine /
    # sqlalchemy.ext.asyncio.create_async_engine) whose references are captured at import
    # time, so the module-level monkey-patch in SQLAlchemyInstrumentor would never reach
    # them.  Passing engine instances directly here ensures both the pool "connect" spans
    # and the actual SQL cursor-execute spans appear as children of the HTTP span.
    if not config.OTEL_ENABLED:
        return
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from codemie.clients.postgres import PostgresClient
    from codemie.configs.otel_config import enrich_sqlalchemy_spans
    from sqlalchemy.engine import Engine
    from sqlalchemy.ext.asyncio import AsyncEngine

    sync_engine = PostgresClient.get_engine()
    async_engine = PostgresClient.get_async_engine()
    engines: list[Engine] = []

    if isinstance(sync_engine, Engine):
        engines.append(sync_engine)
    else:
        logger.debug(f"Skipping SQLAlchemy OTel instrumentation for non-engine sync target: {type(sync_engine)!r}")

    if isinstance(async_engine, AsyncEngine):
        engines.append(async_engine.sync_engine)
    else:
        logger.debug(f"Skipping SQLAlchemy OTel instrumentation for non-engine async target: {type(async_engine)!r}")

    if engines:
        SQLAlchemyInstrumentor().instrument(engines=engines)
        # Enrich span names from "SELECT" to "SELECT <table>" for better visibility.
        # Must be called after instrument() so our listener runs after OTel's.
        enrich_sqlalchemy_spans(engines)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting CodeMie application. Config={config.to_safe_dict()}")

    if config.STANDALONE and not retrieval_available(config):
        logger.warning(
            "Retrieval capability unavailable (ELASTIC_URL is empty). Knowledge bases, "
            "datasources and code indexing are disabled. To enable retrieval, set ELASTIC_URL."
        )
    elif not retrieval_available(config):
        logger.warning(
            "Retrieval capability unavailable (RETRIEVAL_BACKEND=none). Knowledge bases, "
            "datasources and code indexing are disabled. To enable retrieval, set "
            "RETRIEVAL_BACKEND to a supported backend and configure its connection settings."
        )

    from codemie.core.event_loop import set_main_event_loop

    set_main_event_loop(asyncio.get_running_loop())

    # Initialize database and default data
    _initialize_database_and_defaults()

    # Register enterprise IDP providers (MUST be before first auth request)
    from codemie.enterprise.idp import register_enterprise_idps

    register_enterprise_idps()

    initialize_mcp_auth()

    if config.JWKS_VALIDATION_ENABLED:
        from codemie.rest_api.security.jwks.runtime import jwks_warmup

        await jwks_warmup()

    # Initialize enterprise services
    await _initialize_enterprise_services(app)

    # Setup LiteLLM features
    _setup_litellm_features(app)

    # Create preconfigured content after LiteLLM is initialized so the correct
    # default model is used instead of the YAML fallback.
    _initialize_preconfigured_content()

    _instrument_sqlalchemy_engines()

    register_prometheus_db_pool_metrics()

    if config.PROMETHEUS_ENABLED:
        metrics_task, metrics_server = await start_metrics_server()
        app.state.prometheus_metrics_server = metrics_server
        app.state.prometheus_metrics_task = metrics_task

    # Initialize JWT keys and SuperAdmin for user management (EPMCDME-10160)
    _initialize_jwt_keys()
    _bootstrap_superadmin()
    await _run_keycloak_migration()

    # Load dynamic configuration before anything reads a feature flag
    await _warm_customer_config()

    # Initialize optional features
    _initialize_optional_features()
    _check_sharepoint_pkce_redis()
    _check_usage_attribution_settings()
    _run_standalone_startup()
    assert_oauth_state_signing_secret_configured()
    assert_token_vault_available()
    warn_insecure_oauth_storage_for_enabled_providers()
    warn_marketplace_approval_gate_enabled()
    await _check_model_provider_readiness(app)
    _initialize_deployment_version()

    # Start background tasks
    tasks = []
    _schedule_customer_config_refresh(tasks)
    _schedule_litellm_models_recovery(app, tasks)
    if config.TRIGGER_ENGINE_ENABLED:
        tasks.append(asyncio.create_task(NodeController().start()))

    # Initialize plugin service
    plugin_service = await _initialize_plugin_service()
    if plugin_service:
        app.state.plugin_service = plugin_service
        set_global_plugin_service(plugin_service)

    # Setup optional schedulers
    if config.MEMORY_PROFILING_ENABLED:
        _setup_memory_profiling_scheduler()

    _setup_conversation_analysis_scheduler(app)
    _setup_budget_reset_scheduler(app)
    _setup_spend_uploader_scheduler(app)
    _setup_inactive_project_budget_scheduler(app)
    _setup_inactive_cost_center_budget_scheduler(app)
    _setup_active_project_budget_scheduler(app)
    _setup_leaderboard_scheduler(app)
    _setup_stale_datasource_scheduler(app)
    _setup_activity_events_retention_scheduler(app)
    _setup_marketplace_audit_retention_scheduler(app)
    _setup_marketplace_moderation_purge_scheduler(app)
    _setup_marketplace_audit_reaper_scheduler(app)

    _setup_metrics_rotation_scheduler(app)
    _schedule_budget_reconciliation(app, tasks)
    _schedule_startup_recovery(tasks)

    # CLI Analytics storage: PostgreSQL prepares its schema and runs rollup/partition jobs.
    app.state.cli_analytics_jobs = await start_cli_analytics_runtime()

    await _start_hosted_applications(app)

    # Last, so the blocking startup work above is not reported as event-loop lag (EPMCDME-15537).
    _schedule_event_loop_lag_monitor(tasks)

    yield

    # Cleanup on shutdown
    await _stop_hosted_applications(app)
    await _shutdown_services(app, tasks)


async def _stop_hosted_applications(app: FastAPI) -> None:
    if getattr(app.state, "hosted_applications", None):
        await app.state.hosted_applications.close()


async def _start_hosted_applications(app: FastAPI) -> None:
    """Hosted applications never block platform startup: a failure is logged and leaves the feature closed."""
    from codemie.core.hosted_applications.settings import log_disabled_reason
    from codemie.service.hosted_applications.runtime import HostedApplicationsRuntime

    app.state.hosted_applications = None
    hosted = None
    try:
        hosted = HostedApplicationsRuntime(config)
        log_disabled_reason(hosted.settings)
        await hosted.start()
    except Exception as exc:
        logger.error(f"Hosted applications failed to start and stay unavailable: {type(exc).__name__}", exc_info=True)
        if hosted is not None:
            try:
                await hosted.close()
            except Exception as close_error:
                logger.warning(f"Hosted applications cleanup after a failed start: {type(close_error).__name__}")
        return
    app.state.hosted_applications = hosted


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title="Codemie",
        version=config.APP_VERSION,
        description=APP_DESCRIPTION,
        routes=schema_routes(app.routes),
    )
    openapi_schema["servers"] = [{"url": config.API_ROOT_PATH}]

    # Add Bearer JWT authentication to Swagger UI
    openapi_schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "Enter the JWT token obtained from /v1/local-auth/login",
    }
    openapi_schema["security"] = [{"BearerAuth": []}]

    app.openapi_schema = openapi_schema
    return app.openapi_schema


def _get_origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


WEBAPP_CORS = [_get_origin(config.FRONTEND_URL)]

if config.ENV != ENV_LOCAL:
    WEBAPP_CORS.append("http://localhost:3000")


app = FastAPI(lifespan=lifespan)
app.openapi = custom_openapi

# Setup rate limiting for user management endpoints (EPMCDME-10160)
app.state.limiter = limiter


async def _friendly_rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    client_ip = request.client.host if request.client else "unknown"
    logger.warning(f"Rate limit exceeded for {request.method} {request.url.path} from {client_ip}")
    response = JSONResponse(
        status_code=429,
        content={
            "error": {
                "message": "Too many attempts. Please wait and try again later.",
                "details": None,
                "help": None,
            }
        },
    )
    response = request.app.state.limiter._inject_headers(response, request.state.view_rate_limit)
    return response


app.add_exception_handler(RateLimitExceeded, _friendly_rate_limit_handler)
app.add_middleware(SlowAPIMiddleware)

if ElasticSearchClient.is_configured():
    StateImportService().import_indexes()


def register_marketplace_management_router(target_app: FastAPI) -> bool:
    """Mount the marketplace-management router unless the feature is switched off.

    Kill switch for the moderation feature (MARKETPLACE_MANAGEMENT_ENABLED).
    When off, the routes are never registered, so they 404 and do not appear in
    the OpenAPI schema. Returns whether the router was mounted.

    SYNC NOTE (CR-011): Two feature-flag mechanisms protect the marketplace
    management surface:
      1. This startup-time mount guard (here): the 16 /v1/marketplace-management
         routes are only registered when the feature is enabled.
      2. A runtime HTTPException guard in admin.py (POST /admin/marketplace/reindex):
         that single admin endpoint lives on the admin router (always mounted) and
         therefore needs its own per-request check.
    Both guards call is_marketplace_management_enabled(). If you add a new
    marketplace endpoint to the admin router rather than the marketplace_management
    router, you MUST add the same runtime guard to that endpoint. If you switch
    this mount guard to unconditional (runtime-dependency approach), remove the
    runtime guard in admin.py at the same time.
    """
    if not is_marketplace_management_enabled():
        logger.info("Marketplace management feature is disabled; router not mounted")
        return False
    target_app.include_router(marketplace_management_router)
    return True


if ElasticSearchClient.is_configured():
    app.include_router(index.router)
app.include_router(common.router)
app.include_router(feedback.router)
app.include_router(admin.router)
register_marketplace_management_router(app)
app.include_router(a2a.router)
app.include_router(assistant_mapping.router)
app.include_router(assistant.router)
app.include_router(assistant_prompt_variable_mapping.router)
app.include_router(category.router)
app.include_router(conversation.router)
app.include_router(conversation_analysis.router)
app.include_router(share.router)
app.include_router(workflow.router)
app.include_router(workflow_marketplace.router)
app.include_router(workflow_executions.router)
app.include_router(webhook.router)
app.include_router(skill_events.router)
app.include_router(skill.router)
app.include_router(schedulers.router)
app.include_router(background_tasks.router)
app.include_router(user.router)
app.include_router(user_settings.router)
app.include_router(project_settings.router)
app.include_router(settings.router)
app.include_router(projects.router)
app.include_router(cost_centers.router)
app.include_router(llm_models.router)
app.include_router(llm_models.proxy_router)
app.include_router(guardrail.router)
app.include_router(vendor.router)
app.include_router(files.router)
app.include_router(agent_workspace.router)
app.include_router(customer_config.router)
app.include_router(provider.router)
app.include_router(tool.router)
app.include_router(ide.router)
app.include_router(permission.router)
app.include_router(callbacks.router)
app.include_router(auth.router)
app.include_router(metrics.router)
app.include_router(analytics.router)
app.include_router(cli_analytics.router)
app.include_router(logs.router)
app.include_router(mcp_config.router)
app.include_router(mcp_managed.router)
app.include_router(user_kata_progress.router)
app.include_router(ai_kata.router)
app.include_router(export.router)
app.include_router(dynamic_config.router)
from codemie.rest_api.routers import hosted_applications  # noqa: E402

app.include_router(hosted_applications.router)
if is_litellm_enabled() and config.LLM_PROXY_BUDGET_CHECK_ENABLED:
    app.include_router(budget_router.router)
app.include_router(project_budget_router.router)
app.include_router(project_budget_router.group_router)
app.include_router(sharepoint_oauth.router)
app.include_router(google_oauth.router)
app.include_router(gitlab_oauth.router)
app.include_router(jira_oauth.router)
app.include_router(confluence_oauth.router)
app.include_router(user_preferences_router.router)
app.include_router(user_profile_settings_router.router)

# User management routers (EPMCDME-10160)
if config.ENABLE_USER_MANAGEMENT:
    app.include_router(user_management_router.router)
    app.include_router(user_profile_router.router)
    if config.IDP_PROVIDER == "local":
        app.include_router(local_auth_router.router)
    app.include_router(activity_events_router.router)

mount_provisioning_api(app)

app.include_router(get_mcp_auth_router())
app.include_router(get_cimd_router())


@app.middleware("http")
async def add_disconnect_handler(request: Request, call_next):
    async def wait_for_disconnect():
        while True:
            if await request.is_disconnected():
                break
            message = await request.receive()
            if message["type"] == "http.disconnect":
                break
        if request.state.disconnect_handler:
            request.state.disconnect_handler()

    def on_disconnect(handler):
        request.state.disconnect_handler = handler
        if request._is_disconnected:
            handler()

    request.state.disconnect_handler = None
    request.state.wait_for_disconnect = wait_for_disconnect
    request.state.on_disconnect = on_disconnect

    return await call_next(request)


@app.middleware("http")
async def configure_logging(request: Request, call_next):
    """
    Middleware to set the logging UUID for each request
    """
    uuid_str = request.headers.get("X-Request-ID", str(uuid.uuid4()))

    request.state.uuid = uuid_str
    set_logging_info(uuid=uuid_str, user_id="", conversation_id="-")
    set_client_source(request.headers.get(HEADER_CODEMIE_CLIENT))

    # Attach the Codemie request ID to the active OTel span (created by
    # FastAPIInstrumentor before this middleware runs) so traces can be
    # correlated with X-Request-ID in logs and external systems.
    from opentelemetry import trace as otel_trace

    current_span = otel_trace.get_current_span()
    if current_span.is_recording():
        current_span.set_attribute("codemie.request_id", uuid_str)

    return await call_next(request)


INTERNAL_ERROR_MESSAGE = "An internal error occurred. Please contact support."
INTERNAL_ERROR_DETAILS = "An unexpected error occurred while processing the request."


def _internal_error_details(request: Request) -> str:
    details = INTERNAL_ERROR_DETAILS
    if request_id := getattr(request.state, "uuid", None):
        details += f" Request ID: {request_id}"
    return details


@app.middleware("http")
async def handle_exceptions(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as e:
        logger.error(e, exc_info=True)

        if config.is_local:
            raise e

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "message": INTERNAL_ERROR_MESSAGE,
                    "details": _internal_error_details(request),
                    "help": "Please try again later or contact support if the problem persists.",
                }
            },
        )


async def add_hsts_header(request: Request, call_next):
    """Kept for backward compatibility; HSTS is now set by SecurityHeadersMiddleware."""
    response = await call_next(request)
    response.headers["Strict-Transport-Security"] = HSTS_HEADER_VALUE
    return response


@app.exception_handler(ApiError)
async def elastic_exception_handler(request: Request, exception: ApiError) -> JSONResponse:
    """
    Handles Elastic-related exceptions
    """
    logger.error(f"Elastic service error: {exception}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "error": {
                "message": "Elastic service unavailable",
                "details": _internal_error_details(request),
                "help": "This is likely a temporary issue. Please try again later. If the problem persists, "
                "contact the system administrator.",
            }
        },
    )


@app.exception_handler(ValidationException)
async def domain_validation_exception_handler(request: Request, exc: ValidationException) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": {"message": str(exc), "details": None, "help": None}},
    )


@app.exception_handler(NotFoundException)
async def not_found_exception_handler(request: Request, exc: NotFoundException) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"error": {"message": str(exc), "details": None, "help": None}},
    )


@app.exception_handler(ExtendedHTTPException)
async def extended_http_exception_handler(request: Request, exc: ExtendedHTTPException):
    """
    Exception handler for ExtendedHTTPException that extends the built-in Exception
    class to provide more detailed HTTP error information.

    This handler catches ExtendedHTTPException instances and converts them into
    a standardized JSON response.

    Note:
        This handler ensures that all fields from the ExtendedHTTPException
        are included in the response, providing a consistent error reporting
        structure across the API.
    """
    if exc.code >= status.HTTP_500_INTERNAL_SERVER_ERROR and not exc.expected:
        logger.error(exc.details, exc_info=True)
        if exc.code == status.HTTP_500_INTERNAL_SERVER_ERROR:
            return JSONResponse(
                status_code=exc.code,
                content={
                    "error": {"message": exc.message, "details": _internal_error_details(request), "help": exc.help}
                },
            )
    else:
        msg = "Status: {exc.code}, Error: {exc.message}, Details: {exc.details}, Help: {exc.help}".format(exc=exc)
        logger.warning(msg)
    return JSONResponse(
        status_code=exc.code, content={"error": {"message": exc.message, "details": exc.details, "help": exc.help}}
    )


@app.exception_handler(BrokerAuthRequiredException)
async def broker_auth_required_handler(request: Request, exc: BrokerAuthRequiredException) -> JSONResponse:
    """
    Returns HTTP 401 when a broker token exchange fails.

    Includes the ``x-user-mcp-auth-location`` header so clients know where
    to re-authenticate. The header is omitted if ``BROKER_AUTH_LOCATION_URL``
    is not configured.
    """
    logger.warning(f"Broker authentication required: {exc.details}")
    headers = {}
    error_body: dict = {"message": exc.message, "details": exc.details}
    if exc.auth_location:
        headers["x-user-mcp-auth-location"] = exc.auth_location
        error_body["login_url"] = exc.auth_location
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"error": error_body},
        headers=headers,
    )


@app.exception_handler(MCPAuthenticationRequiredException)
async def mcp_auth_required_handler(request: Request, exc: MCPAuthenticationRequiredException) -> JSONResponse:
    logger.warning(f"MCP authentication required: {exc.payload}")
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content=exc.payload,
    )


@app.exception_handler(GitLabAuthRequiredException)
async def gitlab_auth_required_handler(request: Request, exc: GitLabAuthRequiredException) -> JSONResponse:
    logger.warning(f"GitLab authentication required: {exc.payload}")
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content=exc.payload,
    )


@app.exception_handler(JiraAuthRequiredException)
async def jira_auth_required_handler(request: Request, exc: JiraAuthRequiredException) -> JSONResponse:
    logger.warning(f"Jira authentication required: {exc.payload}")
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content=exc.payload,
    )


@app.exception_handler(ConfluenceAuthRequiredException)
async def confluence_auth_required_handler(request: Request, exc: ConfluenceAuthRequiredException) -> JSONResponse:
    logger.warning(f"Confluence authentication required: {exc.payload}")
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content=exc.payload,
    )


@app.exception_handler(OAuthConnectRequiredException)
async def oauth_connect_required_handler(request: Request, exc: OAuthConnectRequiredException) -> JSONResponse:
    logger.warning(f"OAuth connect required (aggregate): {exc.payload}")
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content=exc.payload,
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, str):
        # Validation errors can echo lone surrogates, which JSONResponse cannot encode as UTF-8.
        return value.encode("utf-8", errors="backslashreplace").decode("utf-8")
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {_json_safe(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _sanitize_error(error: dict) -> dict:
    ctx = error.get("ctx")
    if ctx and isinstance(ctx, dict):
        sanitized_ctx = {
            k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v for k, v in ctx.items()
        }
        error = {**error, "ctx": sanitized_ctx}
    return _json_safe(error)


def _validation_error_details(exc: RequestValidationError) -> tuple[list, list[str]]:
    errors = [_sanitize_error(e) for e in exc.errors()]
    detailed_errors: list[str] = []
    for error in errors:
        loc = error.get("loc", [])
        msg = error.get("msg", "Validation error")
        detailed_errors.append(f"{_validation_error_path(loc)}: {msg}")
    return errors, detailed_errors


def _validation_error_path(loc: list) -> str:
    path_parts: list[str] = []
    for item in loc:
        if item == "body":
            continue
        if isinstance(item, int):
            path_parts[-1] = f"{path_parts[-1]}[{item}]"
            continue
        path_parts.append(str(item))
    return ".".join(path_parts) if path_parts else "request"


def _validation_error_message(detailed_errors: list[str]) -> str:
    if not detailed_errors:
        return "Validation Error"
    if len(detailed_errors) == 1:
        return detailed_errors[0]
    return "; ".join(detailed_errors)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """
    Exception handler for FastAPI's RequestValidationError.

    This handler catches validation errors that occur when incoming request data
    fails to meet the expected schema or type constraints and converts them into
    a standardized JSON response with detailed field information.
    """
    errors, detailed_errors = _validation_error_details(exc)
    error_message = _validation_error_message(detailed_errors)

    logger.warning(
        f"Request validation failed: status=422, method={request.method}, path={request.url.path}, "
        f"errors={detailed_errors or errors}"
    )

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "message": error_message,
                "details": errors,
            }
        },
    )


from codemie.hosted_apps_gateway.management import HostedManagementBodyBoundary  # noqa: E402

# Bounds hosted JSON mutation bodies. Registered just before CORS so it runs inside CORSMiddleware and its
# 408/413 refusals carry the same CORS headers as every other management response.
app.add_middleware(HostedManagementBodyBoundary)

app.add_middleware(
    CORSMiddleware,
    allow_origins=WEBAPP_CORS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "ETag", "Retry-After"],
)
app.add_middleware(SecurityHeadersMiddleware)

# Initialize OTEL at module level so that OTelMiddleware is included in the
# middleware stack from its first compile (before the first HTTP request).
# Must come after all app.add_middleware / @app.middleware registrations above.
from codemie.configs.otel_config import configure_opentelemetry, configure_prometheus_metrics  # noqa: E402
from codemie.configs.prometheus_config import (  # noqa: E402
    configure_prometheus_http_metrics,
    register_prometheus_db_pool_metrics,
    start_metrics_server,
)

configure_opentelemetry(app)

# Prometheus: set up OTel MeterProvider with PrometheusMetricReader so that all
# OTel business metrics (BaseMonitoringService counters/histograms) are bridged
# to Prometheus scrape format.  Must run before any metrics are recorded.
configure_prometheus_metrics()

# Every tool reports how long its own work took (direct calls and calls made by workspace scripts).
from codemie.service.monitoring.sinks import register_monitoring_sinks  # noqa: E402

register_monitoring_sinks()

if config.PROMETHEUS_ENABLED:
    configure_prometheus_http_metrics(app)


# Pyroscope: register endpoint-tagging middleware and start the profiler.
# Must come after OTEL so that profiling spans the full request including OTEL overhead.
if config.PYROSCOPE_ENABLED:
    from codemie.configs.pyroscope_config import configure_pyroscope  # noqa: E402
    from codemie.rest_api.middleware.pyroscope_middleware import (  # noqa: E402
        pyroscope_endpoint_tagging_middleware,
    )

    app.middleware("http")(pyroscope_endpoint_tagging_middleware)
    configure_pyroscope()


from codemie.core.hosted_applications.errors import HostedApplicationError  # noqa: E402


@app.exception_handler(HostedApplicationError)
async def hosted_application_failure(request: Request, exc: HostedApplicationError):
    """Bounded public hosted error; 5xx are logged with code, route and exc_info, never bodies or credentials."""
    from codemie.hosted_apps_gateway.management import hosted_error_response

    return hosted_error_response(request, exc)


# Host dispatch wraps the whole application: the content namespace reaches only the closed content tree,
# every other Host (including probes and in-cluster callers) reaches this management application.
if config.HOSTED_APPS_CONTENT_DOMAIN:
    from codemie.core.hosted_applications.settings import HostedSettings
    from codemie.hosted_apps_gateway.dispatch import protect_content_hosts

    app = protect_content_hosts(app, HostedSettings.from_config(config))


if __name__ == '__main__':
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, workers=config.WORKERS)
