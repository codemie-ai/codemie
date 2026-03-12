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

from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum

from pydantic import BaseModel, Field, field_validator
from sqlmodel import Field as SQLField, Column, Index
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


class MCPVariableDefinition(BaseModel):
    """Definition of a required environment variable for MCP server"""

    name: str = Field(description="Variable name (e.g., GITHUB_TOKEN)")
    description: str = Field(description="Description of what this variable represents")
    required: bool = Field(default=True, description="Whether this variable is required")


class MCPServerConfigData(BaseModel):
    """
    MCP server configuration data.
    This represents the actual config block that will be used to start the MCP server.
    """

    command: Optional[str] = Field(None, description="Command to invoke MCP server (e.g., 'uvx', 'npx')")
    url: Optional[str] = Field(None, description="HTTP URL for remote MCP server")
    args: List[str] = Field(default_factory=list, description="Arguments for the command")
    headers: Dict[str, str] = Field(default_factory=dict, description="HTTP headers for URL-based servers")
    env: Dict[str, Any] = Field(default_factory=dict, description="Environment variables")
    type: Optional[str] = Field(None, description="Transport type (e.g., 'streamable-http')")
    auth_token: Optional[str] = Field(None, description="Authentication token")
    single_usage: bool = Field(default=False, description="Whether server is single-use or persistent")
    tools: Optional[List[str]] = Field(
        None,
        description="Optional list of tool names to use from this MCP server. "
        "If specified, only these tools will be available. "
        "If None or empty, all tools from the server will be used.",
    )
    audience: Optional[str] = Field(
        None,
        description="OAuth2 audience for OIDC token exchange (RFC 8693). When set, the user's IdP token "
        "will be exchanged for a service-specific token scoped to this audience.",
    )


class MCPConfig(BaseModelWithSQLSupport, table=True):
    """
    Database model for MCP server configuration catalog.

    Stores shareable MCP server configurations that can be used across multiple assistants.
    """

    __tablename__ = "mcp_configs"

    # Inherited from BaseModelWithSQLSupport:
    # - id: Optional[str] (primary key)
    # - date: Optional[datetime] (creation date)
    # - update_date: Optional[datetime] (last update)

    # Basic information
    name: str = SQLField(index=True, description=MCP_SERVER_NAME_DESCRIPTION)
    description: Optional[str] = SQLField(None, description="Description of what the MCP server does")

    # URLs for documentation
    server_home_url: Optional[str] = SQLField(None, description="Link to MCP server documentation")
    source_url: Optional[str] = SQLField(None, description="Link to source code repository")

    # Logo/Icon
    logo_url: Optional[str] = SQLField(None, description="URL to server logo/icon")

    # Categories
    categories: List[str] = SQLField(
        default_factory=list, sa_column=Column(JSONB), description="Categories for filtering (max 3)"
    )

    # Server configuration
    config: Optional[MCPServerConfigData] = SQLField(
        default=None,
        sa_column=Column('config', PydanticType(MCPServerConfigData)),
        description=MCP_SERVER_CONFIG_DESCRIPTION,
    )

    # Required environment variables
    required_env_vars: List[MCPVariableDefinition] = SQLField(
        default_factory=list,
        sa_column=Column('required_env_vars', PydanticListType(MCPVariableDefinition)),
        description="Required environment variables with descriptions",
    )

    # Ownership and sharing
    user_id: str = SQLField(index=True, description="User who created this config")
    is_public: bool = SQLField(default=False, description="Whether this config is public/shareable")
    is_system: bool = SQLField(default=False, description="Whether this is a system-provided config")

    # Metadata
    created_by: Optional[CreatedByUser] = SQLField(
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
    )


# API Request/Response Models


class MCPConfigCreateRequest(BaseModel):
    """Request model for creating a new MCP configuration"""

    name: str = Field(min_length=1, max_length=255, description=MCP_SERVER_NAME_DESCRIPTION)
    description: Optional[str] = Field(None, max_length=2000, description="Description of the MCP server")
    server_home_url: Optional[str] = Field(None, description="Link to MCP documentation")
    source_url: Optional[str] = Field(None, description="Link to source code")
    logo_url: Optional[str] = Field(None, description="URL to server logo")
    categories: List[str] = Field(default_factory=list, max_length=3, description="Categories (max 3)")
    config: MCPServerConfigData = Field(description=MCP_SERVER_CONFIG_DESCRIPTION)
    required_env_vars: List[MCPVariableDefinition] = Field(
        default_factory=list, description="Required environment variables"
    )
    is_public: bool = Field(default=False, description="Make this config public")

    @field_validator('categories')
    @classmethod
    def validate_categories(cls, v: List[str]) -> List[str]:
        """Validate categories"""
        if len(v) > 3:
            raise ValueError("Maximum 3 categories allowed")
        return v


class MCPConfigUpdateRequest(BaseModel):
    """Request model for updating an existing MCP configuration"""

    name: Optional[str] = Field(None, min_length=1, max_length=255, description=MCP_SERVER_NAME_DESCRIPTION)
    description: Optional[str] = Field(None, max_length=2000, description="Description of the MCP server")
    server_home_url: Optional[str] = Field(None, description="Link to MCP documentation")
    source_url: Optional[str] = Field(None, description="Link to source code")
    logo_url: Optional[str] = Field(None, description="URL to server logo")
    categories: Optional[List[str]] = Field(None, max_length=3, description="Categories (max 3)")
    config: Optional[MCPServerConfigData] = Field(None, description=MCP_SERVER_CONFIG_DESCRIPTION)
    required_env_vars: Optional[List[MCPVariableDefinition]] = Field(None, description="Required environment variables")
    is_public: Optional[bool] = Field(None, description="Make this config public")
    is_active: Optional[bool] = Field(None, description="Activate/deactivate this config")

    @field_validator('categories')
    @classmethod
    def validate_categories(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        """Validate categories"""
        if v is not None and len(v) > 3:
            raise ValueError("Maximum 3 categories allowed")
        return v


class MCPConfigResponse(BaseModel):
    """Response model for MCP configuration"""

    id: str
    name: str
    description: Optional[str]
    server_home_url: Optional[str]
    source_url: Optional[str]
    logo_url: Optional[str]
    categories: List[str]
    config: Optional[MCPServerConfigData]
    required_env_vars: List[MCPVariableDefinition]
    user_id: str
    is_public: bool
    is_system: bool
    created_by: Optional[CreatedByUser]
    usage_count: int
    is_active: bool
    date: Optional[datetime]
    update_date: Optional[datetime]


class MCPConfigListResponse(BaseModel):
    """Response model for list of MCP configurations"""

    total: int
    configs: List[MCPConfigResponse]
    page: int
    per_page: int
