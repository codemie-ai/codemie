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

"""Tests for LLMService integration with LiteLLM enterprise layer.

These tests verify that LLMService correctly integrates with the LiteLLM
enterprise package for model management and gracefully falls back to YAML
configuration when LiteLLM is not available.
"""

from unittest.mock import patch

import pytest

from codemie.configs.llm_config import LLMConfig, LLMModel, LLMProvider, LiteLLMModels, ModelSwitchyard, RoutingMode
from codemie.service.llm_service.llm_service import LLMService


@pytest.fixture
def llm_service():
    """Create a basic LLMService instance for testing."""
    from codemie.configs.llm_config import llm_config

    return LLMService(llm_config)


@pytest.fixture
def mock_litellm_models():
    """Create mock LiteLLM models for testing."""
    chat_models = [
        LLMModel(
            base_name="litellm-gpt-4",
            deployment_name="litellm-gpt-4",
            enabled=True,
            label="LiteLLM GPT-4",
            provider=LLMProvider.AZURE_OPENAI,
            default=True,  # Mark as default for deployment name tests
        ),
        LLMModel(
            base_name="litellm-claude",
            deployment_name="litellm-claude",
            enabled=True,
            label="LiteLLM Claude",
            provider=LLMProvider.ANTHROPIC,
        ),
    ]
    embedding_models = [
        LLMModel(
            base_name="litellm-ada-002",
            deployment_name="litellm-ada-002",
            enabled=True,
            label="LiteLLM Ada",
            provider=LLMProvider.AZURE_OPENAI,
            default=True,  # Mark as default for deployment name tests
        )
    ]
    return LiteLLMModels(chat_models=chat_models, embedding_models=embedding_models)


class TestLiteLLMModelInitialization:
    """Test LiteLLM model initialization in LLMService."""

    def test_initialize_default_litellm_models(self, llm_service, mock_litellm_models):
        """Test initializing LiteLLM models in service."""
        # Initially empty
        assert llm_service.default_litellm_models == []
        assert llm_service.default_litellm_embeddings == []

        # Initialize with LiteLLM models
        llm_service.initialize_default_litellm_models(mock_litellm_models)

        # Verify models are stored
        assert len(llm_service.default_litellm_models) == 2
        assert llm_service.default_litellm_models[0].base_name == "litellm-gpt-4"
        assert llm_service.default_litellm_models[1].base_name == "litellm-claude"

        # Verify embeddings are stored
        assert len(llm_service.default_litellm_embeddings) == 1
        assert llm_service.default_litellm_embeddings[0].base_name == "litellm-ada-002"

    def test_initialize_default_litellm_models_empty(self, llm_service):
        """Test initializing LiteLLM with empty model list."""
        empty_models = LiteLLMModels(chat_models=[], embedding_models=[])

        llm_service.initialize_default_litellm_models(empty_models)

        assert llm_service.default_litellm_models == []
        assert llm_service.default_litellm_embeddings == []


class TestLiteLLMModelFallback:
    """Test fallback behavior between LiteLLM and YAML config."""

    def test_get_all_llm_model_info_uses_litellm_when_enabled(self, llm_service, mock_litellm_models):
        """Test that get_all_llm_model_info returns LiteLLM models when initialized."""
        from codemie.configs.config import config

        # Initialize LiteLLM models
        llm_service.initialize_default_litellm_models(mock_litellm_models)

        with patch.object(config, "LLM_PROXY_ENABLED", True):
            models = llm_service.get_all_llm_model_info()

            # Should return LiteLLM models
            assert len(models) == 2
            assert models[0].base_name == "litellm-gpt-4"
            assert models[1].base_name == "litellm-claude"

    def test_get_all_llm_model_info_uses_yaml_when_disabled(self, llm_service, mock_litellm_models):
        """Test that get_all_llm_model_info returns YAML models when LiteLLM disabled."""
        from codemie.configs.config import config

        # Initialize LiteLLM models (but proxy is disabled)
        llm_service.initialize_default_litellm_models(mock_litellm_models)

        with patch.object(config, "LLM_PROXY_ENABLED", False):
            models = llm_service.get_all_llm_model_info()

            # Should return YAML config models (not LiteLLM)
            # We can't assert exact models since they come from actual config,
            # but we verify it's not the LiteLLM models
            assert all(model.base_name != "litellm-gpt-4" for model in models)

    def test_get_all_llm_model_info_uses_yaml_when_not_initialized(self, llm_service):
        """Test that get_all_llm_model_info returns YAML models when LiteLLM not initialized."""
        from codemie.configs.config import config

        # Don't initialize LiteLLM models
        with patch.object(config, "LLM_PROXY_ENABLED", True):
            models = llm_service.get_all_llm_model_info()

            # Should return YAML config models (LiteLLM not initialized)
            assert all(model.base_name != "litellm-gpt-4" for model in models)

    def test_get_all_embedding_model_info_uses_litellm_when_enabled(self, llm_service, mock_litellm_models):
        """Test that get_all_embedding_model_info returns LiteLLM embeddings when initialized."""
        from codemie.configs.config import config

        # Initialize LiteLLM models
        llm_service.initialize_default_litellm_models(mock_litellm_models)

        with patch.object(config, "LLM_PROXY_ENABLED", True):
            embeddings = llm_service.get_all_embedding_model_info()

            # Should return LiteLLM embeddings
            assert len(embeddings) == 1
            assert embeddings[0].base_name == "litellm-ada-002"

    def test_get_all_embedding_model_info_uses_yaml_when_disabled(self, llm_service, mock_litellm_models):
        """Test that get_all_embedding_model_info returns YAML embeddings when LiteLLM disabled."""
        from codemie.configs.config import config

        # Initialize LiteLLM models (but proxy is disabled)
        llm_service.initialize_default_litellm_models(mock_litellm_models)

        with patch.object(config, "LLM_PROXY_ENABLED", False):
            embeddings = llm_service.get_all_embedding_model_info()

            # Should return YAML config embeddings (not LiteLLM)
            assert all(emb.base_name != "litellm-ada-002" for emb in embeddings)


class TestGetLlmRoutersSourceConsistency:
    """get_llm_routers must use the same either/or model source as get_all_llm_model_info —
    no merge between the static YAML and the live LiteLLM/DIAL catalog."""

    @staticmethod
    def _config(tmp_path, body: str) -> LLMConfig:
        yaml_file = tmp_path / "c.yaml"
        yaml_file.write_text(body)
        return LLMConfig(yaml_file=yaml_file)

    _YAML_WITH_SWITCHYARD = """
llm_models:
  - base_name: 'cap'
    deployment_name: 'cap'
    enabled: true
    switchyard:
      - base_name: 'cap-switchyard-eff-signal'
        efficient: 'eff'
        mode: signal
  - base_name: 'eff'
    deployment_name: 'eff'
    enabled: true
embeddings_models: []
"""

    def test_uses_yaml_switchyard_when_litellm_not_initialized(self, tmp_path, monkeypatch):
        from codemie.configs.config import config as app_config

        monkeypatch.setattr(app_config, "SWITCHYARD_ENABLED", True)
        service = LLMService(self._config(tmp_path, self._YAML_WITH_SWITCHYARD))

        routers = service.get_llm_routers()

        assert {r.base_name for r in routers} == {"cap-switchyard-eff-signal"}

    def test_ignores_yaml_switchyard_when_litellm_enabled_with_no_live_declaration(self, tmp_path, monkeypatch):
        """When the proxy is enabled and initialized, routers come only from the live catalog —
        a switchyard block declared in the static YAML for the same base_name must not leak in."""
        from codemie.configs.config import config as app_config

        monkeypatch.setattr(app_config, "SWITCHYARD_ENABLED", True)
        monkeypatch.setattr(app_config, "LLM_PROXY_ENABLED", True)
        service = LLMService(self._config(tmp_path, self._YAML_WITH_SWITCHYARD))
        service.initialize_default_litellm_models(
            LiteLLMModels(
                chat_models=[
                    LLMModel(base_name="cap", deployment_name="cap", enabled=True),
                    LLMModel(base_name="eff", deployment_name="eff", enabled=True),
                ]
            )
        )

        routers = service.get_llm_routers()

        assert routers == []

    def test_uses_live_catalog_switchyard_when_litellm_enabled(self, tmp_path, monkeypatch):
        from codemie.configs.config import config as app_config

        monkeypatch.setattr(app_config, "SWITCHYARD_ENABLED", True)
        monkeypatch.setattr(app_config, "LLM_PROXY_ENABLED", True)
        yaml_no_switchyard = """
llm_models:
  - base_name: 'cap'
    deployment_name: 'cap'
    enabled: true
embeddings_models: []
"""
        service = LLMService(self._config(tmp_path, yaml_no_switchyard))
        service.initialize_default_litellm_models(
            LiteLLMModels(
                chat_models=[
                    LLMModel(
                        base_name="cap",
                        deployment_name="cap",
                        enabled=True,
                        switchyard=[
                            ModelSwitchyard(
                                base_name="cap-switchyard-eff-signal", efficient="eff", mode=RoutingMode.SIGNAL
                            )
                        ],
                    ),
                    LLMModel(base_name="eff", deployment_name="eff", enabled=True),
                ]
            )
        )

        routers = service.get_llm_routers()

        assert {r.base_name for r in routers} == {"cap-switchyard-eff-signal"}


class TestLiteLLMDeploymentNames:
    """Test deployment name resolution with LiteLLM models."""

    def test_get_llm_deployment_name_from_litellm(self, llm_service, mock_litellm_models):
        """Test getting deployment name from LiteLLM models."""
        from codemie.configs.config import config

        # Initialize LiteLLM models
        llm_service.initialize_default_litellm_models(mock_litellm_models)

        with patch.object(config, "LLM_PROXY_ENABLED", True):
            deployment_name = llm_service.get_llm_deployment_name("litellm-gpt-4")

            assert deployment_name == "litellm-gpt-4"

    def test_get_embedding_deployment_name_from_litellm(self, llm_service, mock_litellm_models):
        """Test getting embedding deployment name from LiteLLM models."""
        from codemie.configs.config import config

        # Initialize LiteLLM models
        llm_service.initialize_default_litellm_models(mock_litellm_models)

        with patch.object(config, "LLM_PROXY_ENABLED", True):
            deployment_name = llm_service.get_embedding_deployment_name("litellm-ada-002")

            assert deployment_name == "litellm-ada-002"
