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

"""Guards that exporter output parses in the IaC document envelope, without importing it (see ``conformance.py``).

The per-kind and per-datasource-type exporter tests run ``assert_conforms`` on every document they build; this module
holds the checks around that: the committed goldens, the schema's own strictness, and the workflow field tables.
"""

import copy
import json
from pathlib import Path

import pytest

from codemie.service.iac_export.workflow_converter import ENVELOPE_FIELDS
from tests.codemie.service.iac_export.envelope.conformance import SCHEMA, assert_conforms, schema_errors

EXPORTERS_DIR = Path(__file__).parents[1] / "exporters"
GOLDENS = sorted(EXPORTERS_DIR.glob("*_golden.json"))

# model key in workflow_converter.ENVELOPE_FIELDS -> pydantic model name in the envelope's workflow schema
WORKFLOW_MODELS = {
    "assistant": "WorkflowAssistant",
    "assistant_tool": "WorkflowAssistantTool",
    "mcp_server": "WorkflowMCPServer",
    "mcp_config": "MCPServerConfig",
    "tool": "WorkflowTool",
    "custom_node": "CustomWorkflowNode",
    "state": "WorkflowState",
    "next": "WorkflowNextState",
    "condition": "WorkflowStateCondition",
    "switch": "WorkflowStateSwitch",
    "switch_case": "WorkflowStateSwitchCondition",
    "retry_policy": "WorkflowRetryPolicy",
}


def test_every_kind_has_a_committed_golden():
    assert {p.name.removesuffix("_golden.json") for p in GOLDENS} == {
        "assistant",
        "category",
        "integration",
        "skill",
        "workflow",
    }


@pytest.mark.parametrize("golden", GOLDENS, ids=lambda p: p.name)
def test_golden_documents_parse_in_the_envelope(golden):
    assert_conforms(json.loads(golden.read_text()))


@pytest.fixture
def category_document():
    return {"version": "v1", "meta": {"kind": "category", "name": "x"}, "spec": {"name": "x"}}


def test_schema_accepts_a_minimal_document(category_document):
    assert schema_errors(category_document) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["spec"].update(unknown_field=1),
        lambda d: d.pop("version"),
        lambda d: d.update(extra="x"),
        lambda d: d["meta"].update(kind="widget"),
        lambda d: d["spec"].pop("name"),
    ],
    ids=["unknown spec field", "no version", "unknown top-level key", "unknown kind", "missing required field"],
)
def test_schema_rejects_what_the_envelope_rejects(category_document, mutate):
    mutate(category_document)
    assert schema_errors(category_document)


def test_datasource_without_a_known_type_is_rejected():
    spec = {"name": "d", "project_name": "p", "description": ""}
    for bad in ({}, {"type": "knowledge_base_nothing"}):
        doc = {"version": "v1", "meta": {"kind": "datasource", "name": "d"}, "spec": {**spec, **bad}}
        assert schema_errors(doc)


def test_literal_secret_in_a_credential_slot_is_rejected():
    doc = {
        "version": "v1",
        "meta": {"kind": "integration", "name": "i"},
        "spec": {
            "project_name": "p",
            "alias": "i",
            "credential_type": "Jira",
            "credential_values": [{"key": "token", "ref": "sk-literal"}],
            "setting_type": "project",
        },
    }
    assert any("credential reference" in e for e in schema_errors(copy.deepcopy(doc)))


@pytest.mark.parametrize("model", sorted(WORKFLOW_MODELS))
def test_workflow_field_tables_match_the_envelope_schema(model):
    declared = set(SCHEMA["specs"]["workflow"]["$defs"][WORKFLOW_MODELS[model]]["properties"])
    assert declared == set(ENVELOPE_FIELDS[model]), f"{model}: workflow_converter.ENVELOPE_FIELDS drifted"
