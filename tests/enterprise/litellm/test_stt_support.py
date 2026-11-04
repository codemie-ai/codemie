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

"""Tests for STT support in the LiteLLM enterprise layer: catalog split, model mapping and the client factory."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.configs import config
from codemie.configs.llm_config import LLMModel, ModelType, SttConfig
from codemie.core.constants import LLMProxyMode
from codemie.enterprise.litellm.dependencies import get_available_models
from codemie.enterprise.litellm.stt_client import get_litellm_stt_client
from codemie.enterprise.litellm.models import map_litellm_to_llm_model


def _raw_model(name, mode, **model_info):
    return {"model_name": name, "model_info": {"mode": mode, "litellm_provider": "openai", **model_info}}


class TestModelTypeMode:
    def test_audio_transcription_is_a_known_mode(self):
        assert ModelType("audio_transcription") is ModelType.AUDIO_TRANSCRIPTION


class TestCatalogSplit:
    def test_audio_transcription_models_are_separated_from_chat_and_embedding_models(self):
        service = MagicMock()
        service.get_available_models.return_value = [
            _raw_model("gpt-x", "chat"),
            _raw_model("embed-x", "embedding"),
            _raw_model("custom-stt", "audio_transcription"),
        ]

        with patch("codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=service):
            models = get_available_models()

        assert [m.base_name for m in models.chat_models] == ["gpt-x"]
        assert [m.base_name for m in models.embedding_models] == ["embed-x"]
        assert [m.base_name for m in models.audio_transcription_models] == ["custom-stt"]

    def test_realtime_models_are_not_treated_as_audio_transcription_models(self):
        service = MagicMock()
        service.get_available_models.return_value = [_raw_model("custom-stt", "realtime")]

        with patch("codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=service):
            models = get_available_models()

        assert models.audio_transcription_models == []

    def test_realtime_models_are_not_listed_at_all(self):
        service = MagicMock()
        service.get_available_models.return_value = [
            _raw_model("gpt-x", "chat"),
            _raw_model("live-stt", "realtime"),
        ]

        with patch("codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=service):
            models = get_available_models()

        assert [m.base_name for m in models.chat_models] == ["gpt-x"]
        assert models.embedding_models == []
        assert models.audio_transcription_models == []

    def test_models_without_a_mode_stay_chat_models(self):
        service = MagicMock()
        service.get_available_models.return_value = [{"model_name": "gpt-x", "model_info": {}}]

        with patch("codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=service):
            models = get_available_models()

        assert [m.base_name for m in models.chat_models] == ["gpt-x"]
        assert models.audio_transcription_models == []


class TestUserCatalogWithAudioOnly:
    def _user_models(self, raw_models):
        from codemie.enterprise.litellm.models import get_user_allowed_models
        from codemie.rest_api.models.settings import LiteLLMCredentials

        service = MagicMock()
        service.get_available_models.return_value = raw_models
        credentials = LiteLLMCredentials(api_key="sk-test", url="http://localhost:4000")
        with (
            patch("codemie.enterprise.litellm.credentials.get_litellm_credentials_for_user", return_value=credentials),
            patch("codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=service),
        ):
            return get_user_allowed_models(user_id="user-1", user_applications=["app"])

    def test_catalog_with_only_audio_models_is_kept(self):
        models = self._user_models([_raw_model("custom-stt", "audio_transcription")])

        assert models is not None
        assert models.chat_models == []
        assert [m.base_name for m in models.audio_transcription_models] == ["custom-stt"]

    def test_realtime_models_are_not_listed_in_a_user_catalog(self):
        models = self._user_models([_raw_model("gpt-x", "chat"), _raw_model("live-stt", "realtime")])

        assert [m.base_name for m in models.chat_models] == ["gpt-x"]
        assert models.audio_transcription_models == []

    def test_catalog_without_chat_or_audio_models_is_dropped(self):
        assert self._user_models([_raw_model("embed-x", "embedding")]) is None


class TestSttConfigMapping:
    def test_stt_config_is_read_from_model_info(self):
        raw = _raw_model(
            "custom-stt",
            "audio_transcription",
            stt_config={
                "default_language": "de",
                "forward_request_headers": ["x-conversation-id"],
                "response_headers": ["x-transaction-id"],
                "response_body_fields": ["file_path"],
            },
        )

        model = map_litellm_to_llm_model(raw)

        assert model.stt_config.default_language == "de"
        assert model.stt_config.forward_request_headers == ["x-conversation-id"]
        assert model.stt_config.response_headers == ["x-transaction-id"]
        assert model.stt_config.response_body_fields == ["file_path"]

    def test_model_without_stt_config_has_none(self):
        assert map_litellm_to_llm_model(_raw_model("custom-stt", "audio_transcription")).stt_config is None

    def test_invalid_stt_config_is_ignored_and_does_not_break_the_model(self):
        raw = _raw_model("custom-stt", "audio_transcription", stt_config={"forward_request_headers": "not-a-list"})

        model = map_litellm_to_llm_model(raw)

        assert model.base_name == "custom-stt"
        assert model.stt_config is None


@pytest.fixture
def litellm_active(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROXY_MODE", LLMProxyMode.lite_llm)
    monkeypatch.setattr(config, "LITE_LLM_URL", "https://litellm.example")
    monkeypatch.setattr(config, "LITE_LLM_APP_KEY", "app-key")
    with patch("codemie.enterprise.litellm.dependencies.is_litellm_enabled", return_value=True):
        yield


def _model():
    return LLMModel(base_name="custom-stt", deployment_name="custom-stt", enabled=True)


class TestSttClientTimeout:
    def _client(self, model):
        with (
            patch("codemie.enterprise.litellm.stt_client._generate_litellm_headers", return_value={}),
            patch("codemie.enterprise.litellm.stt_client._configure_direct_runtime_overrides"),
        ):
            return get_litellm_stt_client(model, None, "user@example.com")

    def test_default_timeout_is_above_the_usual_server_limit(self, litellm_active):
        assert self._client(_model()).timeout == 300.0

    def test_model_can_set_its_own_timeout(self, litellm_active):
        model = LLMModel(
            base_name="custom-stt",
            deployment_name="custom-stt",
            enabled=True,
            stt_config=SttConfig(request_timeout_seconds=45),
        )

        assert self._client(model).timeout == 45

    def test_non_positive_timeout_makes_the_config_invalid_and_it_is_ignored(self):
        raw = _raw_model("custom-stt", "audio_transcription", stt_config={"request_timeout_seconds": 0})

        assert map_litellm_to_llm_model(raw).stt_config is None


class TestSttClientCredentials:
    def _context(self, api_key):
        from codemie.rest_api.models.settings import LiteLLMContext, LiteLLMCredentials

        creds = LiteLLMCredentials(api_key=api_key) if api_key else None
        return LiteLLMContext(credentials=creds, current_project="proj", personal_project="user@example.com")

    def test_the_user_key_is_used_and_no_customer_is_attributed_for_a_personal_key(self, litellm_active):
        with patch("codemie.enterprise.litellm.stt_client._generate_litellm_headers", return_value={}):
            client = get_litellm_stt_client(_model(), self._context("user-key"), "user@example.com", "u-1")

        assert client.api_key == "user-key"
        assert "x-litellm-customer-id" not in client.default_headers

    def test_the_shared_app_key_is_used_when_the_user_has_no_key(self, litellm_active):
        with (
            patch("codemie.enterprise.litellm.stt_client._generate_litellm_headers", return_value={}),
            patch("codemie.enterprise.litellm.stt_client._configure_direct_runtime_overrides"),
        ):
            client = get_litellm_stt_client(_model(), self._context(None), "user@example.com", "u-1")

        assert client.api_key == "app-key"


class TestSttClientFactory:
    def test_returns_none_when_litellm_is_not_enabled(self, monkeypatch):
        monkeypatch.setattr(config, "LLM_PROXY_MODE", LLMProxyMode.lite_llm)
        with patch("codemie.enterprise.litellm.dependencies.is_litellm_enabled", return_value=False):
            assert get_litellm_stt_client(_model(), None, "user@example.com") is None

    def test_returns_none_when_the_proxy_mode_is_not_litellm(self, monkeypatch):
        monkeypatch.setattr(config, "LLM_PROXY_MODE", LLMProxyMode.internal)
        with patch("codemie.enterprise.litellm.dependencies.is_litellm_enabled", return_value=True):
            assert get_litellm_stt_client(_model(), None, "user@example.com") is None

    def test_client_points_at_litellm_with_the_app_key(self, litellm_active):
        with (
            patch(
                "codemie.enterprise.litellm.stt_client._generate_litellm_headers", return_value={"x-litellm-tags": "t"}
            ),
            patch("codemie.enterprise.litellm.stt_client._configure_direct_runtime_overrides"),
        ):
            client = get_litellm_stt_client(_model(), None, "user@example.com", "user-1")

        assert str(client.base_url).rstrip("/") == "https://litellm.example"
        assert client.api_key == "app-key"
        assert client.default_headers["x-litellm-tags"] == "t"

    def test_budget_attribution_puts_the_customer_id_in_the_default_headers(self, litellm_active, monkeypatch):
        monkeypatch.setattr(config, "LITE_LLM_TAGS_HEADER_VALUE", "default")
        monkeypatch.setattr(config, "LITE_LLM_PROJECTS_TO_TAGS_LIST", "")
        with (
            patch("codemie.enterprise.litellm.dependencies.check_user_budget"),
            patch("codemie.enterprise.litellm.llm_factory._get_direct_request_category_budget_id", return_value=None),
            patch("codemie.enterprise.litellm.llm_factory._mirror_budget_assignment"),
        ):
            client = get_litellm_stt_client(_model(), None, "user@example.com", "uid-1")

        assert client.default_headers["x-litellm-customer-id"] == "user@example.com"
