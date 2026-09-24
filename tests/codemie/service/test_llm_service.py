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
from unittest.mock import Mock, patch

import pytest
from pathlib import Path
from codemie.service.llm_service.llm_service import LLMService, LLMModel, LLMConfig
from codemie.configs.llm_config import (
    LiteLLMRouterConfig,
    LLMProvider,
    ModelCategory,
    RouterTier,
    RouterTiers,
    RoutingMode,
)


class TestLLMService:
    @pytest.fixture
    def llm_service(self):
        self.llm_config = LLMConfig(
            yaml_file=Path('tests/service/llm_test_config.yaml'),
            llm_models=[
                LLMModel(
                    label='Model 0',
                    base_name='model-0',
                    deployment_name='deployment-0',
                    enabled=True,
                    default_for_categories=[],
                ),
                LLMModel(
                    label='Model A',
                    base_name='model-a',
                    deployment_name='deployment-a',
                    enabled=True,
                    default_for_categories=[ModelCategory.GLOBAL],
                ),
                LLMModel(label='Model B', base_name='model-b', deployment_name='deployment-b', enabled=False),
                LLMModel(
                    label='Model C',
                    base_name='model-c',
                    deployment_name='deployment-c',
                    enabled=True,
                    default_for_categories=[ModelCategory.CODE, ModelCategory.DOCUMENTATION],
                ),
                LLMModel(
                    label='Model D',
                    base_name='model-d',
                    deployment_name='deployment-d',
                    enabled=True,
                    default_for_categories=[ModelCategory.SUMMARIZATION],
                ),
            ],
            embeddings_models=[
                LLMModel(
                    label='Embedding A',
                    base_name='embedding-a',
                    deployment_name='embedding-deployment-a',
                    enabled=True,
                    default_for_categories=[ModelCategory.GLOBAL],
                )
            ],
        )
        return LLMService(self.llm_config)

    def test_get_model_info(self, llm_service):
        expected_count_of_models = 4  # only enabled

        models = llm_service.get_model_info(self.llm_config.llm_models)
        assert len(models) == expected_count_of_models
        assert models[1].label == 'Model A'
        assert models[1].base_name == 'model-a'

    def test_get_all_llm_model_info(self, llm_service):
        expected_count_of_models = 4  # only enabled

        models = llm_service.get_all_llm_model_info()

        assert len(models) == expected_count_of_models
        assert models[1].label == 'Model A'
        assert models[1].base_name == 'model-a'

    def test_get_all_embedding_model_info(self, llm_service):
        models = llm_service.get_all_embedding_model_info()
        assert len(models) == 1
        assert models[0].label == 'Embedding A'
        assert models[0].base_name == 'embedding-a'

    def test_get_deployment_name(self, llm_service):
        deployment_name = llm_service.get_llm_deployment_name('model-a')
        assert deployment_name == 'deployment-a'

    def test_create_model_types_enum(self, llm_service):
        enum = llm_service.create_model_types_enum()
        assert hasattr(enum, 'MODEL_A')
        assert enum.MODEL_A.value == 'deployment-a'

    def test_default_model(self, llm_service):
        expected_model = llm_service.llm_config.llm_models[1]

        result_model = llm_service.default_llm_model

        assert result_model == expected_model.base_name

    def test_default_model_value_error(self, llm_service):
        llm_service.llm_config.llm_models = [llm_service.llm_config.llm_models[0]]

        with pytest.raises(ValueError):
            _model_name = llm_service.default_llm_model


# Tests for visibility filtering functionality
class TestModelVisibilityFiltering:
    """Tests for _filter_models_by_visibility and related functionality"""

    @pytest.fixture
    def llm_service(self):
        llm_config = LLMConfig(
            yaml_file=Path('tests/service/llm_test_config.yaml'),
            llm_models=[
                LLMModel(
                    label='Visible Model',
                    base_name='visible-model',
                    deployment_name='visible-model',
                    enabled=True,
                    forbidden_for_web=False,
                ),
                LLMModel(
                    label='Hidden Model',
                    base_name='hidden-model',
                    deployment_name='hidden-model',
                    enabled=True,
                    forbidden_for_web=True,
                ),
                LLMModel(
                    label='Default Visible Model',
                    base_name='default-visible',
                    deployment_name='default-visible',
                    enabled=True,
                    # forbidden_for_web not set (defaults to False - visible)
                ),
            ],
            embeddings_models=[],
        )
        return LLMService(llm_config)

    def test_filter_models_include_all_true(self, llm_service):
        """When include_all=True, all models should be returned"""
        models = llm_service.llm_config.llm_models
        filtered = llm_service._filter_models_by_visibility(models, include_all=True)

        assert len(filtered) == 3
        assert any(m.base_name == 'visible-model' for m in filtered)
        assert any(m.base_name == 'hidden-model' for m in filtered)
        assert any(m.base_name == 'default-visible' for m in filtered)

    def test_filter_models_include_all_false(self, llm_service):
        """When include_all=False, only non-forbidden models should be returned"""
        models = llm_service.llm_config.llm_models
        filtered = llm_service._filter_models_by_visibility(models, include_all=False)

        assert len(filtered) == 2
        assert any(m.base_name == 'visible-model' for m in filtered)
        assert any(m.base_name == 'default-visible' for m in filtered)
        assert not any(m.base_name == 'hidden-model' for m in filtered)

    def test_filter_models_none_treated_as_visible(self, llm_service):
        """Models with forbidden_for_web=None should be treated as visible (not forbidden)"""
        model_with_none = LLMModel(
            label='None Visibility',
            base_name='none-visibility',
            deployment_name='none-visibility',
            enabled=True,
            forbidden_for_web=None,
        )

        filtered = llm_service._filter_models_by_visibility([model_with_none], include_all=False)

        assert len(filtered) == 1
        assert filtered[0].base_name == 'none-visibility'

    def test_get_allowed_chat_models_with_include_all(self, llm_service):
        """get_allowed_chat_models should respect include_all parameter"""
        from unittest.mock import Mock

        # Mock user
        user = Mock()
        user.is_external_user = False

        # Default request (include_all=False - filters forbidden models)
        filtered_models = llm_service.get_allowed_chat_models(user, include_all=False)
        assert len(filtered_models) == 2
        assert not any(m.base_name == 'hidden-model' for m in filtered_models)

        # Include all request (include_all=True - shows all models)
        all_models = llm_service.get_allowed_chat_models(user, include_all=True)
        assert len(all_models) == 3
        assert any(m.base_name == 'hidden-model' for m in all_models)

    def test_get_allowed_image_generation_models_ignores_forbidden_for_web(self, llm_service):
        """Image generation models should be returned even if hidden from the web chat picker."""
        from unittest.mock import Mock

        hidden_image_model = LLMModel(
            label='Hidden Image Model',
            base_name='hidden-image-model',
            deployment_name='hidden-image-model',
            enabled=True,
            supports_image_generation=True,
            forbidden_for_web=True,
        )
        visible_image_model = LLMModel(
            label='Visible Image Model',
            base_name='visible-image-model',
            deployment_name='visible-image-model',
            enabled=True,
            supports_image_generation=True,
            forbidden_for_web=False,
        )
        llm_service.llm_config.llm_models = [hidden_image_model, visible_image_model]

        user = Mock()
        user.is_external_user = False

        filtered_models = llm_service.get_allowed_image_generation_models(user, include_all=False)

        assert len(filtered_models) == 2
        assert any(m.base_name == 'hidden-image-model' for m in filtered_models)
        assert any(m.base_name == 'visible-image-model' for m in filtered_models)

    def test_get_allowed_embedding_models_with_include_all(self, llm_service):
        """get_allowed_embedding_models should respect include_all parameter"""
        from unittest.mock import Mock

        # Create service with embedding models
        llm_config = LLMConfig(
            yaml_file=Path('tests/service/llm_test_config.yaml'),
            llm_models=[],
            embeddings_models=[
                LLMModel(
                    label='Visible Embedding',
                    base_name='visible-embedding',
                    deployment_name='visible-embedding',
                    enabled=True,
                    forbidden_for_web=False,
                ),
                LLMModel(
                    label='Hidden Embedding',
                    base_name='hidden-embedding',
                    deployment_name='hidden-embedding',
                    enabled=True,
                    forbidden_for_web=True,
                ),
            ],
        )
        service = LLMService(llm_config)

        user = Mock()
        user.is_external_user = False

        # Filtered request (include_all=False)
        filtered_embeddings = service.get_allowed_embedding_models(user, include_all=False)
        assert len(filtered_embeddings) == 1
        assert filtered_embeddings[0].base_name == 'visible-embedding'

        # Include all request (include_all=True)
        all_embeddings = service.get_allowed_embedding_models(user, include_all=True)
        assert len(all_embeddings) == 2

    def test_get_allowed_image_generation_models(self, llm_service):
        """Image generation models should ignore forbidden_for_web and only filter by capability."""
        from unittest.mock import Mock

        llm_service.llm_config.llm_models = [
            LLMModel(
                label='Visible Image Model',
                base_name='visible-image-model',
                deployment_name='visible-image-model',
                enabled=True,
                supports_image_generation=True,
                forbidden_for_web=False,
            ),
            LLMModel(
                label='Hidden Image Model',
                base_name='hidden-image-model',
                deployment_name='hidden-image-model',
                enabled=True,
                supports_image_generation=True,
                forbidden_for_web=True,
            ),
            LLMModel(
                label='Text Model',
                base_name='text-model',
                deployment_name='text-model',
                enabled=True,
                supports_image_generation=False,
                forbidden_for_web=False,
            ),
        ]

        user = Mock()
        user.is_external_user = False

        filtered_models = llm_service.get_allowed_image_generation_models(user, include_all=False)
        assert [model.base_name for model in filtered_models] == ['visible-image-model', 'hidden-image-model']

        all_models = llm_service.get_allowed_image_generation_models(user, include_all=True)
        assert [model.base_name for model in all_models] == ['visible-image-model', 'hidden-image-model']


class TestApplyPremiumFlags:
    """Tests for is_premium population in get_allowed_chat_models."""

    @pytest.fixture(autouse=True)
    def clear_premium_caches(self):
        from codemie.enterprise.litellm.dependencies import is_premium_model, is_premium_models_enabled

        is_premium_models_enabled.cache_clear()
        is_premium_model.cache_clear()
        yield
        is_premium_models_enabled.cache_clear()
        is_premium_model.cache_clear()

    @contextlib.contextmanager
    def _premium_budget(self, enabled: bool):
        from codemie.configs.budget_config import budget_config
        from codemie.configs.config import PredefinedBudgetConfig

        current = [b for b in budget_config.predefined_budgets if b.budget_category != "premium_models"]
        if enabled:
            current.append(
                PredefinedBudgetConfig(
                    budget_id="premium_models",
                    name="Premium",
                    description=None,
                    soft_budget=0.0,
                    max_budget=0.0,
                    budget_duration="30d",
                    budget_category="premium_models",
                )
            )
        with patch.object(budget_config, "predefined_budgets", current):
            yield

    def _models(self):
        return [
            LLMModel(base_name="claude-opus-4-1", deployment_name="claude-opus-4-1", enabled=True),
            LLMModel(base_name="gpt-4o", deployment_name="gpt-4o", enabled=True),
        ]

    def test_premium_flag_set_when_feature_enabled_and_alias_matches(self):
        from codemie.configs.config import config
        from codemie.configs.llm_config import LiteLLMModels
        from codemie.service.llm_service.llm_service import llm_service

        with self._premium_budget(True), patch.object(config, "LITELLM_PREMIUM_MODELS_ALIASES", ["opus"]):
            with patch.object(llm_service, "get_allowed_models") as mock_allowed:
                mock_allowed.return_value = LiteLLMModels(chat_models=self._models(), embedding_models=[])
                result = llm_service.get_allowed_chat_models(user=Mock())

        by_name = {m.base_name: m for m in result}
        assert by_name["claude-opus-4-1"].is_premium is True
        assert by_name["gpt-4o"].is_premium is False

    def test_premium_flag_none_when_feature_disabled(self):
        from codemie.configs.config import config
        from codemie.configs.llm_config import LiteLLMModels
        from codemie.service.llm_service.llm_service import llm_service

        with self._premium_budget(False), patch.object(config, "LITELLM_PREMIUM_MODELS_ALIASES", ["opus"]):
            with patch.object(llm_service, "get_allowed_models") as mock_allowed:
                mock_allowed.return_value = LiteLLMModels(chat_models=self._models(), embedding_models=[])
                result = llm_service.get_allowed_chat_models(user=Mock())

        assert all(m.is_premium is None for m in result)


class TestLiteLLMAutoRouterCatalog:
    """A model declared as a LiteLLM auto-router (litellm_router.is_router=True) is not a real
    deployment: it must not appear in the plain chat-model catalog, and must surface instead as
    a router option carrying router_type='litellm_auto' plus whatever strategy/tiers/
    classifier_model the live LiteLLM catalog declares for it in model_info.litellm_router."""

    @staticmethod
    def _router_model(**overrides) -> LLMModel:
        litellm_router = overrides.pop("litellm_router", None) or LiteLLMRouterConfig(
            strategy=RoutingMode.CLASSIFIER,
            classifier_model="gpt-5-mini",
            tiers=RouterTiers(
                simple=RouterTier(model="gpt-5-mini", label="GPT-5 mini"),
                medium=RouterTier(model="gpt-5", label="GPT-5"),
                complex=RouterTier(model="gpt-5", label="GPT-5"),
                reasoning=RouterTier(model="gpt-5-pro", label="GPT-5 Pro"),
            ),
        )
        defaults: dict = {
            "base_name": "smart-router",
            "deployment_name": "smart-router",
            "label": "Smart Router",
            "enabled": True,
            "provider": LLMProvider.AZURE_OPENAI,
            "litellm_router": litellm_router,
        }
        defaults.update(overrides)
        return LLMModel(**defaults)

    @staticmethod
    def _config(models: list[LLMModel]) -> LLMConfig:
        return LLMConfig(yaml_file=Path('tests/service/llm_test_config.yaml'), llm_models=models, embeddings_models=[])

    def test_excluded_from_allowed_chat_models(self):
        service = LLMService(self._config([self._router_model()]))

        result = service.get_allowed_chat_models(user=Mock(is_external_user=False))

        assert result == []

    def test_appears_in_router_options_with_litellm_auto_type(self):
        service = LLMService(self._config([self._router_model()]))

        options = service.get_allowed_router_options()

        assert len(options) == 1
        option = options[0]
        assert option.base_name == "smart-router"
        assert option.label == "Smart Router"
        assert option.is_router is True
        assert option.router_type == "litellm_auto"
        assert option.provider == LLMProvider.AZURE_OPENAI
        assert option.strategy == RoutingMode.CLASSIFIER
        assert option.classifier_model == "gpt-5-mini"
        assert option.tiers is not None
        assert option.tiers.simple.model == "gpt-5-mini"
        assert option.tiers.reasoning.label == "GPT-5 Pro"

    def test_is_premium_checks_own_base_name_like_a_regular_model(self):
        """A litellm_auto router's is_premium uses the exact same check as any regular
        model — is_premium_model(base_name) against LITELLM_PREMIUM_MODELS_ALIASES — applied
        to the router's OWN base_name. No tier-model inference, no separate router-only flag:
        a router whose tiers reference a premium-alias-matching model (e.g. opus) but whose
        own base_name doesn't match must report False; a router whose own base_name matches
        must report True regardless of what its tiers contain."""
        from codemie.configs.config import config
        from codemie.configs.budget_config import budget_config
        from codemie.configs.config import PredefinedBudgetConfig
        from codemie.enterprise.litellm.dependencies import is_premium_model, is_premium_models_enabled

        is_premium_models_enabled.cache_clear()
        is_premium_model.cache_clear()
        current = [b for b in budget_config.predefined_budgets if b.budget_category != "premium_models"]
        current.append(
            PredefinedBudgetConfig(
                budget_id="premium_models",
                name="Premium",
                description=None,
                soft_budget=0.0,
                max_budget=0.0,
                budget_duration="30d",
                budget_category="premium_models",
            )
        )
        not_premium = self._router_model(
            base_name="claude-router-standard-v1",
            litellm_router=LiteLLMRouterConfig(
                tiers=RouterTiers(
                    simple=RouterTier(model="claude-haiku"),
                    medium=RouterTier(model="claude-haiku"),
                    complex=RouterTier(model="claude-opus-5-5"),
                    reasoning=RouterTier(model="claude-opus-5-5"),
                ),
            ),
        )
        premium = self._router_model(base_name="claude-opus-router-v1", litellm_router=LiteLLMRouterConfig())
        try:
            with (
                patch.object(budget_config, "predefined_budgets", current),
                patch.object(config, "LITELLM_PREMIUM_MODELS_ALIASES", ["opus"]),
            ):
                is_premium_models_enabled.cache_clear()
                is_premium_model.cache_clear()
                service = LLMService(self._config([not_premium, premium]))
                options = {o.base_name: o for o in service.get_allowed_router_options()}
        finally:
            is_premium_models_enabled.cache_clear()
            is_premium_model.cache_clear()

        assert options["claude-router-standard-v1"].is_premium is False
        assert options["claude-opus-router-v1"].is_premium is True

    def test_is_premium_none_when_premium_feature_disabled(self):
        from codemie.configs.budget_config import budget_config
        from codemie.enterprise.litellm.dependencies import is_premium_model, is_premium_models_enabled

        is_premium_models_enabled.cache_clear()
        is_premium_model.cache_clear()
        no_premium_budget = [b for b in budget_config.predefined_budgets if b.budget_category != "premium_models"]
        model = self._router_model(base_name="claude-opus-router-v1", litellm_router=LiteLLMRouterConfig())
        try:
            with patch.object(budget_config, "predefined_budgets", no_premium_budget):
                is_premium_models_enabled.cache_clear()
                is_premium_model.cache_clear()
                service = LLMService(self._config([model]))
                options = service.get_allowed_router_options()
        finally:
            is_premium_models_enabled.cache_clear()
            is_premium_model.cache_clear()

        assert options[0].is_premium is None

    def test_respects_forbidden_for_web(self):
        model = self._router_model(forbidden_for_web=True)
        service = LLMService(self._config([model]))

        assert service.get_allowed_router_options(include_all=False) == []
        assert len(service.get_allowed_router_options(include_all=True)) == 1

    def test_plain_model_untouched(self):
        """A regular model (no litellm_router) must still appear only in the chat-model
        catalog, never in router options — this test guards against an overbroad filter."""
        plain = LLMModel(base_name="gpt-4o", deployment_name="gpt-4o", enabled=True)
        service = LLMService(self._config([plain]))

        assert [m.base_name for m in service.get_allowed_chat_models(user=Mock(is_external_user=False))] == ["gpt-4o"]
        assert service.get_allowed_router_options() == []
