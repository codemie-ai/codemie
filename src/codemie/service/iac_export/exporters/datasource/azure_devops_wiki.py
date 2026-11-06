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

"""Azure DevOps wiki datasource (envelope type `knowledge_base_azure_devops_wiki`)."""

from codemie.rest_api.models.index import IndexInfo
from codemie.service.iac_export.exporters.datasource.base import (
    DatasourceSpec,
    ExportContext,
    require_block,
    register_datasource_exporter,
)

_TYPE = "knowledge_base_azure_devops_wiki"


class AzureDevOpsWikiSpec(DatasourceSpec):
    wiki_query: str | None = None
    wiki_name: str | None = None


class AzureDevOpsWikiExporter:
    def build_spec(self, index: IndexInfo, ctx: ExportContext) -> AzureDevOpsWikiSpec:
        info = require_block(index, index.azure_devops_wiki, "Azure DevOps wiki")
        return AzureDevOpsWikiSpec(
            **ctx.common_fields(index, _TYPE),
            wiki_query=info.wiki_query,
            wiki_name=info.wiki_name,
        )


register_datasource_exporter(_TYPE, AzureDevOpsWikiExporter())
