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

"""Confluence datasource (envelope type `knowledge_base_confluence`)."""

from codemie.rest_api.models.index import IndexInfo
from codemie.service.iac_export.exporters.datasource.base import (
    DatasourceSpec,
    ExportContext,
    require_block,
    register_datasource_exporter,
)

_TYPE = "knowledge_base_confluence"
_FLAGS = (
    "include_restricted_content",
    "include_archived_content",
    "include_attachments",
    "include_comments",
    "keep_markdown_format",
    "keep_newlines",
    "max_pages",
    "pages_per_request",
)


class ConfluenceSpec(DatasourceSpec):
    cql: str | None = None
    include_restricted_content: bool | None = None
    include_archived_content: bool | None = None
    include_attachments: bool | None = None
    include_comments: bool | None = None
    keep_markdown_format: bool | None = None
    keep_newlines: bool | None = None
    max_pages: int | None = None
    pages_per_request: int | None = None


class ConfluenceExporter:
    def build_spec(self, index: IndexInfo, ctx: ExportContext) -> ConfluenceSpec:
        info = require_block(index, index.confluence, "Confluence")
        extra = {"cql": info.cql, **{f: getattr(info, f) for f in _FLAGS}}
        return ConfluenceSpec(**ctx.common_fields(index, _TYPE), **extra)


register_datasource_exporter(_TYPE, ConfluenceExporter())
