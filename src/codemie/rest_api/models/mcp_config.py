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
Database and API models for MCP Server Configuration Catalog.

This module provides models for managing a shareable pool of MCP server configurations
that can be selected and used across assistants.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from enum import Enum

from pydantic import BaseModel, Field, field_validator
from sqlmodel import Field as SQLField, Column, Index, Session, select, text as sqltext
from sqlalchemy.dialects.postgresql import JSONB

from codemie.rest_api.models.base import BaseModelWithSQLSupport, PydanticType, PydanticListType
from codemie.core.models import CreatedByUser


class MCPCategory(str, Enum):
    """Categories for MCP servers"""

    DEVELOPMENT = "Development"
    AI = "AI"
    API = "API"
    DATABASE = "Database"
    CLOUD = "Cloud"
    FILESYSTEM = "Filesystem"
    GIT = "Git"
    MEMORY = "Memory"
    AUTOMATION = "Automation"
    SEARCH = "Search"
    OTHER = "Other"


# Constants for field descriptions
MCP_SERVER_NAME_DESCRIPTION = "Name of the MCP server"
MCP_SERVER_CONFIG_DESCRIPTION = "MCP server configuration"

_ALLOWED_MCP_COMMANDS: frozenset[str] = frozenset(
    {
        # Package runners (user-configured)
        "npx",
        "uvx",
        # Pre-installed MCP server binaries in the mcp-connect-service container
        "mcp-server-filesystem",
        "mcp-server-memory",
        "mcp-server-sequential-thinking",
        "mcp-server-postgres",
        "mcp-server-puppeteer",
        "mcp-mermaid",
    }
)

_ALLOWED_MCP_PATHS: frozenset[str] = frozenset(
    {
        "/codemie/additional-tools/github-mcp-server/github-mcp-server",
    }
)


class MCPVariableDefinition(BaseModel):
    """Definition of a required environment variable for MCP server"""

    name: str = Field(description="Variable name (e.g., GITHUB_TOKEN)")
    description: str = Field(description="Description of what this variable represents")
    required: bool = Field(default=True, description="Whether this variable is required")


class ConfigWarning(BaseModel):
    """Warning attached to an MCP config read response when configuration is present but inactive."""

    code: str
    message: str
    action: str


class MCPServerConfigData(BaseModel):
    """
    MCP server configuration data.
    This represents the actual config block that will be used to start the MCP server.
    """

    command: str | None = Field(None, description="Command to invoke MCP server (e.g., 'uvx', 'npx')")

    @field_validator("command")
    @classmethod
    def validate_command(cls, v: str | None) -> str | None:
        if v is None:
            return v
        normalized = v.strip()
        if not normalized:
            # Empty/whitespace — no command present, not our concern to validate.
            return v
        if normalized in _ALLOWED_MCP_PATHS:
            return v
        binary = os.path.basename(normalized)
        if binary not in _ALLOWED_MCP_COMMANDS:
            raise ValueError(
                f"MCP server command '{binary}' is not allowed. Permitted: {sorted(_ALLOWED_MCP_COMMANDS)}"
            )
        if normalized != binary:
            raise ValueError(
                f"MCP server command '{normalized}' must be a plain binary name, not a path. "
                f"Permitted: {sorted(_ALLOWED_MCP_COMMANDS)}"
            )
        return v

    url: str | None = Field(None, description="HTTP URL for remote MCP server")
    args: list[str] = Field(default_factory=list, description="Arguments for the command")
    headers: dict[str, str] = Field(default_factory=dict, description="HTTP headers for URL-based servers")
    env: dict[str, Any] = Field(default_factory=dict, description="Environment variables")
    type: str | None = Field(None, description="Transport type (e.g., 'streamable-http')")
    auth_token: str | None = Field(None, description="Authentication token")
    single_usage: bool = Field(default=False, description="Whether server is single-use or persistent")
    tools: list[str] | None = Field(
        None,
        description="Optional list of tool names to use from this MCP server. "
        "If specified, only these tools will be available. "
        "If None or empty, all tools from the server will be used.",
    )
    audience: str | None = Field(
        None,
        description="OAuth2 audience for OIDC token exchange (RFC 8693). When set, the user's IdP token "
        "will be exchanged for a service-specific token scoped to this audience.",
    )
    auth_config: dict[str, Any] | None = Field(
        None,
        description="Authentication configuration for this MCP server. Stored as raw dict; "
        "typed models (OAuth2AuthConfig / SAMLAuthConfig) live in enterprise only.",
    )
    allow_issuer_prefix_match: bool = Field(
        default=False,
        description="When True, accepts an Authorization Server whose metadata returns a base issuer URL "
        "that is a URL prefix of the tenant-specific discovery URL (e.g. Atlassian Rovo). "
        "Only takes effect when auth_config is absent and OAuth2 auto-discovery is used.",
    )


class MCPConfig(BaseModelWithSQLSupport, table=True):
    """
    Database model for MCP server configuration catalog.

    Stores shareable MCP server configurations that can be used across multiple assistants.
    """

    __tablename__ = "mcp_configs"

    # Inherited from BaseModelWithSQLSupport:
    # - id: str | None (primary key)
    # - date: Optional[datetime] (creation date)
    # - update_date: Optional[datetime] (last update)

    # Basic information
    name: str = SQLField(index=True, description=MCP_SERVER_NAME_DESCRIPTION)
    description: str | None = SQLField(None, description="Description of what the MCP server does")

    # URLs for documentation
    server_home_url: str | None = SQLField(None, description="Link to MCP server documentation")
    source_url: str | None = SQLField(None, description="Link to source code repository")

    # Logo/Icon
    logo_url: str | None = SQLField(None, description="URL to server logo/icon")

    # Categories
    categories: list[str] = SQLField(
        default_factory=list, sa_column=Column(JSONB), description="Categories for filtering (max 3)"
    )

    # Server configuration
    config: MCPServerConfigData | None = SQLField(
        default=None,
        sa_column=Column('config', PydanticType(MCPServerConfigData)),
        description=MCP_SERVER_CONFIG_DESCRIPTION,
    )

    # Required environment variables
    required_env_vars: list[MCPVariableDefinition] = SQLField(
        default_factory=list,
        sa_column=Column('required_env_vars', PydanticListType(MCPVariableDefinition)),
        description="Required environment variables with descriptions",
    )

    # Ownership and sharing
    user_id: str = SQLField(index=True, description="User who created this config")
    is_public: bool = SQLField(default=False, description="Whether this config is public/shareable")
    is_system: bool = SQLField(default=False, description="Whether this is a system-provided config")

    # Metadata
    created_by: CreatedByUser | None = SQLField(
        default=None,
        sa_column=Column('created_by', PydanticType(CreatedByUser)),
        description="Information about who created this config",
    )

    # Usage tracking
    usage_count: int = SQLField(default=0, description="Number of times this config has been used")

    # Status
    is_active: bool = SQLField(default=True, description="Whether this config is active")

    # Indexes for common queries
    __table_args__ = (
        Index('ix_mcp_configs_name_user', 'name', 'user_id'),
        Index('ix_mcp_configs_is_public', 'is_public'),
        Index(
            'ix_mcp_configs_auth_config_id',
            sqltext("((config->'auth_config'->>'id'))"),
            unique=True,
            postgresql_using='btree',
            postgresql_where=sqltext("(config->'auth_config'->>'id') IS NOT NULL"),
        ),
    )

    @classmethod
    def get_by_auth_config_id(cls, auth_config_id: str) -> MCPConfig | None:
        """Reverse lookup from auth_config.id to owning MCPConfig.

        Uses the partial unique index ``ix_mcp_configs_auth_config_id`` for
        efficient lookup.  The ``IS NOT NULL`` predicate allows the query planner
        to use the index automatically.
        """
        with Session(cls.get_engine()) as session:
            statement = select(cls).where(
                cls.config["auth_config"]["id"].astext == auth_config_id,  # type: ignore
            )
            return session.exec(statement).first()


# API Request/Response Models


class MCPConfigCreateRequest(BaseModel):
    """Request model for creating a new MCP configuration"""

    name: str = Field(min_length=1, max_length=255, description=MCP_SERVER_NAME_DESCRIPTION)
    description: str | None = Field(None, max_length=2000, description="Description of the MCP server")
    server_home_url: str | None = Field(None, description="Link to MCP documentation")
    source_url: str | None = Field(None, description="Link to source code")
    logo_url: str | None = Field(None, description="URL to server logo")
    categories: list[str] = Field(default_factory=list, max_length=3, description="Categories (max 3)")
    config: MCPServerConfigData = Field(description=MCP_SERVER_CONFIG_DESCRIPTION)
    required_env_vars: list[MCPVariableDefinition] = Field(
        default_factory=list, description="Required environment variables"
    )
    is_public: bool = Field(default=False, description="Make this config public")

    @field_validator('categories')
    @classmethod
    def validate_categories(cls, v: list[str]) -> list[str]:
        """Validate categories"""
        if len(v) > 3:
            raise ValueError("Maximum 3 categories allowed")
        return v


class MCPConfigUpdateRequest(BaseModel):
    """Request model for updating an existing MCP configuration"""

    name: str | None = Field(None, min_length=1, max_length=255, description=MCP_SERVER_NAME_DESCRIPTION)
    description: str | None = Field(None, max_length=2000, description="Description of the MCP server")
    server_home_url: str | None = Field(None, description="Link to MCP documentation")
    source_url: str | None = Field(None, description="Link to source code")
    logo_url: str | None = Field(None, description="URL to server logo")
    categories: list[str] | None = Field(None, max_length=3, description="Categories (max 3)")
    config: MCPServerConfigData | None = Field(None, description=MCP_SERVER_CONFIG_DESCRIPTION)
    required_env_vars: list[MCPVariableDefinition] | None = Field(None, description="Required environment variables")
    is_public: bool | None = Field(None, description="Make this config public")
    is_active: bool | None = Field(None, description="Activate/deactivate this config")

    @field_validator('categories')
    @classmethod
    def validate_categories(cls, v: list[str] | None) -> list[str] | None:
        """Validate categories"""
        if v is not None and len(v) > 3:
            raise ValueError("Maximum 3 categories allowed")
        return v


class MCPConfigResponse(BaseModel):
    """Response model for MCP configuration"""

    id: str
    name: str
    description: str | None
    server_home_url: str | None
    source_url: str | None
    logo_url: str | None
    categories: list[str]
    config: MCPServerConfigData | None
    required_env_vars: list[MCPVariableDefinition]
    user_id: str
    is_public: bool
    is_system: bool
    created_by: CreatedByUser | None
    usage_count: int
    is_active: bool
    warnings: list[ConfigWarning] = Field(default_factory=list)
    date: datetime | None
    update_date: datetime | None


class MCPConfigListResponse(BaseModel):
    """Response model for list of MCP configurations"""

    total: int
    configs: list[MCPConfigResponse]
    page: int
    per_page: int
