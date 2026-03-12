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

from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException

from codemie.configs.llm_config import LLMModel, ModelCategory
from codemie.enterprise.litellm import proxy_router, register_proxy_endpoints  # noqa: F401 (proxy_router used by main.py)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.llm_service.llm_service import llm_service

router = APIRouter(
    tags=["Available LLM Models"],
    prefix="/v1",
    dependencies=[],
)


@router.get(
    "/llm_models",
    response_model=List[LLMModel],
    response_model_exclude_none=True,
)
def get_llm_models(user: User = Depends(authenticate), include_all: bool = False) -> List[LLMModel]:
    """
    Return the list of available LLM models for the authenticated user.

    Logic:
    - Regular users: all enabled models from config
    - External users with LLM Proxy integration: models from their integration
    - External users without integration: default LLM Proxy models
    - When include_all=False (default): filter out models with forbidden_for_web=True
    - When include_all=True: return all models without filtering

    Args:
        user: Authenticated user
        include_all: If True, return all models. If False (default), filter out models forbidden for web

    Returns:
        List of available LLM models
    """
    # Get models from service layer (filtered by include_all parameter)
    return llm_service.get_allowed_chat_models(user, include_all=include_all)


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
    category_id: str, user: User = Depends(authenticate), include_all: bool = False
) -> LLMModel:
    """
    Return the default LLM model for a specific category, filtered by user access and visibility.

    Args:
        category_id: Category identifier
        user: Authenticated user
        include_all: If True, return all models. If False (default), filter out models forbidden for web

    Returns:
        Default LLM model for the specified category
    """
    # Get user's allowed models (filtered by include_all parameter)
    allowed_models = llm_service.get_allowed_chat_models(user, include_all=include_all)

    # Find default for category from allowed models
    for model in allowed_models:
        if model.is_default_for(ModelCategory(category_id)):
            return model

    # Fallback: return first model or raise 404
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
