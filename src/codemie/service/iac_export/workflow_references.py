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

"""Stored-entity references inside a workflow, exported as names.

Stored ids that survive into the document become names: the catalog assistant a workflow assistant points at, the
skills and datasources attached to its assistants, and an MCP server's embedded integration snapshot (exported as the
integration alias). Ids that exist only inside the definition's own graph (assistant, state and tool ids) are left
alone. MCP catalog and sub-workflow ids are not exported at all (see the converter).
"""

from codemie.service.iac_export.references import ExportScope, NameResolver
from codemie.service.iac_export.workflow_converter import (
    extract_assistant_ids,
    extract_datasource_ids,
    extract_integration_aliases,
    extract_integration_ids,
    extract_skill_ids,
)


def resolve_references(
    resolver: NameResolver, stored: dict, project: str
) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
    """(assistant, integration, skill, datasource) id -> name for exactly the ids the workflow references."""
    scope = ExportScope(project=project)
    assistants = resolver.resolve_many("assistant", extract_assistant_ids(stored), scope)
    integrations = resolver.resolve_many("integration", extract_integration_ids(stored), scope)
    # A literal integration_alias (MCP server, tool, tool_access) bypasses id resolution, so it bypasses
    # resolve_many's ambiguity check too; check it separately against the same scope (CR 0bcb7377).
    resolver.ensure_alias_unique("integration", extract_integration_aliases(stored), scope)
    skills = resolver.resolve_many("skill", extract_skill_ids(stored), scope)
    datasources = resolver.resolve_many("datasource", extract_datasource_ids(stored), scope)
    return assistants, integrations, skills, datasources
