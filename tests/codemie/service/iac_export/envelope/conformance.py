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

"""Offline check that an export document parses in the IaC document envelope (EPMCDME-15309).

``envelope_schema.json`` is generated from the envelope's pydantic specs (see ``regenerate_envelope_schema.py``) and
committed, so this repo never imports the other one. The JSON Schema carries field names, nesting, required fields,
enums, ``additionalProperties: false`` (the envelope forbids unknown fields) and the datasource ``type`` discriminator.
What a JSON Schema cannot express is added here: ``CredentialRef`` values must match the envelope's reference format.
"""

import json
import re
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA = json.loads((Path(__file__).with_name("envelope_schema.json")).read_text())
# codemie_iac.credentials: <known source>:<identifier>; "env" is the only source today.
CREDENTIAL_REF = re.compile(r"^env:[A-Za-z0-9_]+$")


def _is_dict(value: Any) -> bool:
    return isinstance(value, dict)


def schema_errors(document: dict[str, Any]) -> list[str]:
    """Violations as ``<json path>: <message>``; values are never echoed beyond jsonschema's own message."""
    errors = _document_errors(document)
    errors += [
        f"meta{e.json_path[1:]}: {e.message}"
        for e in jsonschema.Draft202012Validator(SCHEMA["meta"]).iter_errors(document.get("meta"))
    ]
    kind = (document.get("meta") or {}).get("kind")
    spec_schema = SCHEMA["specs"].get(kind)
    if spec_schema is None:
        return [*errors, f"meta.kind: unknown kind {kind!r}"]
    spec = document.get("spec")
    branch = _discriminated_branch(spec_schema, spec)
    if isinstance(branch, str):
        return [*errors, branch]
    validator = jsonschema.Draft202012Validator(branch)
    errors += [f"spec{e.json_path[1:]}: {e.message}" for e in validator.iter_errors(spec)]
    errors += _credential_ref_errors(kind, document.get("spec") or {})
    return errors


def _document_errors(document: dict[str, Any]) -> list[str]:
    """`Document` is `extra="forbid"` with required `version`, `meta` and `spec`."""
    keys = set(document)
    errors = [f"{key}: missing" for key in ("version", "meta", "spec") if key not in keys]
    errors += [f"{key}: extra fields not permitted" for key in sorted(keys - {"version", "meta", "spec"})]
    if "version" in keys and not isinstance(document["version"], str):
        errors.append("version: not a string")
    return errors


def _discriminated_branch(schema: dict[str, Any], spec: Any) -> dict[str, Any] | str:
    """The schema a spec must satisfy: for a discriminated union (datasource) the branch its ``type`` selects, so errors
    name the offending field of that one spec class instead of 'no match in union'. A string is an error message."""
    discriminator = schema.get("discriminator")
    if not discriminator:
        return schema
    prop = discriminator["propertyName"]
    target = discriminator["mapping"].get(spec.get(prop) if _is_dict(spec) else None)
    if target is None:
        return f"spec.{prop}: missing or unknown discriminator"
    return {"$ref": target, "$defs": schema["$defs"]}


def _credential_ref_errors(kind: str, spec: dict[str, Any]) -> list[str]:
    refs: list[tuple[str, Any]] = [
        (f"spec.credential_values[{i}].ref", e.get("ref"))
        for i, e in enumerate(spec.get("credential_values") or [])
        if _is_dict(e)
    ]
    for i, server in enumerate(_mcp_servers(kind, spec)):
        config = server.get("config")
        if _is_dict(config) and config.get("auth_token") is not None:
            refs.append((f"mcp_servers[{i}].config.auth_token", config["auth_token"]))
    return [
        f"{path}: not a credential reference"
        for path, ref in refs
        if not (isinstance(ref, str) and CREDENTIAL_REF.fullmatch(ref))
    ]


def _mcp_servers(kind: str, spec: dict[str, Any]) -> list[dict[str, Any]]:
    if kind == "assistant":
        return [s for s in spec.get("mcp_servers") or [] if _is_dict(s)]
    if kind == "workflow":
        assistants = (spec.get("workflow") or {}).get("assistants") or {}
        return [s for a in assistants.values() if _is_dict(a) for s in a.get("mcp_servers") or [] if _is_dict(s)]
    return []


def assert_conforms(document: dict[str, Any]) -> None:
    errors = schema_errors(document)
    assert not errors, "document does not parse in the IaC envelope:\n" + "\n".join(errors)
