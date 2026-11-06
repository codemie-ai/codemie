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

"""Jira datasource (envelope type `knowledge_base_jira`)."""

from codemie.rest_api.models.index import IndexInfo
from codemie.service.iac_export.exporters.datasource.base import (
    DatasourceSpec,
    ExportContext,
    register_datasource_exporter,
)

_TYPE = "knowledge_base_jira"


class JiraSpec(DatasourceSpec):
    jql: str | None = None
    custom_fields: list[str] | None = None


class JiraExporter:
    def build_spec(self, index: IndexInfo, ctx: ExportContext) -> JiraSpec:
        jira = index.jira
        return JiraSpec(
            **ctx.common_fields(index, _TYPE),
            jql=jira.jql if jira else None,
            custom_fields=jira.custom_fields if jira else None,
        )


register_datasource_exporter(_TYPE, JiraExporter())
