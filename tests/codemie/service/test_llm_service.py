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

import pytest
from pathlib import Path
from codemie.service.llm_service.llm_service import LLMService, LLMModel, LLMConfig
from codemie.configs.llm_config import ModelCategory


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
