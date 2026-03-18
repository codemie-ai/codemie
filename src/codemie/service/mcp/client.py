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
Client for interacting with MCP-Connect.

This module contains a client for making requests to MCP-Connect,
which acts as a bridge to MCP servers. It provides functionality for listing
available tools and invoking them on remote MCP servers through the MCP-Connect bridge.

The module handles authentication, request routing, and response parsing while
providing a clean interface for interacting with MCP services.
"""

import json
import traceback
from typing import Any, Final

import hashlib
import httpx
from pydantic import ValidationError

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.service.mcp.models import (
    MCPServerConfig,
    MCPListToolsResponse,
    MCPToolDefinition,
    MCPToolInvocationRequest,
    MCPToolInvocationResponse,
    MCPExecutionContext,
)

MCP_CONNECT_BUCKET_PLACEHOLDER: Final[str] = "{MCP_CONNECT_BUCKET}"
X_MCP_CONNECT_BUCKET: Final[str] = "X-MCP-Connect-Bucket"
BUCKET_KEY: Final[str] = 'BUCKET_KEY'


class MCPConnectClient:
    """
    Client for interacting with MCP-Connect.

    Handles communication with MCP-Connect to list available tools
    and invoke tools on MCP servers. Provides asynchronous methods
    for tool listing and invocation with proper error handling and
    response validation.

    Attributes:
        base_url (str): Base URL of the MCP-Connect service
        bridge_endpoint (str): Complete URL for the bridge endpoint
        timeout (httpx.Timeout): Configured timeout for HTTP requests
    """

    def __init__(self, base_url: str = "http://localhost:3000"):
        """
        Initialize the MCP-Connect client.

        Args:
            base_url (str): Base URL of the MCP-Connect service. Defaults to localhost:3000.
                          Trailing slashes are automatically removed.
        """
        self.base_url = base_url.rstrip("/")
        self.bridge_endpoint = f"{self.base_url}/bridge"
        self.timeout = httpx.Timeout(config.MCP_CLIENT_TIMEOUT)  # Use configured timeout value

    def _get_actual_bridge_endpoint_url(self, bucket_no: int) -> str:
        """
        Get the actual bridge endpoint URL with bucket number substitution if needed.

        Args:
            bucket_no (int): The bucket number to be used in the URL

        Returns:
            str: The complete bridge endpoint URL with bucket number if placeholder exists
        """
        if MCP_CONNECT_BUCKET_PLACEHOLDER in self.bridge_endpoint:
            return self.bridge_endpoint.replace(MCP_CONNECT_BUCKET_PLACEHOLDER, str(bucket_no))
        else:
            return self.bridge_endpoint

    async def list_tools(
        self,
        server_config: MCPServerConfig,
        execution_context: MCPExecutionContext | None = None,
    ) -> list[MCPToolDefinition]:
        """
        List tools available in an MCP server with optional execution context.

        Args:
            server_config (MCPServerConfig): Configuration for the MCP server including
                                          command, arguments, and environment variables
            execution_context (Optional[MCPExecutionContext]): Optional execution context with
                                                              user, assistant, and workflow info

        Returns:
            list[MCPToolDefinition]: List of tool definitions available on the server

        Raises:
            httpx.HTTPError: If there's an issue with the HTTP request (e.g., connection error,
                           timeout, or non-200 status code)
            ValueError: If the response cannot be parsed or validated against the expected schema
            ValidationError: If the response data doesn't match the expected Pydantic model
        """
        server_path = server_config.command or server_config.url
        request = MCPToolInvocationRequest(
            method="tools/list",
            serverPath=server_path,
            args=server_config.args,
            params={},
            env=server_config.env,
            mcp_headers=server_config.headers,
            http_transport_type=server_config.type,
            single_usage=server_config.single_usage or False,
            # Inject execution context fields
            **(execution_context.to_request_fields() if execution_context else {}),
        )

        logger.debug(f"Listing tools from MCP server: {server_config.command}{' '.join(server_config.args)}")
        bucket_no = _get_bucket_no(server_config)
        headers = _get_headers(bucket_no, server_config)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            mcp_url = self._get_actual_bridge_endpoint_url(bucket_no)
            post_body = request.model_dump(exclude_none=True)
            try:
                # Make the actual request
                response = await client.post(mcp_url, json=post_body, headers=headers)
                response.raise_for_status()  # This will raise an HTTPStatusError for 4xx/5xx responses
                response_json = response.json()
                # Validate with Pydantic
                tools_response = MCPListToolsResponse.model_validate(response_json)
                logger.debug(f"Found {len(tools_response.tools)} tools")
                return tools_response.tools

            except httpx.HTTPStatusError as e:
                # Handle HTTP error status codes
                logger.error(f"HTTP error: {e.response.status_code} - {e.response.reason_phrase}")
                logger.error(f"Response content: {e.response.text[:1000]}")
                # Try to extract error message from JSON response
                try:
                    error_json = e.response.json()
                    error_message = error_json.get('error', e.response.text[:500])
                except Exception:
                    error_message = e.response.text[:500]
                raise ValueError(error_message) from e

            except json.JSONDecodeError as e:
                # Specific handling for JSON parsing failures
                logger.error(f"Failed to parse response as JSON: {e}")
                logger.error(f"Response text: {response.text[:1000]}")
                raise ValueError(f"Invalid JSON response from MCP-Connect: {e}")

            except httpx.ConnectError as e:
                # Specific handling for connection errors
                logger.error(f"Connection error when connecting to MCP server: {e}")
                # Get more details about the connection error
                details = str(e.__context__) if hasattr(e, '__context__') else 'No details'
                logger.error(f"Connection error details: {details}")
                raise

            except Exception as e:
                # General exception handling
                stacktrace = traceback.format_exc()
                logger.error(f"Unexpected error during MCP tools request: {stacktrace}")
                raise e

    async def invoke_tool(
        self,
        server_config: MCPServerConfig,
        tool_name: str,
        tool_args: dict[str, Any],
        execution_context: MCPExecutionContext | None = None,
    ) -> MCPToolInvocationResponse:
        """
        Invoke a tool on an MCP server with optional execution context.

        Args:
            server_config (MCPServerConfig): Configuration for the MCP server including
                                          command, arguments, and environment variables
            tool_name (str): Name of the tool to invoke
            tool_args (dict[str, Any]): Arguments to pass to the tool as key-value pairs
            execution_context (Optional[MCPExecutionContext]): Optional execution context with
                                                              user, assistant, and workflow info

        Returns:
            MCPToolInvocationResponse: Response from the tool invocation containing
                                     result data and error status

        Raises:
            httpx.HTTPError: If there's an issue with the HTTP request (e.g., connection error,
                           timeout, or non-200 status code)
            ValueError: If the response cannot be parsed or validated against the expected schema
            ValidationError: If the response data doesn't match the expected Pydantic model
        """
        server_path = server_config.command or server_config.url
        request = MCPToolInvocationRequest(
            method="tools/call",
            serverPath=server_path,
            args=server_config.args,
            params={"name": tool_name, "arguments": tool_args},
            env=server_config.env,
            mcp_headers=server_config.headers,
            http_transport_type=server_config.type,
            single_usage=server_config.single_usage or False,
            # Inject execution context fields
            **(execution_context.to_request_fields() if execution_context else {}),
        )

        logger.info(f"Invoking MCP tool: {tool_name} on server: {server_config.command} {' '.join(server_config.args)}")

        # Debug: log tool_args value types to detect if Pydantic model instances sneak in
        for _k, _v in tool_args.items():
            logger.debug(f"[invoke_tool] '{tool_name}' arg '{_k}': type={type(_v).__name__}, value={_v!r}")

        bucket_no = _get_bucket_no(server_config)
        headers = _get_headers(bucket_no, server_config)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            mcp_url = self._get_actual_bridge_endpoint_url(bucket_no)
            logger.info(f"tools/call: Using MCP-Connect URL: {mcp_url}")
            post_body = request.model_dump(exclude_none=True)
            # Debug: log the serialised arguments that will be sent over the wire
            _serialised_args = (post_body.get("params") or {}).get("arguments")
            logger.debug(f"[invoke_tool] '{tool_name}' post_body arguments after model_dump: {_serialised_args!r}")
            response = await client.post(mcp_url, json=post_body, headers=headers)
            response.raise_for_status()

            try:
                response_json = response.json()
                invocation_response = MCPToolInvocationResponse.model_validate(response_json)
                if invocation_response.isError:
                    logger.warning(f"Tool invocation resulted in error: {tool_name}: {str(invocation_response)}")
                return invocation_response
            except ValidationError as e:
                logger.error(f"Failed to parse tool invocation response: {e}")
                raise ValueError(f"Invalid response from MCP-Connect: {e}")


def _get_headers(bucket_no: int, server_config: MCPServerConfig) -> dict[str, str]:
    """
    Get headers for HTTP requests based on server configuration.

    Args:
        bucket_no (int): The bucket number to include in headers
        server_config (MCPServerConfig): Configuration for the MCP server including
                                      authentication details

    Returns:
        dict[str, str]: Dictionary of HTTP headers including Content-Type,
                       optional Authorization, and bucket number
    """
    headers = {"Content-Type": "application/json"}
    if server_config.auth_token:
        headers["Authorization"] = f"Bearer {server_config.auth_token}"
        logger.debug("Adding Authorization header with Bearer token")

    headers[X_MCP_CONNECT_BUCKET] = str(bucket_no)

    return headers


def _get_bucket_no(server_config: MCPServerConfig) -> int:
    """
    Calculate the bucket number for request routing based on server configuration.

    Args:
        server_config (MCPServerConfig): Configuration for the MCP server

    Returns:
        int: Calculated bucket number for request routing
    """
    bucket_key_value = server_config.env.get(BUCKET_KEY) if server_config.env else str(server_config)
    return _hash_remainder(bucket_key_value)


def _hash_remainder(s: str) -> int:
    """
    Calculate a consistent bucket number from a string using Python's hash function.

    Args:
        s (str): Input string to hash

    Returns:
        int: A number between 0 and (MCP_CONNECT_BUCKETS_COUNT - 1) derived from the hash
             of the input string
    """

    if not s:
        return 0
    # Compute the SHA-256 hash of the string encoded in UTF-8
    hash_obj = hashlib.md5(s.encode('utf-8'))
    # Convert the hexadecimal digest to an integer
    hash_int = int(hash_obj.hexdigest(), 16)
    # Return the remainder when the hash integer is divided by the bucket count
    return hash_int % config.MCP_CONNECT_BUCKETS_COUNT
