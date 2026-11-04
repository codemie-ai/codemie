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

"""Tests for ObservabilityProvider.update_current_generation across providers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from codemie.enterprise.observability.langfuse_provider import LangfuseObservabilityProvider
from codemie.enterprise.observability.noop_provider import NoOpObservabilityProvider
from codemie.enterprise.observability.phoenix_provider import PhoenixObservabilityProvider

GENERATION = {
    "model": "custom-stt",
    "model_parameters": {"language": "de"},
    "input": {"audio_file": "audio.webm"},
    "output": "hello",
    "usage_details": {"audio_seconds": 5},
    "metadata": {"x-transaction-id": "tx-1"},
    "level": "DEFAULT",
    "status_message": "ok",
}


class TestNoOpProvider:
    def test_update_current_generation_does_nothing(self):
        NoOpObservabilityProvider().update_current_generation(**GENERATION)


class TestLangfuseProvider:
    def test_all_given_fields_are_sent_to_the_active_generation(self):
        client = MagicMock()
        with patch("codemie.enterprise.observability.langfuse_provider._client_or_none", return_value=client):
            LangfuseObservabilityProvider().update_current_generation(**GENERATION)

        client.update_current_generation.assert_called_once_with(**GENERATION)

    def test_fields_that_are_none_are_not_sent(self):
        client = MagicMock()
        with patch("codemie.enterprise.observability.langfuse_provider._client_or_none", return_value=client):
            LangfuseObservabilityProvider().update_current_generation(output="hello")

        client.update_current_generation.assert_called_once_with(output="hello")

    def test_does_nothing_without_a_client(self):
        with patch("codemie.enterprise.observability.langfuse_provider._client_or_none", return_value=None):
            LangfuseObservabilityProvider().update_current_generation(**GENERATION)

    def test_sdk_failure_is_swallowed(self):
        client = MagicMock()
        client.update_current_generation.side_effect = RuntimeError("sdk down")
        with patch("codemie.enterprise.observability.langfuse_provider._client_or_none", return_value=client):
            LangfuseObservabilityProvider().update_current_generation(**GENERATION)


class TestPhoenixProvider:
    @staticmethod
    def _provider():
        provider = object.__new__(PhoenixObservabilityProvider)
        provider.update_current_observation = MagicMock()
        return provider

    def test_span_is_marked_as_llm_call_with_model_parameters_and_usage(self):
        span = MagicMock()
        provider = self._provider()
        with (
            patch.object(PhoenixObservabilityProvider, "is_enabled", return_value=True),
            patch("codemie.enterprise.observability.phoenix_provider._current_recording_span", return_value=span),
        ):
            provider.update_current_generation(**GENERATION)

        attributes = {call.args[0]: call.args[1] for call in span.set_attribute.call_args_list}
        assert attributes["openinference.span.kind"] == "LLM"
        assert attributes["llm.model_name"] == "custom-stt"
        assert attributes["llm.usage.audio_seconds"] == 5
        assert '"language": "de"' in attributes["llm.invocation_parameters"]

    def test_token_usage_uses_openinference_token_count_keys(self):
        span = MagicMock()
        provider = self._provider()
        with (
            patch.object(PhoenixObservabilityProvider, "is_enabled", return_value=True),
            patch("codemie.enterprise.observability.phoenix_provider._current_recording_span", return_value=span),
        ):
            provider.update_current_generation(usage_details={"input": 3, "output": 4, "total": 7})

        attributes = {call.args[0]: call.args[1] for call in span.set_attribute.call_args_list}
        assert attributes["llm.token_count.prompt"] == 3
        assert attributes["llm.token_count.completion"] == 4
        assert attributes["llm.token_count.total"] == 7

    def test_input_output_metadata_and_level_go_through_update_current_observation(self):
        provider = self._provider()
        with (
            patch.object(PhoenixObservabilityProvider, "is_enabled", return_value=True),
            patch(
                "codemie.enterprise.observability.phoenix_provider._current_recording_span", return_value=MagicMock()
            ),
        ):
            provider.update_current_generation(**GENERATION)

        provider.update_current_observation.assert_called_once_with(
            input=GENERATION["input"],
            output="hello",
            metadata=GENERATION["metadata"],
            level="DEFAULT",
            status_message="ok",
        )

    def test_does_nothing_when_disabled(self):
        provider = self._provider()
        with patch.object(PhoenixObservabilityProvider, "is_enabled", return_value=False):
            provider.update_current_generation(**GENERATION)

        provider.update_current_observation.assert_not_called()

    def test_span_failure_is_swallowed(self):
        span = MagicMock()
        span.set_attribute.side_effect = RuntimeError("otel down")
        provider = self._provider()
        with (
            patch.object(PhoenixObservabilityProvider, "is_enabled", return_value=True),
            patch("codemie.enterprise.observability.phoenix_provider._current_recording_span", return_value=span),
        ):
            provider.update_current_generation(**GENERATION)
