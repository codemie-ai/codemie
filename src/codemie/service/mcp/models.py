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
Model definitions for MCP (Model Context Protocol) integration.

This module contains Pydantic model definitions used for interacting with MCP servers
through MCP-Connect protocol. These models handle configuration, tool definitions,
invocation requests/responses, and content handling for MCP server communication.

All models in this module are based on Pydantic's BaseModel for automatic validation
and serialization/deserialization of data.
"""

from typing import Any

from pydantic import BaseModel, Field, model_validator


class MCPExecutionContext(BaseModel):
    """
    Execution context for MCP tool invocations.

    Contains contextual information about the current execution
    that should be passed to MCP servers but not stored in cached objects.

    This context is created at request time and injected into MCP tool
    invocations without being cached, ensuring proper context isolation
    between concurrent requests.
    """

    user_id: str | None = Field(None, description="Identifier for the user making the request")
    assistant_id: str | None = Field(None, description="Identifier for the assistant making the request")
    project_name: str | None = Field(None, description="The project name the request is associated with")
    workflow_execution_id: str | None = Field(None, description="Identifier for the workflow execution")
    request_headers: dict[str, str] | None = Field(
        None, description="Custom HTTP headers from the original request to propagate to MCP servers"
    )

    def to_request_fields(self) -> dict[str, Any]:
        """
        Convert context to fields for MCPToolInvocationRequest.

        Returns:
            Dictionary with context fields ready to be unpacked into
            MCPToolInvocationRequest constructor
        """
        return self.model_dump()


class MCPServerConfig(BaseModel):
    """
    Configuration for an MCP server.

    Defines how to start and connect to an MCP server instance, including
    command, arguments, environment variables, and authentication parameters.

    Attributes:
        command (str): The command used to invoke the MCP server
        args (Optional[list[str]]): List of arguments for the server command
        env (Optional[dict[str, Any]]): Environment variables for the server process
        auth_token (Optional[str]): Authentication token for MCP-Connect server
        single_usage (bool): Whether server is single-use (True) or persistent (False)
    """

    command: str | None = Field(
        None, description="The command used to invoke the MCP server (e.g., 'npx', 'uvx') using a stdio transport"
    )
    url: str | None = Field(
        None,
        description="The HTTP URL of a remote MCP server (use when connecting over HTTP/streamable-http).",
    )
    args: list[str] | None = Field(
        default_factory=list, description="List of arguments to pass to the MCP server command"
    )
    headers: dict[str, str] | None = Field(
        default_factory=dict,
        description="HTTP headers to include when connecting to an MCP server via `url`. "
        "Supports variable substitution using {{variable_name}} syntax, "
        "where variables are resolved from the environment variables (env field) "
        "or integration credentials.",
    )
    env: dict[str, Any] | None = Field(
        default_factory=dict, description="Environment variables to be set for the MCP server process"
    )
    type: str | None = Field(
        None,
        description="Transport type. Set to 'streamable-http' to use a streamable HTTP transport; "
        "leave null for stdio/sse command transports.",
    )
    auth_token: str | None = Field(None, description="Authentication token for the MCP-Connect server")
    single_usage: bool | None = Field(
        default=False, description="Whether server is single-use (True) or persistent/cached (False)"
    )
    tools: list[str] | None = Field(
        None,
        description="Optional list of tool names to use from this MCP server. "
        "If specified, only these tools will be available. "
        "If None or empty, all tools from the server will be used.",
    )
    audience: str | None = Field(
        None,
        description="OAuth2 audience for OIDC token exchange (RFC 8693). When set, the user's IdP token "
        "will be exchanged for a service-specific token scoped to this audience before being "
        "injected into {{user.token}} header placeholders.",
    )

    @model_validator(mode="after")
    def _ensure_command_xor_url(self):
        has_command = bool(self.command and str(self.command).strip())
        has_url = bool(self.url and str(self.url).strip())

        # XOR: exactly one must be True
        if has_command and has_url:
            raise ValueError("Exactly one of 'command' or 'url' must be provided (not both).")
        if not has_command and not has_url:
            raise ValueError("One of 'command' or 'url' must be provided.")

        return self

    class Config:
        """Pydantic model configuration with usage examples."""

        json_schema_extra = {
            "examples": [
                {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "<your_github_personal_access_token>"},
                    "auth_token": "<your_auth_token>",
                },
                {
                    "command": "uvx",
                    "args": ["cli-mcp-server"],
                    "env": {
                        "ALLOWED_DIR": "/home/user/work/codemie",
                        "ALLOWED_COMMANDS": "all",
                        "ALLOWED_FLAGS": "all",
                        "MAX_COMMAND_LENGTH": "2048",
                        "COMMAND_TIMEOUT": "300",
                        "TIMEOUT": "300",
                    },
                    "auth_token": "<your_auth_token>",
                },
                {
                    "type": "streamable-http",
                    "url": "http://127.0.0.1:3001/mcp",
                    "headers": {
                        "Content-Type": "application/json",
                        "x-destination-name": "<value>",
                        "X-API-KEY": "{{API_KEY}}",
                        "Authorization": "Bearer {{ACCESS_TOKEN}}",
                    },
                    "env": {"API_KEY": "your_api_key_here", "ACCESS_TOKEN": "your_access_token_here"},
                },
            ]
        }


class MCPToolDefinition(BaseModel):
    """
    Definition of a tool available in an MCP server.

    Contains complete metadata about a tool, including its identifier,
    description, and input parameter specifications.

    Attributes:
        name (str): Unique identifier of the tool
        description (str): Detailed description of tool's functionality
        inputSchema (MCPToolInputSchema): Complete input parameter specification
    """

    name: str = Field(description="Name of the tool")
    description: str | None = Field(None, description="Description of what the tool does")
    inputSchema: dict[str, Any] = Field(description="JSON Schema definition for tool inputs")


class MCPListToolsResponse(BaseModel):
    """
    Response from the MCP-Connect tools/list endpoint.

    Contains the complete list of tools available on an MCP server instance.
    Used for tool discovery and capability inspection.

    Attributes:
        tools (list[MCPToolDefinition]): List of available tool definitions
    """

    tools: list[MCPToolDefinition] = Field(description="List of available tools")


class MCPToolContentItem(BaseModel):
    """
    An item in the content array of an MCP tool invocation response.

    Represents various types of content that can be returned by a tool,
    including text, errors, images, and structured data.

    Attributes:
        type (str): Content type identifier (e.g., 'text', 'error', 'image')
        text (Optional[str]): Text content if applicable
        data (Optional[str]): Binary or structured data content
        image_url (Optional[dict[str, str]]): Image URL information
        mimeType (Optional[str]): Content MIME type for proper handling
    """

    type: str = Field(default="text", description="Type of content (e.g., 'text', 'error')")
    text: str | None = Field(None, description="The content text")
    data: str | None = Field(None, description="The content data")
    image_url: dict[str, str] | None = Field(None, description="The URL of the image content")
    mimeType: str | None = Field(None, description="The content MIME type")

    def __str__(self) -> str:
        """
        Convert the content item to its string representation.

        Handles different content types appropriately, including text,
        structured data, and images (both inline and URL-based).

        Returns:
            str: Human-readable representation of the content
        """
        match self.type:
            case "data":
                return self.model_dump_json()
            case "image":
                return f"![Screenshot](data:{self.mimeType};base64,{self.data})"
            case "image_url":
                return f"![Screenshot]({self.image_url.get("url", "<No image URL available>")})"
            case _:
                return self.text if self.text else "<No text content available>"

    def is_image(self) -> bool:
        """
        Check if the content item represents an image.

        Determines whether the content is an image based on its type.
        Supports both inline images and image URLs.

        Returns:
            bool: True if content is an image type, False otherwise
        """
        return self.type in ("image", "image_url")

    def is_text(self) -> bool:
        """
        Check if the content item represents a text.

        Determines whether the content is a text based on its type.

        Returns:
            bool: True if content is a text, False otherwise
        """
        return self.type in ("text", "txt", "error")


class MCPToolInvocationResponse(BaseModel):
    """
    Response from the MCP-Connect tools/call endpoint.

    Represents the complete result of a tool invocation, including all output
    content and error status information.

    Attributes:
        content (list[MCPToolContentItem]): Output content items from the tool
        isError (bool): Indicates if the invocation encountered an error
    """

    content: list[MCPToolContentItem] = Field(description="Array of content items produced by the tool")
    isError: bool = Field(default=False, description="Flag indicating if the invocation resulted in an error")


class MCPToolInvocationRequest(BaseModel):
    """
    Request to invoke an MCP tool via MCP-Connect.

    Contains all necessary information to execute a tool on an MCP server,
    including server configuration, tool parameters, and execution context.

    Attributes:
        method (str): The MCP method to call (typically 'tools/call')
        serverPath (str): Command to invoke the MCP server
        args (list[str]): Arguments for the server invocation
        params (dict[str, Any]): Tool-specific parameters
        env (dict[str, Any]): Environment variables for execution
        single_usage (bool): Whether to use single-usage mode (no caching)
    """

    method: str = Field(default="tools/call", description="The MCP method to call (typically 'tools/call')")
    serverPath: str = Field(description="The command to invoke the MCP server")
    args: list[str] = Field(description="Arguments to pass to the MCP server")
    params: dict[str, Any] = Field(description="Parameters for the tool invocation, including name and arguments")
    env: dict[str, Any] = Field(default_factory=dict, description="Environment variables to pass to the MCP server")
    mcp_headers: dict[str, str] = Field(default_factory=dict, description="Headers for API call")
    http_transport_type: str | None = Field(None, description="Transport type ('streamable-http' or null)")
    single_usage: bool | None = Field(None, description="Whether to use single-usage mode (no caching)")
    user_id: str | None = Field(None, description="Identifier for the user making the request")
    assistant_id: str | None = Field(None, description="Identifier for the assistant making the request")
    project_name: str | None = Field(None, description="The project name the request is associated with")
    workflow_execution_id: str | None = Field(None, description="Identifier for the workflow execution")
    request_headers: dict[str, str] | None = Field(
        None, description="Custom HTTP headers from the original request to propagate to MCP servers"
    )


class MCPToolLoadException(Exception):
    """
    Custom exception raised when MCP tools fail to load from a server.

    This exception provides more specific error handling for MCP tool loading failures,
    including server identification and original error context.
    """

    def __init__(self, server_name: str, original_error: Exception):
        """
        Initialize the MCP tool load error.

        Args:
            server_name: Name of the MCP server that failed to load tools
            original_error: The original exception that caused the failure
        """
        self.server_name = server_name
        self.original_error = original_error
        super().__init__(
            f"Failed to load MCP tools from {server_name}: {type(original_error).__name__}: {original_error}"
        )
