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

"""Stored workflow `yaml_config` to the envelope's typed `WorkflowDefinition`, and back.

The stored text is a list-based document (`assistants`, `tools`, `custom_nodes`, `states`, plus engine switches the
envelope does not model). The envelope wants `assistants` as a mapping keyed by id, catalog assistants referenced by
NAME in `assistant_id`, and only the fields its pydantic models declare (they forbid unknown fields). `to_definition`
projects the stored form onto exactly that; `render` is the inverse used by round-trip checks.
"""

from collections.abc import Mapping
from typing import Any

import yaml

from codemie.service.iac_export.document import ExportError

# Fields of the envelope's workflow models (codemie_iac -> codemie_sdk.builders.workflow.definition). A test compares
# these tables with the committed envelope schema, so a drift in the envelope fails loudly.
ENVELOPE_FIELDS: dict[str, list[str]] = {
    "assistant": [
        "id",
        "assistant_id",
        "name",
        "model",
        "temperature",
        "system_prompt",
        "limit_tool_output_tokens",
        "tools",
        "datasource_ids",
        "exclude_extra_context_tools",
        "mcp_servers",
        "skill_ids",
    ],
    "assistant_tool": ["name", "integration_alias"],
    "mcp_server": [
        "name",
        "enabled",
        "config",
        "command",
        "mcp_config_id",
        "description",
        "mcp_connect_url",
        "integration_alias",
    ],
    "mcp_config": [
        "command",
        "url",
        "args",
        "headers",
        "env",
        "type",
        "auth_token",
        "single_usage",
        "tools",
        "audience",
        "auth_config",
    ],
    "tool": [
        "id",
        "tool",
        "tool_args",
        "integration_alias",
        "tool_result_json_pointer",
        "trace",
        "mcp_server",
        "resolve_dynamic_values_in_response",
        "input_key",
        "tokens_size_limit",
        "tool_access",
    ],
    "custom_node": ["id", "custom_node_id", "name", "model", "system_prompt", "config"],
    "state": [
        "id",
        "assistant_id",
        "custom_node_id",
        "tool_id",
        "workflow_id",
        "task",
        "next",
        "retry_policy",
        "interrupt_before",
        "output_schema",
        "tool_args",
        "resolve_dynamic_values_in_prompt",
        "result_as_human_message",
        "finish_iteration",
    ],
    "next": [
        "state_id",
        "state_ids",
        "iter_key",
        "output_key",
        "condition",
        "switch",
        "store_in_context",
        "include_in_llm_history",
        "clear_prior_messages",
        "clear_context_store",
        "reset_keys_in_context_store",
        "include_in_iterator_context",
        "append_to_context",
    ],
    "condition": ["expression", "then", "otherwise"],
    "switch": ["cases", "default"],
    "switch_case": ["condition", "state_id"],
    "retry_policy": ["initial_interval", "backoff_factor", "max_interval", "max_attempts"],
}

# Envelope fields that hold platform ids the envelope never resolves back, so they would be written verbatim as ids.
# They are not exported. (`assistant_id`, `skill_ids` and `datasource_ids` of a workflow assistant are exported as
# names; the engine resolves `assistant_id` today and the other two with EPMCDME-16198.)
UNRESOLVED_ID_FIELDS: dict[str, frozenset[str]] = {
    "mcp_server": frozenset({"mcp_config_id"}),
    "state": frozenset({"workflow_id"}),
}

# Top-level engine switches of the stored yaml that the envelope's `WorkflowDefinition` models.
ENGINE_SWITCHES = (
    "enable_summarization_node",
    "max_iteration_key_output_limit",
    "messages_limit_before_summarization",
    "tokens_limit_before_summarization",
)

# An explicit null here is meaningful: the envelope default for the field is not null.
_KEEP_NULL = frozenset({"limit_tool_output_tokens"})
_STATE_TARGETS = ("assistant_id", "custom_node_id", "tool_id")


def exported_fields(model: str) -> tuple[str, ...]:
    skipped = UNRESOLVED_ID_FIELDS.get(model, frozenset())
    return tuple(f for f in ENVELOPE_FIELDS[model] if f not in skipped)


def load(yaml_config: str | None) -> dict[str, Any]:
    loaded = yaml.safe_load(yaml_config or "")
    return loaded if isinstance(loaded, dict) else {}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [e for e in value or [] if isinstance(e, dict)]


def _pick(entry: Mapping[str, Any], model: str) -> dict[str, Any]:
    """The entry restricted to the model's exported fields, in stored order; nulls dropped unless meaningful."""
    allowed = exported_fields(model)
    return {k: v for k, v in entry.items() if k in allowed and (v is not None or k in _KEEP_NULL)}


def extract_assistant_ids(stored: dict[str, Any]) -> list[str]:
    """Catalog assistant ids referenced by the workflow's assistants, in first-seen order."""
    return list(dict.fromkeys(a["assistant_id"] for a in _dicts(stored.get("assistants")) if a.get("assistant_id")))


def _agent_ids(stored: dict[str, Any], field: str) -> list[str]:
    ids = (i for a in _dicts(stored.get("assistants")) for i in a.get(field) or [] if isinstance(i, str) and i)
    return list(dict.fromkeys(ids))


def extract_skill_ids(stored: dict[str, Any]) -> list[str]:
    """Skill ids attached to the workflow's assistants, in first-seen order."""
    return _agent_ids(stored, "skill_ids")


def extract_datasource_ids(stored: dict[str, Any]) -> list[str]:
    """Datasource ids attached to the workflow's assistants, in first-seen order."""
    return _agent_ids(stored, "datasource_ids")


def _snapshot_id(value: Any) -> str | None:
    return value.get("id") if isinstance(value, dict) else None


def _servers(stored: dict[str, Any]) -> list[dict[str, Any]]:
    servers = [s for a in _dicts(stored.get("assistants")) for s in _dicts(a.get("mcp_servers"))]
    return servers + [t["mcp_server"] for t in _dicts(stored.get("tools")) if isinstance(t.get("mcp_server"), dict)]


def extract_integration_ids(stored: dict[str, Any]) -> list[str]:
    """Integration ids embedded as `settings` snapshots on MCP servers that carry no `integration_alias`."""
    ids = (_snapshot_id(s.get("settings")) for s in _servers(stored) if not s.get("integration_alias"))
    return list(dict.fromkeys(i for i in ids if i))


def _tool_access_lists(stored: dict[str, Any]) -> list[dict[str, Any]]:
    """Every `assistant_tool`-shaped entry: an assistant's own `tools`, and a workflow tool's `tool_access`."""
    from_assistants = (t for a in _dicts(stored.get("assistants")) for t in _dicts(a.get("tools")))
    from_tools = (t for tool in _dicts(stored.get("tools")) for t in _dicts(tool.get("tool_access")))
    return [*from_assistants, *from_tools]


def extract_integration_aliases(stored: dict[str, Any]) -> list[str]:
    """Literal `integration_alias` strings already embedded in the stored entity (MCP servers, `tools` and
    `tool_access` entries), in first-seen order. These bypass id resolution entirely (there is no settings
    snapshot to look up), so `extract_integration_ids` never sees them and they never reached the ambiguity
    check; a caller must still check them for uniqueness in scope (CR 0bcb7377)."""
    from_servers = (s.get("integration_alias") for s in _servers(stored))
    from_tools = (t.get("integration_alias") for t in _tool_access_lists(stored))
    return list(dict.fromkeys(a for a in (*from_servers, *from_tools) if a))


def _server(server: dict[str, Any], integrations: Mapping[str, str], owner: str) -> dict[str, Any]:
    out = _pick(server, "mcp_server")
    if isinstance(server.get("config"), dict):
        out["config"] = _pick(server["config"], "mcp_config")
    alias = server.get("integration_alias") or integrations.get(_snapshot_id(server.get("settings")) or "")
    if alias:
        out["integration_alias"] = alias
    if out.get("enabled", True) and not (out.get("config") is not None or out.get("command")):
        raise ExportError(
            f"MCP server '{server.get('name')}' of '{owner}' is only a catalog link; "
            "the document format cannot carry it without a platform id"
        )
    return out


def _named(ids: Any, names: Mapping[str, str]) -> list[str]:
    return [names[i] for i in ids if isinstance(i, str) and i]


def _assistant(
    entry: dict[str, Any],
    names: Mapping[str, str],
    integrations: Mapping[str, str],
    attached: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    out = _pick(entry, "assistant")
    owner = str(entry["id"])
    if entry.get("assistant_id"):
        out["assistant_id"] = names[entry["assistant_id"]]
    for field in ("skill_ids", "datasource_ids"):
        if entry.get(field):
            out[field] = _named(entry[field], attached[field])
    if entry.get("tools") is not None:
        out["tools"] = [_pick(t, "assistant_tool") for t in _dicts(entry["tools"])]
    if entry.get("mcp_servers") is not None:
        out["mcp_servers"] = [_server(s, integrations, owner) for s in _dicts(entry["mcp_servers"])]
    if not any(out.get(k) for k in ("system_prompt", "assistant_id", "name")):
        raise ExportError(f"Workflow assistant '{owner}' has no prompt, name or catalog assistant")
    return out


def _assistants(
    stored: dict[str, Any],
    names: Mapping[str, str],
    integrations: Mapping[str, str],
    attached: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for entry in _dicts(stored.get("assistants")):
        key = entry.get("id")
        if not key or key in result:
            raise ExportError(f"Workflow assistant id '{key}' is missing or duplicated")
        result[str(key)] = _assistant(entry, names, integrations, attached)
    return result


def _next(nxt: dict[str, Any]) -> dict[str, Any]:
    out = _pick(nxt, "next")
    if isinstance(nxt.get("condition"), dict):
        out["condition"] = _pick(nxt["condition"], "condition")
    if isinstance(nxt.get("switch"), dict):
        cases = [_pick(c, "switch_case") for c in _dicts(nxt["switch"].get("cases"))]
        out["switch"] = {**_pick(nxt["switch"], "switch"), "cases": cases}
    return out


def _state(state: dict[str, Any]) -> dict[str, Any]:
    out = _pick(state, "state")
    targets = [k for k in _STATE_TARGETS if state.get(k) is not None]
    if len(targets) != 1 or state.get("workflow_id") is not None:
        raise ExportError(
            f"Workflow state '{state.get('id')}' does not run an assistant, tool or custom node; "
            "sub-workflow states cannot be exported"
        )
    if isinstance(state.get("next"), dict):
        out["next"] = _next(state["next"])
    if isinstance(state.get("retry_policy"), dict):
        out["retry_policy"] = _pick(state["retry_policy"], "retry_policy")
    return out


def _tool(tool: dict[str, Any], integrations: Mapping[str, str]) -> dict[str, Any]:
    out = _pick(tool, "tool")
    if tool.get("tool_access") is not None:
        if not isinstance(tool["tool_access"], list):
            raise ExportError(f"Workflow tool '{tool.get('id')}' has a tool_access the document format cannot carry")
        out["tool_access"] = [_pick(t, "assistant_tool") for t in _dicts(tool["tool_access"])]
    if isinstance(tool.get("mcp_server"), dict):
        out["mcp_server"] = _server(tool["mcp_server"], integrations, str(tool.get("id")))
    return out


def to_definition(
    stored: dict[str, Any],
    names: Mapping[str, str],
    integrations: Mapping[str, str],
    header: Mapping[str, Any],
    skills: Mapping[str, str] | None = None,
    datasources: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The envelope's `WorkflowDefinition` as a plain dict. `names` maps catalog assistant ids to assistant names,
    `integrations` maps snapshot integration ids to aliases, `skills` and `datasources` map the ids attached to the
    workflow's assistants to names, `header` carries name/description/mode/shared/icon_url."""
    attached = {"skill_ids": skills or {}, "datasource_ids": datasources or {}}
    return {
        **header,
        **{k: stored[k] for k in ENGINE_SWITCHES if stored.get(k) is not None},
        "assistants": _assistants(stored, names, integrations, attached),
        "tools": [_tool(t, integrations) for t in _dicts(stored.get("tools"))],
        "custom_nodes": [_pick(n, "custom_node") for n in _dicts(stored.get("custom_nodes"))],
        "states": [_state(s) for s in _dicts(stored.get("states"))],
    }


def render(
    definition: dict[str, Any],
    ids: Mapping[str, str],
    skill_ids: Mapping[str, str] | None = None,
    datasource_ids: Mapping[str, str] | None = None,
) -> str:
    """Assistant, skill and datasource names back to ids, then the stored list-based text. Round-trip checks only."""
    assistants = []
    for key, assistant in definition.get("assistants", {}).items():
        entry = {"id": key, **assistant}
        if entry.get("assistant_id"):
            entry["assistant_id"] = ids[entry["assistant_id"]]
        for field, back in (("skill_ids", skill_ids or {}), ("datasource_ids", datasource_ids or {})):
            if entry.get(field):
                entry[field] = [back[n] for n in entry[field]]
        assistants.append({k: v for k, v in entry.items() if v not in ([], None) or k in _KEEP_NULL})
    switches = {k: definition[k] for k in ENGINE_SWITCHES if definition.get(k) is not None}
    sections = {
        "assistants": assistants,
        "tools": definition.get("tools", []),
        "custom_nodes": definition.get("custom_nodes", []),
        "states": definition.get("states", []),
    }
    return yaml.safe_dump({**switches, **{k: v for k, v in sections.items() if v}}, sort_keys=False)
