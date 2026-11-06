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

"""Xray datasource (envelope type `knowledge_base_xray`)."""

from codemie.rest_api.models.index import IndexInfo
from codemie.service.iac_export.exporters.datasource.base import (
    DatasourceSpec,
    ExportContext,
    register_datasource_exporter,
)
from codemie.service.iac_export.document import ExportError

_TYPE = "knowledge_base_xray"


class XraySpec(DatasourceSpec):
    jql: str
    embedding_model: str | None = None


class XrayExporter:
    def build_spec(self, index: IndexInfo, ctx: ExportContext) -> XraySpec:
        jql = index.xray.jql if index.xray else None
        if not jql:
            raise ExportError(f"Xray datasource '{index.repo_name}' has no JQL; the document format requires one")
        return XraySpec(**ctx.common_fields(index, _TYPE), jql=jql, embedding_model=index.embeddings_model)


register_datasource_exporter(_TYPE, XrayExporter())
