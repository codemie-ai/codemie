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

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from codemie.configs.llm_config import LLMModel, ModelStatus
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.tools import toolkit_settings_service as settings


@pytest.fixture
def image_models():
    models = {
        name: LLMModel(
            base_name=f"{name}-image",
            deployment_name=f"dep-{name}-image",
            enabled=True,
            supports_image_generation=True,
        )
        for name in ("request", "assistant", "global")
    }
    assistant = SimpleNamespace(image_generation_model="assistant-image")
    request = SimpleNamespace(image_generation_model="request-image", conversation_id="conversation-1")
    config = SimpleNamespace(
        IMAGE_GENERATION_MODEL="global-image",
        LLM_PROXY_ENABLED=True,
        LITE_LLM_URL="https://proxy.example.test",
        LITE_LLM_APP_KEY="test-key",
        OPENAI_API_VERSION="test-version",
        LLM_PROXY_TIMEOUT=20,
        AZURE_OPENAI_URL="https://azure.example.test",
        AZURE_OPENAI_API_KEY="test-key",
    )
    with (
        patch.object(settings, "config", config),
        patch.object(llm_service, "get_all_llm_model_info", return_value=list(models.values())) as catalog,
    ):
        yield SimpleNamespace(models=models, assistant=assistant, request=request, config=config, catalog=catalog)


@pytest.mark.parametrize(
    "disabled,expected",
    [
        ((), "request-image"),
        (("request",), "assistant-image"),
        (("request", "assistant"), "global-image"),
        (("request", "assistant", "global"), None),
    ],
)
@pytest.mark.parametrize("request_name", ["request-image", "dep-request-image", " dep-request-image "])
def test_disabled_candidates_are_skipped_in_priority_order(image_models, disabled, expected, request_name):
    image_models.request.image_generation_model = request_name
    for name in disabled:
        image_models.models[name].status = ModelStatus.DISABLED

    result = settings.ToolkitSettingService._resolve_image_generation_model(
        image_models.assistant, image_models.request
    )

    assert result == (request_name if not disabled else expected)
    image_models.catalog.assert_called_once()


@pytest.mark.parametrize("source", ["request", "assistant", "global"])
@pytest.mark.parametrize("model_status", [ModelStatus.ENABLED, ModelStatus.DEPRECATED])
def test_existing_enabled_and_deprecated_selections_are_retained(image_models, source, model_status):
    image_models.models[source].status = model_status
    if source != "request":
        image_models.request.image_generation_model = None
    if source == "global":
        image_models.assistant.image_generation_model = None

    result = settings.ToolkitSettingService._resolve_image_generation_model(
        image_models.assistant, image_models.request
    )

    assert result == image_models.models[source].base_name


def test_disabled_assistant_alias_is_skipped(image_models):
    image_models.request.image_generation_model = None
    image_models.assistant.image_generation_model = " dep-assistant-image "
    image_models.models["assistant"].status = ModelStatus.DISABLED

    assert (
        settings.ToolkitSettingService._resolve_image_generation_model(image_models.assistant, image_models.request)
        == "global-image"
    )


def test_disabled_global_alias_is_not_used_as_last_resort(image_models):
    image_models.request.image_generation_model = None
    image_models.assistant.image_generation_model = None
    image_models.config.IMAGE_GENERATION_MODEL = "dep-global-image"
    image_models.models["global"].status = ModelStatus.DISABLED

    assert (
        settings.ToolkitSettingService._resolve_image_generation_model(image_models.assistant, image_models.request)
        is None
    )


def test_repeated_disabled_model_does_not_stop_fallback(image_models):
    image_models.models["request"].status = ModelStatus.DISABLED
    image_models.assistant.image_generation_model = "dep-request-image"

    assert (
        settings.ToolkitSettingService._resolve_image_generation_model(image_models.assistant, image_models.request)
        == "global-image"
    )


def test_legacy_model_outside_catalog_keeps_working(image_models):
    image_models.config.IMAGE_GENERATION_MODEL = "legacy-image-deployment"

    assert settings.ToolkitSettingService._resolve_image_generation_model() == "legacy-image-deployment"


def test_no_candidate_does_not_load_catalog(image_models):
    image_models.config.IMAGE_GENERATION_MODEL = " "

    assert settings.ToolkitSettingService._resolve_image_generation_model() is None
    image_models.catalog.assert_not_called()


GENERATOR_PATHS = [
    ("_build_image_generator", "proxy"),
    ("_build_image_generator", "azure"),
    ("_build_image_generator", "chat"),
    ("_build_workspace_image_generator", "proxy"),
    ("_build_workspace_image_generator", "azure"),
]


@pytest.fixture
def generator_factories():
    with (
        patch("codemie_tools.data_management.file_system.image_generator.LiteLLMImageGenerator") as image,
        patch("codemie_tools.data_management.file_system.image_generator.ChatModelImageGenerator") as chat,
        patch.object(settings, "get_llm_by_credentials", return_value=Mock()) as llm,
    ):
        yield image, chat, llm


def configure_provider(config, provider):
    config.LLM_PROXY_ENABLED = provider == "proxy"
    if provider == "chat":
        config.AZURE_OPENAI_URL = None
        config.AZURE_OPENAI_API_KEY = None


@pytest.mark.parametrize("builder,provider", GENERATOR_PATHS)
def test_every_generator_receives_image_fallback(image_models, generator_factories, builder, provider):
    image, chat, llm = generator_factories
    configure_provider(image_models.config, provider)
    image_models.models["request"].status = ModelStatus.DISABLED

    result = getattr(settings.ToolkitSettingService, builder)(image_models.assistant, image_models.request)

    if provider == "chat":
        assert result is chat.return_value
        assert chat.call_args.kwargs["model_id"] == "assistant-image"
        llm.assert_called_once_with(llm_model="assistant-image", request_id=None)
        image.assert_not_called()
    else:
        assert result is image.return_value
        assert image.call_args.args[0].model_id == "assistant-image"
        llm.assert_not_called()
        chat.assert_not_called()


@pytest.mark.parametrize("builder,provider", GENERATOR_PATHS)
def test_no_generator_or_provider_call_when_every_candidate_disabled(
    image_models,
    generator_factories,
    builder,
    provider,
):
    configure_provider(image_models.config, provider)
    for model in image_models.models.values():
        model.status = ModelStatus.DISABLED

    assert getattr(settings.ToolkitSettingService, builder)(image_models.assistant, image_models.request) is None
    for factory in generator_factories:
        factory.assert_not_called()


def test_workspace_image_tool_not_created_without_usable_candidate(image_models, generator_factories):
    for model in image_models.models.values():
        model.status = ModelStatus.DISABLED

    result = settings.ToolkitSettingService.get_workspace_image_generation_tool(
        image_models.assistant, Mock(), image_models.request
    )

    assert result is None
    for factory in generator_factories:
        factory.assert_not_called()


def test_disabling_model_affects_next_generator_construction(image_models, generator_factories):
    image, _, _ = generator_factories
    settings.ToolkitSettingService._build_workspace_image_generator(image_models.assistant, image_models.request)
    assert image.call_args.args[0].model_id == "request-image"

    image_models.models["request"].status = ModelStatus.DISABLED
    settings.ToolkitSettingService._build_workspace_image_generator(image_models.assistant, image_models.request)

    assert image.call_args.args[0].model_id == "assistant-image"
