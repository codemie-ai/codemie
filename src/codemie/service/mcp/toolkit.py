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
MCP Toolkit implementation.

This module provides classes for representing MCP tools and toolkits
for integration with the CodeMie tool system.
"""

import asyncio
import base64
import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from typing import Any, Type, override

from cachetools import TTLCache
from pydantic import BaseModel, create_model

from codemie_tools.base.base_toolkit import BaseToolkit
from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.errors import TruncatedOutputError
from codemie_tools.base.utils import get_encoding

from codemie.configs.config import config
from codemie.configs.logger import logger
from codemie.core.constants import TOOL_TYPE, ToolType, MCP_IMAGES_SUBDIR
from codemie.core.json_schema_utils import json_schema_to_model
from codemie.repository.repository_factory import FileRepositoryFactory
from codemie.service.mcp.client import MCPConnectClient
from codemie.service.mcp.models import (
    MCPServerConfig,
    MCPToolDefinition,
    MCPToolInvocationResponse,
    MCPExecutionContext,
    MCPToolContentItem,
)

# MIME type to file extension mapping for screenshots
MIME_TO_EXTENSION = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}


class MCPToolExecutionError(Exception):
    """Exception raised when MCP tool execution fails.

    This exception is raised when there is an error during the execution of an MCP tool,
    such as connection failures, invalid responses, or server-side errors. The exception
    message typically includes details about the specific error encountered.
    """

    pass


class MCPTool(CodeMieTool):
    """
    Tool implementation for MCP (Model-Connecting Protocol) tools provided by an MCP server.

    Dynamically created based on tool definitions received from an MCP server.
    Serves as a bridge between CodeMie and external MCP tool functionality.

    Attributes:
        mcp_server_config: Configuration for the MCP server
        mcp_client: Client for communicating with MCP-Connect
        tokens_size_limit: Maximum token size limit for the tool output
    """

    # Additional attributes for MCP tools
    mcp_server_config: MCPServerConfig
    mcp_client: MCPConnectClient
    # Override tokens_size_limit with the value from config
    tokens_size_limit: int = config.MCP_TOOL_TOKENS_SIZE_LIMIT

    def __init__(
        self,
        name: str,
        description: str,
        mcp_client: MCPConnectClient,
        mcp_server_config: MCPServerConfig,
        args_schema: Type[BaseModel],
        **kwargs,
    ):
        """
        Initialize an MCP tool.

        Args:
            name: Name of the tool
            description: Description of the tool
            mcp_client: Client for communicating with MCP-Connect
            mcp_server_config: Configuration for the MCP server
            args_schema: Schema for tool arguments
            **kwargs: Additional keyword arguments to pass to parent constructor
        """
        super().__init__(
            name=name,
            description=description,
            mcp_client=mcp_client,
            mcp_server_config=mcp_server_config,
            args_schema=args_schema,
            **kwargs,
        )

        # Also set as instance attributes for direct access
        self.mcp_client = mcp_client
        self.mcp_server_config = mcp_server_config
        if self.metadata is None:
            self.metadata = {}
        self.metadata[TOOL_TYPE] = ToolType.MCP

    async def _aexecute_with_context(
        self, execution_context: MCPExecutionContext | None = None, **kwargs
    ) -> MCPToolInvocationResponse:
        """
        Asynchronously execute the MCP tool with optional execution context.

        Args:
            execution_context: Optional execution context for the tool invocation
            **kwargs: Tool arguments specified according to the tool's args_schema

        Returns:
            MCPToolInvocationResponse: Response from the tool invocation

        Raises:
            Exception: If the tool invocation fails or returns an error
        """
        try:
            response = await self.mcp_client.invoke_tool(
                server_config=self.mcp_server_config,
                tool_name=self.name,
                tool_args=kwargs,
                execution_context=execution_context,  # Pass context to client
            )

            # Check for errors in response
            if response.isError:
                error_messages = [item.text for item in response.content]
                error_text = "\n".join(error_messages) if error_messages else "Unknown error"
                raise MCPToolExecutionError(f"MCP tool execution failed: {error_text}")

            # Combine text from all content items
            return response

        except Exception as e:
            if isinstance(e, MCPToolExecutionError):
                # Re-raise known error types without modification
                logger.error(str(e))
                raise
            error_message = (
                "\n**This is not an AI/Run CodeMie error**.\n"
                "The Error has been thrown in the MCP server.\n"
                "Please check your MCP Server and its configuration.\n"
                "Error executing MCP tool:\n"
                f"{self.name}: {type(e).__name__}: {e}"
            )
            logger.error(error_message)
            # add more details to the error message and reraise the exception
            raise MCPToolExecutionError(error_message) from e

    def execute_with_context(
        self, execution_context: MCPExecutionContext | None = None, **kwargs
    ) -> MCPToolInvocationResponse:
        """
        Execute the MCP tool synchronously with optional execution context.

        Args:
            execution_context: Optional execution context for the tool invocation
            *args: Positional arguments (not used but included for compatibility)
            **kwargs: Keyword arguments passed to the tool

        Returns:
            MCPToolInvocationResponse: The result of the tool execution
        """
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
                    lambda: asyncio.run(self._aexecute_with_context(execution_context=execution_context, **kwargs))
                )
                return future.result()
        else:
            # No loop is running, so run the coroutine directly.
            return asyncio.run(self._aexecute_with_context(execution_context=execution_context, **kwargs))

    def execute(self, *args, **kwargs) -> MCPToolInvocationResponse:
        return self.execute_with_context(execution_context=None, **kwargs)

    @override
    def _limit_output_content(self, output: Any) -> Any:
        """
        Limit the size of the output based on token constraints.

        Args:
            output (Any): The content to be processed and potentially truncated.

        Returns:
            Tuple[Any, int]: The (possibly truncated) output and the token count.

        Raises:
            TruncatedOutputError: If the output exceeds the token size limit and throwing errors is enabled.
        """
        str_output = (
            "\n".join(str(item) for item in output.content if not item.is_image())
            if isinstance(output, MCPToolInvocationResponse)
            else str(output)
        )

        encoding = get_encoding(self.base_llm_model_name)
        tokens = encoding.encode(str_output)
        token_count = len(tokens)

        logger.info(f"{self.name}: Tokens size of potential response: {token_count}")

        if token_count <= self.tokens_size_limit:
            return output, token_count

        # Output exceeds token limit: calculate truncation details
        truncate_ratio = self.tokens_size_limit / token_count
        truncated_data = encoding.decode(tokens[: self.tokens_size_limit])
        truncated_output = (
            f"{self.truncate_message} Ratio limit/used_tokens: {truncate_ratio}. Tool output: {truncated_data}"
        )
        error_message = (
            f"{self.name} output is too long: {token_count} tokens. "
            f"Ratio limit/used_tokens: {truncate_ratio} for output tokens {self.tokens_size_limit}"
        )

        logger.error(error_message)

        if self.throw_truncated_error:
            raise TruncatedOutputError(truncated_output)

        return truncated_output, token_count

    @override
    def _post_process_output_content(self, output: Any, *args, **kwargs) -> Any:
        if isinstance(output, MCPToolInvocationResponse) and output.content:
            return _convert_mcp_response_to_tool_message(output)
        return super()._post_process_output_content(output, *args, **kwargs)


class ContextAwareMCPTool(MCPTool):
    def __init__(self, original_tool: MCPTool, context: MCPExecutionContext):
        # Collect additional kwargs from original tool to pass to parent constructor
        kwargs = {}

        # List of BaseTool and CodeMieTool attributes that can be passed as kwargs
        tool_attributes = [
            'return_direct',
            'verbose',
            'callbacks',
            'callback_manager',
            'tags',
            'metadata',
            'handle_tool_error',
            'handle_validation_error',
            'response_format',
            'tokens_size_limit',
            'base_llm_model_name',
            'throw_truncated_error',
            'truncate_message',
        ]

        for attr in tool_attributes:
            if hasattr(original_tool, attr):
                value = getattr(original_tool, attr)
                if value is not None:
                    kwargs[attr] = value

        # Copy all attributes from original tool
        super().__init__(
            name=original_tool.name,
            description=original_tool.description,
            mcp_client=original_tool.mcp_client,
            mcp_server_config=original_tool.mcp_server_config,
            args_schema=original_tool.args_schema,
            **kwargs,
        )
        self._execution_context = context

    def execute(self, *args, **kwargs):
        return self.execute_with_context(execution_context=self._execution_context, **kwargs)


def _save_screenshot_to_storage(image_data: str, mime_type: str) -> str:
    """
    Save screenshot to file storage and return encoded URL.

    Args:
        image_data: Base64 encoded image data
        mime_type: MIME type of the image

    Returns:
        Encoded file URL for accessing the saved screenshot

    Raises:
        Exception: If saving fails
    """
    file_repo = FileRepositoryFactory().get_current_repository()

    owner = MCP_IMAGES_SUBDIR

    # Generate filename with appropriate extension
    extension = MIME_TO_EXTENSION.get(mime_type.lower(), ".png")
    file_name = f"{uuid.uuid4().hex}{extension}"

    # Decode and save
    image_bytes = base64.b64decode(image_data)
    file_obj = file_repo.write_file(name=file_name, mime_type=mime_type, owner=owner, content=image_bytes)

    logger.info(f"Saved MCP screenshot: {file_name} ({len(image_bytes)} bytes) to {owner}")
    return file_obj.to_encoded_url()


def _convert_image_to_url(item: MCPToolContentItem) -> None:
    """
    Convert image item to image_url with file reference.

    Saves image to storage and replaces base64 data with file URL

    Args:
        item: Content item to process (modified in place)
    """
    if not item.data:
        return

    try:
        # Save to storage and get file URL
        url = _save_screenshot_to_storage(item.data, item.mimeType or "image/png")
        item.type = 'image_url'
        item.image_url = {"url": f"sandbox:/v1/files/{url}", "detail": "high"}
        item.data = None
        item.mimeType = None

    except (IOError, OSError, ValueError, base64.binascii.Error) as e:
        # Fallback to base64 on error (will cause context overflow but prevents crash)
        logger.error(f"Failed to save MCP screenshot: {e}", exc_info=True)
        item.type = 'image_url'
        item.image_url = {"url": f"data:{item.mimeType};base64,{item.data}", "detail": "high"}
        item.data = None
        item.mimeType = None


def _post_process_tool_result(tool_result: MCPToolInvocationResponse) -> MCPToolInvocationResponse:
    """
    Process MCP tool result and save images to storage.

    Args:
        tool_result: MCP tool execution response

    Returns:
        Processed response with images saved to storage
    """
    for item in tool_result.content:
        if item.type == 'image':
            _convert_image_to_url(item)
    return tool_result


def _format_content_item(item: MCPToolContentItem) -> str | None:
    """
    Format a single content item for LLM consumption.

    Args:
        item: Content item to format

    Returns:
        Formatted string or None if item should be skipped
    """
    if item.type in ("text", "error"):
        return item.text if item.text else None

    if item.type == "image_url":
        if not item.image_url or "url" not in item.image_url:
            return None
        url = item.image_url["url"]
        if not url.startswith("data:"):
            # Return text + markdown so both LLM and UI can use it:
            return f"Screenshot captured. URL: {url}\n\n![Screenshot]({url})"
        return None

    # Other content types - serialize as JSON
    item_model = {k: v for k, v in item.model_dump().items() if v is not None}
    return json.dumps(item_model, ensure_ascii=False)


def _convert_mcp_response_to_tool_message(tool_result: MCPToolInvocationResponse) -> str:
    """
    Convert MCP tool response to text format for LLM.

    Images are saved to storage (preventing context overflow) and replaced with
    file URLs in sandbox:// format for UI rendering.

    Args:
        tool_result: MCP tool execution response

    Returns:
        Formatted text for LLM consumption
    """
    tool_result = _post_process_tool_result(tool_result)

    text_parts = []
    for item in tool_result.content:
        formatted = _format_content_item(item)
        if formatted:
            text_parts.append(formatted)

    return "\n".join(text_parts)


class MCPToolkit(BaseToolkit):
    """
    Toolkit implementation for MCP (Model-Connecting Protocol) tools from an MCP server.

    Created dynamically based on tool definitions from an MCP server.
    Serves as a collection of related MCP tools that can be used together,
    handling tool creation and management based on server-provided definitions.

    This class bridges between CodeMie and external MCP tool providers by creating
    a consistent interface for tools defined by the MCP server, regardless of their
    underlying implementation details.
    """

    name: str
    description: str
    mcp_client: MCPConnectClient
    mcp_server_config: MCPServerConfig
    tools_definitions: list[MCPToolDefinition]
    tools: list[MCPTool] = []  # Will be populated during initialization

    class Config:
        """Pydantic model configuration."""

        arbitrary_types_allowed = True

    def __init__(
        self,
        name: str,
        description: str,
        mcp_client: MCPConnectClient,
        mcp_server_config: MCPServerConfig,
        tools_definitions: list[MCPToolDefinition],
    ):
        """
        Initialize an MCP toolkit.

        Args:
            name: Name of the toolkit
            description: Description of the toolkit
            mcp_client: Client for communicating with MCP-Connect
            mcp_server_config: Configuration for the MCP server
            tools_definitions: List of tool definitions from the MCP server
        """
        super().__init__(
            name=name,
            description=description,
            mcp_client=mcp_client,
            mcp_server_config=mcp_server_config,
            tools_definitions=tools_definitions,
        )

        # Create tools based on definitions
        self.tools = self._create_tools()

    def _create_args_schema(self, tool_def: MCPToolDefinition) -> Type[BaseModel]:
        """
        Create a Pydantic model for tool arguments based on the tool's input schema.

        Dynamically generates a Pydantic model class that represents the expected
        input structure for a specific MCP tool. This model is used for validation
        and type conversion when the tool is invoked.

        Args:
            tool_def: Tool definition containing input schema with properties and their types

        Returns:
            A dynamically created Pydantic model class for validating the tool's arguments
        """

        # Process each property in the input schema
        if tool_def.inputSchema:
            args_schema = json_schema_to_model(tool_def.inputSchema)
        else:
            # Create the model dynamically
            args_schema = create_model(f"{tool_def.name.capitalize()}ArgsSchema")

        return args_schema

    def _create_tools(self) -> list[MCPTool]:
        """
        Create MCP tools based on tool definitions.

        Processes each tool definition to create a corresponding MCPTool instance,
        handling any errors that might occur during tool creation. For each tool,
        it creates an appropriate argument schema and initializes the tool with
        the necessary configuration.

        Returns:
            List of instantiated MCP tools ready for use in the toolkit
        """
        tools = []

        for tool_def in self.tools_definitions:
            try:
                # Create args schema for the tool
                args_schema = self._create_args_schema(tool_def)

                # Create and add the tool
                tool = MCPTool(
                    name=tool_def.name,
                    description=tool_def.description,
                    mcp_client=self.mcp_client,
                    mcp_server_config=self.mcp_server_config,
                    args_schema=args_schema,
                )
                tools.append(tool)

            except Exception as e:
                logger.error(f"Failed to create tool {tool_def.name}: {str(e)}", exc_info=True)

        return tools

    def get_tools(self) -> list[MCPTool]:
        """
        Get all tools in this toolkit.

        Provides access to all the MCPTool instances that were created based on
        the tool definitions from the MCP server. These tools can be used to
        interact with the functionality provided by the MCP server.

        Returns:
            List of CodeMieTool instances (specifically MCPTool instances) in this toolkit
        """
        return self.tools

    def get_tool(self, name: str) -> MCPTool | None:
        """
        Get a specific tool by name.

        Provides a convenient way to access a specific tool within the toolkit
        by its name. This is useful when you know which specific tool you need
        rather than working with the complete list of tools.

        Args:
            name: Name of the tool to retrieve, exactly as defined in the MCP server

        Returns:
            The MCPTool instance if a tool with the given name exists in the toolkit, None otherwise
        """
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None

    def get_tools_ui_info(self) -> list[dict[str, Any]]:
        """
        Get UI information for tools in this toolkit.

        Provides a simplified representation of each tool suitable for UI presentation,
        including the tool name, description, and argument schema information. This is
        particularly useful for generating dynamic UI components or documentation for
        the available tools without exposing internal implementation details.

        Returns:
            List of dictionaries containing UI information for each tool, with keys:
            - name: The tool name
            - description: The tool description
            - args_schema: Dictionary of argument names and their types
        """
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "args_schema": dict(tool.args_schema.__annotations__.items())
                if hasattr(tool, "args_schema") and tool.args_schema
                else {},
            }
            for tool in self.tools
        ]

    @classmethod
    def get_toolkit(cls, *args, **kwargs):
        """
        Get this toolkit instance.

        This method overrides the base class method but always raises an exception
        because MCPToolkit instances should be created through the MCPToolkitService
        rather than directly.

        Returns:
            Never returns

        Raises:
            RuntimeError: Always raised to indicate that MCPToolkitService must be used instead
        """
        raise RuntimeError(
            "MCPToolkit instances cannot be created directly using get_toolkit. "
            "Please use MCPToolkitService to create and retrieve MCP toolkits."
        )


class MCPToolkitFactory:
    """
    Factory class for creating and managing MCP toolkits.

    Handles caching of toolkits for performance and provides methods
    for creating and retrieving toolkits. The caching mechanism uses both
    a size limit (LRU policy) and time-based expiration to ensure optimal
    resource usage while maintaining responsiveness.
    """

    def __init__(self, mcp_client: MCPConnectClient, cache_size: int = None, cache_expiry_seconds: int = None):
        """
        Initialize the factory.

        Args:
            mcp_client: Client for communicating with MCP-Connect
            cache_size: Maximum number of toolkits to cache (defaults to config value)
            cache_expiry_seconds: Time in seconds after which cached toolkits expire (defaults to config value)
        """
        self.mcp_client = mcp_client

        # Use provided values or fallback to config values
        _cache_size = cache_size if cache_size is not None else config.MCP_TOOLKIT_FACTORY_CACHE_SIZE
        _cache_ttl = cache_expiry_seconds if cache_expiry_seconds is not None else config.MCP_TOOLKIT_FACTORY_CACHE_TTL

        # Initialize cache using cachetools' TTLCache
        # TTLCache combines LRU eviction policy with time-based expiration
        self._toolkit_cache = TTLCache(maxsize=_cache_size, ttl=_cache_ttl)

    async def create_toolkit(
        self,
        server_config: MCPServerConfig,
        toolkit_name: str | None = None,
        toolkit_description: str | None = None,
        tools_tokens_size_limit: int | None = None,
        use_cache: bool = True,
        execution_context: MCPExecutionContext | None = None,
    ) -> MCPToolkit:
        """
        Create an MCP toolkit for a given server configuration.

        Args:
            server_config: Configuration for the MCP server
            toolkit_name: Optional custom name for the toolkit
            toolkit_description: Optional custom description for the toolkit
            tools_tokens_size_limit: Optional token size limit for tools
            use_cache: Whether to use cached toolkits
            execution_context: Optional execution context with user, assistant, and workflow info

        Returns:
            An MCPToolkit instance

        Raises:
            Exception: If unable to retrieve tools from the MCP server
        """
        cache_key = None
        if use_cache:
            # Generate a cache key for this server configuration
            cache_key = self._generate_cache_key(server_config)

            # Check if we already have a cached toolkit for this configuration
            try:
                cached_toolkit = self._toolkit_cache[cache_key]
                return cached_toolkit
            except KeyError:
                # Not in cache or expired, continue to create a new toolkit
                pass

        try:
            # Get tool definitions from the MCP server
            tool_definitions = await self.mcp_client.list_tools(server_config, execution_context)

            # Generate toolkit name and description if not provided
            name = toolkit_name or f"MCP Toolkit ({server_config.command})"
            description = (
                toolkit_description
                or f"Tools provided by MCP server: {server_config.command} {' '.join(server_config.args)}"
            )

            # Create the toolkit
            toolkit = MCPToolkit(
                name=name,
                description=description,
                mcp_client=self.mcp_client,
                mcp_server_config=server_config,
                tools_definitions=tool_definitions,
            )

            if tools_tokens_size_limit:
                for tool in toolkit.get_tools():
                    if hasattr(tool, 'tokens_size_limit'):
                        tool.tokens_size_limit = tools_tokens_size_limit

            # Cache the toolkit
            if use_cache:
                self._toolkit_cache[cache_key] = toolkit

            return toolkit

        except Exception as e:
            logger.error(f"Failed to create MCP toolkit: {str(e)}")
            raise

    def get_toolkit(self, server_config: MCPServerConfig) -> MCPToolkit | None:
        """
        Get a cached toolkit for a server configuration if available.

        Args:
            server_config: Configuration for the MCP server

        Returns:
            Cached toolkit if available, None otherwise
        """
        cache_key = self._generate_cache_key(server_config)
        try:
            return self._toolkit_cache[cache_key]
        except KeyError:
            return None

    @staticmethod
    def _generate_cache_key(server_config: MCPServerConfig) -> str:
        """
        Generate a cache key for a server configuration using a SHA-256 hash.

        This method creates a deterministic but compact hash from the server configuration,
        ensuring that even very large configurations result in a reasonably sized cache key.
        The hash is generated from a JSON representation of the server config's command, args,
        and environment variables, ensuring that any change to these values will result in
        a different cache key.

        Args:
            server_config: Configuration for the MCP server containing command, args, and env vars

        Returns:
            A string hash (hexadecimal SHA-256) that uniquely identifies the server configuration
        """
        # Create a dictionary with all relevant configuration parts
        config_dict = {
            "command": server_config.url if server_config.url else server_config.command,
            "args": server_config.args,
            "env": server_config.env,
        }

        # Convert to a stable JSON string (sorted keys for determinism)
        config_json = json.dumps(config_dict, sort_keys=True)

        # Generate SHA-256 hash and return the hexadecimal representation
        hash_obj = hashlib.sha256(config_json.encode())
        return hash_obj.hexdigest()

    def clear_cache(self):
        """Clear the toolkit cache.

        Removes all cached toolkits, forcing new requests to create fresh toolkit instances.
        This is useful when you want to ensure that all subsequent toolkit requests fetch
        the latest tool definitions from MCP servers, or when memory usage needs to be reduced.
        """
        self._toolkit_cache.clear()

    def remove_toolkit_from_cache(self, server_config: MCPServerConfig):
        """
        Remove a specific toolkit from the cache.

        This method invalidates the cached toolkit for a specific server configuration,
        ensuring the next request for this configuration will create a fresh toolkit.
        Useful when you know a server's tool definitions have changed.

        Args:
            server_config: Configuration for the MCP server whose toolkit should be removed
        """
        cache_key = self._generate_cache_key(server_config)
        with suppress(KeyError):
            del self._toolkit_cache[cache_key]
