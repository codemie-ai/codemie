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

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_google_vertexai.model_garden import ChatAnthropicVertex
from langchain_openai import AzureChatOpenAI

from codemie.configs.llm_config import LLMModel, LLMProvider
from codemie.core.prompt_caching import enable_prompt_caching, should_cache_prompts
from codemie.core.prompt_caching_bedrock import PromptCachingChatBedrockConverse
from codemie.enterprise.litellm.llm_factory import LiteLLMChatOpenAI

CACHE_CONTROL = {"type": "ephemeral"}
LITELLM_INJECTION_POINTS = [
    {"location": "message", "role": "system"},
    {"location": "message", "index": -1},
    {"location": "tool_config"},
]


def _model_details(base_name: str = "claude-sonnet-4-6", provider: LLMProvider = LLMProvider.AWS_BEDROCK) -> LLMModel:
    return LLMModel(base_name=base_name, deployment_name=base_name, enabled=True, provider=provider)


def _agent_messages() -> list[BaseMessage]:
    return [
        SystemMessage(content="You are a Jira assistant."),
        HumanMessage(content="Which bugs are open?"),
        AIMessage(content="", tool_calls=[{"name": "search_jira", "args": {"jql": "type=Bug"}, "id": "call_1"}]),
        ToolMessage(content="EPMCDME-1 Login fails; status=Open", tool_call_id="call_1"),
    ]


def _azure_kwargs() -> dict[str, str]:
    return {
        "azure_endpoint": "https://litellm.invalid",
        "api_key": "test-key",
        "api_version": "2024-10-21",
        "deployment_name": "claude-sonnet-4-6",
    }


def _litellm_model() -> LiteLLMChatOpenAI:
    return LiteLLMChatOpenAI(_model_details(), **_azure_kwargs())


def _anthropic_model() -> ChatAnthropic:
    return ChatAnthropic(model="claude-sonnet-4-6", api_key="test-key", model_kwargs={"extra_headers": {"x-tag": "1"}})


def _vertex_model() -> ChatAnthropicVertex:
    return ChatAnthropicVertex(model_name="claude-sonnet-4-6", project="test-project", location="us-east5")


def _bedrock_model() -> PromptCachingChatBedrockConverse:
    return PromptCachingChatBedrockConverse(
        client=Mock(), model="us.anthropic.claude-sonnet-4-6", region_name="us-east-1"
    )


@tool
def _search_jira(jql: str) -> str:
    """Search Jira issues by JQL."""
    return jql


class TestShouldCachePrompts:
    def test_claude_through_litellm_is_cached(self):
        assert should_cache_prompts(_model_details(), uses_litellm=True, disabled_for_request=False)

    def test_claude_on_direct_bedrock_is_cached(self):
        assert should_cache_prompts(_model_details(), uses_litellm=False, disabled_for_request=False)

    def test_former_claude_3_5_sonnet_exclusion_no_longer_applies(self):
        details = _model_details(base_name="claude-3-5-sonnet")

        assert should_cache_prompts(details, uses_litellm=False, disabled_for_request=False)

    def test_non_claude_model_is_not_cached(self):
        details = _model_details(base_name="gpt-4.1", provider=LLMProvider.AZURE_OPENAI)

        assert not should_cache_prompts(details, uses_litellm=True, disabled_for_request=False)

    def test_disable_cache_request_flag_turns_caching_off(self):
        assert not should_cache_prompts(_model_details(), uses_litellm=True, disabled_for_request=True)

    @pytest.mark.parametrize(("uses_litellm", "expected"), [(True, True), (False, False)])
    def test_azure_openai_provider_is_cached_only_through_litellm(self, uses_litellm, expected):
        details = _model_details(provider=LLMProvider.AZURE_OPENAI)

        assert should_cache_prompts(details, uses_litellm=uses_litellm, disabled_for_request=False) is expected

    @pytest.mark.parametrize("flag", [True, False])
    def test_vertex_claude_follows_its_config_flag(self, flag):
        details = _model_details(provider=LLMProvider.VERTEX_AI_ANTHROPIC)

        with patch("codemie.core.prompt_caching.config.VERTEX_AI_ANTHROPIC_ENABLE_PROMPT_CACHE", flag):
            assert should_cache_prompts(details, uses_litellm=True, disabled_for_request=False) is flag


class TestGetLlmByCredentialsPromptCaching:
    @staticmethod
    def _build(*, cache_disabled: bool = False) -> tuple[LiteLLMChatOpenAI, Mock, Mock]:
        litellm_model = _litellm_model()
        with (
            patch(
                "codemie.core.dependecies.enforce_allowed_model", side_effect=lambda model, _project, _user_id: model
            ),
            patch("codemie.core.dependecies.llm_service.get_model_details", return_value=_model_details()),
            patch("codemie.enterprise.litellm.get_litellm_chat_model", return_value=litellm_model),
            patch("codemie.core.dependecies.get_disable_prompt_cache", return_value=cache_disabled),
            patch("codemie.core.dependecies.should_cache_prompts", wraps=should_cache_prompts) as gate,
            patch("codemie.core.dependecies.enable_prompt_caching", wraps=enable_prompt_caching) as enable,
        ):
            from codemie.core.dependecies import get_llm_by_credentials

            llm = get_llm_by_credentials(llm_model="claude-sonnet-4-6")
        return llm, gate, enable

    def test_eligible_claude_model_gets_prompt_caching(self):
        llm, gate, enable = self._build()

        gate.assert_called_once_with(_model_details(), uses_litellm=True, disabled_for_request=False)
        enable.assert_called_once_with(llm, uses_litellm=True)
        assert llm.extra_body["cache_control_injection_points"] == LITELLM_INJECTION_POINTS

    def test_request_with_disable_cache_gets_no_prompt_caching(self):
        llm, _gate, enable = self._build(cache_disabled=True)

        enable.assert_not_called()
        assert llm.extra_body is None


class TestEnablePromptCachingOnLiteLLM:
    @pytest.mark.parametrize("stream", [False, True])
    def test_request_asks_litellm_to_inject_cache_markers(self, stream):
        model = _litellm_model()

        enable_prompt_caching(model, uses_litellm=True)
        payload = model._get_request_payload(_agent_messages(), stream=stream)

        assert payload["extra_body"]["cache_control_injection_points"] == LITELLM_INJECTION_POINTS

    def test_existing_extra_body_fields_are_kept(self):
        model = _litellm_model()
        model.extra_body = {"metadata": {"source": "test"}}

        enable_prompt_caching(model, uses_litellm=True)

        assert model.extra_body["metadata"] == {"source": "test"}

    def test_request_without_caching_has_no_injection_points(self):
        payload = _litellm_model()._get_request_payload(_agent_messages())

        assert "cache_control_injection_points" not in (payload.get("extra_body") or {})


class TestPromptCachingAcrossRequests:
    """Caching is a fixed model option, so earlier calls, failed or not, never change later requests."""

    @staticmethod
    def _longer_conversation() -> list[BaseMessage]:
        return [
            *_agent_messages(),
            AIMessage(content="", tool_calls=[{"name": "search_jira", "args": {"jql": "type=Task"}, "id": "call_2"}]),
            ToolMessage(content="EPMCDME-2 Add login audit; status=Open", tool_call_id="call_2"),
        ]

    def test_litellm_requests_keep_injection_points_after_a_failed_call(self):
        model = _litellm_model()
        enable_prompt_caching(model, uses_litellm=True)
        failing_client = Mock()
        failing_client.create.side_effect = RuntimeError("provider unavailable")
        failing_client.with_raw_response = failing_client
        model.client = failing_client

        with pytest.raises(RuntimeError):
            model.invoke(_agent_messages())

        for messages in (_agent_messages(), self._longer_conversation()):
            payload = model._get_request_payload(messages)
            assert payload["extra_body"]["cache_control_injection_points"] == LITELLM_INJECTION_POINTS

    def test_anthropic_requests_keep_cache_control_after_a_failed_call(self):
        model = _anthropic_model()
        enable_prompt_caching(model, uses_litellm=False)

        with patch.object(ChatAnthropic, "_create", side_effect=RuntimeError("provider unavailable")):
            with pytest.raises(RuntimeError):
                model.invoke(_agent_messages())

        for messages in (_agent_messages(), self._longer_conversation()):
            assert model._get_request_payload(messages)["cache_control"] == CACHE_CONTROL


class TestEnablePromptCachingOnDirectProviders:
    @pytest.mark.parametrize("stream", [False, True])
    def test_anthropic_request_uses_automatic_caching(self, stream):
        model = _anthropic_model()

        enable_prompt_caching(model, uses_litellm=False)
        payload = model._get_request_payload(_agent_messages(), stream=stream)

        assert payload["cache_control"] == CACHE_CONTROL
        assert payload["extra_headers"] == {"x-tag": "1"}

    def test_vertex_request_marks_the_last_message(self):
        model = _vertex_model()

        enable_prompt_caching(model, uses_litellm=False)
        params = model._format_params(messages=_agent_messages())

        assert params["messages"][-1]["content"][-1]["cache_control"] == CACHE_CONTROL

    def test_caller_messages_are_not_mutated(self):
        messages = _agent_messages()
        model = _vertex_model()

        enable_prompt_caching(model, uses_litellm=False)
        model._format_params(messages=messages)

        assert messages == _agent_messages()

    def test_dial_azure_model_is_left_unchanged(self):
        model = AzureChatOpenAI(**_azure_kwargs())

        enable_prompt_caching(model, uses_litellm=False)
        payload = model._get_request_payload(_agent_messages())

        assert "extra_body" not in payload
        assert "cache_control" not in payload


class TestEnablePromptCachingOnDirectBedrock:
    CACHE_POINT = {"cachePoint": {"type": "default"}}

    @staticmethod
    def _sent_request(model: PromptCachingChatBedrockConverse, *, stream: bool) -> dict:
        client = Mock()
        client.converse.side_effect = client.converse_stream.side_effect = RuntimeError("request captured")
        model.client = client
        with_tool = model.bind_tools([_search_jira])
        with pytest.raises(RuntimeError):
            if stream:
                list(with_tool.stream(_agent_messages()))
            else:
                with_tool.invoke(_agent_messages())
        sent = client.converse_stream if stream else client.converse
        return sent.call_args.kwargs

    @pytest.mark.parametrize("stream", [False, True])
    def test_request_carries_cache_points_after_system_last_message_and_tools(self, stream):
        model = _bedrock_model()

        enable_prompt_caching(model, uses_litellm=False)
        request = self._sent_request(model, stream=stream)

        assert request["system"][-1] == self.CACHE_POINT
        assert request["messages"][-1]["content"][-1] == self.CACHE_POINT
        assert request["toolConfig"]["tools"][-1] == self.CACHE_POINT

    def test_request_without_caching_has_no_cache_points(self):
        request = self._sent_request(_bedrock_model(), stream=False)

        assert self.CACHE_POINT not in request["system"]
        assert self.CACHE_POINT not in request["messages"][-1]["content"]
        assert self.CACHE_POINT not in request["toolConfig"]["tools"]
