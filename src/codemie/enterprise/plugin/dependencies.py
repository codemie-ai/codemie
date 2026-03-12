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

from typing import TYPE_CHECKING, List, Optional

from langchain_core.tools import BaseTool
from pydantic import Field

from codemie.configs import config, logger
from codemie.core.constants import TOOL_TYPE, ToolType
from codemie.enterprise.loader import HAS_PLUGIN
from codemie.rest_api.models.settings import SettingsBase
from codemie_tools.base.models import Tool, ToolKit, ToolMetadata, ToolSet

if TYPE_CHECKING:
    from codemie_enterprise.plugin import PluginService

# Constants
_PLUGIN_NOT_AVAILABLE_MSG = "Plugin not available"

# Global service registry (initialized at startup)
_global_plugin_service: Optional["PluginService"] = None


def is_plugin_enabled() -> bool:
    """
    Check if plugin system is available and enabled.

    This is the centralized function that all code should use to check plugin availability.

    Priority order (CRITICAL):
    1. HAS_PLUGIN (source of truth - is enterprise package available?)
    2. config.NATS_PLUGIN_KEY_CHECK_ENABLED (user preference - plugin system enabled?)
    Returns:
        True if all conditions are met, False otherwise

    Usage:
        from codemie.enterprise.plugin import is_plugin_enabled

        if not is_plugin_enabled():
            return []  # Skip plugin operations
    """

    # FIRST: Check if enterprise package is available (SOURCE OF TRUTH)
    if not HAS_PLUGIN:
        return False

    # SECOND: Check if plugin system is enabled in config (USER PREFERENCE)
    return config.NATS_PLUGIN_KEY_CHECK_ENABLED


# Create validation callback for plugin key authentication
async def validate_plugin_key(plugin_key: str) -> bool:
    """Validate plugin key against settings database."""
    try:
        from codemie.core.utils import hash_string
        from codemie.rest_api.models.settings import Settings
        from codemie.service.settings.base_settings import SearchFields
        from codemie.service.settings.settings import SettingsService
        from codemie_tools.base.models import CredentialTypes

        # Hash the plugin key
        plugin_hash = hash_string(plugin_key)

        # Search for matching credentials
        search_fields = {
            SearchFields.CREDENTIAL_TYPE: CredentialTypes.PLUGIN,
            SearchFields.SETTING_HASH: plugin_hash,
        }

        settings = Settings.get_all_by_term_fields(search_fields)

        # Decrypt and exact match
        for setting in settings:
            creds = SettingsService._decrypt_fields(setting.credential_values)
            for cred in creds:
                if cred.value == plugin_key:
                    logger.info(f"Plugin key validated for user {setting.user_id}")
                    return True

        logger.debug("Plugin key validation failed - credentials not found")
        return False

    except Exception as e:
        logger.error(f"Error validating plugin key: {e}", exc_info=True)
        return False


def initialize_plugin_from_config() -> Optional["PluginService"]:
    """
    Initialize plugin service from environment configuration.

    This is a convenience helper for application startup that creates and initializes
    the plugin service based on configuration settings.

    Uses is_plugin_enabled() to check availability and configuration.

    Returns:
        Initialized PluginService or None if not available/disabled
    """
    from codemie.configs import logger

    # Check if plugin system is available and enabled
    if not is_plugin_enabled():
        logger.info("Plugin not available or disabled")
        return None

    try:
        from codemie.clients.natsio import Client as NatsClient
        from codemie.enterprise import PluginConfig, PluginService
        from codemie.service.monitoring.plugin_monitoring_service import PluginMonitoringService

        # Create config from core settings
        plugin_config = PluginConfig(
            enabled=config.NATS_PLUGIN_KEY_CHECK_ENABLED,
            v2_enabled=config.NATS_PLUGIN_V2_ENABLED,
            auth_enabled=config.NATS_PLUGIN_KEY_CHECK_ENABLED,
            nats_servers_uri=config.NATS_SERVERS_URI,
            nats_client_connect_uri=config.NATS_CLIENT_CONNECT_URI or None,
            nats_user=config.NATS_USER,
            nats_password=config.NATS_PASSWORD,
            list_timeout_seconds=config.NATS_PLUGIN_LIST_TIMEOUT_SECONDS,
            tool_timeout=config.NATS_PLUGIN_TOOL_TIMEOUT,
            update_interval=config.NATS_PLUGIN_UPDATE_INTERVAL,
            max_validation_attempts=config.NATS_PLUGIN_MAX_VALIDATION_ATTEMPTS,
        )

        # Create NATS connection factory using core's natsio.Client with callbacks
        nats_client = NatsClient()

        async def nats_connection_factory():
            """Factory that creates NATS connections with callbacks configured."""
            return await nats_client.connect()

        # Create monitoring service instance
        monitoring_service = PluginMonitoringService()

        # Create service with all dependencies injected via constructor
        service = PluginService(
            config=plugin_config,
            validation_callback=validate_plugin_key,
            nats_connection_factory=nats_connection_factory,
            monitoring_service=monitoring_service,
        )

        logger.info("✓ Plugin enterprise service created with NATS callback factory, monitoring, and auth validation")
        return service

    except Exception as e:
        logger.error(f"✗ Failed to create plugin service: {e}")
        return None


def set_global_plugin_service(service: Optional["PluginService"]) -> None:
    """
    Set the global plugin service instance.

    This is called during application startup to make the service available
    to code that doesn't have access to the FastAPI request context.

    Args:
        service: PluginService instance or None
    """
    global _global_plugin_service
    _global_plugin_service = service


def get_global_plugin_service() -> Optional["PluginService"]:
    """
    Get the global plugin service instance.

    Returns None if enterprise feature not available or not initialized.

    Returns:
        PluginService instance if available, None otherwise

    Usage:
        from codemie.enterprise.plugin import get_global_plugin_service

        plugin_service = get_global_plugin_service()
        if plugin_service:
            tools = plugin_service.get_plugin_tools(plugin_key, session_id)
            # Use tools...
    """
    return _global_plugin_service


def get_plugin_service_or_none() -> Optional["PluginService"]:
    """
    Get plugin service, returns None if not available.

    Use this function everywhere: services, routers, background tasks, and FastAPI dependencies.

    Returns None if:
    - Enterprise package not installed (HAS_PLUGIN=False)
    - Plugin disabled in config (NATS_PLUGIN_KEY_CHECK_ENABLED=False)
    - V2 protocol disabled (NATS_PLUGIN_V2_ENABLED=False)
    - Service not initialized

    Returns:
        PluginService instance or None

    Usage in services/background tasks:
        from codemie.enterprise.plugin import get_plugin_service_or_none

        plugin_service = get_plugin_service_or_none()
        if plugin_service is None:
            return []  # Graceful degradation

        tools = plugin_service.get_plugin_tools(plugin_key, session_id)

    Usage with FastAPI Depends():
        from fastapi import Depends
        from codemie.enterprise.plugin import get_plugin_service_or_none

        @router.get("/plugin/tools")
        async def get_tools(
            plugin_service: Optional[PluginService] = Depends(get_plugin_service_or_none)
        ):
            if not plugin_service:
                raise HTTPException(503, "Plugin system not available")
            return plugin_service.get_plugin_tools(plugin_key)
    """
    if not is_plugin_enabled():
        return None
    return get_global_plugin_service()


PLUGIN_TOOL = ToolMetadata(
    name="Plugin",
    user_description="""
    Enables the AI assistant to connect to and utilize local CodeMie Plugin tools installed on your machine.
    Before using it, it is necessary to have codemie-plugins tool running on your local
    machine and to add a new integration for the tool by providing:
    1. Alias (A friendly name for the plugin connection)
    2. Plugin Key (Generated by your installation of local CodeMie Plugin)
    Usage Note:
    Use this tool when you need to access functionality provided by locally installed CodeMie Plugins.
    It allows for custom extensions and integrations tailored to your specific environment or requirements.
    The available operations depend on the installed plugins, so familiarity with the local plugin setup is crucial.
    Often used to integrate with local development environments.
    """.strip(),
)


class PluginToolkitUI(ToolKit):
    """Concrete ToolGate toolkit UI implementation."""

    toolkit: ToolSet = ToolSet.PLUGIN
    settings_config: bool = True
    settings: Optional[SettingsBase] = None
    tools: List[Tool] = Field(default=[Tool.from_metadata(PLUGIN_TOOL)])


def get_plugin_toolkit_ui_info():
    """
    Get plugin toolkit UI information with full metadata preservation.

    This function follows the enterprise dependency pattern similar to
    wrap_enterprise_tool - it checks availability, creates an adapter with
    hardcoded UI metadata, and wraps it for the core package.

    Pattern:
    1. Checks if plugin system is available (HAS_PLUGIN - SOURCE OF TRUTH)
    2. Creates adapter with hardcoded UI metadata in integration layer
    3. Wraps adapter using wrap_enterprise_toolkit() to create ToolKit instance
    4. If not available, returns None (graceful degradation)

    The wrapper ensures that enterprise-specific fields (name, description,
    user_description) are preserved in the returned ToolKit instance.

    Returns:
        ToolKit instance with full metadata if available, None otherwise
    """

    # Check if enterprise package is available (SOURCE OF TRUTH)
    if not HAS_PLUGIN:
        logger.debug(f"{_PLUGIN_NOT_AVAILABLE_MSG}, plugin toolkit UI not available")
        return None

    return PluginToolkitUI()


def get_plugin_tools_for_assistant(
    user_id: str,
    project_name: str,
    assistant_id: Optional[str] = None,
    session_id: str | None = None,
    tool_config: dict | None = None,
) -> list["BaseTool"]:
    """
    Get plugin tools for an assistant, wrapped as LangChain BaseTool instances.

    This is a convenience helper that:
    1. Checks if plugin system is enabled
    2. Gets the plugin service
    3. Retrieves plugin credentials for the user
    4. Gets tool definitions from the plugin service
    5. Wraps each ToolConsumer as a LangChain BaseTool

    Args:
        user_id: User ID
        project_name: Project name (used as plugin key prefix)
        assistant_id: Assistant ID
        session_id: Optional session ID filter
        tool_config: Optional tool configuration

    Returns:
        List of LangChain BaseTool instances, or empty list if not available

    Usage:
        from codemie.enterprise.plugin import get_plugin_tools_for_assistant

        tools = get_plugin_tools_for_assistant(
            user_id=user.id,
            project_name=assistant.project,
            assistant_id=assistant.id,
            tool_config=tool_config,
        )
        if tools:
            # Use tools with LangChain agent
            pass
    """

    plugin_service = get_plugin_service_or_none()
    if plugin_service is None:
        logger.debug(f"{_PLUGIN_NOT_AVAILABLE_MSG}, skipping plugin tools")
        return []

    try:
        # Import here to avoid circular dependency and for graceful degradation
        from codemie.service.settings.settings import SettingsService

        # Get plugin credentials for user
        credentials = SettingsService.get_plugin_creds(
            user_id=user_id,
            project_name=project_name,
            assistant_id=assistant_id,
            tool_config=tool_config,
        )
        if not credentials or not credentials.plugin_key:
            logger.debug(f"No plugin credentials found for user {user_id}")
            return []

        plugin_key = credentials.plugin_key

        # Import MCP converter from core and create adapter for model mapping
        from codemie_enterprise.plugin.mcp_models import MCPToolInvocationResponse

        from codemie.service.mcp.models import (
            MCPToolInvocationResponse as CoreMCPToolInvocationResponse,
        )
        from codemie.service.mcp.toolkit import _convert_mcp_response_to_tool_message

        def mcp_converter_adapter(enterprise_mcp_response: MCPToolInvocationResponse):
            """Adapter that converts enterprise MCP models to core MCP models.

            This adapter bridges the gap between enterprise and core packages by:
            1. Taking enterprise's MCPToolInvocationResponse (from codemie_enterprise.plugin.mcp_models)
            2. Converting it to core's MCPToolInvocationResponse (from codemie.service.mcp.models)
            3. Calling the core converter function
            4. Returning the final string result

            This maintains zero-coupling while enabling proper MCP conversion.

            Args:
                enterprise_mcp_response: MCPToolInvocationResponse from enterprise package

            Returns:
                Converted string from core's _convert_mcp_response_to_tool_message
            """
            enterprise_data = enterprise_mcp_response.model_dump()
            core_mcp_response = CoreMCPToolInvocationResponse(**enterprise_data)

            # Call core converter function with core model (matches original pattern)
            return _convert_mcp_response_to_tool_message(core_mcp_response)

        # Create PluginToolkit to get ToolConsumer instances with MCP converter adapter
        from codemie_enterprise.plugin import PluginToolkit

        toolkit = PluginToolkit(
            plugin_service=plugin_service,
            plugin_key=plugin_key,
            mcp_converter_callback=mcp_converter_adapter,  # Use adapter instead of direct function
        )

        tools = toolkit.get_tools(session_id=session_id)
        if not tools:
            logger.debug(f"No plugin tools found for {plugin_key}")
            return []

        # Wrap each ToolConsumer as CodeMieTool using protocol-based wrapper
        from codemie.enterprise.enterprise_tool import wrap_enterprise_tool

        langchain_tools = []
        for tool_consumer in tools:
            try:
                wrapped_tool = wrap_enterprise_tool(tool_consumer)
                wrapped_tool.metadata = wrapped_tool.metadata or {}
                wrapped_tool.metadata[TOOL_TYPE] = ToolType.PLUGIN

                langchain_tools.append(wrapped_tool)
            except Exception as e:
                logger.error(f"Failed to wrap tool {tool_consumer.name}: {e}", exc_info=True)

        logger.info(f"Retrieved {len(langchain_tools)} plugin tools for {plugin_key}")
        return langchain_tools

    except Exception as e:
        logger.error(f"Error getting plugin tools for assistant {assistant_id}: {e}", exc_info=True)
        return []
