# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

from fastapi import APIRouter, Depends, status

from codemie.core.ability import Ability, Action
from codemie.core.workflow_models import WorkflowConfig
from codemie.rest_api.models.workflow_marketplace import (
    PublishWorkflowToMarketplaceRequest,
    WorkflowPublishValidationResponse,
)
from codemie.rest_api.routers.utils import (
    raise_not_found,
    raise_forbidden,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.workflow_config.workflow_marketplace_service import WorkflowMarketplaceService
from codemie.service.workflow_service import WorkflowService

router = APIRouter(
    tags=["Workflow Marketplace"],
    prefix="/v1",
    dependencies=[],
)

_workflow_service = WorkflowService()
_marketplace_service = WorkflowMarketplaceService()


def _get_writable_workflow(workflow_id: str, user: User, *, action: str) -> WorkflowConfig:
    try:
        workflow = _workflow_service.get_workflow(workflow_id=workflow_id, user=user)
    except KeyError:
        raise_not_found(resource_id=workflow_id, resource_type="Workflow")

    if not Ability(user).can(Action.READ, workflow):
        raise_not_found(resource_id=workflow_id, resource_type="Workflow")

    if not Ability(user).can(Action.WRITE, workflow):
        raise_forbidden(action)

    return workflow


@router.post(
    "/workflows/{workflow_id}/marketplace/publish/validate",
    status_code=status.HTTP_200_OK,
    response_model=WorkflowPublishValidationResponse,
)
async def validate_workflow_for_marketplace(
    workflow_id: str,
    user: User = Depends(authenticate),
) -> WorkflowPublishValidationResponse:
    workflow = _get_writable_workflow(workflow_id, user, action="publish")
    return await _marketplace_service.validate(workflow, user)


@router.post(
    "/workflows/{workflow_id}/marketplace/publish",
    status_code=status.HTTP_200_OK,
    response_model=WorkflowConfig,
    response_model_by_alias=True,
)
async def publish_workflow_to_marketplace(
    workflow_id: str,
    request: PublishWorkflowToMarketplaceRequest,
    user: User = Depends(authenticate),
) -> WorkflowConfig:
    workflow = _get_writable_workflow(workflow_id, user, action="publish")
    return await _marketplace_service.publish(workflow, request, user)


@router.post(
    "/workflows/{workflow_id}/marketplace/unpublish",
    status_code=status.HTTP_200_OK,
    response_model=WorkflowConfig,
    response_model_by_alias=True,
)
async def unpublish_workflow_from_marketplace(
    workflow_id: str,
    user: User = Depends(authenticate),
) -> WorkflowConfig:
    workflow = _get_writable_workflow(workflow_id, user, action="unpublish")
    return await _marketplace_service.unpublish(workflow, user)
