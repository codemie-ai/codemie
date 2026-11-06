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

"""Code datasources: git is envelope type `code`, svn is its own envelope type `svn`."""

from codemie.rest_api.models.index import IndexInfo
from codemie.service.iac_export.exporters.datasource.base import (
    DatasourceSpec,
    ExportContext,
    register_datasource_exporter,
)
from codemie.service.iac_export.document import ExportError
from codemie.service.iac_export.redaction import text_has_credentials, url_has_credentials

SVN = "svn"


def _refuse_link_credential(link: str, repo_name: str, kind: str) -> str:
    """The envelope's credential resolver does not walk a datasource `link`, so a credential there has no slot
    apply resolves; refuse rather than emit a reference nobody substitutes (CR 2aca0289)."""
    if url_has_credentials(link) or text_has_credentials(link):
        raise ExportError(f"{kind} datasource '{repo_name}' link holds a credential; apply never resolves it there")
    return link


class CodeSpec(DatasourceSpec):
    link: str | None = None
    branch: str | None = None
    index_type: str | None = None
    files_filter: str | None = None
    embeddings_model: str | None = None
    summarization_model: str | None = None
    prompt: str | None = None
    docs_generation: bool | None = None


class SvnSpec(DatasourceSpec):
    link: str
    branch: str | None = None
    index_type: str | None = None
    files_filter: str | None = None
    embeddings_model: str | None = None
    prompt: str | None = None
    docs_generation: bool | None = None


class CodeExporter:
    def build_spec(self, index: IndexInfo, ctx: ExportContext) -> DatasourceSpec:
        if index.vcs_type == SVN:
            return self._svn(index, ctx)
        if not index.link:
            raise ExportError(f"Git datasource '{index.repo_name}' has no link; the document format requires one")
        return CodeSpec(
            **ctx.common_fields(index, "code"),
            link=_refuse_link_credential(index.link, index.repo_name, "Git"),
            branch=index.branch,
            index_type=index.index_type,
            files_filter=index.files_filter,
            embeddings_model=index.embeddings_model,
            summarization_model=index.summarization_model,
            prompt=index.prompt,
            docs_generation=index.docs_generation,
        )

    @staticmethod
    def _svn(index: IndexInfo, ctx: ExportContext) -> SvnSpec:
        if not index.link:
            raise ExportError(f"SVN datasource '{index.repo_name}' has no link; the document format requires one")
        return SvnSpec(
            **ctx.common_fields(index, SVN),
            link=_refuse_link_credential(index.link, index.repo_name, "SVN"),
            branch=index.branch,
            index_type=index.index_type,
            files_filter=index.files_filter,
            embeddings_model=index.embeddings_model,
            prompt=index.prompt,
            docs_generation=index.docs_generation,
        )


for _code_type in ("code", "summary", "chunk-summary"):
    register_datasource_exporter(_code_type, CodeExporter())
