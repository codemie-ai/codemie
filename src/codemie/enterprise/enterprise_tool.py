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

"""Enterprise tool protocol and wrapper.

This module provides the protocol definition and wrapper function for
integrating enterprise plugin tools with the CodeMie tool system.

Architecture:
    1. EnterpriseTool protocol defines the interface
    2. wrap_enterprise_plugin_tool() creates CodeMieTool instances
    3. json_schema_to_model() converts JSON Schema to Pydantic models
    4. Enterprise package provides EnterprisePluginBaseTool base class
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, Type, runtime_checkable

from langchain_core.tools import ToolException
from pydantic import BaseModel

from codemie.core.json_schema_utils import json_schema_to_model
from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.models import ToolOutputFormat

logger = logging.getLogger(__name__)


def _sanitize_tool_input(value: Any) -> Any:
    """Recursively convert Pydantic models to dictionaries for JSON serialization.

    This function ensures that all tool inputs are JSON-serializable by converting
    Pydantic BaseModel instances to dictionaries. This is necessary because:
    1. LangChain/LangGraph may pass Pydantic models as tool arguments
    2. Enterprise tools (e.g., MCP via plugin) need to serialize inputs to JSON via NATS
    3. Pydantic models are not JSON serializable by default

    Args:
        value: Any value that might contain Pydantic models

    Returns:
        JSON-serializable version of the value
    """
    # Handle Pydantic models (including subclasses)
    if isinstance(value, BaseModel):
        # Use model_dump() for Pydantic v2, fallback to dict() for v1
        if hasattr(value, "model_dump"):
            return value.model_dump()
        else:
            return value.dict()

    # Handle lists recursively
    elif isinstance(value, list):
        return [_sanitize_tool_input(item) for item in value]

    # Handle dicts recursively
    elif isinstance(value, dict):
        return {key: _sanitize_tool_input(val) for key, val in value.items()}

    # Handle tuples recursively (convert to list for JSON compatibility)
    elif isinstance(value, tuple):
        return [_sanitize_tool_input(item) for item in value]

    # Return primitive types as-is (str, int, float, bool, None)
    else:
        return value


@runtime_checkable
class EnterpriseTool(Protocol):
    """Protocol for enterprise plugin tools.

    Any enterprise tool (e.g., ToolConsumer, EnterprisePluginBaseTool)
    must implement this protocol to be compatible with the wrapper system.
    """

    @property
    def name(self) -> str:
        """Tool name identifier."""
        ...

    @property
    def description(self) -> str:
        """Tool description for LLM understanding."""
        ...

    @property
    def args_schema(self) -> dict[str, Any] | BaseModel:
        """JSON Schema defining tool parameters."""
        ...

    @property
    def output_format(self) -> str:
        """Tool output format, e.g., 'text' or 'markdown'."""
        ...

    def execute(self, *args, **kwargs) -> Any:
        """Execute the tool with given parameters.

        Can return either:
        - str: Synchronous result
        - Coroutine[str]: Async result that the wrapper will await/run

        Args:
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            str or Coroutine[str]: Tool result
        """
        ...


def _convert_args_schema_to_pydantic(args_schema_dict: dict[str, Any] | BaseModel, tool_name: str) -> Type[BaseModel]:
    """Convert JSON Schema or Pydantic model to Pydantic model class.

    Args:
        args_schema_dict: JSON Schema dict or existing Pydantic model
        tool_name: Tool name for error messages

    Returns:
        Pydantic BaseModel class

    Raises:
        ValueError: If schema conversion fails
    """
    try:
        if isinstance(args_schema_dict, dict):
            return json_schema_to_model(args_schema_dict)
        else:
            return type(args_schema_dict)
    except (TypeError, ValueError, NotImplementedError) as e:
        logger.error(
            f"Failed to convert JSON Schema to Pydantic model for tool {tool_name}: {e}. Schema: {args_schema_dict}"
        )
        raise ValueError(f"Invalid args_schema for tool {tool_name}: {e}") from e


def wrap_enterprise_tool(enterprise_tool: EnterpriseTool) -> CodeMieTool:
    """Wrap any enterprise tool as a CodeMieTool.

    This function creates a dynamic CodeMieTool subclass that wraps any
    enterprise tool implementing the EnterpriseTool protocol and provides
    full LangChain integration.

    The wrapper is protocol-based and headless - it works with any enterprise
    tool (plugins, cloud services, custom integrations) that implements the
    EnterpriseTool protocol.

    The wrapper:
    - Converts JSON Schema to Pydantic model using json_schema_to_model()
    - Provides async invocation of enterprise tools
    - Handles token limiting and error handling via CodeMieTool
    - Follows protocol-based design for type safety

    Args:
        enterprise_tool: Any enterprise tool instance implementing EnterpriseTool
                        protocol (e.g., ToolConsumer, EnterprisePluginBaseTool,
                        or any custom enterprise tool)

    Returns:
        CodeMieTool instance ready for use with LangChain agents

    Raises:
        TypeError: If enterprise_tool doesn't implement EnterpriseTool protocol
        ValueError: If args_schema is invalid or cannot be converted
    """
    # Validate protocol compliance
    if not isinstance(enterprise_tool, EnterpriseTool):
        raise TypeError(f"enterprise_tool must implement EnterpriseTool protocol, got {type(enterprise_tool).__name__}")

    # Extract tool metadata
    tool_name = enterprise_tool.name
    tool_description = enterprise_tool.description
    args_schema_dict = enterprise_tool.args_schema
    tool_output_format = enterprise_tool.output_format

    # Convert JSON Schema to Pydantic model using helper function
    args_schema_class = _convert_args_schema_to_pydantic(args_schema_dict, tool_name)

    # Store enterprise tool reference outside class to avoid deepcopy issues
    enterprise_tool_ref = enterprise_tool

    # Create dynamic CodeMieTool subclass
    class EnterpriseToolWrapper(CodeMieTool):
        """Dynamically created wrapper for enterprise tool.

        This class is created at runtime and provides LangChain integration
        for any enterprise tool via CodeMieTool.
        """

        # Tool metadata
        name: str = tool_name
        description: str = tool_description
        args_schema: Type[BaseModel] = args_schema_class  # pyright: ignore
        base_name: str = tool_name  # pyright: ignore

        # CodeMie-specific configuration
        output_format: ToolOutputFormat = ToolOutputFormat(tool_output_format)

        def __init__(self, **kwargs):
            """Initialize the wrapped enterprise tool."""
            super().__init__(**kwargs)

            # Initialize metadata dict
            if not hasattr(self, "metadata") or self.metadata is None:
                self.metadata = {}

        def execute(self, **kwargs) -> Any:
            """Execute the tool by calling the enterprise provider.

            This method is called by CodeMieTool's _run() method and provides
            the bridge between LangChain's sync tool interface and the
            enterprise tool's interface.

            Args:
                **kwargs: Tool parameters matching args_schema

            Returns:
                Tool result as string

            Raises:
                ToolException: If execution fails
            """
            try:
                import asyncio
                import inspect

                # Log tool execution details for debugging
                logger.debug(f"Executing enterprise tool: {self.name}")
                logger.debug(f"kwargs keys: {list(kwargs.keys())}")
                logger.debug(
                    f"kwargs types (before sanitization): {[(k, type(v).__name__) for k, v in kwargs.items()]}"
                )

                # Log detailed info about each kwarg
                for key, value in kwargs.items():
                    logger.debug(f"kwarg '{key}': type={type(value).__name__}, value_preview={str(value)[:200]}")

                # Sanitize kwargs: convert Pydantic models to dicts for JSON serialization
                # This is required because enterprise tools (e.g., MCP via plugin) serialize to JSON
                sanitized_kwargs = {key: _sanitize_tool_input(value) for key, value in kwargs.items()}

                logger.debug(
                    f"kwargs types (after sanitization): {[(k, type(v).__name__) for k, v in sanitized_kwargs.items()]}"
                )

                # Call enterprise tool's execute method with sanitized inputs
                result = enterprise_tool_ref.execute(**sanitized_kwargs)

                # If result is a coroutine, run it properly
                if inspect.iscoroutine(result):
                    # Check if we're already in an event loop
                    try:
                        loop = asyncio.get_running_loop()
                        try:
                            # Use the existing loop with run_until_complete after applying nest_asyncio
                            return loop.run_until_complete(result)
                        except ImportError:
                            raise RuntimeError(
                                f"Cannot run async tool {self.name} synchronously from within an event loop. "
                                f"Either:\n"
                                f"  1. Install nest-asyncio: pip install nest-asyncio\n"
                                f"  2. Use async context: await tool._arun(**kwargs)"
                            ) from None
                    except RuntimeError:
                        # No event loop running - safe to create one
                        return asyncio.run(result)

                # Sync result, return directly
                return result

            except Exception as e:
                error_msg = f"Enterprise tool {self.name} failed: {str(e)}"
                logger.exception(error_msg)
                raise ToolException(error_msg) from e

        async def _arun(self, **kwargs) -> str:
            """Async execution of the tool.

            This is called when the tool is used in async context (e.g.,
            with async agents or workflows).

            Args:
                **kwargs: Tool parameters

            Returns:
                Tool result as string

            Raises:
                ToolException: If execution fails
            """
            try:
                # Log async execution details
                logger.debug(f"Async executing enterprise tool: {self.name}")
                logger.debug(f"async kwargs keys: {list(kwargs.keys())}")
                logger.debug(
                    f"async kwargs types (before sanitization): {[(k, type(v).__name__) for k, v in kwargs.items()]}"
                )

                # Log detailed info about each kwarg
                for key, value in kwargs.items():
                    logger.debug(f"async kwarg '{key}': type={type(value).__name__}, value_preview={str(value)[:200]}")

                # Sanitize kwargs: convert Pydantic models to dicts for JSON serialization
                sanitized_kwargs = {key: _sanitize_tool_input(value) for key, value in kwargs.items()}

                logger.debug(
                    f"async kwargs types (after sanitization): {[(k, type(v).__name__) for k, v in sanitized_kwargs.items()]}"
                )

                # For async context, we need to await if it's a coroutine
                result = enterprise_tool_ref.execute(**sanitized_kwargs)

                # Check if result is a coroutine and await it
                import inspect

                if inspect.iscoroutine(result):
                    result = await result

                return result

            except Exception as e:
                error_msg = f"Enterprise tool {self.name} failed: {str(e)}"
                logger.exception(error_msg)
                raise ToolException(error_msg) from e

    # Create and return instance of wrapped tool
    return EnterpriseToolWrapper()


# Backward compatibility alias
def wrap_enterprise_plugin_tool(enterprise_tool: EnterpriseTool) -> CodeMieTool:
    """Backward compatibility wrapper for wrap_enterprise_tool.

    Deprecated: Use wrap_enterprise_tool instead.

    Args:
        enterprise_tool: Enterprise tool instance

    Returns:
        CodeMieTool instance
    """
    return wrap_enterprise_tool(enterprise_tool)
