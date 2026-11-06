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

"""SharePoint datasource (envelope type `knowledge_base_sharepoint`).

The stored OAuth `access_token` is transient and has no place in the envelope: it is never exported.
"""

from typing import Literal

from codemie.rest_api.models.index import IndexInfo
from codemie.service.iac_export.redaction import url_has_credentials
from codemie.service.iac_export.exporters.datasource.base import (
    DatasourceSpec,
    ExportContext,
    register_datasource_exporter,
)
from codemie.service.iac_export.document import ExportError

_TYPE = "knowledge_base_sharepoint"


class SharePointSpec(DatasourceSpec):
    site_url: str
    include_pages: bool | None = None
    include_documents: bool | None = None
    include_lists: bool | None = None
    max_file_size_mb: int | None = None
    files_filter: str | None = None
    auth_type: Literal["integration", "oauth_codemie", "oauth_custom"] | None = None
    oauth_client_id: str | None = None
    oauth_tenant_id: str | None = None
    embedding_model: str | None = None


class SharePointExporter:
    def build_spec(self, index: IndexInfo, ctx: ExportContext) -> SharePointSpec:
        sp = index.sharepoint
        if sp is None or not sp.site_url:
            raise ExportError(f"SharePoint datasource '{index.repo_name}' has no site URL")
        if url_has_credentials(sp.site_url):
            # The envelope accepts only a plain https URL here, so a reference cannot stand in for it.
            raise ExportError(f"SharePoint datasource '{index.repo_name}' has credentials in its site URL")
        return SharePointSpec(
            **ctx.common_fields(index, _TYPE),
            site_url=sp.site_url,
            include_pages=sp.include_pages,
            include_documents=sp.include_documents,
            include_lists=sp.include_lists,
            max_file_size_mb=sp.max_file_size_mb,
            files_filter=sp.files_filter,
            auth_type=sp.auth_type,
            oauth_client_id=sp.oauth_client_id,
            oauth_tenant_id=sp.oauth_tenant_id,
            embedding_model=index.embeddings_model,
        )


register_datasource_exporter(_TYPE, SharePointExporter())
