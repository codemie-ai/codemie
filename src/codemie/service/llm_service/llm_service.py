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

from enum import Enum
from typing import TYPE_CHECKING, List, Optional

from codemie.configs import config, logger
from codemie.configs.llm_config import LLMConfig, llm_config, LLMModel, CostConfig, ModelCategory, LiteLLMModels

if TYPE_CHECKING:
    from codemie.rest_api.security.user import User


class LLMService:
    BASE_NAME_GPT_41 = "gpt-4.1"
    BASE_NAME_GPT_41_MINI = "gpt-4.1-mini"
    BASE_NAME_GEMINI = "gemini"
    BASE_NAME_CLAUDE = "claude"

    def __init__(self, llm_config: LLMConfig):
        self.llm_config = llm_config
        self._default_litellm_models: List[LLMModel] = []
        self._default_litellm_embeddings: List[LLMModel] = []

    def get_model_info(self, models: List[LLMModel]) -> List[LLMModel]:
        """Retrieve all labels and base names from models."""
        return [model for model in models if model.enabled]

    def get_all_llm_model_info(self) -> List[LLMModel]:
        """Retrieve all labels and base names from LLM models across all providers.

        When LLM_PROXY_ENABLED is True and LiteLLM models are initialized,
        returns models from LiteLLM. Otherwise, returns models from YAML config.
        """
        # If LiteLLM proxy is enabled and models are initialized, use them as source of truth
        if config.LLM_PROXY_ENABLED and self._default_litellm_models:
            return self._default_litellm_models

        # Otherwise, use traditional YAML-based models
        return self.get_model_info(self.llm_config.llm_models)

    def get_all_embedding_model_info(self) -> List[LLMModel]:
        """Retrieve all labels and base names from embedding models across all providers.

        When LLM_PROXY_ENABLED is True and LiteLLM embedding models are initialized,
        returns embedding models from LiteLLM. Otherwise, returns embedding models from YAML config.
        """
        # If LiteLLM proxy is enabled and embedding models are initialized, use them as source of truth
        if config.LLM_PROXY_ENABLED and self._default_litellm_embeddings:
            return self._default_litellm_embeddings

        # Otherwise, use traditional YAML-based embedding models
        return self.get_model_info(self.llm_config.embeddings_models)

    def get_deployment_name(self, model_name: str, models: List[LLMModel], default_name: str) -> str:
        """Retrieve the deployment name for a model based on its base name."""
        if not model_name:
            return self.get_deployment_name(default_name, models, default_name)
        # Try to find an exact match in the model’s base name
        found_model = next((model.deployment_name for model in models if model_name == model.base_name), None)
        logger.debug(f"Found deployment_name for given model {model_name} -> {found_model}")
        if not found_model:
            logger.error(f"No deployment name for given model {model_name}. Fallback to default {default_name}")
        return found_model or self.get_deployment_name(default_name, models, default_name)

    def get_llm_deployment_name(self, llm_model: str, category: Optional[str] = None) -> str:
        """Retrieve the deployment name for an LLM model based on its base name and optional category.

        If a specific model name is provided, that model will be used.
        If no model is provided but a category is specified, the default model for that category will be used.
        If neither model nor category is provided, or if no default exists for the category,
        the global default model will be used.

        Args:
            llm_model: The base name of the LLM model to use, or empty string to use a default
            category: Optional category to determine the appropriate default model

        Returns:
            The deployment name of the selected model
        """
        # If no specific model requested but category provided, try to get category-specific default
        if not llm_model and category:
            # Try to find a model that's default for the specified category
            category_default = self.get_default_model_for_category(category)
            if category_default:
                logger.debug(f"Using category-specific default model for {category}: {category_default.base_name}")
                return category_default.deployment_name
            else:
                logger.debug(f"No category-specific default found for {category}, falling back to global default")

        # Get the active model source (LiteLLM or YAML)
        active_models = self.get_all_llm_model_info()

        # If no category-specific model found or no category specified, use standard logic
        return self.get_deployment_name(llm_model, active_models, self.default_llm_model)

    def get_embedding_deployment_name(self, embeddings_model: str) -> str:
        """Retrieve the deployment name for an embedding model based on its base name."""
        # Get the active embedding model source (LiteLLM or YAML)
        active_embeddings = self.get_all_embedding_model_info()

        return self.get_deployment_name(embeddings_model, active_embeddings, self.default_embedding_model)

    def get_model_details(self, model_name: str) -> LLMModel:
        """Retrieve the model details for a model based on its name.

        Searches both LLM models and embedding models collections.
        """
        # Get the active model sources (LiteLLM or YAML)
        active_llm_models = self.get_all_llm_model_info()
        active_embedding_models = self.get_all_embedding_model_info()

        # Search for the model in both LLM and embedding models collections
        all_models = [*active_llm_models, *active_embedding_models]
        found_model = next(
            (model for model in all_models if model_name == model.base_name or model_name == model.deployment_name),
            None,
        )
        # If not found, get the default model
        if not found_model:
            found_model = next((model for model in all_models if model.default), None)
            logger.error(f"Model {model_name} not found. Getting default model {found_model} details.")

        return found_model

    def get_multimodal_llms(self) -> List[str]:
        # Get the active model source (LiteLLM or YAML)
        active_models = self.get_all_llm_model_info()
        return [model.deployment_name for model in active_models if model.multimodal]

    def get_react_llms(self) -> List[str]:
        # Get the active model source (LiteLLM or YAML)
        active_models = self.get_all_llm_model_info()
        return [model.base_name for model in active_models if model.react_agent]

    def create_model_types_enum(self):
        """Dynamically create an Enum with model deployment names from the configuration."""
        # Get the active model sources (LiteLLM or YAML)
        active_llm_models = self.get_all_llm_model_info()
        active_embedding_models = self.get_all_embedding_model_info()

        enum_dict = {}
        for model in active_llm_models:
            enum_key = model.base_name.upper().replace("-", "_")
            enum_dict[enum_key] = model.deployment_name
        for embedding in active_embedding_models:
            enum_key = embedding.base_name.upper().replace("-", "_")
            enum_dict[enum_key] = embedding.deployment_name
        return Enum('ModelTypes', enum_dict)

    def get_model_cost(self, base_name: Optional[str] = None) -> Optional[CostConfig]:
        """Retrieve the cost configuration for a given model base name."""
        if not base_name:
            base_name = self.default_llm_model

        # Get the active model source (LiteLLM or YAML)
        active_models = self.get_all_llm_model_info()

        for model in active_models:
            if (model.base_name == base_name or model.deployment_name == base_name) and model.cost:
                return model.cost

        # If the base_name is provided but not found, return the cost of the default model
        for model in active_models:
            if model.base_name == self.default_llm_model and model.cost:
                return model.cost

    def get_embeddings_model_cost(self, base_name: Optional[str] = None) -> Optional[CostConfig]:
        """Retrieve the embeddings cost configuration for a given model base name."""
        if not base_name:
            base_name = self.default_embedding_model

        # Get the active embedding model source (LiteLLM or YAML)
        active_embeddings = self.get_all_embedding_model_info()

        for model in active_embeddings:
            if (model.base_name == base_name or model.deployment_name == base_name) and model.cost:
                return model.cost

        # If the base_name is provided but not found, return the cost of the default model
        for model in active_embeddings:
            if model.base_name == self.default_embedding_model and model.cost:
                return model.cost

    def get_default_model_for_category(self, category: str) -> Optional[LLMModel]:
        """Retrieve the default LLM model for a specific category.

        First tries to find a model that's specifically marked as default for the given category.
        If no category-specific default is found, falls back to the global default model (marked with GLOBAL category).

        Args:
            category: The category to find the default model for

        Returns:
            The default model for the category, or the global default if none is specified,
            or None if no defaults are configured
        """
        # Get the active model source (LiteLLM or YAML)
        active_models = self.get_all_llm_model_info()

        # First try to find a model explicitly marked as default for this category
        category_default = next(
            (model for model in active_models if model.enabled and model.is_default_for(category)), None
        )
        if category_default:
            return category_default

        # If no category-specific default found, fall back to GLOBAL in default_for_categories
        return next(
            (model for model in active_models if model.enabled and model.is_default_for(ModelCategory.GLOBAL)),
            None,
        )

    def get_default_models_by_category(self) -> dict:
        """Returns a dictionary mapping all categories to their default models.

        This function determines the default model for each category in `ModelCategory`:
        - If a model is specifically marked default for a category, it is used.
        - Fallback to the global default model (with GLOBAL category) when no specific default exists.

        Returns:
            Dictionary with category values as keys and LLMModel objects as values.
        """
        category_models = self._get_specific_category_defaults()
        global_default = self._get_global_default_model()

        if global_default:
            self._populate_missing_categories_with_global_default(category_models, global_default)

        return category_models

    def _get_specific_category_defaults(self) -> dict:
        """Retrieve specific default models for categories."""
        # Get the active model source (LiteLLM or YAML)
        active_models = self.get_all_llm_model_info()

        specific_defaults = {}
        for model in active_models:
            if model.enabled:
                for category in model.default_for_categories:
                    if model.is_default_for(category):
                        specific_defaults[category] = model
        return specific_defaults

    def _get_global_default_model(self) -> Optional[LLMModel]:
        """Retrieve the global default model for the GLOBAL category, if available."""
        # Get the active model source (LiteLLM or YAML)
        active_models = self.get_all_llm_model_info()

        return next(
            (model for model in active_models if model.enabled and model.is_default_for(ModelCategory.GLOBAL)),
            None,
        )

    def _populate_missing_categories_with_global_default(self, category_models: dict, global_default: LLMModel):
        """Add the global default model for categories without a specific default."""
        for category in ModelCategory:
            if category.value not in category_models and category != ModelCategory.GLOBAL:
                category_models[category.value] = global_default

    @property
    def default_llm_model(self) -> str:
        """Retrieve all default LLM models and return name of first one."""
        # Get the active model source (LiteLLM or YAML)
        active_models = self.get_all_llm_model_info()

        default_model_names = [model.base_name for model in active_models if model.default]
        if not default_model_names:
            raise ValueError("No global default LLM model is configured (no model with GLOBAL category).")
        return default_model_names[0]

    @property
    def default_embedding_model(self) -> str:
        """Retrieve all default embedding models."""
        # Get the active embedding model source (LiteLLM or YAML)
        active_embeddings = self.get_all_embedding_model_info()

        default_models = [model for model in active_embeddings if model.default]
        if not default_models:
            raise ValueError("No default embedding model is configured.")
        return default_models[0].base_name

    def is_gemini_models(self, model_name: str) -> bool:
        return model_name and self.BASE_NAME_GEMINI in model_name

    def is_claude_models(self, model_name: str) -> bool:
        return model_name and self.BASE_NAME_CLAUDE in model_name

    def initialize_default_litellm_models(self, user_models: LiteLLMModels) -> None:
        # Store the models
        self._default_litellm_models = user_models.chat_models
        self._default_litellm_embeddings = user_models.embedding_models

        logger.info(
            f"Initialized {len(self._default_litellm_models)} unique LiteLLM chat models "
            f"and {len(self._default_litellm_embeddings)} unique LiteLLM embedding models"
        )

    @property
    def default_litellm_models(self) -> List[LLMModel]:
        """Get default LiteLLM chat models initialized on startup"""
        return self._default_litellm_models

    @property
    def default_litellm_embeddings(self) -> List[LLMModel]:
        """Get default LiteLLM embedding models initialized on startup"""
        return self._default_litellm_embeddings

    def get_allowed_models(self, user: 'User') -> LiteLLMModels:
        """
        Internal method to get allowed models for a user.
        Returns both chat and embedding models to avoid duplicate API calls.

        Args:
            user: User object with id, user_type, and applications

        Returns:
            LiteLLMModels containing chat and embedding models
        """
        from codemie.enterprise.litellm import get_user_allowed_models

        # If LiteLLM proxy is not enabled, return all enabled models from config
        if not config.LLM_PROXY_ENABLED:
            return LiteLLMModels(
                chat_models=self.get_all_llm_model_info(), embedding_models=self.get_all_embedding_model_info()
            )

        # If user is not external, return all enabled models (no restrictions)
        if not user.is_external_user:
            return LiteLLMModels(
                chat_models=self.get_all_llm_model_info(), embedding_models=self.get_all_embedding_model_info()
            )

        # External user - check for LiteLLM integration
        logger.debug(f"Checking LiteLLM integration for external user {user.id}")

        user_models = get_user_allowed_models(user_id=user.id, user_applications=user.project_names)

        if not user_models or not user_models.chat_models:
            # No user-specific integration - return default LiteLLM models
            logger.info(f"No LiteLLM integration for user {user.id}, using default models")
            return LiteLLMModels(
                chat_models=self.get_all_llm_model_info(), embedding_models=self.get_all_embedding_model_info()
            )

        # Return user-specific models
        logger.info(
            f"Returning user-specific LiteLLM models for {user.id}: "
            f"{len(user_models.chat_models)} chat, {len(user_models.embedding_models)} embedding"
        )
        return user_models

    def _filter_models_by_visibility(self, models: List[LLMModel], include_all: bool) -> List[LLMModel]:
        """
        Filter models based on web visibility settings.

        Args:
            models: List of models to filter
            include_all: If True, return all models. If False, filter out models with forbidden_for_web=True

        Returns:
            Filtered list of models
        """
        if include_all:
            # Show all models - no filtering (when explicitly requested)
            return models

        # Filter out models forbidden for web - only show models with forbidden_for_web != True
        # Handle None as False (visible) for backward compatibility
        filtered_models = [model for model in models if model.forbidden_for_web is not True]

        logger.debug(
            f"Filtered models for web: {len(models)} total, "
            f"{len(filtered_models)} web-suitable, {len(models) - len(filtered_models)} hidden from web"
        )

        return filtered_models

    def get_allowed_chat_models(self, user: 'User', include_all: bool = False) -> List[LLMModel]:
        """
        Get list of LLM models allowed for user, optionally filtering out web-forbidden models.

        Logic:
        - If LLM_PROXY_ENABLED is False: return all enabled models from config
        - If user is NOT external: return all enabled models from config
        - If user IS external:
            - Check for LiteLLM integration (user-level or project-level)
            - If found: return models from user's LiteLLM integration
            - If not found: return default LiteLLM models from startup
        - If include_all is False: filter out models where forbidden_for_web=True

        Args:
            user: User object with id, user_type, and applications
            include_all: If True, return all models. If False (default), filter out models forbidden for web

        Returns:
            List of LLMModel instances accessible to user, filtered by visibility rules
        """
        user_models = self.get_allowed_models(user)
        return self._filter_models_by_visibility(user_models.chat_models, include_all)

    def get_allowed_embedding_models(self, user: 'User', include_all: bool = False) -> List[LLMModel]:
        """
        Get list of embedding models allowed for user, optionally filtering out web-forbidden models.

        Logic:
        - If LLM_PROXY_ENABLED is False: return all enabled embedding models from config
        - If user is NOT external: return all enabled embedding models from config
        - If user IS external:
            - Check for LiteLLM integration (user-level or project-level)
            - If found: return embedding models from user's LiteLLM integration
            - If not found: return default LiteLLM embedding models from startup
        - If include_all is False: filter out models where forbidden_for_web=True

        Args:
            user: User object with id, user_type, and applications
            include_all: If True, return all models. If False (default), filter out models forbidden for web

        Returns:
            List of LLMModel instances (embedding models) accessible to user, filtered by visibility rules
        """
        user_models = self.get_allowed_models(user)
        return self._filter_models_by_visibility(user_models.embedding_models, include_all)


llm_service = LLMService(llm_config)
