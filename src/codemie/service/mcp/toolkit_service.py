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

"""
MCP Toolkit Service.

This module provides a service for managing MCP (Model Control Panel) toolkits and integrating
them with the broader CodeMie tool system. It handles the creation, caching, and management
of toolkits and tools that can be used with LangChain-based applications.

The service implements a singleton pattern with TTL caching for efficient resource management
and provides both synchronous and asynchronous interfaces for toolkit operations.

Key Components:
    - MCPToolkitService: Main service class for managing toolkits
    - TTLCache: Time-based caching for toolkit instances
    - ThreadPoolExecutor: Handles async operations in synchronous contexts
"""

from __future__ import annotations

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from typing import ClassVar, Any, Tuple

from cachetools import TTLCache

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.models import ToolConfig
from codemie.rest_api.models.assistant import MCPServerDetails
from codemie.rest_api.models.settings import SettingsBase
from codemie.rest_api.security.user_context import get_current_user
from codemie.service.mcp.client import MCPConnectClient, BUCKET_KEY
from codemie.service.security.token_exchange_service import token_exchange_service
from codemie.service.security.token_providers.base_provider import BrokerAuthRequiredException
from codemie.service.mcp.models import MCPServerConfig, MCPToolLoadException, MCPExecutionContext
from codemie.service.mcp.toolkit import MCPToolkit, MCPToolkitFactory, MCPTool, ContextAwareMCPTool
from codemie.service.settings.base_settings import SearchFields

# MCP configuration constants
MCP_TOOL_CONFIG_PREFIX = "MCP:"

# Cache logging messages
CACHE_HIT_MSG = "Cache hit for MCP toolkit service instance with base URL: {}"
CACHE_MISS_MSG = "Cache miss for MCP toolkit service instance with base URL: {}"

# Toolkit cache messages
TOOLKIT_CACHE_HIT_MSG = "Cache hit for MCP toolkit with server config: {}"
TOOLKIT_CACHE_MISS_MSG = "Cache miss for MCP toolkit with server config: {}"


class MCPToolkitService:
    """
    Service for managing and providing access to MCP toolkits.

    Acts as the main entry point for getting MCP tools and toolkits
    for use in the CodeMie system.

    This class is implemented with a TTLCache to manage instances by the base_url
    of the MCPConnectClient, ensuring efficient resource use across different
    MCP server connections. The cache TTL and size are configured through
    the application config.
    """

    # Class variable to store the singleton instance for default MCP connection
    _instance: ClassVar[MCPToolkitService | None] = None

    # TTLCache to store instances by base_url, configured through application settings
    _instances_cache: ClassVar[TTLCache] = TTLCache(
        maxsize=config.MCP_TOOLKIT_SERVICE_CACHE_SIZE, ttl=config.MCP_TOOLKIT_SERVICE_CACHE_TTL
    )

    @classmethod
    def get_mcp_server_tools(
        cls,
        mcp_servers: list[MCPServerDetails],
        user_id: str | None = None,
        project_name: str | None = None,
        conversation_id: str | None = None,
        tools_config: list[ToolConfig] | None = None,
        mcp_server_args_preprocessor: callable | None = None,
        mcp_server_single_usage: bool | None = False,
        # New parameters for execution context
        assistant_id: str | None = None,
        workflow_execution_id: str | None = None,
        request_headers: dict[str, str] | None = None,
    ) -> list[MCPTool]:
        """
        Get MCP tools from a list of MCP servers with execution context support.

        This method processes each enabled MCP server to extract tools, handling
        configuration, authentication, and error recovery gracefully.

        Args:
            mcp_servers: List of MCP server configurations
            user_id: Optional user ID for credential resolution
            project_name: Optional project name for credential resolution
            conversation_id: Optional conversation ID for tool context
            tools_config: Optional tool configurations to apply
            mcp_server_args_preprocessor: Optional preprocessor function for server arguments
            mcp_server_single_usage: Whether MCP servers should be single-use (True) or persistent (False)
            assistant_id: Optional assistant ID for execution context
            workflow_execution_id: Optional workflow execution ID for context
            request_headers: Optional custom HTTP headers to propagate to MCP servers

        Returns:
            List of MCP tools from all successfully processed servers
        """
        tools = []
        if not mcp_servers:
            return tools

        # Create execution context
        execution_context = MCPExecutionContext(
            user_id=user_id,
            assistant_id=assistant_id,
            project_name=project_name,
            workflow_execution_id=workflow_execution_id,
            request_headers=request_headers,
        )

        default_mcp_toolkit_service = cls.get_instance()

        for mcp_server in mcp_servers:
            if not mcp_server.enabled:
                logger.debug(f"Skipping disabled MCP server: {mcp_server.name}")
                continue

            server_tools = cls._process_single_mcp_server(
                mcp_server=mcp_server,
                default_toolkit_service=default_mcp_toolkit_service,
                user_id=user_id,
                project_name=project_name,
                conversation_id=conversation_id,
                tools_config=tools_config,
                mcp_server_args_preprocessor=mcp_server_args_preprocessor,
                mcp_server_single_usage=mcp_server_single_usage,
                execution_context=execution_context,  # Pass context to tool processing
            )
            tools.extend(server_tools)

        return tools

    @classmethod
    def _process_single_mcp_server(
        cls,
        mcp_server: MCPServerDetails,
        default_toolkit_service: MCPToolkitService,
        user_id: str | None = None,
        project_name: str | None = None,
        conversation_id: str | None = None,
        tools_config: list[ToolConfig] | None = None,
        mcp_server_args_preprocessor: callable | None = None,
        mcp_server_single_usage: bool | None = False,
        execution_context: MCPExecutionContext | None = None,
    ) -> list[MCPTool]:
        """
        Process a single MCP server to extract its tools.

        Handles all the complex logic for a single server including toolkit service
        selection, configuration building, and error handling.

        Args:
            mcp_server: The MCP server to process
            default_toolkit_service: Default toolkit service instance
            user_id: Optional user ID for credential resolution
            project_name: Optional project name for credential resolution
            conversation_id: Optional conversation ID for tool context
            tools_config: Optional tool configurations to apply
            mcp_server_single_usage: Whether MCP servers should be single-use (True) or persistent (False)

        Returns:
            List of tools from the server, empty list if processing fails
        """
        try:
            toolkit_service = cls._get_toolkit_service_for_server(mcp_server, default_toolkit_service)
            server_config = cls._prepare_server_config(
                mcp_server=mcp_server,
                user_id=user_id,
                project_name=project_name,
                conversation_id=conversation_id,
                tools_config=tools_config,
                mcp_server_args_preprocessor=mcp_server_args_preprocessor,
                mcp_server_single_usage=mcp_server_single_usage,
            )

            toolkit = toolkit_service.get_toolkit(
                server_config=server_config,
                toolkit_name=mcp_server.name,
                toolkit_description=mcp_server.description or f"Tools provided by MCP server: {mcp_server.name}",
                tools_tokens_size_limit=mcp_server.tools_tokens_size_limit,
                execution_context=execution_context,
            )

            tools = toolkit.get_tools()
            tools = cls._filter_tools_by_config(tools, mcp_server)

            # Create context-aware tools if execution context is provided
            if execution_context:
                tools = cls._create_context_aware_tools(tools, execution_context)

            return tools

        except Exception as e:
            err_msg = f"Failed to load MCP tools from {mcp_server.name}: {type(e).__name__}: {e}"
            logger.error(err_msg)
            raise MCPToolLoadException(mcp_server.name, e) from e

    @classmethod
    def _create_context_aware_tools(cls, tools: list[MCPTool], execution_context: MCPExecutionContext) -> list[MCPTool]:
        """
        Create context-aware wrappers for MCP tools.

        This method creates new tool instances that automatically inject
        the execution context when called, without modifying the cached tools.

        All tool attributes and configuration from the original tool are preserved
        """
        context_aware_tools = []

        for tool in tools:
            # Create a wrapper that injects context at execution time

            context_aware_tool = ContextAwareMCPTool(tool, execution_context)
            context_aware_tools.append(context_aware_tool)

        return context_aware_tools

    @classmethod
    def _filter_tools_by_config(cls, tools: list[MCPTool], mcp_server: MCPServerDetails) -> list[MCPTool]:
        """
        Filter tools by name if specified in mcp_server.tools or mcp_server.config.tools.
        Returns all tools if no filtering is specified (None or empty list).
        """
        allowed_tool_names = mcp_server.tools or (mcp_server.config.tools if mcp_server.config else None)

        if not allowed_tool_names:
            return tools

        filtered_tools = [tool for tool in tools if tool.name in allowed_tool_names]
        found_tool_names = {tool.name for tool in filtered_tools}
        missing_tools = set(allowed_tool_names) - found_tool_names

        if missing_tools:
            logger.warning(
                f"MCP server '{mcp_server.name}': Specified tools not found: {sorted(missing_tools)}. "
                f"Available tools: {sorted(tool.name for tool in tools)}"
            )

        logger.debug(
            f"MCP server '{mcp_server.name}': Filtered {len(filtered_tools)} tools from {len(tools)} total. "
            f"Allowed tools: {sorted(allowed_tool_names)}"
        )

        return filtered_tools

    @classmethod
    def _get_toolkit_service_for_server(
        cls,
        mcp_server: MCPServerDetails,
        default_toolkit_service: MCPToolkitService,
    ) -> MCPToolkitService:
        """
        Determine which toolkit service to use for the given MCP server.

        Args:
            mcp_server: The MCP server configuration
            default_toolkit_service: Default toolkit service instance

        Returns:
            Appropriate toolkit service (custom or default)
        """
        if mcp_server.mcp_connect_url:
            custom_client = MCPConnectClient(mcp_server.mcp_connect_url)
            return cls.get_instance(custom_client)
        return default_toolkit_service

    @classmethod
    def _prepare_server_config(
        cls,
        mcp_server: MCPServerDetails,
        user_id: str | None = None,
        project_name: str | None = None,
        conversation_id: str | None = None,
        tools_config: list[ToolConfig] | None = None,
        mcp_server_args_preprocessor: callable | None = None,
        mcp_server_single_usage: bool | None = False,
    ) -> MCPServerConfig:
        """
        Prepare the complete server configuration for an MCP server.

        Builds the base configuration and applies conversation ID, tool configurations,
        and processes server arguments and placeholders.

        Args:
            mcp_server: The MCP server details
            user_id: Optional user ID for credential resolution
            project_name: Optional project name for credential resolution
            conversation_id: Optional conversation ID for tool context
            tools_config: Optional tool configurations to apply
            mcp_server_args_preprocessor: Optional preprocessor function for server arguments
            mcp_server_single_usage: Whether MCP servers should be single-use (True) or persistent (False)

        Returns:
            Complete MCP server configuration
        """
        server_config = cls._build_mcp_server_config(mcp_server, user_id, project_name)

        # Set single_usage
        server_config.single_usage = mcp_server.config and mcp_server.config.single_usage or mcp_server_single_usage

        # For persistent servers (not single-use), use conversation_id for caching
        if not mcp_server_single_usage and conversation_id:
            server_config.env[BUCKET_KEY] = conversation_id

        cls._apply_server_tools_config(server_config, mcp_server, tools_config, user_id)
        cls._process_server_args(server_config, mcp_server_args_preprocessor)
        cls._process_server_url_and_command(server_config, mcp_server_args_preprocessor)

        return server_config

    @classmethod
    def _apply_server_tools_config(
        cls,
        server_config: MCPServerConfig,
        mcp_server: MCPServerDetails,
        tools_config: list[ToolConfig] | None,
        user_id: str | None,
    ) -> None:
        """
        Apply tool configuration to the server config if available.

        Args:
            server_config: The server configuration to modify
            mcp_server: The MCP server details
            tools_config: Optional tool configurations to apply
            user_id: Optional user ID for credential resolution
        """
        if not tools_config:
            return

        mcp_tool_config_name = f"{MCP_TOOL_CONFIG_PREFIX}{mcp_server.name}"
        logger.debug(
            f"Searching for MCP tool config: '{mcp_tool_config_name}' in {len(tools_config)} configs: "
            f"{[tc.name for tc in tools_config]}"
        )
        mcp_tool_config = cls._find_tool_config_by_name(tools_config, mcp_tool_config_name)

        if mcp_tool_config:
            logger.debug(f"Found tool config for MCP server: {mcp_server.name}")
            cls._apply_tool_config_to_mcp_server(server_config, mcp_tool_config, user_id)

    @classmethod
    def get_instance(cls, mcp_client: MCPConnectClient | None = None) -> MCPToolkitService:
        """
        Get an instance of MCPToolkitService for the specified MCP client.

        If no client is provided, returns the singleton instance created by init_singleton.
        If a client is provided, returns either a cached instance for that client's base_url
        or creates a new one and stores it in the cache.

        Args:
            mcp_client: Optional MCPConnectClient instance. If not provided, the singleton
                      instance is used.

        Returns:
            MCPToolkitService: An instance for the specified client

        Raises:
            RuntimeError: If no client is provided and the singleton instance has not been initialized
            TypeError: If mcp_client is provided but is not an instance of MCPConnectClient
        """
        # If no client provided, use the singleton instance
        if mcp_client is None:
            if cls._instance is None:
                raise RuntimeError("MCPToolkitService singleton not initialized. Call init_singleton first.")
            return cls._instance

        # If a client is provided, use the cache based on base_url
        base_url = mcp_client.base_url
        if base_url not in cls._instances_cache:
            cls._log_cache_status(base_url, hit=False)
            cls._instances_cache[base_url] = cls(mcp_client)
        else:
            cls._log_cache_status(base_url, hit=True)

        return cls._instances_cache[base_url]

    @classmethod
    def init_singleton(cls, mcp_client: MCPConnectClient) -> None:
        """
        Initialize the singleton instance of MCPToolkitService.
        This method must be called before using get_instance() without parameters.

        Args:
            mcp_client (MCPConnectClient): MCP client for the singleton instance

        Raises:
            TypeError: If mcp_client is not an instance of MCPConnectClient
        """
        cls._instance = cls(mcp_client)
        cls._instances_cache[mcp_client.base_url] = cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """
        Reset the singleton instance and clear the instances cache.

        Useful primarily for testing or when a fresh instance is needed.
        This method clears all cached toolkits and service instances.
        """
        if cls._instance is not None:
            cls._instance.clear_cache()
        cls._instance = None
        cls._instances_cache.clear()

    def __init__(self, mcp_client: MCPConnectClient):
        """
        Initialize the MCP toolkit service.

        This constructor should normally not be called directly.
        Use get_instance() instead to access instances.

        Args:
            mcp_client (MCPConnectClient): MCP client for this service instance that
                                        provides connectivity to the MCP server

        Raises:
            TypeError: If mcp_client is not an instance of MCPConnectClient
        """
        self.mcp_client = mcp_client
        self.toolkit_factory = MCPToolkitFactory(
            self.mcp_client,
            cache_size=config.MCP_TOOLKIT_FACTORY_CACHE_SIZE,
            cache_expiry_seconds=config.MCP_TOOLKIT_FACTORY_CACHE_TTL,
        )

    async def get_toolkit_async(
        self,
        server_config: MCPServerConfig,
        toolkit_name: str | None = None,
        toolkit_description: str | None = None,
        tools_tokens_size_limit: int | None = None,
        use_cache: bool = True,
        execution_context: MCPExecutionContext | None = None,
    ) -> MCPToolkit:
        """
        Asynchronously get an MCP toolkit for a server configuration.

        Args:
            server_config (MCPServerConfig): Configuration for the MCP server including
                                         server details and authentication
            toolkit_name (str, optional): Custom name for the toolkit. Defaults to None.
            toolkit_description (str, optional): Custom description for the toolkit. Defaults to None.
            tools_tokens_size_limit (int, optional): Token size limit for tools to manage
                                                 context window size. Defaults to None.
            use_cache (bool): Whether to use cached toolkits. Defaults to True.
            execution_context (Optional[MCPExecutionContext]): Optional execution context with
                                                              user, assistant, and workflow info

        Returns:
            MCPToolkit: An MCP toolkit containing tools from the specified server

        Raises:
            ValueError: If server_config is invalid
            ConnectionError: If unable to connect to the MCP server
            Exception: If unable to create or retrieve the toolkit due to other errors
        """
        # Check cache if requested
        if use_cache:
            cached_toolkit = self.toolkit_factory.get_toolkit(server_config)
            if cached_toolkit:
                logger.info(TOOLKIT_CACHE_HIT_MSG.format(server_config))
                return cached_toolkit
            else:
                logger.info(TOOLKIT_CACHE_MISS_MSG.format(server_config))

        # Create a new toolkit
        try:
            toolkit = await self.toolkit_factory.create_toolkit(
                server_config=server_config,
                toolkit_name=toolkit_name,
                toolkit_description=toolkit_description,
                tools_tokens_size_limit=tools_tokens_size_limit,
                use_cache=use_cache,
                execution_context=execution_context,
            )

            return toolkit
        except Exception as e:
            logger.error(f"Failed to get MCP toolkit: {str(e)}")
            raise

    def get_toolkit(
        self,
        server_config: MCPServerConfig,
        toolkit_name: str | None = None,
        toolkit_description: str | None = None,
        tools_tokens_size_limit: int | None = None,
        use_cache: bool = True,
        execution_context: MCPExecutionContext | None = None,
    ) -> MCPToolkit:
        """
        Synchronously get an MCP toolkit for a server configuration.

        This is a synchronous wrapper around get_toolkit_async that handles
        asyncio event loop management for both async and sync contexts.
        It detects if it's running in an existing asyncio loop and adapts its
        execution strategy accordingly to prevent deadlocks.

        Args:
            server_config (MCPServerConfig): Configuration for the MCP server including
                                         server details and authentication
            toolkit_name (str, optional): Custom name for the toolkit. Defaults to None.
            toolkit_description (str, optional): Custom description for the toolkit. Defaults to None.
            tools_tokens_size_limit (int, optional): Token size limit for tools to manage
                                                 context window size. Defaults to None.
            use_cache (bool): Whether to use cached toolkits. Defaults to True.
            execution_context (Optional[MCPExecutionContext]): Optional execution context with
                                                              user, assistant, and workflow info

        Returns:
            MCPToolkit: An MCP toolkit containing tools from the specified server

        Raises:
            ValueError: If server_config is invalid
            ConnectionError: If unable to connect to the MCP server
            RuntimeError: If there are issues with the asyncio event loop
            Exception: If unable to create or retrieve the toolkit due to other errors
        """
        # Override use_cache based on single_usage
        should_use_cache = use_cache and not server_config.single_usage

        try:
            # If this call is made from within an already running loop,
            # get_running_loop() succeeds.
            asyncio.get_running_loop()
            in_running_loop = True
        except RuntimeError:
            in_running_loop = False

        if in_running_loop:
            # Offload the coroutine to a separate thread to avoid deadlock.
            # asyncio.run() is safe to call from a thread with no running loop.
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    lambda: asyncio.run(
                        self.get_toolkit_async(
                            server_config=server_config,
                            toolkit_name=toolkit_name,
                            toolkit_description=toolkit_description,
                            tools_tokens_size_limit=tools_tokens_size_limit,
                            use_cache=should_use_cache,
                            execution_context=execution_context,
                        )
                    )
                )
                return future.result()
        else:
            # No loop is running, so run the coroutine directly.
            return asyncio.run(
                self.get_toolkit_async(
                    server_config=server_config,
                    toolkit_name=toolkit_name,
                    toolkit_description=toolkit_description,
                    tools_tokens_size_limit=tools_tokens_size_limit,
                    use_cache=should_use_cache,
                    execution_context=execution_context,
                )
            )

    def get_tools(
        self,
        server_config: MCPServerConfig,
        use_cache: bool = True,
    ) -> list[MCPTool]:
        """
        Get tools for an MCP server.

        A convenience method that retrieves a toolkit and returns its tools.

        Args:
            server_config (MCPServerConfig): Configuration for the MCP server including
                                         server details and authentication
            use_cache (bool): Whether to use cached tools. Defaults to True.

        Returns:
            list[BaseTool]: List of LangChain-compatible tools from the MCP server

        Raises:
            ValueError: If server_config is invalid
            ConnectionError: If unable to connect to the MCP server
            Exception: If unable to retrieve tools or the toolkit due to other errors
        """
        toolkit = self.get_toolkit(server_config, use_cache=use_cache)
        return toolkit.get_tools()

    def clear_cache(self) -> None:
        """
        Clear the toolkit cache.

        Removes all cached toolkits from the toolkit factory, forcing new toolkits
        to be created on subsequent requests.
        """
        self.toolkit_factory.clear_cache()

    @classmethod
    def _log_cache_status(cls, base_url: str, hit: bool) -> None:
        """
        Log cache hit or miss status for consistent cache logging.

        Args:
            base_url: The base URL of the MCP client used as cache key
            hit: True if cache hit, False if cache miss
        """
        message = CACHE_HIT_MSG if hit else CACHE_MISS_MSG
        logger.info(message.format(base_url))

    @classmethod
    def _extract_credentials_from_settings(cls, settings: SettingsBase | None) -> dict[str, Any]:
        """
        Extract credentials from a settings object.

        Args:
            settings: Settings object containing credential values

        Returns:
            Dictionary mapping credential keys to their values, empty if no credentials found
        """
        if not settings or not settings.credential_values:
            return {}
        return {item.key: item.value for item in settings.credential_values}

    @classmethod
    def _handle_credential_resolution_error(cls, error: Exception, context: str) -> dict[str, Any]:
        """
        Handle errors that occur during credential resolution with centralized logging.

        Args:
            error: The exception that occurred during credential resolution
            context: Contextual information about where the error occurred

        Returns:
            Empty dictionary as fallback when credential resolution fails
        """
        logger.error(f"Failed to resolve credentials for {context}: {str(error)}")
        return {}

    @classmethod
    def _resolve_credentials_by_alias(
        cls,
        integration_alias: str,
        user_id: str | None,
        project_name: str | None,
    ) -> dict[str, Any]:
        """
        Resolve credentials using hierarchical alias-based lookup.

        Args:
            integration_alias: Integration alias for lookup
            user_id: Optional user ID for scoped resolution
            project_name: Optional project name for scoped resolution

        Returns:
            Dictionary of environment variables from credentials
        """
        logger.debug(f"Resolving credentials using integration_alias: {integration_alias}")

        # Priority 1: User-scoped settings
        if user_id and project_name:
            credentials = cls._try_resolve_setting_with_scope(
                integration_alias=integration_alias,
                user_id=user_id,
                project_name=project_name,
            )
            if credentials:
                logger.debug(f"Found user-scoped credentials for alias: {integration_alias}")
                return credentials

        # Priority 2: Project-scoped settings (fallback)
        if project_name:
            credentials = cls._try_resolve_setting_with_scope(
                integration_alias=integration_alias,
                project_name=project_name,
            )
            if credentials:
                logger.debug(f"Found project-scoped credentials for alias: {integration_alias}")
                return credentials

        logger.warning(f"No credentials found for integration_alias: {integration_alias}")
        return {}

    @classmethod
    def _resolve_credentials_by_id(
        cls,
        integration_id: str,
        user_id: str | None,
    ) -> dict[str, Any]:
        """
        Resolve credentials using direct ID lookup.

        Args:
            integration_id: Direct integration ID for lookup
            user_id: Optional user ID for scoped resolution

        Returns:
            Dictionary of environment variables from credentials
        """
        from codemie.service.settings.settings import SettingsService

        logger.debug(f"Resolving credentials using integration_id: {integration_id}")
        search_fields = {SearchFields.USER_ID: user_id} if user_id else {}
        settings = SettingsService.retrieve_setting(search_fields=search_fields, setting_id=integration_id)

        credentials = cls._extract_credentials_from_settings(settings)
        if not credentials:
            logger.warning(f"No credential values found for integration_id: {integration_id}")
        return credentials

    @classmethod
    def _try_resolve_setting_with_scope(
        cls,
        integration_alias: str,
        user_id: str | None = None,
        project_name: str | None = None,
    ) -> dict[str, Any]:
        """
        Try to resolve settings with given scope parameters and extract credentials.

        Args:
            integration_alias: Integration alias for lookup
            user_id: Optional user ID for scoped resolution
            project_name: Optional project name for scoped resolution

        Returns:
            Dictionary of credentials if found, empty dict otherwise
        """
        from codemie.service.settings.settings import SettingsService

        search_fields = {SearchFields.ALIAS: integration_alias}

        if user_id:
            search_fields[SearchFields.USER_ID] = user_id
        if project_name:
            search_fields[SearchFields.PROJECT_NAME] = project_name

        setting = SettingsService.retrieve_setting(search_fields)
        return cls._extract_credentials_from_settings(setting) if setting else {}

    @classmethod
    def _resolve_credentials_with_priority(
        cls, mcp_server: MCPServerDetails, user_id: str | None, project_name: str | None
    ) -> dict[str, Any]:
        """
        Resolve credentials with priority logic for MCP server configuration.

        Args:
            mcp_server: MCP server details containing integration information
            user_id: Optional user ID for scoped resolution
            project_name: Optional project name for scoped resolution

        Returns:
            Dictionary of resolved environment variables
        """
        # Priority 1: Use integration_alias if provided
        if mcp_server.integration_alias:
            resolved_env_vars = cls._resolve_credentials_by_alias(
                integration_alias=mcp_server.integration_alias, user_id=user_id, project_name=project_name
            )
            if resolved_env_vars:
                logger.debug(f"Applied {len(resolved_env_vars)} environment variables from integration_alias")
            return resolved_env_vars

        # Priority 2: Fallback to settings if integration_alias not provided
        elif mcp_server.settings:
            logger.debug("Using direct settings for credential resolution")
            resolved_env_vars = cls._resolve_credentials_by_id(
                integration_id=mcp_server.settings.id, user_id=mcp_server.settings.user_id
            )
            if resolved_env_vars:
                logger.debug(f"Applied {len(resolved_env_vars)} environment variables from settings")
            return resolved_env_vars

        return {}

    @classmethod
    def _build_mcp_server_config(
        cls, mcp_server: MCPServerDetails, user_id: str | None = None, project_name: str | None = None
    ) -> MCPServerConfig:
        """
        Build the actual MCP server configuration from server details.

        Prioritizes integration_alias over settings when both are provided.

        Args:
            mcp_server: The MCP server details containing configuration
            user_id: Optional user ID for credential resolution
            project_name: Optional project name for credential resolution

        Returns:
            The built MCP server configuration
        """
        actual_config = (
            mcp_server.config.model_copy(deep=True)
            if mcp_server.config
            else MCPServerConfig(
                command=mcp_server.command,
            )
        )

        # Set basic configuration
        if mcp_server.command:
            actual_config.command = mcp_server.command
        if mcp_server.arguments:
            actual_config.args = mcp_server.arguments.split()

        # Initialize environment variables
        env_vars = actual_config.env or {}

        # Resolve credentials with priority logic
        resolved_env_vars = cls._resolve_credentials_with_priority(mcp_server, user_id, project_name)
        env_vars.update(resolved_env_vars)

        actual_config.env = env_vars
        return actual_config

    @classmethod
    def _normalize_placeholders(cls, string: str) -> Tuple[str, bool]:
        """
        Normalize variable placeholders from multiple formats to {{VARIABLE_NAME}} format.

        Supports variable names with uppercase letters, lowercase letters, numbers, underscores, and dots.
        Variable names must start with a letter (uppercase or lowercase) or underscore.
        Dots can be used for nested properties (e.g., {{user.name}}, {{config.api.key}}).

        Supported input formats:
        - Square brackets: [variable_name] -> {{variable_name}}
        - Dollar sign: $variable_name -> {{variable_name}}
        - Double braces: {{variable_name}} (already normalized)
        - Nested properties: {{user.name}}, [user.name], $user.name

        Args:
            string: Input string potentially containing variable placeholders

        Returns:
            Tuple of (normalized_string, placeholders_found)
        """
        if not string:
            return string, False

        placeholders_found = False

        # Pattern for valid variable names: start with letter or underscore,
        # followed by letters, digits, underscores, or dots
        # This supports nested properties like user.name, config.api.key
        variable_pattern = r'[a-zA-Z_][a-zA-Z0-9_\.]*'

        # Check for and convert [variable_name] -> {{variable_name}}
        square_bracket_pattern = rf'\[({variable_pattern})\]'
        if re.search(square_bracket_pattern, string):
            string = re.sub(square_bracket_pattern, r'{{\1}}', string)
            placeholders_found = True

        # Check if {{variable_name}} exists (already in target format)
        double_brace_pattern = rf'\{{\{{({variable_pattern})\}}\}}'
        if not placeholders_found and re.search(double_brace_pattern, string):
            placeholders_found = True

        return string, placeholders_found

    @classmethod
    def _process_server_args(
        cls,
        server_config: MCPServerConfig,
        mcp_server_args_preprocessor: callable | None,
    ) -> None:
        """
        Process server arguments with optional preprocessor.

        Args:
            server_config: The server configuration to modify
            mcp_server_args_preprocessor: Optional preprocessor function for arguments
        """
        if server_config.args and mcp_server_args_preprocessor:
            server_config.args = [mcp_server_args_preprocessor(arg, None) for arg in server_config.args]

    @classmethod
    def _process_server_url_and_command(
        cls,
        server_config: MCPServerConfig,
        mcp_server_args_preprocessor: callable | None,
    ) -> None:
        """
        Process server URL, command, and headers placeholders.

        Args:
            server_config: The server configuration to modify
            mcp_server_args_preprocessor: Optional preprocessor function
        """
        if server_config.url:
            server_config.url = cls._process_string_with_placeholders(
                server_config.url, server_config.env, mcp_server_args_preprocessor
            )
        elif server_config.command:
            server_config.command = cls._process_string_with_placeholders(
                server_config.command, server_config.env, mcp_server_args_preprocessor
            )

        # Process headers if they exist
        if server_config.headers:
            cls._process_headers_placeholders(server_config, mcp_server_args_preprocessor)

    @classmethod
    def _process_headers_placeholders(
        cls,
        server_config: MCPServerConfig,
        mcp_server_args_preprocessor: callable | None,
    ) -> None:
        """
        Process placeholders in headers dictionary.

        Supports user context placeholders ({{user.name}}, {{user.username}}, {{user.token}})
        by retrieving the current user from ContextVar and adding user fields
        to the environment variables used for placeholder resolution.

        Args:
            server_config: The server configuration containing headers to modify
            mcp_server_args_preprocessor: Optional preprocessor function
        """
        if not server_config.headers:
            return

        # Build environment variables with user context
        env_vars_with_user = cls._build_env_with_user_context(server_config)

        # Add user token if needed (exchanged for server audience when configured)
        cls._add_user_token_if_needed(server_config.headers, env_vars_with_user, server_config.audience)

        # Process all headers with placeholder resolution
        server_config.headers = cls._process_headers_dict(
            server_config.headers, env_vars_with_user, mcp_server_args_preprocessor
        )
        logger.debug(f"Processed headers with placeholders: {server_config.headers}")

    @classmethod
    def _build_env_with_user_context(cls, server_config: MCPServerConfig) -> dict[str, Any]:
        """
        Build environment variables dictionary enriched with current user context.

        Args:
            server_config: The server configuration containing base environment variables

        Returns:
            Dictionary with environment variables including user context fields
        """
        env_vars = dict(server_config.env or {})
        current_user = get_current_user()

        if not current_user:
            return env_vars

        env_vars['user'] = {}
        if current_user.name:
            env_vars['user']['name'] = current_user.name
        if current_user.username:
            env_vars['user']['username'] = current_user.username

        return env_vars

    @classmethod
    def _has_token_placeholder(cls, headers: dict[str, str]) -> bool:
        """
        Check if any header value contains a user token placeholder.

        Supports multiple placeholder formats: {{user.token}}, [user.token], $user.token

        Args:
            headers: Dictionary of headers to check

        Returns:
            True if any header contains a token placeholder
        """
        token_patterns = ('{{user.token}}', '[user.token]', '$user.token')
        return any(any(pattern in str(value) for pattern in token_patterns) for value in headers.values())

    @classmethod
    def _add_user_token_if_needed(
        cls,
        headers: dict[str, str],
        env_vars_with_user: dict[str, Any],
        audience: str | None = None,
    ) -> None:
        """
        Add user token to environment variables if headers contain token placeholder.

        When ``audience`` is set and ``TOKEN_EXCHANGE_URL`` is configured, the user's
        IdP token is exchanged for a service-specific token scoped to that audience via
        OIDC token exchange (RFC 8693). Otherwise the raw IdP token is used as-is.

        Args:
            headers: Dictionary of headers to check for token placeholder
            env_vars_with_user: Environment variables dict to modify (will add 'user.token' if needed)
            audience: Optional OAuth2 audience. When provided, triggers OIDC token exchange.
        """
        if not cls._has_token_placeholder(headers):
            return

        # Ensure user dict exists
        if 'user' not in env_vars_with_user:
            env_vars_with_user['user'] = {}

        current_user = get_current_user()
        if not current_user:
            logger.warning("Token placeholder detected but no current user in context")
            return

        try:
            if audience and config.TOKEN_EXCHANGE_URL:
                from codemie.service.security.oidc_token_exchange_service import oidc_token_exchange_service

                token = oidc_token_exchange_service.get_exchanged_token(audience)
            else:
                token = token_exchange_service.get_token_for_current_user()

            if token:
                env_vars_with_user['user']['token'] = token
                logger.debug(f"Added user token to placeholder environment for user={current_user.username}")
            else:
                logger.warning(f"Token placeholder detected but no token available for user={current_user.username}")
        except BrokerAuthRequiredException:
            raise
        except Exception as e:
            # SECURITY: Never log the token or exception details that might expose it
            logger.error(f"Failed to retrieve token for placeholder resolution: {type(e).__name__}", exc_info=True)
            # Continue without token - let placeholder resolution fail naturally

    @classmethod
    def _process_headers_dict(
        cls,
        headers: dict[str, str],
        env_vars_with_user: dict[str, Any],
        mcp_server_args_preprocessor: callable | None,
    ) -> dict[str, str]:
        """
        Process all headers by resolving placeholders in both keys and values.

        Args:
            headers: Original headers dictionary
            env_vars_with_user: Environment variables for placeholder resolution
            mcp_server_args_preprocessor: Optional preprocessor function

        Returns:
            New dictionary with processed headers
        """
        processed_headers = {}
        for key, value in headers.items():
            processed_key = cls._process_string_with_placeholders(key, env_vars_with_user, mcp_server_args_preprocessor)
            processed_value = cls._process_string_with_placeholders(
                value, env_vars_with_user, mcp_server_args_preprocessor
            )
            processed_headers[processed_key] = processed_value

        return processed_headers

    @classmethod
    def _process_string_with_placeholders(
        cls,
        source_string: str,
        env_vars: dict[str, Any],
        mcp_server_args_preprocessor: callable | None,
    ) -> str:
        """
        Process a string by normalizing placeholders and applying transformations.

        Args:
            source_string: The string to process
            env_vars: Environment variables for placeholder resolution
            mcp_server_args_preprocessor: Optional preprocessor function

        Returns:
            Processed string with placeholders resolved
        """

        normalized_string, found = cls._normalize_placeholders(source_string)
        if found:
            if mcp_server_args_preprocessor:
                normalized_string = mcp_server_args_preprocessor(normalized_string, env_vars)
            else:
                # Lazy import to avoid circular dependency
                from codemie.service.tools.dynamic_value_utils import process_string

                normalized_string = process_string(
                    source=normalized_string,
                    context=None,
                    initial_dynamic_vals=env_vars,
                    enable_recursive_resolution=None,
                )
        return normalized_string

    @classmethod
    def _find_tool_config_by_name(cls, tools_config: list[ToolConfig] | None, name: str) -> ToolConfig | None:
        """
        Find a specific tool configuration by name from a list of tool configurations.

        Args:
            tools_config: List of tool configurations
            name: Name of the tool to find

        Returns:
            The found tool configuration or None if not found
        """
        if tools_config:
            return next((tc for tc in tools_config if tc.name == name), None)
        return None

    @classmethod
    def _apply_tool_config_to_mcp_server(
        cls, server_config: MCPServerConfig, tool_config: ToolConfig, user_id: str | None = None
    ) -> None:
        """
        Apply tool configuration to MCP server configuration.

        Args:
            server_config: The MCP server configuration to modify
            tool_config: The tool configuration containing credentials or integration reference
            user_id: Optional user ID for credential resolution when using integration_id
        """
        try:
            # Handle direct credentials
            if tool_config.tool_creds:
                logger.debug("Applying direct credentials to MCP server config")
                if isinstance(tool_config.tool_creds, dict):
                    server_config.env.update(tool_config.tool_creds)
                else:
                    logger.warning(f"Invalid tool_creds format: expected dict, got {type(tool_config.tool_creds)}")
                return

            # Handle integration reference
            if tool_config.integration_id:
                logger.debug(f"Resolving integration credentials for integration_id: {tool_config.integration_id}")
                resolved_env_vars = cls._resolve_credentials_by_id(
                    integration_id=tool_config.integration_id, user_id=user_id
                )
                if resolved_env_vars:
                    server_config.env.update(resolved_env_vars)
                    logger.debug(f"Applied {len(resolved_env_vars)} environment variables from integration")

        except Exception as e:
            logger.error(f"Failed to apply tool config to MCP server: {str(e)}")
            # Don't raise the exception to avoid breaking the entire MCP server initialization


# Initialize the singleton instance with the default MCP connection
MCPToolkitService.init_singleton(MCPConnectClient(config.MCP_CONNECT_URL))
