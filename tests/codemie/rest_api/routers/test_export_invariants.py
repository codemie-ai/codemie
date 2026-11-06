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

"""Cross-cutting invariants of GET /v1/export/{kind}/{id}, over every kind and every credential-bearing surface.

For each case the entity is stored with recognisable planted ids (uuids in every id slot) and planted secrets
(in every credential-bearing slot), then exported through the real router:

1. two exports are byte-identical, also after the stored list order is shuffled;
2. the body holds no planted id, no uuid at all, no planted secret (checked on the raw bytes), no mask string,
   no server-managed key (created_by / timestamps / state / ...), and every reference is the referenced name.

Rule for workflow-local ids: `id` / `assistant_id` / `tool_id` / `state_id` values that exist only inside the
workflow definition's own graph (assistants, states, tools) are allowed; they are short local labels, never a
stored entity id. Stored entity ids (assistant, skill, datasource, integration, MCP catalog, nested workflow)
must not appear.
"""

import json
import re
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import CreatedByUser, UserEntity
from codemie.rest_api.routers import export as export_router
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.iac_export.exporters.assistant import AssistantExporter
from codemie.service.iac_export.exporters.datasource import DATASOURCE_EXPORTERS, DatasourceDispatchExporter
from codemie.service.iac_export.exporters.integration import IntegrationExporter
from codemie.service.iac_export.exporters.skill import SkillExporter
from codemie.service.iac_export.exporters.workflow import WorkflowExporter
from codemie.service.iac_export.references import NameEntry, NameResolver
from tests.codemie.service.iac_export.envelope.conformance import assert_conforms

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
MASKS = ("*" * 8, "*" * 10)
FORBIDDEN_KEYS = {
    "id",
    "created_by",
    "creator",
    "user_id",
    "created_date",
    "update_date",
    "updated_date",
    "last_reindex_date",
    "date",
    "created_at",
    "updated_at",
    "timestamp",
    "state",
    "error",
    "setting_hash",
}
# Keys that may legitimately carry workflow-local graph labels (never stored entity ids).
WORKFLOW_LOCAL_KEYS = {"id", "assistant_id", "tool_id", "state_id", "state_ids", "custom_node_id"}


def _pid(n: int) -> str:
    """A recognisable planted stored id."""
    return str(uuid.UUID(int=(0xDEADBEEF << 64) | n))


def _admin() -> User:
    user = User(id=_pid(1), username="admin")
    user.is_admin = True
    return user


@dataclass
class Case:
    kind: str
    entity_id: str
    exporter: Any
    persistence: Callable[[], Any]
    ids: list[str]  # planted stored ids: none may appear in the body
    secrets: list[str]  # planted secret values: none may appear in the raw bytes
    names: list[str]  # reference names that must appear in the body
    shuffle: Callable[[], None] = lambda: None


def _resolver(entries: dict[str, list[tuple[str, str]]]) -> NameResolver:
    return NameResolver(lambda kind, scope: [NameEntry(i, n) for i, n in entries.get(kind, [])])


def _get(case: Case) -> bytes:
    with patch.object(export_router, "EXPORTERS", {case.kind: case.exporter}):
        app = FastAPI()
        app.include_router(export_router.router)

        @app.exception_handler(ExtendedHTTPException)
        async def _handler(request: Request, exc: ExtendedHTTPException):
            return JSONResponse(status_code=exc.code, content={"error": {"message": exc.message}})

        app.dependency_overrides[authenticate] = _admin
        response = TestClient(app, raise_server_exceptions=True).get(f"/v1/export/{case.kind}/{case.entity_id}")
    assert response.status_code == 200, response.text
    return response.content


# ---------------------------------------------------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------------------------------------------------


def _walk(node: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], str | None, Any]]:
    """Yield (path, key, value) for every dict entry and list item."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield path, key, value
            yield from _walk(value, (*path, key))
    elif isinstance(node, list):
        for item in node:
            yield path, None, item
            yield from _walk(item, path)


def scan_body(body: bytes, case: Case) -> None:
    doc = json.loads(body)
    assert_conforms(doc)  # parses in the IaC document envelope (all kinds, every registered datasource type)
    text = body.decode("utf-8")
    for secret in case.secrets:
        assert secret not in text, f"secret {secret!r} leaked"
    for planted in case.ids:
        assert planted not in text, f"stored id {planted} leaked"
    assert not UUID_RE.findall(text), f"uuid-shaped value in body: {UUID_RE.findall(text)}"
    for mask in MASKS:
        assert mask not in text, "mask string in body"
    for path, key, _value in _walk(doc):
        if key is None:
            continue
        local_ok = case.kind == "workflow" and key in WORKFLOW_LOCAL_KEYS and "workflow" in path
        assert key not in FORBIDDEN_KEYS or local_ok, f"forbidden key {key!r} at {'/'.join(path)}"
    for name in case.names:
        assert name in text, f"reference name {name!r} missing from body"


# ---------------------------------------------------------------------------------------------------------------------
# integration
# ---------------------------------------------------------------------------------------------------------------------

OPENAPI = '{"openapi":"3.0.0","info":{"title":"t"}}'


def _integration_case() -> Case:
    from codemie_tools.base.models import CredentialTypes

    from codemie.rest_api.models.settings import CredentialValues, ProjectSetting, Settings, SettingType
    from codemie.service.settings.settings import SettingsService

    def build() -> Settings:
        return Settings(
            id=_pid(10),
            user_id=_pid(11),
            created_by=CreatedByUser(id=_pid(12), username="u", name="U"),
            project_name="demo",
            alias="My Jira",
            credential_type=CredentialTypes.JIRA,
            credential_values=[
                CredentialValues(key="url", value="https://x.example"),
                CredentialValues(key="token", value="SECRET-INT-TOKEN"),
                CredentialValues(key="password", value="SECRET-INT-PASSWORD"),
                CredentialValues(key="openapi_spec", value=OPENAPI),
            ],
            setting_type=SettingType.PROJECT,
        )

    setting = build()

    @contextmanager
    def persistence() -> Iterator[None]:
        with (
            patch.object(Settings, "get_by_id", return_value=setting),
            patch.object(SettingsService, "get_setting_ability", side_effect=lambda *a, **k: ProjectSetting(build())),
            patch.object(SettingsService, "is_budget_integration_hidden_from", return_value=False),
        ):
            yield

    def shuffle() -> None:
        setting.credential_values.reverse()

    return Case(
        kind="integration",
        entity_id=setting.id,
        exporter=IntegrationExporter(),
        persistence=persistence,
        ids=[_pid(10), _pid(11), _pid(12)],
        secrets=["SECRET-INT-TOKEN", "SECRET-INT-PASSWORD"],
        names=["My Jira", "env:MY_JIRA_TOKEN"],
        shuffle=shuffle,
    )


# ---------------------------------------------------------------------------------------------------------------------
# skill
# ---------------------------------------------------------------------------------------------------------------------


def _skill_case() -> Case:
    from codemie.rest_api.models.assistant import ToolDetails, ToolKitDetails
    from codemie.rest_api.models.skill import Skill, SkillCategory, SkillVisibility

    skill = Skill(
        id=_pid(20),
        name="code-reviewer",
        description="Reviews code",
        content="# Review\n" + "Check the diff carefully, line by line, and report every problem you find. " * 2,
        project="demo",
        visibility=SkillVisibility.PROJECT,
        categories=[SkillCategory.CODE_REVIEW.value],
        toolkits=[
            ToolKitDetails(toolkit="git", tools=[ToolDetails(name="diff"), ToolDetails(name="log")]),
            ToolKitDetails(toolkit="jira", tools=[ToolDetails(name="search")]),
        ],
        mcp_servers=[],
        enabled_builtin_subagents=[],
        created_by=CreatedByUser(id=_pid(21), username="u", name="U"),
    )

    @contextmanager
    def persistence() -> Iterator[None]:
        with patch("codemie.service.skill_service.SkillService.get_skill_by_id", return_value=_detail(skill)):
            yield

    def shuffle() -> None:
        skill.toolkits.reverse()
        for toolkit in skill.toolkits:
            toolkit.tools.reverse()

    return Case(
        kind="skill",
        entity_id=skill.id,
        exporter=SkillExporter(),
        persistence=persistence,
        ids=[_pid(20), _pid(21)],
        secrets=[],
        names=["code-reviewer"],
        shuffle=shuffle,
    )


def _detail(skill: Any) -> Any:
    from codemie.rest_api.models.skill import SkillDetailResponse

    return SkillDetailResponse.model_validate(
        {**skill.model_dump(mode="json"), "user_abilities": [], "assistants_count": 0}
    )


# ---------------------------------------------------------------------------------------------------------------------
# assistant
# ---------------------------------------------------------------------------------------------------------------------

SKILL_IDS = {"alpha-skill": _pid(30), "beta-skill": _pid(31)}
SUB_ASSISTANT_ID = _pid(32)
CATEGORY_ID = _pid(33)
INTEGRATION_IDS = {"Toolkit Env": _pid(40), "Tool Env": _pid(41), "Docs Env": _pid(42), "Docs Token": _pid(43)}


def _integration_ref(name: str, secret: str) -> Any:
    from codemie_tools.base.models import CredentialTypes

    from codemie.rest_api.models.settings import CredentialValues, SettingsBase

    return SettingsBase(
        id=INTEGRATION_IDS[name],
        user_id=_pid(44),
        project_name="demo",
        alias=name,
        credential_type=CredentialTypes.JIRA,
        credential_values=[CredentialValues(key="token", value=secret)],
    )


def _assistant_case() -> Case:
    from codemie.rest_api.models.assistant import (
        Assistant,
        Context,
        ContextType,
        MCPServerDetails,
        PromptVariable,
        ToolDetails,
        ToolKitDetails,
    )
    from codemie.service.mcp.models import MCPServerConfig

    assistant = Assistant(
        id=_pid(50),
        name="Helper",
        slug="helper",
        description="Helps",
        system_prompt="Be {{tone}} with {{api_key}}",
        project="demo",
        llm_model_type="gpt-4",
        context=[Context(context_type=ContextType.CODE, name="repo"), Context(context_type=ContextType.CODE, name="a")],
        skill_ids=list(SKILL_IDS.values()),
        assistant_ids=[SUB_ASSISTANT_ID],
        categories=[CATEGORY_ID],
        toolkits=[
            ToolKitDetails(
                toolkit="vcs",
                tools=[
                    ToolDetails(name="zeta", settings=_integration_ref("Tool Env", "SECRET-TOOL-CRED")),
                    ToolDetails(name="alpha"),
                ],
                settings=_integration_ref("Toolkit Env", "SECRET-TOOLKIT-CRED"),
            ),
            ToolKitDetails(toolkit="another", tools=[]),
        ],
        mcp_servers=[
            MCPServerDetails(
                name="docs",
                use_custom_config=True,
                mcp_config_id=_pid(51),
                settings=_integration_ref("Docs Env", "SECRET-MCP-SETTINGS"),
                mcp_connect_auth_token=_integration_ref("Docs Token", "SECRET-MCP-CONNECT-TOKEN"),
                config=MCPServerConfig(
                    url="https://docs.example/mcp",
                    env={"TOKEN": "SECRET-MCP-ENV"},
                    headers={"Authorization": "SECRET-MCP-HEADER"},
                    auth_token="SECRET-MCP-AUTH",
                ),
            ),
            MCPServerDetails(name="a-tools", use_custom_config=True, command="uvx"),
        ],
        prompt_variables=[
            PromptVariable(key="tone", default_value="friendly"),
            PromptVariable(key="api_key", default_value="SECRET-PROMPT-VAR", is_sensitive=True),
        ],
        created_by=CreatedByUser(id=_pid(52), username="u", name="U"),
        creator=_pid(53),
    )
    resolver = _resolver(
        {
            "category": [(CATEGORY_ID, "Support")],
            "assistant": [(SUB_ASSISTANT_ID, "Sub Helper")],
            "skill": [(i, n) for n, i in SKILL_IDS.items()],
            "integration": [(i, n) for n, i in INTEGRATION_IDS.items()],
        }
    )
    module = "codemie.service.iac_export.exporters.assistant"

    @contextmanager
    def persistence() -> Iterator[None]:
        with (
            patch(f"{module}.get_assistant_by_id_or_raise", return_value=assistant),
            patch(f"{module}.check_user_can_access_assistant"),
        ):
            yield

    def shuffle() -> None:
        for items in (assistant.skill_ids, assistant.toolkits, assistant.mcp_servers, assistant.context):
            items.reverse()
        assistant.toolkits[0].tools.reverse()
        assistant.prompt_variables.reverse()

    secrets = [
        "SECRET-TOOL-CRED",
        "SECRET-TOOLKIT-CRED",
        "SECRET-MCP-SETTINGS",
        "SECRET-MCP-CONNECT-TOKEN",
        "SECRET-MCP-ENV",
        "SECRET-MCP-HEADER",
        "SECRET-MCP-AUTH",
        "SECRET-PROMPT-VAR",
    ]
    return Case(
        kind="assistant",
        entity_id=assistant.id,
        exporter=AssistantExporter(resolver=resolver),
        persistence=persistence,
        ids=[_pid(n) for n in (30, 31, 32, 33, 40, 41, 42, 43, 44, 50, 51, 52, 53)],
        secrets=secrets,
        names=["Support", "Sub Helper", "Toolkit Env", "Tool Env", "Docs Env", "Docs Token"],
        shuffle=shuffle,
    )


# ---------------------------------------------------------------------------------------------------------------------
# workflow
# ---------------------------------------------------------------------------------------------------------------------

WF_IDS = {"assistant": _pid(60), "skill_a": _pid(61), "skill_b": _pid(62), "ds": _pid(63), "cfg": _pid(64)}
WF_IDS |= {"set": _pid(65), "tok": _pid(66), "nested": _pid(67), "tool_cfg": _pid(68), "tool_set": _pid(69)}


def _workflow_yaml() -> str:
    def settings(id_: str, alias: str, secret: str) -> dict[str, Any]:
        return {
            "id": id_,
            "user_id": _pid(70),
            "project_name": "demo",
            "alias": alias,
            "credential_type": "Jira",
            "credential_values": [{"key": "token", "value": secret}],
        }

    config = {
        "command": "npx",
        "env": {"TOKEN": "SECRET-WF-ENV"},
        "headers": {"Authorization": "SECRET-WF-HEADER"},
        "auth_token": "SECRET-WF-AUTH",
    }
    definition = {
        "assistants": [
            {
                "id": "a1",
                "assistant_id": WF_IDS["assistant"],
                "model": "gpt-4",
                "skill_ids": [WF_IDS["skill_b"], WF_IDS["skill_a"]],
                "datasource_ids": [WF_IDS["ds"]],
                "mcp_servers": [
                    {
                        "name": "docs",
                        "config": config,
                        "mcp_config_id": WF_IDS["cfg"],
                        "settings": settings(WF_IDS["set"], "Docs Env", "SECRET-WF-SETTINGS"),
                        "mcp_connect_auth_token": settings(WF_IDS["tok"], "Docs Token", "SECRET-WF-CONNECT"),
                    }
                ],
            }
        ],
        "tools": [
            {
                "id": "t1",
                "tool": "mcp_tool",
                # A tool-level MCP server has no credential slot the engine resolves, so a literal credential in
                # `config` here would be an export error (CR 2aca0289), not something to redact; this fixture's
                # `config` carries none. The `settings` alias is still exercised: it must resolve to the
                # integration's name without ever exposing the integration's own stored credential value.
                "mcp_server": {
                    "name": "inline",
                    "config": {"command": "npx"},
                    "mcp_config_id": WF_IDS["tool_cfg"],
                    "settings": settings(WF_IDS["tool_set"], "Tool Env", "SECRET-WF-TOOL-SETTINGS"),
                },
            }
        ],
        "states": [
            {"id": "s1", "assistant_id": "a1", "task": "Do", "next": {"state_id": "end"}},
        ],
    }
    return yaml.safe_dump(definition, sort_keys=False)


def _workflow_case() -> Case:
    from codemie.core.workflow_models import WorkflowConfig

    workflow = WorkflowConfig(
        id=_pid(80),
        name="Release Flow",
        description="Ships releases",
        project="demo",
        mode="Sequential",
        yaml_config=_workflow_yaml(),
        created_by=UserEntity(user_id=_pid(81), username="u", name="U"),
        categories=[_pid(82)],
    )
    workflow.parse_execution_config()
    resolver = _resolver(
        {
            "assistant": [(WF_IDS["assistant"], "Helper")],
            "integration": [
                (WF_IDS["set"], "Docs Env"),
                (WF_IDS["tok"], "Docs Token"),
                (WF_IDS["tool_set"], "Tool Env"),
            ],
            "skill": [(WF_IDS["skill_a"], "Alpha Skill"), (WF_IDS["skill_b"], "Beta Skill")],
            "datasource": [(WF_IDS["ds"], "Docs Index")],
        }
    )

    @contextmanager
    def persistence() -> Iterator[None]:
        with patch(
            "codemie.service.iac_export.exporters.workflow.workflow_service.get_workflow", return_value=workflow
        ):
            yield

    secrets = [f"SECRET-WF-{s}" for s in ("ENV", "HEADER", "AUTH", "SETTINGS", "CONNECT", "TOOL-SETTINGS")]
    return Case(
        kind="workflow",
        entity_id=workflow.id,
        exporter=WorkflowExporter(resolver=resolver),
        persistence=persistence,
        ids=[_pid(n) for n in (60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 80, 81, 82)],
        secrets=secrets,
        names=["Helper", "Docs Env", "Tool Env"],
    )


# ---------------------------------------------------------------------------------------------------------------------
# datasources
# ---------------------------------------------------------------------------------------------------------------------

DS_ID, DS_SETTING = _pid(90), _pid(91)
DS_SECRET = "SECRET-DS-ACCESS-TOKEN"
NO_INTEGRATION = {"provider"}  # no integration is planted on this type


def _ds_index(index_type: str, **kw: Any) -> Any:
    from codemie.rest_api.models.index import IndexInfo

    values = {
        "id": DS_ID,
        "project_name": "demo",
        "repo_name": "my-ds",
        "index_type": index_type,
        "description": "desc",
        "setting_id": DS_SETTING,
        "created_by": CreatedByUser(id=_pid(93), username="u", name="U"),
        "error": True,
        "text": "status",
    }
    values.update(kw)
    return IndexInfo(**values)


def _ds_nested(index_type: str) -> dict[str, Any]:
    from codemie.rest_api.models import index as m

    nested = {
        "knowledge_base_confluence": ("confluence", m.ConfluenceIndexInfo(cql="space = X")),
        "knowledge_base_jira": ("jira", m.JiraIndexInfo(jql="project = X")),
        "knowledge_base_xray": ("xray", m.XrayIndexInfo(jql="project = X")),
        "knowledge_base_azure_devops_wiki": (
            "azure_devops_wiki",
            m.AzureDevOpsWikiIndexInfo(wiki_query="/docs", wiki_name="main"),
        ),
        "knowledge_base_azure_devops_work_item": (
            "azure_devops_work_item",
            m.AzureDevOpsWorkItemIndexInfo(wiql_query="SELECT 1"),
        ),
        "knowledge_base_xwiki": ("xwiki", m.XWikiIndexInfo(space="KB", wiki="main")),
        "knowledge_base_sharepoint": (
            "sharepoint",
            m.SharePointIndexInfo(
                site_url="https://t.sharepoint.com/sites/x",
                auth_type="oauth_custom",
                oauth_client_id="client-1",
                oauth_tenant_id="tenant-1",
                access_token=DS_SECRET,
            ),
        ),
    }
    if index_type not in nested:
        return {}
    key, value = nested[index_type]
    return {key: value}


def _datasource_case(index_type: str) -> Case:
    from codemie.rest_api.models.index import IndexInfo, IndexInfoProviderFields
    from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService

    extra: dict[str, Any] = _ds_nested(index_type)
    if index_type in ("code", "summary", "chunk-summary", "knowledge_base_git_faq"):
        extra |= {"vcs_type": "git", "link": "https://git.example.com/r.git", "branch": "main"}
    secrets = [DS_SECRET] if index_type == "knowledge_base_sharepoint" else []
    if index_type == "provider":
        extra["provider_fields"] = IndexInfoProviderFields(
            provider_id=_pid(94),
            toolkit_id="toolkit-1",
            base_params={"url": "https://aice", "api_key": "SECRET-PROVIDER-KEY", "gen_id": _pid(95)},
            create_params={"depth": "3"},
        )
        extra["setting_id"] = None
        secrets = ["SECRET-PROVIDER-KEY"]
    index = _ds_index(index_type, **extra)

    @contextmanager
    def persistence() -> Iterator[None]:
        with ExitStack() as stack:
            stack.enter_context(patch.object(IndexInfo, "get_by_id", return_value=index))
            stack.enter_context(
                patch.object(
                    SchedulerSettingsService,
                    "get_scheduler_settings_for_datasources",
                    return_value={DS_ID: {"cron_expression": "0 3 * * *", "timezone": "UTC"}},
                )
            )
            if index_type == "provider":
                stack.enter_context(_provider_persistence())
            yield

    resolver = _resolver({"integration": [(DS_SETTING, "My Integration")]})
    ids = [DS_ID, DS_SETTING, _pid(93), _pid(94), _pid(95)]
    return Case(
        kind="datasource",
        entity_id=DS_ID,
        exporter=DatasourceDispatchExporter(resolver=resolver),
        persistence=persistence,
        ids=ids,
        secrets=secrets,
        names=["my-ds"] + ([] if index_type in NO_INTEGRATION else ["My Integration"]),
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
        id=_pid(94),
        toolkit_id="toolkit-1",
        provider_name="AICE",
        name="AICE - toolkit",
        base_schema=ProviderDataSourceTypeSchema(
            description="", parameters=[param("url", "String"), param("api_key", "Secret"), param("gen_id", "UUID")]
        ),
        create_schema=ProviderDataSourceTypeSchema(description="", parameters=[param("depth", "String")]),
    )
    toolkit = SimpleNamespace(toolkit_id="toolkit-1", get_managed_fields=dict)
    provider = SimpleNamespace(id=_pid(94), name="AICE", provided_toolkits=[toolkit])
    module = "codemie.service.iac_export.exporters.datasource.provider"
    with (
        patch.object(Provider, "get_by_id", return_value=provider),
        patch(
            f"{module}.ProviderDatasourceSchemaService",
            side_effect=lambda prov, user=None: SimpleNamespace(schema_for=lambda t, include_autofilled=False: schemas),
        ),
    ):
        yield


# ---------------------------------------------------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------------------------------------------------

DS_TYPES = sorted(DATASOURCE_EXPORTERS)

CASES: dict[str, Callable[[], Case]] = {
    "integration": _integration_case,
    "skill": _skill_case,
    "assistant": _assistant_case,
    "workflow": _workflow_case,
    **{f"datasource-{t}": (lambda t=t: _datasource_case(t)) for t in DS_TYPES},
}


@pytest.fixture(params=sorted(CASES))
def case(request: pytest.FixtureRequest) -> Iterator[Case]:
    built = CASES[request.param]()
    with built.persistence():
        yield built


def test_two_exports_are_byte_identical(case: Case):
    assert _get(case) == _get(case)


def test_export_is_stable_under_shuffled_stored_order(case: Case):
    first = _get(case)
    case.shuffle()

    assert _get(case) == first


def test_body_has_no_ids_secrets_masks_or_server_managed_keys(case: Case):
    scan_body(_get(case), case)


def test_every_datasource_type_is_covered():
    assert {"code", "knowledge_base_sharepoint", "provider", "knowledge_base_jira"} <= set(DS_TYPES)
    assert len(DS_TYPES) == 13


def test_integration_exports_every_credential_as_a_reference_openapi_spec_included():
    case = _integration_case()
    with case.persistence():
        body = _get(case)

    entries = {e["key"]: e["ref"] for e in json.loads(body)["spec"]["credential_values"]}
    assert set(entries) == {"url", "token", "password", "openapi_spec"}
    assert all(ref.startswith("env:") for ref in entries.values())
    assert OPENAPI.encode() not in body


def test_scan_detects_a_planted_leak():
    case = _skill_case()
    leaky = json.dumps({"meta": {"kind": "skill", "name": "x"}, "spec": {"id": _pid(20)}}).encode()

    with pytest.raises(AssertionError):
        scan_body(leaky, case)
