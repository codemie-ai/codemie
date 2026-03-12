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

"""LangFuse service initialization and dependency injection.

This module provides:
- Service availability checks
- Service initialization from config
- Dependency injection helpers for FastAPI
- Helper functions for accessing LangFuse services

All functions gracefully handle cases where LangFuse enterprise package is not available.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

from fastapi import Request

from codemie.enterprise.loader import HAS_LANGFUSE

if TYPE_CHECKING:
    from codemie_enterprise.langfuse import LangFuseService

# Global service registry (initialized at startup)
_global_langfuse_service: Optional["LangFuseService"] = None


def is_langfuse_enabled() -> bool:
    """
    Check if LangFuse is available and enabled.

    This is the centralized function that all code should use to check LangFuse availability.

    Priority order (CRITICAL):
    1. HAS_LANGFUSE (source of truth - is enterprise package available?)
    2. config.LANGFUSE_TRACES (user preference - do they want it enabled?)

    Returns:
        True if both conditions are met, False otherwise

    Usage:
        from codemie.enterprise.langfuse import is_langfuse_enabled

        if not is_langfuse_enabled():
            return None  # Skip LangFuse operations
    """
    from codemie.configs import config

    # FIRST: Check if enterprise package is available (SOURCE OF TRUTH)
    if not HAS_LANGFUSE:
        return False

    # SECOND: Check if global tracing is enabled in config (USER PREFERENCE)
    return config.LANGFUSE_TRACES


def initialize_langfuse_from_config() -> Optional["LangFuseService"]:
    """
    Initialize LangFuse service from environment configuration.

    This is a convenience helper for application startup that creates and initializes
    the LangFuse service based on configuration settings.

    Uses is_langfuse_enabled() to check availability and configuration.

    Returns:
        Initialized LangFuseService or None if not available/disabled

    Usage:
        # In main.py lifespan function:
        langfuse_service = initialize_langfuse_from_config()
        app.state.langfuse_service = langfuse_service
        set_global_langfuse_service(langfuse_service)
    """
    from codemie.configs import config, logger

    # Check if LangFuse is available and enabled
    if not is_langfuse_enabled():
        logger.info("LangFuse not available or disabled")
        return None

    try:
        from codemie.enterprise import LangFuseConfig, LangFuseService

        # Create config from core settings
        langfuse_config = LangFuseConfig(
            enabled=config.LANGFUSE_TRACES,
            blocked_scopes=config.LANGFUSE_BLOCKED_INSTRUMENTATION_SCOPES or [],
            environment=config.ENV,
            flush_at=1000,
            flush_interval=10.0,
            debug=config.ENV == "local",
            host=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
            public_key=os.environ.get("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.environ.get("LANGFUSE_SECRET_KEY"),
        )

        # Create and initialize service
        service = LangFuseService(langfuse_config)
        service.initialize()
        logger.info("✓ LangFuse enterprise service initialized")
        return service
    except Exception as e:
        logger.error(f"✗ Failed to initialize LangFuse: {e}")
        return None


def set_global_langfuse_service(service: Optional["LangFuseService"]) -> None:
    """
    Set the global LangFuse service instance.

    This is called during application startup to make the service available
    to code that doesn't have access to the FastAPI request context.

    Args:
        service: LangFuseService instance or None
    """
    global _global_langfuse_service
    _global_langfuse_service = service


def get_global_langfuse_service() -> Optional["LangFuseService"]:
    """
    Get the global LangFuse service instance.

    Returns None if enterprise feature not available or not initialized.

    Returns:
        LangFuseService instance if available, None otherwise

    Usage:
        from codemie.enterprise.langfuse import get_global_langfuse_service

        langfuse_service = get_global_langfuse_service()
        if langfuse_service:
            client = langfuse_service.get_client()
            # Use client...
    """
    return _global_langfuse_service


def get_langfuse_service(request: Request) -> Optional["LangFuseService"]:
    """
    Get LangFuse service from application state.

    Returns None if enterprise feature not available or not initialized.

    Args:
        request: FastAPI request object

    Returns:
        LangFuseService instance if available, None otherwise

    Usage:
        @router.get("/endpoint")
        async def endpoint(
            langfuse: Optional[LangFuseService] = Depends(get_langfuse_service)
        ):
            if langfuse:
                client = langfuse.get_client()
                # Use client...
    """
    if not HAS_LANGFUSE:
        return None
    return getattr(request.app.state, "langfuse_service", None)


def get_langfuse_callback_handler():
    """
    Get LangFuse CallbackHandler for LangChain/LangGraph tracing.

    This is the CENTRALIZED function that all code should use to get CallbackHandler.

    Returns None if:
    - Enterprise package not installed (HAS_LANGFUSE=False)
    - LangFuse tracing disabled in config (LANGFUSE_TRACES=False)
    - Service not initialized
    - CallbackHandler creation fails and None is returned

    Returns:
        CallbackHandler instance or None

    Usage:
        from codemie.enterprise.langfuse import get_langfuse_callback_handler

        callbacks = []
        handler = get_langfuse_callback_handler()
        if handler:
            callbacks.append(handler)
    """
    # Check if LangFuse is available and enabled
    if not is_langfuse_enabled():
        return None

    service = get_global_langfuse_service()
    if service is None:
        return None

    return service.get_callback_handler()


def require_langfuse_client(request: Request):
    """
    Get LangFuse client from request, raises exception if not available.

    This is a convenience helper that combines service lookup and client retrieval
    with proper error handling. Use this when LangFuse is required for the operation.

    Args:
        request: FastAPI request object

    Returns:
        LangFuse client instance

    Raises:
        ExtendedHTTPException: If LangFuse not available or not initialized

    Usage:
        # In route handlers or services with request context:
        langfuse = require_langfuse_client(raw_request)
        dataset = langfuse.get_dataset(dataset_id)
    """
    from codemie.core.exceptions import ExtendedHTTPException

    service = get_langfuse_service(request)
    if service is None:
        raise ExtendedHTTPException(
            code=503,
            message="LangFuse service not available",
            details="LangFuse enterprise features are not enabled or not installed. "
            "Install codemie-enterprise package and enable LANGFUSE_TRACES in configuration.",
        )

    client = service.get_client()
    if client is None:
        raise ExtendedHTTPException(
            code=503,
            message="LangFuse client not initialized",
            details="LangFuse service is available but client initialization failed. "
            "Check LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY configuration.",
        )

    return client


def get_langfuse_client_or_none():
    """
    Get LangFuse client from global service, returns None if not available.

    This is a convenience helper for graceful degradation in services that don't
    have request context (monitoring, background tasks, etc).

    Returns:
        LangFuse client instance or None

    Usage:
        # In services without request context:
        langfuse = get_langfuse_client_or_none()
        if langfuse is None:
            return  # Skip tracing, graceful degradation
        # Use langfuse client...
    """
    service = get_global_langfuse_service()
    if service is None:
        return None
    return service.get_client()
