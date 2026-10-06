# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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
from unittest.mock import patch, MagicMock
from codemie.service.llm_service.llm_service import LLMService, LLMModel, LLMConfig
from codemie.configs.llm_config import ModelCategory
from codemie.core.exceptions import ModelNotWhitelistedException
from codemie.core.models import Application


class TestModelNarrowingIntegration:
    """Integration tests for project-level model narrowing across all paths."""

    @pytest.fixture(autouse=True)
    def enable_project_model_override(self):
        """These tests exercise project narrowing, which is gated behind the
        projectModelOverride feature flag (disabled by default in customer config)."""
        with patch(
            "codemie.service.llm_service.llm_service.customer_config",
            is_feature_enabled=MagicMock(return_value=True),
        ):
            yield

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
                LLMModel(
                    label='Gemini Pro',
                    base_name='gemini-pro',
                    deployment_name='gemini-pro-deployment',
                    enabled=True,
                    default_for_categories=[],
                ),
            ],
            embeddings_models=[],
        )
        return LLMService(llm_config)

    def test_cross_project_isolation(self, llm_service):
        """Changes to one project's allowed_models don't affect another project."""
        proj1 = Application(id="proj-1", name="proj-1", allowed_models=["gpt-4", "claude-opus"])
        proj2 = Application(id="proj-2", name="proj-2", allowed_models=["gpt-4"])

        # proj2 should only have gpt-4
        models_proj2 = llm_service.get_allowed_chat_models(None, project=proj2)
        assert all(m.base_name == "gpt-4" for m in models_proj2)

        # proj1 should have both gpt-4 and claude-opus
        models_proj1 = llm_service.get_allowed_chat_models(None, project=proj1)
        assert len(models_proj1) == 2
        assert any(m.base_name == "gpt-4" for m in models_proj1)
        assert any(m.base_name == "claude-opus" for m in models_proj1)

    def test_consistency_across_list_and_get_details(self, llm_service):
        """If a model is in get_allowed_chat_models, get_model_details should work."""
        project = Application(id="test-proj", name="test-proj", allowed_models=["gpt-4", "claude-opus"])

        # List available models
        available = llm_service.get_allowed_chat_models(None, project=project)
        available_names = [m.base_name for m in available]

        # Each should be retrievable without refusal
        for name in available_names:
            model = llm_service.get_model_details(name, project=project)
            assert model.base_name == name

        # A model not in the list should be refused
        with pytest.raises(ModelNotWhitelistedException):
            llm_service.get_model_details("gemini-pro", project=project)

    def test_backward_compatibility_no_project(self, llm_service):
        """When no project is passed, all models are visible (backward compatibility)."""
        result = llm_service.get_allowed_chat_models(None, project=None)
        # Should include all models
        assert len(result) == 3
        # Should work with get_model_details
        for model in result:
            details = llm_service.get_model_details(model.base_name, project=None)
            assert details.base_name == model.base_name

    def test_refusal_message_includes_model_and_project(self, llm_service):
        """Refusal exceptions include both model name and project ID in the message."""
        project = Application(id="proj-123", name="proj-123", allowed_models=["claude-opus"])

        with pytest.raises(ModelNotWhitelistedException) as exc_info:
            llm_service.get_model_details("gpt-4", project=project)

        exc = exc_info.value
        assert "gpt-4" in exc.message
        assert "proj-123" in exc.message
        assert exc.code == 400

    def test_default_model_selection_with_allowed_default(self, llm_service):
        """When platform default is allowed by project, select it."""
        project = Application(id="proj-1", name="proj-1", allowed_models=["gpt-4", "claude-opus"])

        available = llm_service.get_allowed_chat_models(None, project=project)
        default = llm_service._select_default_model(available, project)

        # Should return the default model (gpt-4 is configured as default in fixture)
        assert default is not None
        assert default.default is True

    def test_default_model_selection_fallback_when_default_excluded(self, llm_service):
        """When platform default is excluded, select first allowed model."""
        project = Application(id="proj-1", name="proj-1", allowed_models=["claude-opus"])

        available = llm_service.get_allowed_chat_models(None, project=project)
        default = llm_service._select_default_model(available, project)

        # Should return first allowed model (not the platform default)
        assert default is not None
        assert default.base_name == "claude-opus"

    def test_default_model_selection_empty_list_returns_none(self, llm_service):
        """When no models are allowed, return None."""
        project = Application(id="proj-1", name="proj-1", allowed_models=[])

        available = llm_service.get_allowed_chat_models(None, project=project)
        default = llm_service._select_default_model(available, project)

        assert default is None
