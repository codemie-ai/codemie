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

"""Provider datasource (envelope type `provider`): provider name, toolkit id and the settings map.

The update is keyed by id, so the document carries the provider name, toolkit id and settings (no schedule: the
provider request has no such fields). Secret parameters are exported as environment references; auto-filled
(UUID) and toolkit-managed parameters are server-managed and omitted.
"""

import re
from itertools import pairwise
from typing import Any

from elasticsearch.exceptions import NotFoundError
from pydantic import Field

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.models.provider import Provider, ProviderToolkitConfigParameter
from codemie.service.iac_export.credential_ref import env_ref
from codemie.service.iac_export.redaction import redact_free_form
from codemie.service.iac_export.document import ExportError
from codemie.service.iac_export.exporters.datasource.base import (
    DatasourceSpec,
    ExportContext,
    register_datasource_exporter,
)
from codemie.service.provider.datasource.constants import AUTOFILLED_SCHEMA_PARAM_TYPES, PROVIDER_INDEX_TYPE
from codemie.service.provider.datasource.provider_datasource_schema_service import ProviderDatasourceSchemaService

_SECRET = ProviderToolkitConfigParameter.ParameterType.SECRET
# Document-level keys the envelope refuses inside `settings`.
_RESERVED_SETTINGS = frozenset({"name", "project_name", "setting_id", "description", "project_space_visible"})
# The envelope rejects a literal value under a secret-looking setting name even when the provider schema says the
# parameter is harmless, so such a name is always exported as a reference (mirrors codemie_iac datasource specs).
_SECRET_TOKENS = frozenset(
    {"password", "passwd", "secret", "token", "apikey", "credential", "credentials", "passphrase"}
)
_SECRET_PAIRS = frozenset(
    {("api", "key"), ("private", "key"), ("access", "key"), ("secret", "key"), ("auth", "key"), ("client", "secret")}
)


class ProviderSpec(DatasourceSpec):
    provider_name: str
    toolkit_id: str
    settings: dict[str, Any] = Field(default_factory=dict)


# Zero-width boundary (no quantifier precedes the lookahead), so there is nothing to backtrack over.
_ACRONYM_BOUNDARY = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")


def _key_tokens(key: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key)
    spaced = _ACRONYM_BOUNDARY.sub(" ", spaced)
    return [t.lower() for t in re.split(r"[^A-Za-z0-9]+", spaced) if t]


def _looks_secret(key: str) -> bool:
    tokens = _key_tokens(key)
    return any(t in _SECRET_TOKENS for t in tokens) or any(pair in _SECRET_PAIRS for pair in pairwise(tokens))


def _exported_params(params: dict, schema, managed: dict, repo_name: str) -> dict[str, Any]:
    by_name = {p.name: p for p in schema.parameters}
    result: dict[str, Any] = {}
    for key, value in params.items():
        param = by_name.get(key)
        if key in managed or (param is not None and param.parameter_type in AUTOFILLED_SCHEMA_PARAM_TYPES):
            continue
        # A param the schema does not know cannot be shown to be harmless: treat it as a secret.
        is_secret = param is None or param.parameter_type == _SECRET or _looks_secret(key)
        result[key] = env_ref(repo_name, key) if is_secret else redact_free_form(value, repo_name, key)
    return result


def _merged_params(fields, schema, managed: dict, repo_name: str) -> dict[str, Any]:
    """Base and create parameters as exported settings; one name in both groups is ambiguous and refused."""
    base = _exported_params(fields.base_params or {}, schema.base_schema, managed, repo_name)
    create = _exported_params(fields.create_params or {}, schema.create_schema, managed, repo_name)
    duplicate = sorted(set(base) & set(create))
    if duplicate:
        raise ExportError(
            f"Provider datasource '{repo_name}' has settings defined more than once (base and create): {duplicate}"
        )
    return {**base, **create}


def _provider_schema(provider: Provider, fields, index: IndexInfo, ctx: ExportContext):
    try:
        return ProviderDatasourceSchemaService(provider, ctx.user).schema_for(
            fields.toolkit_id, include_autofilled=True
        )
    except ExtendedHTTPException as e:
        raise ExportError(f"Provider schema of datasource '{index.repo_name}' is unavailable", e.code) from e


class ProviderExporter:
    def build_spec(self, index: IndexInfo, ctx: ExportContext) -> ProviderSpec:
        fields = index.provider_fields
        if fields is None:
            raise ExportError(f"Provider datasource '{index.repo_name}' has no provider configuration")
        try:
            provider = Provider.get_by_id(fields.provider_id)
        except NotFoundError as e:
            raise ExportError(f"Provider of datasource '{index.repo_name}' no longer exists") from e
        toolkit = next((t for t in provider.provided_toolkits if t.toolkit_id == fields.toolkit_id), None)
        if toolkit is None:
            raise ExportError(f"Provider toolkit of datasource '{index.repo_name}' no longer exists")
        schema = _provider_schema(provider, fields, index, ctx)
        exported = _merged_params(fields, schema, toolkit.get_managed_fields(), index.repo_name)
        clash = sorted(set(exported) & _RESERVED_SETTINGS)
        if clash:
            raise ExportError(f"Provider datasource '{index.repo_name}' has settings named like document keys: {clash}")
        return ProviderSpec(
            **ctx.common_fields(index, PROVIDER_INDEX_TYPE, with_schedule=False),
            provider_name=provider.name,
            toolkit_id=fields.toolkit_id,
            settings=dict(sorted(exported.items())),
        )


register_datasource_exporter(PROVIDER_INDEX_TYPE, ProviderExporter())
