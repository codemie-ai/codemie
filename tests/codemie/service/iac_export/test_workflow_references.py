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

import pytest

from codemie.service.iac_export.document import ExportError
from codemie.service.iac_export.references import ExportScope, NameEntry, NameResolver
from codemie.service.iac_export.workflow_converter import (
    extract_assistant_ids,
    extract_datasource_ids,
    extract_integration_aliases,
    extract_integration_ids,
    extract_skill_ids,
)
from codemie.service.iac_export.workflow_references import resolve_references

STORED = {
    "assistants": [
        {
            "id": "a1",
            "assistant_id": "asst-1",
            "skill_ids": ["s1"],
            "datasource_ids": ["d1"],
            "mcp_servers": [
                {
                    "name": "m",
                    "mcp_config_id": "c1",
                    "settings": {"id": "i1", "credential_values": [{"value": "SECRET-X"}]},
                },
                {"name": "aliased", "integration_alias": "Direct", "settings": {"id": "i-ignored"}},
            ],
        }
    ],
    "tools": [{"id": "t1", "mcp_server": {"name": "m2", "settings": {"id": "i2"}}}],
    "states": [{"id": "s1", "workflow_id": "w1", "next": {"state_id": "end"}}],
}


def _resolver(*pairs):
    entries = [NameEntry(i, n) for i, n in pairs]
    return NameResolver(lookup=lambda kind, scope: entries)


def test_only_catalog_assistants_and_unaliased_integration_snapshots_are_referenced():
    assert extract_assistant_ids(STORED) == ["asst-1"]
    assert extract_integration_ids(STORED) == ["i1", "i2"]
    assert extract_skill_ids(STORED) == ["s1"]
    assert extract_datasource_ids(STORED) == ["d1"]


def test_references_resolve_to_names():
    names, integrations, skills, datasources = resolve_references(
        _resolver(("asst-1", "Helper"), ("i1", "Env"), ("i2", "Other"), ("s1", "Triage"), ("d1", "Docs")),
        STORED,
        "demo",
    )
    assert names == {"asst-1": "Helper"}
    assert integrations == {"i1": "Env", "i2": "Other"}
    assert skills == {"s1": "Triage"}
    assert datasources == {"d1": "Docs"}


def test_unresolvable_reference_is_an_export_error():
    with pytest.raises(ExportError, match="asst-1"):
        resolve_references(_resolver(), STORED, "demo")


# MR 4545 review (CR 0bcb7377): a literal integration_alias (MCP server, tool, tool_access) bypasses id
# resolution entirely, so resolve_many's ambiguity check never saw it either.
def test_literal_integration_aliases_are_collected_from_servers_and_tool_access():
    stored = {
        "assistants": [
            {
                "id": "a1",
                "tools": [{"name": "t", "integration_alias": "Jira"}],
                "mcp_servers": [{"name": "m", "integration_alias": "Direct"}],
            }
        ],
        "tools": [
            {
                "id": "t1",
                "mcp_server": {"name": "m2", "integration_alias": "Direct"},
                "tool_access": [{"name": "x", "integration_alias": "Jira"}],
            }
        ],
    }
    assert extract_integration_aliases(stored) == ["Direct", "Jira"]


def test_ambiguous_literal_integration_alias_fails_the_export():
    stored = {
        "assistants": [{"id": "a1", "mcp_servers": [{"name": "m", "integration_alias": "Jira"}]}],
    }
    resolver = _resolver(("int-1", "Jira"), ("int-2", "Jira"))
    with pytest.raises(ExportError, match="Jira"):
        resolve_references(resolver, stored, "demo")


def test_unambiguous_literal_integration_alias_is_left_as_is():
    stored = {
        "assistants": [{"id": "a1", "mcp_servers": [{"name": "m", "integration_alias": "Jira"}]}],
    }
    resolver = _resolver(("int-1", "Jira"))
    names, integrations, skills, datasources = resolve_references(resolver, stored, "demo")
    assert names == {} and integrations == {} and skills == {} and datasources == {}


def test_ensure_alias_unique_is_a_noop_with_no_aliases():
    NameResolver(lookup=lambda kind, scope: []).ensure_alias_unique("integration", [], ExportScope(project="demo"))
