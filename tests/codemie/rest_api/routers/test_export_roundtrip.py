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

"""Export -> update round trip: the exported document, turned into the kind's update request and submitted to the
real update path, must leave the stored entity unchanged (except timestamps and a datasource's `error`).

Persistence is replaced with in-memory fakes; the update-request models, the routers and the update services are real.
Every case also asserts that the exported spec names every field the update request accepts, and a guard test proves
that dropping a field from the document makes the round trip fail.

LIMITATIONS (design-level; the document is the IaC envelope's shape, which carries less than the update requests).
Those that change the stored entity on update are pinned by an explicit xfail below (strict, `raises=AssertionError`);
the others are fields the document omits and the update leaves untouched, pinned by the exporters' "not exported" tests:
  * assistant: bedrock / agentcore / agent_card, guardrail assignments, the MCP catalog id (mcp_config_id) and the
    credentials inside a toolkit settings snapshot are not exported.
  * workflow: supervisor_prompt / meta_config have no envelope field and are kept by the update; inline MCP secrets in the definition become env references and
    the update path has no "unchanged" mask for them (the real engine resolves the reference from the environment).
  * assistant MCP inline secrets: same, update has no mask path for them.
  * assistant sensitive prompt variables: the default is exported as an env: reference (the envelope has no
    sensitive flag), and resolving that reference into a real masked/encrypted value on apply is on the consuming
    tool; this backend's own update path has no mask for it either, so a naive apply overwrites the stored secret
    with the literal reference text. The other round-trip cases below neutralize this one field (via
    `_assistant_update_body`'s `sensitive` handling) so they can test unrelated fields without tripping over it.
  * datasource: the schedule (cron_expression / timezone) is per user and is not entity state.
Fields the envelope cannot carry (plan_prompt, interactive_enabled, skills, ...) are simply absent from the update
request built here; the update keeps the stored value of every field that is not set, which the round trips prove.
"""

import copy
import json
from datetime import datetime
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import UserEntity
from codemie.rest_api.models.category import Category, CategoryUpdateRequest
from codemie.rest_api.models.skill import Skill, SkillUpdateRequest
from codemie.rest_api.routers import category as category_router
from codemie.rest_api.routers import skill as skill_router
from codemie.rest_api.security.authentication import admin_access_only, authenticate
from codemie.rest_api.security.user import User
from codemie.service.iac_export.document import ExportDocument, to_canonical_bytes

TIMESTAMPS = {"date", "update_date", "updated_date", "created_date", "last_reindex_date", "updated_at"}
MASK = "*" * 10  # SettingsService.MASKED_VALUE / provider datasource MASKED_VALUE: "keep the stored secret"
PROMPT_MASK = "*" * 8  # SENSITIVE_VALUE_MASK: same meaning for assistant prompt variables


def _admin() -> User:
    user = User(id="admin-1", username="admin-1")
    user.is_admin = True
    return user


def _app(*routers: Any, extra_overrides: dict | None = None) -> FastAPI:
    app = FastAPI()
    for router in routers:
        app.include_router(router)

    @app.exception_handler(ExtendedHTTPException)
    async def _handler(request, exc: ExtendedHTTPException):
        return JSONResponse(status_code=exc.code, content={"error": {"message": exc.message, "details": exc.details}})

    @app.middleware("http")
    async def _state(request: Request, call_next):
        request.state.user = _admin()
        request.state.uuid = "req-uuid"
        return await call_next(request)

    app.dependency_overrides[authenticate] = _admin
    app.dependency_overrides[admin_access_only] = lambda: None
    app.dependency_overrides.update(extra_overrides or {})
    return app


def _client(*routers: Any, extra_overrides: dict | None = None) -> TestClient:
    return TestClient(_app(*routers, extra_overrides=extra_overrides), raise_server_exceptions=True)


def _spec(document: ExportDocument) -> dict[str, Any]:
    """The `spec` exactly as a client receives it: canonical bytes parsed back (nulls dropped)."""
    return json.loads(to_canonical_bytes(document))["spec"]


def _snapshot(entity: Any, ignore: set[str] = frozenset()) -> dict[str, Any]:
    data = entity.model_dump(mode="json")
    return copy.deepcopy({k: v for k, v in data.items() if k not in TIMESTAMPS | set(ignore)})


def _assert_unchanged(before: dict[str, Any], entity: Any, ignore: set[str] = frozenset()) -> None:
    assert _snapshot(entity, ignore) == before


def _assert_covers(spec: dict[str, Any], request_model: type, not_exported: set[str], renames: dict | None = None):
    """Every field the update request accepts is present in the exported spec (or consciously not exported)."""
    renames = renames or {}
    accepted = {renames.get(f, f) for f in request_model.model_fields} - not_exported
    assert accepted <= set(spec), f"update-request fields missing from the export: {sorted(accepted - set(spec))}"


def _without(spec: dict[str, Any], *fields: str) -> dict[str, Any]:
    return {k: v for k, v in spec.items() if k not in fields}


# ---------------------------------------------------------------------------------------------------------------------
# category
# ---------------------------------------------------------------------------------------------------------------------


def _category() -> Category:
    return Category(id="dev_tools", name="Dev Tools", description="Tools for developers", date=datetime(2026, 1, 1))


def _roundtrip_category(spec_edit: Callable[[dict], dict] = lambda s: s) -> tuple[dict, Category]:
    from codemie.service.iac_export.exporters.category import CategoryExporter

    stored = _category()
    before = _snapshot(stored)
    with (
        patch.object(Category, "find_by_id", return_value=stored),
        patch.object(Category, "update"),
        patch("codemie.rest_api.routers.category.category_service.get_category_stats", return_value={}),
    ):
        spec = spec_edit(_spec(CategoryExporter().export("dev_tools", _admin())))
        CategoryUpdateRequest(**spec)  # real request-model validation
        response = _client(category_router.router).put("/v1/assistants/categories/dev_tools", json=spec)
    assert response.status_code == 200, response.text
    return before, stored


def test_category_round_trip_leaves_entity_unchanged():
    before, stored = _roundtrip_category()
    _assert_unchanged(before, stored)


def test_category_spec_covers_every_update_field():
    from codemie.service.iac_export.exporters.category import CategoryExporter

    with patch.object(Category, "find_by_id", return_value=_category()):
        spec = _spec(CategoryExporter().export("dev_tools", _admin()))
    _assert_covers(spec, CategoryUpdateRequest, not_exported=set())


def test_guard_category_round_trip_fails_when_a_field_is_omitted():
    # `description` omitted from the document: the update writes None over the stored value.
    before, stored = _roundtrip_category(lambda s: _without(s, "description"))
    with pytest.raises(AssertionError):
        _assert_unchanged(before, stored)


# ---------------------------------------------------------------------------------------------------------------------
# skill
# ---------------------------------------------------------------------------------------------------------------------


# The envelope's skill spec has no slot for these: toolkit integration settings, MCP catalog ids, companion files.
SKILL_NOT_EXPORTED = {"toolkits", "mcp_servers", "companion_files"}


def _skill() -> Skill:
    from codemie.rest_api.models.assistant import ToolDetails, ToolKitDetails
    from codemie.rest_api.models.skill import SkillCategory, SkillVisibility

    return Skill(
        id="skill-uuid-1",
        name="code-reviewer",
        description="Reviews code for correctness",
        content="# Review\n"
        + "Check the diff carefully, line by line, and report every correctness problem you find. " * 2,
        project="demo",
        visibility=SkillVisibility.PROJECT,
        categories=[SkillCategory.CODE_REVIEW.value],
        toolkits=[ToolKitDetails(toolkit="git", tools=[ToolDetails(name="diff"), ToolDetails(name="log")])],
        mcp_servers=[],
        enabled_builtin_subagents=[],
        created_by=None,
    )


class _SkillStore:
    """In-memory SkillRepository: `update` applies the updates dict onto the stored row like the real one."""

    def __init__(self, skill: Skill):
        self.skill = skill

    def update(self, skill_id: str, updates: dict[str, Any]) -> Skill:
        for key, value in updates.items():
            setattr(self.skill, key, value)
        return self.skill


@contextmanager
def _skill_persistence(skill: Skill) -> Iterator[None]:
    store = _SkillStore(skill)
    with (
        patch("codemie.service.skill_service.SkillRepository.get_by_id", return_value=skill),
        patch("codemie.service.skill_service.SkillRepository.update", side_effect=store.update),
        patch("codemie.service.skill_service.SkillRepository.count_assistants_using_skill", return_value=0),
        patch("codemie.service.skill_service.SkillService._get_skill_or_raise", return_value=skill),
        patch("codemie.service.skill_service.SkillMonitoringService.send_skill_management_metric"),
    ):
        yield


def _skill_update_body(spec: dict[str, Any]) -> dict[str, Any]:
    """Document -> SkillUpdateRequest body. The envelope's skill spec has no toolkits / mcp_servers, so the request
    leaves them unset and the update keeps the stored ones (like companion files)."""
    return dict(spec)


def _roundtrip_skill(skill: Skill, spec_edit: Callable[[dict], dict] = lambda s: s) -> tuple[dict, Skill]:
    from codemie.service.iac_export.exporters.skill import SkillExporter

    before = _snapshot(skill)
    with _skill_persistence(skill):
        spec = spec_edit(_spec(SkillExporter().export(skill.id, _admin())))
        body = _skill_update_body(spec)
        SkillUpdateRequest(**body)
        response = _client(skill_router.router).put(f"/v1/skills/{skill.id}", json=body)
    assert response.status_code == 200, response.text
    return before, skill


def test_skill_round_trip_leaves_entity_unchanged():
    skill = _skill()
    before, after = _roundtrip_skill(skill)
    _assert_unchanged(before, after)


def test_skill_spec_covers_every_update_field():
    from codemie.service.iac_export.exporters.skill import SkillExporter

    skill = _skill()
    with _skill_persistence(skill):
        spec = _spec(SkillExporter().export(skill.id, _admin()))
    _assert_covers(spec, SkillUpdateRequest, not_exported=SKILL_NOT_EXPORTED)


def test_guard_skill_completeness_check_fails_when_a_field_is_omitted():
    # Every SkillUpdateRequest field has keep-the-stored-value semantics, so an omission cannot change the stored
    # entity; the completeness check is what catches it.
    from codemie.service.iac_export.exporters.skill import SkillExporter

    skill = _skill()
    with _skill_persistence(skill):
        spec = _spec(SkillExporter().export(skill.id, _admin()))
    with pytest.raises(AssertionError, match="visibility"):
        _assert_covers(_without(spec, "visibility"), SkillUpdateRequest, not_exported=SKILL_NOT_EXPORTED)


def test_skill_round_trip_keeps_toolkit_settings_and_mcp_catalog_id():
    # These are not in the document at all (the former strict xfail no longer applies): the update request leaves
    # `toolkits` / `mcp_servers` unset, so the stored integration settings and catalog id survive.
    from codemie_tools.base.models import CredentialTypes

    from codemie.rest_api.models.assistant import MCPServerDetails, ToolDetails, ToolKitDetails
    from codemie.rest_api.models.settings import SettingsBase

    skill = _skill()
    skill.toolkits = [
        ToolKitDetails(
            toolkit="git",
            tools=[ToolDetails(name="diff")],
            settings=SettingsBase(id="int-1", project_name="demo", credential_type=CredentialTypes.GIT),
        )
    ]
    skill.mcp_servers = [MCPServerDetails(name="docs", mcp_config_id="catalog-id-1")]
    before, after = _roundtrip_skill(skill)
    _assert_unchanged(before, after)


# ---------------------------------------------------------------------------------------------------------------------
# integration
# ---------------------------------------------------------------------------------------------------------------------

OPENAPI = '{"openapi":"3.0.0","info":{"title":"t"}}'


def _integration(scope: str = "project") -> Any:
    from codemie_tools.base.models import CredentialTypes

    from codemie.rest_api.models.settings import CredentialValues, Settings, SettingType

    return Settings(
        id="setting-uuid-1",
        user_id="admin-1",
        project_name="demo",
        alias="My Jira",
        credential_type=CredentialTypes.JIRA,
        credential_values=[
            CredentialValues(key="url", value="https://x.example"),
            CredentialValues(key="token", value="sk-live-secret"),
            CredentialValues(key="openapi_spec", value=OPENAPI),
        ],
        setting_type=SettingType(scope),
        is_global=False,
    )


def _integration_update_body(spec: dict[str, Any]) -> dict[str, Any]:
    """Document -> SettingRequest body: every env reference becomes the mask ("keep the stored secret")."""
    values = [{"key": e["key"], "value": MASK} for e in spec.get("credential_values") or []]
    body = _without(spec, "setting_type", "credential_values", "default")
    return {**body, "credential_values": values}


@contextmanager
def _integration_persistence(setting: Any, fresh: Callable[[], Any]) -> Iterator[None]:
    from codemie.rest_api.models.settings import ProjectSetting, Settings, SettingType, UserSetting
    from codemie.service.settings.settings import SettingsService

    def ability(credential_id: str, settings_type: SettingType = SettingType.USER) -> Any:
        # The real lookup masks sensitive values in place; production hands it a fresh DB row, so give it one.
        wrapper = ProjectSetting if settings_type == SettingType.PROJECT else UserSetting
        return wrapper(fresh())

    with (
        patch.object(Settings, "get_by_id", return_value=setting),
        patch.object(Settings, "check_alias_unique"),
        patch.object(Settings, "update"),
        patch("codemie.service.settings.settings.attributes.flag_modified"),  # ORM change tracking only
        patch.object(SettingsService, "check_webhook_unique"),
        patch.object(SettingsService, "get_setting_ability", side_effect=ability),
        patch.object(SettingsService, "is_budget_integration_hidden_from", return_value=False),
        patch.object(SettingsService, "_clear_litellm_user_credentials_cache_if_needed"),
    ):
        yield


def _roundtrip_integration(scope: str, spec_edit: Callable[[dict], dict] = lambda s: s) -> tuple[dict, Any]:
    from codemie.rest_api.models.settings import SettingRequest
    from codemie.rest_api.routers import project_settings, user_settings
    from codemie.service.iac_export.exporters.integration import IntegrationExporter

    setting = _integration(scope)
    before = _snapshot(setting)
    router = project_settings.router if scope == "project" else user_settings.router
    with _integration_persistence(setting, lambda: _integration(scope)):
        spec = spec_edit(_spec(IntegrationExporter().export(setting.id, _admin())))
        assert spec["setting_type"] == scope
        body = _integration_update_body(spec)
        SettingRequest(**body)
        response = _client(router).put(f"/v1/settings/{scope}/{setting.id}", json=body)
    assert response.status_code == 200, response.text
    return before, setting


@pytest.mark.parametrize("scope", ["project", "user"])
def test_integration_round_trip_leaves_entity_unchanged(scope):
    before, setting = _roundtrip_integration(scope)
    _assert_unchanged(before, setting)
    stored = {c.key: c.value for c in setting.credential_values}
    assert stored["token"] == "sk-live-secret" and stored["openapi_spec"] == OPENAPI


def test_integration_spec_covers_every_update_field():
    from codemie.rest_api.models.settings import SettingRequest
    from codemie.service.iac_export.exporters.integration import IntegrationExporter

    setting = _integration()
    with _integration_persistence(setting, _integration):
        spec = _spec(IntegrationExporter().export(setting.id, _admin()))
    # oauth_state is a transient OAuth-flow parameter, not entity state.
    _assert_covers(spec, SettingRequest, not_exported={"oauth_state"})


def test_guard_integration_round_trip_fails_when_credential_is_omitted():
    # `token` left out of the document: the update drops credentials absent from the request.
    def drop_token(spec: dict) -> dict:
        return {**spec, "credential_values": [e for e in spec["credential_values"] if e["key"] != "token"]}

    before, setting = _roundtrip_integration("project", drop_token)
    with pytest.raises(AssertionError):
        _assert_unchanged(before, setting)


# ---------------------------------------------------------------------------------------------------------------------
# assistant
# ---------------------------------------------------------------------------------------------------------------------

SKILL_IDS = {"Skill A": "skill-id-a", "Skill B": "skill-id-b"}  # attached to the assistant, exported as names
SUB_ASSISTANT_IDS = {"Sub Helper": "asst-id-2"}
CATEGORY_IDS = {"Support": "cat-id-1"}


def _integration_refs() -> dict[str, Any]:
    """Integrations by name: what the importer resolves a reference to (reference only, no embedded secrets)."""
    from codemie_tools.base.models import CredentialTypes

    from codemie.rest_api.models.settings import SettingsBase

    def ref(id_: str, alias: str) -> SettingsBase:
        return SettingsBase(id=id_, project_name="demo", alias=alias, credential_type=CredentialTypes.JIRA)

    return {
        "Jira": ref("int-id-1", "Jira"),
        "Docs Env": ref("int-id-2", "Docs Env"),
        "Docs Token": ref("int-id-3", "T"),
    }


def _assistant(**overrides: Any) -> Any:
    from codemie.rest_api.models.assistant import (
        Assistant,
        Context,
        ContextType,
        MCPServerDetails,
        PromptVariable,
        ToolDetails,
        ToolKitDetails,
        ToolPermissionsConfig,
    )
    from codemie.rest_api.models.hedging import HedgingConfig, HedgingToolDetails
    from codemie.service.mcp.models import MCPServerConfig
    from codemie.service.subagents.builtin_subagents import BuiltinSubagent

    refs = _integration_refs()
    values: dict[str, Any] = {
        "id": "assistant-uuid-1",
        "name": "Helper",
        "slug": "helper",
        "description": "Helps",
        "system_prompt": "Be {{tone}} with {{api_key}}",
        "plan_prompt": "Plan first",
        "project": "demo",
        "icon_url": "https://img.example/i.png",
        "llm_model_type": "gpt-4",
        "enable_image_generation": True,
        "image_generation_model": "dall-e",
        "temperature": 0.3,
        "top_p": 0.9,
        "tools_tokens_size_limit": 1000,
        "smart_tool_selection_enabled": True,
        "file_attachment_enabled": True,
        "interactive_enabled": True,
        "shared": False,
        "is_global": False,
        "conversation_starters": ["hello", "help"],
        "context": [Context(context_type=ContextType.CODE, name="repo")],
        "skill_ids": list(SKILL_IDS.values()),
        "assistant_ids": ["asst-id-2"],
        "categories": ["cat-id-1"],
        "enabled_builtin_subagents": [BuiltinSubagent.GENERAL_PURPOSE_SUBAGENT],
        "hedging_config": HedgingConfig(
            tool=HedgingToolDetails(name="fast"), timeout_ms=300, input_mapping={"q": "query"}
        ),
        "tool_permissions": ToolPermissionsConfig(allow_override=False),
        "custom_metadata": {"team": "core"},
        "toolkits": [
            ToolKitDetails(
                toolkit="vcs",
                tools=[
                    ToolDetails(name="alpha", user_description="first", settings=refs["Jira"]),
                    ToolDetails(name="zeta"),
                ],
                settings=refs["Jira"],
            )
        ],
        "mcp_servers": [
            MCPServerDetails(
                name="docs",
                description="Docs server",
                enabled=True,
                use_custom_config=True,
                mcp_connect_url="https://mcp.example",
                tools_tokens_size_limit=500,
                tools=["search"],
                resolve_dynamic_values_in_arguments=True,
                settings=refs["Docs Env"],
                mcp_connect_auth_token=refs["Docs Token"],
                config=MCPServerConfig(url="https://docs.example/mcp", type="streamable-http"),
            ),
            MCPServerDetails(
                name="tools",
                enabled=False,
                use_custom_config=True,
                command="npx",
                arguments="-y tools",
                config=MCPServerConfig(command="uvx", args=["--fast"], single_usage=True),
            ),
        ],
        "prompt_variables": [
            PromptVariable(key="api_key", default_value="cipher-text-1", is_sensitive=True),
            PromptVariable(key="tone", description="Tone", default_value="friendly"),
        ],
        "created_by": None,
        "creator": "creator-id",
    }
    values.update(overrides)
    return Assistant(**values)


def _assistant_resolver() -> Any:
    from codemie.service.iac_export.references import NameEntry, NameResolver

    names = {
        "category": [NameEntry(i, n) for n, i in CATEGORY_IDS.items()],
        "integration": [NameEntry(s.id, name) for name, s in _integration_refs().items()],
        "assistant": [NameEntry(i, n) for n, i in SUB_ASSISTANT_IDS.items()],
        "skill": [NameEntry(i, n) for n, i in SKILL_IDS.items()],
    }
    return NameResolver(lookup=lambda kind, scope: names[kind])


def _is_ref(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("env:")


def _resolved(value: Any, stored_value: Any) -> Any:
    """What a correctly configured apply does with a positional-slot reference: substitute the operator-supplied
    value, which in a real round trip is the same literal the export derived it from. A naive apply that skips
    this step is pinned separately (`test_assistant_round_trip_with_unresolved_mcp_command_reference`)."""
    return stored_value if _is_ref(value) else value


def _assistant_mcp_body(server: dict[str, Any], integrations: dict[str, Any], stored_server: Any) -> dict[str, Any]:
    """MCP server entry of the document -> request entry: aliases back to integration objects, secrets to the mask,
    and positional-slot references (command / url / args) resolved back to the stored literal."""
    body = {k: v for k, v in server.items() if k not in ("config", "settings", "mcp_connect_auth_token")}
    for field in ("settings", "mcp_connect_auth_token"):
        if server.get(field):
            body[field] = integrations[server[field]].model_dump(mode="json")
    for field in ("command", "arguments", "mcp_connect_url"):
        if field in body:
            body[field] = _resolved(body[field], getattr(stored_server, field))
    if server.get("config") is not None:
        config = dict(server["config"])
        config["env"] = dict.fromkeys(config.get("env") or {}, MASK)
        config["headers"] = dict.fromkeys(config.get("headers") or {}, MASK)
        if config.get("auth_token"):
            config["auth_token"] = MASK
        stored_config = stored_server.config
        for field in ("command", "url"):
            if field in config:
                config[field] = _resolved(config[field], getattr(stored_config, field))
        if config.get("args") is not None:
            config["args"] = [
                _resolved(a, stored_config.args[i] if stored_config.args and i < len(stored_config.args) else a)
                for i, a in enumerate(config["args"])
            ]
        body["config"] = config
    return body


def _assistant_update_body(spec: dict[str, Any], stored: Any) -> dict[str, Any]:
    """Document -> AssistantRequest body, the way the envelope's apply builds it: names back to ids, env references
    to the mask. A sensitive prompt variable is forced to the mask here as a test-isolation device, not a claim
    about today's apply: it is the one field every fixture carries by default, so neutralizing it here lets every
    other round-trip case test its own field without also tripping over the pinned limitation in the module
    docstring. `test_assistant_round_trip_with_sensitive_prompt_variable` below removes the neutralization and
    pins the real, current behavior."""
    integrations = _integration_refs()

    def with_settings(entry: dict[str, Any]) -> dict[str, Any]:
        body = {k: v for k, v in entry.items() if k != "settings"}
        if entry.get("settings"):
            body["settings"] = integrations[entry["settings"]].model_dump(mode="json")
        return body

    sensitive = {v.key for v in stored.prompt_variables if v.is_sensitive}
    body = {k: v for k, v in spec.items() if k not in ("assistant_ids", "categories", "skill_ids")}
    body["skill_ids"] = [SKILL_IDS[n] for n in spec.get("skill_ids", [])]
    body["assistant_ids"] = [SUB_ASSISTANT_IDS[n] for n in spec.get("assistant_ids", [])]
    body["categories"] = [CATEGORY_IDS[n] for n in spec.get("categories", [])]
    body["toolkits"] = [
        {**with_settings(t), "tools": [with_settings(tool) for tool in t.get("tools", [])]}
        for t in spec.get("toolkits", [])
    ]
    stored_servers = {s.name: s for s in stored.mcp_servers}
    body["mcp_servers"] = [
        _assistant_mcp_body(s, integrations, stored_servers[s["name"]]) for s in spec.get("mcp_servers", [])
    ]
    body["prompt_variables"] = [
        {**v, "default_value": PROMPT_MASK, "is_sensitive": True} if v["key"] in sensitive else v
        for v in spec.get("prompt_variables", [])
    ]
    body["skip_integration_validation"] = True  # integration validation needs the real settings store
    return body


@contextmanager
def _assistant_persistence(assistant: Any) -> Iterator[None]:
    from codemie.rest_api.models.assistant import Assistant
    from codemie.rest_api.routers.assistant import management

    mod = "codemie.rest_api.routers.assistant.management"
    with (
        patch(f"{mod}.get_assistant_by_id_or_raise", return_value=assistant),
        patch("codemie.service.iac_export.exporters.assistant.get_assistant_by_id_or_raise", return_value=assistant),
        patch(f"{mod}.check_user_can_access_assistant"),
        patch("codemie.service.iac_export.exporters.assistant.check_user_can_access_assistant"),
        patch(f"{mod}.project_access_check"),
        patch(f"{mod}._validate_remote_entities_and_raise"),
        patch(f"{mod}._track_assistant_management_metric"),
        patch(f"{mod}._track_mcp_usage_changes"),
        patch(f"{mod}._filter_invalid_datasources"),
        patch(f"{mod}.category_service.validate_category_ids"),
        patch(f"{mod}.GuardrailService.sync_guardrail_assignments_for_entity"),
        patch.object(Assistant, "update"),
        patch.object(Assistant, "refresh_marketplace_columns_from_db"),
        patch(
            "codemie.service.assistant.assistant_version_compare_service."
            "AssistantVersionCompareService.has_configuration_changes",
            return_value=False,
        ),
    ):
        assert management.router is not None
        yield


def _roundtrip_assistant(
    assistant: Any, spec_edit: Callable[[dict], dict] = lambda s: s, body_edit: Callable[[dict], dict] = lambda b: b
) -> tuple[dict, dict, Any]:
    from codemie.rest_api.models.assistant import AssistantRequest
    from codemie.rest_api.routers.assistant import management
    from codemie.service.iac_export.exporters.assistant import AssistantExporter

    before = _snapshot(assistant)
    with _assistant_persistence(assistant):
        spec = spec_edit(_spec(AssistantExporter(resolver=_assistant_resolver()).export(assistant.id, _admin())))
        body = body_edit(_assistant_update_body(spec, assistant))
        AssistantRequest(**body)  # real request-model validation
        app = _app()
        app.include_router(management.router, prefix="/v1")
        response = TestClient(app).put(f"/v1/assistants/{assistant.id}", json=body)
    assert response.status_code == 200, response.text
    assert "validation" not in response.json() or not response.json()["validation"], response.text
    return before, spec, assistant


def test_assistant_round_trip_leaves_entity_unchanged():
    assistant = _assistant()
    before, _spec_doc, after = _roundtrip_assistant(assistant)
    _assert_unchanged(before, after)


# AssistantRequest fields the document consciously does not carry (see LIMITATIONS in the module docstring).
ASSISTANT_NOT_EXPORTED = {
    "guardrail_assignments",  # per-entity assignment table, not assistant state
    "skip_integration_validation",  # request-time flag
    "source_assistant_id",  # clone bookkeeping
    "type",  # codemie / bedrock / agentcore discriminator: remote assistants are not exportable
    "bedrock",
    "bedrock_agentcore_runtime",
    "agent_card",
    # no field in the envelope's assistant spec:
    "plan_prompt",
    "agent_mode",
    "image_generation_model",
    "smart_tool_selection_enabled",
    "custom_metadata",
    "hide_tool_outputs",
}


def test_assistant_spec_covers_every_update_field():
    from codemie.rest_api.models.assistant import AssistantRequest
    from codemie.service.iac_export.exporters.assistant import AssistantExporter

    assistant = _assistant()
    with _assistant_persistence(assistant):
        spec = _spec(AssistantExporter(resolver=_assistant_resolver()).export(assistant.id, _admin()))
    _assert_covers(spec, AssistantRequest, not_exported=ASSISTANT_NOT_EXPORTED)


def test_guard_assistant_round_trip_fails_when_a_field_is_omitted():
    # `interactive_enabled` omitted from the document: the request model default (False) is then not "set", so the
    # update keeps the stored value; omitting `toolkits` instead wipes them. Drop the toolkits from the body.
    assistant = _assistant()
    before, _spec_doc, after = _roundtrip_assistant(assistant, body_edit=lambda b: {**b, "toolkits": []})
    with pytest.raises(AssertionError):
        _assert_unchanged(before, after)


def _assistant_with_server(**server_fields: Any) -> Any:
    from codemie.rest_api.models.assistant import MCPServerDetails
    from codemie.service.mcp.models import MCPServerConfig

    config = server_fields.pop("config", MCPServerConfig(command="uvx", args=["--fast"]))
    return _assistant(
        mcp_servers=[MCPServerDetails(name="tools", use_custom_config=True, config=config, **server_fields)]
    )


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "LIMITATION (assistant): inline MCP config secrets (env / headers / auth_token) are exported as env references. "
        "With the real engine the secret is preserved when the operator's environment variable holds the same value "
        "(apply resolves the env: reference and sends the real value); an unset variable fails preflight with no "
        "write. The backend update itself still has no mask path for env: references, which is what this test pins: "
        "the reference text overwrites the stored secrets."
    ),
)
def test_assistant_round_trip_with_inline_mcp_secrets():
    from codemie.service.mcp.models import MCPServerConfig

    assistant = _assistant_with_server(config=MCPServerConfig(command="uvx", env={"TOKEN": "s3cret"}))
    before, _spec_doc, after = _roundtrip_assistant(assistant)
    _assert_unchanged(before, after)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "LIMITATION (assistant): the MCP catalog reference (mcp_config_id) is an id and is not exported, so a "
        "catalog-backed server loses its catalog link on update."
    ),
)
def test_assistant_round_trip_with_mcp_catalog_reference():
    assistant = _assistant_with_server(mcp_config_id="catalog-id-9")
    before, _spec_doc, after = _roundtrip_assistant(assistant)
    _assert_unchanged(before, after)


def test_assistant_round_trip_with_auto_credentials_lookup_disabled():
    from codemie.rest_api.models.assistant import ToolDetails, ToolKitDetails

    toolkit = ToolKitDetails(toolkit="vcs", tools=[ToolDetails(name="alpha")], auto_credentials_lookup=False)
    before, _spec_doc, after = _roundtrip_assistant(_assistant(toolkits=[toolkit]))
    _assert_unchanged(before, after)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "LIMITATION (assistant): a toolkit settings snapshot that carries credential values is reduced to its "
        "integration reference plus the mask, and the assistant update has no mask path for toolkit credentials, "
        "so the stored values are overwritten with the mask."
    ),
)
def test_assistant_round_trip_with_toolkit_settings_snapshot_credentials():
    from codemie_tools.base.models import CredentialTypes

    from codemie.rest_api.models.assistant import ToolDetails, ToolKitDetails
    from codemie.rest_api.models.settings import CredentialValues, SettingsBase

    settings = SettingsBase(
        id="int-id-1",
        project_name="demo",
        alias="Jira",
        credential_type=CredentialTypes.JIRA,
        credential_values=[CredentialValues(key="token", value="stored-secret")],
    )
    toolkit = ToolKitDetails(toolkit="vcs", tools=[ToolDetails(name="alpha", settings=settings)], settings=settings)
    before, _spec_doc, after = _roundtrip_assistant(_assistant(toolkits=[toolkit]))
    _assert_unchanged(before, after)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "LIMITATION (assistant): a sensitive prompt variable's default is exported empty (Jira decision, amended: "
        "the envelope never resolves a reference inside a prompt variable, so an env: reference there would be "
        "literal, unresolvable text); this backend's own update path has no mask path for it either, so a naive "
        "apply still overwrites the stored secret, now with an empty value instead of reference text."
    ),
)
def test_assistant_round_trip_with_sensitive_prompt_variable():
    captured: dict[str, Any] = {}

    def capture(spec: dict[str, Any]) -> dict[str, Any]:
        captured["prompt_variables"] = spec["prompt_variables"]
        return spec

    def naive_apply(body: dict[str, Any]) -> dict[str, Any]:
        return {**body, "prompt_variables": captured["prompt_variables"]}

    before, _spec_doc, after = _roundtrip_assistant(_assistant(), spec_edit=capture, body_edit=naive_apply)
    _assert_unchanged(before, after)


# ---------------------------------------------------------------------------------------------------------------------
# workflow
# ---------------------------------------------------------------------------------------------------------------------

WORKFLOW_ASSISTANT_IDS = {"Helper": "asst-id-1"}
WORKFLOW_YAML = """\
assistants:
- id: a1
  assistant_id: asst-id-1
  model: gpt-4
states:
- id: s1
  assistant_id: a1
  task: Do the work
  next:
    state_id: end
"""
WORKFLOW_YAML_WITH_SECRETS = """\
assistants:
- id: a1
  assistant_id: asst-id-1
  model: gpt-4
  mcp_servers:
  - name: docs
    config:
      command: npx
      env:
        TOKEN: workflow-secret
      auth_token: workflow-secret
states:
- id: s1
  assistant_id: a1
  task: Do the work
  next:
    state_id: end
"""


def _workflow(yaml_config: str = WORKFLOW_YAML, **overrides: Any) -> Any:
    from codemie.core.workflow_models import WorkflowConfig

    values: dict[str, Any] = {
        "id": "wf-uuid-1",
        "name": "Release Flow",
        "description": "Ships releases",
        "project": "demo",
        "mode": "Sequential",
        "icon_url": "https://img.example/wf.png",
        "shared": False,
        "yaml_config": yaml_config,
        "created_by": UserEntity(user_id="admin-1", username="admin-1", name="admin-1"),
        "categories": ["cat-id-1"],
    }
    workflow = WorkflowConfig(**{**values, **overrides})
    workflow.parse_execution_config()  # the stored row carries the parsed definition
    return workflow


WORKFLOW_IGNORED = {"updated_by", "yaml_config_history", "guardrail_assignments"}  # server-managed / response-only


def _workflow_update_body(spec: dict[str, Any]) -> dict[str, Any]:
    """Document -> UpdateWorkflowRequest body, the way the envelope's apply builds it: the definition's header, the
    structured definition rendered back to the stored yaml text (assistant names back to ids) and the target
    project. Env references stay as they are: the workflow update has no mask semantics."""
    from codemie.service.iac_export.workflow_converter import render

    definition = spec["workflow"]
    body = {k: definition[k] for k in ("name", "description", "mode", "shared", "icon_url") if k in definition}
    return {
        **body,
        "project": "demo",
        "start_hint": spec.get("start_hint"),
        "yaml_config": render(definition, WORKFLOW_ASSISTANT_IDS),
    }


@contextmanager
def _workflow_persistence(workflow: Any) -> Iterator[None]:
    from codemie.core.workflow_models import WorkflowConfig

    router = "codemie.rest_api.routers.workflow"
    with (
        patch("codemie.service.iac_export.exporters.workflow.workflow_service.get_workflow", return_value=workflow),
        patch(f"{router}.workflow_service.get_workflow", return_value=workflow),
        patch(f"{router}.project_access_check"),
        patch(f"{router}._normalize_workflow_model_ids"),
        patch(f"{router}._run_pre_persist_update_validation"),
        patch(f"{router}.GuardrailService.sync_guardrail_assignments_for_entity"),
        patch(f"{router}.GuardrailService.get_entity_guardrail_assignments", return_value=[]),
        patch(f"{router}._consumer_slot_warnings", return_value=[]),
        patch(f"{router}.update_workflow_schema"),
        patch.object(WorkflowConfig, "update"),
    ):
        yield


def _roundtrip_workflow(
    workflow: Any, spec_edit: Callable[[dict], dict] = lambda s: s, body_edit: Callable[[dict], dict] = lambda b: b
) -> tuple[dict, dict, Any]:
    from codemie.core.workflow_models.workflow_models import UpdateWorkflowRequest
    from codemie.rest_api.routers import workflow as workflow_router
    from codemie.service.iac_export.exporters.workflow import WorkflowExporter
    from codemie.service.iac_export.references import NameEntry, NameResolver

    before = _snapshot(workflow, WORKFLOW_IGNORED)
    resolver = NameResolver(lookup=lambda kind, scope: [NameEntry(i, n) for n, i in WORKFLOW_ASSISTANT_IDS.items()])
    with _workflow_persistence(workflow):
        spec = spec_edit(_spec(WorkflowExporter(resolver=resolver).export(workflow.id, _admin())))
        body = body_edit(_workflow_update_body(spec))
        UpdateWorkflowRequest(**body)  # real request-model validation
        response = _client(workflow_router.router).put(f"/v1/workflows/{workflow.id}", json=body)
    assert response.status_code == 200, response.text
    return before, spec, workflow


def test_workflow_round_trip_leaves_entity_unchanged():
    workflow = _workflow()
    before, _spec_doc, after = _roundtrip_workflow(workflow)
    _assert_unchanged(before, after, WORKFLOW_IGNORED)
    assert after.categories == ["cat-id-1"]  # omitted from the request, so the stored categories are kept


# UpdateWorkflowRequest fields outside the envelope's workflow header: project is the apply target, categories keep
# the stored value when unset, supervisor_prompt / meta_config have no envelope field and are left out of the request
# (the update keeps their stored values).
WORKFLOW_NOT_EXPORTED = {
    "id",
    "guardrail_assignments",
    "categories",
    "project",
    "supervisor_prompt",
    "meta_config",
    "yaml_config",  # exported as the structured definition
}


def test_workflow_spec_covers_every_update_field():
    from codemie.core.workflow_models.workflow_models import UpdateWorkflowRequest
    from codemie.service.iac_export.exporters.workflow import WorkflowExporter
    from codemie.service.iac_export.references import NameEntry, NameResolver

    workflow = _workflow()
    resolver = NameResolver(lookup=lambda kind, scope: [NameEntry("asst-id-1", "Helper")])
    with _workflow_persistence(workflow):
        spec = _spec(WorkflowExporter(resolver=resolver).export(workflow.id, _admin()))
    _assert_covers(
        {**spec["workflow"], "start_hint": spec.get("start_hint")},
        UpdateWorkflowRequest,
        not_exported=WORKFLOW_NOT_EXPORTED,
    )
    assert {"assistants", "states"} <= set(spec["workflow"])  # the definition itself


def test_guard_workflow_round_trip_fails_when_a_field_is_omitted():
    # `execution_config` reduced to nothing: the stored definition is replaced.
    workflow = _workflow()
    before, _spec_doc, after = _roundtrip_workflow(
        workflow, body_edit=lambda b: {**b, "yaml_config": WORKFLOW_YAML.replace("Do the work", "Other work")}
    )
    with pytest.raises(AssertionError):
        _assert_unchanged(before, after, WORKFLOW_IGNORED)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "LIMITATION (workflow): inline MCP secrets inside the workflow yaml are exported as env references. With the "
        "real engine the secret is preserved when the operator's environment variable holds the same value (apply "
        "resolves the env: reference and sends the real value); an unset variable fails preflight with no write. The "
        "backend update itself still has no mask path for env: references, which is what this test pins: the "
        "reference text overwrites the stored secrets."
    ),
)
def test_workflow_round_trip_with_inline_mcp_secrets():
    workflow = _workflow(WORKFLOW_YAML_WITH_SECRETS)
    before, _spec_doc, after = _roundtrip_workflow(workflow)
    _assert_unchanged(before, after, WORKFLOW_IGNORED)


def test_workflow_round_trip_with_start_hint():
    workflow = _workflow(start_hint="Say go")
    before, _spec_doc, after = _roundtrip_workflow(workflow)
    _assert_unchanged(before, after, WORKFLOW_IGNORED)


@pytest.mark.parametrize(
    ("field", "value"), [("supervisor_prompt", "Supervise"), ("meta_config", "meta: true")], ids=lambda v: str(v)
)
def test_workflow_round_trip_keeps_supervisor_prompt_and_meta_config(field, value):
    # The real engine's update body leaves these out, so the update keeps the stored values.
    workflow = _workflow(**{field: value})
    before, _spec_doc, after = _roundtrip_workflow(workflow)
    _assert_unchanged(before, after, WORKFLOW_IGNORED)
    assert getattr(after, field) == value


def test_workflow_round_trip_with_engine_switches_in_the_yaml():
    workflow = _workflow("enable_summarization_node: false\n" + WORKFLOW_YAML)
    before, _spec_doc, after = _roundtrip_workflow(workflow)
    _assert_unchanged(before, after, WORKFLOW_IGNORED)


# ---------------------------------------------------------------------------------------------------------------------
# datasource
# ---------------------------------------------------------------------------------------------------------------------

DS_INDEX_ID = "index-uuid-1"
DS_PROJECT = "demo"
DS_SETTING_ID = "setting-uuid-9"
DS_INTEGRATION = "My Integration"
DS_GUARDRAILS = {"pii:1": "g-uuid"}
DS_CRON = {"cron_expression": "0 3 * * *", "timezone": "Europe/Warsaw"}
DS_IGNORED = {"error", "text"}  # `error` is the one datasource field the update may legitimately touch


def _snake(name: str) -> str:
    import re

    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _ds_index(index_type: str, **kw: Any) -> Any:
    from codemie.rest_api.models.index import IndexInfo

    values = {
        "id": DS_INDEX_ID,
        "project_name": DS_PROJECT,
        "repo_name": "my-ds",
        "index_type": index_type,
        "description": "desc",
        "project_space_visible": True,
        "setting_id": DS_SETTING_ID,
        "error": True,
        "text": "status text",
    }
    values.update(kw)
    return IndexInfo(**values)


def _ds_code(vcs: str, index_type: str) -> Any:
    link = "https://git.example.com/r.git" if vcs == "git" else "svn://svn.example.com/repo"
    return _ds_index(
        index_type,
        vcs_type=vcs,
        prompt="summarise",
        embeddings_model="emb-1",
        docs_generation=True,
        branch="main" if vcs == "git" else "trunk",
        link=link,
        files_filter="*.py",
    )


def _ds_confluence() -> Any:
    from codemie.rest_api.models.index import ConfluenceIndexInfo

    return _ds_index("knowledge_base_confluence", confluence=ConfluenceIndexInfo(cql="space = X"))


def _ds_jira() -> Any:
    from codemie.rest_api.models.index import JiraIndexInfo

    return _ds_index("knowledge_base_jira", jira=JiraIndexInfo(jql="project = X", custom_fields=["cf_1"]))


def _ds_xray() -> Any:
    from codemie.rest_api.models.index import XrayIndexInfo

    return _ds_index("knowledge_base_xray", xray=XrayIndexInfo(jql="project = X"))


def _ds_azure_wiki() -> Any:
    from codemie.rest_api.models.index import AzureDevOpsWikiIndexInfo

    info = AzureDevOpsWikiIndexInfo(wiki_query="/docs", wiki_name="main")
    return _ds_index("knowledge_base_azure_devops_wiki", azure_devops_wiki=info)


def _ds_azure_work_item() -> Any:
    from codemie.rest_api.models.index import AzureDevOpsWorkItemIndexInfo

    info = AzureDevOpsWorkItemIndexInfo(wiql_query="SELECT 1")
    return _ds_index("knowledge_base_azure_devops_work_item", azure_devops_work_item=info)


def _ds_git_faq() -> Any:
    return _ds_index(
        "knowledge_base_git_faq", link="https://git.example.com/faq.git", branch="main", files_filter="*.md"
    )


def _ds_google() -> Any:
    return _ds_index("llm_routing_google")


def _ds_sharepoint() -> Any:
    from codemie.rest_api.models.index import SharePointIndexInfo

    info = SharePointIndexInfo(
        site_url="https://t.sharepoint.com/sites/x",
        include_pages=True,
        include_documents=False,
        include_lists=True,
        max_file_size_mb=20,
        files_filter="*.docx",
        auth_type="oauth_custom",
        oauth_client_id="client-1",
        oauth_tenant_id="tenant-1",
        access_token="LIVE-TOKEN-VALUE",
    )
    # The update path mirrors the nested files_filter onto the index row, so a consistent row carries both.
    return _ds_index("knowledge_base_sharepoint", sharepoint=info, embeddings_model="emb-1", files_filter="*.docx")


def _ds_xwiki() -> Any:
    from codemie.rest_api.models.index import XWikiIndexInfo

    return _ds_index("knowledge_base_xwiki", xwiki=XWikiIndexInfo(space="KB.Onboarding", wiki="main"))


def _ds_body(spec: dict[str, Any], request: type) -> dict[str, Any]:
    """Document -> update request body, common part, the way the envelope's apply builds it: the discriminator is
    dropped, `shared_with_project` becomes `project_space_visible`, the integration alias goes back to its id and
    fields this update request does not accept are left out. Guardrail assignments are not in the document, so they
    stay unset (the update then keeps the stored ones)."""
    accepted = set(request.model_fields) | {f.alias for f in request.model_fields.values() if f.alias}
    body = {("project_space_visible" if k == "shared_with_project" else k): v for k, v in spec.items() if k != "type"}
    if body.get("setting_id") == DS_INTEGRATION:
        body["setting_id"] = DS_SETTING_ID
    return {k: v for k, v in body.items() if k in accepted or CODE_CAMEL.get(k) in accepted}


CODE_CAMEL = {
    "embeddings_model": "embeddingsModel",
    "project_space_visible": "projectSpaceVisible",
    "docs_generation": "docsGeneration",
    "files_filter": "filesFilter",
}


def _ds_code_body(spec: dict[str, Any], request: type) -> dict[str, Any]:
    return {CODE_CAMEL.get(k, k): v for k, v in _ds_body(spec, request).items()}


def _ds_requests() -> dict[str, type]:
    from codemie.rest_api.models import index as m

    return {
        "code": m.UpdateIndexRequest,
        "confluence": m.UpdateKnowledgeBaseConfluenceRequest,
        "jira": m.UpdateKnowledgeBaseJiraRequest,
        "xray": m.UpdateKnowledgeBaseXrayRequest,
        "azure_wiki": m.UpdateKnowledgeBaseAzureDevOpsWikiRequest,
        "azure_work_item": m.UpdateKnowledgeBaseAzureDevOpsWorkItemRequest,
        "git_faq": m.UpdateKnowledgeBaseGitFaqRequest,
        "google": m.UpdateKnowledgeBaseGoogleRequest,
        "sharepoint": m.UpdateKnowledgeBaseSharePointRequest,
        "xwiki": m.UpdateKnowledgeBaseXWikiRequest,
    }


KB = "/v1/index/knowledge_base"
# id -> (factory, update request key, PUT url, query, body builder)
DS_KB_CASES: dict[str, tuple[Callable[[], Any], str, str, str]] = {
    "confluence": (_ds_confluence, "confluence", f"{KB}/confluence", ""),
    "jira": (_ds_jira, "jira", f"{KB}/jira", ""),
    "xray": (_ds_xray, "xray", f"{KB}/xray", ""),
    "azure_devops_wiki": (_ds_azure_wiki, "azure_wiki", f"{KB}/azure_devops_wiki", ""),
    "azure_devops_work_item": (_ds_azure_work_item, "azure_work_item", f"{KB}/azure_devops_work_item", ""),
    "git_faq": (_ds_git_faq, "git_faq", f"{KB}/git_faq", "full_reindex=true"),  # source fields demand a reindex
    "google": (_ds_google, "google", f"{KB}/google", ""),
    "sharepoint": (_ds_sharepoint, "sharepoint", f"{KB}/sharepoint", ""),
    "xwiki": (_ds_xwiki, "xwiki", f"{KB}/xwiki", ""),
}
# Request-time routing (a project move is rejected by apply), the guardrail assignment table and the transient
# SharePoint token have no field in the envelope; project_space_visible is its `shared_with_project`.
DS_NOT_EXPORTED = {"new_project_name", "guardrail_assignments", "access_token"}
DS_RENAMES = {"project_space_visible": "shared_with_project"}


class _Recorder:
    """What the update path handed to its collaborators (schedule, guardrails, reindex)."""

    def __init__(self) -> None:
        self.schedule: list[dict[str, Any]] = []
        self.guardrails: list[Any] = []
        self.reindex: list[dict[str, Any]] = []


@contextmanager
def _ds_persistence(indexes: list[Any], recorder: _Recorder) -> Iterator[None]:
    from unittest.mock import MagicMock

    from codemie.rest_api.models.guardrail import GuardrailAssignmentItem, GuardrailMode, GuardrailSource
    from codemie.rest_api.models.index import IndexInfo, KnowledgeBaseIndexInfo
    from codemie.service.guardrail.guardrail_service import GuardrailService
    from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService

    index = indexes[0]
    assignment = GuardrailAssignmentItem(
        guardrail_id="g-uuid",
        mode=GuardrailMode.ALL,
        source=GuardrailSource.INPUT,
        editable=True,
        guardrail_name="pii:1",
    )

    def handle_schedule(**kwargs: Any) -> None:
        recorder.schedule.append(kwargs)

    def sync(**kwargs: Any) -> None:
        recorder.guardrails.append(kwargs.get("guardrail_assignments"))

    def processor(*args: Any, **kwargs: Any) -> MagicMock:
        recorder.reindex.append(kwargs)
        return MagicMock()

    routers = "codemie.rest_api.routers.index"
    with (
        patch.object(IndexInfo, "get_by_id", return_value=index),
        patch.object(IndexInfo, "find_by_id", return_value=index),
        patch.object(IndexInfo, "update"),
        patch.object(IndexInfo, "filter_by_project_and_repo", return_value=[index]),
        patch.object(KnowledgeBaseIndexInfo, "filter_by_project_and_repo", return_value=[index]),
        patch.object(GuardrailService, "get_entity_guardrail_assignments", return_value=[assignment]),
        patch.object(GuardrailService, "sync_guardrail_assignments_for_entity", side_effect=sync),
        patch.object(
            SchedulerSettingsService,
            "get_scheduler_settings_for_datasources",
            return_value={DS_INDEX_ID: dict(DS_CRON)},
        ),
        patch.object(SchedulerSettingsService, "handle_schedule", side_effect=handle_schedule),
        patch(f"{routers}.kb_git_faq._build_git_faq_processor_from_index", side_effect=processor),
        patch(f"{routers}.kb_git_faq._validate_git_credentials"),
        patch(f"{routers}.kb_atlassian.SettingsService.get_jira_creds"),
        patch(f"{routers}.kb_atlassian.JiraDatasourceProcessor.check_jira_query"),
        patch(f"{routers}.code_repositories.request_summary_manager"),
        patch(f"{routers}._repository_support.AgentMonitoringService"),
        patch(f"{routers}.code_repositories._validate_git_credentials"),
        patch(f"{routers}.code_repositories.SVNIndexService.validate_credentials"),
    ):
        yield


def _ds_client() -> TestClient:
    from codemie.rest_api.routers import index
    from codemie.rest_api.security.authentication import application_access_check

    return _client(index.router, extra_overrides={application_access_check: lambda: None})


def _ds_export(index: Any, **resolver_overrides: Any) -> dict[str, Any]:
    from codemie.service.iac_export.exporters.datasource import DatasourceDispatchExporter
    from codemie.service.iac_export.references import NameEntry, NameResolver

    resolver = NameResolver(lambda kind, scope: [NameEntry(DS_SETTING_ID, DS_INTEGRATION)])
    return _spec(DatasourceDispatchExporter(resolver=resolver).export(DS_INDEX_ID, _admin()))


def _roundtrip_kb_datasource(
    case: str, spec_edit: Callable[[dict], dict] = lambda s: s
) -> tuple[dict, Any, _Recorder, dict]:
    factory, request_key, url, query = DS_KB_CASES[case]
    index = factory()
    before = _snapshot(index, DS_IGNORED)
    recorder = _Recorder()
    with _ds_persistence([index], recorder):
        spec = spec_edit(_ds_export(index))
        body = {**_ds_body(spec, _ds_requests()[request_key]), "project_name": DS_PROJECT}
        _ds_requests()[request_key](**body)  # real request-model validation
        response = _ds_client().put(f"{url}?{query}" if query else url, json=body)
    assert response.status_code == 200, response.text
    return before, index, recorder, spec


@pytest.mark.parametrize("case", sorted(DS_KB_CASES))
def test_kb_datasource_round_trip_leaves_entity_unchanged(case):
    before, index, recorder, spec = _roundtrip_kb_datasource(case)
    _assert_unchanged(before, index, DS_IGNORED)
    assert index.setting_id == DS_SETTING_ID or "setting_id" not in spec
    assert recorder.guardrails == [None]  # not in the document: the update leaves the stored assignments alone
    _assert_schedule_applied(case, recorder)


def _assert_schedule_applied(case: str, recorder: _Recorder) -> None:
    if case == "git_faq":  # a full reindex hands the schedule to the processor instead
        assert recorder.reindex[-1]["cron_expression"] == DS_CRON["cron_expression"]
    elif case == "sharepoint":  # oauth_custom datasources are never scheduled (short-lived tokens)
        assert not recorder.schedule
    else:
        assert recorder.schedule[-1]["cron_expression"] == DS_CRON["cron_expression"]
        assert recorder.schedule[-1]["timezone"] == DS_CRON["timezone"]


@pytest.mark.parametrize("case", sorted(DS_KB_CASES))
def test_kb_datasource_spec_covers_every_update_field(case):
    factory, request_key, _url, _query = DS_KB_CASES[case]
    index = factory()
    with _ds_persistence([index], _Recorder()):
        spec = _ds_export(index)
    _assert_covers(spec, _ds_requests()[request_key], not_exported=DS_NOT_EXPORTED, renames=DS_RENAMES)


def test_guard_datasource_round_trip_fails_when_a_field_is_omitted():
    # `cql` omitted from the document: the confluence update falls back to the request default and the stored query
    # no longer matches the document it was exported from.
    before, index, _recorder, _spec_doc = _roundtrip_kb_datasource(
        "confluence", spec_edit=lambda s: {**s, "cql": "space = OTHER"}
    )
    with pytest.raises(AssertionError):
        _assert_unchanged(before, index, DS_IGNORED)


def test_guard_datasource_completeness_check_fails_when_a_field_is_omitted():
    index = _ds_confluence()
    with _ds_persistence([index], _Recorder()):
        spec = _ds_export(index)
    with pytest.raises(AssertionError, match="cql"):
        _assert_covers(
            _without(spec, "cql"), _ds_requests()["confluence"], not_exported=DS_NOT_EXPORTED, renames=DS_RENAMES
        )


# datasource: code (git / svn, also summary and chunk-summary) ---------------------------------------------------------

DS_CODE_CASES = [
    pytest.param("git", "code", id="code-git"),
    pytest.param("git", "summary", id="summary-git"),
    pytest.param("git", "chunk-summary", id="chunk-summary-git"),
    pytest.param("svn", "code", id="code-svn"),
]


def _ds_repo(index: Any, vcs: str) -> Any:
    from codemie.core.models import BaseRepository, CodeIndexType, GitRepo, SVNRepo

    fields = {
        "id": "repo-1",
        "name": index.repo_name,
        "description": "desc",
        "link": index.link,
        "branch": index.branch,
        "files_filter": index.files_filter,
        "index_type": CodeIndexType.CODE,
        "embeddings_model": index.embeddings_model,
        "prompt": index.prompt,
        "docs_generation": index.docs_generation,
        "project_space_visible": index.project_space_visible,
        "setting_id": index.setting_id,
        "app_id": DS_PROJECT,
    }
    assert issubclass(GitRepo, BaseRepository)
    return (GitRepo if vcs == "git" else SVNRepo)(**fields)


def _roundtrip_code_datasource(vcs: str, index_type: str, spec_edit: Callable[[dict], dict] = lambda s: s):
    from codemie.core.models import Application, GitRepo, SVNRepo

    index = _ds_code(vcs, index_type)
    repo = _ds_repo(index, vcs)
    before = (_snapshot(index, DS_IGNORED), _snapshot(repo))
    recorder = _Recorder()
    router = "codemie.rest_api.routers.index.code_repositories"
    url = f"/v1/application/{DS_PROJECT}/index/{'svn/' if vcs == 'svn' else ''}my-ds?skip_reindex=true"
    with (
        _ds_persistence([index], recorder),
        patch.object(GitRepo, "get_by_app_id", return_value=[repo]),
        patch.object(SVNRepo, "get_by_app_id", return_value=[repo]),
        patch.object(GitRepo, "update"),
        patch.object(SVNRepo, "update"),
        patch.object(Application, "get_by_id", return_value=Application(name=DS_PROJECT)),
        patch(f"{router}.Ability") as ability,
    ):
        ability.return_value.can.return_value = True
        spec = spec_edit(_ds_export(index))
        body = {**_ds_code_body(spec, _ds_requests()["code"]), "project_name": DS_PROJECT}
        _ds_requests()["code"](**body)  # real request-model validation
        response = _ds_client().put(url, json=body)
    assert response.status_code == 201, response.text
    return before, (index, repo), recorder, spec


@pytest.mark.parametrize(("vcs", "index_type"), DS_CODE_CASES)
def test_code_datasource_round_trip_leaves_entity_unchanged(vcs, index_type):
    before, (index, repo), recorder, spec = _roundtrip_code_datasource(vcs, index_type)
    assert spec["index_type"] == index_type
    assert spec["type"] == ("svn" if vcs == "svn" else "code")
    _assert_unchanged(before[0], index, DS_IGNORED)
    _assert_unchanged(before[1], repo)
    assert recorder.schedule[-1]["cron_expression"] == DS_CRON["cron_expression"]


@pytest.mark.parametrize(("vcs", "index_type"), DS_CODE_CASES)
def test_code_datasource_spec_covers_every_update_field(vcs, index_type):
    index = _ds_code(vcs, index_type)
    with _ds_persistence([index], _Recorder()):
        spec = _ds_export(index)
    renames = {camel: DS_RENAMES.get(snake, snake) for snake, camel in CODE_CAMEL.items()}
    _assert_covers(spec, _ds_requests()["code"], not_exported=DS_NOT_EXPORTED, renames=renames)


def test_guard_code_datasource_round_trip_fails_when_a_field_is_omitted():
    # `docs_generation` omitted: UpdateIndexRequest defaults it to False, which the svn update then writes.
    before, (index, repo), _recorder, _spec_doc = _roundtrip_code_datasource(
        "svn", "code", spec_edit=lambda s: _without(s, "docs_generation")
    )
    with pytest.raises(AssertionError):
        _assert_unchanged(before[0], index, DS_IGNORED)


# datasource: provider --------------------------------------------------------------------------------------------------


def _ds_provider() -> Any:
    from codemie.rest_api.models.index import IndexInfoProviderFields

    return _ds_index(
        "provider",
        setting_id=None,
        provider_fields=IndexInfoProviderFields(
            provider_id="provider-uuid-7",
            toolkit_id="toolkit-1",
            base_params={"url": "https://aice", "api_key": "ENCRYPTED-BLOB", "gen_id": "uuid-gen", "managed": "m"},
            create_params={"depth": "3"},
        ),
    )


@contextmanager
def _provider_persistence() -> Iterator[None]:
    from types import SimpleNamespace

    from codemie.rest_api.models.provider import (
        Provider,
        ProviderDataSourceSchemas,
        ProviderDataSourceTypeSchema,
        ProviderToolkitConfigParameter,
    )

    def param(name: str, kind: str) -> ProviderDataSourceTypeSchema.Parameter:
        return ProviderDataSourceTypeSchema.Parameter(
            name=name,
            description="",
            required=False,
            parameter_type=ProviderToolkitConfigParameter.ParameterType(kind),
        )

    schemas = ProviderDataSourceSchemas(
        id="provider-uuid-7",
        toolkit_id="toolkit-1",
        provider_name="AICE",
        name="AICE - toolkit",
        base_schema=ProviderDataSourceTypeSchema(
            description="",
            parameters=[
                param("url", "String"),
                param("api_key", "Secret"),
                param("gen_id", "UUID"),
                param("managed", "String"),
            ],
        ),
        create_schema=ProviderDataSourceTypeSchema(description="", parameters=[param("depth", "String")]),
    )
    toolkit = SimpleNamespace(toolkit_id="toolkit-1", get_managed_fields=lambda: {"managed": "m"})
    provider = SimpleNamespace(id="provider-uuid-7", name="AICE", provided_toolkits=[toolkit])

    def schema_service(prov: Any, user: Any = None) -> Any:
        return SimpleNamespace(schema_for=lambda toolkit_id, include_autofilled=False: schemas)

    exporter = "codemie.service.iac_export.exporters.datasource.provider"
    update = "codemie.service.provider.datasource.provider_datasource_update_service"
    with (
        patch.object(Provider, "get_by_id", return_value=provider),
        patch(f"{exporter}.ProviderDatasourceSchemaService", side_effect=schema_service),
        patch(f"{update}.ProviderDatasourceSchemaService", side_effect=schema_service),
    ):
        yield


def _ds_provider_body(spec: dict[str, Any]) -> dict[str, Any]:
    """Document -> provider update body: a flat dict of the codemie fields and the toolkit parameters. Secret
    parameters (exported as env references) are sent as the mask."""
    secrets = {"api_key"}
    settings = {k: (MASK if k in secrets else v) for k, v in spec.get("settings", {}).items()}
    codemie = {
        "name": spec["name"],
        "description": spec.get("description"),
        "project_space_visible": spec["shared_with_project"],
    }
    return {**codemie, "project_name": DS_PROJECT, **settings}


def _roundtrip_provider_datasource(spec_edit: Callable[[dict], dict] = lambda s: s) -> tuple[dict, Any, dict]:
    index = _ds_provider()
    before = _snapshot(index, DS_IGNORED)
    with _ds_persistence([index], _Recorder()), _provider_persistence():
        spec = spec_edit(_ds_export(index))
        response = _ds_client().put(f"/v1/index/provider/{DS_INDEX_ID}", json=_ds_provider_body(spec))
    assert response.status_code == 200, response.text
    return before, index, spec


def test_provider_datasource_round_trip_leaves_entity_unchanged():
    before, index, spec = _roundtrip_provider_datasource()
    assert (spec["type"], spec["provider_name"]) == ("provider", "AICE")
    _assert_unchanged(before, index, DS_IGNORED)
    assert index.provider_fields.base_params["api_key"] == "ENCRYPTED-BLOB"  # the mask kept the stored secret


def test_provider_datasource_spec_covers_every_update_field():
    from codemie.service.provider.datasource.provider_datasource_base_service import ProviderDatasourceBaseService

    index = _ds_provider()
    with _ds_persistence([index], _Recorder()), _provider_persistence():
        spec = _ds_export(index)
    codemie_fields = {
        DS_RENAMES.get(f, f) for f in ProviderDatasourceBaseService.CODEMIE_FIELDS_MAPPING
    } - DS_NOT_EXPORTED
    assert codemie_fields <= set(spec)
    # url / depth / api_key are the schema fields; gen_id (UUID) and `managed` are server-managed
    assert set(spec["settings"]) == {"url", "api_key", "depth"}


def test_guard_provider_datasource_round_trip_fails_when_a_field_is_omitted():
    # `description` omitted from the document: the provider update writes None over the stored description.
    before, index, _spec = _roundtrip_provider_datasource(lambda s: _without(s, "description"))
    with pytest.raises(AssertionError):
        _assert_unchanged(before, index, DS_IGNORED)
