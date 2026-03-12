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
from fastapi.testclient import TestClient
from unittest.mock import patch
import sys
from pathlib import Path

from codemie.rest_api.security.user import User

# Add src directory to sys.path
sys.path.append(str(Path(__file__).resolve().parents[3] / 'src'))

from codemie.rest_api.routers.llm_models import router
from codemie.service.llm_service.llm_service import LLMModel
from codemie.rest_api.main import app

# Include the router in the FastAPI app for testing
app.include_router(router)

client = TestClient(app)


@pytest.fixture
def user():
    return User(id="123", username="testuser", name="Test User")


@pytest.fixture(autouse=True)
def override_dependency(user):
    from codemie.rest_api.routers import llm_models as llm_models_router

    app.dependency_overrides[llm_models_router.authenticate] = lambda: user
    yield
    app.dependency_overrides = {}


@pytest.fixture
def mock_llm_service():
    with patch("codemie.rest_api.routers.llm_models.llm_service", autospec=True) as mock:
        yield mock


def test_get_llm_models(mock_llm_service):
    mock_llm_service.get_allowed_chat_models.return_value = [
        LLMModel(base_name="model-a", deployment_name="model-a", enabled=True, label="Model A"),
        LLMModel(base_name="model-b", deployment_name="model-b", enabled=True, label="Model B"),
    ]

    response = client.get("/v1/llm_models")
    assert response.status_code == 200
    assert response.json() == [
        {
            'base_name': 'model-a',
            'default_for_categories': [],
            'default': False,
            'deployment_name': 'model-a',
            'enabled': True,
            'features': {
                'max_tokens': True,
                'parallel_tool_calls': False,
                'streaming': True,
                'system_prompt': True,
                'temperature': True,
                'tools': True,
                'top_p': True,
            },
            'label': 'Model A',
            'forbidden_for_web': False,
        },
        {
            'base_name': 'model-b',
            'default_for_categories': [],
            'default': False,
            'deployment_name': 'model-b',
            'enabled': True,
            'features': {
                'max_tokens': True,
                'parallel_tool_calls': False,
                'streaming': True,
                'system_prompt': True,
                'temperature': True,
                'tools': True,
                'top_p': True,
            },
            'label': 'Model B',
            'forbidden_for_web': False,
        },
    ]


def test_get_llm_model_categories(mock_llm_service):
    with patch("codemie.rest_api.routers.llm_models.ModelCategory") as mock_model_category:
        mock_model_category.__iter__ = lambda x: iter([mock_model_category.GLOBAL, mock_model_category.CODE])
        mock_model_category.GLOBAL.value = "global"
        mock_model_category.CODE.value = "code"

        response = client.get("/v1/categories")
        assert response.status_code == 200
        assert response.json() == ["global", "code"]


def test_get_default_models(mock_llm_service):
    mock_llm_service.get_default_models_by_category.return_value = {
        "global": LLMModel(
            base_name="default-global", deployment_name="default-global", enabled=True, label="Default Global"
        ),
        "code": LLMModel(base_name="default-code", deployment_name="default-code", enabled=True, label="Default Code"),
    }

    response = client.get("/v1/default_models")
    assert response.status_code == 200
    assert "global" in response.json()
    assert "code" in response.json()
    assert response.json()["global"]["base_name"] == "default-global"
    assert response.json()["code"]["base_name"] == "default-code"


def test_get_default_model_for_category(mock_llm_service):
    """Test that get_default_model_for_category returns the correct default model for a category"""
    from codemie.configs.llm_config import ModelCategory

    # Mock the allowed models for the user - include a model with the "code" category as default
    mock_llm_service.get_allowed_chat_models.return_value = [
        LLMModel(
            base_name="general-model",
            deployment_name="general-model",
            enabled=True,
            label="General Model",
            default_for_categories=[],
        ),
        LLMModel(
            base_name="default-code",
            deployment_name="default-code",
            enabled=True,
            label="Default Code",
            default_for_categories=[ModelCategory.CODE],
        ),
    ]

    response = client.get("/v1/default_models/code")
    assert response.status_code == 200
    assert response.json()["base_name"] == "default-code"
    assert response.json()["label"] == "Default Code"


def test_get_default_model_for_category_fallback_to_first(mock_llm_service):
    """Test that get_default_model_for_category falls back to first model if no default for category"""
    # Mock the allowed models without any model having the requested category as default
    mock_llm_service.get_allowed_chat_models.return_value = [
        LLMModel(
            base_name="first-model",
            deployment_name="first-model",
            enabled=True,
            label="First Model",
            default_for_categories=[],
        ),
        LLMModel(
            base_name="second-model",
            deployment_name="second-model",
            enabled=True,
            label="Second Model",
            default_for_categories=[],
        ),
    ]

    response = client.get("/v1/default_models/code")
    assert response.status_code == 200
    # Should return the first model as fallback
    assert response.json()["base_name"] == "first-model"
    assert response.json()["label"] == "First Model"


def test_get_default_model_for_category_no_models(mock_llm_service):
    """Test that get_default_model_for_category returns 404 when user has no accessible models"""
    # Mock empty list of allowed models
    mock_llm_service.get_allowed_chat_models.return_value = []

    response = client.get("/v1/default_models/code")
    assert response.status_code == 404
    assert "No accessible models found" in response.json()["detail"]


def test_get_embeddings_models(mock_llm_service):
    mock_llm_service.get_allowed_embedding_models.return_value = [
        LLMModel(base_name="embedding-a", deployment_name="embedding-a", enabled=True, label="Embedding A"),
        LLMModel(base_name="embedding-b", deployment_name="embedding-b", enabled=True, label="Embedding B"),
    ]

    response = client.get("/v1/embeddings_models")
    assert response.status_code == 200
    assert response.json() == [
        {
            'base_name': 'embedding-a',
            'default_for_categories': [],
            'default': False,
            'deployment_name': 'embedding-a',
            'enabled': True,
            'features': {
                'max_tokens': True,
                'parallel_tool_calls': False,
                'streaming': True,
                'system_prompt': True,
                'temperature': True,
                'tools': True,
                'top_p': True,
            },
            'label': 'Embedding A',
            'forbidden_for_web': False,
        },
        {
            'base_name': 'embedding-b',
            'default_for_categories': [],
            'default': False,
            'deployment_name': 'embedding-b',
            'enabled': True,
            'features': {
                'max_tokens': True,
                'parallel_tool_calls': False,
                'streaming': True,
                'system_prompt': True,
                'temperature': True,
                'tools': True,
                'top_p': True,
            },
            'label': 'Embedding B',
            'forbidden_for_web': False,
        },
    ]


# Tests for include_all parameter
class TestIncludeAllParameter:
    """Tests for include_all query parameter in API endpoints"""

    def test_default_filters_forbidden_models(self, mock_llm_service):
        """Default behavior (include_all=False) should filter out models with forbidden_for_web=True"""
        mock_llm_service.get_allowed_chat_models.return_value = [
            LLMModel(base_name="visible-model", deployment_name="visible-model", enabled=True, forbidden_for_web=False),
        ]

        response = client.get("/v1/llm_models")

        assert response.status_code == 200
        # Verify service was called with include_all=False (default)
        mock_llm_service.get_allowed_chat_models.assert_called_once()
        call_args = mock_llm_service.get_allowed_chat_models.call_args
        assert call_args.kwargs['include_all'] is False

    def test_include_all_true_shows_all_models(self, mock_llm_service):
        """include_all=true should show all models including forbidden ones"""
        mock_llm_service.get_allowed_chat_models.return_value = [
            LLMModel(base_name="visible-model", deployment_name="visible-model", enabled=True, forbidden_for_web=False),
            LLMModel(base_name="hidden-model", deployment_name="hidden-model", enabled=True, forbidden_for_web=True),
        ]

        response = client.get("/v1/llm_models?include_all=true")

        assert response.status_code == 200
        # Verify service was called with include_all=True
        mock_llm_service.get_allowed_chat_models.assert_called_once()
        call_args = mock_llm_service.get_allowed_chat_models.call_args
        assert call_args.kwargs['include_all'] is True

    def test_include_all_false_filters_forbidden_models(self, mock_llm_service):
        """include_all=false should explicitly filter forbidden models"""
        mock_llm_service.get_allowed_chat_models.return_value = []

        response = client.get("/v1/llm_models?include_all=false")

        assert response.status_code == 200
        # Verify service was called with include_all=False
        call_args = mock_llm_service.get_allowed_chat_models.call_args
        assert call_args.kwargs['include_all'] is False

    def test_embeddings_models_respects_include_all(self, mock_llm_service):
        """Embeddings endpoint should also respect include_all parameter"""
        mock_llm_service.get_allowed_embedding_models.return_value = []

        # Test with include_all=true
        response = client.get("/v1/embeddings_models?include_all=true")

        assert response.status_code == 200
        call_args = mock_llm_service.get_allowed_embedding_models.call_args
        assert call_args.kwargs['include_all'] is True

    def test_default_model_respects_include_all(self, mock_llm_service):
        """Default model endpoint should respect include_all parameter"""
        from codemie.configs.llm_config import ModelCategory

        mock_llm_service.get_allowed_chat_models.return_value = [
            LLMModel(
                base_name="default-code",
                deployment_name="default-code",
                enabled=True,
                forbidden_for_web=False,
                default_for_categories=[ModelCategory.CODE],
            ),
        ]

        # Test with include_all=true
        response = client.get("/v1/default_models/code?include_all=true")

        assert response.status_code == 200
        call_args = mock_llm_service.get_allowed_chat_models.call_args
        assert call_args.kwargs['include_all'] is True
