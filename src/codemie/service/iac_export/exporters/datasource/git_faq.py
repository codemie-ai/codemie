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

"""Git FAQ datasource (envelope type `knowledge_base_git_faq`)."""

from codemie.rest_api.models.index import IndexInfo
from codemie.service.iac_export.redaction import redact_link
from codemie.service.iac_export.exporters.datasource.base import (
    DatasourceSpec,
    ExportContext,
    register_datasource_exporter,
)

_TYPE = "knowledge_base_git_faq"


class GitFaqSpec(DatasourceSpec):
    link: str | None = None
    branch: str | None = None
    files_filter: str | None = None
    embedding_model: str | None = None


class GitFaqExporter:
    def build_spec(self, index: IndexInfo, ctx: ExportContext) -> GitFaqSpec:
        return GitFaqSpec(
            **ctx.common_fields(index, _TYPE),
            link=redact_link(index.link, index.repo_name, "link"),
            branch=index.branch,
            files_filter=index.files_filter,
            embedding_model=index.embeddings_model,
        )


register_datasource_exporter(_TYPE, GitFaqExporter())
