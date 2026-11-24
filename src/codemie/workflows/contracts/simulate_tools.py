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

"""Tool parameter schema lookups of one availability pass (EPMCDME-16503, AC-38).

A lookup is a service call, not an abstract operation, so it has its own budget (`lookup_budget` distinct built-in
tools); beyond it a tool's schema stays unknown with that reason stated. The schemas come from T10's resolver
(`SaveTimeToolSchemaResolver`); with no resolver every tool binding that needs a schema stays UNVERIFIED.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from codemie.workflows.constants import PLUGIN_TOOL_PREFIX
from codemie.workflows.contracts.simulate_model import text_of

if TYPE_CHECKING:
    from codemie.core.workflow_models.workflow_models import WorkflowState, WorkflowTool
    from codemie.workflows.contracts.protocols import ToolSchemaResolver

logger = logging.getLogger(__name__)


class ToolLookups:
    """Memo and budget of the tool schema lookups of one pass. Never shared between passes."""

    def __init__(self, resolver: ToolSchemaResolver | None, lookup_budget: int) -> None:
        self.resolver = resolver
        self.lookup_budget = lookup_budget
        self._lookups: dict[str, frozenset[str] | None] = {}
        self.service_lookups: set[str] = set()
        self.budget_blocked: set[str] = set()

    def mandatory_parameters(
        self, state_id: str, state: WorkflowState, tool: WorkflowTool | None
    ) -> frozenset[str] | None:
        """The tool's mandatory parameter names, or `None` for "unknown" (AC-38).

        Asked only for a tool state, memoised per tool name, and bounded by the lookup budget, because a lookup is a
        service call and not an abstract operation. Only built-in tools spend the budget: a static MCP tool was already
        listed by the resource stage and a plugin tool has no schema, so neither costs a service call. The answer is
        normalised here, so T10 may return a set, a frozenset or its own UNKNOWN sentinel.
        """
        if self.resolver is None or tool is None or not text_of(getattr(state, "tool_id", None)):
            return None
        key = self._lookup_key(tool)
        if key in self._lookups:
            return self._lookups[key]
        service_call = needs_service_call(tool)
        if service_call and len(self.service_lookups) >= self.lookup_budget:
            self.budget_blocked.add(state_id)
            return None
        try:
            answer = self.resolver.mandatory_params(tool)
        except Exception:
            logger.warning("Contract linker tool schema lookup failed for %s", key, exc_info=True)
            answer = None
        value = normalise_parameters(answer)
        self._lookups[key] = value
        if service_call:
            self.service_lookups.add(key)
        return value

    @staticmethod
    def _lookup_key(tool: WorkflowTool) -> str:
        """Memo and budget key of a tool: its name, except an MCP tool, whose schema belongs to its own server entry.

        Two MCP tools of one name on different servers have different schemas, so they are keyed by `WorkflowTool.id`.
        """
        if getattr(tool, "mcp_server", None) is not None:
            return text_of(getattr(tool, "id", None)) or text_of(getattr(tool, "tool", None))
        return text_of(getattr(tool, "tool", None)) or text_of(getattr(tool, "id", None))


def needs_service_call(tool: WorkflowTool) -> bool:
    """Only a built-in tool is looked up through the discovery service; an MCP tool's schema was already listed by the
    resource stage and a plugin tool has no schema."""
    if getattr(tool, "mcp_server", None) is not None:
        return False
    name = text_of(getattr(tool, "tool", None)) or text_of(getattr(tool, "id", None))
    return not name.startswith(PLUGIN_TOOL_PREFIX)


def normalise_parameters(answer: object) -> frozenset[str] | None:
    """A resolver answer is a set of names or "unknown"; anything that is not a collection of names is unknown."""
    if isinstance(answer, frozenset | set | tuple | list):
        names = frozenset(item for item in answer if isinstance(item, str) and item)
        # Duplicates and stray entries are dropped; only an answer with no usable name at all is unknown.
        return names if names or not answer else None
    return None
