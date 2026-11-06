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
import yaml

from codemie.service.iac_export.document import ExportError
from codemie.service.iac_export.workflow_converter import (
    ENVELOPE_FIELDS,
    UNRESOLVED_ID_FIELDS,
    load,
    render,
    to_definition,
)

STORED = yaml.safe_dump(
    {
        "enable_summarization_node": False,
        "assistants": [
            {"id": "a1", "assistant_id": "asst-1", "model": "gpt-4"},
            {"id": "a2", "model": "gpt-4", "system_prompt": "inline"},
        ],
        "states": [{"id": "s1", "assistant_id": "a1", "next": {"state_id": "end"}}],
    },
    sort_keys=False,
)
HEADER = {"name": "Flow", "mode": "Sequential", "shared": True}


def _definition(stored: str = STORED, integrations=None, skills=None, datasources=None):
    return to_definition(load(stored), {"asst-1": "Helper"}, integrations or {}, HEADER, skills, datasources)


def test_assistants_become_a_mapping_keyed_by_id_with_the_catalog_assistant_named():
    definition = _definition()
    assert list(definition["assistants"]) == ["a1", "a2"]
    assert definition["assistants"]["a1"] == {"id": "a1", "assistant_id": "Helper", "model": "gpt-4"}
    assert definition["states"][0]["assistant_id"] == "a1"  # state refs are workflow-local ids
    assert definition["name"] == "Flow"


def test_keys_the_envelope_does_not_model_are_dropped():
    assert _definition()["enable_summarization_node"] is False  # an engine switch the envelope models is carried
    stored = yaml.safe_dump(
        {
            "assistants": [{"id": "a1", "system_prompt": "p", "skill_ids": ["s"], "datasource_ids": ["d"]}],
            "tools": [{"id": "t", "tool": "x", "tool_access": [{"name": "n", "integration_alias": "A", "extra": 1}]}],
            "states": [{"id": "s1", "assistant_id": "a1", "next": {"state_id": "end", "keep_history": True}}],
        }
    )
    definition = _definition(stored, skills={"s": "Skill S"}, datasources={"d": "Docs D"})
    # skills and datasources are exported as names (EPMCDME-16198 makes the engine resolve them on apply)
    assert definition["assistants"]["a1"] == {
        "id": "a1",
        "system_prompt": "p",
        "skill_ids": ["Skill S"],
        "datasource_ids": ["Docs D"],
    }
    assert definition["tools"] == [{"id": "t", "tool": "x", "tool_access": [{"name": "n", "integration_alias": "A"}]}]
    assert definition["states"][0]["next"] == {"state_id": "end"}


def test_nested_models_are_projected():
    stored = yaml.safe_dump(
        {
            "assistants": [{"id": "a1", "system_prompt": "p"}],
            "states": [
                {
                    "id": "s1",
                    "assistant_id": "a1",
                    "retry_policy": {"initial_interval": 1, "backoff_factor": 2, "max_interval": 3, "max_attempts": 4},
                    "next": {
                        "switch": {"cases": [{"condition": "x", "state_id": "e", "other": 1}], "default": "end"},
                    },
                }
            ],
        }
    )
    state = _definition(stored)["states"][0]
    assert state["next"]["switch"] == {"cases": [{"condition": "x", "state_id": "e"}], "default": "end"}
    assert state["retry_policy"]["max_attempts"] == 4


def test_mcp_server_alias_comes_from_the_snapshot_when_not_set():
    stored = yaml.safe_dump(
        {
            "assistants": [
                {
                    "id": "a1",
                    "system_prompt": "p",
                    "mcp_servers": [
                        {"name": "m", "command": "npx", "mcp_config_id": "cat-1", "settings": {"id": "i1"}},
                    ],
                }
            ]
        }
    )
    server = _definition(stored, {"i1": "Env"})["assistants"]["a1"]["mcp_servers"][0]
    assert server == {"name": "m", "command": "npx", "integration_alias": "Env"}


def test_catalog_only_enabled_mcp_server_cannot_be_exported():
    stored = yaml.safe_dump(
        {"assistants": [{"id": "a1", "system_prompt": "p", "mcp_servers": [{"name": "m", "mcp_config_id": "cat-1"}]}]}
    )
    with pytest.raises(ExportError, match="catalog link"):
        _definition(stored)


def test_sub_workflow_state_cannot_be_exported():
    stored = yaml.safe_dump({"states": [{"id": "s1", "workflow_id": "w1", "next": {"state_id": "end"}}]})
    with pytest.raises(ExportError, match="s1"):
        _definition(stored)


def test_assistant_without_identity_or_id_is_an_export_error():
    with pytest.raises(ExportError, match="a1"):
        _definition(yaml.safe_dump({"assistants": [{"id": "a1", "model": "m"}]}))
    with pytest.raises(ExportError, match="missing or duplicated"):
        _definition(yaml.safe_dump({"assistants": [{"system_prompt": "p"}]}))


def test_render_inverts_to_stored_text_for_the_modelled_subset():
    stored = yaml.safe_dump(
        {
            "assistants": [{"id": "a1", "assistant_id": "asst-1", "model": "gpt-4"}],
            "states": [{"id": "s1", "assistant_id": "a1", "next": {"state_id": "end"}}],
        },
        sort_keys=False,
    )
    assert render(_definition(stored), {"Helper": "asst-1"}) == stored


def test_empty_yaml_gives_an_empty_definition():
    assert _definition("") == {**HEADER, "assistants": {}, "tools": [], "custom_nodes": [], "states": []}


def test_field_tables_exclude_exactly_the_unresolved_id_fields():
    for model, skipped in UNRESOLVED_ID_FIELDS.items():
        assert skipped <= set(ENVELOPE_FIELDS[model])
