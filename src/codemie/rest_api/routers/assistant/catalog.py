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
from typing import Annotated, Optional

from pydantic import BaseModel

from fastapi import APIRouter, status, Request, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from codemie.configs import logger
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.guardrail import GuardrailEntity
from codemie.rest_api.models.assistant import (
    Assistant,
    AssistantListResponse,
    ToolKitDetails,
    AssistantSortBy,
)
from codemie.service.llm.model_availability_service import ModelAvailabilityService
from codemie.rest_api.models.index import SortOrder
from codemie.rest_api.models.prebuilt_assistants import PrebuiltAssistant
from codemie.rest_api.routers.assistant._shared import (
    _get_assistant_by_slug_or_raise,
    _validate_remote_entities_and_raise,
)
from codemie.rest_api.routers.utils import check_user_can_access_assistant, get_assistant_by_id_or_raise
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_repository import AssistantScope, AssistantRepository
from codemie.service.assistant.category_service import category_service
from codemie.service.assistant.sensitive_prompt_variables import mask_sensitive_prompt_variables
from codemie.service.guardrail.guardrail_service import GuardrailService
from codemie.service.tools import ToolsInfoService


router = APIRouter()


class CategoriesRequest(BaseModel):
    """Request model for assistant categories"""

    categories: Optional[list[str]] = None


def _apply_template_filters(templates, parsed_filters):
    if not parsed_filters:
        return templates
    name_filter = parsed_filters.get("search") or parsed_filters.get("name")
    categories_filter = parsed_filters.get("categories")
    created_by_filter = parsed_filters.get("created_by")
    if name_filter:
        templates = [t for t in templates if name_filter.lower() in t.name.lower()]
    if categories_filter:
        templates = [t for t in templates if any(c in (t.categories or []) for c in categories_filter)]
    if created_by_filter:
        templates = [
            t
            for t in templates
            if t.created_by
            and (
                getattr(t.created_by, "name", None) == created_by_filter
                or getattr(t.created_by, "username", None) == created_by_filter
            )
        ]
    return templates


@router.get(
    "/assistants",
    status_code=status.HTTP_200_OK,
    response_model=list[AssistantListResponse],
    response_model_by_alias=True,
)
def index_assistants(
    user: User = Depends(authenticate),
    scope: Annotated[AssistantScope, Query()] = AssistantScope.VISIBLE_TO_USER,
    minimal_response: bool = False,
    filters: str = None,
    page: int = 0,
    per_page: int = 12,
    sort_by: AssistantSortBy | None = Query(None, description="Sort assistants by field"),
    sort_order: SortOrder = Query(SortOrder.DESC, description="Sort direction (asc or desc)"),
    group_by_is_global: bool = Query(
        True,
        description="When true, non-global assistants appear first in PROJECT_WITH_MARKETPLACE scope",
    ),
):
    """
    Returns all saved assistants
    """
    try:
        parsed_filters = json.loads(filters) if filters else None
    except json.JSONDecodeError:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid filters",
            details="Filters must be a valid encoded JSON object.",
            help="Please check the filters and ensure they are in the correct format. ",
        )

    if scope == AssistantScope.TEMPLATES:
        all_templates = _apply_template_filters(PrebuiltAssistant.prebuilt_assistants(user), parsed_filters)
        total = len(all_templates)
        paginated = all_templates[page * per_page : (page + 1) * per_page]
        pages = (total + per_page - 1) // per_page if per_page else 0
        meta = {"page": page, "per_page": per_page, "total": total, "pages": pages}
        return JSONResponse(
            content=jsonable_encoder({"data": paginated, "pagination": meta}),
            status_code=status.HTTP_200_OK,
        )

    repository = AssistantRepository()
    result = repository.query(
        user=user,
        scope=scope,
        filters=parsed_filters,
        page=page,
        per_page=per_page,
        minimal_response=minimal_response,
        sort_by=sort_by,
        sort_order=sort_order,
        group_by_is_global=group_by_is_global,
    )

    # Usage stats are added in the repository

    return JSONResponse(content=jsonable_encoder(result), status_code=status.HTTP_200_OK)


@router.get(
    "/assistants/users",
    status_code=status.HTTP_200_OK,
)
def get_assistant_users(
    user: User = Depends(authenticate),
    scope: AssistantScope = AssistantScope.VISIBLE_TO_USER,
):
    """
    Returns list of users who created assistants within the specified scope
    """
    if scope == AssistantScope.TEMPLATES:
        all_templates = PrebuiltAssistant.prebuilt_assistants(user)
        seen = {}
        for t in all_templates:
            if t.created_by and t.created_by.username and t.created_by.username not in seen:
                seen[t.created_by.username] = t.created_by
        return list(seen.values())

    repository = AssistantRepository()
    result = repository.get_users(user=user, scope=scope)

    return result


def _enrich_nested_assistants(assistant: Assistant, user: User, tools_info) -> None:
    """Attach nested assistants and enrich their toolkits' settings_config."""
    assistant.nested_assistants = Assistant.get_by_ids(user, assistant.assistant_ids, parent_assistant=assistant)
    for nested_assistant in assistant.nested_assistants:
        if nested_assistant.toolkits:
            _enrich_toolkit_settings_config(nested_assistant.toolkits, tools_info)


def _get_skills_data(assistant: Assistant, user: User) -> list[dict]:
    """Enrich skills with full details (id, name, description)."""
    if not assistant.skill_ids:
        return []

    from codemie.service.skill_service import SkillService

    skills = SkillService.get_skills_by_ids(assistant.skill_ids, user)
    return [skill.to_basic_info().model_dump() for skill in skills]


def _resolve_effective_llm_model(assistant: Assistant, response_data: dict) -> tuple[str, str]:
    """Resolve the effective LLM model for an assistant, honoring project restrictions."""
    effective_llm_model = assistant.llm_model_type
    picked, overridden = ModelAvailabilityService.resolve_requested_model(
        assistant.project, effective_llm_model, is_global=assistant.is_global
    )
    if not overridden:
        return effective_llm_model, "assistant"

    # Ensure UI form doesn't display a disabled model
    response_data["llm_model_type"] = picked
    return picked, "project_default"


def _build_assistant_detail_response(assistant: Assistant, user: User) -> JSONResponse:
    """
    Enrich a single assistant record and build its detail JSON response.

    Shared by the by-id and by-slug lookup endpoints so both return an identically
    shaped payload. Historically the by-slug endpoint skipped `user_abilities`, which
    left the UI unable to show owner actions (Edit/Delete/Publish) on human-readable
    `/{project}/{slug}` URLs; centralizing the enrichment here keeps the two endpoints
    from drifting apart again.
    """
    assistant.user_abilities = Ability(user).list(assistant)

    check_user_can_access_assistant(user, assistant, "view", Action.READ)
    _validate_remote_entities_and_raise(assistant)

    # Match the settings_config flag with the correct value from ToolsInfoService
    tools_info = ToolsInfoService.get_tools_info(user=user)
    if assistant.toolkits:
        _enrich_toolkit_settings_config(assistant.toolkits, tools_info)

    _enrich_nested_assistants(assistant, user, tools_info)
    skills_data = _get_skills_data(assistant, user)

    _get_categories_data(assistant)

    # Mask sensitive prompt variable default values
    mask_sensitive_prompt_variables(assistant)

    # Enrich system_prompt_history from version configurations
    repository = AssistantRepository()
    repository.enrich_system_prompt_history(assistant)

    # Enrich with guardrail assignments
    assistant.guardrail_assignments = GuardrailService.get_entity_guardrail_assignments(
        user,
        GuardrailEntity.ASSISTANT,
        str(assistant.id),
    )

    # Convert to dict and add skills
    response_data = jsonable_encoder(assistant)
    response_data["skills"] = skills_data

    # Add effective model (project restrictions)
    effective_llm_model, effective_llm_model_source = _resolve_effective_llm_model(assistant, response_data)
    response_data["effective_llm_model"] = effective_llm_model
    response_data["effective_llm_model_source"] = effective_llm_model_source

    return JSONResponse(content=response_data, status_code=status.HTTP_200_OK)


@router.get(
    "/assistants/id/{assistant_id}",
    status_code=status.HTTP_200_OK,
    response_model=Assistant,
    response_model_by_alias=True,
)
def get_assistant_by_id(request: Request, assistant_id: str, user: User = Depends(authenticate)):
    """
    Returns saved assistant by id
    """
    assistant = get_assistant_by_id_or_raise(assistant_id)
    return _build_assistant_detail_response(assistant, user)


@router.get(
    "/assistants/slug/{assistant_slug:path}",
    status_code=status.HTTP_200_OK,
    response_model=Assistant,
    response_model_by_alias=True,
)
def get_assistant_by_slug(
    request: Request,
    assistant_slug: str,
    user: User = Depends(authenticate),
    project: Optional[str] = Query(
        None,
        description="Project name to scope the slug lookup. Slugs are unique per-project; "
        "pass it to resolve human-readable {project}/{slug} URLs deterministically.",
    ),
):
    """
    Returns saved assistant by slug
    """
    assistant = _get_assistant_by_slug_or_raise(assistant_slug, project)
    return _build_assistant_detail_response(assistant, user)


@router.get(
    "/assistants/prebuilt",
    status_code=status.HTTP_200_OK,
    response_model=list[PrebuiltAssistant],
    response_model_by_alias=True,
)
def get_prebuilt_assistants(user: User = Depends(authenticate)):
    """
    Returns prebuilt assistants
    """
    return PrebuiltAssistant.prebuilt_assistants(user)


@router.get(
    "/assistants/prebuilt/{slug}",
    status_code=status.HTTP_200_OK,
    response_model=PrebuiltAssistant,
    response_model_by_alias=True,
)
def get_prebuilt_assistant_by_slug(slug: str, user: User = Depends(authenticate)):
    """
    Returns prebuilt assistants
    """
    try:
        assistants = PrebuiltAssistant.prebuilt_assistants(user)
        assistant = next(item for item in assistants if item.slug == slug)

        return assistant
    except StopIteration:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Prebuilt assistant not found",
            details=f"No prebuilt assistant found with the slug '{slug}'.",
            help="Please check the assistant slug and ensure it is correct. ",
        )


def _enrich_toolkit_settings_config(assistant_toolkits: list[ToolKitDetails], tools_info: list[dict]):
    """
    Updates the settings_config flag in assistant toolkits to match the correct value from tools_info.

    Args:
        assistant_toolkits: list of toolkits from the assistant model
        tools_info: list of toolkit information from ToolsInfoService
    """
    logger.debug("Updating toolkit settings_config flags for assistant")

    # Create lookup dictionary for settings_config
    tools_info_lookup = _create_settings_config_lookup(tools_info)

    # Update settings_config for toolkits and tools
    for toolkit in assistant_toolkits:
        _update_toolkit_settings_config(toolkit, tools_info_lookup)
        _update_tools_settings_config(toolkit, tools_info_lookup)


def _create_settings_config_lookup(tools_info: list[dict]) -> dict:
    """
    Creates a dictionary for quick lookup of settings_config values.
    Args:
        tools_info: list of toolkit information
    Returns:
        dictionary with toolkit and tool names as keys, settings_config as values
    """
    lookup = {}
    for item_info in tools_info:
        toolkit_name = item_info.get("toolkit")
        if not toolkit_name:
            continue

        # Store settings_config for toolkit
        lookup[toolkit_name] = item_info.get("settings_config", False)

        # Store settings_config for tools within the toolkit
        for tool_info in item_info.get("tools", []):
            tool_name = tool_info.get("name")
            if tool_name:
                lookup[(toolkit_name, tool_name)] = tool_info.get("settings_config", False)

    return lookup


def _update_toolkit_settings_config(toolkit, tools_info_lookup: dict):
    """
    Updates the settings_config flag for a single toolkit.
    Args:
        toolkit: The toolkit to update
        tools_info_lookup: dictionary with settings_config values
    """
    # Get settings_config for the toolkit, default to False if not found
    toolkit_info_settings_config = tools_info_lookup.get(toolkit.toolkit, False)

    # Determine the final settings_config value
    if toolkit_info_settings_config and not toolkit.settings:
        toolkit.settings_config = True
    elif toolkit_info_settings_config and toolkit.settings:
        toolkit.settings_config = False
    else:
        toolkit.settings_config = toolkit_info_settings_config


def _update_tools_settings_config(toolkit, tools_info_lookup: dict):
    """
    Updates the settings_config flag for all tools in a toolkit.
    Args:
        toolkit: The toolkit containing tools to update
        tools_info_lookup: dictionary with settings_config values
    """
    for tool in toolkit.tools:
        tool_key = (toolkit.toolkit, tool.name)

        # Get settings_config for the tool, default to False if not found
        tool_info_settings_config = tools_info_lookup.get(tool_key, False)

        # Determine the final settings_config value
        if tool_info_settings_config and not tool.settings:
            tool.settings_config = True
        elif tool_info_settings_config and tool.settings:
            tool.settings_config = False
        else:
            tool.settings_config = tool_info_settings_config


def _get_categories_data(assistant: Assistant):
    """
    Enriches assistant categories from list[str] to list[Category].
    Modifies the assistant object in place.

    Args:
        assistant: The assistant object to enrich
    """

    enriched_categories = category_service.enrich_categories(assistant.categories)
    assistant._enriched_categories = enriched_categories
