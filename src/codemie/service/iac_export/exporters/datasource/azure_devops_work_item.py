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

"""Azure DevOps work item datasource (envelope type `knowledge_base_azure_devops_work_item`)."""

from codemie.rest_api.models.index import IndexInfo
from codemie.service.iac_export.exporters.datasource.base import (
    DatasourceSpec,
    ExportContext,
    require_block,
    register_datasource_exporter,
)

_TYPE = "knowledge_base_azure_devops_work_item"


class AzureDevOpsWorkItemSpec(DatasourceSpec):
    wiql_query: str | None = None


class AzureDevOpsWorkItemExporter:
    def build_spec(self, index: IndexInfo, ctx: ExportContext) -> AzureDevOpsWorkItemSpec:
        info = require_block(index, index.azure_devops_work_item, "Azure DevOps work item")
        return AzureDevOpsWorkItemSpec(**ctx.common_fields(index, _TYPE), wiql_query=info.wiql_query)


register_datasource_exporter(_TYPE, AzureDevOpsWorkItemExporter())
