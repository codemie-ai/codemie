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

from codemie.configs import logger
from codemie.configs.customer_config import customer_config
from codemie.core.exceptions import ValidationException
from codemie.rest_api.models.assistant import MCPServerDetails
from codemie.rest_api.models.mcp_config import MCPConfig
from codemie.service.mcp.models import MCPServerConfig

# Fields allowed on a catalog-ref server (mcp_config_id set).
# Anything not in this set is connection-level config that must come from the catalog.
_CATALOG_REF_ALLOWED_FIELDS = frozenset(
    {
        "name",
        "description",
        "enabled",
        "mcp_config_id",
        "tools",
        "tools_tokens_size_limit",
        "settings",
        "integration_alias",
        "resolve_dynamic_values_in_arguments",
    }
)


def _build_catalog_map(mcp_servers: list[MCPServerDetails]) -> dict[str, MCPConfig]:
    catalog_ids = list({s.mcp_config_id for s in mcp_servers if s.mcp_config_id is not None})
    if not catalog_ids:
        return {}
    result: dict[str, MCPConfig] = {}
    for entry in MCPConfig.get_by_ids(catalog_ids):
        if entry.id:
            result[entry.id] = entry
    return result


class MCPAccessControlService:
    """Centralizes MCP server access control: save-time validation and runtime filtering."""

    @staticmethod
    def _validate_restricted_mode(servers: list[MCPServerDetails]) -> None:
        for server in servers:
            if not server.mcp_config_id:
                raise ValidationException(
                    f"Custom MCP servers are not allowed in restricted mode. "
                    f"Server '{server.name}' must reference a catalog entry via mcp_config_id."
                )
            for field in MCPServerDetails.model_fields:
                if field not in _CATALOG_REF_ALLOWED_FIELDS and getattr(server, field, None) is not None:
                    raise ValidationException(
                        f"Field '{field}' is not allowed when mcp_config_id is set "
                        f"in restricted mode (server '{server.name}')."
                    )

    @staticmethod
    def _validate_catalog_entries(
        catalog_refs: list[MCPServerDetails],
        catalog_map: dict[str, MCPConfig],
    ) -> None:
        for server in catalog_refs:
            config_id = server.mcp_config_id
            if not config_id:
                continue
            entry = catalog_map.get(config_id)
            if entry is None:
                raise ValidationException(
                    f"MCP configuration not found for server '{server.name}' (mcp_config_id='{config_id}')."
                )
            if not entry.is_active:
                raise ValidationException(
                    f"MCP configuration is not available for server '{server.name}' "
                    f"(mcp_config_id='{config_id}' is inactive)."
                )
            if not entry.is_public:
                raise ValidationException(
                    f"MCP configuration is not available for server '{server.name}' "
                    f"(mcp_config_id='{config_id}' is not public)."
                )

    @staticmethod
    def validate_on_save(mcp_servers: list[MCPServerDetails]) -> None:
        """Validate MCP servers before persisting. Raises ValidationException on violation."""
        if not mcp_servers:
            return

        if customer_config.is_component_enabled("mcpCustomServersDisabled"):
            MCPAccessControlService._validate_restricted_mode(mcp_servers)

        catalog_refs = [s for s in mcp_servers if s.mcp_config_id]
        if not catalog_refs:
            return

        all_config_ids = [s.mcp_config_id for s in catalog_refs]
        if len(all_config_ids) != len(set(all_config_ids)):
            duplicates = sorted({cid for cid in all_config_ids if all_config_ids.count(cid) > 1 and cid is not None})
            raise ValidationException(f"Duplicate mcp_config_id values are not allowed: {duplicates}.")

        catalog_map = _build_catalog_map(catalog_refs)
        MCPAccessControlService._validate_catalog_entries(catalog_refs, catalog_map)

    @staticmethod
    def _strip_one(server: MCPServerDetails) -> MCPServerDetails:
        return server

    @classmethod
    def strip_inline_config(cls, mcp_servers: list[MCPServerDetails]) -> list[MCPServerDetails]:
        """No-op preserved for callers; inline overrides are kept and win over the catalog at runtime."""
        return list(mcp_servers)

    @classmethod
    def sanitize_for_save(cls, mcp_servers: list[MCPServerDetails] | None) -> list[MCPServerDetails]:
        """Validate; inline overrides are preserved and win over the catalog at runtime."""
        servers = mcp_servers or []
        cls.validate_on_save(servers)
        return list(servers)

    @staticmethod
    def filter_for_runtime(mcp_servers: list[MCPServerDetails]) -> list[MCPServerDetails]:
        """In restricted mode, silently drop servers that are not allowed or have unavailable catalog entries.

        Returns the input unchanged in open mode.
        """
        if not customer_config.is_component_enabled("mcpCustomServersDisabled"):
            return mcp_servers

        catalog_map = _build_catalog_map(mcp_servers)

        filtered: list[MCPServerDetails] = []
        for server in mcp_servers:
            config_id = server.mcp_config_id
            if not config_id:
                logger.warning(f"MCP server '{server.name}' skipped: no mcp_config_id in restricted mode")
                continue
            entry = catalog_map.get(config_id)
            if entry is None or not entry.is_active or not entry.is_public:
                logger.warning(f"MCP server '{server.name}' skipped: catalog config {config_id} is unavailable")
                continue
            filtered.append(server)

        return filtered

    @staticmethod
    def resolve_catalog_config(mcp_server: MCPServerDetails) -> MCPServerDetails | None:
        """When mcp_config_id is set and no inline override exists, fetch the connection config from the catalog.

        Returns mcp_server unchanged if no mcp_config_id, or if the server already carries an
        inline `config` override (inline wins wholesale over the catalog).
        Returns None if the catalog entry is unavailable or cannot be resolved — the caller
        must skip the server in that case.
        """
        config_id = mcp_server.mcp_config_id
        if not config_id:
            return mcp_server
        if mcp_server.config is not None:
            return mcp_server

        entry = MCPConfig.find_by_id(config_id)
        if entry is None or entry.config is None:
            logger.warning(f"MCP server '{mcp_server.name}': catalog entry {config_id} unavailable at runtime")
            return None

        try:
            resolved_config = MCPServerConfig(**entry.config.model_dump())
            return mcp_server.model_copy(update={"config": resolved_config})
        except Exception as e:
            logger.warning(
                f"MCP server '{mcp_server.name}': failed to resolve catalog config {config_id}: {e}",
                exc_info=True,
            )
            return None
