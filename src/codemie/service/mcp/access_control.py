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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from codemie.configs import logger
from codemie.configs.customer_config import customer_config
from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException, ValidationException
from codemie.rest_api.models.assistant import MCPServerDetails
from codemie.rest_api.models.mcp_config import MCPConfig
from codemie.rest_api.models.mcp_restore import DisabledMcpServer
from codemie.service.mcp.models import MCPServerConfig

if TYPE_CHECKING:
    from codemie.core.workflow_models.workflow_config import WorkflowConfig

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
        "allow_user_context",
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


@dataclass(frozen=True)
class NodeAddress:
    """Where an MCP server sits in a workflow: an assistant's list entry or a tool's single server."""

    kind: Literal["assistant", "tool"]
    index: int
    server_index: int | None = None

    def render(self) -> str:
        if self.kind == "assistant":
            return f"assistants[{self.index}].mcp_servers[{self.server_index}]"
        return f"tools[{self.index}].mcp_server"


UnavailableReason = Literal["missing", "inactive", "not_public"]


_REASON_TEXT: dict[str, str] = {"missing": "not found", "inactive": "inactive", "not_public": "not public"}


def is_enabled(server: MCPServerDetails) -> bool:
    """True unless the server is explicitly disabled; a missing `enabled` counts as enabled."""
    return getattr(server, "enabled", True) is not False


def is_link(server: MCPServerDetails) -> bool:
    """An enabled entry that points at a catalogue entry and does not use its own configuration.

    Note: an enabled custom-config entry (use_custom_config) carrying an id is not a link here, while
    management.py:582-603 counts it as one. EPMCDME-16490 decides which reading wins.
    """
    return is_enabled(server) and bool(server.mcp_config_id) and not server.use_custom_config


def unavailable_reason(entry: MCPConfig | None) -> UnavailableReason | None:
    """Why a catalogue entry cannot be used, or None when it is available."""
    if entry is None:
        return "missing"
    if not entry.is_active:
        return "inactive"
    if not entry.is_public:
        return "not_public"
    return None


def _restricted_violations(servers: list[MCPServerDetails]) -> tuple[list[MCPServerDetails], list[MCPServerDetails]]:
    """(custom servers, catalogue servers that carry a custom configuration) among the enabled given servers."""
    servers = [s for s in servers if is_enabled(s)]
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


def after_workflow_links_changed(workflow: WorkflowConfig) -> None:
    """Seam called after a workflow was saved with MCP links that may differ from the stored ones. A no-op here.

    Called today by workflow rollback and by preconfigured workflows (always after the save, not only when a link
    was disabled). NOT called by workflow create, update or the workflow generator, and the MCP check
    (`validate_workflow_mcp`) is likewise made by each caller itself. EPMCDME-16640 fills the seam in and must
    cover those write paths too; it must not assume the seam already sees every workflow write.
    """


class CatalogLookupError(Exception):
    """The catalogue could not be read, or (strict restore lookup) returned nothing for the requested entries."""


def catalog_unavailable_error() -> ExtendedHTTPException:
    """503 for a rollback whose MCP catalogue read failed; nothing was changed and a retry may succeed."""
    return ExtendedHTTPException(
        code=status.HTTP_503_SERVICE_UNAVAILABLE,
        message="MCP catalogue is temporarily unavailable.",
        details="The restored configuration could not be checked against the MCP catalogue.",
        help="Try the rollback again in a moment.",
    )


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
            reason = unavailable_reason(catalog_map.get(config_id))
            if reason is None:
                continue
            # The exact reason stays in the log; users get one message, so they cannot probe which entries exist.
            logger.warning(f"MCP server {server.name!r} refused: catalog entry {config_id!r} is {_REASON_TEXT[reason]}")
            unavailable.append(server)
        if unavailable:
            raise ValidationException(
                f"MCP server not available in the catalogue: {_labels(unavailable)}. "
                f"Choose another MCP server or ask your administrator."
            )

    @staticmethod
    def check_duplicates(links: list[MCPServerDetails]) -> None:
        config_ids = [s.mcp_config_id for s in links]
        if len(config_ids) != len(set(config_ids)):
            duplicates = sorted({cid for cid in config_ids if config_ids.count(cid) > 1 and cid is not None})
            logger.warning(f"Duplicate MCP catalog entries refused: {duplicates}")
            raise ValidationException("The same MCP server from the catalogue was added more than once. Keep only one.")

    @staticmethod
    def check_restricted(mcp_servers: list[MCPServerDetails]) -> None:
        if customer_config.is_component_enabled("mcpCustomServersDisabled"):
            MCPAccessControlService._validate_restricted_mode(mcp_servers)

    @staticmethod
    def check_catalog(links: list[MCPServerDetails]) -> None:
        if links:
            MCPAccessControlService._validate_catalog_entries(links, _build_catalog_map(links))

    @staticmethod
    def validate_on_save(mcp_servers: list[MCPServerDetails]) -> None:
        """Validate MCP servers before persisting. Raises ValidationException on violation.

        Duplicates and catalogue availability are checked over links only, so a disabled entry always saves.
        """
        if not mcp_servers:
            return
        MCPAccessControlService.check_restricted(mcp_servers)
        links = [s for s in mcp_servers if is_link(s)]
        MCPAccessControlService.check_duplicates(links)
        MCPAccessControlService.check_catalog(links)

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
    def _load_restore_catalog(servers: list[MCPServerDetails], strict_lookup: bool) -> dict[str, MCPConfig]:
        """Catalogue entries for the links in `servers`.

        A failed read always raises CatalogLookupError; with strict_lookup an empty result raises it too.
        """
        links = [s for s in servers if is_link(s) and s.mcp_config_id]
        if not links:
            return {}
        try:
            catalog = _build_catalog_map(links)
        except Exception as e:
            raise CatalogLookupError(f"MCP catalogue lookup failed: {e}") from e
        if strict_lookup and not catalog:
            raise CatalogLookupError("MCP catalogue returned no entries for the linked servers")
        return catalog

    @staticmethod
    def restore_mcp_servers(
        servers: list[MCPServerDetails],
        addresses: list[str] | None = None,
        strict_lookup: bool = False,
    ) -> tuple[list[MCPServerDetails], list[DisabledMcpServer]]:
        """Copy servers for a restore, disabling links whose catalogue entry is unavailable.

        Returns (new_servers, report). Inputs are never mutated; non-links are returned as-is.
        `addresses` is an optional parallel list giving each report item's address.
        CatalogLookupError is raised when the catalogue read fails. With strict_lookup it is also raised when the
        lookup returns no rows for a non-empty id set; a partial miss still disables the missing links.
        """
        catalog = MCPAccessControlService._load_restore_catalog(servers, strict_lookup)

        new_servers: list[MCPServerDetails] = []
        report: list[DisabledMcpServer] = []
        for idx, server in enumerate(servers):
            reason = unavailable_reason(catalog.get(server.mcp_config_id)) if is_link(server) else None
            if reason is None:
                new_servers.append(server)
                continue
            new_servers.append(server.model_copy(update={"enabled": False}))
            report.append(
                DisabledMcpServer(
                    name=server.name,
                    mcp_config_id=server.mcp_config_id,
                    reason=reason,
                    address=addresses[idx] if addresses is not None and idx < len(addresses) else None,
                )
            )
        return new_servers, report

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
    def collect_workflow_mcp_nodes(workflow: WorkflowConfig) -> list[tuple[NodeAddress, MCPServerDetails]]:
        """Collect every MCP server entry from a workflow's assistants and tools, with its address."""
        nodes: list[tuple[NodeAddress, MCPServerDetails]] = []
        for i, assistant in enumerate(workflow.assistants or []):
            for j, server in enumerate(assistant.mcp_servers or []):
                nodes.append((NodeAddress("assistant", i, j), server))
        for k, tool in enumerate(workflow.tools or []):
            if tool.mcp_server:
                nodes.append((NodeAddress("tool", k), tool.mcp_server))
        return nodes

    @classmethod
    def collect_workflow_mcp_servers(cls, workflow: WorkflowConfig) -> list[MCPServerDetails]:
        """Flat view of collect_workflow_mcp_nodes."""
        return [server for _, server in cls.collect_workflow_mcp_nodes(workflow)]

    @classmethod
    def validate_workflow_mcp(cls, workflow: WorkflowConfig) -> None:
        """Validate a workflow's MCP servers: duplicates per node, restricted mode and catalogue over all nodes.

        The same catalogue entry may be used by several nodes; only a repeat inside one node is refused.
        """
        nodes = cls.collect_workflow_mcp_nodes(workflow)
        if not nodes:
            return
        servers = [server for _, server in nodes]
        cls.check_restricted(servers)
        per_node: dict[tuple[str, int], list[MCPServerDetails]] = {}
        for address, server in nodes:
            if is_link(server):
                per_node.setdefault((address.kind, address.index), []).append(server)
        for links in per_node.values():
            cls.check_duplicates(links)
        cls.check_catalog([s for s in servers if is_link(s)])

    @classmethod
    def validate_and_strip_workflow_mcp_servers(cls, workflow: WorkflowConfig) -> None:
        """Validate a workflow's MCP servers before persisting, then strip inline overrides in-place."""
        cls.validate_workflow_mcp(workflow)
        cls.strip_workflow_mcp_servers(workflow)

    @classmethod
    def strip_workflow_mcp_servers(cls, workflow: WorkflowConfig) -> None:
        """Strip inline config fields from a workflow's catalog-ref MCP servers in-place."""
        for assistant in workflow.assistants or []:
            if assistant.mcp_servers:
                assistant.mcp_servers = cls.strip_inline_config(assistant.mcp_servers)
        for tool in workflow.tools or []:
            if tool.mcp_server:
                tool.mcp_server = cls._strip_one(tool.mcp_server)

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
            if unavailable_reason(catalog_map.get(config_id)) is not None:
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
