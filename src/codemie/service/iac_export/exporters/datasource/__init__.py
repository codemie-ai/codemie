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

"""Datasource export: reads the index, checks access, dispatches by index type.

To support another type, add a module with a `DatasourceExporter` that calls
`register_datasource_exporter("<index_type>", ...)` and import it below.
"""

from typing import Any

from fastapi import status

from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.routers.utils import raise_forbidden
from codemie.service.iac_export.document import ExportDocument, ExportMeta
from codemie.service.iac_export.exporters.datasource import (  # noqa: F401 (register)
    azure_devops_wiki,
    azure_devops_work_item,
    code,
    confluence,
    git_faq,
    google,
    jira,
    provider,
    sharepoint,
    xray,
    xwiki,
)
from codemie.service.iac_export.exporters.datasource.base import (
    DATASOURCE_EXPORTERS,
    DatasourceExporter,
    ExportContext,
    get_datasource_exporter,
    register_datasource_exporter,
)
from codemie.service.iac_export.references import NameResolver, reference_access

__all__ = [
    "DATASOURCE_EXPORTERS",
    "DatasourceDispatchExporter",
    "DatasourceExporter",
    "register_datasource_exporter",
]


def _load_index(id: str) -> IndexInfo:
    try:
        return IndexInfo.get_by_id(id)
    except KeyError as e:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=f"Cannot find datasource by given id: {id}",
            details=f"The datasource with id {id} could not be found in the system.",
        ) from e


class DatasourceDispatchExporter:
    """Top-level `datasource` kind exporter (an `Exporter`)."""

    def __init__(self, resolver: NameResolver | None = None):
        self._resolver = resolver

    def export(self, id: str, user: Any) -> ExportDocument:
        index = _load_index(id)
        if not Ability(user).can(Action.READ, index):
            raise_forbidden("export")
        exporter = get_datasource_exporter(index.index_type)
        spec = exporter.build_spec(
            index,
            ExportContext(
                user=user, resolver=self._resolver or NameResolver(access=reference_access(user), user_id=user.id)
            ),
        )
        return ExportDocument(
            meta=ExportMeta(kind="datasource", name=index.repo_name), spec=spec, verbatim=frozenset({"settings"})
        )
