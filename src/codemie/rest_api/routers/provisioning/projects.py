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

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import EmailStr

from codemie.configs.logger import logger
from codemie.configs.provisioning_clients_config import ProvisioningClient, ProvisioningPermission
from codemie.rest_api.models.analytics import ErrorResponse
from codemie.rest_api.models.provisioning import ProvisioningProjectResponse, ProvisioningValidationErrorResponse
from codemie.rest_api.security.provisioning_access import RequirePermission
from codemie.service.provisioning_service import provisioning_service

router = APIRouter()

_PROJECTS_DESCRIPTION = """\
Returns the CodeMie projects that any of the given people belong to. Personal projects are excluded.

The result is the deduplicated union across all emails, sorted by project name. \
Emails with no active CodeMie user and service-account emails are skipped; \
if no email matches, the response is an empty list.

Callable only by registered provisioning clients holding `provisioning:read-projects`.
"""


@router.get(
    "/projects",
    response_model=list[ProvisioningProjectResponse],
    summary="List projects for the given people",
    description=_PROJECTS_DESCRIPTION,
    responses={
        403: {
            "model": ErrorResponse,
            "description": "The caller is not a registered provisioning client holding provisioning:read-projects.",
        },
        422: {
            "model": ProvisioningValidationErrorResponse,
            "description": "Invalid query: no member_email or an invalid email.",
        },
    },
)
async def list_member_projects(
    member_email: list[EmailStr] = Query(
        description="Email of a person whose projects to list; repeat for several. Matched case-insensitively.",
        examples=["requester@epam.com"],
    ),
    client: ProvisioningClient = Depends(RequirePermission(ProvisioningPermission.READ_PROJECTS)),
) -> list[ProvisioningProjectResponse]:
    projects = await provisioning_service.list_member_projects(member_email)
    logger.info(
        f"provisioning_member_lookup: client={client.name}, emails={len(member_email)},"
        f" projects={len(projects)}, domain=provisioning"
    )
    return [
        ProvisioningProjectResponse(project_name=project.name, display_name=project.display_name)
        for project in projects
    ]
