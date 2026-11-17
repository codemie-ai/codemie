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

"""Tests for Codemie traffic-tagging header injection in core dependencies."""

from unittest.mock import MagicMock, patch

import pytest


def _make_model_details(client_headers=None):
    """Build a minimal LLMModel mock that reaches the AzureChatOpenAI call."""
    m = MagicMock()
    m.provider = None  # not GOOGLE_VERTEX_AI / AWS_BEDROCK / ANTHROPIC
    m.deployment_name = "gpt-4"
    m.base_name = "gpt-4"
    m.api_version = None
    m.max_output_tokens = 4096
    m.features.streaming = True
    m.features.temperature = True
    m.features.parallel_tool_calls = True
    m.features.max_tokens = True
    m.features.top_p = True
    if client_headers is not None:
        m.configuration = MagicMock()
        m.configuration.client_headers = client_headers
    else:
        m.configuration = None
    return m


def _make_creds():
    creds = MagicMock()
    creds.url = "https://test.openai.azure.com"
    creds.api_key = "test-key"
    creds.api_version = "2025-04-01-preview"
    return creds


class TestAzureTaggingHeaders:
    """X-CodeMie-* header injection on the Azure/DIAL path."""

    def test_both_tags_injected_when_project_set(self):
        """AzureChatOpenAI receives X-CodeMie-Version and X-CodeMie-Project."""
        from codemie.configs.config import config

        with (
            patch("codemie.core.dependecies.AzureChatOpenAI") as mock_azure,
            patch("codemie.core.dependecies.llm_service.get_model_details", return_value=_make_model_details()),
            patch("codemie.core.dependecies.dial_credentials") as mock_creds_var,
            patch("codemie.core.dependecies.get_current_project", return_value="my-project"),
            patch.object(config, "APP_VERSION", "1.2.3"),
            patch.object(config, "OPENAI_API_TYPE", "azure"),
            patch.object(config, "AZURE_OPENAI_MAX_RETRIES", 3),
        ):
            mock_creds_var.get.return_value = _make_creds()
            from codemie.core.dependecies import get_llm_by_credentials_raw

            get_llm_by_credentials_raw("gpt-4")

        call_kwargs = mock_azure.call_args[1]
        assert "default_headers" in call_kwargs
        assert call_kwargs["default_headers"]["X-CodeMie-Version"] == "1.2.3"
        assert call_kwargs["default_headers"]["X-CodeMie-Project"] == "my-project"

    def test_project_header_skipped_when_empty(self):
        """X-CodeMie-Project is absent when get_current_project() returns empty string."""
        from codemie.configs.config import config

        with (
            patch("codemie.core.dependecies.AzureChatOpenAI") as mock_azure,
            patch("codemie.core.dependecies.llm_service.get_model_details", return_value=_make_model_details()),
            patch("codemie.core.dependecies.dial_credentials") as mock_creds_var,
            patch("codemie.core.dependecies.get_current_project", return_value=""),
            patch.object(config, "APP_VERSION", "1.2.3"),
            patch.object(config, "OPENAI_API_TYPE", "azure"),
            patch.object(config, "AZURE_OPENAI_MAX_RETRIES", 3),
        ):
            mock_creds_var.get.return_value = _make_creds()
            from codemie.core.dependecies import get_llm_by_credentials_raw

            get_llm_by_credentials_raw("gpt-4")

        call_kwargs = mock_azure.call_args[1]
        assert "default_headers" in call_kwargs
        assert "X-CodeMie-Version" in call_kwargs["default_headers"]
        assert "X-CodeMie-Project" not in call_kwargs["default_headers"]

    def test_client_headers_override_codemie_tags(self):
        """Model client_headers win when they set the same key as a Codemie tag."""
        from codemie.configs.config import config

        model = _make_model_details(client_headers={"X-CodeMie-Version": "override-value"})
        with (
            patch("codemie.core.dependecies.AzureChatOpenAI") as mock_azure,
            patch("codemie.core.dependecies.llm_service.get_model_details", return_value=model),
            patch("codemie.core.dependecies.dial_credentials") as mock_creds_var,
            patch("codemie.core.dependecies.get_current_project", return_value="proj"),
            patch.object(config, "APP_VERSION", "1.2.3"),
            patch.object(config, "OPENAI_API_TYPE", "azure"),
            patch.object(config, "AZURE_OPENAI_MAX_RETRIES", 3),
        ):
            mock_creds_var.get.return_value = _make_creds()
            from codemie.core.dependecies import get_llm_by_credentials_raw

            get_llm_by_credentials_raw("gpt-4")

        call_kwargs = mock_azure.call_args[1]
        assert call_kwargs["default_headers"]["X-CodeMie-Version"] == "override-value"


class TestVertexTaggingHeaders:
    """X-CodeMie-* header injection on the Google Vertex AI path."""

    def test_tags_injected_into_client_options(self):
        """ChatVertexAI receives additional_headers with both Codemie tags."""
        from codemie.configs.config import config

        model = MagicMock()
        model.base_name = "gemini-1.5-pro"
        model.deployment_name = "gemini-1.5-pro"
        model.max_output_tokens = 8192
        model.features.streaming = True
        model.features.temperature = True
        model.features.top_p = True
        model.configuration = None

        with (
            patch("langchain_google_vertexai.ChatVertexAI") as mock_vertex,
            patch("codemie.core.dependecies.get_current_project", return_value="vertex-proj"),
            patch.object(config, "APP_VERSION", "2.0.0"),
            patch.object(config, "GOOGLE_PROJECT_ID", "test-gcp-project"),
            patch.object(config, "GOOGLE_VERTEXAI_REGION", "us-central1"),
            patch.object(config, "GOOGLE_VERTEXAI_MAX_RETRIES", 2),
        ):
            mock_vertex.return_value = MagicMock()
            from codemie.core.dependecies import get_vertex_llm
            from codemie.core.dependecies import llm_service as _llm_service

            with (
                patch.object(_llm_service, "is_gemini_models", return_value=True),
                patch.object(_llm_service, "is_claude_models", return_value=False),
            ):
                get_vertex_llm(llm_model_details=model)

        call_kwargs = mock_vertex.call_args[1]
        headers = call_kwargs["client_options"]["additional_headers"]
        assert headers["X-CodeMie-Version"] == "2.0.0"
        assert headers["X-CodeMie-Project"] == "vertex-proj"


class TestAnthropicTaggingHeaders:
    """X-CodeMie-* header injection on the Anthropic direct path."""

    def test_tags_injected_into_extra_headers(self):
        """ChatAnthropic receives extra_headers with both Codemie tags."""
        from codemie.configs.config import config

        model = MagicMock()
        model.deployment_name = "claude-3-5-sonnet-20241022"
        model.max_output_tokens = 8192
        model.features.streaming = True
        model.features.temperature = True
        model.features.top_p = True
        model.configuration = None

        with (
            patch("langchain_anthropic.ChatAnthropic") as mock_anthropic,
            patch("codemie.core.dependecies.get_current_project", return_value="anthro-proj"),
            patch.object(config, "APP_VERSION", "3.0.0"),
            patch.object(config, "ANTHROPIC_MAX_RETRIES", 2),
        ):
            mock_anthropic.return_value = MagicMock()
            from codemie.core.dependecies import get_anthropic_llm

            get_anthropic_llm(llm_model_details=model)

        call_kwargs = mock_anthropic.call_args[1]
        extra_headers = call_kwargs["model_kwargs"]["extra_headers"]
        assert extra_headers["X-CodeMie-Version"] == "3.0.0"
        assert extra_headers["X-CodeMie-Project"] == "anthro-proj"


def _make_anthropic_model(effort_feature):
    model = MagicMock()
    model.deployment_name = "claude-opus-4-5-20251101"
    model.max_output_tokens = 8192
    model.features.streaming = True
    model.features.temperature = True
    model.features.top_p = True
    model.features.effort = effort_feature
    model.configuration = None
    return model


class TestAnthropicEffort:
    """effort clamping and silent-drop on the direct Anthropic path."""

    def test_over_ceiling_effort_clamps_down(self):
        model = _make_anthropic_model(["low", "medium", "high", "max"])

        with patch("langchain_anthropic.ChatAnthropic") as mock_anthropic:
            mock_anthropic.return_value = MagicMock()
            from codemie.core.dependecies import get_anthropic_llm

            get_anthropic_llm(llm_model_details=model, effort="xhigh")

        assert mock_anthropic.call_args[1]["effort"] == "high"

    def test_effort_above_lower_ceiling_clamps_down(self):
        model = _make_anthropic_model(["low", "medium", "high"])

        with patch("langchain_anthropic.ChatAnthropic") as mock_anthropic:
            mock_anthropic.return_value = MagicMock()
            from codemie.core.dependecies import get_anthropic_llm

            get_anthropic_llm(llm_model_details=model, effort="max")

        assert mock_anthropic.call_args[1]["effort"] == "high"

    def test_unsupported_effort_omitted(self):
        model = _make_anthropic_model(None)

        with patch("langchain_anthropic.ChatAnthropic") as mock_anthropic:
            mock_anthropic.return_value = MagicMock()
            from codemie.core.dependecies import get_anthropic_llm

            get_anthropic_llm(llm_model_details=model, effort="medium")

        assert "effort" not in mock_anthropic.call_args[1]


def _make_bedrock_model(effort_feature, client_headers=None):
    model = MagicMock()
    model.deployment_name = "us.anthropic.claude-opus-4-5-20251101-v1:0"
    model.max_output_tokens = 8192
    model.features.streaming = True
    model.features.effort = effort_feature
    if client_headers is not None:
        model.configuration = MagicMock()
        model.configuration.client_headers = client_headers
    else:
        model.configuration = None
    return model


class TestBedrockEffort:
    """effort clamping and silent-drop on the Bedrock-hosted Claude path."""

    def test_over_ceiling_effort_clamps_down(self):
        model = _make_bedrock_model(["low", "medium", "high", "max"])

        with (
            patch("codemie.core.prompt_caching_bedrock.PromptCachingChatBedrockConverse") as mock_bedrock,
            patch("codemie.core.dependecies.get_bedrock_runtime_client"),
        ):
            mock_bedrock.return_value = MagicMock()
            from codemie.core.dependecies import get_bedrock_llm

            get_bedrock_llm(llm_model_details=model, effort="high")

        assert mock_bedrock.call_args[1]["additional_model_request_fields"] == {"output_config": {"effort": "high"}}

    def test_effort_above_lower_ceiling_clamps_down(self):
        model = _make_bedrock_model(["low", "medium", "high"])

        with (
            patch("codemie.core.prompt_caching_bedrock.PromptCachingChatBedrockConverse") as mock_bedrock,
            patch("codemie.core.dependecies.get_bedrock_runtime_client"),
        ):
            mock_bedrock.return_value = MagicMock()
            from codemie.core.dependecies import get_bedrock_llm

            get_bedrock_llm(llm_model_details=model, effort="max")

        assert mock_bedrock.call_args[1]["additional_model_request_fields"] == {"output_config": {"effort": "high"}}

    def test_unsupported_effort_no_output_config_key(self):
        model = _make_bedrock_model(None)

        with (
            patch("codemie.core.prompt_caching_bedrock.PromptCachingChatBedrockConverse") as mock_bedrock,
            patch("codemie.core.dependecies.get_bedrock_runtime_client"),
        ):
            mock_bedrock.return_value = MagicMock()
            from codemie.core.dependecies import get_bedrock_llm

            get_bedrock_llm(llm_model_details=model, effort="medium")

        call_kwargs = mock_bedrock.call_args[1]
        assert call_kwargs["additional_model_request_fields"] is None

    def test_existing_client_headers_not_clobbered(self):
        """Merging output_config.effort must not clobber a pre-existing
        client_headers-derived additional_model_request_fields entry."""
        model = _make_bedrock_model(["low", "medium", "high"], client_headers={"existing-key": "existing-value"})

        with (
            patch("codemie.core.prompt_caching_bedrock.PromptCachingChatBedrockConverse") as mock_bedrock,
            patch("codemie.core.dependecies.get_bedrock_runtime_client"),
        ):
            mock_bedrock.return_value = MagicMock()
            from codemie.core.dependecies import get_bedrock_llm

            get_bedrock_llm(llm_model_details=model, effort="high")

        fields = mock_bedrock.call_args[1]["additional_model_request_fields"]
        assert fields["existing-key"] == "existing-value"
        assert fields["output_config"] == {"effort": "high"}

    def test_existing_output_config_keys_kept_when_effort_merged(self):
        """An output_config already set in client_headers keeps its other keys; effort is added to it."""
        model = _make_bedrock_model(["low", "medium", "high"], client_headers={"output_config": {"format": "json"}})

        with (
            patch("codemie.core.prompt_caching_bedrock.PromptCachingChatBedrockConverse") as mock_bedrock,
            patch("codemie.core.dependecies.get_bedrock_runtime_client"),
        ):
            mock_bedrock.return_value = MagicMock()
            from codemie.core.dependecies import get_bedrock_llm

            get_bedrock_llm(llm_model_details=model, effort="high")

        fields = mock_bedrock.call_args[1]["additional_model_request_fields"]
        assert fields["output_config"] == {"format": "json", "effort": "high"}
        assert model.configuration.client_headers["output_config"] == {"format": "json"}

    def test_claude_4_5_haiku_excluded_gets_no_output_config(self):
        """claude-4-5-haiku/claude-4-5-sonnet have features.effort=None by construction
        (lack supports_output_config entirely) — no output_config key regardless of what's
        requested."""
        model = _make_bedrock_model(None)
        model.deployment_name = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

        with (
            patch("codemie.core.prompt_caching_bedrock.PromptCachingChatBedrockConverse") as mock_bedrock,
            patch("codemie.core.dependecies.get_bedrock_runtime_client"),
        ):
            mock_bedrock.return_value = MagicMock()
            from codemie.core.dependecies import get_bedrock_llm

            get_bedrock_llm(llm_model_details=model, effort="max")

        assert mock_bedrock.call_args[1]["additional_model_request_fields"] is None


class TestAzureEffort:
    """effort clamping and silent-drop on the Azure/DIAL path (reasoning_effort)."""

    @staticmethod
    def _azure_kwargs(effort_feature, effort, creds, base_name="gpt-5"):
        from codemie.configs.config import config

        model = _make_model_details()
        model.base_name = base_name
        model.features.effort = effort_feature
        with (
            patch("codemie.core.dependecies.AzureChatOpenAI") as mock_azure,
            patch("codemie.core.dependecies.llm_service.get_model_details", return_value=model),
            patch("codemie.core.dependecies.dial_credentials") as mock_creds_var,
            patch("codemie.core.dependecies.get_current_project", return_value=""),
            patch.object(config, "OPENAI_API_TYPE", "azure"),
            patch.object(config, "AZURE_OPENAI_MAX_RETRIES", 3),
        ):
            mock_creds_var.get.return_value = creds
            from codemie.core.dependecies import get_llm_by_credentials_raw

            get_llm_by_credentials_raw("gpt-5", effort=effort)
        return mock_azure.call_args[1]

    @pytest.mark.parametrize("creds_factory", [lambda: None, _make_creds], ids=["azure", "dial"])
    @pytest.mark.parametrize(
        ("base_name", "feature"),
        [("claude-opus-4-6", ["low", "medium", "high", "max"]), ("gemini-3.1-pro", ["low", "medium", "high"])],
    )
    def test_claude_and_gemini_reasoning_effort_capped_at_high(self, creds_factory, base_name, feature):
        kwargs = self._azure_kwargs(feature, "max", creds_factory(), base_name=base_name)
        assert kwargs["reasoning_effort"] == "high"

    @pytest.mark.parametrize("creds_factory", [lambda: None, _make_creds], ids=["azure", "dial"])
    def test_claude_lower_effort_passes_through(self, creds_factory):
        kwargs = self._azure_kwargs(
            ["low", "medium", "high", "max"], "low", creds_factory(), base_name="claude-opus-4-6"
        )
        assert kwargs["reasoning_effort"] == "low"

    @pytest.mark.parametrize("creds_factory", [lambda: None, _make_creds], ids=["azure", "dial"])
    def test_reasoning_effort_clamped(self, creds_factory):
        kwargs = self._azure_kwargs(["low", "medium", "high"], "max", creds_factory())
        assert kwargs["reasoning_effort"] == "high"

    @pytest.mark.parametrize("creds_factory", [lambda: None, _make_creds], ids=["azure", "dial"])
    def test_reasoning_effort_within_ceiling(self, creds_factory):
        kwargs = self._azure_kwargs(["low", "medium", "high"], "medium", creds_factory())
        assert kwargs["reasoning_effort"] == "medium"

    @pytest.mark.parametrize("creds_factory", [lambda: None, _make_creds], ids=["azure", "dial"])
    def test_no_effort_declared_omits_key(self, creds_factory):
        kwargs = self._azure_kwargs(None, "high", creds_factory())
        assert "reasoning_effort" not in kwargs

    @pytest.mark.parametrize("creds_factory", [lambda: None, _make_creds], ids=["azure", "dial"])
    def test_effort_not_requested_omits_key(self, creds_factory):
        kwargs = self._azure_kwargs(["low", "medium", "high"], None, creds_factory())
        assert "reasoning_effort" not in kwargs


def _make_vertex_model(effort_feature, base_name="gemini-3-pro"):
    model = MagicMock()
    model.base_name = base_name
    model.deployment_name = base_name
    model.max_output_tokens = 8192
    model.features.streaming = True
    model.features.temperature = True
    model.features.top_p = True
    model.features.effort = effort_feature
    model.configuration = None
    return model


class TestVertexEffort:
    """effort -> Gemini thinking_budget on the Vertex path; Vertex Claude stays untouched."""

    @staticmethod
    def _gemini_kwargs(effort_feature, effort):
        with patch("langchain_google_vertexai.ChatVertexAI") as mock_vertex:
            mock_vertex.return_value = MagicMock()
            from codemie.core.dependecies import get_vertex_llm
            from codemie.core.dependecies import llm_service as _llm_service

            with (
                patch.object(_llm_service, "is_gemini_models", return_value=True),
                patch.object(_llm_service, "is_claude_models", return_value=False),
            ):
                get_vertex_llm(llm_model_details=_make_vertex_model(effort_feature), effort=effort)
        return mock_vertex.call_args[1]

    @pytest.mark.parametrize(("effort", "budget"), [("low", 1024), ("medium", 2048), ("high", 4096)])
    def test_effort_maps_to_thinking_budget(self, effort, budget):
        kwargs = self._gemini_kwargs(["low", "medium", "high"], effort)
        assert kwargs["thinking_budget"] == budget

    def test_max_clamps_to_high_budget(self):
        kwargs = self._gemini_kwargs(["low", "medium", "high"], "max")
        assert kwargs["thinking_budget"] == 4096

    def test_no_effort_declared_no_budget(self):
        kwargs = self._gemini_kwargs(None, "high")
        assert "thinking_budget" not in kwargs

    def test_effort_not_requested_no_budget(self):
        kwargs = self._gemini_kwargs(["low", "medium", "high"], None)
        assert "thinking_budget" not in kwargs

    def test_vertex_claude_never_gets_budget(self):
        model = _make_vertex_model(["low", "medium", "high"], base_name="claude-opus-4-6")
        with patch("langchain_google_vertexai.model_garden.ChatAnthropicVertex") as mock_claude:
            mock_claude.return_value = MagicMock()
            from codemie.core.dependecies import get_vertex_llm
            from codemie.core.dependecies import llm_service as _llm_service

            with (
                patch.object(_llm_service, "is_gemini_models", return_value=False),
                patch.object(_llm_service, "is_claude_models", return_value=True),
            ):
                get_vertex_llm(llm_model_details=model, effort="high")

        assert "thinking_budget" not in mock_claude.call_args[1]
