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

import unicodedata

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
        "use_custom_config",
        "tools",
        "tools_tokens_size_limit",
        "settings",
        "integration_alias",
        "resolve_dynamic_values_in_arguments",
    }
)


_MAX_LABELS = 5
_MARKUP_AND_QUOTES = str.maketrans(dict.fromkeys("<>'\"&`", " "))


def _clean_name(value: object) -> str:
    """A server name safe for a user-facing message: no markup, quotes or invisible characters; bounded; not empty."""
    text = str(value or "").translate(_MARKUP_AND_QUOTES)
    # Unicode categories C* (control, format such as bidi and zero-width, unassigned) and the line/paragraph
    # separators Zl/Zp become spaces: no hand-maintained code point ranges.
    text = "".join(
        " " if unicodedata.category(c)[0] == "C" or unicodedata.category(c) in ("Zl", "Zp") else c for c in text
    )
    return " ".join(text.split())[:60] or "unnamed"


def _labels(servers: list[MCPServerDetails]) -> str:
    """Server names for a user-facing message: cleaned, at most _MAX_LABELS of them, then 'and N more'."""
    names = ", ".join(f"'{_clean_name(s.name)}'" for s in servers[:_MAX_LABELS])
    extra = len(servers) - _MAX_LABELS
    return f"{names} and {extra} more" if extra > 0 else names


def _restricted_violations(servers: list[MCPServerDetails]) -> tuple[list[MCPServerDetails], list[MCPServerDetails]]:
    """(custom servers, catalogue servers that carry a custom configuration) among the given servers."""
    custom = [s for s in servers if not s.mcp_config_id]
    overridden = [s for s in servers if s.mcp_config_id and (s.use_custom_config or _has_inline_config(s))]
    return custom, overridden


def _has_inline_config(server: MCPServerDetails) -> bool:
    """True when a catalog-ref server carries connection-level fields that must come from the catalog."""
    return any(
        f not in _CATALOG_REF_ALLOWED_FIELDS and getattr(server, f, None) is not None
        for f in MCPServerDetails.model_fields
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
        custom, overridden = _restricted_violations(servers)
        if custom:
            many = len(custom) > 1
            subject = (
                f"MCP servers {_labels(custom)} are custom servers"
                if many
                else f"MCP server {_labels(custom)} is a custom server"
            )
            raise ValidationException(
                f"{subject}. Custom servers are turned off by your administrator. "
                f"Remove {'them' if many else 'it'}, then add {'servers' if many else 'a server'} from the catalogue."
            )

        if overridden:
            many = len(overridden) > 1
            subject = f"MCP servers {_labels(overridden)} have" if many else f"MCP server {_labels(overridden)} has"
            raise ValidationException(
                f"{subject} a custom configuration. Custom configuration is turned off by your administrator. "
                f"Remove {'them' if many else 'the server'}, "
                f"then add {'them' if many else 'it'} again from the catalogue."
            )

    @staticmethod
    def _validate_catalog_entries(
        catalog_refs: list[MCPServerDetails],
        catalog_map: dict[str, MCPConfig],
    ) -> None:
        unavailable: list[MCPServerDetails] = []
        for server in catalog_refs:
            config_id = server.mcp_config_id
            if not config_id:
                continue
            entry = catalog_map.get(config_id)
            if entry is None:
                reason = "not found"
            elif not entry.is_active:
                reason = "inactive"
            elif not entry.is_public:
                reason = "not public"
            else:
                continue
            # The exact reason stays in the log; users get one message, so they cannot probe which entries exist.
            logger.warning(f"MCP server {server.name!r} refused: catalog entry {config_id!r} is {reason}")
            unavailable.append(server)
        if unavailable:
            raise ValidationException(
                f"MCP server not available in the catalogue: {_labels(unavailable)}. "
                f"Choose another MCP server or ask your administrator."
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
            logger.warning(f"Duplicate MCP catalog entries refused: {duplicates}")
            raise ValidationException("The same MCP server from the catalogue was added more than once. Keep only one.")

        catalog_map = _build_catalog_map(catalog_refs)
        MCPAccessControlService._validate_catalog_entries(catalog_refs, catalog_map)

    @staticmethod
    def validate_on_restore(mcp_servers: list[MCPServerDetails] | None) -> None:
        """Validate servers being restored from stored data (e.g. a version rollback).

        Enforces restricted mode only; catalog availability is not re-checked, so open-mode restores are unchanged.
        """
        if mcp_servers and customer_config.is_component_enabled("mcpCustomServersDisabled"):
            custom, overridden = _restricted_violations(mcp_servers)
            blocked = custom + overridden
            if blocked:
                raise ValidationException(
                    f"Can't restore this version: it contains MCP servers that your administrator no longer allows "
                    f"({_labels(blocked)}). Custom servers and custom configuration are turned off. "
                    f"Choose a version without them."
                )

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
                logger.warning(f"MCP server {server.name!r} skipped: no mcp_config_id in restricted mode")
                continue
            entry = catalog_map.get(config_id)
            if entry is None or not entry.is_active or not entry.is_public:
                logger.warning(f"MCP server {server.name!r} skipped: catalog config {config_id!r} is unavailable")
                continue
            filtered.append(server)

        return filtered

    @staticmethod
    def resolve_catalog_config(mcp_server: MCPServerDetails) -> MCPServerDetails | None:
        """Resolve MCP config based on use_custom_config flag."""

        # If custom mode is enabled, use inline config
        if mcp_server.use_custom_config:
            if mcp_server.config is None:
                logger.warning(f"MCP server {mcp_server.name!r}: custom mode enabled but no config present")
                return None
            return mcp_server

        # Global mode: fetch from catalog
        config_id = mcp_server.mcp_config_id
        if not config_id:
            return mcp_server

        entry = MCPConfig.find_by_id(config_id)
        if entry is None or entry.config is None:
            logger.warning(f"MCP server {mcp_server.name!r}: catalog entry {config_id!r} unavailable at runtime")
            return None

        try:
            resolved_config = MCPServerConfig(**entry.config.model_dump())
            return mcp_server.model_copy(update={"config": resolved_config})
        except Exception as e:
            logger.warning(
                f"MCP server {mcp_server.name!r}: failed to resolve catalog config {config_id!r}: {e}",
                exc_info=True,
            )
            return None
