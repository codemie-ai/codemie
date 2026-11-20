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
    LlmRouterOption,
    ModelCategory,
    ModelStatus,
    RouterTier,
    RouterTiers,
    RoutingMode,
)
from codemie.core.exceptions import ModelNotSelectableError


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

    def test_admin_catalog_is_global_and_filters_provider_availability_only(self, llm_service):
        def model(name, enabled=True, status=ModelStatus.ENABLED):
            return LLMModel(base_name=name, deployment_name=name, enabled=enabled, status=status)

        auto_model = model('auto')
        auto_model.litellm_router = LiteLLMRouterConfig()
        chat = [model('disabled-chat', status=ModelStatus.DISABLED), model('offline-chat', enabled=False), auto_model]
        embeddings = [model('embedding'), model('offline-embedding', enabled=False)]
        switchyard = LlmRouterOption(base_name='switchyard', router_type='switchyard', status=ModelStatus.DISABLED)
        auto = LlmRouterOption(base_name='auto', router_type='litellm_auto')
        with (
            patch.object(llm_service, 'get_all_llm_model_info', return_value=chat),
            patch.object(llm_service, 'get_all_embedding_model_info', return_value=embeddings),
            patch.object(llm_service, 'get_allowed_router_options', return_value=[switchyard, auto]) as routers,
            patch.object(
                llm_service, 'get_allowed_models', side_effect=AssertionError('User catalog must not be read')
            ),
            patch('codemie.enterprise.litellm.dependencies.is_premium_models_enabled', return_value=True),
            patch('codemie.enterprise.litellm.dependencies.is_premium_model', return_value=True),
        ):
            result = llm_service.get_admin_model_catalog()

        assert [(kind.value, item.base_name) for kind, item in result] == [
            ('chat', 'disabled-chat'),
            ('chat', 'auto'),
            ('embedding', 'embedding'),
            ('chat', 'switchyard'),
        ]
        assert result[0][1].status == ModelStatus.DISABLED
        assert result[0][1].is_premium is True
        assert result[1][1].is_declared_litellm_router()
        assert chat[0].is_premium is None  # Display metadata must not mutate the source catalog.
        routers.assert_called_once_with(include_all=True)

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
        llm_service.llm_config.llm_models[0].enabled = False

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

    def test_filter_models_status_enabled_included(self, llm_service):
        """status=ENABLED, enabled=True, include_all=False -> model is listed"""
        model = LLMModel(
            label='Enabled Status Model',
            base_name='enabled-status-model',
            deployment_name='enabled-status-model',
            enabled=True,
            status=ModelStatus.ENABLED,
        )

        filtered = llm_service._filter_models_by_visibility([model], include_all=False)

        assert len(filtered) == 1
        assert filtered[0].base_name == 'enabled-status-model'

    def test_filter_models_status_disabled_excluded(self, llm_service):
        """status=DISABLED, enabled=True, include_all=False -> model is absent"""
        model = LLMModel(
            label='Disabled Status Model',
            base_name='disabled-status-model',
            deployment_name='disabled-status-model',
            enabled=True,
            status=ModelStatus.DISABLED,
        )

        filtered = llm_service._filter_models_by_visibility([model], include_all=False)

        assert filtered == []

    def test_filter_models_status_deprecated_included(self, llm_service):
        """status=DEPRECATED, enabled=True, include_all=False -> model is listed with status preserved"""
        model = LLMModel(
            label='Deprecated Status Model',
            base_name='deprecated-status-model',
            deployment_name='deprecated-status-model',
            enabled=True,
            status=ModelStatus.DEPRECATED,
        )

        filtered = llm_service._filter_models_by_visibility([model], include_all=False)

        assert len(filtered) == 1
        assert filtered[0].base_name == 'deprecated-status-model'
        assert filtered[0].status == ModelStatus.DEPRECATED

    def test_filter_models_status_disabled_included_when_include_all_true(self, llm_service):
        """status=DISABLED, enabled=True, include_all=True -> include_all bypasses status narrowing"""
        model = LLMModel(
            label='Disabled Status Model',
            base_name='disabled-status-model',
            deployment_name='disabled-status-model',
            enabled=True,
            status=ModelStatus.DISABLED,
        )

        filtered = llm_service._filter_models_by_visibility([model], include_all=True)

        assert len(filtered) == 1
        assert filtered[0].base_name == 'disabled-status-model'

    def test_get_allowed_chat_models_provider_disabled_wins_over_deprecated_status(self, llm_service):
        """enabled=False, status=DEPRECATED, include_all=False -> model absent end-to-end
        (provider enabled=False already excludes the model before visibility filtering)."""
        from unittest.mock import Mock

        llm_service.llm_config.llm_models = [
            LLMModel(
                label='Disabled Provider Deprecated Status Model',
                base_name='disabled-provider-deprecated-status-model',
                deployment_name='disabled-provider-deprecated-status-model',
                enabled=False,
                status=ModelStatus.DEPRECATED,
            ),
        ]

        user = Mock()
        user.is_external_user = False

        filtered_models = llm_service.get_allowed_chat_models(user, include_all=False)

        assert not any(m.base_name == 'disabled-provider-deprecated-status-model' for m in filtered_models)

    def test_get_allowed_chat_models_user_override_with_no_catalog_match_falls_back_to_project(self, llm_service):
        from unittest.mock import Mock, patch

        user = Mock(is_external_user=False)
        stale_override = Mock(allowed_models=["removed-model"])

        with patch.object(llm_service, "get_user_model_override", return_value=stale_override):
            result = llm_service.get_allowed_chat_models(user)

        assert len(result) == 2

    def test_get_allowed_chat_models_user_override_narrows_when_models_match(self, llm_service):
        from unittest.mock import Mock, patch

        user = Mock(is_external_user=False)
        all_names = [m.base_name for m in llm_service.get_allowed_chat_models(user)]
        override = Mock(allowed_models=[all_names[0]])

        with patch.object(llm_service, "get_user_model_override", return_value=override):
            result = llm_service.get_allowed_chat_models(user)

        assert [m.base_name for m in result] == [all_names[0]]

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

    @patch("codemie.service.llm_service.llm_service.LLMModelStatusRepository")
    @patch("codemie.service.llm_service.llm_service.get_session")
    def test_get_allowed_image_generation_models_excludes_disabled_status(
        self, mock_get_session, mock_repo, llm_service
    ):
        """A persisted-status-disabled model must not remain selectable for image generation."""
        from unittest.mock import Mock

        mock_get_session.return_value.__enter__.return_value = Mock()
        mock_get_session.return_value.__exit__.return_value = False
        mock_repo.get_all.return_value = [Mock(base_name="disabled-image-model", status="disabled")]

        llm_service.llm_config.llm_models = [
            LLMModel(
                label='Available Image Model',
                base_name='available-image-model',
                deployment_name='available-image-model',
                enabled=True,
                supports_image_generation=True,
            ),
            LLMModel(
                label='Disabled Image Model',
                base_name='disabled-image-model',
                deployment_name='disabled-image-model',
                enabled=True,
                supports_image_generation=True,
            ),
            LLMModel(
                label='Provider Disabled Image Model',
                base_name='provider-disabled-image-model',
                deployment_name='provider-disabled-image-model',
                enabled=False,
                supports_image_generation=True,
            ),
        ]

        user = Mock()
        user.is_external_user = False

        filtered_models = llm_service.get_allowed_image_generation_models(user, include_all=False)

        assert [model.base_name for model in filtered_models] == ['available-image-model']

        # include_all continues to only bypass forbidden_for_web, not persisted/provider status.
        all_models = llm_service.get_allowed_image_generation_models(user, include_all=True)
        assert [model.base_name for model in all_models] == ['available-image-model']


class TestGetAllowedRouterOptionsStatusNarrowing:
    """`get_allowed_router_options` skips routers whose capable/efficient model is DISABLED."""

    def _service_with_router(self, capable_status: ModelStatus, efficient_status: ModelStatus):
        from codemie.configs.llm_config import LLMRouter, RoutingMode, SwitchyardConfig

        llm_config = LLMConfig(
            yaml_file=Path('tests/service/llm_test_config.yaml'),
            llm_models=[],
            embeddings_models=[],
        )
        service = LLMService(llm_config)

        capable = LLMModel(
            label='Capable Model',
            base_name='capable-model',
            deployment_name='capable-model',
            enabled=True,
            status=capable_status,
        )
        efficient = LLMModel(
            label='Efficient Model',
            base_name='efficient-model',
            deployment_name='efficient-model',
            enabled=True,
            status=efficient_status,
        )
        router = LLMRouter(
            base_name='router-model',
            label='Router Model',
            enabled=True,
            forbidden_for_web=False,
            switchyard=SwitchyardConfig(
                capable_model='capable-model',
                efficient_model='efficient-model',
                mode=RoutingMode.SIGNAL,
            ),
        )

        service.get_all_llm_model_info = Mock(return_value=[capable, efficient])
        service.get_llm_routers = Mock(return_value=[router])

        return service

    def test_router_skipped_when_capable_model_disabled_and_include_all_false(self):
        service = self._service_with_router(ModelStatus.DISABLED, ModelStatus.ENABLED)

        options = service.get_allowed_router_options(include_all=False)

        assert not any(o.base_name == 'router-model' for o in options)

    def test_router_skipped_when_efficient_model_disabled_and_include_all_false(self):
        service = self._service_with_router(ModelStatus.ENABLED, ModelStatus.DISABLED)

        options = service.get_allowed_router_options(include_all=False)

        assert not any(o.base_name == 'router-model' for o in options)

    def test_router_included_when_disabled_status_and_include_all_true(self):
        service = self._service_with_router(ModelStatus.DISABLED, ModelStatus.ENABLED)

        options = service.get_allowed_router_options(include_all=True)

        assert any(o.base_name == 'router-model' for o in options)

    def test_router_included_when_both_statuses_enabled(self):
        service = self._service_with_router(ModelStatus.ENABLED, ModelStatus.ENABLED)

        options = service.get_allowed_router_options(include_all=False)

        assert any(o.base_name == 'router-model' for o in options)


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
                llm_service.get_allowed_chat_models(user=Mock())


# Tests for project-level model narrowing
class TestProjectModelNarrowing:
    """Tests for _filter_models_by_project and related functionality"""

    @pytest.fixture
    def llm_service(self):
        llm_config = LLMConfig(
            yaml_file=Path('tests/service/llm_test_config.yaml'),
            llm_models=[
                LLMModel(
                    label='GPT-4',
                    base_name='gpt-4',
                    deployment_name='gpt-4',
                    enabled=True,
                    forbidden_for_web=False,
                ),
                LLMModel(
                    label='Claude Opus',
                    base_name='claude-opus',
                    deployment_name='claude-opus',
                    enabled=True,
                    forbidden_for_web=False,
                ),
                LLMModel(
                    label='Gemini Pro',
                    base_name='gemini-pro',
                    deployment_name='gemini-pro',
                    enabled=True,
                    forbidden_for_web=False,
                ),
            ],
            embeddings_models=[],
        )
        return LLMService(llm_config)

    def test_filter_models_by_project_narrows_to_allowed(self, llm_service):
        """When project has allowed_models set, only those models are returned."""
        from codemie.core.models import Application

        all_models = llm_service.llm_config.llm_models

        # Create project allowing only gpt-4 and claude-opus
        project = Application(name="test-proj", allowed_models=["gpt-4", "claude-opus"])

        result = llm_service._filter_models_by_project(all_models, project)

        assert len(result) == 2
        assert any(m.base_name == "gpt-4" for m in result)
        assert any(m.base_name == "claude-opus" for m in result)
        assert not any(m.base_name == "gemini-pro" for m in result)

    def test_filter_models_by_project_no_narrowing_when_project_none(self, llm_service):
        """When project is None, all models returned (no narrowing applied)."""
        all_models = llm_service.llm_config.llm_models

        result = llm_service._filter_models_by_project(all_models, project=None)

        assert len(result) == 3

    def test_filter_models_by_project_no_narrowing_when_allowed_models_empty(self, llm_service):
        """When project.allowed_models is None or empty, behavior differs: None = all models, empty = no models."""
        from codemie.core.models import Application

        all_models = llm_service.llm_config.llm_models

        # None = no restriction, return all models
        project_none = Application(name="test-proj-none", allowed_models=None)
        result_none = llm_service._filter_models_by_project(all_models, project_none)
        assert len(result_none) == 3

        # Empty list = explicit deny all, return no models
        project_empty = Application(name="test-proj-empty", allowed_models=[])
        result_empty = llm_service._filter_models_by_project(all_models, project_empty)
        assert len(result_empty) == 0

        # Verify semantics are distinct
        assert len(result_none) != len(result_empty)
        assert all(m.is_premium is None for m in result_none)


class TestGetModelDetailsRefusal:
    """Tests for get_model_details refusal behavior."""

    @pytest.fixture
    def llm_service(self):
        llm_config = LLMConfig(
            yaml_file=Path('tests/service/llm_test_config.yaml'),
            llm_models=[
                LLMModel(
                    label='GPT-4',
                    base_name='gpt-4',
                    deployment_name='gpt-4-deployment',
                    enabled=True,
                    default_for_categories=[ModelCategory.GLOBAL],
                ),
                LLMModel(
                    label='Claude Opus',
                    base_name='claude-opus',
                    deployment_name='claude-opus-deployment',
                    enabled=True,
                    default_for_categories=[],
                ),
            ],
            embeddings_models=[],
        )
        return LLMService(llm_config)

    def test_get_model_details_model_not_found(self, llm_service):
        """When model is not found, raise ModelNotAllowedException."""
        from codemie.core.exceptions import ModelNotAllowedException

        with pytest.raises(ModelNotAllowedException) as exc_info:
            llm_service.get_model_details("nonexistent-model")

        assert "nonexistent-model" in exc_info.value.message

    def test_get_model_details_model_not_allowed_for_project(self, llm_service):
        """When model exists but is excluded by project, raise ModelNotWhitelistedException."""
        from codemie.core.exceptions import ModelNotWhitelistedException
        from codemie.core.models import Application

        project = Application(id="proj-1", name="test-proj", allowed_models=["claude-opus"])

        with (
            patch(
                "codemie.service.llm_service.llm_service.customer_config",
                is_feature_enabled=Mock(return_value=True),
            ),
            pytest.raises(ModelNotWhitelistedException) as exc_info,
        ):
            llm_service.get_model_details("gpt-4", project=project)

        assert "gpt-4" in exc_info.value.message

    def test_get_model_details_model_allowed(self, llm_service):
        """When model is found and allowed for project, return it."""
        from codemie.core.models import Application

        project = Application(id="proj-1", name="test-proj", allowed_models=["gpt-4"])
        result = llm_service.get_model_details("gpt-4", project=project)

        assert result.base_name == "gpt-4"

    def test_get_model_details_no_project_returns_model(self, llm_service):
        """When no project is passed, return model if it exists."""
        result = llm_service.get_model_details("gpt-4")
        assert result.base_name == "gpt-4"

    def test_get_model_details_strips_whitespace_from_model_name(self, llm_service):
        """model_name with leading/trailing whitespace is normalized."""
        result = llm_service.get_model_details("  gpt-4  ")
        assert result.base_name == "gpt-4"

    def test_get_model_details_project_id_none_raises_error(self, llm_service):
        """When project.id is None, raise ValueError before creating exception."""
        from codemie.core.models import Application

        project = Application(name="test-proj", allowed_models=["gpt-4"])
        project.id = None  # Simulate unsaved object

        with pytest.raises(ValueError, match="non-None id"):
            llm_service.get_model_details("gpt-4", project=project)

    def test_get_model_details_distinguishes_not_found_message(self, llm_service):
        """Error message for non-existent model differs from project-denied message."""
        from codemie.core.models import Application

        project = Application(name="test-proj", id="proj-1", allowed_models=["claude-3"])

        try:
            llm_service.get_model_details("nonexistent-model", project=project)
        except Exception as e:
            assert "does not exist in the system" in e.details

        try:
            llm_service.get_model_details("gpt-4", project=project)
        except Exception as e:
            assert "not in the allowed models list" in e.details

    def test_get_model_details_deny_all_message(self, llm_service):
        """Error message when project explicitly denies all models is distinct."""
        from codemie.core.models import Application

        project = Application(name="test-proj", id="proj-1", allowed_models=[])

        try:
            llm_service.get_model_details("gpt-4", project=project)
        except Exception as e:
            assert "explicitly denies all models" in e.details


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
        assert option.status == ModelStatus.ENABLED
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

    def test_reports_forbidden_for_web_so_clients_can_mark_cli_only_routers(self):
        cli_only = self._router_model(base_name="cli-router", forbidden_for_web=True)
        web = self._router_model(base_name="web-router")
        service = LLMService(self._config([cli_only, web]))

        options = {o.base_name: o for o in service.get_allowed_router_options(include_all=True)}

        assert options["cli-router"].forbidden_for_web is True
        assert options["web-router"].forbidden_for_web is False

    def test_excluded_by_project_allowed_models(self):
        """Bug repro: a litellm_auto router excluded from project.allowed_models must not
        appear in get_allowed_router_options — previously the method had no project param
        at all, so GET /v1/llm_models?project_id=... always returned every router regardless
        of the project's restriction. Project narrowing is itself gated behind the
        projectModelOverride feature flag (disabled by default in customer config — see
        TestModelNarrowingIntegration), so that flag is patched enabled here too."""
        from unittest.mock import MagicMock, patch

        from codemie.core.models import Application

        model = self._router_model(base_name="smart-router")
        service = LLMService(self._config([model]))
        project = Application(name="test-proj", id="proj-1", allowed_models=["some-other-model"])

        with patch(
            "codemie.service.llm_service.llm_service.customer_config",
            is_feature_enabled=MagicMock(return_value=True),
        ):
            assert service.get_allowed_router_options(project=project) == []
            # Sanity: without the project restriction, the router is still visible.
            assert len(service.get_allowed_router_options()) == 1

    def test_included_by_project_allowed_models(self):
        """A router explicitly named in project.allowed_models (by its own base_name, its
        only identifier — see LLMRouter's docstring) must still be returned."""
        from unittest.mock import MagicMock, patch

        from codemie.core.models import Application

        model = self._router_model(base_name="smart-router")
        service = LLMService(self._config([model]))
        project = Application(name="test-proj", id="proj-1", allowed_models=["smart-router"])

        with patch(
            "codemie.service.llm_service.llm_service.customer_config",
            is_feature_enabled=MagicMock(return_value=True),
        ):
            options = service.get_allowed_router_options(project=project)

        assert len(options) == 1
        assert options[0].base_name == "smart-router"

    def test_respects_disabled_status(self):
        """A litellm_auto router whose own persisted status is DISABLED must be excluded the
        same way a disabled switchyard router is — status-based narrowing must not depend on
        which router_type wraps the model."""
        model = self._router_model(status=ModelStatus.DISABLED)
        service = LLMService(self._config([model]))
        service.get_all_llm_model_info = Mock(return_value=[model])

        assert service.get_allowed_router_options(include_all=False) == []
        assert service.get_allowed_router_options(include_all=True)[0].status == ModelStatus.DISABLED

    def _options_for_user(self, override, *, project_allowed, routers=("smart-router",), user=True):
        """Router options for a project whose whitelist is `project_allowed`, with the user's override `override`."""
        from unittest.mock import MagicMock, patch

        from codemie.core.models import Application

        service = LLMService(self._config([self._router_model(base_name=name) for name in routers]))
        project = Application(name="test-proj", id="proj-1", allowed_models=project_allowed)
        caller = Mock(is_external_user=False) if user else None

        with (
            patch(
                "codemie.service.llm_service.llm_service.customer_config",
                is_feature_enabled=MagicMock(return_value=True),
            ),
            patch.object(service, "get_user_model_override", return_value=override) as lookup,
        ):
            options = service.get_allowed_router_options(project=project, user=caller)
        return [o.base_name for o in options], lookup, caller, project

    def test_user_override_offers_a_router_the_project_whitelist_excludes(self):
        names, _, _, _ = self._options_for_user(
            Mock(allowed_models=["smart-router"]), project_allowed=["some-other-model"]
        )

        assert names == ["smart-router"]

    def test_user_override_hides_a_router_the_project_whitelist_allows(self):
        names, _, _, _ = self._options_for_user(Mock(allowed_models=["gpt-4o"]), project_allowed=["smart-router"])

        assert names == []

    def test_user_override_keeps_only_the_routers_it_lists(self):
        names, _, _, _ = self._options_for_user(
            Mock(allowed_models=["router-b"]),
            project_allowed=["router-a", "router-b"],
            routers=("router-a", "router-b"),
        )

        assert names == ["router-b"]

    def test_override_without_routers_does_not_fall_back_to_the_project_set(self):
        """Unlike the chat models, an override that lists only chat models means the user has no router."""
        names, _, _, _ = self._options_for_user(Mock(allowed_models=["gpt-4o"]), project_allowed=None)

        assert names == []

    def test_project_whitelist_applies_when_the_user_has_no_override(self):
        names, lookup, caller, project = self._options_for_user(None, project_allowed=["smart-router"])

        assert names == ["smart-router"]
        lookup.assert_called_once_with(caller, project)

    def test_project_whitelist_still_excludes_when_the_user_has_no_override(self):
        names, _, _, _ = self._options_for_user(None, project_allowed=["some-other-model"])

        assert names == []

    def test_override_is_not_looked_up_without_a_user(self):
        names, lookup, _, _ = self._options_for_user(
            Mock(allowed_models=["gpt-4o"]), project_allowed=["smart-router"], user=False
        )

        assert names == ["smart-router"]
        lookup.assert_not_called()

    def test_user_override_applies_even_when_the_project_override_feature_is_off(self):
        """The user feature has its own flag: with the project one off the project whitelist is ignored,
        but the user's override still decides."""
        from unittest.mock import MagicMock, patch

        from codemie.core.models import Application

        service = LLMService(self._config([self._router_model(base_name="smart-router")]))
        project = Application(name="test-proj", id="proj-1", allowed_models=["smart-router"])

        with (
            patch(
                "codemie.service.llm_service.llm_service.customer_config",
                is_feature_enabled=MagicMock(return_value=False),
            ),
            patch.object(service, "get_user_model_override", return_value=Mock(allowed_models=["gpt-4o"])),
        ):
            options = service.get_allowed_router_options(project=project, user=Mock(is_external_user=False))

        assert options == []

    def test_plain_model_untouched(self):
        """A regular model (no litellm_router) must still appear only in the chat-model
        catalog, never in router options — this test guards against an overbroad filter."""
        plain = LLMModel(base_name="gpt-4o", deployment_name="gpt-4o", enabled=True)
        service = LLMService(self._config([plain]))

        assert [m.base_name for m in service.get_allowed_chat_models(user=Mock(is_external_user=False))] == ["gpt-4o"]
        assert service.get_allowed_router_options() == []

    def test_default_flag_propagated_when_router_marked_global_default(self):
        """Mocks a LiteLLM config where a litellm_auto router (e.g. gpt-router-standard) is
        marked default_for_categories=[GLOBAL] in model_info, mirroring the live LiteLLM proxy
        config. The router option surfaced to the REST boundary must carry default=True so
        clients relying on /v1/llm_models or /v1/default_models can see it."""
        router = self._router_model(
            base_name="gpt-router-standard",
            default_for_categories=[ModelCategory.GLOBAL],
        )
        service = LLMService(self._config([router]))

        options = service.get_allowed_router_options()

        assert len(options) == 1
        assert options[0].default is True
        assert options[0].default_for_categories == [ModelCategory.GLOBAL]

    def test_router_is_catalog_fallback_when_no_default_configured(self):
        """The only selectable router becomes the catalog fallback."""
        router = self._router_model(base_name="gpt-router-standard")
        service = LLMService(self._config([router]))

        options = service.get_allowed_router_options()

        assert len(options) == 1
        assert options[0].default is True
        assert options[0].default_for_categories == [ModelCategory.GLOBAL]

    def test_default_llm_model_resolves_to_default_router(self):
        """When a litellm_auto router is marked default_for_categories=[GLOBAL] in a mocked
        LiteLLM config (matching the real gpt-router-standard setup), LLMService.default_llm_model
        must resolve to that router's base_name rather than falling back to a concrete model,
        even if a concrete model exists but isn't marked default."""
        router = self._router_model(
            base_name="gpt-router-standard",
            default_for_categories=[ModelCategory.GLOBAL],
        )
        concrete = LLMModel(base_name="gpt-4o", deployment_name="gpt-4o", enabled=True)
        service = LLMService(self._config([router, concrete]))

        assert service.default_llm_model == "gpt-router-standard"

    def test_default_llm_model_falls_back_to_concrete_model_when_no_default_router(self):
        """When no router is marked default, LLMService.default_llm_model must fall back to
        the concrete model marked default_for_categories=[GLOBAL], preserving pre-fix behavior."""
        router = self._router_model(base_name="gpt-router-standard")
        concrete = LLMModel(
            base_name="gpt-4o",
            deployment_name="gpt-4o",
            enabled=True,
            default_for_categories=[ModelCategory.GLOBAL],
        )
        service = LLMService(self._config([router, concrete]))

        assert service.default_llm_model == "gpt-4o"

    def test_default_llm_model_ignores_disabled_default_router(self):
        """A disabled router marked default_for_categories=[GLOBAL] must not be selected as the
        default; default_llm_model must fall back to the enabled concrete default."""
        router = self._router_model(
            base_name="gpt-router-standard",
            enabled=False,
            default_for_categories=[ModelCategory.GLOBAL],
        )
        concrete = LLMModel(
            base_name="gpt-4o",
            deployment_name="gpt-4o",
            enabled=True,
            default_for_categories=[ModelCategory.GLOBAL],
        )
        service = LLMService(self._config([router, concrete]))

        assert service.default_llm_model == "gpt-4o"

    def _router_and_concrete_both_global_default(self) -> LLMService:
        """Concrete model listed first so a plain first-match lookup would pick it over the router."""
        concrete = LLMModel(
            base_name="gpt-4o",
            deployment_name="gpt-4o",
            enabled=True,
            default_for_categories=[ModelCategory.GLOBAL],
        )
        router = self._router_model(
            base_name="gpt-router-standard",
            default_for_categories=[ModelCategory.GLOBAL],
        )
        return LLMService(self._config([concrete, router]))

    def test_global_default_lookups_agree_when_router_and_concrete_both_default(self):
        """default_llm_model, get_default_model_for_category and get_default_models_by_category
        must all resolve the GLOBAL default to the same (router) model."""
        service = self._router_and_concrete_both_global_default()

        assert service.default_llm_model == "gpt-router-standard"
        assert service.get_default_model_for_category(ModelCategory.CHAT).base_name == "gpt-router-standard"
        assert service.get_default_models_by_category()[ModelCategory.CHAT.value].base_name == "gpt-router-standard"


class TestInitializeDefaultLiteLLMModelsMergesPersistedStatus:
    """`initialize_default_litellm_models` merges persisted `LLMModelStatus` rows into the catalog."""

    def _service(self):
        return LLMService(
            llm_config=LLMConfig(
                yaml_file=Path('tests/service/llm_test_config.yaml'),
                llm_models=[],
                embeddings_models=[],
            )
        )

    def _user_models(self):
        from codemie.configs.llm_config import LiteLLMModels

        return LiteLLMModels(
            chat_models=[
                LLMModel(base_name="m1", deployment_name="m1", enabled=True),
                LLMModel(base_name="m2", deployment_name="m2", enabled=True),
            ],
            embedding_models=[
                LLMModel(base_name="e1", deployment_name="e1", enabled=True),
            ],
        )

    @patch("codemie.service.llm_service.llm_service.LLMModelStatusRepository")
    @patch("codemie.service.llm_service.llm_service.get_session")
    def test_persisted_status_is_applied_to_matching_chat_model(self, mock_get_session, mock_repo):
        mock_session = Mock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = False
        row_m1 = Mock(base_name="m1", status="deprecated")
        mock_repo.get_all.return_value = [row_m1]

        service = self._service()
        service.initialize_default_litellm_models(self._user_models())

        by_name = {m.base_name: m for m in service.default_litellm_models}
        assert by_name["m1"].status == ModelStatus.DEPRECATED

    @patch("codemie.service.llm_service.llm_service.LLMModelStatusRepository")
    @patch("codemie.service.llm_service.llm_service.get_session")
    def test_model_without_persisted_row_defaults_to_enabled(self, mock_get_session, mock_repo):
        mock_session = Mock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = False
        row_m1 = Mock(base_name="m1", status="deprecated")
        mock_repo.get_all.return_value = [row_m1]

        service = self._service()
        service.initialize_default_litellm_models(self._user_models())

        by_name = {m.base_name: m for m in service.default_litellm_models}
        assert by_name["m2"].status == ModelStatus.ENABLED

    @patch("codemie.service.llm_service.llm_service.LLMModelStatusRepository")
    @patch("codemie.service.llm_service.llm_service.get_session")
    def test_db_failure_reuses_last_known_status_instead_of_failing_open(self, mock_get_session, mock_repo):
        """A transient DB outage must not silently re-enable an admin-deprecated model."""
        mock_get_session.return_value.__enter__.return_value = Mock()
        mock_get_session.return_value.__exit__.return_value = False
        mock_repo.get_all.return_value = [Mock(base_name="m1", status="deprecated")]

        service = self._service()
        # First pass succeeds and warms the override cache.
        service.initialize_default_litellm_models(self._user_models())
        assert {m.base_name: m for m in service.default_litellm_models}["m1"].status == ModelStatus.DEPRECATED

        # Second pass: the DB is unreachable. The cached override must still apply.
        mock_get_session.side_effect = RuntimeError("database unreachable")
        service.initialize_default_litellm_models(self._user_models())

        by_name = {m.base_name: m for m in service.default_litellm_models}
        assert by_name["m1"].status == ModelStatus.DEPRECATED
        assert by_name["m2"].status == ModelStatus.ENABLED

    @patch("codemie.service.llm_service.llm_service.LLMModelStatusRepository")
    @patch("codemie.service.llm_service.llm_service.get_session")
    def test_db_failure_with_cold_cache_defaults_to_enabled(self, mock_get_session, mock_repo):
        """With no cache ever populated there is nothing to fall back to, so models stay ENABLED."""
        mock_get_session.side_effect = RuntimeError("database unreachable")

        service = self._service()
        service.initialize_default_litellm_models(self._user_models())

        for model in service.default_litellm_models:
            assert model.status == ModelStatus.ENABLED

    @patch("codemie.service.llm_service.llm_service.LLMModelStatusRepository")
    @patch("codemie.service.llm_service.llm_service.get_session")
    def test_embedding_models_also_get_persisted_status(self, mock_get_session, mock_repo):
        mock_session = Mock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = False
        row_e1 = Mock(base_name="e1", status="deprecated")
        mock_repo.get_all.return_value = [row_e1]

        service = self._service()
        service.initialize_default_litellm_models(self._user_models())

        by_name = {m.base_name: m for m in service.default_litellm_embeddings}
        assert by_name["e1"].status == ModelStatus.DEPRECATED

    @patch("codemie.service.llm_service.llm_service.config.LLM_PROXY_ENABLED", True)
    @patch("codemie.service.llm_service.llm_service.LLMModelStatusRepository")
    @patch("codemie.service.llm_service.llm_service.get_session")
    def test_cached_litellm_status_refreshes_after_admin_write(self, mock_get_session, mock_repo):
        mock_get_session.return_value.__enter__.return_value = Mock()
        mock_repo.get_all.side_effect = [[], [Mock(base_name="m1", status="disabled")]]

        service = self._service()
        service.initialize_default_litellm_models(self._user_models())
        service.get_model_status_overrides(force_refresh=True)

        assert service.get_all_llm_model_info()[0].status == ModelStatus.DISABLED


class TestPersistedSettingsCache:
    @pytest.fixture
    def cache_context(self):
        module = "codemie.service.llm_service.llm_service"
        with (
            patch(f"{module}.get_session") as session,
            patch(f"{module}.LLMModelStatusRepository") as statuses,
            patch(f"{module}.AdminGlobalDefaultRepository") as defaults,
            patch(f"{module}.monotonic", return_value=100.0) as clock,
            patch(f"{module}.logger") as log,
            patch(f"{module}.config.LLM_PROXY_ENABLED", False),
        ):
            statuses.get_all.return_value = []
            defaults.get.return_value = None
            service = LLMService(
                LLMConfig(
                    yaml_file=Path('tests/service/llm_test_config.yaml'),
                    llm_models=[LLMModel(base_name="m1", deployment_name="m1", enabled=True, default=True)],
                    embeddings_models=[],
                )
            )
            yield service, session, statuses, defaults, clock, log

    def test_hot_paths_cache_empty_results_and_refresh_at_expiry(self, cache_context):
        from codemie.core.utils import calculate_tokens

        service, session, statuses, defaults, clock, _ = cache_context
        with (
            patch("codemie.core.utils.llm_service", service),
            patch("codemie.core.utils.get_encoding") as encoding,
        ):
            encoding.return_value.encode.return_value = [1]
            for now in (100.0, 105.0):
                clock.return_value = now
                for _ in range(100):
                    assert service.default_llm_model == "m1"
                    assert service.get_model_details("m1").base_name == "m1"
                    assert calculate_tokens("text") == 1
            assert session.call_count == 4  # Two cold reads, then two TTL refreshes.
            assert statuses.get_all.call_count == defaults.get.call_count == 2

    @pytest.mark.parametrize("warm_cache", [False, True])
    def test_outage_throttles_retries_and_warnings_until_recovery(self, cache_context, warm_cache):
        service, session, statuses, defaults, clock, log = cache_context
        if warm_cache:
            statuses.get_all.return_value = [Mock(base_name="m1", status="disabled")]
            defaults.get.return_value = Mock(base_name="m1")
            service.get_all_llm_model_info()
            service.get_admin_global_default_choice()
        session.reset_mock()
        session.side_effect = RuntimeError("database unavailable")
        for now in (105.0, 110.0):
            clock.return_value = now
            for _ in range(100):
                model = service.get_all_llm_model_info()[0]
                choice = service.get_admin_global_default_choice()
                assert model.status == (ModelStatus.DISABLED if warm_cache else ModelStatus.ENABLED)
                assert (choice is not None) == warm_cache
        assert session.call_count == 4
        assert log.warning.call_count == 2  # One warning per cache across both retry intervals.

        session.side_effect = None
        statuses.get_all.return_value = []
        defaults.get.return_value = None
        clock.return_value = 115.0
        assert service.get_all_llm_model_info()[0].status == ModelStatus.ENABLED
        assert service.get_admin_global_default_choice() is None
        session.side_effect = RuntimeError("new outage")
        clock.return_value = 120.0
        service.get_all_llm_model_info()
        service.get_admin_global_default_choice()
        assert log.warning.call_count == 4

    def test_default_writes_replace_warm_cache_without_rereading(self, cache_context):
        service, session, _, defaults, _, _ = cache_context
        assert service.get_admin_global_default_choice() is None
        defaults.replace.return_value = Mock(base_name="m1")
        service.set_admin_global_default("m1", "admin", "Admin")
        reads_after_set = session.call_count
        assert service.get_admin_global_default_choice() is defaults.replace.return_value
        assert session.call_count == reads_after_set
        service.clear_admin_global_default()
        reads_after_clear = session.call_count
        assert service.get_admin_global_default_choice() is None
        assert session.call_count == reads_after_clear
        defaults.get.assert_called_once()


class TestYamlFallbackMergesPersistedStatus:
    """get_all_llm_model_info/get_all_embedding_model_info must merge persisted status even on the
    YAML-fallback path (LLM_PROXY_ENABLED=False, the documented default) - not only inside
    initialize_default_litellm_models, which only runs on the LiteLLM-proxy path.
    """

    def _service(self):
        return LLMService(
            llm_config=LLMConfig(
                yaml_file=Path('tests/service/llm_test_config.yaml'),
                llm_models=[
                    LLMModel(base_name="m1", deployment_name="m1", enabled=True),
                    LLMModel(base_name="m2", deployment_name="m2", enabled=True),
                ],
                embeddings_models=[
                    LLMModel(base_name="e1", deployment_name="e1", enabled=True),
                ],
            )
        )

    @patch("codemie.service.llm_service.llm_service.LLMModelStatusRepository")
    @patch("codemie.service.llm_service.llm_service.get_session")
    def test_persisted_status_merged_into_yaml_chat_models(self, mock_get_session, mock_repo):
        from codemie.configs.config import config

        mock_get_session.return_value.__enter__.return_value = Mock()
        mock_get_session.return_value.__exit__.return_value = False
        mock_repo.get_all.return_value = [Mock(base_name="m1", status="deprecated")]

        service = self._service()
        with patch.object(config, "LLM_PROXY_ENABLED", False):
            models = service.get_all_llm_model_info()

        by_name = {m.base_name: m for m in models}
        assert by_name["m1"].status == ModelStatus.DEPRECATED
        assert by_name["m2"].status == ModelStatus.ENABLED

    @patch("codemie.service.llm_service.llm_service.LLMModelStatusRepository")
    @patch("codemie.service.llm_service.llm_service.get_session")
    def test_persisted_status_merged_into_yaml_embedding_models(self, mock_get_session, mock_repo):
        from codemie.configs.config import config

        mock_get_session.return_value.__enter__.return_value = Mock()
        mock_get_session.return_value.__exit__.return_value = False
        mock_repo.get_all.return_value = [Mock(base_name="e1", status="disabled")]

        service = self._service()
        with patch.object(config, "LLM_PROXY_ENABLED", False):
            models = service.get_all_embedding_model_info()

        assert models[0].status == ModelStatus.DISABLED


class TestExternalUserBranchMergesPersistedStatus:
    """get_allowed_models' external-user LiteLLM-integration branch must also merge persisted
    status - not just the default catalog merged once at startup - otherwise admin
    disable/deprecate has no effect for external users with their own LiteLLM integration.
    """

    def _service(self):
        return LLMService(
            llm_config=LLMConfig(
                yaml_file=Path('tests/service/llm_test_config.yaml'),
                llm_models=[],
                embeddings_models=[],
            )
        )

    @patch("codemie.service.llm_service.llm_service.LLMModelStatusRepository")
    @patch("codemie.service.llm_service.llm_service.get_session")
    def test_persisted_status_merged_for_external_user_models(self, mock_get_session, mock_repo):
        from codemie.configs.config import config
        from codemie.configs.llm_config import LiteLLMModels

        mock_get_session.return_value.__enter__.return_value = Mock()
        mock_get_session.return_value.__exit__.return_value = False
        mock_repo.get_all.return_value = [Mock(base_name="user-model-1", status="deprecated")]

        user_models = LiteLLMModels(
            chat_models=[LLMModel(base_name="user-model-1", deployment_name="user-model-1", enabled=True)],
            embedding_models=[],
        )

        service = self._service()
        user = Mock()
        user.is_external_user = True
        user.id = "user-1"
        user.project_names = []

        with (
            patch.object(config, "LLM_PROXY_ENABLED", True),
            patch(
                "codemie.enterprise.litellm.get_user_allowed_models",
                return_value=user_models,
            ),
        ):
            result = service.get_allowed_models(user)

        assert result.chat_models[0].status == ModelStatus.DEPRECATED


class TestMergePersistedStatusHandlesInvalidValue:
    """A persisted status value outside the ModelStatus enum (e.g. a corrupt/manual DB row) must
    not crash the caller - it should fall back to ENABLED with a logged warning.
    """

    def _service(self):
        return LLMService(
            llm_config=LLMConfig(
                yaml_file=Path('tests/service/llm_test_config.yaml'),
                llm_models=[],
                embeddings_models=[],
            )
        )

    @patch("codemie.service.llm_service.llm_service.LLMModelStatusRepository")
    @patch("codemie.service.llm_service.llm_service.get_session")
    def test_invalid_persisted_status_falls_back_to_enabled(self, mock_get_session, mock_repo):
        mock_get_session.return_value.__enter__.return_value = Mock()
        mock_get_session.return_value.__exit__.return_value = False
        mock_repo.get_all.return_value = [Mock(base_name="m1", status="not-a-real-status")]

        service = self._service()
        model = LLMModel(base_name="m1", deployment_name="m1", enabled=True)

        service._merge_persisted_status([model])

        assert model.status == ModelStatus.ENABLED

    @patch("codemie.service.llm_service.llm_service.get_session")
    def test_db_failure_degrades_to_unmerged_catalog(self, mock_get_session):
        mock_get_session.side_effect = Exception("connection refused")

        service = self._service()
        model = LLMModel(base_name="m1", deployment_name="m1", enabled=True)

        service._merge_persisted_status([model])

        assert model.status == ModelStatus.ENABLED


class TestAssertModelSelectable:
    """`assert_model_selectable` guards write-path model selection against deprecated/unavailable models."""

    def _service_with_model(self, **model_kwargs):
        llm_config = LLMConfig(
            yaml_file=Path('tests/service/llm_test_config.yaml'),
            llm_models=[],
            embeddings_models=[],
        )
        service = LLMService(llm_config)
        model = LLMModel(
            label='Model M1',
            base_name='m1',
            deployment_name='m1-deployment',
            **model_kwargs,
        )
        service.get_all_llm_model_info = Mock(return_value=[model])
        return service

    @pytest.mark.parametrize("model_name", ["m1", "m1-deployment", " m1 ", " m1-deployment "])
    def test_raises_when_status_deprecated(self, model_name):
        service = self._service_with_model(enabled=True, status=ModelStatus.DEPRECATED)

        with pytest.raises(ModelNotSelectableError):
            service.assert_model_selectable(model_name)

    @pytest.mark.parametrize("model_name", ["m1", "m1-deployment"])
    def test_raises_when_status_disabled(self, model_name):
        service = self._service_with_model(enabled=True, status=ModelStatus.DISABLED)

        with pytest.raises(ModelNotSelectableError):
            service.assert_model_selectable(model_name)

    @pytest.mark.parametrize("model_name", ["m1", "m1-deployment"])
    def test_raises_when_enabled_false_defense_in_depth(self, model_name):
        service = self._service_with_model(enabled=False, status=ModelStatus.ENABLED)

        with pytest.raises(ModelNotSelectableError):
            service.assert_model_selectable(model_name)

    @pytest.mark.parametrize("model_name", ["m1", "m1-deployment", " m1-deployment "])
    def test_does_not_raise_when_enabled_and_status_enabled(self, model_name):
        service = self._service_with_model(enabled=True, status=ModelStatus.ENABLED)

        service.assert_model_selectable(model_name)

    def test_does_not_raise_for_unknown_base_name(self):
        service = self._service_with_model(enabled=True, status=ModelStatus.ENABLED)

        service.assert_model_selectable("unknown-model")

    @pytest.mark.parametrize("model_status", [ModelStatus.DEPRECATED, ModelStatus.DISABLED])
    @pytest.mark.parametrize(
        "previous_name,requested_name",
        [("m1", "m1"), ("m1-deployment", "m1-deployment"), ("m1", "m1-deployment"), ("m1-deployment", " m1 ")],
    )
    def test_existing_selection_can_be_retained(self, model_status, previous_name, requested_name):
        service = self._service_with_model(enabled=True, status=model_status)

        service.assert_model_selectable(requested_name, previous_model_name=previous_name)

    @pytest.mark.parametrize("model_name", ["m1", "m1-deployment"])
    def test_changing_to_deprecated_model_is_rejected(self, model_name):
        service = self._service_with_model(enabled=True, status=ModelStatus.DEPRECATED)

        with pytest.raises(ModelNotSelectableError):
            service.assert_model_selectable(model_name, previous_model_name="another-model")


class TestAdminGlobalDefault:
    @staticmethod
    def _model(name, *, categories=(), status=ModelStatus.ENABLED, enabled=True, router=False):
        return LLMModel(
            base_name=name,
            deployment_name=name,
            enabled=enabled,
            status=status,
            default_for_categories=list(categories),
            litellm_router=LiteLLMRouterConfig() if router else None,
        )

    @staticmethod
    def _service(models):
        service = LLMService(
            LLMConfig(yaml_file=Path('tests/service/llm_test_config.yaml'), llm_models=[], embeddings_models=[])
        )
        service.get_all_llm_model_info = Mock(return_value=models)
        return service

    @patch("codemie.service.llm_service.llm_service.AdminGlobalDefaultRepository")
    @patch("codemie.service.llm_service.llm_service.get_session")
    def test_override_and_clear_flow(self, session, repository):
        models = [self._model("configured", categories=[ModelCategory.GLOBAL]), self._model("admin")]
        service = self._service(models)
        repository.get.return_value = Mock(base_name="admin")

        assert service.default_llm_model == "admin"
        assert service.get_default_model_for_category(ModelCategory.CHAT).base_name == "admin"
        assert service.get_default_models_by_category()[ModelCategory.GLOBAL.value].base_name == "admin"
        assert service.resolve_global_default(models).source == "admin"

        repository.get.return_value = None
        service.clear_admin_global_default()
        assert service.default_llm_model == "configured"
        repository.clear.assert_called_once()

    @patch("codemie.service.llm_service.llm_service.AdminGlobalDefaultRepository")
    @patch("codemie.service.llm_service.llm_service.get_session")
    def test_set_validates_known_selectable_name(self, session, repository):
        service = self._service([self._model("usable"), self._model("old", status=ModelStatus.DEPRECATED)])

        with pytest.raises(ValueError, match="Unknown"):
            service.set_admin_global_default("missing", "id", "name")
        with pytest.raises(ModelNotSelectableError):
            service.set_admin_global_default("old", "id", "name")
        repository.replace.assert_not_called()

        service.set_admin_global_default("usable", "id", "name")
        repository.replace.assert_called_once_with(session.return_value.__enter__.return_value, "usable", "id", "name")

    @pytest.mark.parametrize(
        "status,enabled",
        [
            (ModelStatus.DISABLED, True),
            (ModelStatus.DEPRECATED, True),
            (ModelStatus.ENABLED, False),
        ],
    )
    def test_unselectable_admin_choice_falls_back_and_restores(self, status, enabled):
        configured = self._model("configured", categories=[ModelCategory.GLOBAL])
        chosen = self._model("chosen", status=status, enabled=enabled)
        service = self._service([configured, chosen])
        service.get_admin_global_default_choice = Mock(return_value=Mock(base_name="chosen"))

        assert service.resolve_global_default([configured, chosen]).model is configured
        chosen.status, chosen.enabled = ModelStatus.ENABLED, True
        assert service.resolve_global_default([configured, chosen]).model is chosen

    def test_configured_router_first_then_safe_catalog_fallback(self):
        concrete = self._model("concrete", categories=[ModelCategory.GLOBAL])
        router = self._model("router", categories=[ModelCategory.GLOBAL], router=True)
        service = self._service([concrete, router])
        service.get_admin_global_default_choice = Mock(return_value=None)
        assert service.resolve_global_default([concrete, router]).model is router

        concrete.default_for_categories = []
        router.default_for_categories = []
        concrete.default = False
        router.default = False
        assert service.resolve_global_default([concrete, router]).model is concrete

    def test_specific_category_still_precedes_global_override(self):
        configured = self._model("configured", categories=[ModelCategory.GLOBAL])
        code = self._model("code", categories=[ModelCategory.CODE])
        admin = self._model("admin")
        service = self._service([configured, code, admin])
        service.get_admin_global_default_choice = Mock(return_value=Mock(base_name="admin"))
        assert service.get_default_model_for_category(ModelCategory.CODE) is code
        assert service.get_default_models_by_category()[ModelCategory.CODE.value] is code

    @patch("codemie.service.llm_service.llm_service.AdminGlobalDefaultRepository")
    @patch("codemie.service.llm_service.llm_service.get_session")
    def test_transient_db_outage_reuses_last_read_choice(self, session, repository):
        configured = self._model("configured", categories=[ModelCategory.GLOBAL])
        admin = self._model("admin")
        service = self._service([configured, admin])
        repository.get.return_value = Mock(base_name="admin")
        assert service.default_llm_model == "admin"
        repository.get.side_effect = RuntimeError("database unavailable")
        assert service.default_llm_model == "admin"


class TestCatalogGlobalDefaultProjection:
    @staticmethod
    def _model(name, categories=(), **kwargs):
        return LLMModel(
            base_name=name, deployment_name=name, enabled=True, default_for_categories=list(categories), **kwargs
        )

    @staticmethod
    def _service(models, selected=None):
        service = LLMService(
            LLMConfig(yaml_file=Path('tests/service/llm_test_config.yaml'), llm_models=models, embeddings_models=[])
        )
        service.get_admin_global_default_choice = Mock(return_value=Mock(base_name=selected) if selected else None)
        return service

    def test_plain_catalog_recomputes_one_global_without_mutating_source(self):
        configured = self._model('configured', [ModelCategory.GLOBAL, ModelCategory.CODE])
        chosen = self._model('chosen', [ModelCategory.DOCUMENTATION])
        service = self._service([configured, chosen], selected='chosen')
        user = Mock(is_external_user=False)

        result = service.get_allowed_chat_models(user)
        assert [(m.base_name, m.default) for m in result] == [('configured', False), ('chosen', True)]
        assert result[0].default_for_categories == [ModelCategory.CODE]
        assert result[1].default_for_categories == [ModelCategory.DOCUMENTATION, ModelCategory.GLOBAL]
        assert configured.default is True
        assert configured.default_for_categories == [ModelCategory.GLOBAL, ModelCategory.CODE]
        assert chosen.default is False

        service.get_admin_global_default_choice.return_value = None
        result = service.get_allowed_chat_models(user)
        assert [(m.base_name, m.default) for m in result] == [('configured', True), ('chosen', False)]

    def test_restricted_catalog_uses_accessible_fallback(self):
        hidden = self._model('hidden', [ModelCategory.GLOBAL])
        accessible = self._model('accessible')
        service = self._service([hidden, accessible], selected='hidden')
        service.get_allowed_models = Mock(return_value=Mock(chat_models=[accessible], embedding_models=[]))

        result = service.get_allowed_chat_models(Mock(is_external_user=True))
        assert [m.base_name for m in result if m.default] == ['accessible']
        assert result[0].default_for_categories == [ModelCategory.GLOBAL]

    def test_auto_router_and_plain_model_share_one_choice(self):
        plain = self._model('plain', [ModelCategory.GLOBAL, ModelCategory.CODE])
        router = self._model('router', litellm_router=LiteLLMRouterConfig())
        service = self._service([plain, router], selected='router')

        assert not any(m.default for m in service.get_allowed_chat_models(Mock(is_external_user=False)))
        option = service.get_allowed_router_options()[0]
        assert option.base_name == 'router'
        assert option.default is True
        assert option.default_for_categories == [ModelCategory.GLOBAL]
        service.get_admin_global_default_choice.return_value = None
        assert service.get_allowed_router_options()[0].default is False

    def test_restricted_router_catalog_does_not_expose_global_router(self):
        router = self._model('router', [ModelCategory.GLOBAL], litellm_router=LiteLLMRouterConfig())
        plain = self._model('plain')
        service = self._service([router, plain], selected='router')
        service.get_allowed_models = Mock(return_value=Mock(chat_models=[plain], embedding_models=[]))

        assert service.get_allowed_router_options(user=Mock(is_external_user=True)) == []
        assert [m.base_name for m in service.get_allowed_chat_models(Mock(is_external_user=True)) if m.default] == [
            'plain'
        ]

    @patch("codemie.service.llm_service.llm_service.AdminGlobalDefaultRepository")
    @patch("codemie.service.llm_service.llm_service.get_session")
    def test_set_clear_and_reload_remove_stale_catalog_flags(self, session, repository):
        configured = self._model('configured', [ModelCategory.GLOBAL])
        selected = self._model('selected')
        models = [configured, selected]
        service = self._service(models)
        service.get_admin_global_default_choice = LLMService.get_admin_global_default_choice.__get__(service)
        user = Mock(is_external_user=False)
        repository.get.return_value = None

        assert [m.base_name for m in service.get_allowed_chat_models(user) if m.default] == ['configured']
        repository.replace.return_value = Mock(base_name='selected')
        service.set_admin_global_default('selected', 'admin-id', 'Admin')
        repository.get.return_value = repository.replace.return_value
        assert [m.base_name for m in service.get_allowed_chat_models(user) if m.default] == ['selected']

        repository.get.return_value = None
        service.clear_admin_global_default()
        assert [m.base_name for m in service.get_allowed_chat_models(user) if m.default] == ['configured']
        reloaded = self._service(models)
        reloaded.get_admin_global_default_choice = LLMService.get_admin_global_default_choice.__get__(reloaded)
        assert [m.base_name for m in reloaded.get_allowed_chat_models(user) if m.default] == ['configured']
        assert selected.default is False
        assert selected.default_for_categories == []
