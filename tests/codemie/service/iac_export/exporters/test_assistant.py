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
from codemie_tools.base.models import CredentialTypes

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.assistant import (
    Assistant,
    Context,
    ContextType,
    MCPServerDetails,
    PromptVariable,
    ToolDetails,
    ToolKitDetails,
)
from codemie.rest_api.models.settings import CredentialValues, SettingsBase
from codemie.rest_api.security.user import User
from codemie.service.iac_export.document import ExportError, to_canonical_bytes
from codemie.service.mcp.models import MCPServerConfig
from codemie.service.iac_export.exporters.assistant import AssistantExporter
from codemie.service.iac_export.references import NameEntry, NameResolver
from tests.codemie.service.iac_export.envelope.conformance import assert_conforms

GOLDEN = Path(__file__).parent / "assistant_golden.json"
MODULE = "codemie.service.iac_export.exporters.assistant"
SECRETS = ("tool-secret-AAA", "mcp-env-secret-BBB", "mcp-token-secret-CCC", "var-secret-DDD", "header-secret-EEE")


def _setting(id_: str, secret: str) -> SettingsBase:
    return SettingsBase(
        id=id_,
        project_name="demo",
        alias=None,
        credential_type=CredentialTypes.JIRA,
        credential_values=[CredentialValues(key="token", value=secret)],
    )


def _assistant() -> Assistant:
    return Assistant(
        id="assistant-uuid-1",
        name="Helper",
        slug="helper",
        description="Helps",
        system_prompt="Be {{tone}} with {{api_key}}",
        project="demo",
        context=[Context(context_type=ContextType.CODE, name="repo")],
        llm_model_type="gpt-4",
        skill_ids=["skill-id-b", "skill-id-a"],
        assistant_ids=["asst-id-2"],
        categories=["cat-id-1"],
        is_react=True,
        toolkits=[
            ToolKitDetails(
                toolkit="vcs",
                tools=[
                    ToolDetails(name="zeta"),
                    ToolDetails(name="alpha", settings=_setting("int-id-1", SECRETS[0])),
                ],
                settings=_setting("int-id-1", SECRETS[0]),
            )
        ],
        mcp_servers=[
            MCPServerDetails(
                name="docs",
                mcp_config_id="catalog-id-9",
                settings=_setting("int-id-2", SECRETS[1]),
                mcp_connect_url="https://mcp.example",
                mcp_connect_auth_token=_setting("int-id-3", SECRETS[2]),
            )
        ],
        prompt_variables=[
            PromptVariable(key="tone", default_value="friendly"),
            PromptVariable(key="api_key", default_value=SECRETS[3], is_sensitive=True),
        ],
        created_by=None,
        creator="creator-id",
    )


def _resolver() -> NameResolver:
    names = {
        "category": [NameEntry("cat-id-1", "Support")],
        "integration": [
            NameEntry("int-id-1", "Jira"),
            NameEntry("int-id-2", "Docs Env"),
            NameEntry("int-id-3", "Docs Token"),
        ],
        "assistant": [NameEntry("asst-id-2", "Sub Helper")],
        "skill": [NameEntry("skill-id-a", "Skill A"), NameEntry("skill-id-b", "Skill B")],
    }
    return NameResolver(lookup=lambda kind, scope: names[kind])


def _export(assistant=None):
    with (
        patch(f"{MODULE}.get_assistant_by_id_or_raise", return_value=assistant or _assistant()),
        patch(f"{MODULE}.check_user_can_access_assistant"),
    ):
        return AssistantExporter(resolver=_resolver()).export("assistant-uuid-1", user=User(id="u", username="u"))


def test_export_matches_golden():
    body = to_canonical_bytes(_export())
    assert json.loads(body) == json.loads(GOLDEN.read_text())
    assert_conforms(json.loads(body))
    assert body == to_canonical_bytes(_export())


def test_no_ids_or_secrets_in_output():
    body = to_canonical_bytes(_export())
    for secret in SECRETS:
        assert secret.encode() not in body
    for forbidden in (
        b"assistant-uuid-1",
        b"skill-id-",
        b"int-id-",
        b"catalog-id-9",
        b"asst-id-2",
        b"cat-id-1",
        b"creator-id",
    ):
        assert forbidden not in body


def test_access_denied_propagates():
    err = ExtendedHTTPException(code=403, message="denied")
    with (
        patch(f"{MODULE}.get_assistant_by_id_or_raise", return_value=_assistant()),
        patch(f"{MODULE}.check_user_can_access_assistant", side_effect=err),
    ):
        with pytest.raises(ExtendedHTTPException) as exc:
            AssistantExporter(resolver=_resolver()).export("x", user=User(id="u", username="u"))
    assert exc.value.code == 403


def test_missing_assistant_gets_404():
    with patch(f"{MODULE}.get_assistant_by_id_or_raise", side_effect=ExtendedHTTPException(code=404, message="nf")):
        with pytest.raises(ExtendedHTTPException) as exc:
            AssistantExporter(resolver=_resolver()).export("x", user=User(id="u", username="u"))
    assert exc.value.code == 404


def test_unresolvable_reference_raises_export_error():
    resolver = NameResolver(lookup=lambda kind, scope: [])
    with (
        patch(f"{MODULE}.get_assistant_by_id_or_raise", return_value=_assistant()),
        patch(f"{MODULE}.check_user_can_access_assistant"),
    ):
        with pytest.raises(ExportError):
            AssistantExporter(resolver=resolver).export("x", user=User(id="u", username="u"))


def test_meta_name_is_the_name_other_documents_reference():
    # The envelope links documents of a bundle by `meta.name`, and every reference to an assistant (a workflow's
    # `assistant_id`, another assistant's `assistant_ids`) is the assistant's name, so `meta.name` must be the name
    # (the slug is a separate spec field).
    doc = json.loads(to_canonical_bytes(_export()))
    assert doc["meta"] == {"kind": "assistant", "name": "Helper"}
    assert doc["spec"]["name"] == "Helper"
    assert doc["spec"]["slug"] == "helper"


def test_tool_permissions_are_exported():
    from codemie.rest_api.models.assistant import ToolPermissionsConfig

    assistant = _assistant()
    assistant.tool_permissions = ToolPermissionsConfig(allow_override=False)

    doc = json.loads(to_canonical_bytes(_export(assistant)))
    assert doc["spec"]["tool_permissions"] == {"tool_call_policy": "auto_approve", "allow_override": False}
    assert_conforms(doc)


def test_fields_the_envelope_cannot_carry_are_not_exported():
    assistant = _assistant()
    assistant.custom_metadata = {"team": "core"}
    assistant.plan_prompt = "Plan"

    spec = json.loads(to_canonical_bytes(_export(assistant)))["spec"]

    for absent in ("custom_metadata", "plan_prompt", "skills"):
        assert absent not in spec


def test_fields_the_envelope_now_carries_are_exported():
    from codemie.rest_api.models.hedging import HedgingConfig, HedgingToolDetails

    assistant = _assistant()
    assistant.interactive_enabled = True
    assistant.enable_image_generation = True
    assistant.file_attachment_enabled = False
    assistant.hedging_config = HedgingConfig(tool=HedgingToolDetails(name="fast"), timeout_ms=300)

    document = json.loads(to_canonical_bytes(_export(assistant)))
    spec = document["spec"]

    assert spec["skill_ids"] == ["Skill A", "Skill B"]  # names, sorted
    assert spec["interactive_enabled"] is True and spec["enable_image_generation"] is True
    assert spec["file_attachment_enabled"] is False
    assert spec["hedging_config"]["tool"] == {"name": "fast"} and spec["hedging_config"]["timeout_ms"] == 300
    assert_conforms(document)


def test_toolkit_and_tool_auto_credentials_lookup_and_mcp_oauth_fields_are_exported():
    assistant = _assistant()
    for toolkit in assistant.toolkits:
        toolkit.auto_credentials_lookup = False
        for tool in toolkit.tools:
            tool.auto_credentials_lookup = False
    assistant.mcp_servers[0].config = MCPServerConfig(
        command="npx", audience="api://x", auth_config={"type": "oauth2", "scopes": ["read"]}
    )
    document = json.loads(to_canonical_bytes(_export(assistant)))
    spec = document["spec"]
    assert all(t["auto_credentials_lookup"] is False for t in spec["toolkits"])
    assert all(x["auto_credentials_lookup"] is False for t in spec["toolkits"] for x in t["tools"])
    config = spec["mcp_servers"][0]["config"]
    assert config["audience"] == "api://x" and config["auth_config"] == {"type": "oauth2", "scopes": ["read"]}
    assert_conforms(document)


def test_mcp_auth_config_holding_a_credential_is_refused():
    assistant = _assistant()
    assistant.mcp_servers[0].config = MCPServerConfig(command="npx", auth_config={"client_secret": "LEAK-AUTH"})
    with pytest.raises(ExportError, match="auth_config") as exc:
        _export(assistant)
    assert "LEAK-AUTH" not in exc.value.message


def test_sensitive_prompt_variable_default_is_exported_empty():
    # The envelope never resolves a reference inside a prompt variable (no sensitive flag, no credential slot
    # there), so an env: reference would be literal, unresolvable text applied straight into the stored default.
    # Jira decision (amended): export an empty default instead (review auto_epmd-edp_vcs / Jira EPMCDME-15313).
    spec = json.loads(to_canonical_bytes(_export()))["spec"]
    variables = {v["key"]: v for v in spec["prompt_variables"]}
    assert variables["api_key"]["default_value"] == ""
    assert variables["tone"]["default_value"] == "friendly"
    assert "is_sensitive" not in variables["api_key"]


def test_mcp_config_is_nested_and_every_value_in_it_is_a_reference_by_position():
    # command / url / args / env / headers are slots the envelope's credential resolver walks on apply, so every
    # literal value there becomes a reference regardless of its name or shape (review decision, CR dfcf1994).
    from codemie.service.mcp.models import MCPServerConfig

    assistant = _assistant()
    assistant.mcp_servers = [
        MCPServerDetails(
            name="docs",
            use_custom_config=True,
            config=MCPServerConfig(
                command="uvx",
                args=["--fast"],
                single_usage=True,
                tools=["search"],
                audience="aud",
                allow_issuer_prefix_match=True,
                env={"TOKEN": "mcp-env-secret-BBB"},
                headers={"X-Key": "header-secret-EEE"},
                auth_token="mcp-token-secret-CCC",
            ),
        )
    ]

    doc = json.loads(to_canonical_bytes(_export(assistant)))
    config = doc["spec"]["mcp_servers"][0]["config"]

    assert config["command"] == "env:HELPER_DOCS_CONFIG_COMMAND"
    assert config["args"] == ["env:HELPER_DOCS_CONFIG_ARGS_0"]
    assert config["single_usage"] is True
    assert config["tools"] == ["search"]
    assert config["env"] == {"TOKEN": "env:HELPER_DOCS_ENV_TOKEN"}
    assert config["headers"] == {"X-Key": "env:HELPER_DOCS_HEADER_X_KEY"}
    assert config["auth_token"] == "env:HELPER_DOCS_AUTH_TOKEN"
    assert config["audience"] == "aud" and "allow_issuer_prefix_match" not in config
    assert_conforms(doc)


def test_mcp_config_pure_template_values_stay_literal():
    # A positional slot still exempts a value that is nothing but a template: the operator fills it in some other
    # way (e.g. `{{user.token}}` substitution), and the envelope has no way to represent a reference to it anyway.
    from codemie.service.mcp.models import MCPServerConfig

    assistant = _assistant()
    assistant.mcp_servers = [
        MCPServerDetails(
            name="docs",
            use_custom_config=True,
            config=MCPServerConfig(command="npx", headers={"Authorization": "Bearer {{user.token}}"}),
        )
    ]

    doc = json.loads(to_canonical_bytes(_export(assistant)))
    config = doc["spec"]["mcp_servers"][0]["config"]

    assert config["command"] == "env:HELPER_DOCS_CONFIG_COMMAND"
    assert config["headers"] == {"Authorization": "Bearer {{user.token}}"}
    assert_conforms(doc)


def test_integration_references_are_aliases_in_settings():
    spec = json.loads(to_canonical_bytes(_export()))["spec"]
    toolkit = spec["toolkits"][0]
    assert toolkit["settings"] == "Jira"
    assert {t["name"]: t.get("settings") for t in toolkit["tools"]} == {"alpha": "Jira", "zeta": None}
    server = spec["mcp_servers"][0]
    assert (server["settings"], server["mcp_connect_auth_token"]) == ("Docs Env", "Docs Token")
    assert "mcp_config_id" not in server  # a platform id the envelope would keep verbatim


def test_mcp_url_userinfo_and_command_args_secrets_are_redacted():
    assistant = _assistant()
    server = assistant.mcp_servers[0]
    server.command = "npx"
    server.arguments = "--api-key LEAK-ARG"
    server.mcp_connect_url = "https://user:LEAK-PW@mcp.example"
    body = to_canonical_bytes(_export(assistant))
    assert b"LEAK-ARG" not in body
    assert b"LEAK-PW" not in body


def test_duplicate_or_empty_mcp_server_names_are_export_errors():
    assistant = _assistant()
    twin = assistant.mcp_servers[0].model_copy(deep=True)
    assistant.mcp_servers.append(twin)
    with pytest.raises(ExportError, match="docs"):
        _export(assistant)
    assistant = _assistant()
    assistant.mcp_servers[0].name = ""
    with pytest.raises(ExportError, match="name"):
        _export(assistant)


def test_assistant_without_a_model_type_is_an_export_error():
    assistant = _assistant()
    assistant.llm_model_type = None
    with pytest.raises(ExportError, match="model"):
        _export(assistant)


# CR-008 / CR-009 (final review)
def test_sub_assistant_name_shared_with_an_unreferenced_assistant_fails_naming_it():
    names = {
        "category": [NameEntry("cat-id-1", "Support")],
        "integration": [NameEntry("int-id-1", "Jira"), NameEntry("int-id-2", "Docs Env"), NameEntry("int-id-3", "T")],
        "assistant": [NameEntry("asst-id-2", "Sub Helper"), NameEntry("asst-id-77", "Sub Helper")],
    }
    with (
        patch(f"{MODULE}.get_assistant_by_id_or_raise", return_value=_assistant()),
        patch(f"{MODULE}.check_user_can_access_assistant"),
    ):
        with pytest.raises(ExportError, match="Sub Helper"):
            AssistantExporter(resolver=NameResolver(lookup=lambda kind, scope: names[kind])).export(
                "x", user=User(id="u", username="u")
            )


def test_default_resolver_checks_read_access_of_every_reference():
    seen = []
    names = {
        "category": [NameEntry("cat-id-1", "Support")],
        "integration": [NameEntry("int-id-1", "Jira"), NameEntry("int-id-2", "D"), NameEntry("int-id-3", "T")],
        "assistant": [NameEntry("asst-id-2", "Sub Helper")],
        "skill": [NameEntry("skill-id-a", "Skill A"), NameEntry("skill-id-b", "Skill B")],
    }

    built = []

    def resolver_for(access):
        built.append(access)
        return NameResolver(lookup=lambda kind, scope: names[kind], access=lambda k, i: seen.append((k, i)))

    with (
        patch(f"{MODULE}.get_assistant_by_id_or_raise", return_value=_assistant()),
        patch(f"{MODULE}.check_user_can_access_assistant"),
        patch(f"{MODULE}.NameResolver", side_effect=lambda access=None, user_id=None: resolver_for(access)),
    ):
        AssistantExporter().export("x", user=User(id="u", username="u"))
    assert callable(built[0])  # the exporter's own resolver is built with a read-access check
    assert {k for k, _ in seen} == {"assistant", "integration", "category", "skill"}


def test_enabled_catalog_only_mcp_server_is_an_export_error():
    assistant = _assistant()
    server = assistant.mcp_servers[0]
    server.mcp_connect_url = None
    server.config = None
    server.command = None
    with pytest.raises(ExportError, match="catalog link"):
        _export(assistant)
    server.enabled = False
    _export(assistant)  # a disabled link carries no behaviour


def test_export_requires_write_access_to_the_assistant():
    from codemie.core.ability import Action

    with (
        patch(f"{MODULE}.get_assistant_by_id_or_raise", return_value=_assistant()),
        patch(f"{MODULE}.check_user_can_access_assistant") as check,
    ):
        AssistantExporter(resolver=_resolver()).export("assistant-uuid-1", user=User(id="u", username="u"))
    assert check.call_args.args[3] == Action.WRITE


def test_a_user_who_can_only_read_the_assistant_is_denied():
    from codemie.core.ability import Action
    from codemie.core.exceptions import ExtendedHTTPException

    with (
        patch(f"{MODULE}.get_assistant_by_id_or_raise", return_value=_assistant()),
        patch("codemie.rest_api.routers.utils.Ability") as ability,
    ):
        ability.return_value.can.side_effect = lambda action, entity: action == Action.READ
        with pytest.raises(ExtendedHTTPException) as exc:
            AssistantExporter(resolver=_resolver()).export("assistant-uuid-1", user=User(id="u", username="u"))
    assert exc.value.code == 401  # the platform's access-denied code for assistants, as on its read path
