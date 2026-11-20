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

from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from threading import Lock
from time import monotonic
from typing import TYPE_CHECKING, Literal, Optional

from codemie.clients.postgres import get_session
from codemie.configs import config, logger
from codemie.configs.customer_config import customer_config
from codemie.core.exceptions import ModelNotSelectableError, ValidationException
from codemie.service.llm_service.stt_models import (
    LegacySttModel,
    build_legacy_stt_model,
    merge_stt_models,
    pick_stt_model,
)
from codemie.configs.llm_config import (
    CostConfig,
    LiteLLMModels,
    LLMConfig,
    LLMModel,
    LLMRouter,
    LlmRouterOption,
    ModelCategory,
    ModelStatus,
    ModelType,
    RouterTier,
    RouterTiers,
    build_switchyard_routers,
    is_positive_token_limit,
    llm_config,
)
from codemie.repository.admin_global_default_repository import AdminGlobalDefaultRepository
from codemie.repository.llm_model_status_repository import LLMModelStatusRepository

if TYPE_CHECKING:
    from codemie.core.models import Application
    from codemie.rest_api.models.admin_global_default import AdminGlobalDefault
    from codemie.rest_api.models.user_model_override import UserModelOverride
    from codemie.rest_api.security.user import User


@dataclass(frozen=True)
class GlobalDefaultResolution:
    model: LLMModel | None
    source: str | None
    saved_base_name: str | None


class LLMService:
    PERSISTED_SETTINGS_CACHE_TTL_SECONDS = 5
    BASE_NAME_GPT_41 = "gpt-4.1"
    BASE_NAME_GPT_41_MINI = "gpt-4.1-mini"
    BASE_NAME_GEMINI = "gemini"
    BASE_NAME_CLAUDE = "claude"

    def __init__(self, llm_config: LLMConfig):
        self.llm_config = llm_config
        self._default_litellm_models: list[LLMModel] = []
        self._default_litellm_embeddings: list[LLMModel] = []
        self._default_litellm_audio_transcription_models: list[LLMModel] = []
        self._litellm_route_costs: dict[str, CostConfig] = {}
        # Last successfully-loaded persisted status overrides, keyed by base_name. Reused when the
        # database is briefly unreachable so an outage cannot silently re-enable a model an admin
        # disabled or deprecated. See _merge_persisted_status.
        self._status_override_cache: dict[str, str] | None = None
        self._admin_global_default_cache: AdminGlobalDefault | None = None
        # Separate deadlines also cache empty results and failed attempts. Keep the last
        # successful values after expiry so an outage cannot re-enable retired models.
        self._status_cache_expires_at = 0.0
        self._default_cache_expires_at = 0.0
        self._status_cache_lock = Lock()
        self._default_cache_lock = Lock()
        self._status_load_failed = False
        self._default_load_failed = False

    def get_model_info(self, models: list[LLMModel]) -> list[LLMModel]:
        """Retrieve all labels and base names from models."""
        return [model for model in models if model.enabled]

    def get_all_llm_model_info(self) -> list[LLMModel]:
        """Retrieve all labels and base names from LLM models across all providers.

        When LLM_PROXY_ENABLED is True and LiteLLM models are initialized,
        returns models from LiteLLM. Otherwise, returns models from YAML config.
        """
        # If LiteLLM proxy is enabled and models are initialized, use them as source of truth.
        # Re-merge cached overrides so status changes take effect without a catalog reload.
        if self.catalog_source() == "litellm":
            self._merge_persisted_status(self._default_litellm_models)
            return self._default_litellm_models

        # Otherwise, use traditional YAML-based models. This is the default configuration
        # (LLM_PROXY_ENABLED=False), so the persisted status override must be merged here too -
        # otherwise disabled/deprecated enforcement silently no-ops for it.
        models = self.get_model_info(self.llm_config.llm_models)
        self._merge_persisted_status(models)
        return models

    def catalog_source(self) -> Literal["litellm", "yaml"]:
        """Name the source get_all_llm_model_info reads the chat models from."""
        return "litellm" if config.LLM_PROXY_ENABLED and self._default_litellm_models else "yaml"

    def get_route_cost(self, route: str) -> CostConfig | None:
        """Price of the LiteLLM deployment named exactly `route` (its `litellm_params.model`), LiteLLM catalog only."""
        if self.catalog_source() != "litellm":
            return None
        return self._litellm_route_costs.get(route)

    def get_route_costs(self) -> dict[str, CostConfig]:
        """A copy of every route price `get_route_cost` can answer; empty unless the catalog is LiteLLM's."""
        if self.catalog_source() != "litellm":
            return {}
        return dict(self._litellm_route_costs)

    def get_all_embedding_model_info(self) -> list[LLMModel]:
        """Retrieve all labels and base names from embedding models across all providers.

        When LLM_PROXY_ENABLED is True and LiteLLM embedding models are initialized,
        returns embedding models from LiteLLM. Otherwise, returns embedding models from YAML config.
        """
        # If LiteLLM proxy is enabled and embedding models are initialized, use them as source of
        # truth. Refresh persisted status on reads after admin changes.
        if config.LLM_PROXY_ENABLED and self._default_litellm_embeddings:
            self._merge_persisted_status(self._default_litellm_embeddings)
            return self._default_litellm_embeddings

        # Otherwise, use traditional YAML-based embedding models - merge persisted status here too
        # for the same reason as get_all_llm_model_info above.
        models = self.get_model_info(self.llm_config.embeddings_models)
        self._merge_persisted_status(models)
        return models

    def get_all_audio_transcription_model_info(self) -> list[LLMModel]:
        """Speech-to-text models from the LiteLLM catalog (model_info.mode=audio_transcription)."""
        if config.LLM_PROXY_ENABLED:
            self._merge_persisted_status(self._default_litellm_audio_transcription_models)
            return self._default_litellm_audio_transcription_models
        return []

    def get_legacy_stt_model(self) -> LLMModel | None:
        """The single STT model configured through the STT_API_* environment variables, if any."""
        return build_legacy_stt_model()

    def is_legacy_stt_model(self, model: LLMModel) -> bool:
        return isinstance(model, LegacySttModel)

    def get_admin_model_catalog(self) -> list[tuple[ModelType, LLMModel | LlmRouterOption]]:
        """Platform catalog for administration, independent of the requesting user's access.

        Provider-unavailable models are excluded; CodeMie-disabled models remain
        manageable. Synthetic Switchyard routers retain their computed status.
        """
        models = [
            (model_type, model.model_copy())
            for model_type, catalog in (
                (ModelType.CHAT, self.get_all_llm_model_info()),
                (ModelType.EMBEDDING, self.get_all_embedding_model_info()),
                (ModelType.AUDIO_TRANSCRIPTION, self.get_all_audio_transcription_model_info()),
            )
            for model in catalog
            if model.enabled
        ]
        self._apply_premium_flags([model for _, model in models])
        return [
            *models,
            *(
                (ModelType.CHAT, router)
                for router in self.get_allowed_router_options(include_all=True)
                if router.router_type == "switchyard"
            ),
        ]

    def get_llm_routers(self, models: list[LLMModel] | None = None) -> list[LLMRouter]:
        """Return the effective Switchyard router catalog.

        Built from the same model source as every other catalog view (get_all_llm_model_info):
        the live LiteLLM/DIAL catalog when the proxy is enabled and initialized, the static
        YAML declaration otherwise. No merge between the two — mixing a live capable/efficient
        model with a stale YAML one (or vice versa) would let a router pair models that never
        coexist in the same catalog view.

        A caller that already holds that catalog passes it as models, so it is not read again.
        """
        return build_switchyard_routers(self.get_all_llm_model_info() if models is None else models)

    def _find_router(self, name: str) -> LLMRouter | None:
        return next((r for r in self.get_llm_routers() if r.base_name == name and r.enabled), None)

    def get_all_routers(self) -> list[LLMRouter]:
        return [r for r in self.get_llm_routers() if r.enabled]

    @staticmethod
    def _compute_router_capabilities(capable: LLMModel, efficient: LLMModel) -> tuple[bool, bool]:
        """Return (multimodal, supports_tools) for a capable/efficient model pair — the AND of
        both tiers, since a router can only guarantee a capability both tiers have. is_premium
        is NOT part of this: it's computed uniformly for every router (switchyard or
        litellm_auto) the same way as for a regular model — is_premium_model(router.base_name)
        — never inferred from its constituent models (see get_allowed_router_options)."""
        cap_multi = capable.multimodal if capable.multimodal is not None else False
        eff_multi = efficient.multimodal if efficient.multimodal is not None else False
        multimodal = bool(cap_multi and eff_multi)

        cap_tools = capable.features.tools if capable.features is not None else True
        eff_tools = efficient.features.tools if efficient.features is not None else True
        supports_tools = bool((cap_tools is not False) and (eff_tools is not False))

        return multimodal, supports_tools

    @staticmethod
    def _switchyard_tiers(capable: LLMModel, efficient: LLMModel) -> RouterTiers:
        """Map a switchyard router's 2-model pair onto the platform's fixed 4-tier shape: the
        efficient model serves the two lower tiers, the capable model the two higher ones."""
        efficient_tier = RouterTier(model=efficient.base_name, label=efficient.label or efficient.base_name)
        capable_tier = RouterTier(model=capable.base_name, label=capable.label or capable.base_name)
        return RouterTiers(simple=efficient_tier, medium=efficient_tier, complex=capable_tier, reasoning=capable_tier)

    @staticmethod
    def _router_tier_models(tiers: RouterTiers | None, by_name: dict[str, LLMModel]) -> list[LLMModel] | None:
        """A router's distinct tier models; None when tiers are absent or any tier model is unresolved.

        The single rule behind every value a router derives from its tiers: an unknown tier makes the
        value unknown, never a guess from the tiers that did resolve."""
        if tiers is None:
            return None
        names = {tiers.simple.model, tiers.medium.model, tiers.complex.model, tiers.reasoning.model}
        tier_models = [by_name.get(name) for name in names]
        return None if any(tier_model is None for tier_model in tier_models) else tier_models

    @staticmethod
    def _router_max_input_tokens(tiers: RouterTiers | None, by_name: dict[str, LLMModel]) -> int | None:
        """Minimum max_input_tokens across a router's distinct tier models. None when tiers are absent
        or any tier model is unresolved/windowless: an unknown tier must never allow a 1M opt-in."""
        tier_models = LLMService._router_tier_models(tiers, by_name)
        if tier_models is None or any(tier_model.max_input_tokens is None for tier_model in tier_models):
            return None
        return min(tier_model.max_input_tokens for tier_model in tier_models)

    @staticmethod
    def _router_max_output_tokens(tiers: RouterTiers | None, by_name: dict[str, LLMModel]) -> int | None:
        """Minimum max_output_tokens across a router's distinct tier models. None when tiers are absent
        or any tier model is unresolved or has no positive limit: a limit above an unknown tier's own
        could make the provider reject the whole request."""
        tier_models = LLMService._router_tier_models(tiers, by_name)
        if tier_models is None or not all(is_positive_token_limit(m.max_output_tokens) for m in tier_models):
            return None
        return min(tier_model.max_output_tokens for tier_model in tier_models)

    @staticmethod
    def _apply_router_output_limits(chat_models: list[LLMModel]) -> None:
        """Give each declared LiteLLM auto-router without its own positive max_output_tokens the limit
        derived from its tiers. A router entry carries no provider metadata, so without this codemie
        forwards no max_tokens for it and the proxy default (4096) cuts replies short.

        A router whose tiers are all Claude is marked `claude_family`, so the limit is forwarded whatever
        the router's alias; otherwise the forwarding check would skip a router named without "claude"."""
        by_name = {model.base_name: model for model in chat_models}
        for model in chat_models:
            if not model.is_declared_litellm_router():
                continue
            tier_models = LLMService._router_tier_models(model.litellm_router.tiers, by_name)
            if tier_models and all(tier_model.is_claude() for tier_model in tier_models):
                model.claude_family = True
            if is_positive_token_limit(model.max_output_tokens):
                continue
            derived = LLMService._router_max_output_tokens(model.litellm_router.tiers, by_name)
            if derived is None:
                # A tier is missing, disabled or has no limit, so the proxy default still caps this router
                logger.warning(
                    f"router_output_limit model={model.base_name} max_output_tokens=unset source=unresolved_tiers"
                )
                continue
            model.max_output_tokens = derived
            forwarded = model.is_claude()
            logger.info(
                f"router_output_limit model={model.base_name} max_output_tokens={derived} "
                f"source=router_tiers forwarded={str(forwarded).lower()}"
            )

    def _build_switchyard_router_option(
        self,
        router: LLMRouter,
        by_name: dict[str, LLMModel],
        include_all: bool,
        premium_enabled: bool,
    ) -> LlmRouterOption | None:
        """Project one switchyard LLMRouter into its REST-facing LlmRouterOption, or None when
        it's not selectable right now (disabled, or web-hidden and include_all wasn't asked)."""
        if not router.enabled:
            return None
        if not include_all and router.forbidden_for_web:
            return None

        from codemie.enterprise.litellm.dependencies import is_premium_model

        capable = by_name.get(router.switchyard.capable_model)
        efficient = by_name.get(router.switchyard.efficient_model)

        if not include_all and (
            (capable is not None and capable.status == ModelStatus.DISABLED)
            or (efficient is not None and efficient.status == ModelStatus.DISABLED)
        ):
            return None

        multimodal: bool | None = None
        supports_tools: bool | None = None
        tiers: RouterTiers | None = None
        router_status: ModelStatus | None = None
        if capable is not None and efficient is not None:
            multimodal, supports_tools = self._compute_router_capabilities(capable, efficient)
            tiers = self._switchyard_tiers(capable, efficient)
            if ModelStatus.DISABLED in (capable.status, efficient.status):
                router_status = ModelStatus.DISABLED
            elif ModelStatus.DEPRECATED in (capable.status, efficient.status):
                router_status = ModelStatus.DEPRECATED
            else:
                router_status = ModelStatus.ENABLED

        return LlmRouterOption(
            base_name=router.base_name,
            label=router.label,
            provider=router.provider,
            multimodal=multimodal,
            supports_tools=supports_tools,
            is_premium=is_premium_model(router.base_name) if premium_enabled else None,
            status=router_status,
            router_type="switchyard",
            strategy=router.switchyard.mode,
            tiers=tiers,
            classifier_model=router.switchyard.classifier_model,
            max_input_tokens=self._router_max_input_tokens(tiers, by_name),
            forbidden_for_web=router.forbidden_for_web is True,
        )

    @staticmethod
    def _build_litellm_auto_router_option(
        model: LLMModel, by_name: dict[str, LLMModel], include_all: bool, premium_enabled: bool
    ) -> LlmRouterOption | None:
        """Project one live-catalog model declared as a LiteLLM auto-router into its REST-facing
        LlmRouterOption, or None when it's not a router at all or not selectable right now."""
        if not model.is_declared_litellm_router():
            return None
        if not model.enabled:
            return None
        if not include_all and model.forbidden_for_web:
            return None
        if not include_all and model.status == ModelStatus.DISABLED:
            return None

        from codemie.enterprise.litellm.dependencies import is_premium_model

        litellm_router = model.litellm_router
        assert litellm_router is not None  # guaranteed by is_declared_litellm_router()

        return LlmRouterOption(
            base_name=model.base_name,
            label=model.label,
            provider=model.provider,
            multimodal=model.multimodal,
            supports_tools=model.features.tools if model.features is not None else None,
            is_premium=is_premium_model(model.base_name) if premium_enabled else None,
            status=model.status,
            default=model.default,  # Propagate the default flag from model_info
            default_for_categories=model.default_for_categories,  # Propagate default categories
            router_type="litellm_auto",
            strategy=litellm_router.strategy,
            tiers=litellm_router.tiers,
            classifier_model=litellm_router.classifier_model,
            max_input_tokens=LLMService._router_max_input_tokens(litellm_router.tiers, by_name),
            forbidden_for_web=model.forbidden_for_web is True,
        )

    def get_allowed_router_options(
        self, include_all: bool = False, project: Application | None = None, user: User | None = None
    ) -> list[LlmRouterOption]:
        """Router options, narrowed like the chat models: by the user's override when they have one for
        the project (it is their whole model set, so no router outside it is offered and one it grants
        is offered even if the project whitelist excludes it), otherwise by the project whitelist."""
        from codemie.enterprise.litellm.dependencies import is_premium_models_enabled
        from codemie.service.llm.model_availability_service import PROJECT_MODEL_OVERRIDE_FEATURE

        user_override = self.get_user_model_override(user, project) if user else None

        if project and not customer_config.is_feature_enabled(PROJECT_MODEL_OVERRIDE_FEATURE):
            project = None

        source_models = self.get_allowed_models(user).chat_models if user is not None else self.get_all_llm_model_info()
        all_models = self._filter_models_by_visibility(source_models, include_all)
        all_models = [model for model in all_models if model.enabled]
        by_name: dict[str, LLMModel] = {m.base_name: m for m in source_models}
        visible_names = {model.base_name for model in all_models}
        premium_enabled = is_premium_models_enabled()
        selected = self.resolve_global_default(all_models).model
        selected_name = selected.base_name if selected else None

        switchyard_options = (
            self._build_switchyard_router_option(router, by_name, include_all, premium_enabled)
            for router in self.get_llm_routers()
            if user is None
            or (router.switchyard.capable_model in visible_names and router.switchyard.efficient_model in visible_names)
        )
        litellm_auto_options = (
            self._build_litellm_auto_router_option(model, by_name, include_all, premium_enabled) for model in all_models
        )
        options = [option for option in (*switchyard_options, *litellm_auto_options) if option is not None]
        if user_override:
            options = self._filter_router_options_by_user_override(options, user_override)
        else:
            options = self._filter_router_options_by_project(options, project)
        return [
            option.model_copy(
                update=self._global_default_fields(option.default_for_categories, option.base_name == selected_name)
            )
            if option.router_type == "litellm_auto"
            else option
            for option in options
        ]

    @staticmethod
    def _filter_router_options_by_user_override(
        options: list[LlmRouterOption], override: UserModelOverride
    ) -> list[LlmRouterOption]:
        """Keep only routers listed in the user's override, by base_name (a router has no deployment name).

        An override that lists no router yields none; unlike the chat models there is no fallback to the
        project set, because "this user has no router" is a normal override.
        """
        from codemie.service.llm.model_availability_service import model_in_override

        return [option for option in options if model_in_override(override.allowed_models, option.base_name)]

    @staticmethod
    def _filter_router_options_by_project(
        options: list[LlmRouterOption], project: Application | None
    ) -> list[LlmRouterOption]:
        """Filter router options based on project's allowed_models list.

        Mirrors _filter_models_by_project, but keys solely on base_name: a router option
        has no deployment of its own (see LLMRouter's own docstring), so base_name is its
        only identifier in allowed_models — matching ProjectService._get_available_router_model_ids.
        """
        from codemie.service.llm.model_availability_service import ProjectModelPolicy

        if project is None:
            return options

        # Membership only, so a whitelist without a default_model does not fail the listing.
        policy = ProjectModelPolicy.for_application(project, require_default=False)
        return [option for option in options if policy.allows(option.base_name)]

    @staticmethod
    def _global_default_fields(categories: list[ModelCategory], selected: bool) -> dict:
        """Rebuild global metadata for a response without changing catalog configuration."""
        projected_categories = [category for category in categories if category != ModelCategory.GLOBAL]
        if selected:
            projected_categories.append(ModelCategory.GLOBAL)
        return {"default": selected, "default_for_categories": projected_categories}

    def is_router_model(self, model_name: str) -> bool:
        """Return True if *model_name* is a configured and enabled Switchyard router."""
        return any(router.base_name == model_name and router.enabled for router in self.get_llm_routers())

    def get_deployment_name(self, model_name: str, models: list[LLMModel], default_name: str) -> str:
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

    def _find_model_or_router_target(
        self, model_name: str, active_llm_models: list, active_embedding_models: list
    ) -> Optional[LLMModel]:
        """Find a model by base/deployment name, resolving Switchyard routers to their capable model."""
        all_models = [*active_llm_models, *active_embedding_models]
        found_model = next(
            (model for model in all_models if model_name == model.base_name or model_name == model.deployment_name),
            None,
        )
        if found_model:
            return found_model

        router = next(
            (r for r in self.get_llm_routers() if r.base_name == model_name and r.enabled),
            None,
        )
        if not router:
            return None
        return next(
            (model for model in active_llm_models if model.base_name == router.switchyard.capable_model),
            None,
        )

    def _resolve_model_not_found(
        self, model_name: str, active_llm_models: list, project: Application | None, fallback_to_default: bool
    ) -> LLMModel:
        """Handle the not-found case for get_model_details: fall back to default or raise."""
        from codemie.core.exceptions import ModelNotAllowedException

        logger.error(f"Model '{model_name}' not found in active models")
        if fallback_to_default:
            allowed = self._filter_models_by_project(active_llm_models, project)
            default = self._select_default_model(allowed, project)
            if default:
                logger.warning(f"Model '{model_name}' not found; using default '{default.base_name}'")
                return default
        raise ModelNotAllowedException(
            model_name=model_name,
            project_id=project.id if project else None,
            details=f"Model '{model_name}' does not exist in the system.",
        )

    def _resolve_model_not_allowed(
        self,
        model_name: str,
        active_llm_models: list,
        project: Application | None,
        fallback_to_default: bool,
    ) -> LLMModel:
        """Handle the not-allowed-for-project case for get_model_details: fall back or raise.

        project is always non-None here: _filter_models_by_project returns models
        unfiltered when project is None, so get_model_details can only reach this
        method with a project that actually narrows the model set.
        """
        from codemie.core.exceptions import ModelNotWhitelistedException

        logger.warning(f"Model '{model_name}' requested for project '{project.id}' but not in allowed_models list")
        if fallback_to_default:
            allowed = self._filter_models_by_project(active_llm_models, project)
            default = self._select_default_model(allowed, project)
            if default:
                logger.warning(
                    f"Model '{model_name}' not allowed for project '{project.id}'; "
                    f"using project default '{default.base_name}'"
                )
                return default

        # Provide differentiated details for deny-all vs model-not-in-list cases
        if project.allowed_models is not None and len(project.allowed_models) == 0:
            details = f"Project '{project.id}' explicitly denies all models."
        else:
            details = f"Model '{model_name}' is not in the allowed models list for project '{project.id}'."
        raise ModelNotWhitelistedException(model_id=model_name, project_name=project.id, details=details)

    def get_model_details(
        self, model_name: str, project: Application | None = None, fallback_to_default: bool = False
    ) -> LLMModel:
        """Retrieve model details by name, enforcing project narrowing.

        Searches both LLM models and embedding models collections. If the name
        matches a Switchyard router, the capable underlying model is returned so
        callers that do not implement routing still get a usable LLM.

        Args:
            model_name: Base name or deployment name of the model
            project: Project to check allowance against (optional)
            fallback_to_default: If True and model not allowed, return project's default model instead of raising

        Returns:
            LLMModel object

        Raises:
            ModelNotAllowedException: If model does not exist in the system (unless fallback_to_default=True)
            ModelNotWhitelistedException: If model exists but is excluded by the project's
                allowed_models whitelist (unless fallback_to_default=True)
            ValueError: If project exists but project.id is None
        """
        from codemie.service.llm.model_availability_service import PROJECT_MODEL_OVERRIDE_FEATURE

        # Validate project.id is not None when project is provided
        if project and not project.id:
            raise ValueError("Project object must have a non-None id for model validation")

        execution_project = project
        if project and not customer_config.is_feature_enabled(PROJECT_MODEL_OVERRIDE_FEATURE):
            project = None

        # Normalize model_name: strip leading/trailing whitespace
        model_name = model_name.strip() if model_name else model_name

        # Get the active model sources (LiteLLM or YAML)
        active_llm_models = self.get_all_llm_model_info()
        active_embedding_models = self.get_all_embedding_model_info()

        found_model = self._find_model_or_router_target(model_name, active_llm_models, active_embedding_models)
        if not found_model:
            return self._resolve_model_not_found(model_name, active_llm_models, project, fallback_to_default)

        if self.is_disabled_for_execution(model_name, active_llm_models):
            return self._execution_fallback(model_name, active_llm_models, execution_project)

        # Check project narrowing
        if not self._filter_models_by_project([found_model], project):
            return self._resolve_model_not_allowed(model_name, active_llm_models, project, fallback_to_default)

        return found_model

    @staticmethod
    def is_disabled_for_execution(model_name: str | None, models: list[LLMModel]) -> bool:
        """Whether a model, or a Switchyard router through its capable or efficient model, is disabled at
        platform level in the given catalog. A name the catalog does not know is not disabled."""
        model_name = model_name.strip() if model_name else model_name
        model = next((m for m in models if model_name in (m.base_name, m.deployment_name)), None)
        if model is not None:
            return model.status == ModelStatus.DISABLED
        router = next((r for r in build_switchyard_routers(models) if r.base_name == model_name), None)
        if router is None:
            return False
        tier_names = {router.switchyard.capable_model, router.switchyard.efficient_model}
        return any(m.base_name in tier_names and m.status == ModelStatus.DISABLED for m in models)

    def _execution_fallback(
        self, requested_model: str, models: list[LLMModel], project: Application | None
    ) -> LLMModel:
        selected = self._execution_fallback_choice(requested_model, models, project)
        if isinstance(selected, LLMModel):
            return selected
        # Callers of get_model_details need concrete provider configuration.
        target = self._find_model_or_router_target(selected.base_name, models, [])
        if target is None:
            raise ModelNotSelectableError(requested_model, "no selectable fallback model is available")
        return target

    def _execution_fallback_choice(
        self, requested_model: str, models: list[LLMModel], project: Application | None
    ) -> LLMModel | LlmRouterOption:
        """Keep fallback choices within the project, including its configured router."""
        candidates = self._filter_models_by_project([m for m in models if self._is_selectable(m)], project)
        choices = [
            *candidates,
            *(
                router
                for router in self.get_allowed_router_options(include_all=True, project=project)
                if router.enabled and router.status in (None, ModelStatus.ENABLED)
            ),
        ]
        project_default = next(
            (
                m
                for m in choices
                if project
                and project.default_model
                and project.default_model in (m.base_name, getattr(m, "deployment_name", None))
            ),
            None,
        )
        selected = project_default or self.resolve_global_default(candidates).model or next(iter(choices), None)
        if selected is None:
            raise ModelNotSelectableError(requested_model, "no selectable fallback model is available")
        logger.info(
            "model_availability_event=fallback_applied reason=unavailable requested_model=%r fallback_model=%r",
            requested_model,
            selected.base_name,
        )
        return selected

    def resolve_disabled_model(
        self,
        model_name: str | None,
        project_name: str | None = None,
        *,
        is_global: bool = False,
        allow_deprecated: bool = True,
    ) -> str | None:
        """Keep existing enabled/deprecated selections; replace disabled ones at execution time.

        Load project settings only when a disabled selection actually needs a replacement.
        Global assets always use the global default, regardless of their owning project.
        Newly chosen defaults and fallbacks must also exclude deprecated models.
        """
        models = self.get_all_llm_model_info()
        needs_fallback = self.is_disabled_for_execution(model_name, models)
        if not needs_fallback and not allow_deprecated:
            try:
                self.assert_model_selectable(model_name)
            except ModelNotSelectableError:
                needs_fallback = True
        if not needs_fallback:
            return model_name
        project = None
        if project_name and not is_global:
            from codemie.core.models import Application

            with suppress(KeyError):
                project = Application.get_by_id(project_name)
        return self._execution_fallback_choice(model_name, models, project).base_name

    def get_model_label(self, model_name: str) -> Optional[str]:
        """Return the configured label for a model identified by base_name or deployment_name."""
        active_llm_models = self.get_all_llm_model_info()
        active_embedding_models = self.get_all_embedding_model_info()
        found_model = next(
            (
                model
                for model in [*active_llm_models, *active_embedding_models]
                if model_name == model.base_name or model_name == model.deployment_name
            ),
            None,
        )
        if found_model is not None:
            return found_model.label
        router = self._find_router(model_name)
        return router.label if router is not None else None

    def get_model_deployment_name(self, base_name: str) -> Optional[str]:
        """Return the deployment_name for a model base_name.

        Prefers the live LiteLLM/DIAL catalog when available (see get_all_llm_model_info),
        falling back to the static YAML catalog otherwise. Returns None if not found in
        either — unlike get_model_details, this deliberately does not fall back to the
        default model, so callers can apply their own fallback semantics.
        """
        active_llm_models = self.get_all_llm_model_info()
        found_model = next((model for model in active_llm_models if model.base_name == base_name), None)
        return found_model.deployment_name if found_model else None

    def normalize_chat_model_id(
        self,
        model_id: str | None,
        *,
        user,
        project: object | None = None,
        include_all: bool = True,
    ) -> str | None:
        """
        Normalize base_name -> deployment_name when possible.
        Leaves unknown model strings unchanged.
        """
        if not model_id:
            return model_id

        models = self.get_allowed_chat_models(user, include_all=include_all, project=project)
        by_base = {
            m.base_name: m.deployment_name
            for m in models
            if isinstance(m, LLMModel) and getattr(m, "deployment_name", None)
        }
        return by_base.get(model_id, model_id)

    def get_multimodal_llms(self) -> list[str]:
        # Get the active model source (LiteLLM or YAML)
        active_models = self.get_all_llm_model_info()
        return [model.deployment_name for model in active_models if model.multimodal]

    def get_multimodal_llm_names(self) -> set[str]:
        """Every name a multimodal model can be referred to by: its base_name and its deployment_name."""
        multimodal_models = [model for model in self.get_all_llm_model_info() if model.multimodal]
        return {name for model in multimodal_models for name in (model.base_name, model.deployment_name)}

    def get_react_llms(self) -> list[str]:
        # Get the active model source (LiteLLM or YAML)
        active_models = self.get_all_llm_model_info()
        return [model.base_name for model in active_models if model.react_agent]

    def resolve_model_with_project_availability(
        self,
        requested_model: str,
        project_name: str,
    ) -> tuple[str, bool]:
        """
        Stage 2: Resolve model to use at execution time with fallback.

        When a workflow executes and requests a model, check if it's in the project's
        whitelist. If not, fall back to the project's default model. Returns a tuple
        with the actual model to use and a flag indicating whether fallback was applied.

        Returns:
            (actual_model, was_fallback_applied)

        Raises:
            ModelNotWhitelistedException: if model not in whitelist and no default available
            NoDefaultModelException: if project has no default model
            NotFoundException: if project doesn't exist
        """
        from codemie.service.llm.model_availability_service import ModelAvailabilityService

        actual_model, was_fallback = ModelAvailabilityService.resolve_model_for_execution(requested_model, project_name)

        if was_fallback:
            logger.info(
                f"model_availability_event=fallback_applied "
                f"requested_model={requested_model!r} "
                f"project={project_name!r} "
                f"fallback_model={actual_model!r}"
            )

        return actual_model, was_fallback

    def create_model_types_enum(self):
        """Dynamically create an Enum with model deployment names from the configuration."""
        # Read the raw, unmerged model list rather than get_all_llm_model_info/
        # get_all_embedding_model_info: this method only needs base_name/deployment_name to build
        # an Enum and never reads .status, so it must not pay for (or depend on the availability of)
        # the persisted-status database merge. This also runs at module-import time via
        # core/constants.py's module-level ModelTypes assignment, where a database call would be a
        # startup-ordering hazard.
        active_llm_models = self.get_model_info(self.llm_config.llm_models)
        active_embedding_models = self.get_model_info(self.llm_config.embeddings_models)

        enum_dict = {}
        for model in active_llm_models:
            enum_key = model.base_name.upper().replace("-", "_")
            enum_dict[enum_key] = model.deployment_name
        for embedding in active_embedding_models:
            enum_key = embedding.base_name.upper().replace("-", "_")
            enum_dict[enum_key] = embedding.deployment_name
        return Enum('ModelTypes', enum_dict)

    def _get_router_model_cost(self, base_name: str, active_models: list[LLMModel]) -> Optional[CostConfig]:
        """If *base_name* is an enabled router, return the capable model's cost config."""
        router = next((r for r in self.get_llm_routers() if r.base_name == base_name and r.enabled), None)
        if not router:
            return None
        for model in active_models:
            if model.base_name == router.switchyard.capable_model and model.cost:
                return model.cost
        return None

    def get_model_cost(self, base_name: Optional[str] = None) -> Optional[CostConfig]:
        """Retrieve the cost configuration for a given model base name."""
        if not base_name:
            base_name = self.default_llm_model

        # Get the active model source (LiteLLM or YAML)
        active_models = self.get_all_llm_model_info()

        for model in active_models:
            if (model.base_name == base_name or model.deployment_name == base_name) and model.cost:
                return model.cost

        # If the name is a router, resolve to its capable model cost.
        router_cost = self._get_router_model_cost(base_name, active_models)
        if router_cost is not None:
            return router_cost

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
        category_default = (
            next(
                (model for model in active_models if self._is_selectable(model) and model.is_default_for(category)),
                None,
            )
            if category != ModelCategory.GLOBAL
            else None
        )
        if category_default:
            return category_default

        # If no category-specific default found, fall back to GLOBAL in default_for_categories
        return self.resolve_global_default(active_models).model

    def get_default_models_by_category(self) -> dict:
        """Returns a dictionary mapping all categories to their default models.

        This function determines the default model for each category in `ModelCategory`:
        - If a model is specifically marked default for a category, it is used.
        - Fallback to the global default model (with GLOBAL category) when no specific default exists.

        Returns:
            Dictionary with category values as keys and LLMModel objects as values.
        """
        active_models = self.get_all_llm_model_info()
        category_models = self._get_specific_category_defaults(active_models)
        global_default = self.resolve_global_default(active_models).model

        if global_default:
            category_models[ModelCategory.GLOBAL.value] = global_default
            self._populate_missing_categories_with_global_default(category_models, global_default)

        return category_models

    def _get_specific_category_defaults(self, active_models: list[LLMModel] | None = None) -> dict:
        """Retrieve specific default models for categories."""
        # Get the active model source (LiteLLM or YAML)
        if active_models is None:
            active_models = self.get_all_llm_model_info()

        specific_defaults = {}
        for model in active_models:
            if self._is_selectable(model):
                for category in model.default_for_categories:
                    if model.is_default_for(category):
                        specific_defaults[category] = model
        return specific_defaults

    def _get_global_default_model(self) -> Optional[LLMModel]:
        """Retrieve the global default model for the GLOBAL category, if available."""
        return self.resolve_global_default(self.get_all_llm_model_info()).model

    @staticmethod
    def _is_selectable(model: LLMModel) -> bool:
        return model.enabled and model.status == ModelStatus.ENABLED

    def get_admin_global_default_choice(self) -> AdminGlobalDefault | None:
        """Read the TTL-cached choice, retaining the last successful value during an outage."""
        with self._default_cache_lock:
            if monotonic() < self._default_cache_expires_at:
                return self._admin_global_default_cache
            try:
                with get_session() as session:
                    choice = AdminGlobalDefaultRepository.get(session)
            except Exception:
                if not self._default_load_failed:
                    logger.warning("Failed to load admin global default; reusing last known choice.")
                self._default_load_failed = True
            else:
                self._admin_global_default_cache = choice
                self._default_load_failed = False
            self._default_cache_expires_at = monotonic() + self.PERSISTED_SETTINGS_CACHE_TTL_SECONDS
            return self._admin_global_default_cache

    def set_admin_global_default(self, base_name: str, admin_id: str, admin_name: str) -> AdminGlobalDefault:
        """Save a known and currently selectable model or auto-router by base name."""
        model = next((m for m in self.get_all_llm_model_info() if m.base_name == base_name), None)
        if model is None:
            raise ValidationException(f"Unknown LLM model or router: {base_name}")
        if not self._is_selectable(model):
            reason = "model is deprecated" if model.status == ModelStatus.DEPRECATED else "model is not available"
            raise ModelNotSelectableError(base_name, reason)
        # Serialize the write with refreshes so an older read cannot overwrite this choice.
        with self._default_cache_lock:
            with get_session() as session:
                choice = AdminGlobalDefaultRepository.replace(session, base_name, admin_id, admin_name)
            self._admin_global_default_cache = choice
            self._default_cache_expires_at = monotonic() + self.PERSISTED_SETTINGS_CACHE_TTL_SECONDS
            self._default_load_failed = False
        return choice

    def clear_admin_global_default(self) -> None:
        with self._default_cache_lock:
            with get_session() as session:
                AdminGlobalDefaultRepository.clear(session)
            self._admin_global_default_cache = None
            self._default_cache_expires_at = monotonic() + self.PERSISTED_SETTINGS_CACHE_TTL_SECONDS
            self._default_load_failed = False

    def resolve_global_default(self, models: list[LLMModel]) -> GlobalDefaultResolution:
        """Choose the admin selection, configured global default, or a safe catalog fallback."""
        choice = self.get_admin_global_default_choice()
        saved_name = choice.base_name if choice else None
        selectable = [model for model in models if self._is_selectable(model)]
        if saved_name:
            saved_model = next((model for model in selectable if model.base_name == saved_name), None)
            if saved_model:
                return GlobalDefaultResolution(saved_model, "admin", saved_name)
        configured = self._prefer_litellm_router(
            [model for model in selectable if model.default or model.is_default_for(ModelCategory.GLOBAL)]
        )
        if configured:
            return GlobalDefaultResolution(configured, "configured", saved_name)
        return GlobalDefaultResolution(next(iter(selectable), None), "fallback" if selectable else None, saved_name)

    @staticmethod
    def _prefer_litellm_router(models: list[LLMModel]) -> Optional[LLMModel]:
        """Pick the global default from candidate models: a LiteLLM auto-router wins over concrete models,
        otherwise the first candidate. Shared by every global-default lookup so they never disagree.

        Reads the model catalog only (no get_allowed_router_options()): default_llm_model is evaluated at
        import time (core.utils default args), and that path imports codemie.enterprise, which creates a
        circular import.
        """
        return next((model for model in models if model.is_declared_litellm_router()), next(iter(models), None))

    def _populate_missing_categories_with_global_default(self, category_models: dict, global_default: LLMModel):
        """Add the global default model for categories without a specific default."""
        for category in ModelCategory:
            if category.value not in category_models and category != ModelCategory.GLOBAL:
                category_models[category.value] = global_default

    @property
    def default_llm_model(self) -> str:
        """Retrieve the default LLM model/router and return name of first one.

        Checks both concrete models and routers for default status. Routers are checked first
        since they provide intelligent routing capabilities.
        """
        default_model = self.resolve_global_default(self.get_all_llm_model_info()).model
        if default_model is None:
            raise ValueError("No selectable LLM model is available for the global default.")
        return default_model.base_name

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

    def get_model_status_overrides(self, *, force_refresh: bool = False) -> dict[str, str] | None:
        """Cache overrides for a short TTL; admin writes force a refresh after committing.

        Failed reads are throttled by the same TTL and retain the last successful snapshot.
        Warn once per outage, resetting only after a successful read.
        """
        with self._status_cache_lock:
            if not force_refresh and monotonic() < self._status_cache_expires_at:
                return self._status_override_cache
            try:
                with get_session() as session:
                    status_rows = LLMModelStatusRepository.get_all(session)
                    status_by_base_name = {row.base_name: row.status for row in status_rows}
            except Exception:
                if not self._status_load_failed:
                    if self._status_override_cache is None:
                        logger.warning(
                            "Failed to load persisted LLM model status overrides and no cached overrides "
                            "are available; using unmerged catalog."
                        )
                    else:
                        logger.warning(
                            "Failed to load persisted LLM model status overrides; reusing last known values."
                        )
                self._status_load_failed = True
            else:
                self._status_override_cache = status_by_base_name
                self._status_load_failed = False
            self._status_cache_expires_at = monotonic() + self.PERSISTED_SETTINGS_CACHE_TTL_SECONDS
            return self._status_override_cache

    def _merge_persisted_status(self, models: list[LLMModel]) -> None:
        """Overlay CodeMie-owned persisted status onto the provider-sourced model catalog, in place."""
        status_by_base_name = self.get_model_status_overrides()
        if status_by_base_name is None:
            return
        for model in models:
            persisted_status = status_by_base_name.get(model.base_name)
            if not persisted_status:
                model.status = ModelStatus.ENABLED
                continue
            try:
                model.status = ModelStatus(persisted_status)
            except ValueError:
                logger.warning(
                    f"Persisted status '{persisted_status}' for model '{model.base_name}' is not a "
                    f"valid ModelStatus value; falling back to enabled."
                )
                model.status = ModelStatus.ENABLED

    def initialize_default_litellm_models(self, user_models: LiteLLMModels) -> None:
        self._apply_router_output_limits(user_models.chat_models)
        # Merge in CodeMie-owned persisted status before storing the models
        self._merge_persisted_status(user_models.chat_models)
        self._merge_persisted_status(user_models.embedding_models)

        # Store the models
        self._default_litellm_models = user_models.chat_models
        self._default_litellm_embeddings = user_models.embedding_models
        self._default_litellm_audio_transcription_models = user_models.audio_transcription_models
        self._litellm_route_costs = user_models.route_costs

        logger.info(
            f"Initialized {len(self._default_litellm_models)} unique LiteLLM chat models, "
            f"{len(self._default_litellm_embeddings)} unique LiteLLM embedding models "
            f"and {len(self._default_litellm_audio_transcription_models)} unique LiteLLM STT models"
        )

    @property
    def default_litellm_models(self) -> list[LLMModel]:
        """Get default LiteLLM chat models initialized on startup"""
        return self._default_litellm_models

    @property
    def default_litellm_embeddings(self) -> list[LLMModel]:
        """Get default LiteLLM embedding models initialized on startup"""
        return self._default_litellm_embeddings

    @property
    def default_litellm_audio_transcription_models(self) -> list[LLMModel]:
        """Get default LiteLLM speech-to-text models initialized on startup"""
        return self._default_litellm_audio_transcription_models

    def get_allowed_models(self, user: User) -> LiteLLMModels:
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
                chat_models=self.get_all_llm_model_info(),
                embedding_models=self.get_all_embedding_model_info(),
                audio_transcription_models=self.get_all_audio_transcription_model_info(),
            )

        # If user is not external, return all enabled models (no restrictions)
        if not user.is_external_user:
            return LiteLLMModels(
                chat_models=self.get_all_llm_model_info(),
                embedding_models=self.get_all_embedding_model_info(),
                audio_transcription_models=self.get_all_audio_transcription_model_info(),
            )

        # External user - check for LiteLLM integration
        logger.debug(f"Checking LiteLLM integration for external user {user.id}")

        user_models = get_user_allowed_models(user_id=user.id, user_applications=user.project_names)

        if not user_models or not (user_models.chat_models or user_models.audio_transcription_models):
            # No user-specific integration - return default LiteLLM models
            logger.info(f"No LiteLLM integration for user {user.id}, using default models")
            return LiteLLMModels(
                chat_models=self.get_all_llm_model_info(),
                embedding_models=self.get_all_embedding_model_info(),
                audio_transcription_models=self.get_all_audio_transcription_model_info(),
            )

        if not user_models.chat_models:
            # Transcription-only integration: keep the default chat catalog, use the user's own transcription models
            logger.info(f"LiteLLM integration of user {user.id} has only audio transcription models")
            return LiteLLMModels(
                chat_models=self.get_all_llm_model_info(),
                embedding_models=user_models.embedding_models or self.get_all_embedding_model_info(),
                audio_transcription_models=user_models.audio_transcription_models,
            )

        # Return user-specific models. These come from the org's own LiteLLM integration and were
        # never passed through initialize_default_litellm_models, so the persisted status override
        # must be merged here as well - otherwise an admin disabling/deprecating a model has no
        # effect for external users with a LiteLLM integration.
        self._merge_persisted_status(user_models.chat_models)
        self._merge_persisted_status(user_models.embedding_models)

        logger.info(
            f"Returning user-specific LiteLLM models for {user.id}: "
            f"{len(user_models.chat_models)} chat, {len(user_models.embedding_models)} embedding, "
            f"{len(user_models.audio_transcription_models)} audio transcription"
        )
        return user_models

    def _filter_models_by_visibility(self, models: list[LLMModel], include_all: bool) -> list[LLMModel]:
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
        # Also drop models whose persisted status is DISABLED (narrower than the provider-sourced
        # `enabled` signal, which is already applied upstream in get_model_info).
        filtered_models = [
            model for model in models if model.forbidden_for_web is not True and model.status != ModelStatus.DISABLED
        ]

        logger.debug(
            f"Filtered models for web: {len(models)} total, "
            f"{len(filtered_models)} web-suitable, {len(models) - len(filtered_models)} hidden from web"
        )

        return filtered_models

    def _filter_models_by_project(self, models: list[LLMModel], project: Application | None) -> list[LLMModel]:
        """Filter models based on project's allowed_models list.

        None allowed_models = no restriction (all models pass through).
        Empty allowed_models = explicit deny all (no models pass through).
        """
        from codemie.service.llm.model_availability_service import ProjectModelPolicy

        if project is None:
            return models

        # Membership only, so a whitelist without a default_model does not fail the listing.
        policy = ProjectModelPolicy.for_application(project, require_default=False)
        filtered = [model for model in models if policy.allows(model.base_name, model.deployment_name)]

        logger.debug(
            f"Filtered models by project {project.id}: {len(models)} total, "
            f"{len(filtered)} allowed by project, {len(models) - len(filtered)} excluded"
        )

        return filtered

    def _select_default_model(self, allowed_models: list[LLMModel], project: Application | None) -> LLMModel | None:
        """Select a default model, respecting project-level allowed_models narrowing.

        When the project's allowed_models set excludes the platform default, this method
        selects the first allowed model as a fallback default.

        Args:
            allowed_models: List of models to choose from
            project: Optional project for narrowing (already applied to allowed_models by caller)

        Returns:
            The admin-configured project default if set and in allowed_models,
            otherwise the platform default model if it's in allowed_models,
            otherwise the first allowed model,
            otherwise None if the allowed_models list is empty.
        """
        if not allowed_models:
            logger.debug("No allowed models available; cannot select a default")
            return None

        # Check stored project default first
        if project and project.default_model:
            stored_default = next(
                (
                    m
                    for m in allowed_models
                    if (m.base_name == project.default_model or m.deployment_name == project.default_model)
                ),
                None,
            )
            if stored_default:
                logger.debug(
                    f"Selecting admin-configured default model '{stored_default.base_name}' (project={project.id})"
                )
                return stored_default

        # Try to return the platform default if it's in the allowed list
        default_model = next((m for m in allowed_models if m.default), None)
        if default_model:
            logger.debug(
                f"Selecting default model '{default_model.base_name}' (project={project.id if project else 'None'})"
            )
            return default_model

        # Platform default is not allowed; use first allowed model as fallback
        fallback = allowed_models[0]
        logger.debug(
            f"Platform default is excluded by project; selecting fallback '{fallback.base_name}' "
            f"(project={project.id if project else 'None'})"
        )
        return fallback

    def get_allowed_chat_models(
        self, user: User, include_all: bool = False, project: Application | None = None
    ) -> list[LLMModel]:
        """
        Get list of LLM models allowed for user, optionally filtering out web-forbidden models and by project.

        Logic:
        - If LLM_PROXY_ENABLED is False: return all enabled models from config
        - If user is NOT external: return all enabled models from config
        - If user IS external:
            - Check for LiteLLM integration (user-level or project-level)
            - If found: return models from user's LiteLLM integration
            - If not found: return default LiteLLM models from startup
        - If include_all is False: filter out models where forbidden_for_web=True
        - If project is provided: filter models to only those in project's allowed_models
        - When a project excludes the platform default: _select_default_model() provides a fallback

        Args:
            user: User object with id, user_type, and applications
            include_all: If True, return all models. If False (default), filter out models forbidden for web
            project: Optional Application object to apply project-level model narrowing

        Returns:
            List of LLMModel instances accessible to user, filtered by visibility and project rules
        """
        from codemie.service.llm.model_availability_service import PROJECT_MODEL_OVERRIDE_FEATURE

        user_override = self.get_user_model_override(user, project)

        if project and not customer_config.is_feature_enabled(PROJECT_MODEL_OVERRIDE_FEATURE):
            project = None

        user_models = self.get_allowed_models(user)

        chat_models = [m for m in user_models.chat_models if not m.is_declared_litellm_router()]
        models = self._filter_models_by_visibility(chat_models, include_all)
        override_models = self._filter_models_by_user_override(models, user_override) if user_override else []
        # An override whose models all left the catalog must not leave the user with nothing to select.
        models = override_models or self._filter_models_by_project(models, project)

        candidates = self._filter_models_by_visibility(user_models.chat_models, include_all)
        selected = self.resolve_global_default(candidates).model
        selected_name = selected.base_name if selected else None
        projected = [
            model.model_copy(
                update=self._global_default_fields(model.default_for_categories, model.base_name == selected_name)
            )
            for model in models
        ]
        return self._apply_premium_flags(projected)

    @staticmethod
    def get_user_model_override(user: User, project: Application | None) -> UserModelOverride | None:
        """Return the user's model override for the project, or None when the feature is off or none exists."""
        from codemie.service.llm.user_override_cache import load_user_override

        if not project or not customer_config.is_feature_enabled("userModelOverride"):
            return None
        return load_user_override(user.id, project.id)

    @staticmethod
    def _filter_models_by_user_override(models: list[LLMModel], override: UserModelOverride) -> list[LLMModel]:
        """Keep only models listed in the user's override; takes precedence over the project whitelist."""
        from codemie.service.llm.model_availability_service import model_in_override

        return [
            model
            for model in models
            if model_in_override(override.allowed_models, model.base_name, model.deployment_name)
        ]

    @staticmethod
    def _apply_premium_flags(models: list[LLMModel]) -> list[LLMModel]:
        """Set is_premium on each model when the premium-models budget feature is enabled.

        When no premium_models budget is configured the field stays None and is omitted
        from API responses (response_model_exclude_none=True on the router).
        """
        from codemie.enterprise.litellm.dependencies import is_premium_model, is_premium_models_enabled

        if is_premium_models_enabled():
            for model in models:
                model.is_premium = is_premium_model(model.base_name)
        return models

    def get_allowed_image_generation_models(self, user: User, include_all: bool = False) -> list[LLMModel]:
        """Get list of image generation models allowed for user.

        Image-generation models should remain selectable even when a model is
        hidden from the general web chat model picker via forbidden_for_web.
        The include_all flag is therefore intentionally ignored for that signal
        and kept only for API compatibility. The enabled/persisted-status narrowing
        that applies to every other get_allowed_* method still applies here -
        a disabled or provider-disabled model must not remain selectable for
        image generation either.
        """
        user_models = self.get_allowed_models(user)
        return [
            model
            for model in user_models.chat_models
            if model.supports_image_generation and model.enabled and model.status != ModelStatus.DISABLED
        ]

    def get_allowed_embedding_models(self, user: User, include_all: bool = False) -> list[LLMModel]:
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

    def get_allowed_audio_transcription_models(self, user: User) -> list[LLMModel]:
        """STT models allowed for the user: the env-configured model and the LiteLLM ones (see merge_stt_models)."""
        from codemie.core.constants import LLMProxyMode

        litellm_models = (
            self.get_allowed_models(user).audio_transcription_models
            if LLMProxyMode.lite_llm == config.LLM_PROXY_MODE
            else []
        )
        return merge_stt_models(build_legacy_stt_model(), litellm_models)

    def resolve_stt_model(self, user: User, model_name: str | None = None) -> LLMModel | None:
        """Pick the STT model for a request: the named one, or the first allowed model.

        Raises:
            UnknownSttModelError: if a name is given but is not among the models allowed for the user.
        """
        return pick_stt_model(self.get_allowed_audio_transcription_models(user), model_name)

    def assert_model_selectable(
        self, model_name: str, *, previous_model_name: str | None = None, allow_deprecated: bool = False
    ) -> None:
        """Raise ModelNotSelectableError if the model cannot be selected for new work.

        Looks up catalog models by base_name or deployment_name, then router names using
        their computed status. Unknown names are not guarded here - the caller's own
        validation handles unknown models elsewhere.
        An existing selection may be retained on edit, including through its other catalog name.
        Legacy IDE adoption may allow deprecated models, but still rejects disabled models.
        """
        model_name = model_name.strip() if model_name else model_name
        previous_model_name = previous_model_name.strip() if previous_model_name else None
        if previous_model_name is not None and model_name == previous_model_name:
            return
        model = next(
            (m for m in self.get_all_llm_model_info() if model_name in (m.base_name, m.deployment_name)),
            None,
        )
        if model is not None and previous_model_name in (model.base_name, model.deployment_name):
            return
        if model is None:
            model = next(
                (
                    router
                    for router in self.get_allowed_router_options(include_all=True)
                    if router.base_name == model_name
                ),
                None,
            )
        if model is None:
            return
        if model.status == ModelStatus.DEPRECATED and not allow_deprecated:
            raise ModelNotSelectableError(model_name, "model is deprecated")
        if not model.enabled or model.status == ModelStatus.DISABLED:
            raise ModelNotSelectableError(model_name, "model is not available")


llm_service = LLMService(llm_config)
