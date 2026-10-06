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
from codemie.configs.llm_config import LLMModel, LlmRouterOption, ModelCategory
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
    models: list[Union[LLMModel, LlmRouterOption]] = list(
        llm_service.get_allowed_chat_models(user, include_all=include_all, project=project)
    )
    models.extend(llm_service.get_allowed_router_options(include_all=include_all))

    # Apply project default model to response
    if project and getattr(project, "default_model", None):
        default_name = project.default_model

        patched: list[Union[LLMModel, LlmRouterOption]] = []
        for m in models:
            if isinstance(m, LLMModel):
                patched.append(
                    m.model_copy(update={"default": (m.base_name == default_name or m.deployment_name == default_name)})
                )
            else:
                patched.append(m)
        models = patched

    return models


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
    response_model=LLMModel,
    response_model_exclude_none=True,
)
def get_default_model_for_category(
    category_id: str,
    user: User = Depends(authenticate),
    include_all: bool = False,
    project: Application | None = Depends(get_project_from_id),
) -> LLMModel:
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
    allowed_models = llm_service.get_allowed_chat_models(user, include_all=include_all, project=project)

    for model in allowed_models:
        if model.is_default_for(ModelCategory(category_id)):
            return model

    if allowed_models:
        return allowed_models[0]

    raise HTTPException(404, f"No accessible models found for category {category_id}")


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


# Explicitly register proxy endpoints if LiteLLM is enabled
register_proxy_endpoints()
