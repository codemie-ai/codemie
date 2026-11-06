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

"""Assistant exporter: the envelope's AssistantSpec shape, names instead of ids, env references for secrets.

Integration references are the integration's alias in `settings`; MCP secrets are `env:` references inside the server
`config` (and the `mcp_connect_url` / `command` / `arguments` fields) where the envelope's credential resolver
collects them. Inside those fields every literal value becomes a reference by position (`redact_positional*`):
the resolver walks them regardless of what they hold, so name/shape heuristics would leave real secrets in the
document; only a pure template (`{{...}}` / `${...}`) is left literal. Everything the envelope cannot carry is left
out (see "Deliberately non-exportable" in the task spec).
"""

from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

from codemie.core.ability import Action
from codemie.rest_api.models.assistant import Assistant, MCPServerDetails, PromptVariable, ToolDetails, ToolKitDetails
from codemie.rest_api.models.settings import SettingsBase
from codemie.rest_api.routers.utils import check_user_can_access_assistant, get_assistant_by_id_or_raise
from codemie.service.iac_export.credential_ref import env_ref
from codemie.service.iac_export.document import ExportDocument, ExportError, ExportMeta
from codemie.service.iac_export.redaction import (
    redact_free_form,
    redact_positional,
    redact_positional_list,
    redact_positional_map,
    require_server_name,
)
from codemie.service.iac_export.references import ExportScope, NameResolver, reference_access


class ContextSpec(BaseModel):
    context_type: str
    name: str


class ToolSpec(BaseModel):
    name: str
    label: str | None = None
    settings_config: bool = False
    user_description: str | None = None
    settings: str | None = None  # integration alias
    auto_credentials_lookup: bool = True


class ToolkitSpec(BaseModel):
    toolkit: str
    tools: list[ToolSpec] = Field(default_factory=list)
    label: str = ""
    settings_config: bool = False
    is_external: bool = False
    settings: str | None = None  # integration alias
    auto_credentials_lookup: bool = True


class McpConfigSpec(BaseModel):
    command: str | None = None
    url: str | None = None
    args: list[str] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    type: str | None = None
    auth_token: str | None = None
    single_usage: bool = False
    tools: list[str] | None = None
    audience: str | None = None
    auth_config: dict[str, Any] | None = None


class McpServerSpec(BaseModel):
    name: str
    description: str | None = None
    enabled: bool = True
    use_custom_config: bool = False
    config: McpConfigSpec | None = None
    mcp_connect_url: str | None = None
    tools_tokens_size_limit: int | None = None
    command: str | None = None
    arguments: str | None = None
    settings: str | None = None  # integration alias
    integration_alias: str | None = None
    mcp_connect_auth_token: str | None = None  # integration alias
    resolve_dynamic_values_in_arguments: bool = False
    tools: list[str] | None = None


class PromptVariableSpec(BaseModel):
    key: str
    description: str | None = None
    default_value: str = ""


class ToolPermissionsSpec(BaseModel):
    tool_call_policy: str
    allow_override: bool = True


class AssistantSpec(BaseModel):
    name: str
    description: str
    icon_url: str | None = None
    system_prompt: str
    project: str
    context: list[ContextSpec] = Field(default_factory=list)
    llm_model_type: str
    toolkits: list[ToolkitSpec] = Field(default_factory=list)
    conversation_starters: list[str] = Field(default_factory=list)
    shared: bool = True
    is_react: bool = False
    is_global: bool | None = None
    categories: list[str] = Field(default_factory=list)  # category names
    slug: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    tools_tokens_size_limit: int | None = None
    mcp_servers: list[McpServerSpec] = Field(default_factory=list)
    assistant_ids: list[str] = Field(default_factory=list)  # assistant names
    enabled_builtin_subagents: list[str] = Field(default_factory=list)
    skill_ids: list[str] = Field(default_factory=list)  # skill names
    hedging_config: dict[str, Any] | None = None
    interactive_enabled: bool = False
    enable_image_generation: bool | None = None
    file_attachment_enabled: bool | None = None
    prompt_variables: list[PromptVariableSpec] = Field(default_factory=list)
    tool_permissions: ToolPermissionsSpec | None = None


def _setting_id(setting: SettingsBase | None) -> str | None:
    return str(setting.id) if setting is not None and setting.id else None


def _alias(setting: SettingsBase | None, names: dict[str, str]) -> str | None:
    sid = _setting_id(setting)
    return names.get(sid) if sid else None


def _collect_integration_ids(assistant: Assistant) -> list[str]:
    settings: list[SettingsBase | None] = []
    for toolkit in assistant.toolkits:
        settings.append(toolkit.settings)
        settings.extend(tool.settings for tool in toolkit.tools)
    for server in assistant.mcp_servers:
        settings.extend([server.settings, server.mcp_connect_auth_token])
    return sorted({sid for sid in map(_setting_id, settings) if sid})


def _tool_spec(tool: ToolDetails, names: dict[str, str]) -> ToolSpec:
    return ToolSpec(
        name=tool.name,
        label=tool.label,
        settings_config=tool.settings_config,
        user_description=tool.user_description,
        settings=_alias(tool.settings, names),
        auto_credentials_lookup=tool.auto_credentials_lookup,
    )


def _toolkit_spec(toolkit: ToolKitDetails, names: dict[str, str]) -> ToolkitSpec:
    return ToolkitSpec(
        toolkit=toolkit.toolkit,
        tools=[_tool_spec(t, names) for t in toolkit.tools],
        label=toolkit.label,
        settings_config=toolkit.settings_config,
        is_external=bool(toolkit.is_external),
        settings=_alias(toolkit.settings, names),
        auto_credentials_lookup=toolkit.auto_credentials_lookup,
    )


def _auth_config(auth_config: dict[str, Any] | None, ident: str, prefix: str) -> dict[str, Any] | None:
    """The OAuth config as stored; one that holds a credential cannot be carried (the envelope never resolves it)."""
    if auth_config is not None and redact_free_form(auth_config, ident, "probe") != auth_config:
        raise ExportError(f"Assistant '{ident}' MCP server '{prefix}' holds a credential in auth_config")
    return auth_config


def _mcp_config_spec(server: MCPServerDetails, ident: str) -> McpConfigSpec | None:
    config = server.config
    if config is None:
        return None
    prefix = server.name
    return McpConfigSpec(
        command=redact_positional(config.command, ident, f"{prefix}_CONFIG_COMMAND"),
        url=redact_positional(config.url, ident, f"{prefix}_URL"),
        args=redact_positional_list(config.args, ident, f"{prefix}_CONFIG_ARGS") or [],
        headers=redact_positional_map(dict(sorted((config.headers or {}).items())), ident, f"{prefix}_HEADER"),
        env=redact_positional_map(dict(sorted((config.env or {}).items())), ident, f"{prefix}_ENV"),
        type=config.type,
        auth_token=env_ref(ident, f"{prefix}_AUTH_TOKEN") if config.auth_token else None,
        single_usage=bool(config.single_usage),
        tools=config.tools,
        audience=config.audience,
        auth_config=_auth_config(config.auth_config, ident, prefix),
    )


def _mcp_server_spec(server: MCPServerDetails, ident: str, names: dict[str, str]) -> McpServerSpec:
    if server.enabled and not (server.config or server.command or server.mcp_connect_url):
        raise ExportError(
            f"MCP server '{server.name}' of assistant '{ident}' is only a catalog link; "
            "the document format cannot carry it without a platform id"
        )
    return McpServerSpec(
        name=server.name,
        description=server.description,
        enabled=server.enabled,
        use_custom_config=server.use_custom_config,
        config=_mcp_config_spec(server, ident),
        mcp_connect_url=redact_positional(server.mcp_connect_url, ident, f"{server.name}_MCP_CONNECT_URL"),
        tools_tokens_size_limit=server.tools_tokens_size_limit,
        command=redact_positional(server.command, ident, f"{server.name}_COMMAND"),
        arguments=redact_positional(server.arguments, ident, f"{server.name}_ARGUMENTS"),
        settings=_alias(server.settings, names),
        integration_alias=server.integration_alias,
        mcp_connect_auth_token=_alias(server.mcp_connect_auth_token, names),
        resolve_dynamic_values_in_arguments=server.resolve_dynamic_values_in_arguments,
        tools=server.tools,
    )


def _prompt_variable_spec(var: PromptVariable) -> PromptVariableSpec:
    # The envelope never resolves a reference inside a prompt variable (no sensitive flag, no credential slot),
    # so an env: reference here would be literal, unresolvable text applied straight into the stored default.
    # Jira decision (amended): export an empty default instead, which is clearly not a value an operator should
    # apply blind rather than text that merely looks like a resolved secret.
    default_value = "" if var.is_sensitive else var.default_value
    return PromptVariableSpec(key=var.key, description=var.description, default_value=default_value)


def _scalar_fields(a: Assistant) -> dict[str, Any]:
    return {
        "name": a.name,
        "description": a.description,
        "icon_url": a.icon_url,
        "system_prompt": a.system_prompt,
        "project": a.project,
        "context": [ContextSpec(context_type=c.context_type.value, name=c.name) for c in a.context or []],
        "llm_model_type": a.llm_model_type,
        "conversation_starters": list(a.conversation_starters or []),
        "shared": a.shared,
        "is_react": bool(a.is_react),
        "is_global": a.is_global,
        "slug": a.slug,
        "temperature": a.temperature,
        "top_p": a.top_p,
        "tools_tokens_size_limit": a.tools_tokens_size_limit,
        "enabled_builtin_subagents": [s.value for s in a.enabled_builtin_subagents or []],
        "hedging_config": a.hedging_config.model_dump(mode="json", exclude_none=True) if a.hedging_config else None,
        "interactive_enabled": bool(a.interactive_enabled),
        "enable_image_generation": a.enable_image_generation,
        "file_attachment_enabled": a.file_attachment_enabled,
        "tool_permissions": (
            ToolPermissionsSpec(
                tool_call_policy=a.tool_permissions.tool_call_policy.value,
                allow_override=a.tool_permissions.allow_override,
            )
            if a.tool_permissions
            else None
        ),
    }


def _resolve_references(resolver: NameResolver, a: Assistant) -> tuple[list[str], list[str], list[str], dict[str, str]]:
    scope = ExportScope(project=a.project)
    category_ids = a.categories or []
    sub_ids = a.assistant_ids or []
    skill_ids = a.skill_ids or []
    categories = resolver.resolve_many("category", category_ids, scope)
    subs = resolver.resolve_many("assistant", sub_ids, scope)
    skills = resolver.resolve_many("skill", skill_ids, scope)
    integrations = resolver.resolve_many("integration", _collect_integration_ids(a), scope)
    return (
        [categories[i] for i in category_ids],
        [subs[i] for i in sub_ids],
        [skills[i] for i in skill_ids],
        integrations,
    )


def _check_server_names(a: Assistant, ident: str) -> None:
    """MCP server names key the env references, so each must be present and unique."""
    names = [require_server_name(m.name, ident) for m in a.mcp_servers or []]
    duplicate = next((n for n, c in Counter(names).items() if c > 1), None)
    if duplicate is not None:
        raise ExportError(f"Assistant '{ident}' has more than one MCP server named '{duplicate}'")


def _build_spec(a: Assistant, resolver: NameResolver) -> AssistantSpec:
    ident = a.slug or a.name
    if not a.llm_model_type:
        raise ExportError(f"Assistant '{ident}' has no LLM model type; the document format requires one")
    _check_server_names(a, ident)
    categories, subs, skills, integrations = _resolve_references(resolver, a)
    return AssistantSpec(
        **_scalar_fields(a),
        categories=categories,
        assistant_ids=subs,
        skill_ids=skills,
        toolkits=[_toolkit_spec(t, integrations) for t in a.toolkits or []],
        mcp_servers=[_mcp_server_spec(m, ident, integrations) for m in a.mcp_servers or []],
        prompt_variables=[_prompt_variable_spec(v) for v in a.prompt_variables or []],
    )


def _by_name(item: Any) -> Any:
    if isinstance(item, dict):
        return item.get("name") or item.get("toolkit") or item.get("key")
    return item


_SORT_KEYS = {
    "toolkits": lambda t: t["toolkit"],
    "tools": _by_name,
    "mcp_servers": _by_name,
    "context": lambda c: (c["context_type"], c["name"]),
    "prompt_variables": lambda v: v["key"],
    "assistant_ids": _by_name,
    "skill_ids": _by_name,
    "categories": _by_name,
    "enabled_builtin_subagents": _by_name,
}


class AssistantExporter:
    def __init__(self, resolver: NameResolver | None = None):
        self._resolver = resolver

    def export(self, id: str, user: Any) -> ExportDocument:
        # 404 as the catalog read path. Exporting needs WRITE (owner, project admin, admin): the document carries the
        # whole definition (system prompt, MCP command and args) in a form meant to be copied into version control.
        assistant = get_assistant_by_id_or_raise(id)
        check_user_can_access_assistant(user, assistant, "export", Action.WRITE)
        spec = _build_spec(assistant, self._resolver or NameResolver(access=reference_access(user), user_id=user.id))
        return ExportDocument(
            meta=ExportMeta(kind="assistant", name=assistant.name),
            spec=spec,
            sort_keys=_SORT_KEYS,
        )
