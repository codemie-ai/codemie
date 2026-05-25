# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from codemie.service.mcp.models import MCPExecutionContext, MCPServerConfig


@runtime_checkable
class AuthResolverProtocol(Protocol):
    """Protocol for enterprise MCP auth resolvers.

    Enterprise implementations must satisfy this interface to be registered
    via MCPToolkitService.register_auth_resolver().
    """

    def can_handle(self, server_config: "MCPServerConfig") -> bool:
        """Return True if this resolver can handle auth for the given server config."""

    def resolve(
        self,
        server_config: "MCPServerConfig",
        user_id: str | None,
        execution_context: "MCPExecutionContext | None" = None,
    ) -> bool | None:
        """Resolve auth credentials by mutating target auth fields in place.

        Retrieves credentials via `user_id`, then mutates `server_config.env`
        or `execution_context.auth_headers` in place. Does not return a new
        execution context.
        Returning False explicitly declines handling after inspection, allowing
        later resolvers to run. Returning None preserves the historical handled
        behavior.

        Args:
            server_config: MCP server configuration; resolver may mutate
                `server_config.env`.
            user_id: The authenticated user's ID for credential lookup. None if
                unavailable.
            execution_context: Optional execution context; resolver may mutate
                `execution_context.auth_headers`.

        Raises:
            MCPAuthenticationRequiredException: When the user must authenticate
                interactively before the MCP server can be used.
        """
