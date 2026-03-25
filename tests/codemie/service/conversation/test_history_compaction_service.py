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

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from codemie.service.conversation.history_compaction_service import ConversationHistoryCompactionService
from codemie.service.conversation import history_compaction_service as history_compaction_module


def test_create_text_batches_splits_oversized_lines(monkeypatch):
    monkeypatch.setattr(history_compaction_module, "calculate_tokens", lambda text, llm_model: len(text))

    batches = ConversationHistoryCompactionService._create_text_batches(
        history_text="abcdef\ngh",
        llm_model="test-model",
        max_tokens=3,
    )

    assert batches == ["abc", "def", "gh"]


@pytest.mark.asyncio
async def test_build_langgraph_pre_model_hook_returns_compacted_messages(monkeypatch):
    captured = {}
    compacted_messages = [
        AIMessage(content="[Compacted conversation summary]\nsummary"),
        HumanMessage(content="latest question"),
    ]

    async def fake_compact_messages_async(cls, messages, llm_model, request_id=None):
        captured["messages"] = messages
        captured["llm_model"] = llm_model
        captured["request_id"] = request_id
        return compacted_messages

    monkeypatch.setattr(
        ConversationHistoryCompactionService,
        "_compact_messages_async",
        classmethod(fake_compact_messages_async),
    )

    hook = ConversationHistoryCompactionService.build_langgraph_pre_model_hook(
        llm_model="gpt-test",
        request_id="req-123",
    )

    original_messages = [
        HumanMessage(content="older question"),
        AIMessage(content="older answer"),
    ]
    result = await hook({"messages": original_messages})

    assert result == {"llm_input_messages": compacted_messages}
    assert captured == {
        "messages": original_messages,
        "llm_model": "gpt-test",
        "request_id": "req-123",
    }


def test_get_summarization_model_respects_provider_boundary(monkeypatch):
    active_model = SimpleNamespace(provider="openai")
    monkeypatch.setattr(history_compaction_module.llm_service, "get_model_details", lambda llm_model: active_model)

    matching_summary_model = SimpleNamespace(provider="openai", base_name="summary-openai")
    monkeypatch.setattr(
        history_compaction_module.llm_service,
        "get_default_model_for_category",
        lambda category: matching_summary_model,
    )
    assert ConversationHistoryCompactionService._get_summarization_model("chat-model") == "summary-openai"

    different_provider_summary_model = SimpleNamespace(provider="anthropic", base_name="summary-anthropic")
    monkeypatch.setattr(
        history_compaction_module.llm_service,
        "get_default_model_for_category",
        lambda category: different_provider_summary_model,
    )
    assert ConversationHistoryCompactionService._get_summarization_model("chat-model") == "chat-model"


def test_compact_messages_returns_original_when_feature_flag_disabled(monkeypatch):
    messages = [
        HumanMessage(content="older question"),
        AIMessage(content="older answer"),
    ]

    monkeypatch.setattr(
        history_compaction_module.DynamicConfigService,
        "get_typed_value",
        lambda *args, **kwargs: False,
    )

    result = ConversationHistoryCompactionService.compact_messages(
        messages=messages,
        llm_model="test-model",
    )

    assert result == messages
