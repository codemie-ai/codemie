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

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.workflow_models import WorkflowConfig
from codemie.rest_api.security.user import User
from codemie.service.iac_export.document import ExportError, to_canonical_bytes
from codemie.service.iac_export.exporters.workflow import WorkflowExporter
from codemie.service.iac_export.references import NameEntry, NameResolver
from tests.codemie.service.iac_export.envelope.conformance import assert_conforms
from codemie.service.iac_export.workflow_converter import render

GOLDEN = Path(__file__).parent / "workflow_golden.json"
MODULE = "codemie.service.iac_export.exporters.workflow"
SECRET = "mcp-env-secret-AAA"

YAML_CONFIG = yaml.safe_dump(
    {
        "enable_summarization_node": False,
        "assistants": [
            {"id": "a1", "assistant_id": "asst-id-1", "model": "gpt-4"},
            {
                "id": "a2",
                "model": "gpt-4",
                "system_prompt": "Ship it",
                "skill_ids": ["skill-id-1"],
                "mcp_servers": [
                    {
                        "name": "docs",
                        "config": {"command": "npx", "env": {"TOKEN": SECRET}, "auth_token": SECRET},
                    }
                ],
            },
        ],
        "states": [{"id": "s1", "assistant_id": "a1", "next": {"state_id": "end"}}],
    },
    sort_keys=False,
)
PLAIN_YAML = yaml.safe_dump(
    {"assistants": [{"id": "a1", "assistant_id": "asst-id-1"}], "states": [{"id": "s1", "assistant_id": "a1"}]},
    sort_keys=False,
)


def _workflow(yaml_config: str = YAML_CONFIG) -> WorkflowConfig:
    return WorkflowConfig(
        id="wf-uuid-1",
        name="Release Flow",
        description="Ships releases",
        project="demo",
        mode="Sequential",
        start_hint="Say go",
        yaml_config=yaml_config,
        supervisor_prompt="Supervise",
        created_by=None,
        categories=["cat-id-1"],
    )


def _resolver(entries=None) -> NameResolver:
    entries = entries or [NameEntry("asst-id-1", "Helper"), NameEntry("skill-id-1", "Release Skill")]
    return NameResolver(lookup=lambda kind, scope: entries)


def _export(workflow=None, resolver=None, can=True):
    with (
        patch(f"{MODULE}.workflow_service.get_workflow", return_value=workflow or _workflow()),
        patch(f"{MODULE}.Ability") as ability,
    ):
        ability.return_value.can.return_value = can
        return WorkflowExporter(resolver=resolver or _resolver()).export("wf-uuid-1", user=User(id="u", username="u"))


def test_export_matches_golden():
    body = to_canonical_bytes(_export())
    assert json.loads(body) == json.loads(GOLDEN.read_text())
    assert_conforms(json.loads(body))
    assert body == to_canonical_bytes(_export())


def test_no_ids_or_secrets_in_output():
    body = to_canonical_bytes(_export())
    assert SECRET.encode() not in body
    for forbidden in (b"wf-uuid-1", b"asst-id-1", b"cat-id-1", b"skill-id-1"):
        assert forbidden not in body


def test_render_of_export_equals_stored_yaml_config():
    document = _export(_workflow(PLAIN_YAML))
    definition = document.spec.model_dump(mode="json")["workflow"]
    assert render(definition, {"Helper": "asst-id-1"}) == PLAIN_YAML


def test_workflow_fields_the_envelope_models_are_exported_and_the_rest_are_not():
    spec = json.loads(to_canonical_bytes(_export()))["spec"]
    assert set(spec) == {"start_hint", "workflow", "credential_values"}
    assert spec["start_hint"] == "Say go"
    assert spec["workflow"]["enable_summarization_node"] is False
    assert "supervisor_prompt" not in spec and "meta_config" not in spec
    assert spec["workflow"]["assistants"]["a2"]["skill_ids"] == ["Release Skill"]  # a name, never the id


def test_credential_values_list_the_assistant_mcp_auth_tokens():
    spec = json.loads(to_canonical_bytes(_export()))["spec"]
    assert spec["credential_values"] == [{"key": "Release Flow_a2_docs", "ref": "env:RELEASE_FLOW_A2_DOCS_AUTH_TOKEN"}]


def test_clashing_assistant_name_fails_naming_it():
    clash = _resolver([NameEntry("asst-id-1", "Helper"), NameEntry("asst-id-9", "Helper")])
    two = yaml.safe_dump(
        {"assistants": [{"id": "a1", "assistant_id": "asst-id-1"}, {"id": "a2", "assistant_id": "asst-id-9"}]}
    )
    with pytest.raises(ExportError, match="Helper"):
        _export(_workflow(two), resolver=clash)


# CR-008: a name shared with ANY entity of the kind in scope is ambiguous, referenced or not.
def test_clashing_assistant_name_with_an_unreferenced_entity_fails_naming_it():
    unrelated = _resolver([NameEntry("asst-id-1", "Helper"), NameEntry("asst-id-9", "Helper")])
    with pytest.raises(ExportError, match="Helper"):
        _export(resolver=unrelated)


def test_export_denied_without_write():
    with pytest.raises(ExtendedHTTPException) as exc:
        _export(can=False)
    assert exc.value.code == 401  # raise_access_denied, same code as AssistantExporter.export


def test_missing_workflow_gets_404():
    with patch(f"{MODULE}.workflow_service.get_workflow", side_effect=KeyError("nf")):
        with pytest.raises(ExtendedHTTPException) as exc:
            WorkflowExporter(resolver=_resolver()).export("x", user=User(id="u", username="u"))
    assert exc.value.code == 404


def test_non_not_found_errors_are_not_masked_as_404():
    with patch(f"{MODULE}.workflow_service.get_workflow", side_effect=RuntimeError("db down")):
        with pytest.raises(RuntimeError):
            WorkflowExporter(resolver=_resolver()).export("x", user=User(id="u", username="u"))


def test_extended_http_exception_from_read_path_is_reraised():
    err = ExtendedHTTPException(code=403, message="denied")
    with patch(f"{MODULE}.workflow_service.get_workflow", side_effect=err):
        with pytest.raises(ExtendedHTTPException) as exc:
            WorkflowExporter(resolver=_resolver()).export("x", user=User(id="u", username="u"))
    assert exc.value.code == 403


def _mcp_yaml(server: dict) -> str:
    return yaml.safe_dump(
        {"assistants": [{"id": "a2", "model": "m", "system_prompt": "p", "mcp_servers": [server]}]}, sort_keys=False
    )


def test_unnamed_mcp_server_with_secrets_is_an_export_error():
    wf = _workflow(_mcp_yaml({"config": {"env": {"T": SECRET}}}))
    with pytest.raises(ExportError, match="name"):
        _export(wf)


def test_workflow_mcp_url_and_args_credentials_are_redacted():
    server = {
        "name": "docs",
        "config": {"url": "https://u:pw-LEAK@h/x", "args": ["--token", "tok-LEAK"], "command": "npx"},
        "mcp_connect_url": "https://mcp:pw-LEAK2@h/x",
    }
    body = to_canonical_bytes(_export(_workflow(_mcp_yaml(server))))
    assert b"pw-LEAK" not in body
    assert b"tok-LEAK" not in body
    assert_conforms(json.loads(body))


def test_explicit_null_that_overrides_an_envelope_default_is_preserved():
    wf = _workflow(
        yaml.safe_dump({"assistants": [{"id": "a1", "assistant_id": "asst-id-1", "limit_tool_output_tokens": None}]})
    )
    spec = json.loads(to_canonical_bytes(_export(wf)))["spec"]
    assert spec["workflow"]["assistants"]["a1"]["limit_tool_output_tokens"] is None


def test_tool_mcp_server_secrets_are_refused_not_referenced():
    # The engine's credential resolver walks only an assistant's mcp_servers, never a tool-level one, so a
    # reference written there is never substituted on apply; refuse instead (CR 2aca0289).
    stored = yaml.safe_dump(
        {"tools": [{"id": "t1", "tool": "x", "mcp_server": {"name": "docs", "config": {"auth_token": SECRET}}}]}
    )
    with pytest.raises(ExportError, match="t1") as exc:
        _export(_workflow(stored))
    assert SECRET not in exc.value.message


# MR 4545 review (auto_epmd-edp_vcs): an unrecognized env/header name with a value matching no token shape
# (DB_PASS: hunter2) passed the name/shape scan unchanged; env/headers are refused by position instead, same
# judgement an assistant's MCP config already applies to them (CR dfcf1994), just scoped to those two fields.
def test_tool_mcp_server_env_secret_with_an_unrecognized_name_is_still_refused():
    stored = yaml.safe_dump(
        {
            "tools": [
                {
                    "id": "t1",
                    "tool": "x",
                    "mcp_server": {"name": "docs", "config": {"command": "server", "env": {"DB_PASS": "hunter2"}}},
                }
            ]
        }
    )
    with pytest.raises(ExportError, match="t1") as exc:
        _export(_workflow(stored))
    assert "hunter2" not in exc.value.message


def test_tool_mcp_server_with_only_a_benign_command_still_exports():
    stored = yaml.safe_dump(
        {"tools": [{"id": "t1", "tool": "x", "mcp_server": {"name": "docs", "config": {"command": "npx"}}}]}
    )
    body = to_canonical_bytes(_export(_workflow(stored)))
    assert json.loads(body)["spec"]["workflow"]["tools"][0]["mcp_server"]["config"] == {"command": "npx"}


# MR 4545 review (auto_epmd-edp_vcs): a flag and its value as separate args list elements (["--password",
# "hunter2"]) evaded the per-element scan, which has no memory of the flag preceding each element.
def test_tool_mcp_server_arg_value_following_a_secret_flag_is_still_refused():
    stored = yaml.safe_dump(
        {
            "tools": [
                {
                    "id": "t1",
                    "tool": "x",
                    "mcp_server": {
                        "name": "docs",
                        "config": {"command": "npx", "args": ["server", "--password", "hunter2"]},
                    },
                }
            ]
        }
    )
    with pytest.raises(ExportError, match="t1") as exc:
        _export(_workflow(stored))
    assert "hunter2" not in exc.value.message


def test_tool_mcp_server_with_only_benign_args_still_exports():
    stored = yaml.safe_dump(
        {
            "tools": [
                {
                    "id": "t1",
                    "tool": "x",
                    "mcp_server": {"name": "docs", "config": {"command": "npx", "args": ["--max-tokens", "4096"]}},
                }
            ]
        }
    )
    body = to_canonical_bytes(_export(_workflow(stored)))
    config = json.loads(body)["spec"]["workflow"]["tools"][0]["mcp_server"]["config"]
    assert config["args"] == ["--max-tokens", "4096"]


# CR-005 (final review)
def _yaml_with(**parts) -> str:
    base = {
        "assistants": [{"id": "a1", "assistant_id": "asst-id-1"}],
        "states": [{"id": "s1", "assistant_id": "a1", "next": {"state_id": "end"}}],
    }
    base.update(parts)
    return yaml.safe_dump(base, sort_keys=False)


def _server_yaml(servers):
    return _yaml_with(assistants=[{"id": "a1", "model": "gpt-4", "system_prompt": "p", "mcp_servers": servers}])


def test_duplicate_mcp_server_names_in_one_assistant_are_refused():
    twin = {"name": "docs", "config": {"command": "npx", "auth_token": SECRET}}
    with pytest.raises(ExportError, match="docs"):
        _export(_workflow(_server_yaml([twin, {**twin, "config": {"auth_token": "other"}}])))


@pytest.mark.parametrize(
    "config",
    [
        {"headers": ["Authorization: Bearer LEAK-1"]},
        {"env": "TOKEN=LEAK-1"},
        {"url": ["https://u:LEAK-1@h/x"]},
        {"command": {"run": "LEAK-1"}},
        {"args": "--token LEAK-1"},
    ],
)
def test_unexpectedly_typed_mcp_config_fields_are_never_exported_raw(config):
    body = to_canonical_bytes(_export(_workflow(_server_yaml([{"name": "docs", "config": config}]))))
    assert b"LEAK-1" not in body


def test_tool_level_mcp_token_is_refused():
    tools = [{"id": "t1", "tool": "mcp", "mcp_server": {"name": "docs", "config": {"auth_token": SECRET}}}]
    with pytest.raises(ExportError, match="t1") as exc:
        _export(_workflow(_yaml_with(tools=tools)))
    assert SECRET not in exc.value.message


@pytest.mark.parametrize(
    "part,where",
    [
        ({"tools": [{"id": "t1", "tool": "http", "tool_args": {"headers": {"Authorization": "Bearer LEAK-2"}}}]}, "t1"),
        ({"custom_nodes": [{"id": "c1", "custom_node_id": "x", "config": {"api_key": "LEAK-2"}}]}, "c1"),
        (
            {
                "states": [
                    {"id": "s1", "assistant_id": "a1", "next": {"state_id": "end"}, "tool_args": {"password": "LEAK-2"}}
                ]
            },
            "s1",
        ),
    ],
)
def test_credentials_in_free_form_parts_are_refused_naming_the_location(part, where):
    with pytest.raises(ExportError, match=where) as exc:
        _export(_workflow(_yaml_with(**part)))
    assert "LEAK-2" not in exc.value.message


def test_non_secret_free_form_values_and_placeholders_are_exported_as_stored():
    tools = [{"id": "t1", "tool": "http", "tool_args": {"n": 3}}]
    states = [{"id": "s1", "assistant_id": "a1", "next": {"state_id": "end"}, "tool_args": {"q": "{{ var }}"}}]
    workflow = json.loads(to_canonical_bytes(_export(_workflow(_yaml_with(tools=tools, states=states)))))["spec"][
        "workflow"
    ]
    assert workflow["tools"][0]["tool_args"]["n"] == 3
    assert workflow["states"][0]["tool_args"]["q"] == "{{ var }}"


# MR 4545 review: reference keys of MCP owners must not coincide
def test_assistant_and_tool_sharing_an_id_with_mcp_servers_fail():
    from codemie.service.iac_export.exporters.workflow import _redact_secrets

    server = {"name": "docs", "config": {"auth_token": "t"}}
    definition = {
        "assistants": {"x": {"mcp_servers": [server]}},
        "tools": [{"id": "x", "mcp_server": server}],
        "states": [],
        "custom_nodes": [],
    }
    with pytest.raises(ExportError, match="'x'"):
        _redact_secrets(definition, "wf")


def test_server_with_non_object_config_does_not_break_credential_entries():
    from codemie.service.iac_export.exporters.workflow import _credential_entries

    definition = {
        "assistants": {"a": {"mcp_servers": [{"name": "docs", "config": "env:WF_A_DOCS_CONFIG"}]}},
        "tools": [{"id": "t", "mcp_server": {"name": "docs", "config": "env:WF_T_DOCS_CONFIG"}}],
    }
    assert _credential_entries(definition, "wf") == []
