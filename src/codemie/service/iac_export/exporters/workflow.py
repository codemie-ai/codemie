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

"""Workflow exporter: the envelope's WorkflowSpec (a typed definition plus credential references).

Catalog assistants are referenced by name, MCP secrets are `env:` references and the definition carries only what the
envelope's workflow models declare (see "Deliberately non-exportable" in the task spec). Inside an inline MCP
`config` / `mcp_connect_url` / `command`, every literal value becomes a reference by position (`redact_positional*`),
same as the assistant exporter; `tool_args`, state `tool_args` and custom-node `config` have no credential slot in
the envelope, so a credential there is refused rather than redacted (`_refuse_if_secret`).
"""

from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.workflow_models import WorkflowConfig
from codemie.rest_api.routers.utils import raise_access_denied, raise_not_found
from codemie.service.iac_export.credential_ref import env_ref
from codemie.service.iac_export.document import ExportDocument, ExportError, ExportMeta
from codemie.service.iac_export.redaction import (
    redact_arg_list,
    redact_free_form,
    redact_positional,
    redact_positional_list,
    redact_positional_map,
    require_server_name,
)
from codemie.service.iac_export.references import NameResolver, reference_access
from codemie.service.iac_export.workflow_converter import load, to_definition
from codemie.service.iac_export.workflow_references import resolve_references
from codemie.service.workflow_service import WorkflowService

workflow_service = WorkflowService()

_SECRET_MAPS = ("env", "headers")


class CredentialRefEntry(BaseModel):
    key: str
    ref: str


class WorkflowSpec(BaseModel):
    start_hint: str | None = None
    workflow: dict[str, Any]
    credential_values: list[CredentialRefEntry] = Field(default_factory=list)


def _as(value: Any, kind: type, redactor, *args) -> Any:
    """Redact a field of the expected type; a value of any other type cannot be judged and becomes a reference."""
    if value is None:
        return None
    if isinstance(value, kind):
        return redactor(value, *args)
    return env_ref(args[0], args[1])


def _redact_config(config: dict[str, Any], ident: str, prefix: str) -> dict[str, Any]:
    """Every literal value of an inline MCP config becomes an env reference by position; keys are kept."""
    out = dict(config)
    for field, kind, redactor in (
        ("url", str, redact_positional),
        ("command", str, redact_positional),
        ("args", list, redact_positional_list),
    ):
        if field in out:
            out[field] = _as(out[field], kind, redactor, ident, f"{prefix}_{field}")
    for field in _SECRET_MAPS:
        if field in out:
            out[field] = _as(out[field], dict, redact_positional_map, ident, f"{prefix}_{field}")
    if out.get("auth_token"):
        out["auth_token"] = env_ref(ident, f"{prefix}_auth_token")
    if out.get("auth_config") is not None:
        _refuse_if_secret(out["auth_config"], ident, f"auth_config of MCP server '{prefix}'")
    return out


def _redact_server(server: dict[str, Any], ident: str, owner: str) -> dict[str, Any]:
    prefix = f"{owner}_{require_server_name(server.get('name'), ident)}"
    out = dict(server)
    for field, redactor in (("mcp_connect_url", redact_positional), ("command", redact_positional)):
        if field in out:
            out[field] = _as(out[field], str, redactor, ident, f"{prefix}_{field}")
    if out.get("config") is not None:
        out["config"] = _as(out["config"], dict, _redact_config, ident, prefix)
    return out


def _check_server_names(definition: dict[str, Any], ident: str) -> None:
    """Server names key the env references, so two servers of one owner must not share a name.

    The owner is an assistant key or a tool id; one string used for both would make their references coincide.
    """
    shared = {str(t.get("id")) for t in definition["tools"] if t.get("mcp_server")} & {
        k for k, a in definition["assistants"].items() if a.get("mcp_servers")
    }
    if shared:
        raise ExportError(
            f"Workflow '{ident}' uses '{sorted(shared)[0]}' as both an assistant and a tool with MCP servers"
        )
    for key, assistant in definition["assistants"].items():
        names = [require_server_name(s.get("name"), ident) for s in assistant.get("mcp_servers") or []]
        duplicate = next((n for n, c in Counter(names).items() if c > 1), None)
        if duplicate is not None:
            raise ExportError(f"Workflow '{ident}' assistant '{key}' has more than one MCP server named '{duplicate}'")


def _refuse_if_secret(value: Any, ident: str, where: str) -> Any:
    """Free-form parts have no credential slot in the envelope: apply would write the reference text over the value."""
    if redact_free_form(value, ident, "probe") != value:
        raise ExportError(
            f"Workflow '{ident}' holds a credential in {where}; the document format has no credential slot there"
        )
    return value


def _refuse_positional_config_secrets(config: Any, ident: str, where: str) -> None:
    """`env` / `headers` carry arbitrary variable and header names, so `_refuse_if_secret`'s name/shape scan can
    miss one (review finding, auto_epmd-edp_vcs: `env: {DB_PASS: hunter2}` matched neither `is_secret_name` nor
    a token shape). An assistant's MCP config treats every value in these two maps as a credential by position,
    regardless of name (`_redact_config`, CR dfcf1994); a tool-level server has no slot the engine resolves at
    all, so the same positional judgement decides whether to refuse here too. `command` / `url` are not included:
    unlike an assistant's server, a tool-level one is refused rather than redacted, and those two are legitimately
    literal on almost every tool-level server (e.g. `command: npx`), so judging them positionally would refuse
    the overwhelming majority of credential-free servers instead of the rare credential-bearing one.

    `args` gets its own, narrower check (`redact_arg_list`, review finding, auto_epmd-edp_vcs): a flag and its
    value are separate list elements (`["--password", "hunter2"]`), and `_refuse_if_secret`'s scan judges each
    element alone, with no memory of the flag before it, so it never sees `--password` as naming the element
    after it. A single `command` string with the same words space-separated (`"run --password hunter2"`) has no
    such gap: `_SECRET_ARG` already matches a secret name followed by its value within one string. `redact_arg_list`
    is not applied positionally like `env`/`headers` above, because it already keeps ordinary flags and values
    (`--max-tokens 4096`, `-y @scope/server`) while still catching a secret-named flag's value and known token
    shapes — the same balance `test_benign_arguments_are_kept` pins for the assistant-level case.
    """
    if not isinstance(config, dict):
        return
    for field in _SECRET_MAPS:
        value = config.get(field)
        if isinstance(value, dict) and redact_positional_map(value, ident, "probe") != value:
            raise ExportError(
                f"Workflow '{ident}' holds a credential in config.{field} of {where}; "
                "the document format has no credential slot there"
            )
    args = config.get("args")
    if isinstance(args, list) and redact_arg_list(args, ident, "probe") != args:
        raise ExportError(
            f"Workflow '{ident}' holds a credential in config.args of {where}; "
            "the document format has no credential slot there"
        )


def _redact_tool(tool: dict[str, Any], ident: str) -> dict[str, Any]:
    """A tool-level MCP server has no credential slot the engine resolves (unlike an assistant's `mcp_servers`,
    which `_iter_servers` walks): a reference written there is never substituted, so a credential here is refused
    the same way `tool_args` already is (CR 2aca0289)."""
    out = dict(tool)
    owner = str(tool.get("id"))
    if tool.get("mcp_server"):
        name = tool["mcp_server"].get("name")
        where = f"MCP server '{name}' of tool '{owner}'"
        _refuse_positional_config_secrets(tool["mcp_server"].get("config"), ident, where)
        _refuse_if_secret(tool["mcp_server"], ident, where)
    if "tool_args" in out:
        _refuse_if_secret(out["tool_args"], ident, f"tool_args of tool '{owner}'")
    return out


def _redact_free_form_parts(definition: dict[str, Any], ident: str) -> dict[str, Any]:
    out = dict(definition)
    for node in definition.get("custom_nodes") or []:
        if "config" in node:
            _refuse_if_secret(node["config"], ident, f"config of custom node '{node.get('id')}'")
    for state in definition.get("states") or []:
        if "tool_args" in state:
            _refuse_if_secret(state["tool_args"], ident, f"tool_args of state '{state.get('id')}'")
    return out


def _redact_secrets(definition: dict[str, Any], ident: str) -> dict[str, Any]:
    _check_server_names(definition, ident)
    out = _redact_free_form_parts(definition, ident)
    out["assistants"] = {
        key: {**a, "mcp_servers": [_redact_server(s, ident, key) for s in a["mcp_servers"]]}
        if a.get("mcp_servers")
        else a
        for key, a in definition["assistants"].items()
    }
    out["tools"] = [_redact_tool(t, ident) for t in definition["tools"]]
    return out


def _auth_token_ref(server: dict[str, Any]) -> str | None:
    """The server's auth token reference; a config that is not an object was already replaced by a reference."""
    config = server.get("config")
    return config.get("auth_token") if isinstance(config, dict) and config.get("auth_token") else None


def _credential_entries(definition: dict[str, Any], workflow_name: str) -> list[CredentialRefEntry]:
    """One entry per assistant MCP server auth token, matching the envelope's `WorkflowSpec.from_entity`.

    Not tools: a tool-level MCP server is refused when it holds a credential (`_redact_tool`), since the engine
    never substitutes a reference there, so it can never reach this function carrying one.
    """
    return [
        CredentialRefEntry(key=f"{workflow_name}_{key}_{server['name']}", ref=ref)
        for key, assistant in definition["assistants"].items()
        for server in assistant.get("mcp_servers", [])
        if (ref := _auth_token_ref(server))
    ]


def _build_spec(wf: WorkflowConfig, resolver: NameResolver) -> WorkflowSpec:
    stored = load(wf.yaml_config)
    names, integrations, skills, datasources = resolve_references(resolver, stored, wf.project)
    header = {
        k: v
        for k, v in {
            "name": wf.name,
            "description": wf.description,
            "mode": wf.mode.value,
            "shared": wf.shared,
            "icon_url": wf.icon_url,
        }.items()
        if v is not None
    }
    definition = _redact_secrets(to_definition(stored, names, integrations, header, skills, datasources), wf.name)
    return WorkflowSpec(
        start_hint=wf.start_hint,
        workflow=definition,
        credential_values=_credential_entries(definition, wf.name),
    )


class WorkflowExporter:
    def __init__(self, resolver: NameResolver | None = None):
        self._resolver = resolver

    def export(self, id: str, user: Any) -> ExportDocument:
        # Same 404 as the read path, but export needs WRITE (owner, project admin, admin): the document carries the
        # whole definition (MCP command and args) in a form meant to be copied into version control, same as assistant
        # export (see AssistantExporter.export).
        try:
            workflow = workflow_service.get_workflow(id, user)
        except ExtendedHTTPException:
            raise  # 403/404 decided by the read path itself
        except (KeyError, IndexError):
            raise_not_found(resource_id=id, resource_type="Workflow")
        if not Ability(user).can(Action.WRITE, workflow):
            raise_access_denied("export")
        spec = _build_spec(workflow, self._resolver or NameResolver(access=reference_access(user), user_id=user.id))
        return ExportDocument(
            meta=ExportMeta(kind="workflow", name=workflow.name), spec=spec, verbatim=frozenset({"workflow"})
        )
