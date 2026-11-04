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

from unittest.mock import MagicMock, patch

import pytest

from codemie.configs import config
from codemie.configs.llm_config import LiteLLMModels, LLMModel, llm_config
from codemie.service.llm_service.llm_service import LLMService


def _stt_model(name: str) -> LLMModel:
    return LLMModel(base_name=name, deployment_name=name, enabled=True)


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(config, "STT_API_KEY", "")
    monkeypatch.setattr(config, "STT_MODEL_NAME", "")
    monkeypatch.setattr(config, "STT_API_DEPLOYMENT_NAME", "")
    monkeypatch.setattr(config, "LLM_PROXY_ENABLED", False)
    return LLMService(llm_config)


@pytest.fixture
def user():
    mock_user = MagicMock()
    mock_user.is_external_user = False
    return mock_user


def _enable_env_model(monkeypatch, name="whisper"):
    monkeypatch.setattr(config, "STT_API_KEY", "key")
    monkeypatch.setattr(config, "STT_MODEL_NAME", name)
    monkeypatch.setattr(config, "STT_API_DEPLOYMENT_NAME", name)


def _enable_litellm(service, monkeypatch, *names):
    monkeypatch.setattr(config, "LLM_PROXY_ENABLED", True)
    monkeypatch.setattr(config, "LLM_PROXY_MODE", "lite_llm")
    service.initialize_default_litellm_models(LiteLLMModels(audio_transcription_models=[_stt_model(n) for n in names]))


def _names(models):
    return [m.base_name for m in models]


class TestSttModelSources:
    def test_nothing_configured_returns_no_models_and_no_default(self, service, user):
        assert service.get_allowed_audio_transcription_models(user) == []
        assert service.resolve_stt_model(user) is None

    def test_env_model_only_is_listed_and_is_the_default(self, service, user, monkeypatch):
        _enable_env_model(monkeypatch)

        assert _names(service.get_allowed_audio_transcription_models(user)) == ["whisper"]
        assert service.resolve_stt_model(user).base_name == "whisper"

    def test_litellm_models_only_are_listed_and_first_is_the_default(self, service, user, monkeypatch):
        _enable_litellm(service, monkeypatch, "custom-stt", "other-stt")

        assert _names(service.get_allowed_audio_transcription_models(user)) == ["custom-stt", "other-stt"]
        assert service.resolve_stt_model(user).base_name == "custom-stt"

    def test_env_model_is_listed_first_and_stays_the_default_next_to_litellm_models(self, service, user, monkeypatch):
        _enable_env_model(monkeypatch)
        _enable_litellm(service, monkeypatch, "custom-stt")

        assert _names(service.get_allowed_audio_transcription_models(user)) == ["whisper", "custom-stt"]
        assert service.resolve_stt_model(user).base_name == "whisper"

    def test_litellm_model_with_the_env_model_name_wins(self, service, user, monkeypatch):
        _enable_env_model(monkeypatch, "whisper")
        _enable_litellm(service, monkeypatch, "whisper", "custom-stt")

        assert _names(service.get_allowed_audio_transcription_models(user)) == ["whisper", "custom-stt"]
        assert service.is_legacy_stt_model(service.resolve_stt_model(user)) is False

    def test_litellm_model_with_the_env_model_name_keeps_the_default_place(self, service, user, monkeypatch):
        _enable_env_model(monkeypatch, "whisper")
        _enable_litellm(service, monkeypatch, "other-stt", "whisper")

        assert _names(service.get_allowed_audio_transcription_models(user)) == ["whisper", "other-stt"]
        assert service.resolve_stt_model(user).base_name == "whisper"
        assert service.is_legacy_stt_model(service.resolve_stt_model(user)) is False

    def test_litellm_models_are_not_listed_when_the_proxy_mode_cannot_serve_them(self, service, user, monkeypatch):
        _enable_env_model(monkeypatch)
        _enable_litellm(service, monkeypatch, "custom-stt")
        monkeypatch.setattr(config, "LLM_PROXY_MODE", "internal")

        assert _names(service.get_allowed_audio_transcription_models(user)) == ["whisper"]

    def test_litellm_models_are_ignored_when_the_proxy_is_off(self, service, user, monkeypatch):
        _enable_litellm(service, monkeypatch, "custom-stt")
        monkeypatch.setattr(config, "LLM_PROXY_ENABLED", False)

        assert service.get_allowed_audio_transcription_models(user) == []


class TestExternalUserAudioOnlyIntegration:
    GET_USER_MODELS = "codemie.enterprise.litellm.get_user_allowed_models"

    @pytest.fixture
    def external_user(self):
        external = MagicMock()
        external.id = "ext-1"
        external.is_external_user = True
        external.project_names = ["app"]
        return external

    def test_user_audio_models_are_used_and_chat_models_stay_the_default_catalog(
        self, service, external_user, monkeypatch
    ):
        monkeypatch.setattr(config, "LLM_PROXY_ENABLED", True)
        monkeypatch.setattr(config, "LLM_PROXY_MODE", "lite_llm")
        service.initialize_default_litellm_models(
            LiteLLMModels(
                chat_models=[_stt_model("default-chat")], audio_transcription_models=[_stt_model("default-stt")]
            )
        )
        user_models = LiteLLMModels(audio_transcription_models=[_stt_model("own-stt")])

        with patch(self.GET_USER_MODELS, return_value=user_models):
            allowed = service.get_allowed_models(external_user)

        assert _names(allowed.audio_transcription_models) == ["own-stt"]
        assert _names(allowed.chat_models) == ["default-chat"]

    def test_user_model_with_the_env_model_name_is_routed_as_a_litellm_model(self, service, external_user, monkeypatch):
        _enable_env_model(monkeypatch, "whisper")
        monkeypatch.setattr(config, "LLM_PROXY_ENABLED", True)
        monkeypatch.setattr(config, "LLM_PROXY_MODE", "lite_llm")
        service.initialize_default_litellm_models(LiteLLMModels(chat_models=[_stt_model("default-chat")]))
        user_models = LiteLLMModels(audio_transcription_models=[_stt_model("whisper")])

        with patch(self.GET_USER_MODELS, return_value=user_models):
            selected = service.resolve_stt_model(external_user, "whisper")

        assert selected.base_name == "whisper"
        assert service.is_legacy_stt_model(selected) is False

    def test_user_without_any_supported_model_gets_the_default_catalog(self, service, external_user, monkeypatch):
        monkeypatch.setattr(config, "LLM_PROXY_ENABLED", True)
        monkeypatch.setattr(config, "LLM_PROXY_MODE", "lite_llm")
        service.initialize_default_litellm_models(
            LiteLLMModels(
                chat_models=[_stt_model("default-chat")], audio_transcription_models=[_stt_model("default-stt")]
            )
        )

        with patch(self.GET_USER_MODELS, return_value=None):
            allowed = service.get_allowed_models(external_user)

        assert _names(allowed.audio_transcription_models) == ["default-stt"]


class TestResolveSttModel:
    def test_named_model_is_returned(self, service, user, monkeypatch):
        _enable_env_model(monkeypatch)
        _enable_litellm(service, monkeypatch, "custom-stt")

        assert service.resolve_stt_model(user, "custom-stt").base_name == "custom-stt"
        assert service.resolve_stt_model(user, "whisper").base_name == "whisper"

    def test_unknown_model_raises(self, service, user, monkeypatch):
        _enable_env_model(monkeypatch)

        with pytest.raises(ValueError, match="nope"):
            service.resolve_stt_model(user, "nope")

    def test_env_model_is_identified_as_legacy(self, service, monkeypatch):
        _enable_env_model(monkeypatch)
        _enable_litellm(service, monkeypatch, "custom-stt")

        assert service.is_legacy_stt_model(service.get_legacy_stt_model()) is True
        assert service.is_legacy_stt_model(_stt_model("custom-stt")) is False

    def test_litellm_model_with_the_env_model_name_is_not_legacy_even_when_only_the_user_has_it(
        self, service, monkeypatch
    ):
        _enable_env_model(monkeypatch, "whisper")

        assert service.is_legacy_stt_model(_stt_model("whisper")) is False


class TestLegacySttModel:
    def test_no_legacy_model_without_api_key(self, service):
        assert service.get_legacy_stt_model() is None

    def test_name_falls_back_to_deployment_name_then_default(self, service, monkeypatch):
        monkeypatch.setattr(config, "STT_API_KEY", "key")
        monkeypatch.setattr(config, "STT_MODEL_NAME", "")
        monkeypatch.setattr(config, "STT_API_DEPLOYMENT_NAME", "deploy")
        assert service.get_legacy_stt_model().base_name == "deploy"

        monkeypatch.setattr(config, "STT_API_DEPLOYMENT_NAME", "")
        assert service.get_legacy_stt_model().base_name == "default"
