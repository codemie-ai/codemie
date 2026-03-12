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

import json
from typing import Literal, Optional
from fastapi import APIRouter, Body, Query, status, Depends

from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import BaseResponse
from codemie.rest_api.models.guardrail import (
    BulkAssignmentResult,
    GuardrailApplyRequest,
    GuardrailAssignmentRequestResponse,
    Guardrail,
    GuardrailEntity,
)
from codemie.rest_api.models.settings import Settings, SettingsBase
from codemie.rest_api.security.authentication import authenticate, project_access_check
from codemie.rest_api.security.user import User
from codemie.service.aws_bedrock.bedrock_guardrail_service import BedrockGuardrailService
from codemie.service.guardrail.guardrail_repository import GuardrailRepository
from codemie.service.guardrail.guardrail_service import GuardrailService


router = APIRouter(
    tags=["Guardrail"],
    prefix="/v1",
    dependencies=[],
)

DEFAULT_PAGE = 0
DEFAULT_PER_PAGE = 12


def _get_guardrail_by_id_or_raise(guardrail_id: str) -> Guardrail:
    """
    Retrieves guardrail by ID or raises a standardized exception if not found
    """
    guardrail: Guardrail = Guardrail.find_by_id(guardrail_id)  # type: ignore
    if not guardrail:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Guardrail not found",
            details=f"No guardrail found with the id '{guardrail_id}'.",
            help="Please check the guardrail id and ensure it is correct.",
        )

    return guardrail


def _validate_guardrail_read(guardrail: Guardrail, user: User):
    if not Ability(user).can(Action.READ, guardrail):
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="Permission denied",
            details="You must own this guardrail or be an admin to access it.",
            help="Contact the guardrail owner or an administrator for assistance.",
        )


def _validate_guardrail_write_delete(guardrail: Guardrail, user: User):
    # Remote entities cannot be "WRITTEN", so we check for strict DELETE permissions
    if not Ability(user).can(Action.DELETE, guardrail):
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="Permission denied",
            details="You must own this guardrail or be an admin to write or delete.",
            help="Contact the guardrail owner or an administrator for assistance.",
        )


def _validate_guardrail_and_get_settings(guardrail: Guardrail) -> SettingsBase:
    """
    Validates the remote guardrail and returns the associated settings.
    """
    # Validate guardrail has bedrock configuration
    if not guardrail.bedrock or not guardrail.bedrock.bedrock_aws_settings_id:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid guardrail configuration",
            details="This guardrail does not have a valid Bedrock settings configuration.",
            help="Ensure the guardrail is properly configured with Bedrock settings.",
        )

    # Retrieve settings
    setting: SettingsBase = Settings.get_by_id(id_=guardrail.bedrock.bedrock_aws_settings_id)  # type: ignore

    if not setting:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Settings not found",
            details=f"Settings with ID {guardrail.bedrock.bedrock_aws_settings_id} not found.",
            help="Verify the guardrail's Bedrock settings configuration.",
        )

    return setting


@router.get(
    "/guardrails",
    status_code=status.HTTP_200_OK,
)
def list_guardrails(
    filters: Optional[str] = Query(None),
    page: int = Query(DEFAULT_PAGE, ge=0),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1),
    user: User = Depends(authenticate),
):
    """
    Returns all saved guardrails with optional filters.
    """
    try:
        parsed_filters = json.loads(filters) if filters else None
    except json.JSONDecodeError:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid filters",
            details="Filters must be a valid encoded JSON object.",
            help="Please check the filters and ensure they are in the correct format.",
        )

    # Validate that at least one required filter is present
    if not parsed_filters or (not parsed_filters.get("project") and not parsed_filters.get("setting_id")):
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Missing required filters",
            details="At least one of 'project' or 'setting_id' must be provided in filters.",
            help="Add either 'project' or 'setting_id' to your filters parameter.",
        )

    repository = GuardrailRepository()
    result = repository.query(
        user=user,
        filters=parsed_filters,
        page=page,
        per_page=per_page,
    )
    return result


@router.get(
    "/guardrails/assignments",
    status_code=status.HTTP_200_OK,
)
def get_guardrail_project_entity_type_assignments(
    project: str = Query(...),
    entity_type: GuardrailEntity = Query(...),
    user: User = Depends(authenticate),
):
    """
    Get all assignments for a project with a given entity type.
    """
    project_access_check(user, project)

    repository = GuardrailRepository()
    assignments = repository.get_entity_type_and_project_guardrail_assignments(
        project_name=project, entity_type=entity_type
    )

    # Filter to return only the specified fields
    return [
        {
            "project_name": assignment.project_name,
            "guardrail_id": assignment.guardrail_id,
            "entity_type": assignment.entity_type,
            "source": assignment.source,
            "scope": assignment.scope,
            "id": assignment.id,
            "entity_id": assignment.entity_id,
            "mode": assignment.mode,
        }
        for assignment in assignments
    ]


@router.get(
    "/guardrails/{guardrail_id}",
    status_code=status.HTTP_200_OK,
)
def get_guardrail_by_id(guardrail_id: str, user: User = Depends(authenticate)):
    """
    Returns guardrail by codemie database id.
    """
    guardrail = _get_guardrail_by_id_or_raise(guardrail_id)
    _validate_guardrail_read(guardrail, user)

    return {
        "guardrailId": guardrail.id,
        "name": guardrail.bedrock.bedrock_name if guardrail.bedrock else "",
        "description": guardrail.description,
    }


@router.delete(
    "/guardrails/{guardrail_id}",
    status_code=status.HTTP_200_OK,
)
def delete_guardrail(guardrail_id: str, user: User = Depends(authenticate)):
    """
    Delete guardrail by codemie database id.
    """
    guardrail = _get_guardrail_by_id_or_raise(guardrail_id)
    _validate_guardrail_write_delete(guardrail, user)

    GuardrailService.remove_guardrail_assignments_for_guardrail(str(guardrail.id))
    guardrail.delete()

    return BaseResponse(message="Specified guardrail removed")


@router.get(
    "/guardrails/{guardrail_id}/assignments",
    status_code=status.HTTP_200_OK,
)
def get_guardrail_assignments(
    guardrail_id: str,
    user: User = Depends(authenticate),
):
    """
    Get all assignments for a guardrail.

    Returns assignments in the same structure as the bulk assignment request,
    organized by entity type (project, assistants, workflows, datasources).
    """

    guardrail = _get_guardrail_by_id_or_raise(guardrail_id)
    _validate_guardrail_write_delete(guardrail, user)

    assignments = GuardrailService.get_guardrail_assignments(user, guardrail_id)

    return {
        "project_name": guardrail.project_name,
        **assignments,
    }


@router.put(
    "/guardrails/{guardrail_id}/assignments",
    status_code=status.HTTP_200_OK,
)
def bulk_assign_guardrail(
    guardrail_id: str,
    request: GuardrailAssignmentRequestResponse,
    user: User = Depends(authenticate),
):
    """
    Bulk assign a guardrail to multiple entities (assistants, workflows, datasources, project).

    The user must own the guardrail or be an admin. Additionally, the user must have
    appropriate permissions for each entity being assigned.

    This endpoint synchronizes the assignments:
    - Creates new assignments that don't exist
    - Keeps existing assignments that match
    - Deletes assignments that are no longer in the request
    """
    # Verify guardrail exists and user has permissions
    guardrail = _get_guardrail_by_id_or_raise(guardrail_id)
    _validate_guardrail_write_delete(guardrail, user)
    setting = _validate_guardrail_and_get_settings(guardrail)

    project_name = setting.project_name

    # Sync all assignments
    success_count, failed_count, errors = GuardrailService.sync_guardrail_bulk_assignments(
        guardrail_id=guardrail_id,
        guardrail_project_name=project_name,
        user=user,
        request=request,
    )

    return BulkAssignmentResult(
        success=success_count,
        failed=failed_count,
        errors=errors,
    )


@router.post(
    "/guardrails/{guardrail_id}/apply",
    status_code=status.HTTP_200_OK,
)
def apply_guardrail(
    guardrail_id: str,
    mode: Literal["all", "filtered"] = Query("filtered"),
    source: Literal["input", "output"] = Query("input"),
    request: GuardrailApplyRequest = Body(...),
    user: User = Depends(authenticate),
):
    """
    Apply a guardrail to content.
    """
    guardrail = _get_guardrail_by_id_or_raise(guardrail_id)
    _validate_guardrail_write_delete(guardrail, user)

    # Build content structure, only include qualifiers if non-empty
    apply_guardrail_content = []
    for item in request.content:
        # Only 'text' type is supported for version 1."
        content_item: dict = {"text": {"text": item.source.text}}

        # Only add qualifiers if they exist and are non-empty
        if item.source.category is not None:
            content_item["text"]["qualifiers"] = item.source.category

        apply_guardrail_content.append(content_item)

    response = BedrockGuardrailService.apply_guardrail(
        guardrail=guardrail,
        content=apply_guardrail_content,
        source="INPUT" if source == "input" else "OUTPUT",
        output_scope="FULL" if mode == "all" else "INTERVENTIONS",
    )

    # Extract only the relevant fields for the simplified response
    return {
        "action": response.get("action", ""),
        "actionReason": response.get("actionReason", ""),
        "outputs": response.get("outputs", []),
    }
