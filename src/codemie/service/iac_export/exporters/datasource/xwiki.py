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

"""xWiki datasource (envelope type `knowledge_base_xwiki`)."""

from codemie.rest_api.models.index import IndexInfo
from codemie.service.iac_export.exporters.datasource.base import (
    DatasourceSpec,
    ExportContext,
    require_block,
    register_datasource_exporter,
)

_TYPE = "knowledge_base_xwiki"


class XWikiSpec(DatasourceSpec):
    space: str | None = None
    wiki: str | None = None


class XWikiExporter:
    def build_spec(self, index: IndexInfo, ctx: ExportContext) -> XWikiSpec:
        info = require_block(index, index.xwiki, "XWiki")
        return XWikiSpec(
            **ctx.common_fields(index, _TYPE),
            space=info.space,
            wiki=info.wiki,
        )


register_datasource_exporter(_TYPE, XWikiExporter())
