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
from codemie.configs.llm_config import LlmRouterOption, ModelCategory, ModelStatus

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
        mock.get_default_models_by_category.return_value = {}
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
            'supports_image_generation': False,
            'forbidden_for_web': False,
            'switchyard': [],
            'status': 'enabled',
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
            'supports_image_generation': False,
            'forbidden_for_web': False,
            'switchyard': [],
            'status': 'enabled',
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
    models = [
        LLMModel(
            base_name="default-global",
            deployment_name="default-global",
            enabled=True,
            default=True,
            default_for_categories=[ModelCategory.GLOBAL],
        ),
        LLMModel(
            base_name="default-code",
            deployment_name="default-code",
            enabled=True,
            default_for_categories=[ModelCategory.CODE],
        ),
    ]
    mock_llm_service.get_default_models_by_category.return_value = {"global": models[0], "code": models[1]}
    from codemie.rest_api.security.authentication import authenticate

    app.dependency_overrides.pop(authenticate, None)
    route = next(route for route in router.routes if route.path == "/v1/default_models")
    assert authenticate not in [dependency.call for dependency in route.dependant.dependencies]

    response = client.get("/v1/default_models")
    assert response.status_code == 200
    assert "global" in response.json()
    assert "code" in response.json()
    assert response.json()["global"]["base_name"] == "default-global"
    assert response.json()["code"]["base_name"] == "default-code"
    mock_llm_service.get_default_models_by_category.assert_called_once_with()
    mock_llm_service.get_allowed_chat_models.assert_not_called()


def test_global_default_agrees_across_public_endpoints(mock_llm_service, user):
    old = LLMModel(
        base_name="configured", deployment_name="configured", enabled=True, default_for_categories=[ModelCategory.CODE]
    )
    selected = LLMModel(
        base_name="admin-choice",
        deployment_name="admin-choice",
        enabled=True,
        default=True,
        default_for_categories=[ModelCategory.GLOBAL],
    )
    mock_llm_service.get_allowed_chat_models.return_value = [old, selected]
    mock_llm_service.get_allowed_router_options.return_value = []
    mock_llm_service.get_default_models_by_category.return_value = {"global": selected, "code": old}

    catalog = client.get("/v1/llm_models").json()
    defaults = client.get("/v1/default_models").json()
    single = client.get("/v1/default_models/global").json()

    assert [item["base_name"] for item in catalog if item["default"]] == ["admin-choice"]
    assert defaults["global"]["base_name"] == single["base_name"] == "admin-choice"
    assert defaults["code"]["base_name"] == "configured"
    assert client.get("/v1/default_models/code").json()["base_name"] == "configured"
    assert mock_llm_service.get_allowed_router_options.call_count == 3
    assert all(
        call.kwargs == {"include_all": False, "project": None, "user": user}
        for call in mock_llm_service.get_allowed_router_options.call_args_list
    )


def test_restricted_user_scoped_endpoints_do_not_expose_inaccessible_admin_choice(mock_llm_service, user):
    scoped = LLMModel(
        base_name="allowed",
        deployment_name="allowed",
        enabled=True,
        default=True,
        default_for_categories=[ModelCategory.GLOBAL],
    )
    mock_llm_service.get_allowed_chat_models.return_value = [scoped]
    mock_llm_service.get_allowed_router_options.return_value = []

    assert [item["base_name"] for item in client.get("/v1/llm_models").json()] == ["allowed"]
    assert client.get("/v1/default_models/global").json()["base_name"] == "allowed"
    assert all(call.kwargs["user"] is user for call in mock_llm_service.get_allowed_router_options.call_args_list)


def test_auto_router_can_be_global_default(mock_llm_service):
    mock_llm_service.get_default_models_by_category.return_value = {
        "global": LLMModel(base_name="auto", deployment_name="auto", enabled=True, default=True)
    }
    mock_llm_service.get_allowed_chat_models.return_value = [
        LLMModel(base_name="plain", deployment_name="plain", enabled=True)
    ]
    mock_llm_service.get_allowed_router_options.return_value = [
        LlmRouterOption(
            base_name="auto", router_type="litellm_auto", default=True, default_for_categories=[ModelCategory.GLOBAL]
        )
    ]

    assert [item["base_name"] for item in client.get("/v1/llm_models").json() if item["default"]] == ["auto"]
    assert client.get("/v1/default_models").json()["global"]["base_name"] == "auto"
    assert client.get("/v1/default_models").json()["global"]["deployment_name"] == "auto"
    assert "router_type" not in client.get("/v1/default_models").json()["global"]
    assert client.get("/v1/default_models/global").json()["base_name"] == "auto"


def test_public_defaults_skip_deprecated_code_model_and_router(mock_llm_service):
    mock_llm_service.get_allowed_chat_models.return_value = [
        LLMModel(base_name="fallback", deployment_name="fallback", enabled=True, default=True),
        LLMModel(
            base_name="old-code",
            deployment_name="old-code",
            enabled=True,
            status=ModelStatus.DEPRECATED,
            default_for_categories=[ModelCategory.CODE],
        ),
    ]
    mock_llm_service.get_allowed_router_options.return_value = [
        LlmRouterOption(
            base_name="old-router",
            router_type="litellm_auto",
            status=ModelStatus.DEPRECATED,
            default_for_categories=[ModelCategory.CODE],
        )
    ]
    mock_llm_service.get_default_models_by_category.return_value = {
        "code": mock_llm_service.get_allowed_chat_models.return_value[0]
    }

    assert client.get("/v1/default_models").json()["code"]["base_name"] == "fallback"
    assert client.get("/v1/default_models/code").json()["base_name"] == "fallback"


def test_public_defaults_skip_disabled_include_all_and_return_none_if_no_selectable(mock_llm_service):
    mock_llm_service.get_allowed_chat_models.return_value = [
        LLMModel(
            base_name="disabled",
            deployment_name="disabled",
            enabled=True,
            status=ModelStatus.DISABLED,
            default=True,
            default_for_categories=[ModelCategory.GLOBAL],
        )
    ]
    mock_llm_service.get_allowed_router_options.return_value = []

    assert client.get("/v1/default_models?include_all=true").json() == {}
    assert client.get("/v1/default_models/global?include_all=true").status_code == 404


def test_no_models_preserves_empty_defaults_and_404(mock_llm_service):
    mock_llm_service.get_allowed_chat_models.return_value = []
    mock_llm_service.get_allowed_router_options.return_value = []

    assert client.get("/v1/llm_models").json() == []
    assert client.get("/v1/default_models").json() == {}
    assert client.get("/v1/default_models/global").status_code == 404


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
            'supports_image_generation': False,
            'forbidden_for_web': False,
            'switchyard': [],
            'status': 'enabled',
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
            'supports_image_generation': False,
            'forbidden_for_web': False,
            'switchyard': [],
            'status': 'enabled',
        },
    ]


def test_get_image_generation_models(mock_llm_service):
    mock_llm_service.get_allowed_image_generation_models.return_value = [
        LLMModel(
            base_name="image-model",
            deployment_name="image-model",
            enabled=True,
            label="Image Model",
            supports_image_generation=True,
        ),
    ]

    response = client.get("/v1/llm_models/image_generation")
    assert response.status_code == 200
    assert response.json() == [
        {
            'base_name': 'image-model',
            'default_for_categories': [],
            'default': False,
            'deployment_name': 'image-model',
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
            'label': 'Image Model',
            'supports_image_generation': True,
            'forbidden_for_web': False,
            'switchyard': [],
            'status': 'enabled',
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

    def test_image_generation_models_respects_include_all(self, mock_llm_service):
        """Image generation endpoint should also respect include_all parameter."""
        mock_llm_service.get_allowed_image_generation_models.return_value = []

        response = client.get("/v1/llm_models/image_generation?include_all=true")

        assert response.status_code == 200
        call_args = mock_llm_service.get_allowed_image_generation_models.call_args
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

    def test_all_category_defaults_keep_public_global_contract(self, mock_llm_service):
        mock_llm_service.get_allowed_chat_models.return_value = []
        mock_llm_service.get_allowed_router_options.return_value = []

        response = client.get("/v1/default_models?include_all=true")

        assert response.status_code == 200
        assert response.json() == {}
        mock_llm_service.get_default_models_by_category.assert_called_once_with()
        mock_llm_service.get_allowed_chat_models.assert_not_called()
        mock_llm_service.get_allowed_router_options.assert_not_called()


def test_llm_models_is_premium_serialization(mock_llm_service):
    """is_premium appears in JSON only when set (response_model_exclude_none)."""
    mock_llm_service.get_allowed_chat_models.return_value = [
        LLMModel(
            base_name="claude-opus-4-1",
            deployment_name="claude-opus-4-1",
            enabled=True,
            label="Claude Opus 4.1",
            is_premium=True,
        ),
        LLMModel(base_name="gpt-4o", deployment_name="gpt-4o", enabled=True, label="GPT-4o"),
    ]

    response = client.get("/v1/llm_models")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["is_premium"] is True
    assert "is_premium" not in payload[1]


def test_llm_models_status_serialization(mock_llm_service):
    """status is serialized on GET /v1/llm_models: persisted value and default (enabled)."""
    from codemie.configs.llm_config import ModelStatus

    mock_llm_service.get_allowed_chat_models.return_value = [
        LLMModel(
            base_name="model-a",
            deployment_name="model-a",
            enabled=True,
            label="Model A",
            status=ModelStatus.DEPRECATED,
        ),
        LLMModel(base_name="model-b", deployment_name="model-b", enabled=True, label="Model B"),
    ]

    response = client.get("/v1/llm_models")

    assert response.status_code == 200
    payload = response.json()
    # Explicitly persisted status survives serialization.
    assert payload[0]["status"] == "deprecated"
    # No persisted status falls back to the field default (enabled).
    assert payload[1]["status"] == "enabled"
