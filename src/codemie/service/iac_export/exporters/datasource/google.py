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

"""Google doc datasource (envelope type `llm_routing_google`)."""

from codemie.rest_api.models.index import IndexInfo
from codemie.service.iac_export.redaction import redact_link
from codemie.service.iac_export.exporters.datasource.base import (
    DatasourceSpec,
    ExportContext,
    register_datasource_exporter,
)

_TYPE = "llm_routing_google"


class GoogleSpec(DatasourceSpec):
    google_doc: str | None = None


class GoogleExporter:
    def build_spec(self, index: IndexInfo, ctx: ExportContext) -> GoogleSpec:
        return GoogleSpec(
            **ctx.common_fields(index, _TYPE),
            google_doc=redact_link(index.google_doc_link, index.repo_name, "google_doc"),
        )


register_datasource_exporter(_TYPE, GoogleExporter())
