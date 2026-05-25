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
from codemie.enterprise.mcp_auth.dependencies import (
    build_mcp_insufficient_scope_auth_exception,
    build_mcp_post_auth_401_result,
)
from codemie.service.security.token_providers.base_provider import BrokerAuthRequiredException
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
WWW_AUTHENTICATE_HEADER: Final[str] = "WWW-Authenticate"
AUTH_DISCOVERY_STATUS_CODES: Final[frozenset[int]] = frozenset({401, 403})


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

    def _build_request_payload(
        self,
        *,
        method: str,
        server_config: MCPServerConfig,
        execution_context: MCPExecutionContext | None,
        params: dict[str, Any],
    ) -> tuple[str, dict[str, Any], dict[str, str]]:
        """Build the POST URL, JSON body, and HTTP headers for an MCP-Connect request."""
        server_path = server_config.command or server_config.url
        request = MCPToolInvocationRequest(
            method=method,
            serverPath=server_path,
            args=server_config.args,
            params=params,
            env=_bridge_env(server_config.env),
            mcp_headers=_merge_mcp_headers(
                server_config.headers,
                execution_context.auth_headers if execution_context else None,
            ),
            http_transport_type=server_config.type,
            single_usage=server_config.single_usage or False,
            # Inject execution context fields
            **(execution_context.to_request_fields() if execution_context else {}),
        )
        bucket_no = _get_bucket_no(server_config)
        http_headers = _get_headers(bucket_no, server_config)
        mcp_url = self._get_actual_bridge_endpoint_url(bucket_no)
        post_body = request.model_dump(exclude_none=True)
        return mcp_url, post_body, http_headers

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
        mcp_url, post_body, http_headers = self._build_request_payload(
            method="tools/list",
            server_config=server_config,
            execution_context=execution_context,
            params={},
        )
        logger.debug(
            f"Listing tools from MCP server: {server_config.command or server_config.url} "
            f"{' '.join(server_config.args or [])}"
        )

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(mcp_url, json=post_body, headers=http_headers)
                response.raise_for_status()  # This will raise an HTTPStatusError for 4xx/5xx responses
                tools_response = MCPListToolsResponse.model_validate(response.json())
                logger.debug(f"Found {len(tools_response.tools)} tools")
                return tools_response.tools

            except httpx.HTTPStatusError as e:
                if _is_auth_challenge_response(e.response):
                    logger.warning(f"HTTP auth challenge status preserved: {e.response.status_code}")
                    raise
                logger.error(f"HTTP error: {e.response.status_code} - {e.response.reason_phrase}")
                logger.error(f"Response content: {e.response.text[:1000]}")
                if e.response.status_code == 401:
                    raise BrokerAuthRequiredException(
                        message="Authentication required. Please log in to access the MCP server.",
                        auth_location=config.BROKER_AUTH_LOCATION_URL,
                        details=f"HTTP {e.response.status_code}",
                    ) from e
                raise ValueError(_error_message_from_http_status_error(e)) from e

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse response as JSON: {e}")
                logger.error(f"Response text: {response.text[:1000]}")
                raise ValueError(f"Invalid JSON response from MCP-Connect: {e}")

            except httpx.ConnectError as e:
                logger.error(f"Connection error when connecting to MCP server: {e}")
                details = str(e.__context__) if hasattr(e, '__context__') else 'No details'
                logger.error(f"Connection error details: {details}")
                raise

            except Exception as e:
                logger.error(f"Unexpected error during MCP tools request: {traceback.format_exc()}")
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
        mcp_url, post_body, http_headers = self._build_request_payload(
            method="tools/call",
            server_config=server_config,
            execution_context=execution_context,
            params={"name": tool_name, "arguments": tool_args},
        )
        logger.info(f"Invoking MCP tool: {tool_name} on server: {server_config.command} {' '.join(server_config.args)}")
        logger.info(f"tools/call: Using MCP-Connect URL: {mcp_url}")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(mcp_url, json=post_body, headers=http_headers)
            if response.status_code == 401 and _is_authenticated_http_tool_call(server_config, execution_context):
                response = await self._maybe_retry_after_post_auth_401(
                    client=client,
                    response=response,
                    mcp_url=mcp_url,
                    post_body=post_body,
                    http_headers=http_headers,
                    server_config=server_config,
                    execution_context=execution_context,
                )
            if response.status_code == 403 and response.headers.get(WWW_AUTHENTICATE_HEADER) is not None:
                _raise_if_insufficient_scope(response, server_config, execution_context)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                if _is_auth_challenge_response(e.response):
                    raise
                logger.error(f"HTTP error: {e.response.status_code} - {e.response.reason_phrase}")
                if e.response.status_code == 401:
                    raise BrokerAuthRequiredException(
                        message="Authentication required. Please log in to access the MCP server.",
                        auth_location=config.BROKER_AUTH_LOCATION_URL,
                        details=f"HTTP {e.response.status_code}",
                    ) from e
                raise

            try:
                invocation_response = MCPToolInvocationResponse.model_validate(response.json())
                if invocation_response.isError:
                    logger.warning(f"Tool invocation resulted in error: {tool_name}: {str(invocation_response)}")
                return invocation_response
            except ValidationError as e:
                logger.error(f"Failed to parse tool invocation response: {e}")
                raise ValueError(f"Invalid response from MCP-Connect: {e}")

    async def _maybe_retry_after_post_auth_401(
        self,
        *,
        client: httpx.AsyncClient,
        response: httpx.Response,
        mcp_url: str,
        post_body: dict[str, Any],
        http_headers: dict[str, str],
        server_config: MCPServerConfig,
        execution_context: MCPExecutionContext | None,
    ) -> httpx.Response:
        """Raise if the 401 is non-recoverable; retry once with refreshed headers if a refresh is offered."""
        post_auth_result = build_mcp_post_auth_401_result(
            status_code=response.status_code,
            www_authenticate_header=response.headers.get(WWW_AUTHENTICATE_HEADER),
            server_config=server_config,
            execution_context=execution_context,
        )
        if post_auth_result is None:
            return response
        if post_auth_result.auth_exception is not None:
            raise post_auth_result.auth_exception
        if post_auth_result.retry_auth_headers is None:
            return response

        retry_post_body = dict(post_body)
        retry_mcp_headers = dict(post_body.get("mcp_headers") or {})
        retry_mcp_headers.update(post_auth_result.retry_auth_headers)
        retry_post_body["mcp_headers"] = retry_mcp_headers
        retry_response = await client.post(mcp_url, json=retry_post_body, headers=http_headers)
        if retry_response.status_code == 401:
            retry_result = build_mcp_post_auth_401_result(
                status_code=retry_response.status_code,
                www_authenticate_header=retry_response.headers.get(WWW_AUTHENTICATE_HEADER),
                server_config=server_config,
                execution_context=execution_context,
                refresh_allowed=False,
            )
            if retry_result is not None and retry_result.auth_exception is not None:
                raise retry_result.auth_exception
        return retry_response


def _merge_mcp_headers(
    server_headers: dict[str, str] | None,
    auth_headers: dict[str, str] | None,
) -> dict[str, str]:
    """Merge static server headers with request-scoped auth headers; auth values override on collision."""
    merged_headers = dict(server_headers or {})
    merged_headers.update(auth_headers or {})
    return merged_headers


def _is_authenticated_http_tool_call(
    server_config: MCPServerConfig,
    execution_context: MCPExecutionContext | None,
) -> bool:
    if not server_config.url:
        return False
    if (server_config.headers or {}).get("Authorization"):
        return True
    if server_config.auth_config is not None:
        return True
    if execution_context is None:
        return False
    if execution_context.oauth2_auth_config_id:
        return True
    return bool((execution_context.auth_headers or {}).get("Authorization"))


def _bridge_env(env: dict[str, Any] | None) -> dict[str, Any]:
    return {key: value for key, value in (env or {}).items() if key != BUCKET_KEY}


def _is_auth_challenge_response(response: httpx.Response) -> bool:
    return (
        response.status_code in AUTH_DISCOVERY_STATUS_CODES
        and response.headers.get(WWW_AUTHENTICATE_HEADER) is not None
    )


def _raise_if_insufficient_scope(
    response: httpx.Response,
    server_config: MCPServerConfig,
    execution_context: MCPExecutionContext | None,
) -> None:
    """Convert an `insufficient_scope` 403 into an auth-required exception when applicable."""
    auth_exception = build_mcp_insufficient_scope_auth_exception(
        status_code=response.status_code,
        www_authenticate_header=response.headers.get(WWW_AUTHENTICATE_HEADER),
        server_config=server_config,
        execution_context=execution_context,
    )
    if auth_exception is not None:
        raise auth_exception


def _error_message_from_http_status_error(error: httpx.HTTPStatusError) -> str:
    """Extract a human-readable error message from a non-auth HTTP error response."""
    try:
        return error.response.json().get('error', error.response.text[:500])
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        return error.response.text[:500]


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
    bucket_key_value = getattr(server_config, "bucket_key", None)
    if bucket_key_value is None and server_config.env:
        bucket_key_value = server_config.env.get(BUCKET_KEY)
    if bucket_key_value is None:
        bucket_key_value = _get_server_config_bucket_key(server_config)
    return _hash_remainder(str(bucket_key_value))


def _get_server_config_bucket_key(server_config: MCPServerConfig) -> str:
    return json.dumps(server_config.model_dump(mode="json"), sort_keys=True, default=str, separators=(",", ":"))


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
