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

import io
from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest
from openai.types.audio import Transcription

from codemie.configs import config
from codemie.configs.llm_config import CostConfig, LiteLLMModels, LLMModel, SttConfig, llm_config
from codemie.core.exceptions import ExtendedHTTPException, UnknownSttModelError
from codemie.service import stt_service as stt_module
from codemie.service.llm_service.llm_service import LLMService
from codemie.service.stt_service import (
    TRACE_NAME,
    SttResult,
    _collect_response_fields,
    _collect_response_headers,
    _cost_details,
    select_forward_headers,
    stt_service,
)

CUSTOM_CONFIG = SttConfig(
    default_language="de",
    forward_request_headers=["x-conversation-id", "x-conversation-trace-id"],
    response_headers=["x-transaction-id", "x-file-uri"],
    response_body_fields=["file_path"],
)


PRICES = CostConfig(input=0.5, output=2.0, input_cost_per_second=0.25)


@pytest.fixture
def user():
    mock_user = MagicMock()
    mock_user.id = "user-1"
    mock_user.is_external_user = False
    return mock_user


@pytest.fixture
def observability():
    provider = MagicMock()
    provider.is_enabled.return_value = True
    provider.make_observe_decorator.return_value = lambda **kwargs: lambda fn: fn
    with patch.object(stt_module, "get_observability_provider", return_value=provider):
        yield provider


@pytest.fixture
def clients(monkeypatch, user, observability):
    """Env (legacy) model "whisper" plus LiteLLM model "custom-stt"; both clients are mocks."""
    monkeypatch.setattr(config, "STT_API_KEY", "key")
    monkeypatch.setattr(config, "STT_MODEL_NAME", "whisper")
    monkeypatch.setattr(config, "STT_API_DEPLOYMENT_NAME", "whisper")
    monkeypatch.setattr(config, "LLM_PROXY_ENABLED", True)
    monkeypatch.setattr(config, "LLM_PROXY_MODE", "lite_llm")

    service = LLMService(llm_config)
    service.initialize_default_litellm_models(
        LiteLLMModels(
            audio_transcription_models=[
                LLMModel(base_name="custom-stt", deployment_name="custom-stt", enabled=True, stt_config=CUSTOM_CONFIG),
                LLMModel(base_name="plain-stt", deployment_name="plain-stt", enabled=True),
                LLMModel(
                    base_name="priced-stt",
                    deployment_name="priced-stt",
                    enabled=True,
                    cost=PRICES,
                ),
            ]
        )
    )
    legacy_client = MagicMock()
    litellm_client = MagicMock()
    with (
        patch.object(stt_module, "llm_service", service),
        patch("codemie.core.dependecies.get_stt_openai_client", return_value=legacy_client),
        patch("codemie.enterprise.litellm.get_litellm_stt_client", return_value=litellm_client) as litellm_factory,
    ):
        yield MagicMock(legacy=legacy_client, litellm=litellm_client, litellm_factory=litellm_factory)


def _raw(text="hello", headers=None, **extra_body_fields):
    raw = MagicMock()
    raw.parse.return_value = Transcription.model_validate({"text": text, **extra_body_fields})
    raw.headers = headers or {}
    return raw


def _audio():
    audio = io.BytesIO(b"audio-bytes")
    audio.name = "audio.webm"
    return audio


def _create_kwargs(client):
    return client.audio.transcriptions.with_raw_response.create.call_args.kwargs


def _prepare(client, raw):
    client.audio.transcriptions.with_raw_response.create.return_value = raw


class TestModelRouting:
    def test_default_model_is_the_env_model_and_uses_the_legacy_client_unchanged(self, clients, user):
        _prepare(clients.legacy, _raw("from whisper"))

        result = stt_service.transcribe(_audio(), user=user)

        assert result.text == "from whisper"
        kwargs = _create_kwargs(clients.legacy)
        assert kwargs["model"] == "whisper"
        assert "language" not in kwargs
        assert "extra_headers" not in kwargs
        assert "extra_body" not in kwargs
        clients.litellm_factory.assert_not_called()

    def test_litellm_client_is_built_with_the_user_context_and_identity(self, clients, user):
        from codemie.configs.logger import current_username, logging_user_id
        from codemie.core.dependecies import litellm_context
        from codemie.rest_api.models.settings import LiteLLMContext, LiteLLMCredentials

        context = LiteLLMContext(credentials=LiteLLMCredentials(api_key="user-key"), current_project="proj")
        _prepare(clients.litellm, _raw())
        tokens = (litellm_context.set(context), current_username.set("jdoe"), logging_user_id.set("u-1"))
        try:
            with patch("codemie.service.llm_service.utils.set_llm_context"):
                stt_service.transcribe(_audio(), user=user, model_name="custom-stt")
        finally:
            litellm_context.reset(tokens[0])
            current_username.reset(tokens[1])
            logging_user_id.reset(tokens[2])

        factory_kwargs = clients.litellm_factory.call_args.kwargs
        assert factory_kwargs["llm_model_details"].base_name == "custom-stt"
        assert factory_kwargs["litellm_context"] is context
        assert factory_kwargs["username"] == "jdoe"
        assert factory_kwargs["user_id"] == "u-1"

    def test_named_litellm_model_uses_the_litellm_client(self, clients, user):
        _prepare(clients.litellm, _raw("from custom"))

        result = stt_service.transcribe(_audio(), user=user, model_name="custom-stt")

        assert result.text == "from custom"
        kwargs = _create_kwargs(clients.litellm)
        assert kwargs["model"] == "custom-stt"
        assert "extra_body" not in kwargs
        clients.legacy.audio.transcriptions.with_raw_response.create.assert_not_called()

    def test_unknown_model_raises_the_dedicated_error(self, clients, user):
        with pytest.raises(UnknownSttModelError, match="nope"):
            stt_service.transcribe(_audio(), user=user, model_name="nope")

    def test_a_model_name_without_a_user_is_rejected(self, clients):
        with pytest.raises(UnknownSttModelError):
            stt_service.transcribe(_audio(), model_name="custom-stt")

    def test_without_user_the_legacy_client_is_used(self, clients):
        _prepare(clients.legacy, _raw())

        stt_service.transcribe(_audio())

        assert _create_kwargs(clients.legacy)["model"] == "whisper"


class TestLanguageResolution:
    def test_request_language_wins_over_the_model_language(self, clients, user):
        _prepare(clients.litellm, _raw())

        stt_service.transcribe(_audio(), user=user, model_name="custom-stt", language="tr")

        assert _create_kwargs(clients.litellm)["language"] == "tr"

    def test_model_language_is_used_when_the_request_has_none(self, clients, user):
        _prepare(clients.litellm, _raw())

        stt_service.transcribe(_audio(), user=user, model_name="custom-stt")

        assert _create_kwargs(clients.litellm)["language"] == "de"

    def test_no_language_is_sent_for_models_without_a_language(self, clients, user):
        _prepare(clients.litellm, _raw())
        _prepare(clients.legacy, _raw())

        stt_service.transcribe(_audio(), user=user, model_name="plain-stt")
        stt_service.transcribe(_audio(), user=user)

        assert "language" not in _create_kwargs(clients.litellm)
        assert "language" not in _create_kwargs(clients.legacy)

    def test_env_model_gets_a_language_only_from_the_request(self, clients, user):
        _prepare(clients.legacy, _raw())

        stt_service.transcribe(_audio(), user=user, language="en")

        assert _create_kwargs(clients.legacy)["language"] == "en"


class TestProviderMetadata:
    INCOMING = {
        "X-Conversation-Id": "conv-1",
        "X-Conversation-Trace-Id": "trace-1",
        "x-userinfo": "secret",
        "authorization": "Bearer token",
    }

    def test_only_allowed_request_headers_are_forwarded(self, clients, user):
        _prepare(clients.litellm, _raw())

        stt_service.transcribe(_audio(), user=user, model_name="custom-stt", forward_headers=self.INCOMING)

        assert _create_kwargs(clients.litellm)["extra_headers"] == {
            "X-Conversation-Id": "conv-1",
            "X-Conversation-Trace-Id": "trace-1",
        }

    def test_no_request_headers_are_forwarded_for_models_without_configuration(self, clients, user):
        _prepare(clients.litellm, _raw())
        _prepare(clients.legacy, _raw())

        stt_service.transcribe(_audio(), user=user, model_name="plain-stt", forward_headers=self.INCOMING)
        stt_service.transcribe(_audio(), user=user, forward_headers=self.INCOMING)

        assert "extra_headers" not in _create_kwargs(clients.litellm)
        assert "extra_headers" not in _create_kwargs(clients.legacy)

    def test_response_headers_are_matched_without_the_gateway_prefix(self, clients, user):
        _prepare(
            clients.litellm,
            _raw(
                headers={
                    "llm_provider-x-transaction-id": "tx-1",
                    "X-File-Uri": "https://bucket/key.wav",
                    "content-type": "application/json",
                }
            ),
        )

        result = stt_service.transcribe(_audio(), user=user, model_name="custom-stt")

        assert result.headers == {"x-transaction-id": "tx-1", "x-file-uri": "https://bucket/key.wav"}

    def test_missing_optional_response_header_is_skipped(self, clients, user):
        _prepare(clients.litellm, _raw(headers={"x-transaction-id": "tx-1"}))

        result = stt_service.transcribe(_audio(), user=user, model_name="custom-stt")

        assert result.headers == {"x-transaction-id": "tx-1"}

    def test_prefix_pattern_matches_response_headers(self, clients, user):
        model = LLMModel(
            base_name="prefix-stt",
            deployment_name="prefix-stt",
            enabled=True,
            stt_config=SttConfig(response_headers=["x-vendor-*"]),
        )
        stt_module.llm_service.initialize_default_litellm_models(LiteLLMModels(audio_transcription_models=[model]))
        _prepare(clients.litellm, _raw(headers={"X-Vendor-Id": "1", "x-other": "2"}))

        result = stt_service.transcribe(_audio(), user=user, model_name="prefix-stt")

        assert result.headers == {"x-vendor-id": "1"}

    def test_configured_body_fields_are_returned_and_others_are_ignored(self, clients, user):
        _prepare(clients.litellm, _raw(file_path="s3://bucket/key.wav", ignored="x"))

        result = stt_service.transcribe(_audio(), user=user, model_name="custom-stt")

        assert result.fields == {"file_path": "s3://bucket/key.wav"}
        assert result.metadata == {"file_path": "s3://bucket/key.wav"}

    def test_legacy_model_returns_no_metadata(self, clients, user):
        _prepare(clients.legacy, _raw(headers={"x-transaction-id": "tx-1"}, file_path="s3://x"))

        result = stt_service.transcribe(_audio(), user=user)

        assert result == SttResult(text="hello")


class TestForwardedHeadersReuseTheExistingBlocklists:
    @staticmethod
    def _allow(*names: str) -> SttConfig:
        """An stt_config that explicitly names every incoming header as allowed (no wildcard)."""
        return SttConfig(forward_request_headers=list(names))

    def test_headers_in_the_configured_blocklist_are_never_forwarded(self):
        incoming = {
            "Authorization": "Bearer secret",
            "Cookie": "session=1",
            "X-Api-Key": "secret",
            "x-auth-token": "token",
            "x-conversation-id": "conv-1",
        }
        allowed = self._allow("authorization", "cookie", "x-api-key", "x-auth-token", "x-conversation-id")

        assert select_forward_headers(incoming, allowed) == {"x-conversation-id": "conv-1"}

    def test_hop_by_hop_and_internal_headers_of_the_proxy_are_never_forwarded(self):
        incoming = {
            "Host": "example",
            "Content-Length": "10",
            "Connection": "keep-alive",
            "Proxy-Authorization": "Basic x",
            "TE": "trailers",
            "Upgrade": "websocket",
            "x-litellm-customer-id": "customer-2",
            "x-conversation-id": "conv-1",
        }
        allowed = self._allow(
            "host",
            "content-length",
            "connection",
            "proxy-authorization",
            "te",
            "upgrade",
            "x-litellm-customer-id",
            "x-conversation-id",
        )

        assert select_forward_headers(incoming, allowed) == {"x-conversation-id": "conv-1"}

    def test_names_added_to_the_configured_blocklist_are_blocked_too(self, monkeypatch):
        monkeypatch.setattr(config, "FORWARDED_HEADERS_BLOCKLIST", "authorization, X-Custom-Blocked")
        incoming = {"x-custom-blocked": "v", "x-conversation-id": "conv-1"}
        allowed = self._allow("x-custom-blocked", "x-conversation-id")

        assert select_forward_headers(incoming, allowed) == {"x-conversation-id": "conv-1"}

    def test_a_bare_wildcard_pattern_is_ignored_and_forwards_nothing(self):
        wildcard = SttConfig(forward_request_headers=["*"])

        assert select_forward_headers({"x-conversation-id": "conv-1"}, wildcard) == {}

    def test_a_blocked_header_is_not_forwarded_even_when_listed_by_name(self):
        named = SttConfig(forward_request_headers=["authorization", "x-conversation-id"])

        forwarded = select_forward_headers({"authorization": "Bearer secret", "x-conversation-id": "conv-1"}, named)

        assert forwarded == {"x-conversation-id": "conv-1"}

    def test_headers_that_are_not_listed_in_the_model_config_are_not_forwarded(self):
        named = SttConfig(forward_request_headers=["x-conversation-id"])

        assert select_forward_headers({"x-userinfo": "claims", "x-conversation-id": "c"}, named) == {
            "x-conversation-id": "c"
        }


class TestResponseHeaderSafety:
    @staticmethod
    def _allow(*names: str) -> SttConfig:
        """An stt_config that explicitly names every incoming header as allowed (no wildcard)."""
        return SttConfig(response_headers=list(names))

    def test_cookie_credential_and_framing_headers_are_dropped(self):
        headers = {
            "Content-Length": "5",
            "Set-Cookie": "a=b",
            "Authorization": "Bearer x",
            "transfer-encoding": "chunked",
            "Connection": "close",
            "x-transaction-id": "tx-1",
        }
        allowed = self._allow(
            "content-length", "set-cookie", "authorization", "transfer-encoding", "connection", "x-transaction-id"
        )

        assert _collect_response_headers(headers, allowed) == {"x-transaction-id": "tx-1"}

    def test_names_added_to_the_configured_blocklist_are_dropped_from_responses_too(self, monkeypatch):
        monkeypatch.setattr(config, "FORWARDED_HEADERS_BLOCKLIST", "set-cookie,x-secret")
        allowed = self._allow("x-secret", "x-transaction-id")

        assert _collect_response_headers({"x-secret": "s", "x-transaction-id": "tx-1"}, allowed) == {
            "x-transaction-id": "tx-1"
        }

    def test_values_that_cannot_be_encoded_are_dropped(self):
        headers = {"x-transaction-id": "tx-1", "x-note": "Əli"}
        allowed = self._allow("x-transaction-id", "x-note")

        assert _collect_response_headers(headers, allowed) == {"x-transaction-id": "tx-1"}

    @pytest.mark.parametrize("order", [("x-id", "llm_provider-x-id"), ("llm_provider-x-id", "x-id")])
    def test_provider_header_wins_over_the_gateway_header_of_the_same_name(self, order):
        values = {"x-id": "gateway", "llm_provider-x-id": "provider"}
        allowed = self._allow("x-id")

        assert _collect_response_headers({k: values[k] for k in order}, allowed) == {"x-id": "provider"}

    def test_a_bare_wildcard_pattern_is_ignored_and_collects_nothing(self):
        wildcard = SttConfig(response_headers=["*"])

        assert _collect_response_headers({"x-transaction-id": "tx-1"}, wildcard) == {}


class TestResponseFieldMatching:
    def _parsed(self, **fields):
        return Transcription.model_validate({"text": "hi", **fields})

    def test_names_match_without_regard_to_case(self):
        parsed = self._parsed(File_Path="s3://x")

        fields = _collect_response_fields(parsed, SttConfig(response_body_fields=["file_path"]))

        assert fields == {"File_Path": "s3://x"}

    def test_trailing_star_matches_a_prefix(self):
        parsed = self._parsed(file_path="s3://x", file_size=7, other="no")

        fields = _collect_response_fields(parsed, SttConfig(response_body_fields=["file_*"]))

        assert fields == {"file_path": "s3://x", "file_size": 7}

    def test_missing_values_are_skipped(self):
        parsed = self._parsed(file_path=None)

        assert _collect_response_fields(parsed, SttConfig(response_body_fields=["file_*"])) == {}


class TestNothingConfigured:
    def test_no_stt_model_at_all_is_a_503_not_configured(self, clients, monkeypatch):
        monkeypatch.setattr(config, "STT_API_KEY", "")

        with pytest.raises(ExtendedHTTPException) as error:
            stt_service.transcribe(_audio())

        assert error.value.code == 503
        assert "not configured" in error.value.message

    def test_incomplete_env_settings_are_a_503_without_internal_details(self, clients, user):
        with patch("codemie.core.dependecies.get_stt_openai_client", side_effect=ValueError("azure_endpoint missing")):
            with pytest.raises(ExtendedHTTPException) as error:
                stt_service.transcribe(_audio(), user=user)

        assert error.value.code == 503
        assert "azure_endpoint" not in str(error.value.details)


class TestLiteLLMUnavailable:
    def test_missing_litellm_client_is_a_server_side_503_not_a_client_error(self, clients, user):
        clients.litellm_factory.return_value = None

        with pytest.raises(ExtendedHTTPException) as error:
            stt_service.transcribe(_audio(), user=user, model_name="custom-stt")

        assert error.value.code == 503


class TestUserLiteLLMContext:
    SET_CONTEXT = "codemie.service.llm_service.utils.set_llm_context"

    def test_context_is_set_for_the_user_before_a_litellm_model_call(self, clients, user):
        _prepare(clients.litellm, _raw())

        with patch(self.SET_CONTEXT) as set_context:
            stt_service.transcribe(_audio(), user=user, model_name="custom-stt")

        set_context.assert_called_once_with(None, user.current_project, user)

    def test_context_is_not_touched_for_the_env_model(self, clients, user):
        _prepare(clients.legacy, _raw())

        with patch(self.SET_CONTEXT) as set_context:
            stt_service.transcribe(_audio(), user=user)

        set_context.assert_not_called()


class TestConversationServiceWiring:
    def test_request_values_are_mapped_to_the_stt_service_call(self, user):
        from codemie.service.conversation_service import ConversationService

        audio = _audio()
        headers = {"x-conversation-id": "conv-1"}
        expected = SttResult(text="hello")

        with patch.object(stt_service, "transcribe", return_value=expected) as transcribe:
            result = ConversationService.recognize_speech(
                audio,
                user=user,
                model="custom-stt",
                language="de",
                forward_headers=headers,
                conversation_id="c1",
            )

        assert result is expected
        transcribe.assert_called_once_with(
            audio,
            user=user,
            model_name="custom-stt",
            language="de",
            forward_headers=headers,
            conversation_id="c1",
        )


class TestObserveDecorator:
    def test_outer_span_and_nested_generation_are_observed_without_capturing_audio(self, clients, user, observability):
        decorator_calls = []

        def recording_factory(**kwargs):
            decorator_calls.append(kwargs)
            return lambda fn: fn

        observability.make_observe_decorator.return_value = recording_factory
        _prepare(clients.legacy, _raw())

        stt_service.transcribe(_audio(), user=user)

        assert decorator_calls == [
            {"name": TRACE_NAME, "as_type": "span", "capture_input": False, "capture_output": False},
            {"name": "stt.transcribe", "as_type": "generation", "capture_input": False, "capture_output": False},
        ]

    def test_trace_fields_are_set_on_the_outer_span_and_generation_fields_on_the_nested_one(
        self, clients, user, observability
    ):
        events = []

        def recording_factory(**kwargs):
            def decorate(fn):
                def wrapper(*args, **wkwargs):
                    events.append(("enter", kwargs["name"]))
                    try:
                        return fn(*args, **wkwargs)
                    finally:
                        events.append(("exit", kwargs["name"]))

                return wrapper

            return decorate

        observability.make_observe_decorator.return_value = recording_factory
        observability.update_current_trace.side_effect = lambda **kw: events.append(("trace", sorted(kw)))
        observability.update_current_generation.side_effect = lambda **kw: events.append(("generation", sorted(kw)))
        _prepare(clients.legacy, _raw())

        stt_service.transcribe(_audio(), user=user, conversation_id="conv-1")

        names = [(kind, detail if kind in ("enter", "exit") else "") for kind, detail in events]
        assert names == [
            ("enter", TRACE_NAME),
            ("trace", ""),
            ("enter", "stt.transcribe"),
            ("generation", ""),
            ("generation", ""),
            ("exit", "stt.transcribe"),
            ("trace", ""),
            ("exit", TRACE_NAME),
        ]
        assert "name" in events[1][1] and "session_id" in events[1][1]


class TestObservability:
    def test_generation_and_trace_record_model_language_usage_and_metadata(self, clients, user, observability):
        raw = _raw(headers={"x-transaction-id": "tx-1"})
        raw.parse.return_value = Transcription.model_validate(
            {"text": "hello", "usage": {"type": "duration", "seconds": 5}}
        )
        _prepare(clients.litellm, raw)

        stt_service.transcribe(
            _audio(), user=user, model_name="custom-stt", forward_headers={"x-conversation-id": "conv-1"}
        )

        first, last = observability.update_current_generation.call_args_list
        assert first.kwargs["model"] == "custom-stt"
        assert first.kwargs["model_parameters"] == {"language": "de"}
        assert first.kwargs["input"] == {"audio_file": "audio.webm", "language": "de"}
        assert first.kwargs["metadata"]["via_litellm"] is True
        assert first.kwargs["metadata"]["x-conversation-id"] == "conv-1"
        assert last.kwargs["output"] == "hello"
        assert last.kwargs["usage_details"] == {"audio_seconds": 5}
        assert last.kwargs["metadata"] == {
            "x-conversation-id": "conv-1",
            "x-transaction-id": "tx-1",
            "transcript_chars": 5,
            "usage": {"audio_seconds": 5},
        }

        trace_first, trace_last = observability.update_current_trace.call_args_list
        assert trace_first.kwargs["name"] == TRACE_NAME
        assert trace_first.kwargs["user_id"] == "user-1"
        assert trace_first.kwargs["tags"] == ["stt", "custom-stt"]
        assert trace_first.kwargs["input"] == {"audio_file": "audio.webm", "language": "de"}
        assert trace_first.kwargs["metadata"] == {
            "type": "speech-to-text",
            "model": "custom-stt",
            "language": "de",
            "language_source": "model_default",
            "via_litellm": True,
            "audio_file": "audio.webm",
            "audio_bytes": len(b"audio-bytes"),
            "x-conversation-id": "conv-1",
        }
        assert trace_last.kwargs["output"] == "hello"
        assert trace_last.kwargs["metadata"]["x-transaction-id"] == "tx-1"
        assert trace_last.kwargs["metadata"]["transcript_chars"] == 5

    def test_conversation_id_becomes_the_trace_session(self, clients, user, observability):
        _prepare(clients.legacy, _raw())

        stt_service.transcribe(_audio(), user=user, conversation_id="conv-42")

        trace_first = observability.update_current_trace.call_args_list[0]
        assert trace_first.kwargs["session_id"] == "conv-42"

    def test_request_language_is_recorded_as_request_source(self, clients, user, observability):
        _prepare(clients.legacy, _raw())

        stt_service.transcribe(_audio(), user=user, language="tr", conversation_id="conv-42")

        metadata = observability.update_current_trace.call_args_list[0].kwargs["metadata"]
        assert metadata["language"] == "tr"
        assert metadata["language_source"] == "request"
        assert metadata["conversation_id"] == "conv-42"
        assert metadata["via_litellm"] is False

    def test_language_left_to_the_provider_is_recorded_as_auto(self, clients, user, observability):
        _prepare(clients.legacy, _raw())

        stt_service.transcribe(_audio(), user=user)

        metadata = observability.update_current_trace.call_args_list[0].kwargs["metadata"]
        assert "language" not in metadata
        assert metadata["language_source"] == "provider_auto"

    def test_trace_has_no_session_without_conversation_id(self, clients, user, observability):
        _prepare(clients.legacy, _raw())

        stt_service.transcribe(_audio(), user=user)

        assert observability.update_current_trace.call_args_list[0].kwargs["session_id"] is None

    def test_token_usage_is_mapped(self, clients, user, observability):
        raw = _raw()
        raw.parse.return_value = Transcription.model_validate(
            {"text": "hi", "usage": {"type": "tokens", "input_tokens": 3, "output_tokens": 2, "total_tokens": 5}}
        )
        _prepare(clients.legacy, raw)

        stt_service.transcribe(_audio(), user=user)

        usage = observability.update_current_generation.call_args_list[-1].kwargs["usage_details"]
        assert usage == {"input": 3, "output": 2, "total": 5}

    def test_duration_usage_is_priced_per_second_on_the_generation(self, clients, user, observability):
        raw = _raw()
        raw.parse.return_value = Transcription.model_validate(
            {"text": "hi", "usage": {"type": "duration", "seconds": 6}}
        )
        _prepare(clients.litellm, raw)

        stt_service.transcribe(_audio(), user=user, model_name="priced-stt")

        assert observability.update_current_generation.call_args_list[-1].kwargs["cost_details"] == {"total": 1.5}

    def test_token_usage_is_priced_per_token_on_the_generation(self, clients, user, observability):
        raw = _raw()
        raw.parse.return_value = Transcription.model_validate(
            {"text": "hi", "usage": {"type": "tokens", "input_tokens": 4, "output_tokens": 3, "total_tokens": 7}}
        )
        _prepare(clients.litellm, raw)

        stt_service.transcribe(_audio(), user=user, model_name="priced-stt")

        cost = observability.update_current_generation.call_args_list[-1].kwargs["cost_details"]
        assert cost == {"input": 2.0, "output": 6.0, "total": 8.0}

    def test_no_cost_is_attached_for_a_model_without_prices(self, clients, user, observability):
        raw = _raw()
        raw.parse.return_value = Transcription.model_validate(
            {"text": "hi", "usage": {"type": "duration", "seconds": 6}}
        )
        _prepare(clients.litellm, raw)

        stt_service.transcribe(_audio(), user=user, model_name="plain-stt")

        assert observability.update_current_generation.call_args_list[-1].kwargs["cost_details"] is None

    def test_no_cost_is_attached_on_the_legacy_path(self, clients, user, observability):
        _prepare(clients.legacy, _raw())

        stt_service.transcribe(_audio(), user=user)

        assert observability.update_current_generation.call_args_list[-1].kwargs["cost_details"] is None

    def test_audio_content_is_never_sent_to_observability(self, clients, user, observability):
        _prepare(clients.legacy, _raw())

        stt_service.transcribe(_audio(), user=user)

        recorded = str(observability.mock_calls)
        assert "audio-bytes" not in recorded

    def test_nothing_is_recorded_when_observability_is_disabled(self, clients, user, observability):
        observability.is_enabled.return_value = False
        _prepare(clients.legacy, _raw())

        result = stt_service.transcribe(_audio(), user=user)

        assert result.text == "hello"
        observability.update_current_generation.assert_not_called()
        observability.update_current_trace.assert_not_called()

    def test_failure_is_recorded_as_error(self, clients, user, observability):
        clients.legacy.audio.transcriptions.with_raw_response.create.side_effect = ValueError("boom")

        with pytest.raises(ValueError):
            stt_service.transcribe(_audio(), user=user)

        error_call = observability.update_current_generation.call_args_list[-1]
        assert error_call.kwargs == {"level": "ERROR", "status_message": "boom"}


def _status_error(status_code):
    response = httpx.Response(
        status_code,
        request=httpx.Request("POST", "http://provider"),
        json={"error": {"message": "provider message", "type": "invalid_request_error"}},
    )
    return openai.APIStatusError("provider message", response=response, body=None)


class TestProviderErrors:
    @pytest.mark.parametrize(
        ("provider_status", "expected_status"),
        [(400, 400), (413, 413), (429, 429), (503, 503), (504, 504), (401, 502), (403, 502), (404, 502), (500, 502)],
    )
    def test_status_errors_are_mapped(self, clients, user, provider_status, expected_status):
        clients.legacy.audio.transcriptions.with_raw_response.create.side_effect = _status_error(provider_status)

        with pytest.raises(ExtendedHTTPException) as error:
            stt_service.transcribe(_audio(), user=user)

        assert error.value.code == expected_status
        assert "provider message" in error.value.details

    def test_timeout_becomes_504(self, clients, user):
        clients.legacy.audio.transcriptions.with_raw_response.create.side_effect = openai.APITimeoutError(
            httpx.Request("POST", "http://provider")
        )

        with pytest.raises(ExtendedHTTPException) as error:
            stt_service.transcribe(_audio(), user=user)

        assert error.value.code == 504

    def test_connection_error_becomes_502(self, clients, user):
        clients.legacy.audio.transcriptions.with_raw_response.create.side_effect = openai.APIConnectionError(
            request=httpx.Request("POST", "http://provider")
        )

        with pytest.raises(ExtendedHTTPException) as error:
            stt_service.transcribe(_audio(), user=user)

        assert error.value.code == 502

    def test_other_errors_are_not_wrapped(self, clients, user):
        clients.legacy.audio.transcriptions.with_raw_response.create.side_effect = RuntimeError("unexpected")

        with pytest.raises(RuntimeError):
            stt_service.transcribe(_audio(), user=user)


class TestCostDetails:
    def test_duration_usage_is_seconds_times_the_per_second_price(self):
        assert _cost_details({"audio_seconds": 6}, PRICES) == {"total": 1.5}

    def test_duration_usage_without_a_per_second_price_has_no_cost(self):
        assert _cost_details({"audio_seconds": 6}, CostConfig(input=0.5, output=2.0)) is None

    def test_token_usage_is_priced_for_input_and_output(self):
        assert _cost_details({"input": 4, "output": 3, "total": 7}, PRICES) == {
            "input": 2.0,
            "output": 6.0,
            "total": 8.0,
        }

    def test_token_usage_with_only_input_tokens_is_priced(self):
        assert _cost_details({"input": 4}, PRICES) == {"input": 2.0, "output": 0.0, "total": 2.0}

    def test_token_usage_with_zero_token_prices_has_no_cost(self):
        assert _cost_details({"input": 4, "output": 3}, CostConfig(input=0.0, output=0.0)) is None

    @pytest.mark.parametrize("usage", [None, {}])
    def test_missing_usage_has_no_cost(self, usage):
        assert _cost_details(usage, PRICES) is None

    def test_missing_prices_have_no_cost(self):
        assert _cost_details({"audio_seconds": 6}, None) is None
