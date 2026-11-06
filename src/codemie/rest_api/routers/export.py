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

"""Export API: GET /v1/export/{kind}/{id} returns a canonical `{meta, spec}` document."""

import yaml
from fastapi import APIRouter, Depends, Response, status
from pydantic import ValidationError

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.iac_export.credential_ref import env_ref_scope
from codemie.service.iac_export.document import ExportError, Exporter, to_canonical_bytes
from codemie.service.iac_export.exporters.assistant import AssistantExporter
from codemie.service.iac_export.exporters.category import CategoryExporter
from codemie.service.iac_export.exporters.datasource import DatasourceDispatchExporter
from codemie.service.iac_export.exporters.integration import IntegrationExporter
from codemie.service.iac_export.exporters.skill import SkillExporter
from codemie.service.iac_export.exporters.workflow import WorkflowExporter

router = APIRouter(
    tags=["Export"],
    prefix="/v1",
    dependencies=[Depends(authenticate)],
)

# kind -> Exporter, one per supported kind.
EXPORTERS: dict[str, Exporter] = {}


def register_exporter(kind: str, exporter: Exporter) -> None:
    EXPORTERS[kind] = exporter


register_exporter("assistant", AssistantExporter())
register_exporter("category", CategoryExporter())
register_exporter("datasource", DatasourceDispatchExporter())
register_exporter("integration", IntegrationExporter())
register_exporter("skill", SkillExporter())
register_exporter("workflow", WorkflowExporter())


def _error_locations(error: Exception) -> str:
    """Field paths of a validation error, without the offending values."""
    if isinstance(error, ValidationError):
        return ", ".join(".".join(str(part) for part in item["loc"]) for item in error.errors())
    return type(error).__name__


@router.get(
    "/export/{kind}/{id}",
    summary="Export an entity as a canonical document",
    description=(
        "Returns the entity as an IaC document (version, meta, spec) with names instead of ids and env: "
        "references instead of credentials. Kinds: assistant, workflow, datasource, integration, skill, category."
    ),
)
def export_entity(kind: str, id: str, user: User = Depends(authenticate)) -> Response:
    exporter = EXPORTERS.get(kind)
    if exporter is None:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=f"Unknown export kind '{kind}'",
            details=f"Supported kinds: {', '.join(sorted(EXPORTERS))}",
        )
    try:
        with env_ref_scope():
            document = exporter.export(id, user)
            body = to_canonical_bytes(document)
    except ExportError as e:
        raise ExtendedHTTPException(code=e.status_code, message=e.message) from e
    except (ValidationError, yaml.YAMLError) as e:
        # Stored data the document format cannot carry: name the entity, never echo its values. Other errors
        # (AttributeError, TypeError, ...) are code bugs and surface as 500 so that monitoring sees them.
        logger.warning(f"Export of {kind} '{id}' failed validation at: {_error_locations(e)}")
        raise ExtendedHTTPException(
            code=422,
            message=f"The {kind} '{id}' has stored data the export document cannot represent",
        ) from e
    return Response(content=body, media_type="application/json")
