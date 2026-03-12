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

import contextlib
from typing import Dict, List, Optional, Type
from fastapi import APIRouter, Body, Query, status, Depends
from pydantic import ValidationError
import urllib.parse

from codemie.core.ability import Ability, Action
from codemie.core.exceptions import NOT_FOUND_MESSAGE, ExtendedHTTPException
from codemie.core.workflow_models.workflow_config import WorkflowConfig
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.guardrail import Guardrail
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.models.vendor import (
    Entities,
    ImportAgent,
    ImportAgentcoreRuntime,
    ImportEntityBase,
    ImportFlow,
    ImportGuardrail,
    ImportKnowledgeBase,
    Vendor,
)
from codemie.rest_api.routers.utils import raise_access_denied
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.aws_bedrock.base_bedrock_service import BaseBedrockService
from codemie.service.aws_bedrock.bedrock_agentcore_runtime_service import BedrockAgentCoreRuntimeService
from codemie.service.aws_bedrock.bedrock_guardrail_service import BedrockGuardrailService
from codemie.service.aws_bedrock.bedrock_agent_service import BedrockAgentService
from codemie.service.aws_bedrock.bedrock_flow_service import BedrockFlowService
from codemie.service.aws_bedrock.bedrock_knowledge_base_service import BedrockKnowledgeBaseService
from codemie.service.guardrail.guardrail_service import GuardrailService
from codemie.service.workflow_service import WorkflowService


router = APIRouter(
    tags=["Vendor"],
    prefix="/v1",
    dependencies=[],
)

workflow_service = WorkflowService()

DEFAULT_PAGE = 0
DEFAULT_PER_PAGE = 12


SERVICE_MAPPING: Dict[str, Dict[str, Type[BaseBedrockService]]] = {
    Vendor.AWS: {
        Entities.AWS_GUARDRAILS: BedrockGuardrailService,
        Entities.AWS_AGENTS: BedrockAgentService,
        Entities.AWS_FLOWS: BedrockFlowService,
        Entities.AWS_KNOWLEDGE_BASES: BedrockKnowledgeBaseService,
        Entities.AWS_AGENTCORE_RUNTIMES: BedrockAgentCoreRuntimeService,
    }
}


ENTITY_KEY_MAP = {
    Entities.AWS_AGENTS: "agentAliasId",
    Entities.AWS_KNOWLEDGE_BASES: "knowledgeBaseId",
    Entities.AWS_GUARDRAILS: "guardrailId",
    Entities.AWS_FLOWS: "flowAliasId",
    Entities.AWS_AGENTCORE_RUNTIMES: "agentcoreRuntimeEndpointName",
}

ENTITY_MODEL_MAP = {
    Entities.AWS_AGENTS: ImportAgent,
    Entities.AWS_KNOWLEDGE_BASES: ImportKnowledgeBase,
    Entities.AWS_GUARDRAILS: ImportGuardrail,
    Entities.AWS_FLOWS: ImportFlow,
    Entities.AWS_AGENTCORE_RUNTIMES: ImportAgentcoreRuntime,
}


def get_service_or_404(origin: Vendor, entity_type: Entities) -> Type[BaseBedrockService]:
    origin_services = SERVICE_MAPPING.get(origin)
    if not origin_services:
        raise ExtendedHTTPException(
            code=404,
            message="Unknown origin",
            details=f"Unknown origin '{origin}'",
            help="Please provide a valid vendor origin (e.g., 'aws').",
        )

    service = origin_services.get(entity_type)
    if not service:
        raise ExtendedHTTPException(
            code=404,
            message="Unknown entity type",
            details=f"Unknown entity type '{entity_type}' for origin '{origin}'",
            help="Please provide a valid entity type for the selected origin.",
        )

    return service


def unquote_and_validate_next_token(next_token: Optional[str]) -> Optional[str]:
    if next_token is None:
        return None

    try:
        return urllib.parse.unquote(next_token)
    except Exception as e:
        raise ExtendedHTTPException(
            code=400,
            message="Invalid next_token",
            details=f"Could not decode next_token: {e}",
            help="Ensure the next_token is properly URL-encoded",
        )


@router.get(
    "/vendors/{origin}/{entity}/settings",
    status_code=status.HTTP_200_OK,
)
def get_all_settings_overview(
    origin: Vendor,
    entity: Entities,
    page: int = Query(DEFAULT_PAGE, ge=0),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1),
    user: User = Depends(authenticate),
):
    """
    List all importable entities available on remote.
    """
    service = get_service_or_404(origin, entity)

    settings_overview = service.get_all_settings_overview(user=user, page=page, per_page=per_page)

    return settings_overview


@router.get(
    "/vendors/{origin}/{entity}",
    status_code=status.HTTP_200_OK,
)
def list_vendor_main_entities(
    origin: Vendor,
    entity: Entities,
    setting_id: str,
    page: int = Query(DEFAULT_PAGE, ge=0),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1),
    next_token: Optional[str] = None,
    user: User = Depends(authenticate),
):
    """
    List main entities available on remote.
    """
    service = get_service_or_404(origin, entity)

    next_token = unquote_and_validate_next_token(next_token)

    importable_entities, return_next_token = service.list_main_entities(
        user=user,
        setting_id=setting_id,
        page=page,
        per_page=per_page,
        next_token=next_token,
    )

    return {
        "data": importable_entities,
        "pagination": {
            "next_token": urllib.parse.quote(return_next_token) if return_next_token else None,
        },
    }


@router.get(
    "/vendors/{origin}/{entity}/{vendor_entity_id}/aliases",
    status_code=status.HTTP_200_OK,
)
def list_vendor_importable_entities_aliases(
    origin: Vendor,
    entity: Entities,
    vendor_entity_id: str,
    setting_id: str,
    page: int = Query(DEFAULT_PAGE, ge=0),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1),
    next_token: Optional[str] = None,
    user: User = Depends(authenticate),
):
    if entity not in [Entities.AWS_AGENTS, Entities.AWS_FLOWS]:
        raise ExtendedHTTPException(
            code=404,
            message=NOT_FOUND_MESSAGE,
            details=f"Entity '{entity.value}' does not support aliases",
            help="Please provide a valid entity type that supports aliases.",
        )
    service = get_service_or_404(origin, entity)

    next_token = unquote_and_validate_next_token(next_token)

    importable_entities, return_next_token = service.list_importable_entities_for_main_entity(
        user=user,
        main_entity_id=vendor_entity_id,
        setting_id=setting_id,
        page=page,
        per_page=per_page,
        next_token=next_token,
    )

    return {
        "data": importable_entities,
        "pagination": {
            "next_token": urllib.parse.quote(return_next_token) if return_next_token else None,
        },
    }


@router.get(
    "/vendors/{origin}/{entity}/{vendor_entity_id}/versions",
    status_code=status.HTTP_200_OK,
)
def list_vendor_importable_entities_versions(
    origin: Vendor,
    entity: Entities,
    vendor_entity_id: str,
    setting_id: str,
    page: int = Query(DEFAULT_PAGE, ge=0),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1),
    next_token: Optional[str] = None,
    user: User = Depends(authenticate),
):
    if entity != Entities.AWS_GUARDRAILS:
        raise ExtendedHTTPException(
            code=404,
            message=NOT_FOUND_MESSAGE,
            details=f"Entity '{entity.value}' does not support versions",
            help="Please provide a valid entity type that supports versions.",
        )
    service = get_service_or_404(origin, entity)

    next_token = unquote_and_validate_next_token(next_token)

    importable_entities, return_next_token = service.list_importable_entities_for_main_entity(
        user=user,
        main_entity_id=vendor_entity_id,
        setting_id=setting_id,
        page=page,
        per_page=per_page,
        next_token=next_token,
    )

    return {
        "data": importable_entities,
        "pagination": {
            "next_token": urllib.parse.quote(return_next_token) if return_next_token else None,
        },
    }


@router.get(
    "/vendors/{origin}/{entity}/{vendor_entity_id}/endpoints",
    status_code=status.HTTP_200_OK,
)
def list_vendor_importable_entities_endpoints(
    origin: Vendor,
    entity: Entities,
    vendor_entity_id: str,
    setting_id: str,
    page: int = Query(DEFAULT_PAGE, ge=0),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1),
    next_token: Optional[str] = None,
    user: User = Depends(authenticate),
):
    if entity != Entities.AWS_AGENTCORE_RUNTIMES:
        raise ExtendedHTTPException(
            code=404,
            message=NOT_FOUND_MESSAGE,
            details=f"Entity '{entity.value}' does not support endpoints",
            help="Please provide a valid entity type that supports endpoints.",
        )
    service = get_service_or_404(origin, entity)

    next_token = unquote_and_validate_next_token(next_token)

    importable_entities, return_next_token = service.list_importable_entities_for_main_entity(
        user=user,
        main_entity_id=vendor_entity_id,
        setting_id=setting_id,
        page=page,
        per_page=per_page,
        next_token=next_token,
    )

    return {
        "data": importable_entities,
        "pagination": {
            "next_token": urllib.parse.quote(return_next_token) if return_next_token else None,
        },
    }


@router.get(
    "/vendors/{origin}/{entity}/{vendor_entity_id}",
    status_code=status.HTTP_200_OK,
)
def get_main_entity_detail(
    origin: Vendor,
    entity: Entities,
    vendor_entity_id: str,
    setting_id: str,
    user: User = Depends(authenticate),
):
    service = get_service_or_404(origin, entity)

    main_entity_detail = service.get_main_entity_detail(
        user=user,
        main_entity_id=vendor_entity_id,
        setting_id=setting_id,
    )

    if not main_entity_detail:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Entity not found.",
            details=f"No entity with the id '{vendor_entity_id}' found.",
            help="Please check the id and ensure it is correct.",
        )

    return main_entity_detail


@router.get(
    "/vendors/{origin}/{entity}/{vendor_entity_id}/{importable_entity_detail}",
    status_code=status.HTTP_200_OK,
)
def get_importable_entity_detail(
    origin: Vendor,
    entity: Entities,
    vendor_entity_id: str,
    importable_entity_detail: str,
    setting_id: str,
    user: User = Depends(authenticate),
):
    service = get_service_or_404(origin, entity)

    detail = service.get_importable_entity_detail(
        user=user,
        main_entity_id=vendor_entity_id,
        importable_entity_detail=importable_entity_detail,
        setting_id=setting_id,
    )

    return detail


@router.post(
    "/vendors/{origin}/{entity}",
    status_code=status.HTTP_200_OK,
)
def import_vendor_entities(
    origin: Vendor,
    entity: Entities,
    body: list = Body(...),
    user: User = Depends(authenticate),
):
    """
    Import entities from vendor (mocked).
    """

    # Sadly because the contracts for this API are just wrong, we have to do manual payload parsing here.
    entity_key = ENTITY_KEY_MAP.get(entity)
    entity_model = ENTITY_MODEL_MAP.get(entity)
    if not entity_key or not entity_model:
        raise ExtendedHTTPException(
            code=422,
            message="Validation Error",
            details="Unsupported entity type or missing entity key.",
            help="Please provide correct payload.",
        )

    # Validate and parse body using the correct model, raise HTTP 422 on validation error
    try:
        parsed_items: List[ImportEntityBase] = [entity_model(**item) for item in body]
    except ValidationError as e:
        raise ExtendedHTTPException(
            code=422,
            message="Validation Error",
            details=str(e.errors()),
            help="Please provide correct payload.",
        )

    # create a proper structure of a dictionary with setting_id as a key
    # and a list of ImportEntityBase as a value
    result: Dict[str, List[ImportEntityBase]] = {}
    for item in parsed_items:
        setting_id = item.setting_id
        if setting_id:
            result.setdefault(setting_id, []).append(item)

    service = get_service_or_404(origin, entity)
    summary = service.import_entities(user=user, import_payload=result)

    return {"summary": summary}


@router.delete(
    "/vendors/{origin}/{entity}/{entity_id}",
    status_code=status.HTTP_200_OK,
)
def delete_vendor_entity(
    origin: Vendor,
    entity: Entities,
    entity_id: str,
    user: User = Depends(authenticate),
):
    entity_model = None
    if entity in [Entities.AWS_AGENTS, Entities.AWS_AGENTCORE_RUNTIMES]:
        entity_model = Assistant.find_by_id(entity_id)
    elif entity == Entities.AWS_KNOWLEDGE_BASES:
        entity_model = IndexInfo.find_by_id(entity_id)
    elif entity == Entities.AWS_FLOWS:
        with contextlib.suppress(KeyError):
            entity_model = workflow_service.get_workflow(workflow_id=entity_id)
    elif entity == Entities.AWS_GUARDRAILS:
        entity_model = Guardrail.find_by_id(entity_id)

    if not entity_model:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=f"{entity.value[:-1]} not found",
            details=f"No {entity.value[:-1]} found with the id '{entity_id}'.",
            help="Please check the id and ensure it is correct.",
        )

    if not Ability(user).can(Action.DELETE, entity_model):
        raise_access_denied("delete")

    try:
        if isinstance(entity_model, WorkflowConfig):
            workflow_service.delete_workflow(entity_model, user)
        elif isinstance(entity_model, Guardrail):
            GuardrailService.remove_guardrail_assignments_for_guardrail(str(entity_model.id))
            entity_model.delete()
        else:
            entity_model.delete()
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to delete entity",
            details=f"An error occurred while deleting the {entity.value[:-1]}: {str(e)}",
            help="This is likely a temporary issue. Please try again later. "
            "If the problem persists, contact the system administrator.",
        )

    return {"success": True}
