# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the “License”);
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an “AS IS” BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

from typing import Dict, List, Union

from fastapi import APIRouter, Depends, HTTPException, Query

from codemie.clients.postgres import get_session
from codemie.configs.llm_config import LLMModel, LlmRouterOption, ModelCategory, ModelStatus
from codemie.core.models import Application
from codemie.enterprise.litellm import proxy_router, register_proxy_endpoints  # noqa: F401 (proxy_router used by main.py)
from codemie.repository.application_repository import application_repository
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.llm_service.llm_service import llm_service

router = APIRouter(
    tags=["Available LLM Models"],
    prefix="/v1",
    dependencies=[],
)

ChatChoice = Union[LLMModel, LlmRouterOption]


def _get_accessible_chat_choices(user: User, include_all: bool, project: Application | None = None) -> list[ChatChoice]:
    """Use the same user-scoped projections for catalog and default responses."""
    models: list[ChatChoice] = list(llm_service.get_allowed_chat_models(user, include_all=include_all, project=project))
    models.extend(llm_service.get_allowed_router_options(include_all=include_all, project=project, user=user))

    if project and project.default_model:
        return _apply_project_default(models, project.default_model)
    return models


def _apply_project_default(models: list[ChatChoice], default_name: str) -> list[ChatChoice]:
    """Give models and routers one consistent effective project default."""
    selectable = [m for m in models if m.enabled and m.status in (None, ModelStatus.ENABLED)]
    selected = next(
        (m for m in selectable if default_name in (m.base_name, getattr(m, "deployment_name", None))),
        None,
    ) or _default_for_category(selectable, ModelCategory.GLOBAL)
    return [
        m.model_copy(
            update=llm_service._global_default_fields(
                m.default_for_categories, selected is not None and m.base_name == selected.base_name
            )
        )
        for m in models
    ]


def _default_for_category(choices: list[ChatChoice], category: ModelCategory) -> ChatChoice | None:
    selectable = [
        choice
        for choice in choices
        if choice.enabled and getattr(choice, "status", None) in (None, ModelStatus.ENABLED)
    ]
    if category != ModelCategory.GLOBAL:
        specific = next((choice for choice in selectable if category in choice.default_for_categories), None)
        if specific is not None:
            return specific
    global_default = next((choice for choice in selectable if choice.default), None) or next(
        (choice for choice in selectable if ModelCategory.GLOBAL in choice.default_for_categories), None
    )
    return global_default or next(iter(selectable), None)


def get_project_from_id(project_id: str | None = Query(None)) -> Application | None:
    """Fetch Application by name, or return None if not provided. Raises 404 if name provided but not found."""
    if not project_id:
        return None

    with get_session() as session:
        project = application_repository.get_by_name(session, project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
        return project


@router.get(
    "/llm_models",
    response_model=List[Union[LLMModel, LlmRouterOption]],
    response_model_exclude_none=True,
)
def get_llm_models(
    user: User = Depends(authenticate),
    include_all: bool = False,
    project: Application | None = Depends(get_project_from_id),
) -> list[Union[LLMModel, LlmRouterOption]]:
    """
    Return the list of available LLM models for the authenticated user.

    Logic:
    - Regular users: all enabled models from config
    - External users with LLM Proxy integration: models from their integration
    - External users without integration: default LLM Proxy models
    - When include_all=False (default): filter out models with forbidden_for_web=True
    - When project is provided: filter models to only those allowed by the project

    Args:
        user: Authenticated user
        include_all: If True, return all models. If False (default), filter out models forbidden for web
        project: Optional Application object for model narrowing (resolved via dependency)

    Returns:
        List of available LLM models and router options
    """
    return _get_accessible_chat_choices(user, include_all, project)


@router.get(
    "/llm_models/image_generation",
    response_model=List[LLMModel],
    response_model_exclude_none=True,
)
def get_image_generation_models(user: User = Depends(authenticate), include_all: bool = False) -> List[LLMModel]:
    """Return the list of image generation models for the authenticated user."""
    return llm_service.get_allowed_image_generation_models(user, include_all=include_all)


# Categories resource
@router.get(
    "/categories",
    response_model=List[str],
)
def get_llm_model_categories() -> List[str]:
    """
    Return the list of available LLM model categories
    """
    return [category.value for category in ModelCategory]


# Default models resource
@router.get(
    "/default_models",
    description="Returns the default LLM models for each category",
    response_model=Dict[str, LLMModel],
    response_model_exclude_none=True,
)
def get_default_models() -> Dict[str, LLMModel]:
    """
    Return the default LLM models for each category
    """
    return llm_service.get_default_models_by_category()


@router.get(
    "/default_models/{category_id}",
    description="Returns the default LLM model for a specific category",
    response_model=ChatChoice,
    response_model_exclude_none=True,
)
def get_default_model_for_category(
    category_id: str,
    user: User = Depends(authenticate),
    include_all: bool = False,
    project: Application | None = Depends(get_project_from_id),
) -> ChatChoice:
    """
    Return the default LLM model for a specific category, filtered by user access and visibility.

    Args:
        category_id: Category identifier
        user: Authenticated user
        include_all: If True, return all models. If False (default), filter out models forbidden for web
        project: Optional Application object for model narrowing (resolved via dependency)

    Returns:
        Default LLM model for the specified category
    """
    choices = _get_accessible_chat_choices(user, include_all, project)
    default = _default_for_category(choices, ModelCategory(category_id))
    if default is None:
        raise HTTPException(404, f"No accessible models found for category {category_id}")
    return default


@router.get(
    "/embeddings_models",
    response_model=List[LLMModel],
    response_model_exclude_none=True,
)
def get_embeddings_models(user: User = Depends(authenticate), include_all: bool = False) -> List[LLMModel]:
    """
    Return the list of available embedding models for the authenticated user.

    Logic:
    - Regular users: all enabled embedding models from config
    - External users with LLM Proxy integration: embedding models from their integration
    - External users without integration: default LLM Proxy embedding models
    - When include_all=False (default): filter out models with forbidden_for_web=True
    - When include_all=True: return all models without filtering

    Args:
        user: Authenticated user
        include_all: If True, return all models. If False (default), filter out models forbidden for web

    Returns:
        List of available embedding models
    """
    # Get models from service layer (filtered by include_all parameter)
    return llm_service.get_allowed_embedding_models(user, include_all=include_all)


@router.get(
    "/audio_transcription_models",
    response_model=List[LLMModel],
    response_model_exclude_none=True,
)
def get_audio_transcription_models(user: User = Depends(authenticate)) -> List[LLMModel]:
    """
    Return the speech-to-text models available to the authenticated user.

    The model configured through the STT_API_* variables comes first, followed by the LiteLLM
    models with mode=audio_transcription.
    """
    return llm_service.get_allowed_audio_transcription_models(user)


# Explicitly register proxy endpoints if LiteLLM is enabled
register_proxy_endpoints()
