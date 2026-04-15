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
import uuid
import traceback
from contextlib import asynccontextmanager

from elasticsearch import ApiError
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
from codemie.configs import config
from codemie.configs.config import ENV_LOCAL
from codemie.enterprise.langfuse import (
    initialize_langfuse_from_config,
    set_global_langfuse_service,
)
from codemie.enterprise.litellm import (
    close_llm_proxy_client,
    ensure_predefined_budgets,
    initialize_litellm_from_config,
    is_litellm_enabled,
    set_global_litellm_service,
)
from codemie.enterprise.plugin import (
    initialize_plugin_from_config,
    set_global_plugin_service,
    get_global_plugin_service,
)
from codemie.configs.logger import set_logging_info, logger
from codemie.core.constants import APP_DESCRIPTION
from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.security.token_providers.base_provider import BrokerAuthRequiredException
from codemie.rest_api.routers import budget_router
from codemie.rest_api.routers import (
    guardrail,
    index,
    common,
    admin,
    feedback,
    background_tasks,
    assistant,
    assistant_mapping,
    assistant_prompt_variable_mapping,
    category,
    vendor,
    workflow,
    workflow_executions,
    user_settings,
    project_settings,
    projects,
    cost_centers,
    llm_models,
    files,
    conversation,
    conversation_analysis,
    webhook,
    user,
    customer_config,
    provider,
    tool,
    share,
    a2a,
    ide,
    permission,
    auth,
    metrics,
    analytics,
    callbacks,
    logs,
    mcp_config,
    ai_kata,
    user_kata_progress,
    skill,
    dynamic_config,
)
from codemie.rest_api.routers import sharepoint_oauth

# User management routers (EPMCDME-10160)
from codemie.rest_api.routers import local_auth_router
from codemie.rest_api.routers import user_management_router
from codemie.rest_api.routers import user_profile_router
from codemie.rest_api.utils.state_import import StateImportService
from codemie.rest_api.utils.default_applications import create_default_applications
from codemie.triggers.node_controller import NodeController
from external.deployment_scripts.preconfigured_assistants import manage_preconfigured_assistants
from external.deployment_scripts.preconfigured_skills import manage_preconfigured_skills
from external.deployment_scripts.preconfigured_workflows import create_preconfigured_workflows
from external.deployment_scripts.preconfigured_katas import import_preconfigured_katas
from codemie.clients.postgres import alembic_upgrade_postgres

# Rate limiting imports (EPMCDME-10160)
from slowapi.middleware import SlowAPIMiddleware
from slowapi.errors import RateLimitExceeded
from codemie.rest_api.rate_limit import limiter


def _initialize_litellm_models():
    """
    Initialize default LiteLLM models from proxy if enabled.

    Fetches models from LiteLLM proxy via enterprise and stores them in llm_service.
    """
    from codemie.enterprise.litellm import get_available_models
    from codemie.service.llm_service.llm_service import llm_service

    try:
        # Fetch models from LiteLLM via enterprise (already mapped to LLMModel)
        models = get_available_models()

        # Store in llm_service
        llm_service.initialize_default_litellm_models(models)

        logger.info(
            f"Initialized {len(models.chat_models)} chat models, and  {len(models.embedding_models)} embedding models."
        )
    except Exception as e:
        logger.error(f"Failed to initialize LiteLLM models: {e}")


def _setup_litellm_cache_cleanup_scheduler():
    """
    Start cache cleanup schedulers for LiteLLM customer and model caches.

    Sets up periodic cleanup jobs using APScheduler based on configured TTL values.
    """
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from codemie.enterprise.litellm import get_litellm_service_or_none

    # Get enterprise service
    litellm_service = get_litellm_service_or_none()
    if litellm_service is None:
        logger.warning("LiteLLM enterprise service not available, skipping cache cleanup scheduler")
        return

    litellm_scheduler = AsyncIOScheduler()

    # Customer cache cleanup
    customer_cleanup_interval_minutes = max(1, config.LITELLM_CUSTOMER_CACHE_TTL // 60)
    litellm_scheduler.add_job(
        litellm_service.clean_expired_cache,
        "interval",
        minutes=customer_cleanup_interval_minutes,
        id="litellm_customer_cache_cleanup",
        replace_existing=True,
    )

    # Models cache cleanup
    models_cleanup_interval_minutes = max(1, config.LITELLM_MODELS_CACHE_TTL // 60)
    litellm_scheduler.add_job(
        litellm_service.clean_expired_models_cache,
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
            logger.info("✓ Plugin enterprise service initialized")
            return plugin_service
        else:
            logger.warning("✗ Plugin service initialization failed")
            return None

    except Exception as e:
        logger.error(f"✗ Failed to initialize plugin service: {e}", exc_info=True)
        return None


def _initialize_enterprise_services(app: FastAPI) -> tuple:
    """Initialize Langfuse and LiteLLM enterprise services."""
    langfuse_service = initialize_langfuse_from_config()
    app.state.langfuse_service = langfuse_service
    set_global_langfuse_service(langfuse_service)

    litellm_service = initialize_litellm_from_config()
    app.state.litellm_service = litellm_service
    set_global_litellm_service(litellm_service)

    return langfuse_service, litellm_service


def _setup_litellm_features():
    """Initialize LiteLLM features if enabled."""
    if is_litellm_enabled():
        _initialize_litellm_models()
        if config.LLM_PROXY_BUDGET_CHECK_ENABLED:
            _setup_litellm_cache_cleanup_scheduler()


def _initialize_database_and_defaults():
    """Run database migrations and create default data."""
    alembic_upgrade_postgres()
    create_default_applications()
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


def _setup_spend_tracking_scheduler(app: FastAPI):
    """Setup spend tracking collector scheduler if enabled."""
    if not config.LITELLM_SPEND_COLLECTOR_ENABLED:
        return

    if not config.LLM_PROXY_ENABLED:
        logger.warning(
            "LITELLM_SPEND_COLLECTOR_ENABLED=True but LLM_PROXY_ENABLED=False; "
            "spend collector requires the LiteLLM proxy — skipping scheduler setup"
        )
        return

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from codemie.service.spend_tracking.scheduler import SpendTrackingScheduler

    spend_tracking_scheduler_instance = AsyncIOScheduler()
    spend_tracking_scheduler = SpendTrackingScheduler(scheduler=spend_tracking_scheduler_instance)
    spend_tracking_scheduler.start()
    app.state.spend_tracking_scheduler = spend_tracking_scheduler
    logger.info("Spend tracking scheduler started successfully")


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
        and config.ENV != ENV_LOCAL
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


async def _shutdown_services(app: FastAPI, langfuse_service, litellm_service, tasks: list):
    """Shutdown all services and background tasks."""
    logger.info("Shutting down CodeMie application...")

    if langfuse_service is not None:
        langfuse_service.shutdown()
        logger.info("LangFuse service shutdown complete")

    if litellm_service is not None:
        litellm_service.close()
        logger.info("LiteLLM service shutdown complete")

    plugin_service = get_global_plugin_service()
    if plugin_service is not None:
        try:
            await plugin_service.shutdown()
            logger.info("Plugin service shutdown complete")
        except Exception as e:
            logger.error(f"Error shutting down plugin service: {e}", exc_info=True)

    conversation_analysis_scheduler = getattr(app.state, 'conversation_analysis_scheduler', None)
    if conversation_analysis_scheduler is not None:
        conversation_analysis_scheduler.stop()
        logger.info("Conversation analysis scheduler shutdown complete")

    spend_tracking_scheduler = getattr(app.state, 'spend_tracking_scheduler', None)
    if spend_tracking_scheduler is not None:
        spend_tracking_scheduler.stop()
        logger.info("Spend tracking scheduler shutdown complete")

    leaderboard_scheduler = getattr(app.state, 'leaderboard_scheduler', None)
    if leaderboard_scheduler is not None:
        leaderboard_scheduler.stop()
        logger.info("Leaderboard scheduler shutdown complete")

    await close_llm_proxy_client()
    logger.info("LLM Proxy HTTP client closed")

    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    logger.info("CodeMie application shutdown complete")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting CodeMie application. Config={config.to_safe_dict()}")

    # Initialize database and default data
    _initialize_database_and_defaults()

    # Register enterprise IDP providers (MUST be before first auth request)
    from codemie.enterprise.idp import register_enterprise_idps

    register_enterprise_idps()

    # Initialize enterprise services
    langfuse_service, litellm_service = _initialize_enterprise_services(app)

    # Setup LiteLLM features
    _setup_litellm_features()
    if is_litellm_enabled() and config.LLM_PROXY_BUDGET_CHECK_ENABLED:
        await ensure_predefined_budgets()

    # Initialize JWT keys and SuperAdmin for user management (EPMCDME-10160)
    _initialize_jwt_keys()
    _bootstrap_superadmin()
    await _run_keycloak_migration()

    # Initialize optional features
    _initialize_optional_features()

    # Start background tasks
    tasks = []
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
    _setup_spend_tracking_scheduler(app)
    _setup_leaderboard_scheduler(app)

    yield

    # Cleanup on shutdown
    await _shutdown_services(app, langfuse_service, litellm_service, tasks)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title="Codemie",
        version=config.APP_VERSION,
        description=APP_DESCRIPTION,
        routes=app.routes,
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

StateImportService().import_indexes()

app.include_router(a2a.router)
app.include_router(assistant.router)
app.include_router(assistant_mapping.router)
app.include_router(assistant_prompt_variable_mapping.router)
app.include_router(category.router)
app.include_router(index.router)
app.include_router(common.router)
app.include_router(feedback.router)
app.include_router(admin.router)
app.include_router(background_tasks.router)
app.include_router(conversation.router)
app.include_router(conversation_analysis.router)
app.include_router(user.router)
app.include_router(workflow.router)
app.include_router(workflow_executions.router)
app.include_router(user_settings.router)
app.include_router(project_settings.router)
app.include_router(projects.router)
app.include_router(cost_centers.router)
app.include_router(llm_models.router)
app.include_router(llm_models.proxy_router)
app.include_router(guardrail.router)
app.include_router(vendor.router)
app.include_router(files.router)
app.include_router(webhook.router)
app.include_router(customer_config.router)
app.include_router(provider.router)
app.include_router(tool.router)
app.include_router(share.router)
app.include_router(ide.router)
app.include_router(permission.router)
app.include_router(callbacks.router)
app.include_router(auth.router)
app.include_router(metrics.router)
app.include_router(analytics.router)
app.include_router(logs.router)
app.include_router(mcp_config.router)
app.include_router(user_kata_progress.router)
app.include_router(ai_kata.router)
app.include_router(skill.router)
app.include_router(dynamic_config.router)
app.include_router(budget_router.router)
app.include_router(sharepoint_oauth.router)

# User management routers (EPMCDME-10160)
if config.ENABLE_USER_MANAGEMENT:
    app.include_router(user_management_router.router)
    app.include_router(user_profile_router.router)
    if config.IDP_PROVIDER == "local":
        app.include_router(local_auth_router.router)


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
    set_logging_info(uuid=uuid_str, user_id="")

    return await call_next(request)


@app.middleware("http")
async def handle_exceptions(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as e:
        logger.error(e, exc_info=True)

        if config.is_local:
            raise e

        trace = traceback.format_exc()[-2000:]

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "message": "Internal Server Error. <br> Stacktrace: {}".format(trace),
                    "details": "An unexpected error occurred while processing the request.",
                    "help": "Please try again later or contact support if the problem persists.",
                }
            },
        )


@app.exception_handler(ApiError)
async def elastic_exception_handler(request: Request, exception: ApiError) -> JSONResponse:
    """
    Handles Elastic-related exceptions
    """
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "error": {
                "message": "Elastic service unavailable",
                "details": f"An error occurred while communicating with the Elastic service: {str(exception)}",
                "help": "This is likely a temporary issue. Please try again later. If the problem persists, "
                "contact the system administrator.",
            }
        },
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
    if exc.code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        logger.error(exc.details, exc_info=True)
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


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """
    Exception handler for FastAPI's RequestValidationError.

    This handler catches validation errors that occur when incoming request data
    fails to meet the expected schema or type constraints and converts them into
    a standardized JSON response with detailed field information.
    """
    logger.exception(exc)

    # Extract detailed error information
    errors = exc.errors()

    # Format errors with field location and message
    detailed_errors = []
    for error in errors:
        # Get field path (e.g., ['body', 'history', 0, 'role'])
        loc = error.get("loc", [])
        msg = error.get("msg", "Validation error")

        # Convert location to readable string (e.g., "history[0].role")
        # Skip 'body' as it's the request body marker
        path_parts = []
        for item in loc:
            if item == "body":
                continue
            if isinstance(item, int):
                # Array index
                path_parts[-1] = f"{path_parts[-1]}[{item}]"
            else:
                # Field name
                path_parts.append(str(item))

        field_path = ".".join(path_parts) if path_parts else "request"

        # Build detailed error message
        detailed_errors.append(f"{field_path}: {msg}")

    # Join multiple messages
    if not detailed_errors:
        error_message = "Validation Error"
    elif len(detailed_errors) == 1:
        error_message = detailed_errors[0]
    else:
        error_message = "; ".join(detailed_errors)

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "message": error_message,
                "details": errors,
            }
        },
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


if __name__ == '__main__':
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, workers=config.WORKERS)
