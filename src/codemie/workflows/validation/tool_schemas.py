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

"""Tool parameter schemas at save time (EPMCDME-16503, T10, AC-13 / AC-14 / AC-15 / AC-38).

`SaveTimeToolSchemaResolver` is the only place that reads a service for the contract linker: `contracts/` receives
from it a set of mandatory parameter names or `UNKNOWN`, never a schema class. One instance serves one save and is
never kept; every memo is an instance attribute.

Where the names come from:

- Static MCP tools: the resource stage already lists the tools of the server and selects the one the state uses; it
  hands that tool's args schema to `record_mcp_args_schema` (the `schema_sink` of the stage), keyed by the owning
  `WorkflowTool.id`. No second MCP call is made here.
- Built-in tools: `ToolDiscoveryService.find_tool_by_name` says whether the tool exists and which parameters it has. Its
  `required` flags are NOT used: they are the credential-config rule ("a default is not optional"), which would call a
  parameter with a default mandatory. The mandatory names are read from the `args_schema` model of the tool classes
  registered under the name (`tool_registry`), taking the names that are mandatory in every class that carries the name.
- Everything else is `UNKNOWN`: dynamic MCP servers, plugin tools (`_` prefix), provider tools (empty schema), an empty
  or missing schema, a tool the discovery cannot place on the registered classes, and any exception from a lookup, which
  degrades that one tool only.

A declared `input_schema` is never a source of mandatory parameters. Before `enable_lookup()` every answer is
`UNKNOWN`: the resolver is created before the resource stage as a recorder and enabled only once the stage returned
without raising, because the stage is what rejects the tools the user cannot use.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from codemie.configs.logger import logger
from codemie.service.tools.discovery import ToolDiscoveryService
from codemie.workflows.constants import PLUGIN_TOOL_PREFIX
from codemie.workflows.contracts.protocols import UNKNOWN, _Unknown


class SaveTimeToolSchemaResolver:
    """Per-save implementation of `contracts.protocols.ToolSchemaResolver`; stores name sets only."""

    def __init__(self) -> None:
        self._lookup_enabled = False
        self._mcp: dict[str, frozenset[str] | None] = {}
        self._built_in: dict[str, frozenset[str] | None] = {}

    def enable_lookup(self) -> None:
        """Start answering. Called once the resource stage returned without raising."""
        self._lookup_enabled = True

    def record_mcp_args_schema(self, tool_id: str, args_schema: Any) -> None:
        """The `schema_sink` of the resource stage: keep the mandatory names of one static MCP tool, or `None`."""
        self._mcp[str(tool_id)] = _mandatory_names_of(args_schema)

    def mandatory_params(self, tool_ref: Any) -> frozenset[str] | _Unknown:
        """The mandatory parameter names of the tool, or `UNKNOWN`. Never raises."""
        if not self._lookup_enabled:
            return UNKNOWN
        try:
            names = self._resolve(tool_ref)
        except Exception:
            logger.warning("Contract linker could not resolve a tool parameter schema", exc_info=True)
            return UNKNOWN
        return UNKNOWN if names is None else names

    def _resolve(self, tool_ref: Any) -> frozenset[str] | None:
        mcp_server = getattr(tool_ref, "mcp_server", None)
        if mcp_server is not None:
            if getattr(mcp_server, "resolve_dynamic_values_in_arguments", False):
                return None
            return self._mcp.get(str(getattr(tool_ref, "id", "")))
        name = tool_ref if isinstance(tool_ref, str) else getattr(tool_ref, "tool", None)
        if not isinstance(name, str) or not name.strip() or name.startswith(PLUGIN_TOOL_PREFIX):
            return None
        if name not in self._built_in:
            self._built_in[name] = self._look_up_built_in(name)
        return self._built_in[name]

    @staticmethod
    def _look_up_built_in(name: str) -> frozenset[str] | None:
        try:
            info = ToolDiscoveryService.find_tool_by_name(name)
            discovered = set(getattr(info, "args_schema", None) or {})
            if not discovered:
                return None
            models = _registered_args_models(name)
            if not models or any(not discovered <= set(getattr(model, "model_fields", {})) for model in models):
                return None
            return frozenset.intersection(*(_required_fields(model) for model in models))
        except Exception:
            logger.warning("Contract linker tool schema lookup failed for tool %s", name, exc_info=True)
            return None


def record_tool_schema(schema_sink: Callable[[str, Any], None] | None, tool: Any, mcp_tool: Any) -> None:
    """Hand the args schema of the MCP tool the check selected to the optional sink; a sink never changes the answer."""
    if schema_sink is None or mcp_tool is None:
        return
    try:
        schema_sink(tool.id, getattr(mcp_tool, "args_schema", None))
    except Exception:
        logger.warning("Tool schema sink failed for tool %s", getattr(tool, "id", None), exc_info=True)


def _required_fields(model: Any) -> frozenset[str]:
    return frozenset(param for param, field in model.model_fields.items() if field.is_required())


def _mandatory_names_of(args_schema: Any) -> frozenset[str] | None:
    """Mandatory names of an args schema model; an empty or unreadable model means "no schema" (AC-38)."""
    fields = getattr(args_schema, "model_fields", None)
    if not isinstance(fields, dict) or not fields:
        return None
    try:
        return _required_fields(args_schema)
    except Exception:
        logger.warning("Contract linker could not read an MCP tool args schema", exc_info=True)
        return None


def _registered_args_models(name: str) -> list[Any]:
    """The `args_schema` model of every tool class registered under `name`; empty when any class has none."""
    from codemie_tools.base import tool_registry

    tool_registry.ensure_loaded()
    record = tool_registry.lookup(name)
    if record is None:
        return []
    models = []
    for tool_class in record.classes:
        field = tool_class.model_fields.get("args_schema")
        model = getattr(field, "default", None)
        if not hasattr(model, "model_fields"):
            return []
        models.append(model)
    return models
