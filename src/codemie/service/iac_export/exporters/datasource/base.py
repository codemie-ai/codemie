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

"""Datasource exporter contract, shared spec fields and the index-type registry.

Specs follow the envelope's datasource specs: a `type` discriminator, `shared_with_project`, the integration alias in
`setting_id`, and the target `project_name`. Guardrail assignments have no place in the envelope and are not exported.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel

from codemie.rest_api.models.index import IndexInfo
from codemie.service.iac_export.document import ExportError
from codemie.service.iac_export.references import ExportScope, NameResolver
from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService


class DatasourceSpec(BaseModel):
    """Fields shared by every datasource spec. Type modules subclass and add their own."""

    type: str
    name: str
    project_name: str
    description: str = ""
    shared_with_project: bool = False
    setting_id: str | None = None
    cron_expression: str | None = None
    timezone: str | None = None


def require_block(index: IndexInfo, info: Any, what: str) -> Any:
    """The type-specific block of a datasource; without it the document would carry no query or space."""
    if info is None:
        raise ExportError(f"Datasource '{index.repo_name}' has no {what} settings; the document format requires them")
    return info


@dataclass
class ExportContext:
    """What a datasource exporter may use: the requesting user and the reference resolver."""

    user: Any
    resolver: NameResolver

    def integration_alias(self, index: IndexInfo) -> str | None:
        if not index.setting_id:
            return None
        return self.resolver.resolve("integration", index.setting_id, ExportScope(project=index.project_name))

    def schedule(self, index: IndexInfo) -> dict[str, str | None]:
        owner = index.created_by.id if index.created_by and index.created_by.id else self.user.id
        found = SchedulerSettingsService.get_scheduler_settings_for_datasources(owner, [str(index.id)])
        entry = found.get(str(index.id)) or {}
        return {"cron_expression": entry.get("cron_expression"), "timezone": entry.get("timezone")}

    def common_fields(self, index: IndexInfo, type_: str, with_schedule: bool = True) -> dict[str, Any]:
        """Values for every `DatasourceSpec` field; type modules add the type-specific ones.

        `with_schedule=False` leaves the schedule out (and skips its lookup) for a type whose create/update request
        carries no schedule, so the real engine could never write it back.
        """
        return {
            "type": type_,
            "name": index.repo_name,
            "project_name": index.project_name,
            "description": index.description or "",
            "shared_with_project": bool(index.project_space_visible),
            "setting_id": self.integration_alias(index),
            **(self.schedule(index) if with_schedule else {}),
        }


class DatasourceExporter(Protocol):
    """Builds the `spec` for one family of index types."""

    def build_spec(self, index: IndexInfo, ctx: ExportContext) -> DatasourceSpec: ...


DATASOURCE_EXPORTERS: dict[str, DatasourceExporter] = {}


def register_datasource_exporter(index_type: str, exporter: DatasourceExporter) -> None:
    DATASOURCE_EXPORTERS[index_type] = exporter


def get_datasource_exporter(index_type: str) -> DatasourceExporter:
    exporter = DATASOURCE_EXPORTERS.get(index_type)
    if exporter is None:
        raise ExportError(f"Datasource type '{index_type}' is not supported for export")
    return exporter
